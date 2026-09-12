#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : core/bridge_worker.py
توضیح   : BridgeWorker — یک thread برای هر مبدل TCP↔RS485 (USR-DR134)

           اصلاح patch-tx (روی نسخه اصلی v4.2.4):
           - _tx_queue از List به PriorityQueue تغییر یافت
           - send() پارامتر priority اضافه شد (سازگار با service.py)
           - TX loop: به جای drain کامل، فقط یک فریم در هر iteration
           - turnaround قبل از هر فریم چک می‌شود (نه فقط اول batch)
           - _tx_lock حذف شد (PriorityQueue خودش thread-safe است)
           - _tx_seq برای FIFO درون یک سطح priority اضافه شد
===============================================================================
"""

from __future__ import annotations

import socket as _socket
import time
import threading
from queue import PriorityQueue, Empty
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from core.protocol import (
    build_frame, parse_frame, mac_str, mac_bytes_from_str,
    CMD_NAMES, CMD_ACK, CMD_NACK, CMD_DISCOVERY_REQ, CMD_DISCOVERY_RES,
    CMD_ADDRESS_ACK, CMD_GET_STATUS, CMD_TOUCH_EVENT, CMD_ADC_EVENT,
    CMD_BOOT_ACK, CMD_UPDATE_ACK, CMD_BOOT_ERROR,
    ADDR_BROADCAST, OTA_PASSWORD, MAX_RX_BUF, CMD_MOTION_EVENT,
    CMD_GAME_EVENT,
    TX_PRIORITY_GAME, TX_PRIORITY_POLL,
)
from core.tcp_link import TCPLink
from core.node_registry import NodeRegistry
from core.feedback import FeedbackTracker
from core.ota_session import OtaSession

TX_INTER_FRAME_DELAY_S = 0.010
RX_TO_TX_TURNAROUND_S  = 0.010
LOOP_SLEEP_S           = 0.0005
DEBUG_RX               = False


class BridgeWorker(threading.Thread):

    def __init__(self, bridge_id: str, host: str, port: int,
                 registry: NodeRegistry, feedback: FeedbackTracker,
                 on_event: Callable[[str, Dict[str, Any]], None]) -> None:
        super().__init__(name=f"BridgeWorker-{bridge_id}", daemon=True)
        self.bridge_id = bridge_id
        self.host      = host
        self.port      = port
        self.registry  = registry
        self.feedback  = feedback
        self._on_event = on_event

        self.link    = TCPLink()
        self._rx_buf = b""
        self._alive  = True
        self._reconnect_at   = 0.0
        self._want_connected = False

        # ✅ patch-tx: PriorityQueue به جای List
        # هر آیتم: (priority, seq, addr, cmd, data, tag)
        # PriorityQueue خودش thread-safe است — نیازی به _tx_lock نیست
        self._tx_queue: PriorityQueue = PriorityQueue()
        self._tx_seq      = 0
        self._tx_seq_lock = threading.Lock()

        self._ota: Optional[OtaSession] = None
        self._ota_lock = threading.Lock()

        self._addr_to_mac:   Dict[int, str] = {}
        self._zero_addr_macs: Set[str]      = set()

        self._poll_ack     = threading.Event()
        self._last_rx_time = 0.0

    # ── مدیریت اتصال ──────────────────────────────────────────────────────────

    def connect(self) -> bool:
        self._want_connected = True
        ok = self.link.connect(self.host, self.port)
        if ok and self.link.sock is not None:
            try:
                self.link.sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
                self.link.sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF, 65536)
                self.link.sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_SNDBUF, 65536)
            except OSError:
                pass
        self._on_event("connected" if ok else "conn_error", {
            "bridge_id": self.bridge_id,
            "host": self.host,
            "port": self.port,
        })
        return ok

    def disconnect(self) -> None:
        self._want_connected = False
        with self._ota_lock:
            if self._ota is not None:
                self._ota.request_abort()
        self.link.disconnect()
        self._on_event("disconnected", {"bridge_id": self.bridge_id})

    def set_endpoint(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

    def stop(self) -> None:
        self._alive = False

    def notify_cleared(self, mac: str) -> None:
        mac = mac.upper().strip()
        to_delete = [a for a, m in self._addr_to_mac.items() if m == mac]
        for a in to_delete:
            del self._addr_to_mac[a]
        self._zero_addr_macs.add(mac)

    def notify_poll_response(self) -> None:
        self._poll_ack.set()

    # ── API ارسال دستور ───────────────────────────────────────────────────────

    def send(self, addr: int, cmd: int, data: bytes = b"",
             tag: str = "", expect_feedback: bool = True,
             priority: int = TX_PRIORITY_GAME) -> bool:
        """
        ✅ patch-tx: پارامتر priority اضافه شد.
        پیش‌فرض TX_PRIORITY_GAME (0) — سازگار با همه فراخوان‌های قدیمی.
        """
        with self._ota_lock:
            ota_active = self._ota is not None and not self._ota.is_finished()
        if ota_active:
            return False

        if addr == ADDR_BROADCAST:
            expect_feedback = False

        # ✅ seq یکتا برای FIFO درون یک سطح priority
        with self._tx_seq_lock:
            seq = self._tx_seq
            self._tx_seq += 1

        self._tx_queue.put((priority, seq, addr, cmd, bytes(data), tag))

        if expect_feedback and cmd not in (CMD_ACK, CMD_NACK):
            self.feedback.expect(self.bridge_id, addr, cmd, tag, bytes(data))
        return True

    def is_ota_active(self) -> bool:
        with self._ota_lock:
            return self._ota is not None and not self._ota.is_finished()

    # ── API مدیریت OTA ────────────────────────────────────────────────────────

    def start_ota(self, mac: str, addr: int, firmware: bytes,
                  password: bytes = OTA_PASSWORD) -> Tuple[bool, str]:
        with self._ota_lock:
            if self._ota is not None and not self._ota.is_finished():
                return False, "یک session OTA روی این bridge در حال اجرا است"
            if not self.link.connected:
                return False, "bridge متصل نیست"
            session = OtaSession(
                bridge_id=self.bridge_id, mac=mac, addr=addr,
                firmware=firmware, password=password,
                send_raw=self.link.send,
                on_event=self._on_event,
            )
            ok, reason = session.start()
            if not ok:
                return False, reason
            self._ota = session
        self.registry.set_ota_flag(mac, True)
        return True, "started"

    def abort_ota(self) -> None:
        with self._ota_lock:
            if self._ota is not None:
                self._ota.request_abort()

    def get_ota_status(self) -> Optional[Dict[str, Any]]:
        with self._ota_lock:
            if self._ota is None:
                return None
            snap = self._ota.snapshot()
            snap["mac"]  = self._ota.mac
            snap["addr"] = self._ota.addr
            return snap

    # ── حلقه اصلی thread ──────────────────────────────────────────────────────

    def run(self) -> None:
        while self._alive:

            # ── reconnect خودکار ──────────────────────────────────────────────
            if self._want_connected and not self.link.connected:
                now = time.time()
                if now >= self._reconnect_at:
                    ok = self.link.connect(self.host, self.port)
                    self._reconnect_at = now + 2.0
                    if ok and self.link.sock is not None:
                        try:
                            self.link.sock.setsockopt(
                                _socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
                            self.link.sock.setsockopt(
                                _socket.SOL_SOCKET, _socket.SO_RCVBUF, 65536)
                            self.link.sock.setsockopt(
                                _socket.SOL_SOCKET, _socket.SO_SNDBUF, 65536)
                            if hasattr(_socket, "SIO_KEEPALIVE_VALS"):
                                self.link.sock.ioctl(
                                    _socket.SIO_KEEPALIVE_VALS, (1, 30000, 10000))
                            else:
                                if hasattr(_socket, "TCP_KEEPIDLE"):
                                    self.link.sock.setsockopt(
                                        _socket.IPPROTO_TCP,
                                        _socket.TCP_KEEPIDLE, 30)
                        except OSError:
                            pass
                    self._on_event("connected" if ok else "conn_error", {
                        "bridge_id": self.bridge_id,
                        "host": self.host,
                        "port": self.port,
                    })

            now = time.time()

            # ── tick OTA state machine ────────────────────────────────────────
            with self._ota_lock:
                ota = self._ota
            if ota is not None:
                ota.tick(now)
                if ota.is_finished():
                    self._finalize_ota(ota)

            # ── خواندن داده دریافتی ───────────────────────────────────────────
            chunk = self.link.read()
            if chunk:
                if DEBUG_RX:
                    self._on_event("rx_raw_debug", {
                        "bridge_id": self.bridge_id,
                        "hex":       chunk.hex(" ").upper(),
                        "len":       len(chunk),
                    })
                self._rx_buf += chunk
                if len(self._rx_buf) > MAX_RX_BUF:
                    self._on_event("rx_overflow", {
                        "bridge_id":     self.bridge_id,
                        "dropped_bytes": len(self._rx_buf),
                    })
                    self._rx_buf = b""
                else:
                    self._drain_rx()
                self._last_rx_time = now

            # ── ✅ patch-tx: ارسال یک فریم در هر iteration ───────────────────
            self._tx_one_frame(now)

            time.sleep(LOOP_SLEEP_S)

    def _tx_one_frame(self, now: float) -> None:
        """
        ✅ patch-tx: فقط یک فریم در هر iteration ارسال می‌شود.

        قبل: drain کامل صف → همه فریم‌ها پشت سر هم → collision با RX نودها
        بعد: یک فریم → RX چک → یک فریم → RX چک → ...

        اگر turnaround فعال باشد، فریم به صف برمی‌گردد و
        iteration بعدی (0.5ms دیگر) دوباره چک می‌کند.
        """
        # ── ۱. یک آیتم از صف ─────────────────────────────────────────────────
        try:
            item = self._tx_queue.get_nowait()
        except Empty:
            return

        priority, seq, addr, cmd, data, tag = item

        # ── ۲. اگر bridge متصل نیست → برگردان به صف ─────────────────────────
        if not self.link.connected:
            self._tx_queue.put(item)
            return

        # ── ۳. turnaround: اگر اخیراً RX داشتیم → برگردان به صف ─────────────
        # این مهم‌ترین بخش fix است — جلوگیری از collision RS485 half-duplex
        if self._last_rx_time > 0:
            elapsed = now - self._last_rx_time
            if elapsed < RX_TO_TX_TURNAROUND_S:
                self._tx_queue.put(item)
                return

        # ── ۴. ارسال ─────────────────────────────────────────────────────────
        pkt = build_frame(addr, cmd, data)
        ok  = self.link.send(pkt)
        self._on_event("tx", {
            "bridge_id": self.bridge_id,
            "addr":      addr,
            "cmd":       cmd,
            "cmd_name":  CMD_NAMES.get(cmd, f"0x{cmd:02X}"),
            "raw":       pkt,
            "ok":        ok,
            "tag":       tag,
        })

        # ── ۵. inter-frame delay فقط اگر صف خالی نیست ───────────────────────
        if not self._tx_queue.empty():
            time.sleep(TX_INTER_FRAME_DELAY_S)

    # ── پردازش بافر دریافتی ───────────────────────────────────────────────────

    def _drain_rx(self) -> None:
        while True:
            pkt, self._rx_buf = parse_frame(self._rx_buf)
            if not pkt:
                break
            if DEBUG_RX:
                self._on_event("rx_parsed_debug", {
                    "bridge_id": self.bridge_id,
                    "addr":      f"0x{pkt['addr']:02X}",
                    "cmd":       f"0x{pkt['cmd']:02X}",
                    "cmd_name":  CMD_NAMES.get(pkt['cmd'], '???'),
                    "data_hex":  pkt['data'].hex(" ").upper(),
                })
            self._on_event("rx", {"bridge_id": self.bridge_id, **pkt})
            self._auto_dispatch(pkt)

    # ── تفسیر خودکار فریم‌های دریافتی ────────────────────────────────────────
    # ✅ این بخش کاملاً دست‌نخورده از نسخه اصلی کپی شده

    def _auto_dispatch(self, pkt: Dict[str, Any]) -> None:
        cmd  = pkt["cmd"]
        data = pkt["data"]

        if cmd in (CMD_BOOT_ACK, CMD_UPDATE_ACK, CMD_BOOT_ERROR):
            with self._ota_lock:
                ota = self._ota
            if ota is not None:
                ota.feed_frame(cmd, data)
            self._on_event({
                CMD_BOOT_ACK:   "boot_ack",
                CMD_UPDATE_ACK: "ota_ack",
                CMD_BOOT_ERROR: "ota_error_raw",
            }[cmd], {"bridge_id": self.bridge_id, "data": data})
            return

        if cmd == CMD_DISCOVERY_RES and len(data) >= 11:
            mac      = mac_str(data[0:6])
            addr     = 0 if data[6] == ADDR_BROADCAST else data[6]
            has_code = bool(data[7])
            node     = self.registry.upsert_discovery(
                mac, self.bridge_id, addr, has_code)
            if addr != 0:
                self._addr_to_mac[addr] = mac
                self._zero_addr_macs.discard(mac)
            else:
                self._zero_addr_macs.add(mac)
            self._on_event("node_updated", {
                "bridge_id": self.bridge_id,
                "mac":       mac,
                "node":      node,
            })
            target = addr if addr != 0 else ADDR_BROADCAST
            self.send(target, CMD_GET_STATUS, b"",
                      "status_auto", expect_feedback=False)
            return

        if cmd == CMD_ADDRESS_ACK and len(data) >= 7:
            mac  = mac_str(data[0:6])
            addr = data[6]
            node = self.registry.upsert_addr_ack(mac, self.bridge_id, addr)
            if addr != 0:
                self._addr_to_mac[addr] = mac
                self._zero_addr_macs.discard(mac)
            self._on_event("addr_confirmed", {
                "bridge_id": self.bridge_id,
                "mac":       mac,
                "addr":      addr,
                "node":      node,
            })
            if addr:
                self.send(addr, CMD_GET_STATUS, b"",
                          "status_after_assign", expect_feedback=False)
            return

        if cmd == CMD_GET_STATUS and len(data) >= 14:
            addr       = data[0]
            mac        = mac_str(data[1:7])
            fw_major   = data[7]
            fw_minor   = data[8]
            in_ota     = bool(data[10])
            touch_mask = data[11]
            adc        = (data[12] << 8) | data[13]
            node = self.registry.upsert_status(
                mac, self.bridge_id, addr, in_ota,
                touch_mask, adc, fw_major, fw_minor,
            )
            if addr != 0:
                self._addr_to_mac[addr] = mac
                self._zero_addr_macs.discard(mac)
            else:
                self._zero_addr_macs.add(mac)
            self._on_event("status_rx", {
                "bridge_id": self.bridge_id,
                "node":      node,
            })
            self.notify_poll_response()
            return

        if cmd == CMD_TOUCH_EVENT and len(data) >= 4:
            sender_addr = data[0]
            mac = self._addr_to_mac.get(sender_addr, "")
            if not mac and sender_addr == 0x00:
                if len(self._zero_addr_macs) == 1:
                    mac = next(iter(self._zero_addr_macs))
            self._on_event("touch", {
                "bridge_id": self.bridge_id,
                "addr":      sender_addr,
                "mac":       mac,
                "event": {
                    0x00: "PRESS",
                    0x11: "RELEASE",
                    0x22: "LONG",
                }.get(data[1], f"0x{data[1]:02X}"),
                "seq": data[2],
                "ch":  data[3],
            })
            return

        if cmd == CMD_ADC_EVENT and len(data) >= 4:
            self._on_event("adc", {
                "bridge_id": self.bridge_id,
                "addr":      data[0],
                "value":     (data[2] << 8) | data[3],
            })
            return

        if cmd == CMD_MOTION_EVENT and len(data) >= 3:
            self._on_event("motion", {
                "bridge_id": self.bridge_id,
                "addr":      data[0],
                "sensor":    data[1],
                "state":     data[2],
            })
            return

        if cmd == CMD_GAME_EVENT and len(data) >= 5:
            from core.protocol import GEVT_NAMES
            evt = data[2]
            self._on_event("game", {
                "bridge_id": self.bridge_id,
                "addr":      data[0],
                "key":       data[1],
                "evt":       evt,
                "evt_name":  GEVT_NAMES.get(evt, f"0x{evt:02X}"),
                "value":     data[3],
                "seq":       data[4],
            })
            return

        if cmd == CMD_ACK:
            if len(data) >= 2:
                self.feedback.on_ack(self.bridge_id, data[1], data[0])
            elif len(data) == 1:
                self.feedback.on_ack(self.bridge_id, pkt["addr"], data[0])
            return

        if cmd == CMD_NACK:
            if len(data) >= 3:
                for_cmd, err, src_addr = data[0], data[1], data[2]
                self.feedback.on_nack(self.bridge_id, src_addr, for_cmd, err)
                with self._ota_lock:
                    ota = self._ota
                if ota is not None and src_addr == ota.addr:
                    ota.feed_frame(CMD_NACK, bytes(data))
            elif len(data) >= 2:
                self.feedback.on_nack(
                    self.bridge_id, pkt["addr"], data[0], data[1])
            return

    def _finalize_ota(self, finished_session: OtaSession) -> None:
        mac = finished_session.mac
        with self._ota_lock:
            if self._ota is finished_session:
                self._ota = None
        self.registry.set_ota_flag(mac, False)
