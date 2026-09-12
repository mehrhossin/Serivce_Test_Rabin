#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : core/rest_api.py
توضیح   : لایه REST/HTTP برای «نرم‌افزار ↔ سرویس» (بدون وابستگی بیرونی).
           سرویس نقش پل دارد: REST برای نرم‌افزار + RBUS/RS485 برای میکرو.
           این فایل «هیچ چیزی» در مسیر سرویس↔میکرو (RBUS) عوض نمی‌کند؛
           فقط همان مسیر تست‌شدهٔ game_bridge._dispatch را با HTTP/JSON می‌پوشاند.

ثابت‌ها/قوانین:
- مبادله JSON (POST). هیچ ASCII شبیه L.. / NNN در این لایه نیست.
- آدرس‌دهی: bridge_id = "Bridge-N" و addr = شماره نود (1..12) یا 0xFF = برودکست.
- پاسخ: {"ok": true/false, ...} + کد HTTP متناسب (200/400/404).
- رنگ: (r,g,b)=0..255.
===============================================================================
"""

from __future__ import annotations

import json
import ssl          # noqa: F401 — برای آینده (خودسنجیده TLS)؛ فعلاً استفاده نمی‌شود
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http import HTTPStatus
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse


# ── پورت پیش‌فرض REST (به‌درخواست کاربر: 9001، جدا از 9000 متعلق به game_bridge) ──
REST_HOST = "127.0.0.1"
REST_PORT = 9001

# ── توکن ساده برای مرحلهٔ تست (بدون وابستگی JWT) ──
_REST_TOKEN = "RBUS-TEST-TOKEN-0001"
_TOKEN_TTL_S = 3600


# =============================================================================
# یک endpoint مقید به (msg_type, data) که به game_bridge._dispatch می‌رود.
# برای «توافق» بین نرم‌افزارِ جدید (RESTful) و سرویسِ RBUS.
# =============================================================================
class RestApiError(Exception):
    """خطای کاربر (بیرونی) — با هدر مناسب به کلاینت برمی‌گردد."""
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class RestApiServer:
    """
    سرور HTTP/JSON برای مصرف‌کنندهٔ بیرونی (نرم‌افزار).
    به game_bridge._dispatch وصل است؛ پس تمام opcodeها/قوانین سرویس دست‌نخورده‌اند.

    thread-safety: هر درخواست در thread خودش از ThreadingHTTPServer اجرا می‌شود.
    _dispatch هیچ lock داخلی نمی‌گیرد (طبق سند game_bridge) و send_cmd هم
    thread-safe است (lock داخل BridgeWorker). پس یک lock اضافه اینجا نیاز نیست.
    """

    def __init__(
        self,
        service: Any,
        host: str = REST_HOST,
        port: int = REST_PORT,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.service = service
        self.host = host
        self.port = port
        self._on_log = on_log or (lambda msg: None)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        # _last_auth_header حذف شد — thread-unsafe بود (fix v5.0)""
        # ── صف رویداد برای مصرف‌کننده REST (لمس/فیدبک/وضعیت) ──
        #   وایرینگ: run.py (یا GUI) در `_on_event` از این متد صدا می‌زند؛
        #   کلاینت با `GET /api/events/drain` هر چند صد ms یک‌جا برمی‌دارد.
        self._events: "deque[dict]" = deque(maxlen=512)
        self._event_lock = threading.Lock()
        self._event_cond = threading.Condition(self._event_lock)  # realtime: wait/notify
        self._event_since = min(time.time(), 0)  # همان first-ever
        self._query_lock = threading.Lock()
        self._last_query = ""

    # ── صف رویداد (فیدبک میکرو → بازی از طریق REST) ──
    def enqueue_event(self, kind: str, data: dict) -> None:
        """توسط وایرینگِ رویدادهای سرویس صدا زده می‌شود.
        فقط رویدادهای مرتبط با بازی/لمس را به صف REST اضافه می‌کند."""
        if kind not in ("touch", "adc", "feedback", "node_online", "motion",
                        "game"):   # ✅ v6.8: رویدادهای بازی (KEY_HIT/DROP_*/…)
            return
        ev = {"kind": kind, "ts": time.time(), "data": data}
        with self._event_cond:
            self._events.append(ev)
            self._event_cond.notify_all()  # realtime: بیدار کردن long-poll ها

    def _drain_events(self, wait_ms: int = 0) -> Dict[str, Any]:
        """GET /api/events/drain — یک‌جا همهٔ رویدادهای انباشته را برمی‌دارد.
        realtime: اگر wait_ms>0 و صف خالی بود، تا wait_ms منتظر رویداد می‌ماند (long-poll).
        این باعث می‌شود تاچ در 30-50ms به کلاینت برسد بدون polling شلوغ.
        NoFilter: هیچ فیلتر، maxlen 512 با هشدار overflow
        """
        with self._event_cond:
            if not self._events and wait_ms > 0:
                self._event_cond.wait(timeout=wait_ms/1000.0)
            # overflow detection (before clear)
            if len(self._events) == self._events.maxlen:
                try: self._on_log(f"⚠️ REST queue overflow maxlen={self._events.maxlen} — قدیمی‌ها دور انداخته شد")
                except: pass
            batch = list(self._events)
            self._events.clear()
        return {"ok": True, "events": batch, "count": len(batch)}

    # ────────────────────────────────────────── helpers
    def _bridge(self) -> Any:
        """game_bridge (که _dispatch را دارد) — همان واسطی که TCP نرم‌افزار قبلی می‌گرفت."""
        gb = getattr(self.service, "game_bridge", None)
        if gb is None:
            raise RestApiError(HTTPStatus.SERVICE_UNAVAILABLE, "game_bridge not ready")
        return gb

    def _dispatch(self, msg_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._bridge()._dispatch(msg_type, data)

    # ────────────────────────────── مسیرها
    def handle(self, path: str, body: Dict[str, Any], method: str = "POST", auth_header: str = "") -> Dict[str, Any]:
        """
        روتر اصلی REST. path = مسیر بدون query.
        هر مسیر به (msg_type, data) تبدیل و به _dispatch فرستاده می‌شود.
        """
        parts = [p for p in path.split("/") if p]   # ['api','auth','login'] یا ['api','v1',...]
        if len(parts) < 2 or parts[0] != "api":
            raise RestApiError(HTTPStatus.NOT_FOUND, f"unknown path {path}")

        # ═══════════════ قرارداد ApiEndpoint.txt (سرویس ↔ نرم‌افزار) ═══════════════
        if parts[1] == "auth" and len(parts) == 3 and parts[2] == "login":
            return self._handle_login(body)

        # ═══════════════ فیدبک زنده به بازی — GET /api/events/drain ═══════════════
        #   (فیدبک/لمس میکرو → صف سرویس → اینجا؛ کلاینت هر چند صد ms poll می‌کند.)
        if len(parts) == 3 and parts[1] == "events" and parts[2] == "drain" \
                and method.upper() == "GET":
            self._require_auth(auth_header)
            # realtime: ?wait=200 پشتیبانی می‌شود — اگر کلاینت بده، long-poll
            wait_ms = 0
            # body ممکن است شامل wait باشد یا query string
            # query string در handle از _RestHandler پاس داده نمی‌شد، پس از body بخوان
            # و همچنین از path query
            try:
                # سعی از body
                if isinstance(body, dict) and "wait" in body:
                    wait_ms = int(body.get("wait") or 0)
            except Exception:
                wait_ms = 0
            # query string via attribute با lock
            if hasattr(self, "_last_query"):
                try:
                    from urllib.parse import parse_qs
                    with self._query_lock:
                        q = self._last_query or ""
                    qs = parse_qs(q)
                    if "wait" in qs:
                        wait_ms = int(qs["wait"][0])
                except Exception:
                    pass
            wait_ms = max(0, min(wait_ms, 2000))  # سقف 2 ثانیه
            return self._drain_events(wait_ms=wait_ms)

        if parts[1] == "nodes":
            if len(parts) == 3 and parts[2] == "set" and method.upper() in ("PUT", "POST"):
                self._require_auth(auth_header)
                return self._handle_nodes_set(body)
            if len(parts) == 2 and method.upper() == "GET":
                self._require_auth(auth_header)
                # ✅ v6.9: GET /api/nodes?bridge=N — فیلتر بریج (نقشهٔ endpoint قرارداد v5.1)
                try:
                    from urllib.parse import parse_qs
                    with self._query_lock:
                        q = self._last_query or ""
                    qs = parse_qs(q)
                except Exception:
                    qs = {}
                bfilter = None
                if "bridge" in qs:
                    try:
                        bfilter = int(qs["bridge"][0])
                    except (TypeError, ValueError):
                        bfilter = -1
                    if not (1 <= bfilter <= 5):
                        raise RestApiError(HTTPStatus.BAD_REQUEST,
                                           "'bridge' must be 1..5")
                return self._handle_nodes_get(bridge_num=bfilter)
            raise RestApiError(HTTPStatus.NOT_FOUND, f"unknown nodes route {'/'.join(parts)}")

        # ═════ ✅ v6.9 — قرارداد v5.1: node/cmd و game/cmd «حذف کامل» (تصمیم کاربر) ══════

        # ═══════════════ قرارداد v1 (روش قدیمی + سازگاری) ══════════════════════════
        if len(parts) == 3 and parts[1] == "v1" and parts[2] == "raw":
            msg_type = body.get("type", "")
            if not msg_type:
                raise RestApiError(HTTPStatus.BAD_REQUEST, "missing 'type'")
            return self._dispatch(msg_type, body.get("data", {}))

        return self._handle_v1(parts, body)

    # ═══════════════ قرارداد ApiEndpoint.txt — auth ═══════════════
    def _handle_login(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        POST /api/auth/login  ->  {token, expiresIn}
        (برای مرحلهٔ تست ساده؛ هر credential مجاز است — بعداً JWT واقعی شدنی است.)
        """
        username = body.get("username", "")
        password = body.get("password", "")
        if not username or not password:
            raise RestApiError(HTTPStatus.BAD_REQUEST, "username/password required")
        return {"token": _REST_TOKEN, "expiresIn": _TOKEN_TTL_S}

    def _require_auth(self, auth_header: str = "") -> None:
        """بررسی Bearer token روی هدر Authorization — thread-safe."""
        auth = auth_header or ""
        expected = "Bearer " + _REST_TOKEN
        if auth != expected:
            raise RestApiError(HTTPStatus.UNAUTHORIZED, "invalid or missing token")

    # ═══════════════ ✅ v6.9 — قرارداد v5.1: PUT /api/nodes/set (Unified Game Endpoint) ═══════════════
    def _handle_nodes_set(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        ✅ v6.9 (منبع: uploads/CHANGES_v5.1.md) — زبان واحد همهٔ بازی‌ها.
        بدنه: {Game, Bridge, General, SetKey, ActiveBoard, Reset, Idle, Error,
               Arm, Start, Fail, Cancel, Motion, GridEffect, TimerStart, TimerStop,
               TeslaStart, LightningFx, DropArm, DropCancel, DropStatus,
               color, sec, key, drops, trail, act_pct, flags, length}

        قوانین (تصمیم‌های تأییدشدهٔ کاربر 2026-09-06):
          · Game اجباری: Action|Hide|Vibron|Tesla — بدون Game → 400 (زبان سخت‌گیرانه)
          · Bridge عددی: 1..5 → همان بریج · 0/غایب → همهٔ بریج‌های سرویس
          · رنگ: «1» پالت کاراکتری یا «FF0080» hex6 خام (هر دو)
          · node/cmd و game/cmd حذف کامل شدند — تنها زبان همین endpoint است
        اعتبارسنجی «شکل» این‌جا؛ ترجمهٔ بایت فقط در ActionGame (قانون طلایی MEMORY).
        ActiveBoard/Error: پذیرفته می‌شوند اما معنا/opcode در قرارداد تعریف نشده —
        به میکرو نمی‌روند (در CONTRACT.md مستند شده).
        """
        if not isinstance(body, dict):
            raise RestApiError(HTTPStatus.BAD_REQUEST, "JSON body required")

        game_raw = body.get("Game")
        if game_raw in (None, ""):
            raise RestApiError(HTTPStatus.BAD_REQUEST,
                               "'Game' required (Action|Hide|Vibron|Tesla)")
        game = str(game_raw).strip().capitalize()
        if game not in ("Action", "Hide", "Vibron", "Tesla"):
            raise RestApiError(HTTPStatus.BAD_REQUEST,
                               "'Game' must be Action|Hide|Vibron|Tesla")

        # ── Bridge: 1..5 → Bridge-N · 0/غایب → همه ──
        br = body.get("Bridge", 0)
        if isinstance(br, bool) or not isinstance(br, int):
            raise RestApiError(HTTPStatus.BAD_REQUEST,
                               "'Bridge' must be int 0..5 (0=all bridges)")
        if br != 0 and not (1 <= br <= 5):
            raise RestApiError(HTTPStatus.BAD_REQUEST, "'Bridge' must be 0..5")
        reg_bids = {n.bridge_id for n in self.service.registry.all()}
        if br == 0:
            # همهٔ بریج‌های متصل؛ اگر هیچ‌کدام متصل نیست (حالت دمو) → بریج‌های رجیستری
            targets = list(self.service.bridges) or sorted(reg_bids)
        else:
            bid = f"Bridge-{br}"
            known = bid in self.service.bridges or bid in reg_bids
            targets = [bid] if known else []

        action = getattr(self.service, "_action_game", None)
        if action is None:
            return {"statusCode": 200, "message": "success", "ok": True,
                    "applied": {"ok": False, "game": game,
                                "reason": "action_game_not_active"}}

        general = str(body.get("General", "") or "")
        set_key = str(body.get("SetKey", "") or "")
        reset = bool(body.get("Reset", False))
        idle = bool(body.get("Idle", False))

        if reset and idle:
            raise RestApiError(HTTPStatus.BAD_REQUEST,
                               "'Reset' and 'Idle' are exclusive")

        
        def _has_cmd() -> bool:
            if game == "Action":
                return bool(general or set_key or body.get("AllOff"))  # ✅ AllOff اضافه شد
            if game == "Hide":
                return bool(general or body.get("Arm") or body.get("Start")
                            or body.get("Fail") or body.get("Cancel")
                            or body.get("Motion") or body.get("AllOff"))  # ✅
            if game == "Vibron":
                return bool(body.get("GridEffect") or body.get("TimerStart")
                            or body.get("TimerStop") or general or set_key
                            or body.get("AllOff"))  # ✅
            if game == "Tesla":
                return bool(body.get("TeslaStart") or ("LightningFx" in body)
                            or body.get("DropArm") or body.get("DropCancel")
                            or body.get("DropStatus") or body.get("AllOff"))  # ✅
            return False


        if (reset or idle) and _has_cmd():
            raise RestApiError(
                HTTPStatus.BAD_REQUEST,
                "'Reset'/'Idle' cannot be combined with game commands")

        if not targets:
            return {"statusCode": 200, "message": "success", "ok": True,
                    "applied": {"ok": False, "game": game,
                                "reason": "bridge_not_found", "bridge": br}}

        results: list = []

        def _fan(msg_type: str, data: Dict[str, Any], field: str) -> None:
            d = dict(data)
            d["bridges"] = list(targets)
            res = action.on_service_event(msg_type, d)
            for r in (res if isinstance(res, list) else [res]):
                r = dict(r)
                r.setdefault("field", field)
                results.append(r)


        if reset or idle:
            msg = "action_reset" if reset else "action_idle"
            for bid_ in targets:
                idle_data: Dict[str, Any] = {
                    "bridge_id": bid_,
                    "game":      game,
                }
                if idle and "pixel_count" in body:
                    idle_data["pixel_count"] = body["pixel_count"]

                res = action.on_service_event(msg, idle_data)
                field = "Reset" if reset else "Idle"

                if isinstance(res, list):
                    for item in res:
                        r = dict(item)
                        r["field"] = field
                        results.append(r)
                else:
                    r = dict(res)
                    r["field"] = field
                    results.append(r)

        # ✅ AllOff: هم‌سطح با reset/idle — مستقل از بازی
        elif body.get("AllOff"):
            _fan("action_all_off", {}, "AllOff")

        elif game == "Action":
            if not (general or set_key):
                raise RestApiError(HTTPStatus.BAD_REQUEST,
                                   "'General' and/or 'SetKey' required")
            for bid_ in targets:
                if general:
                    r = dict(self._parse_general(general, action, bridge_id=bid_))
                    r["field"], r["bridge"] = "General", bid_
                    results.append(r)
                if set_key:
                    r = dict(self._parse_set_key(set_key, action, bridge_id=bid_))
                    r["field"], r["bridge"] = "SetKey", bid_
                    results.append(r)
        elif game == "Hide":
            if set_key:
                raise RestApiError(HTTPStatus.BAD_REQUEST,
                                   "'SetKey' not defined for Hide")

            if general:
                self._validate_tokens(general)
                _hide_data: Dict[str, Any] = {
                    "general": general,
                    "key":     body.get("key", 0),
                }
                _node_addr = body.get("addr") or body.get("node") or body.get("Node")
                if _node_addr is not None:
                    try:
                        _hide_data["addr"] = int(_node_addr)
                    except (TypeError, ValueError):
                        raise RestApiError(HTTPStatus.BAD_REQUEST,
                                        "'addr' must be int (1..12)")
                _fan("hide_cmd", _hide_data, "General")

           
            
            for fld, msg in (("Arm", "hide_arm"), ("Start", "hide_start"),
                             ("Fail", "hide_fail"), ("Cancel", "hide_cancel")):
                if body.get(fld):
                    _fan(msg, body, fld)
            motion = body.get("Motion")
            if motion in (1, 2, "1", "2"):
                _fan("hide_motion", {"motion": int(motion)}, "Motion")
            elif motion not in (None, 0, "0", False):
                raise RestApiError(HTTPStatus.BAD_REQUEST,
                                   "'Motion' must be 1 (0x54) or 2 (0x55)")
        elif game == "Vibron":
            if general:
                self._validate_tokens(general)
                _fan("vibron_general", {"general": general}, "General")
            ge = str(body.get("GridEffect", "") or "").upper()
            if ge:
                if ge not in ("EEE", "SSS", "LLL", "MMM"):
                    raise RestApiError(
                        HTTPStatus.BAD_REQUEST,
                        "'GridEffect' must be EEE|SSS|LLL|MMM")
                _fan("vibron_grid", dict(body, grid=ge), "GridEffect")
            if body.get("TimerStart"):
                _fan("vibron_timer_start", body, "TimerStart")
            if body.get("TimerStop"):
                _fan("vibron_timer_stop", body, "TimerStop")
            if set_key:
                _fan("vibron_setkey", {"setkey": set_key}, "SetKey")
        elif game == "Tesla":
            if general or set_key:
                raise RestApiError(
                    HTTPStatus.BAD_REQUEST,
                    "'General'/'SetKey' not defined for Tesla")
            if body.get("TeslaStart"):
                _fan("tesla_start", {}, "TeslaStart")
            if "LightningFx" in body:
                # قرارداد: حاضر=true→0x70 · حاضر=false→0x71 (تشخیص حاضر/غایب)
                _fan("tesla_start" if body["LightningFx"] else "tesla_stop",
                     {}, "LightningFx")
            if body.get("DropArm"):
                _fan("tesla_drop_arm", body, "DropArm")
            if body.get("DropCancel"):
                _fan("tesla_drop_cancel", {}, "DropCancel")
            if body.get("DropStatus"):
                _fan("tesla_drop_status", {}, "DropStatus")

        self._set_bridge_state(bool(body.get("ActiveBoard", True)), reset, idle)

        ok_all = all(bool(r.get("ok")) for r in results) if results else False
        return {"statusCode": 200, "message": "success", "ok": True,
                "applied": {"ok": ok_all, "game": game,
                            "bridges": targets, "results": results}}

    # توکن‌های مجاز General در Hide/Vibron (قرارداد v5.1)
    _TOKENS = ("RRR", "NNN", "QQQ", "WWW")

    def _validate_tokens(self, general: str) -> None:
        """اعتبارسنجی شکل توکن General — خطا → 400 قبل از هر ارسالی."""
        if general in self._TOKENS:
            return
        if len(general) == 5 and general[0].upper() == "R":
            c, ones, tens, chk = general[1], general[2], general[3], general[4]
            if not (ones.isdigit() and tens.isdigit()):
                raise RestApiError(
                    HTTPStatus.BAD_REQUEST,
                    "R-format: R{color}{ones}{tens}{chk} — ones/tens باید رقم باشند")
            exp = chr((ord(c) + ord(ones) + ord(tens)) & 0x7F)
            if chk != exp:
                raise RestApiError(
                    HTTPStatus.BAD_REQUEST,
                    f"bad_checksum: got '{chk}' expected '{exp}'")
            return
        raise RestApiError(
            HTTPStatus.BAD_REQUEST,
            f"unknown General '{general}' (RRR|NNN|QQQ|WWW|R{{c}}{{o}}{{t}}{{p}})")

    def _parse_general(self, raw: str, action,
                       bridge_id: Optional[str] = None) -> Dict[str, Any]:
        """L + c(رنگ 1..5) + s(ماسک کلید) + p(چک‌سام=ascii(c)+ascii(s))"""
        if len(raw) < 4:
            raise RestApiError(HTTPStatus.BAD_REQUEST, "General must be L{c}{s}{p} (4 chars)")
        c = raw[1]
        s = raw[2]
        p = raw[3] if len(raw) > 3 else ""
        expected = chr(ord(c) + ord(s))          # ascii(s)+ascii(c) → همان چک‌سام
        if p and p != expected:
            raise RestApiError(HTTPStatus.BAD_REQUEST,
                               f"bad_checksum: got '{p}' expected '{expected}'")
        if action is None:
            return {"ok": False, "reason": "action_game_not_active"}
        # ▼ FIX (کاربر): s یک «ماسک ۱۶بیتی» است؛ هر بیت = یک بخش نود
        #   بیت۰ (0b0001)=بخش کلیدهای ۱..۴ · بیت۱ (0b0010)=بخش کلیدهای ۵..۱۰ · بیت۲ (0b0100)=بخش کلید ۱۲
        #   این‌ها را به «لیست نودها» تبدیل می‌کنیم و مستقیم به action_L می‌دهیم
        #   (تا از _GROUP_MAP ناهماهنگِ action_game رد نشویم — بدون تغییر میکرو).
        nodes = _mask_to_nodes(s)
        if not nodes:
            return {"ok": False, "reason": f"invalid_mask:{s}"}
        # ✅ raw مطابق پروتکلِ نرم‌افزار↔سرویس (L{c}{s}{p}) ساخته می‌شود؛
        #   (قبلاً «L{s}{c}{p}» ساخته می‌شد — swap ناهماهنگ که هر کلاینت raw-only را
        #    خراب می‌کرد. حالا با _handle_L که raw را هم با L{c}{s}{p} می‌فهمد، هماهنگ است.)
        data: Dict[str, Any] = {"raw": f"L{c}{s}{p}", "nodes": nodes}
        if bridge_id:
            data["bridge_id"] = bridge_id
        return action.on_service_event("action_L", data)

    # ── پارس SetKey S{s}{c} → action_S (تکنود) ──
    def _parse_set_key(self, raw: str, action,
                       bridge_id: Optional[str] = None) -> Dict[str, Any]:
        """S + s(شماره کلید 0..12) + c(شماره رنگ 1..5)"""
        if len(raw) < 3:
            raise RestApiError(HTTPStatus.BAD_REQUEST, "SetKey must be S{s}{c} (3 chars)")
        if action is None:
            return {"ok": False, "reason": "action_game_not_active"}
        data: Dict[str, Any] = {"raw": raw}
        if bridge_id:
            data["bridge_id"] = bridge_id
        return action.on_service_event("action_S", data)

    # ── حفظ وضعیت بریج‌ها (بدون ارسال به میکرو) ──
    def _set_bridge_state(self, active: bool, reset: bool, idle: bool) -> None:
        if not hasattr(self, "_bridge_state"):
            self._bridge_state: Dict[str, Any] = {}
        self._bridge_state.update({
            "active": active, "reset": reset, "idle": idle,
        })
        self._on_log(f"🏷  [بریج‌ها] active={active} reset={reset} idle={idle} "
                     "(فقط حالت در سرویس — ارسال به میکرو نمی‌شود)")

    # ═══════════════ قرارداد — GET /api/nodes ═══════════════
    def _handle_nodes_get(self, bridge_num=None) -> Dict[str, Any]:
        """
        GET /api/nodes[?bridge=N]  ->  [ {B, N, C1, C2}, ... ]
        B = شماره بریج (1..5) · N = آدرس نود (1..12) · C1/C2 = وضعیت کانال (0/1)
        ✅ v6.9: ?bridge=N فقط نودهای همان بریج (نقشهٔ endpoint قرارداد v5.1)
        """
        svc = self.service
        rows = []
        for n in svc.registry.all():
            b = _bridge_num(n.bridge_id)
            if b == 0:
                continue
            if bridge_num is not None and b != bridge_num:
                continue
            c1, c2 = _channel_flags(n)
            rows.append({"B": b, "N": n.addr, "C1": c1, "C2": c2})
        rows.sort(key=lambda r: (r["B"], r["N"]))
        return {"ok": True, "nodes": rows, "count": len(rows)}

    # ── عملیات روی بریج‌های فعال (پایین نگه‌داشته نشد؛ فقط وضعیت در سرویس) ──

    def _handle_v1(self, parts: list, body: Dict[str, Any]) -> Dict[str, Any]:
        """مسیرهای /api/v1/..."""
        if parts[1] != "v1":
            raise RestApiError(HTTPStatus.NOT_FOUND, "unknown path")

        # ── GET/POST بدون bridge — وضعیت کلی
        if len(parts) == 2:
            return self._dispatch("ping", {})

        # ── /api/v1/nodes  و  /api/v1/bridges
        if len(parts) == 3:
            if parts[2] == "nodes":
                return self._dispatch("get_nodes", {})
            if parts[2] == "bridges":
                return self._dispatch("get_bridges", {})
            if parts[2] == "feedback":
                return self._dispatch("get_status", {})
            raise RestApiError(HTTPStatus.NOT_FOUND, f"unknown {parts[2]}")

        # ── /api/v1/bridge/{bridge-id}/...  →  {b, ...}
        if len(parts) >= 4 and parts[2] == "bridge":
            bid = parts[3]
            rest = parts[4:]
            return self._bridge_routes(bid, rest, body)

        raise RestApiError(HTTPStatus.NOT_FOUND, f"unknown path {'/'.join(parts)}")

    # ────────────────────────────── مسیرهای /bridge/{bid}/...
    def _bridge_routes(self, bid: str, rest: list, body: Dict[str, Any]) -> Dict[str, Any]:
        base = {"bridge_id": bid}

        # /bridge/{bid}/node/{a}/...  (نود مشخص)
        if len(rest) >= 3 and rest[0] == "node":
            try:
                addr = int(rest[1])
            except ValueError:
                raise RestApiError(HTTPStatus.BAD_REQUEST, f"bad addr {rest[1]}")
            cmd = rest[2]
            sub = rest[3:] if len(rest) > 3 else []
            return self._node_routes(bid, addr, cmd, sub, body)

        # /bridge/{bid}/color  یا  /off  (برودکست کل بریج)
        if len(rest) == 1 and rest[0] == "off":
            return self._dispatch("node_cmd", {
                **base, "addr": 0xFF, "cmd": "0x43", "data": "",
            })
        if len(rest) == 1 and rest[0] == "color":
            return self._dispatch("set_color", {
                **base, "addr": 0xFF, "ch": body.get("ch", 1),
                "r": body.get("r", 0), "g": body.get("g", 0), "b": body.get("b", 0),
            })

        # /bridge/{bid}/action  |  /hide  |  /reset
        if len(rest) == 1 and rest[0] == "action":
            return self._dispatch("action_L", _action_data(bid, body))
        if len(rest) == 1 and rest[0] == "hide":
            return self._dispatch("hide_cmd", {**base, **body})
        if len(rest) == 1 and rest[0] == "reset":
            return self._dispatch("reset_node", {**base, "addr": 0xFF})

        # /bridge/{bid}/stage/lock | unlock
        if len(rest) == 2 and rest[0] == "stage" and rest[1] in ("lock", "unlock"):
            return self._dispatch("stage_lock" if rest[1] == "lock" else "stage_unlock",
                                  {**base, "addr": 0xFF})

        raise RestApiError(HTTPStatus.NOT_FOUND, f"unknown bridge route {'/'.join(rest)}")

    def _node_routes(self, bid: str, addr: int, cmd: str, sub: list,
                     body: Dict[str, Any]) -> Dict[str, Any]:
        base = {"bridge_id": bid, "addr": addr}

        if cmd == "color":
            return self._dispatch("set_color", {
                **base, "ch": body.get("ch", 1),
                "r": body.get("r", 0), "g": body.get("g", 0), "b": body.get("b", 0),
            })
        if cmd == "off":
            return self._dispatch("node_cmd", {**base, "cmd": "0x43", "data": ""})
        if cmd == "brightness":
            return self._dispatch("set_brightness", {
                **base, "value": body.get("value", 128),
            })
        if cmd == "reset":
            return self._dispatch("reset_node", base)
        if cmd == "effect":
            return self._dispatch("pixel_effect", {
                **base, "effect": body.get("effect", 0x01), "ch": body.get("ch", 1),
            })
        if cmd == "status":
            return self._dispatch("get_status", base)

        raise RestApiError(HTTPStatus.NOT_FOUND, f"unknown node cmd {cmd}")

    # ────────────────────────────── lifecycle
    def start(self) -> bool:
        try:
            self._httpd = _RestHTTPServer((self.host, self.port),
                                          _RestHandler, api_server=self)
        except OSError as e:
            self._on_log(f"❌ REST API: bind {self.host}:{self.port} → {e}")
            self._httpd = None
            return False
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="RestApi", daemon=True)
        self._thread.start()
        self._on_log(f"🔗 REST API فعال شد: http://{self.host}:{self.port}/api/v1")
        return True

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._on_log("🔗 REST API متوقف شد")


# =============================================================================
# نگاشت بدنهٔ اکشن (action_L) با فرمت صحیح ActionGame
# =============================================================================
def _action_data(bid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    action_L در ActionGame به {bridge_id, groups, color} نیاز دارد.
    نرم‌افزار می‌تواند بفرستد:
        { "groups": [1,2,3], "color": {"r":255,"g":0,"b":0} }
    یا برای گروهِ ۳+۱ (پل رنگ→سرویس):
        { "group": 1, "color": "pink" }   (سازگاری با گویش قدیمی — در ActionGame پارس می‌شود)
    """
    return {**{"bridge_id": bid}, **body}


# =============================================================================
# Handler — وابسته به RestApiServer (بسته بندی در _make_handler)
# =============================================================================
class _RestHandler(BaseHTTPRequestHandler):
    server_version = "RBusRest/1.0"

    def _server(self) -> RestApiServer:
        return self.server.api_server  # type: ignore[attr-defined]

    def _send_json(self, status: int, obj: Dict[str, Any]) -> None:
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length)
            obj = json.loads(raw.decode("utf-8"))
            return obj if isinstance(obj, dict) else {}
        except (ValueError, UnicodeDecodeError):
            raise RestApiError(HTTPStatus.BAD_REQUEST, "invalid JSON body")

    def _handle(self) -> None:
        try:
            parsed = urlparse(self.path)
            server = self._server()
            # realtime: query string با lock (race fix v6.0)
            with server._query_lock:
                server._last_query = parsed.query  # type: ignore
            auth_header = self.headers.get("Authorization", "")
            body = self._read_json()
            # برای GET، query wait را هم به body تزریق کن (با lock)
            if parsed.query:
                from urllib.parse import parse_qs
                try:
                    # parsed.query از همین درخواست است، race ندارد
                    qs = parse_qs(parsed.query)
                    if "wait" in qs and "wait" not in body:
                        body["wait"] = qs["wait"][0]
                except Exception:
                    pass
            result = server.handle(parsed.path, body, method=self.command, auth_header=auth_header)
            res = result if isinstance(result, dict) else {"ok": bool(result)}
            if not res.get("ok", True):
                self._send_json(HTTPStatus.BAD_REQUEST, res)
            else:
                self._send_json(HTTPStatus.OK, res)
        except RestApiError as e:
            self._send_json(e.status, {"ok": False, "reason": e.reason})
        except Exception as e:  # noqa: BLE001 — خطای غیرمنتظره، بدون crash سرور
            self._server()._on_log(f"❌ REST {self.path}: {e!r}")
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                            {"ok": False, "reason": f"internal: {e}"})

    def do_GET(self) -> None:   # noqa: N802
        if self.path in ("/", "/rest_test.html"):
            self._serve_splash()
            return
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_PUT(self) -> None:   # noqa: N802
        self._handle()

    def _serve_splash(self) -> None:
        """ارائهٔ صفحهٔ تست خام (POST/GET) در root، بدون وابستگی خارجی."""
        try:
            import os
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "rest_test.html")
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
        except OSError:
            html = "<h1>RBUS REST — upstream</h1>"
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:  # آرام‌تر از لاگ پیش‌فرض
        self._server()._on_log(f"[rest] {fmt % args}")


