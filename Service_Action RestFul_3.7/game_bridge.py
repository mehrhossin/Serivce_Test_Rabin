#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : game_bridge.py
توضیح   : GameBridgeServer — لایه ارتباطی بین RBusService و بازی Unity
           پروتکل: TCP localhost:9000
           فریم‌بندی: [4 بایت LE = طول payload] + [JSON UTF-8]
           جهت Game→Service: دستورات بازی به نودها
           جهت Service→Game: رویدادهای نودها به بازی (push)

           معماری thread‌ها:
           - یک thread اصلی: accept کلاینت‌های جدید
           - یک thread per client: دریافت پیام از آن کلاینت
           - push رویدادها از service به همه کلاینت‌ها (broadcast)
             در GUI thread از طریق on_service_event() فراخوانی می‌شود

           قوانین thread-safety:
           - _clients_lock فقط برای خواندن/نوشتن دیکشنری _clients گرفته می‌شود
           - هیچ‌گاه در حین نگه‌داشتن _clients_lock، send() فراخوانی نمی‌شود
             (جلوگیری از deadlock بین accept_loop و client_thread)
           - هر _ClientHandler یک _send_lock مستقل دارد
           - _recv_exact بین socket.timeout (ادامه) و OSError (قطع) تفکیک می‌کند
===============================================================================
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from core.protocol import (
    CMD_SET_CHANNEL_COLOR, CMD_SET_BRIGHTNESS, CMD_PIXEL_EFFECT,
    CMD_STAGE_LOCK, CMD_STAGE_UNLOCK, CMD_RESET, CMD_GET_STATUS,
    CMD_CLEAR_ALL, ADDR_BROADCAST,
    CMD_ACTION_RING, CMD_ACTION_STAGE_RGB,                 # ✅ v4.4.9: 0x42/0x45
    CMD_SET_KEY_DISPLAY, CMD_MOTION_MONITOR_1, CMD_MOTION_MONITOR_2,  # 0x50/0x54/0x55
)
from service import RBusService

# ---- ثابت‌های پروتکل GameBridge -------------------------------------------
GB_HOST           = "127.0.0.1"
GB_PORT           = 9000
GB_MAX_CLIENTS    = 8        # حداکثر کلاینت همزمان
GB_RECV_TIMEOUT_S = 1.0      # timeout recv — فقط برای بررسی _alive، نه قطع اتصال
GB_MAX_MSG_SIZE   = 65536    # حداکثر اندازه یک پیام JSON (بایت)
GB_HEADER_SIZE    = 4        # اندازه هدر length-prefix (بایت)GameBridgeServer
GB_ACCEPT_TIMEOUT = 0.5      # timeout حلقه accept برای بررسی _alive


# =============================================================================
# ابزارهای فریم‌بندی
# =============================================================================

def _encode_msg(obj: Dict[str, Any]) -> bytes:
    """
    تبدیل دیکشنری Python به فریم GameBridge.
    ساختار: [4 بایت LE = طول] + [JSON UTF-8]
    """
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(payload)) + payload


def _recv_exact(sock: socket.socket, n: int,
                alive_flag: "list[bool]") -> Optional[bytes]:
    """
    دریافت دقیقاً n بایت از socket.

    تفکیک دقیق خطاها:
    - socket.timeout  → ادامه حلقه (بررسی alive_flag)
    - recv() == b""   → peer اتصال را بست → None
    - OSError         → خطای واقعی → None

    alive_flag: یک list تک‌عنصری [bool] که از _ClientHandler.run() پاس می‌شود
                تا در صورت stop() حلقه خارج شود.
    """
    buf = b""
    while len(buf) < n:
        if not alive_flag[0]:
            return None
        try:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                # اتصال توسط peer بسته شد
                return None
            buf += chunk
        except socket.timeout:
            # timeout موقت — بررسی alive و ادامه
            continue
        except OSError:
            # خطای واقعی socket
            return None
    return buf


def _recv_msg(sock: socket.socket,
              alive_flag: "list[bool]") -> Optional[Dict[str, Any]]:
    """
    دریافت یک پیام کامل از socket (length-prefix framing).
    خروجی: دیکشنری پیام یا None فقط در صورت قطع واقعی اتصال.
    """
    header = _recv_exact(sock, GB_HEADER_SIZE, alive_flag)
    if header is None:
        return None
    length = struct.unpack("<I", header)[0]
    if length == 0 or length > GB_MAX_MSG_SIZE:
        # پیام نامعتبر — اتصال را قطع کن
        return None
    payload = _recv_exact(sock, length, alive_flag)
    if payload is None:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


