#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : service.py
توضیح   : RBusService — ارکستراتور اصلی سیستم
           مدیریت bridge‌ها، رجیستری نودها، polling، discovery و OTA
           این فایل هیچ کد UI ندارد و مستقل از PyQt5 است.

           قوانین:
           - بدون فایل config یا json — همه چیز در RAM
           - MAC address = هویت یکتای نود
           - polling هر 1 ثانیه به صورت round-robin
           - discovery هر 10 ثانیه
           - polling و discovery در طول OTA روی آن bridge متوقف می‌شوند

           اصلاح patch-tx:
           - poll_pause_until از 20ms به 150ms افزایش یافت
             (کافی برای batch کامل 12 نود × 10ms inter-frame)
           - send_cmd از TX_PRIORITY_GAME استفاده می‌کند
           - _poll_loop از TX_PRIORITY_POLL استفاده می‌کند
           - broadcast_discovery و assign_address از TX_PRIORITY_SYS
===============================================================================
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.protocol import (
    CMD_GET_STATUS, CMD_DISCOVERY_REQ, CMD_SET_ADDRESS, CMD_CLEAR_ADDRESS,
    CMD_ACTION_STAGE_RGB,
    ADDR_BROADCAST, OTA_PASSWORD,
    POLL_INTERVAL_S, POLL_TIMEOUT_S, DISCOVERY_INTERVAL_S,
    mac_bytes_from_str,
    TX_PRIORITY_GAME, TX_PRIORITY_SYS, TX_PRIORITY_POLL,  # ✅ patch
)

from core.node_registry import NodeRegistry
from core.feedback import FeedbackTracker
from core.bridge_worker import BridgeWorker