# =============================================================================
# توابع کمکی — نگاشت ماسک s (بخش‌ها) ⇄ گروه action_game
# =============================================================================
# هر بیت ماسک «s» (در دستور L{c}{s}{p}) یک «بخش» نود را فعال می‌کند:
#   بیت۰ (0b0001) → بخش کلیدهای 1..4
#   بیت۱ (0b0010) → بخش کلیدهای 5..10
#   بیت۲ (0b0100) → بخش کلید 12
# (دریافتی با کاربرد حفظ شد — از خود تو، کاربر.)
_SECTION_GROUPS = {           # بیت → گروهِ action_game (مطابق _GROUPS)
    "bit0": "1",              # 1..4
    "bit1": "2",              # 5..10
    "bit2": "4",              # 12
}


def _mask_to_nodes(s: str):
    """
    ماسک s → لیست نودها (به‌ترتیب بخش‌ها). هر بیت = یک بخش.
      بیت۰ (0b0001) → نودهای [1,2,3,4]
      بیت۱ (0b0010) → نودهای [5,6,7,8,9,10]
      بیت۲ (0b0100) → نود [12]
    s یک کاراکتر است ('1'..'7' یا 'A'..). مقدار بیتی = int(s,16).
    خروجی: لیست نودها (خالی اگر هیچ بیتی نباشد).
    """
    if s is None or len(s) != 1:
        return None
    try:
        val = int(s, 16)
    except ValueError:
        return None
    nodes = []
    if val & 0b0001: nodes.extend([1, 2, 3, 4])
    if val & 0b0010: nodes.extend([5, 6, 7, 8, 9, 10])
    if val & 0b0100: nodes.append(12)
    return nodes or None