# =============================================================================
# مدیریت یک کلاینت متصل
# =============================================================================

class _ClientHandler(threading.Thread):
    """
    یک thread برای دریافت پیام از یک کلاینت Unity.

    نکات thread-safety:
    - _send_lock فقط برای sendall استفاده می‌شود
    - alive_flag یک list است تا _recv_exact بتواند بدون race condition بخواند
    - on_disconnect پس از خروج از حلقه recv فراخوانی می‌شود
    """

    def __init__(self, client_id: int, sock: socket.socket,
                 addr: tuple,
                 on_message: Callable[[int, Dict[str, Any]], None],
                 on_disconnect: Callable[[int], None]) -> None:
        super().__init__(name=f"GBClient-{client_id}", daemon=True)
        self.client_id        = client_id
        self._sock            = sock
        self._addr            = addr
        self._on_message      = on_message
        self._on_disconnect   = on_disconnect
        # list تک‌عنصری برای اشتراک‌گذاری با _recv_exact بدون race condition
        self._alive_flag: list[bool] = [True]
        self._send_lock       = threading.Lock()

    @property
    def alive(self) -> bool:
        return self._alive_flag[0]

    def send(self, obj: Dict[str, Any]) -> bool:
        """
        ارسال یک پیام JSON به این کلاینت.
        thread-safe — چند thread می‌توانند همزمان فراخوانی کنند.
        خروجی False اگر اتصال قطع شده باشد.
        """
        if not self._alive_flag[0]:
            return False
        with self._send_lock:
            try:
                self._sock.sendall(_encode_msg(obj))
                return True
            except OSError:
                self._alive_flag[0] = False
                return False

    def stop(self) -> None:
        """توقف thread — socket بسته می‌شود تا recv از block خارج شود."""
        self._alive_flag[0] = False
        try:
            self._sock.close()
        except OSError:
            pass

    def run(self) -> None:
        """
        حلقه دریافت پیام از کلاینت.
        socket.timeout باعث قطع نمی‌شود — فقط alive_flag بررسی می‌شود.
        """
        self._sock.settimeout(GB_RECV_TIMEOUT_S)
        while self._alive_flag[0]:
            msg = _recv_msg(self._sock, self._alive_flag)
            if msg is None:
                # قطع واقعی اتصال یا stop() فراخوانی شده
                break
            # پردازش پیام — بیرون از هر lock
            self._on_message(self.client_id, msg)

        self._alive_flag[0] = False
        self._on_disconnect(self.client_id)


# =============================================================================
# سرور اصلی GameBridge
# =============================================================================