class RBusService:
    """
    ارکستراتور اصلی سیستم RBUS.
    نقطه ورود برای GUI و GameBridge به تمام قابلیت‌های سیستم.
    """

    def __init__(self, on_event: Callable[[str, Dict[str, Any]], None]) -> None:
        self.registry = NodeRegistry()
        self.feedback = FeedbackTracker(
            lambda r: self._on_event("feedback", r)
        )
        self.bridges:  Dict[str, BridgeWorker] = {}
        self._on_event_cb = on_event
        self._alive       = True

        # ایندکس round-robin polling برای هر bridge
        self._rr_index: Dict[str, int] = {}

        # ── توقف موقت polling هنگام ارسال دستور کاربر ────────────────────────
        # ✅ patch: مقدار پیش‌فرض 150ms — کافی برای batch کامل 12 نود
        # فرمول: (TX_INTER_FRAME_DELAY_S × max_nodes) + RX_TO_TX_TURNAROUND_S
        #        = (10ms × 12) + 10ms = 130ms → گرد شده به 150ms
        self._poll_pause_until: Dict[str, float] = {}
        self._POLL_PAUSE_DURATION = 0.150   # ✅ patch: 20ms → 150ms

        # ── Game Bridge (بازی‌ها) ─────────────────────────────────────────────
        self.game_bridge: Optional[Any] = None

        # thread پس‌زمینه برای polling و discovery
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="RBusPoller",
            daemon=True,
        )
        self._poll_thread.start()

    def _on_event(self, kind: str, data: Dict[str, Any]) -> None:
        """ارسال رویداد به caller (GUI یا GameBridge)."""
        self._on_event_cb(kind, data)

    # ── مدیریت bridge‌ها ──────────────────────────────────────────────────────

    def add_bridge(self, bridge_id: str, host: str, port: int) -> BridgeWorker:
        """
        اضافه کردن یک bridge جدید و شروع thread آن.
        bridge بلافاصله شروع به کار می‌کند اما متصل نمی‌شود
        تا connect_bridge() فراخوانی شود.
        """
        w = BridgeWorker(
            bridge_id, host, port,
            self.registry, self.feedback, self._on_event,
        )
        self.bridges[bridge_id] = w
        w.start()
        return w

    def connect_bridge(self, bridge_id: str) -> bool:
        """اتصال به یک bridge + discovery چندگانه طی 10 ثانیه اول."""
        w = self.bridges.get(bridge_id)
        if not w:
            return False
        ok = w.connect()
        if ok:
            # زمان‌بندی discovery: 0، 1، 2، 3.5، 5، 7، 10 ثانیه
            delays = [0, 1.0, 2.0, 3.5, 5.0, 7.0, 10.0]
            for delay in delays:
                threading.Timer(
                    delay,
                    lambda b=bridge_id: (
                        self.bridges.get(b) and
                        self.bridges[b].link.connected and
                        self.broadcast_discovery(b)
                    )
                ).start()
        return ok

    def disconnect_bridge(self, bridge_id: str) -> None:
        """قطع اتصال از یک bridge."""
        w = self.bridges.get(bridge_id)
        if w:
            w.disconnect()

    def set_bridge_endpoint(self, bridge_id: str, host: str, port: int) -> None:
        """تغییر آدرس IP و پورت یک bridge."""
        w = self.bridges.get(bridge_id)
        if w:
            w.set_endpoint(host, port)

    def remove_bridge(self, bridge_id: str) -> None:
        """حذف کامل یک bridge و توقف thread آن."""
        w = self.bridges.pop(bridge_id, None)
        if w:
            w.stop()
            w.disconnect()

    # ── API دستورات سطح بالا ─────────────────────────────────────────────────

    def _resend_feedback(self, bridge_id: str, addr: int,
                         cmd: int, data: bytes) -> bool:
        """✅ v4.4.1: ارسال مجدد خودکار دستور بدون‌پاسخ (قانون پروژه)."""
        from core.protocol import build_frame
        w = self.bridges.get(bridge_id)
        if not w or not w.link.connected:
            return False
        # ✅ v4.4.7: در حین OTA هیچ فریمی روی باس نروید
        if w.is_ota_active():
            return False
        return w.link.send(build_frame(addr, cmd, data or b""))

    def send_cmd(self, bridge_id: str, addr: int, cmd: int,
                 data: bytes = b"", tag: str = "",
                 expect_feedback: bool = True) -> bool:
        """
        ارسال یک دستور به نود مشخص روی bridge مشخص.
        خروجی False اگر bridge وجود نداشته باشد یا متصل نباشد.

        ✅ patch: همیشه با TX_PRIORITY_GAME ارسال می‌شود تا
        جلوی دستورات poll (TX_PRIORITY_POLL) قرار بگیرد.
        """
        w = self.bridges.get(bridge_id)
        if not w or not w.link.connected:
            return False

        # ✅ v4.4.3 + v4.4.9: کالیبراسیون کانال RGB
        _GAME_RGB_OPS = (0x42, 0x50, 0x51, 0x64, 0x65, 0x72)
        if cmd in _GAME_RGB_OPS and len(data) >= 4:
            from core.led_mapper import led_mapper
            mr, mg, mb = led_mapper.apply(data[1], data[2], data[3])
            data = bytes([data[0], mr, mg, mb]) + data[4:]
        # ✅ v7.10: 0x45 STAGE_RGB — [R][G][B] بدون بایت key
        elif cmd == CMD_ACTION_STAGE_RGB and len(data) >= 3:
            from core.led_mapper import led_mapper
            mr, mg, mb = led_mapper.apply(data[0], data[1], data[2])
            data = bytes([mr, mg, mb]) + data[3:]

        # ✅ patch: توقف polling به اندازه واقعی زمان ارسال batch
        # فرمول: (inter_frame × max_nodes_per_cmd) + turnaround
        # = (10ms × 12) + 10ms = 130ms → گرد شده به 150ms
        self._poll_pause_until[bridge_id] = (
            time.time() + self._POLL_PAUSE_DURATION
        )

        # ✅ patch: TX_PRIORITY_GAME — دستورات بازی جلوی poll می‌روند
        return bool(w.send(
            addr, cmd, data, tag,
            expect_feedback=expect_feedback,
            priority=TX_PRIORITY_GAME,
        ))

    def broadcast_discovery(self, bridge_id: Optional[str] = None) -> None:
        """
        ارسال DISCOVERY_REQ به یک bridge یا همه bridge‌ها.
        در طول OTA روی یک bridge، آن bridge نادیده گرفته می‌شود.
        """
        if bridge_id:
            targets = [self.bridges[bridge_id]] if bridge_id in self.bridges else []
        else:
            targets = list(self.bridges.values())

        for w in targets:
            if w.link.connected and not w.is_ota_active():
                # ✅ patch: TX_PRIORITY_SYS — اولویت متوسط
                w.send(ADDR_BROADCAST, CMD_DISCOVERY_REQ, b"",
                       "discovery",
                       expect_feedback=False,
                       priority=TX_PRIORITY_SYS)

    def assign_address(self, mac: str, bridge_id: str,
                       new_addr: int) -> Tuple[bool, str]:
        ok, reason = self.registry.can_assign(mac, bridge_id, new_addr)
        if not ok:
            return False, reason

        w = self.bridges.get(bridge_id)
        if not w or not w.link.connected:
            return False, "bridge متصل نیست"

        mac_b   = mac_bytes_from_str(mac)
        payload = mac_b + bytes([new_addr & 0xFF])

        # ✅ patch: TX_PRIORITY_SYS — اولویت متوسط
        w.send(ADDR_BROADCAST, CMD_SET_ADDRESS, payload,
               "assign_addr",
               expect_feedback=False,
               priority=TX_PRIORITY_SYS)

        # آپدیت فوری رجیستری
        self.registry.upsert_addr_ack(mac, bridge_id, new_addr)

        return True, "sent"

    def clear_address(self, mac: str, bridge_id: str) -> bool:
        """پاک کردن آدرس منطقی یک نود."""
        w = self.bridges.get(bridge_id)
        if not w or not w.link.connected:
            return False
        # ✅ patch: TX_PRIORITY_SYS
        w.send(ADDR_BROADCAST, CMD_CLEAR_ADDRESS,
               mac_bytes_from_str(mac),
               "clear_addr",
               expect_feedback=False,
               priority=TX_PRIORITY_SYS)
        # v4.2.5: وضعیت محلی bridge_worker را آپدیت کن
        w.notify_cleared(mac)
        return True

    # ── API مدیریت OTA ────────────────────────────────────────────────────────

    def start_ota(self, bridge_id: str, mac: str, addr: int,
                  firmware: bytes,
                  password: bytes = OTA_PASSWORD) -> Tuple[bool, str]:
        """
        شروع به‌روزرسانی firmware یک نود.
        بررسی‌های اولیه: bridge متصل باشد، نود در رجیستری باشد،
        آدرس داشته باشد، firmware معتبر باشد.
        """
        w = self.bridges.get(bridge_id)
        if not w:
            return False, "bridge پیدا نشد"
        if not w.link.connected:
            return False, "bridge متصل نیست"
        if not firmware:
            return False, "firmware خالی است"
        n = self.registry.get(mac)
        if not n:
            return False, "نود در رجیستری پیدا نشد"
        if addr == 0:
            return False, "نود آدرس منطقی ندارد"
        return w.start_ota(mac, addr, firmware, password)

    def abort_ota(self, bridge_id: str) -> None:
        """لغو session OTA جاری روی یک bridge."""
        w = self.bridges.get(bridge_id)
        if w:
            w.abort_ota()

    def get_ota_status(self, bridge_id: str) -> Optional[Dict[str, Any]]:
        """دریافت وضعیت جاری OTA روی یک bridge."""
        w = self.bridges.get(bridge_id)
        return w.get_ota_status() if w else None

    def load_firmware_file(self, path: str) -> Tuple[Optional[bytes], str]:
        """
        خواندن و بررسی اولیه فایل firmware قبل از شروع OTA.
        بررسی‌ها: وجود فایل، حداقل اندازه، بایت magic ESP8266.
        """
        if not os.path.exists(path):
            return None, "فایل پیدا نشد"
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            return None, str(e)
        if len(data) < 1024:
            return None, "فایل خیلی کوچک است (کمتر از 1KB) — احتمالاً firmware معتبر نیست"
        if data[0] != 0xE9:
            return None, "تصویر ESP8266 نامعتبر است (بایت magic باید 0xE9 باشد)"
        return data, "OK"

    # ── حلقه پس‌زمینه: polling و discovery ───────────────────────────────────

    def _poll_loop(self) -> None:
        """
        Adaptive Polling Loop — v4.4.0 + patch-tx
        ─────────────────────────────────────────────────────────────────────
        به جای sleep ثابت، بعد از هر poll منتظر پاسخ نود می‌ماند.
        اگر در POLL_TIMEOUT_S (15ms) پاسخ نرسید → timeout → نود بعدی.
        اگر پاسخ رسید → بلافاصله نود بعدی poll می‌شود.

        ✅ patch: poll با TX_PRIORITY_POLL ارسال می‌شود.
        دستورات بازی (TX_PRIORITY_GAME=0) همیشه جلوی poll (10) هستند.

        قوانین:
        - هر bridge مستقل poll می‌شود
        - نودها بر اساس addr مرتب می‌شوند
        - discovery هر 10 ثانیه
        - sweep_timeouts هر 20 iteration
        - در طول OTA آن bridge skip می‌شود
        - اگر poll_pause_until فعال باشد آن bridge skip می‌شود
        ─────────────────────────────────────────────────────────────────────
        """
        last_disc     = 0.0
        sweep_counter = 0

        while self._alive:
            now = time.time()

            # ── sweep_timeouts هر 20 iteration ───────────────────────────────
            sweep_counter += 1
            if sweep_counter >= 20:
                sweep_counter = 0
                self.feedback.sweep_timeouts(resend=self._resend_feedback)

            # ── discovery broadcast هر 10 ثانیه ──────────────────────────────
            if now - last_disc >= DISCOVERY_INTERVAL_S:
                last_disc = now
                self.broadcast_discovery()

            # ── polling مستقل هر bridge ───────────────────────────────────────
            polled_any = False

            for bid, w in list(self.bridges.items()):
                if not w.link.connected or w.is_ota_active():
                    continue

                # ── بررسی توقف موقت polling ───────────────────────────────────
                # ✅ patch: pause_duration = 150ms (کافی برای batch 12 نود)
                if now < self._poll_pause_until.get(bid, 0.0):
                    continue

                # نودها همیشه بر اساس addr مرتب می‌شوند
                nodes = sorted(
                    [
                        n for n in self.registry.by_bridge(bid)
                        if n.addr != 0 and not n.in_ota
                    ],
                    key=lambda n: n.addr,
                )
                if not nodes:
                    continue

                idx  = self._rr_index.get(bid, 0) % len(nodes)
                node = nodes[idx]
                self._rr_index[bid] = (idx + 1) % len(nodes)

                # ── reset event قبل از ارسال ──────────────────────────────────
                w._poll_ack.clear()

                # ── ارسال GET_STATUS با اولویت پایین ─────────────────────────
                # ✅ patch: TX_PRIORITY_POLL=10 — همیشه بعد از دستورات بازی
                w.send(node.addr, CMD_GET_STATUS, b"",
                       tag="poll",
                       expect_feedback=False,
                       priority=TX_PRIORITY_POLL)

                # ── انتظار برای پاسخ یا timeout ──────────────────────────────
                w._poll_ack.wait(timeout=POLL_TIMEOUT_S)

                polled_any = True

            # ── اگر هیچ bridge فعالی نبود → کمی استراحت ─────────────────────
            if not polled_any:
                time.sleep(0.005)

    def shutdown(self) -> None:
        """توقف کامل سرویس و قطع همه اتصالات."""
        self._alive = False
        for w in list(self.bridges.values()):
            w.stop()
            w.disconnect()