def _mask_to_group_char(s: str):
    """
    ماسک s (رشتهٔ یک‌نویسی، مثل '1'/'2'/'4') → char گروه action_game ('1'..'7').
    s = به‌صورت یک عدد «باینری-نیت» نیست؛ بلکه بیت‌ها در خودِ کاراکتر s.
    چون s یک کاراکتر است (مثلاً '1'=0b0001، '2'=0b0010، '4'=0b0100):
    مقدار بیتی = ord(s) - ord('0').
    خروجی: گروه‌های ترکیبی مثل '3'(بیت۰+بیت۱)=بخش 1..4 و 5..10.
    """
    # s یک کاراکتر یک‌رقمی است → عدد 0..7 (یا 'C' برای 12؟ نه؛ s ماسک است)
    if s is None or len(s) != 1:
        return None
    if s in "0123456789":
        val = int(s, 16)          # '1'→1, '2'→2, '4'→4 ...
    else:
        val = int(s, 16)          # برای 'A'..'F' هم با مبنای ۱۶
    if val == 0:
        return None               # هیچ بخشی فعال نمی‌شود → نامعتبر
    # بیت‌های val را به گروه‌ها ترجمه کن و گروه ترکیبی بساز
    bit_groups = []
    if val & 0b0001: bit_groups.append("1")     # بخش 1..4
    if val & 0b0010: bit_groups.append("2")     # بخش 5..10
    if val & 0b0100: bit_groups.append("4")     # بخش 12
    if not bit_groups:
        return None
    # گروه ترکیبی: اگر چند بیت است، باید یک گروه معتبر در _GROUP_MAP برگردد.
    # _GROUP_MAP گروه‌های '1'..'7' دارد؛ ترکیب بیت‌ها → جمعِ بیتی (نمی‌باید باشد،
    # ولی برای گروه‌های بازی: '3'=بخش‌های 1و2، '5'=1و4، '6'=2و4، '7'=همه.
    if len(bit_groups) == 1:
        return bit_groups[0]
    # ترکیب: '1','2' → '3' ; '1','4' → '5' ; '2','4' → '6' ; همه → '7'
    combo = int(bit_groups[0]) | int(bit_groups[1])
    if len(bit_groups) == 3:
        return "7"
    return str(combo)