class GameBridgeServer:
    """
    سرور TCP که بازی Unity (یا هر کلاینت دیگری) به آن وصل می‌شود.

    قانون اصلی thread-safety:
    ┌─────────────────────────────────────────────────────────┐
    │  _clients_lock فقط برای خواندن/نوشتن _clients گرفته    │
    │  می‌شود. هیچ‌گاه در حین نگه‌داشتن این lock، متد        │
    │  send() یا هیچ عملیات blocking دیگری فراخوانی نمی‌شود. │
    └─────────────────────────────────────────────────────────┘
    """

    def __init__(self, service: RBusService,
                 host: str = GB_HOST,
                 port: int = GB_PORT,
                 on_log: Optional[Callable[[str], None]] = None) -> None:
        self.service   = service
        self.host      = host
        self.port      = port
        self._on_log   = on_log or (lambda msg: None)

        # دیکشنری کلاینت‌های متصل: client_id → _ClientHandler
        self._clients:      Dict[int, _ClientHandler] = {}
        self._clients_lock  = threading.Lock()
        self._next_id       = 0

        self._server_sock:  Optional[socket.socket] = None
        self._alive         = False
        self._accept_thread: Optional[threading.Thread] = None

    # =========================================================================
    # مدیریت سرور
    # =========================================================================

    def start(self) -> bool:
        """
        شروع سرور و thread accept.
        خروجی False اگر پورت در دسترس نباشد.
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(GB_MAX_CLIENTS)
            s.settimeout(GB_ACCEPT_TIMEOUT)
            self._server_sock = s
            self._alive       = True
        except OSError as e:
            self._on_log(
                f"❌ GameBridge: خطای bind روی "
                f"{self.host}:{self.port}: {e}"
            )
            return False

        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="GBAccept",
            daemon=True,
        )
        self._accept_thread.start()
        self._on_log(f"🎮 GameBridge شروع به کار کرد: {self.host}:{self.port}")
        return True

    def stop(self) -> None:
        """توقف سرور و قطع همه کلاینت‌ها."""
        self._alive = False

        # بستن server socket تا accept از block خارج شود
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass

        # کپی handler‌ها بیرون از lock، سپس stop بیرون از lock
        with self._clients_lock:
            handlers = list(self._clients.values())
            self._clients.clear()

        for h in handlers:
            h.stop()

        self._on_log("🔴 GameBridge متوقف شد")

    def is_running(self) -> bool:
        return self._alive

    def client_count(self) -> int:
        with self._clients_lock:
            return len(self._clients)

    # =========================================================================
    # حلقه accept
    # =========================================================================

    def _accept_loop(self) -> None:
        """thread پذیرش کلاینت‌های جدید."""
        while self._alive:
            try:
                sock, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            # بررسی ظرفیت — فقط با lock کوتاه
            with self._clients_lock:
                if len(self._clients) >= GB_MAX_CLIENTS:
                    # ظرفیت پر — رد کردن بیرون از lock
                    pass_to_reject = True
                else:
                    cid = self._next_id
                    self._next_id += 1
                    handler = _ClientHandler(
                        cid, sock, addr,
                        on_message=self._on_client_message,
                        on_disconnect=self._on_client_disconnect,
                    )
                    self._clients[cid] = handler
                    pass_to_reject = False

            if pass_to_reject:
                # ارسال خطا و بستن — بیرون از lock
                try:
                    sock.sendall(_encode_msg({
                        "type": "error",
                        "data": {"reason": "server_full"},
                    }))
                    sock.close()
                except OSError:
                    pass
                self._on_log(
                    f"⚠️ GameBridge: کلاینت رد شد (ظرفیت پر: {GB_MAX_CLIENTS})"
                )
                continue

            # start و initial_state کاملاً بیرون از lock
            handler.start()
            self._on_log(
                f"🟢 GameBridge: کلاینت #{cid} متصل شد "
                f"({addr[0]}:{addr[1]}) — مجموع: {self.client_count()}"
            )
            self._send_initial_state(handler)

    # =========================================================================
    # مدیریت رویدادهای کلاینت
    # =========================================================================

    def _on_client_disconnect(self, cid: int) -> None:
        """هنگام قطع اتصال یک کلاینت — فقط از دیکشنری حذف می‌شود."""
        with self._clients_lock:
            self._clients.pop(cid, None)
        self._on_log(
            f"🔴 GameBridge: کلاینت #{cid} قطع شد — "
            f"مجموع: {self.client_count()}"
        )

    def _on_client_message(self, cid: int, msg: Dict[str, Any]) -> None:
        """
        پردازش پیام دریافتی از یک کلاینت.

        جریان:
        1. dispatch (بدون هیچ lock)
        2. ساخت ack payload
        3. پیدا کردن handler با lock کوتاه
        4. ارسال بیرون از lock

        هیچ‌گاه در حین نگه‌داشتن _clients_lock، send() فراخوانی نمی‌شود.
        """
        msg_type = msg.get("type", "")
        data     = msg.get("data", {})
        if not isinstance(data, dict):
            data = {}

        # مرحله 1: dispatch — بدون هیچ lock
        try:
            result = self._dispatch(msg_type, data)
        except Exception as e:
            result = {"ok": False, "reason": str(e)}
            self._on_log(
                f"⚠️ GameBridge: خطا در '{msg_type}' از #{cid}: {e}"
            )

        # مرحله 2: ساخت ack — همه کلیدهای اضافه (nodes, bridges, ts, ...) کپی می‌شوند
        ack_payload: Dict[str, Any] = {
            "for":    msg_type,
            "ok":     result.get("ok", True),
            "reason": result.get("reason", ""),
        }
        for k, v in result.items():
            if k not in ("ok", "reason"):
                ack_payload[k] = v

        ack_msg = {"type": "ack", "data": ack_payload}

        # مرحله 3: پیدا کردن handler با lock کوتاه
        with self._clients_lock:
            handler = self._clients.get(cid)

        # مرحله 4: ارسال کاملاً بیرون از lock
        if handler:
            handler.send(ack_msg)

    # =========================================================================
    # dispatch: Game → Service
    # =========================================================================

    def _dispatch(self, msg_type: str,
                  data: Dict[str, Any]) -> Dict[str, Any]:
        """
        تبدیل پیام JSON کلاینت به دستور RBusService.
        خروجی: dict با کلید "ok" و در صورت نیاز "nodes"/"bridges"/...
        این متد هیچ lock نمی‌گیرد.
        """
        svc = self.service

        if msg_type == "node_cmd":
            bid  = data.get("bridge_id", "")
            addr = (int(data["addr"], 16)
                    if isinstance(data.get("addr"), str)
                    else int(data.get("addr", 0xFF)))
            cmd  = (int(data["cmd"], 16)
                    if isinstance(data.get("cmd"), str)
                    else int(data.get("cmd", 0)))
            raw  = data.get("data", "")
            payload = bytes.fromhex(raw) if raw else b""
            ok = svc.send_cmd(bid, addr, cmd, payload, tag="gb_node_cmd")
            return {"ok": ok, "reason": "" if ok else "bridge not connected"}

        if msg_type == "set_color":
            from core.led_mapper import led_mapper          # ← اضافه
            bid  = data.get("bridge_id", "")
            addr = int(data.get("addr", 0xFF))
            ch   = int(data.get("ch", 1))
            r    = int(data.get("r", 0))
            g    = int(data.get("g", 0))
            b    = int(data.get("b", 0))
            payload = led_mapper.apply_bytes(ch, r, g, b)   # ← mapper اعمال می‌شود
            ok = svc.send_cmd(
                bid, addr, CMD_SET_CHANNEL_COLOR,
                payload, tag="gb_color",
            )
            return {"ok": ok}


        if msg_type == "set_brightness":
            bid   = data.get("bridge_id", "")
            addr  = int(data.get("addr", 0xFF))
            value = max(0, min(255, int(data.get("value", 128))))
            ok = svc.send_cmd(
                bid, addr, CMD_SET_BRIGHTNESS,
                bytes([value]), tag="gb_brightness",
            )
            return {"ok": ok}

        if msg_type == "pixel_effect":
            bid    = data.get("bridge_id", "")
            addr   = int(data.get("addr", 0xFF))
            effect = int(data.get("effect", 0x01))
            ch     = int(data.get("ch", 1))
            ok = svc.send_cmd(
                bid, addr, CMD_PIXEL_EFFECT,
                bytes([effect, ch]), tag="gb_effect",
            )
            return {"ok": ok}

        if msg_type == "stage_lock":
            bid  = data.get("bridge_id", "")
            addr = int(data.get("addr", 0xFF))
            ok = svc.send_cmd(
                bid, addr, CMD_STAGE_LOCK,
                bytes([0x01]), tag="gb_lock",
            )
            return {"ok": ok}

        if msg_type == "stage_unlock":
            bid  = data.get("bridge_id", "")
            addr = int(data.get("addr", 0xFF))
            ok = svc.send_cmd(
                bid, addr, CMD_STAGE_UNLOCK,
                bytes([0x01]), tag="gb_unlock",
            )
            return {"ok": ok}

        if msg_type == "reset_node":
            bid  = data.get("bridge_id", "")
            addr = int(data.get("addr", 0xFF))
            ok = svc.send_cmd(bid, addr, CMD_RESET, b"", tag="gb_reset")
            return {"ok": ok}

        if msg_type == "get_status":
            bid  = data.get("bridge_id", "")
            addr = int(data.get("addr", 0xFF))
            ok = svc.send_cmd(
                bid, addr, CMD_GET_STATUS, b"",
                tag="gb_status", expect_feedback=False,
            )
            return {"ok": ok}

        if msg_type == "broadcast_discovery":
            bid = data.get("bridge_id") or None
            svc.broadcast_discovery(bid)
            return {"ok": True}

        if msg_type == "get_nodes":
            bid   = data.get("bridge_id") or None
            nodes = (svc.registry.by_bridge(bid)
                     if bid else svc.registry.all())
            return {
                "ok": True,
                "nodes": [
                    {
                        "mac":        n.mac,
                        "bridge_id":  n.bridge_id,
                        "addr":       n.addr,
                        "status":     n.status,
                        "fw":         n.fw_str,
                        "touch_mask": n.touch_mask,
                        "adc":        n.adc,
                    }
                    for n in nodes
                ],
            }

        if msg_type == "get_bridges":
            bridges = []
            for bid, w in svc.bridges.items():
                bridges.append({
                    "bridge_id":  bid,
                    "host":       w.host,
                    "port":       w.port,
                    "connected":  w.link.connected,
                    "ota_active": w.is_ota_active(),
                })
            return {"ok": True, "bridges": bridges}

        if msg_type == "ping":
            # ts کلاینت رو echo می‌کنیم تا latency دقیق محاسبه شود
            return {"ok": True, "ts": data.get("ts", time.time())}

        # =========================================================================
        # Action Game — مسیر یکتای پارس: ActionGame (فعال از تب Games) را صدا بزن.
        # ✅ Node Bus: چون «بازیکن N همیشه روی بریج N است» و آدرس در هر بریج
        # مستقل است، ActionGame.bridge_id را برای روتینگ دقیق می‌گیرد.
        # اگر ActionGame فعال نبود → fallback به handler داخلی (بدون شکستن ساختار).
        if msg_type in ("action_L", "action_S", "action_get_groups"):
            action = getattr(svc, "_action_game", None)
            if action is not None:
                return action.on_service_event(msg_type, data)
            # fallback هنگام غیرفعال بودن ActionGame — بدون شکستن ساختار
            if msg_type == "action_get_groups":
                return {
                    "ok": True,
                    "groups": {1: [1,2,3,4], 2: [5,6,7,8,9,10], 3: [11], 4: [12]},
                }
            return {"ok": False, "reason": "action_game_not_active"}

        if msg_type == "hide_cmd":
            return self._handle_hide(data)
        
        self._on_log(f"⚠️ GameBridge: دستور ناشناخته '{msg_type}'")
        return {"ok": False, "reason": f"unknown type: {msg_type}"}

    # =========================================================================
    # ارسال به کلاینت‌ها
    # =========================================================================

    def _get_handler(self, cid: int) -> Optional[_ClientHandler]:
        """دریافت handler با lock کوتاه."""
        with self._clients_lock:
            return self._clients.get(cid)

    def _send_initial_state(self, handler: _ClientHandler) -> None:
        """
        ارسال وضعیت فعلی bridge‌ها و نودها به کلاینت تازه‌وارد.
        مستقیم به handler ارسال می‌شود — بدون عبور از _clients_lock.
        """
        for bid, w in self.service.bridges.items():
            handler.send({
                "type": "bridge_status",
                "data": {
                    "bridge_id": bid,
                    "connected": w.link.connected,
                    "host":      w.host,
                    "port":      w.port,
                },
            })
        for n in self.service.registry.all():
            handler.send({
                "type": "node_online",
                "data": {
                    "bridge_id":  n.bridge_id,
                    "mac":        n.mac,
                    "addr":       n.addr,
                    "fw":         n.fw_str,
                    "status":     n.status,
                    "touch_mask": n.touch_mask,
                    "adc":        n.adc,
                },
            })

    def broadcast(self, obj: Dict[str, Any]) -> None:
        """
        ارسال پیام به همه کلاینت‌های متصل.

        الگو:
        1. کپی handler‌ها با lock کوتاه
        2. ارسال کاملاً بیرون از lock
        3. حذف handler‌های مرده با lock کوتاه
        """
        with self._clients_lock:
            targets = list(self._clients.values())

        dead: List[int] = []
        for h in targets:
            if not h.send(obj):
                dead.append(h.client_id)

        if dead:
            with self._clients_lock:
                for cid in dead:
                    self._clients.pop(cid, None)

    # =========================================================================
    # دریافت رویدادها از RBusService (در GUI thread)
    # =========================================================================

    def on_service_event(self, kind: str, data: Dict[str, Any]) -> None:
        """
        توسط MainWindow._handle_event() در GUI thread فراخوانی می‌شود.
        رویدادهای مرتبط با بازی را به همه کلاینت‌های Unity push می‌کند.
        """
        if not self._alive or self.client_count() == 0:
            return

        if kind == "touch":
            self.broadcast({
                "type": "touch_event",
                "data": {
                    "bridge_id": data.get("bridge_id"),
                    "addr":      data.get("addr"),
                    "ch":        data.get("ch"),
                    "event":     data.get("event"),
                    "seq":       data.get("seq", 0),
                    "ts":        time.time(),
                },
            })

        elif kind == "adc":
            self.broadcast({
                "type": "adc_event",
                "data": {
                    "bridge_id": data.get("bridge_id"),
                    "addr":      data.get("addr"),
                    "value":     data.get("value"),
                    "ts":        time.time(),
                },
            })

        elif kind in ("node_updated", "addr_confirmed", "status_rx"):
            node = data.get("node")
            if node is None:
                return
            self.broadcast({
                "type": "node_online",
                "data": {
                    "bridge_id":  node.bridge_id,
                    "mac":        node.mac,
                    "addr":       node.addr,
                    "fw":         node.fw_str,
                    "status":     node.status,
                    "touch_mask": node.touch_mask,
                    "adc":        node.adc,
                },
            })

        elif kind in ("connected", "disconnected", "conn_error"):
            self.broadcast({
                "type": "bridge_status",
                "data": {
                    "bridge_id": data.get("bridge_id"),
                    "connected": kind == "connected",
                    "host":      data.get("host", ""),
                    "port":      data.get("port", 0),
                },
            })

        elif kind == "feedback":
            self.broadcast({
                "type": "feedback",
                "data": {
                    "bridge_id":  data.get("bridge_id"),
                    "addr":       data.get("addr"),
                    "cmd_name":   data.get("cmd_name"),
                    "code":       data.get("code"),
                    "tag":        data.get("tag"),
                    "latency_ms": data.get("latency_ms"),
                },
            })

        elif kind == "ota_done":
            self.broadcast({
                "type": "ota_done",
                "data": {
                    "bridge_id": data.get("bridge_id"),
                    "mac":       data.get("mac"),
                    "addr":      data.get("addr"),
                },
            })

        elif kind == "ota_error":
            self.broadcast({
                "type": "ota_error",
                "data": {
                    "bridge_id": data.get("bridge_id"),
                    "mac":       data.get("mac"),
                    "reason":    data.get("reason"),
                },
            })

        elif kind == "motion":
            sensor = data.get("sensor", 0)
            # فقط سنسور 1 کد 114 را می‌فرستد
            if sensor == 1:
                self.broadcast({
                    "type": "hide_event",
                    "data": {
                        "bridge_id": data.get("bridge_id"),
                        "addr":      data.get("addr"),
                        "code":      114,
                        "sensor":    sensor,
                        "ts":        time.time(),
                    }
                })

      # =========================================================================
    # Action Game — پردازش دستورات L و S
    # =========================================================================

    _ACTION_GROUPS: Dict[int, List[int]] = {
        1: [1, 2, 3, 4],
        2: [5, 6, 7, 8, 9, 10],
        3: [11],
        4: [12],
    }

    _ACTION_GROUP_MAP: Dict[str, List[int]] = {
        "1": [1],
        "2": [2],
        "4": [3],
        "3": [1, 2],
        "5": [1, 3],
        "6": [2, 3],
        "7": [1, 2, 3],
    }

    _ACTION_COLOR_MAP: Dict[str, tuple] = {
        "1": (0,   0,   255),
        "2": (255, 200, 0  ),
        "3": (128, 0,   255),
        "4": (255, 0,   128),
        "5": (255, 255, 255),
    }

    _ACTION_HEX_NODE: Dict[str, int] = {
        **{str(i): i for i in range(10)},
        "A": 10, "B": 11, "C": 12,
        "D": 13, "E": 14, "F": 15,
    }

    def _handle_action_L(self, data: dict) -> dict:
        raw = data.get("raw", "")
        if len(raw) < 3 or raw[0].upper() != "L":
            return {"ok": False, "reason": "bad_format"}

        # ⚠️ پروتکل نرم‌افزار↔سرویس: General = L{c}{s}{p}
        #   → c=رنگ(raw[1]) · s=ماسک(raw[2]) · p=چک‌سام(raw[3])
        #   (قبلاً raw درست opposite خوانده می‌شد؛ فیکس شد تا با ActionGame هماهنگ باشد.)
        c_char = raw[1]
        g_char = raw[2]
        chk_recv = raw[3] if len(raw) > 3 else ""

        # بررسی checksum  (chr(ord(c)+ord(s)))
        if chk_recv and chk_recv != chr(ord(c_char) + ord(g_char)):
            self._on_log(f"⚠️ action_L: checksum اشتباه")
            return {"ok": False, "reason": "bad_checksum"}

        # ⚠️ ماسک s را مستقیماً به نودها ترجمه می‌کنیم (بیت۰/۱/۲ = بخش ۱..۴/۵..۱۰/۱۲)
        #   تا از _GROUP_MAP ناهماهنگ (_GROUPS[3]=[11]) رد نشویم و bridge_id را رعایت کنیم.
        from core.rest_api import _mask_to_nodes
        g_char = str(g_char)            # ماسک s (رشته) برای لاگ صادقانه
        nodes  = _mask_to_nodes(g_char) or []
        color  = self._ACTION_COLOR_MAP.get(c_char)

        if not nodes:
            return {"ok": False, "reason": f"invalid_mask:{g_char}"}
        if color is None:
            return {"ok": False, "reason": f"invalid_color:{c_char}"}

        bid = data.get("bridge_id") or None   # ✅ هر IP=بریج — برای routing دقیق
        r, g, b = color
        self._on_log(
            f"🎮 action_L → ماسک '{g_char}' | "
            f"رنگ ({r},{g},{b}) | نودها: {nodes}"
        )

        for addr in nodes:
            self._action_set_color(addr, r, g, b, bid)

        return {"ok": True, "nodes": nodes}

    def _handle_action_S(self, data: dict) -> dict:
        raw = data.get("raw", "")
        if len(raw) < 3 or raw[0].upper() != "S":
            return {"ok": False, "reason": "bad_format"}

        n_char = raw[1].upper()
        c_char = raw[2]

        addr  = self._ACTION_HEX_NODE.get(n_char)
        color = self._ACTION_COLOR_MAP.get(c_char)

        if addr is None:
            return {"ok": False, "reason": f"invalid_node:{n_char}"}
        if color is None:
            return {"ok": False, "reason": f"invalid_color:{c_char}"}

        r, g, b = color
        bid = data.get("bridge_id") or None   # ✅ NodeBus: روتینگ دقیق
        self._on_log(
            f"🎮 action_S → نود {addr} (0x{n_char}) | رنگ ({r},{g},{b})"
        )
        self._action_set_color(addr, r, g, b, bid)

        return {"ok": True, "addr": addr}

    def _action_set_color(self, addr: int, r: int, g: int, b: int,
                          bridge_id: Optional[str] = None) -> None:
        """پیدا کردن نود در رجیستری و ارسال رنگ با اعمال RGB Channel Mapping.

        ✅ v4.4.11: `bridge_id` قابل تزریق است (هر IP=بریج، بریج=۱۲ نود؛ `addr` در هر
        بریج مستقل است). اگر بدهی → دقیقاً همان بریج؛ اگر ندهی → جستجوی همهٔ بریج‌ها
        (رفتار قبلی). این از «همه به اولین بریج» جلوگیری می‌کند.
        """
        # هماهنگ با ActionGame: مپ فقط در send_cmd (قانون درس #۱۰) — این‌جا raw.
        node = None
        if bridge_id:
            node = self.service.registry.get_by_addr(bridge_id, addr)
        else:
            for bid in self.service.bridges:
                node = self.service.registry.get_by_addr(bid, addr)
                if node is not None:
                    break

        if node is None:
            self._on_log(
                f"⚠️ action_set_color: نود {addr} (bridge={bridge_id or '*'}) "
                f"در هیچ bridge‌ای نیست"
            )
            return

        # ✅ v7.3 FIX (باگ اصلی اکشن): قبلاً 0x26 (رنگ کانال) فرستاده می‌شد که
        # ماشین‌حالت لمسِ اکشن را هرگز فعال نمی‌کرد → فید 200ms اجرا نمی‌شد.
        # اکنون opcode درست بازی اکشن: 0x42 ACTION_RING [key r g b]
        # ✅ v4.4.9: ثابت CMD_ACTION_RING (0x42) — بدون عدد خام.
        payload = bytes([0x00, r, g, b])

        ok = self.service.send_cmd(
            node.bridge_id, addr,
            CMD_ACTION_RING, payload,            # 0x42 ACTION_RING
            tag="action_ring", expect_feedback=True,   # قانون: ACK/NACK + retry×2
        )
        if not ok:
            self._on_log(
                f"⚠️ action_set_color: ارسال به نود {addr} "
                f"(bridge={node.bridge_id}) ناموفق"
            )

    # =========================================================================
    # Hide Game — پردازش دستورات QQQ, WWW, NNN, RRR, R...
    # =========================================================================

    def _handle_hide(self, data: dict) -> dict:
        # ✅ هم "raw" (از game_bridge مستقیم) هم "general" (از rest_api) پشتیبانی می‌شود
        raw = (data.get("raw") or data.get("general") or "").strip().upper()
        if not raw:
            return {"ok": False, "reason": "empty_raw"}

        # تابع کمکی برای ارسال بروزکست به همه بریج‌های متصل.
        # ✅ v4.4.9: از service.send_cmd استفاده می‌شود نه w.send مستقیم —
        # تا کالیبراسیون RGB (درس #۱۰) و قواعد broadcast در یک نقطه اعمال شود.
        # broadcast = 0xFF → send_cmd آن را به w.send می‌دهد که expect_feedback=False اجبار می‌کند.
        def _broadcast_cmd(cmd: int, payload: bytes = b""):
            for bid, w in self.service.bridges.items():
                if w.link.connected:
                    self.service.send_cmd(
                        bid, ADDR_BROADCAST, cmd, payload,
                        tag="hide_cmd", expect_feedback=False,
                    )

        # 1. مانیتور سنسور 1
        if raw == "QQQ":
            _broadcast_cmd(CMD_MOTION_MONITOR_1)  # 0x54
            return {"ok": True, "action": "monitor1"}

        # 2. مانیتور سنسور 2
        elif raw == "WWW":
            _broadcast_cmd(CMD_MOTION_MONITOR_2)  # 0x55
            return {"ok": True, "action": "monitor2"}

        # 3. دمو حرکت پیکسلی (Idle)
        elif raw == "NNN":
            _broadcast_cmd(CMD_PIXEL_EFFECT, bytes([0x09]))  # 0x27 PIXEL_EFFECT IDLE
            return {"ok": True, "action": "idle_demo"}

        # 4. ریست میکروها
        elif raw == "RRR":
            _broadcast_cmd(CMD_RESET)  # 0x02
            return {"ok": True, "action": "reset"}

        # 5. دستور اصلی اعمال رنگ و عدد (R[color][Yekan][Dahgan][bitCheck])
        elif raw.startswith("R") and len(raw) == 5:
            color_char = raw[1]
            ones_char  = raw[2]  # یکان
            tens_char  = raw[3]  # دهگان
            chk_char   = raw[4]

            # بررسی checksum
            expected_chk = chr(ord(color_char) + ord(ones_char) + ord(tens_char))
            if chk_char != expected_chk:
                self._on_log(f"⚠️ hide_cmd: checksum اشتباه")
                return {"ok": False, "reason": "bad_checksum"}

            try:
                color_int = int(color_char)
                ones_int  = int(ones_char)
                tens_int  = int(tens_char)
            except ValueError:
                return {"ok": False, "reason": "invalid_number"}

            if not (1 <= color_int <= 5):
                return {"ok": False, "reason": "color_out_of_range"}
            if not (0 <= ones_int <= 9) or not (0 <= tens_int <= 9):
                return {"ok": False, "reason": "number_out_of_range"}

            # ✅ v7.0: 0x50 با RGB یکپارچه [key][r][g][b][tens][ones]
            # ✅ v7.1: unicast به هر نود + انتظار ACK (قانون: میکرو تأیید کند)
            _HIDE_RGB = {1: (0x00, 0x00, 0xFF), 2: (0xFF, 0xFF, 0x00),
                         3: (0xFF, 0x3C, 0x64), 4: (0xFF, 0x3C, 0x00),
                         5: (0xFF, 0xFF, 0xFF)}
            r_, g_, b_ = _HIDE_RGB.get(color_int, (0xFF, 0xFF, 0xFF))
            payload = bytes([0x00, r_, g_, b_, tens_int, ones_int])

            # ✅ v7.2: اگر addr مشخص شد → unicast به نود خاص؛ در غیر این صورت broadcast به همه
            # ✅ v7.2: اگر addr مشخص شد → unicast به نود خاص؛ در غیر این صورت broadcast به همه
            target_addr = data.get("addr")

            if target_addr is not None:
                # unicast — فقط نود مشخص‌شده
                target_addr = int(target_addr)
                sent = 0
                for bid, w in self.service.bridges.items():
                    if not w.link.connected:
                        continue
                    node = self.service.registry.get_by_addr(bid, target_addr)
                    if node and node.addr:
                        self.service.send_cmd(bid, node.addr, CMD_SET_KEY_DISPLAY, payload,
                                            tag="hide_display",
                                            expect_feedback=True)
                        sent += 1
                if sent == 0:
                    self._on_log(f"⚠️ hide_cmd: نود {target_addr} در هیچ bridge‌ای پیدا نشد")
                    return {"ok": False, "reason": f"node_not_found: {target_addr}"}
            else:
                # broadcast — همه نودها (رفتار قبلی)
                sent = 0
                for bid, w in self.service.bridges.items():
                    if not w.link.connected:
                        continue
                    for node in self.service.registry.by_bridge(bid):
                        if node.addr:
                            self.service.send_cmd(bid, node.addr, CMD_SET_KEY_DISPLAY, payload,
                                                tag="hide_display",
                                                expect_feedback=True)
                            sent += 1
                if sent == 0:
                    _broadcast_cmd(CMD_SET_KEY_DISPLAY, payload)

            return {"ok": True, "action": "display"}

            

        return {"ok": False, "reason": f"unknown_hide_cmd: {raw}"}