# =============================================================================
# توابع کمکی — نگاشت bridge_id ⇄ شماره بریج و وضعیت کانال
# =============================================================================
def _bridge_num(bridge_id: str) -> int:
    """'Bridge-3' -> 3   |   هر عدد خالی/بی‌فرم -> 0."""
    if not bridge_id:
        return 0
    sid = str(bridge_id).replace("Bridge-", "").replace("bridge-", "")
    try:
        return int(sid)
    except ValueError:
        return 0


def _channel_flags(n: Any) -> tuple:
    """
    C1/C2 برای GET /nodes — وضعیت لمس «دو کانالِ» سِنسور تاچ.
      C1 = کانال 0 (بیت 0 touch_mask)   ·   C2 = کانال 1 (بیت 1 touch_mask)
      (1,1)=لمس در هر دو کانال · (0,0)=لمس رخ نداد · (1,0)/(0,1)=نیم‌لمسِ یک کانال (مجاز)
    """
    mask = int(getattr(n, "touch_mask", 0) or 0)
    c1 = 1 if (mask & 0x01) else 0
    c2 = 1 if (mask & 0x02) else 0
    return c1, c2


def _active_bridge_ids(service: Any, active: bool) -> list:
    """کدام بریج‌ها فعال‌اند؟ active=True → همهٔ بریج‌های معرفی‌شده."""
    if not active:
        return []
    return list(service.bridges.keys()) if service.bridges else [f"Bridge-{i}" for i in range(1, 6)]


# =============================================================================
# سرورِ بسته‌بندی — api_server را روی instance سرور می‌گذارد تا handler بداند.
# =============================================================================
class _RestHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler_cls, api_server: RestApiServer):
        self.api_server = api_server
        super().__init__(addr, handler_cls)
