#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
RBUS Action Game — شبیه‌ساز کلاینت RESTful  v6.0-MEMORY
===============================================================================
Single Source of Truth: MEMORY.md v5.0 realtime

REST:
  POST {host}:9001/api/auth/login  → token
  PUT  {host}:9001/api/nodes/set   ← General L{c}{s}{p} / SetKey S{s}{c}
       body: {General, SetKey, ActiveBoard, Reset, Idle, bridge_id}
  GET  {host}:9001/api/nodes
  GET  {host}:9001/api/events/drain?wait=80  (realtime long-poll, Condition.wait)

General = L{c}{s}{p}  c=1..5 رنگ  s=hex mask  p=chr(ord(c)+ord(s))
  mask: 0b0001→1..4 , 0b0010→5..10 , 0b0100→12  (3→1..10, 7→همه, 0→نامعتبر)
SetKey = S{s}{c}  s=0..C (hex) , c=1..5
Palette: 1 آبی 0,0,255  2 زرد 255,200,0  3 صورتی 255,0,128  4 نارنجی 255,165,0  5 سفید
bridge_id: Bridge-1..5 (هر IP=12 نود مجزا)
پالت و ماسک دقیقاً مطابق core/rest_api._mask_to_nodes و MEMORY

اجرا: python ActionGame.py
تست:  python ActionGame.py --selftest
===============================================================================
"""
from __future__ import annotations

import json
import sys
import time
import threading
import traceback
# — global crash logger — never let exception silently close app
def _global_excepthook(exc_type, exc, tb):
    try:
        with open("ActionGame_crash.log","a",encoding="utf-8") as f:
            f.write("\n["+time.strftime("%H:%M:%S")+"] UNCAUGHT "+ "".join(traceback.format_exception(exc_type, exc, tb)))
    except: pass
    try: traceback.print_exception(exc_type, exc, tb)
    except: pass
sys.excepthook = _global_excepthook
from typing import Dict, List, Optional, Tuple

# ── PyQt5 lazy ──
try:
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QMetaObject, Qt as QtCore
    from PyQt5.QtGui import QBrush, QColor, QFont, QPainter, QPen, QRadialGradient
    from PyQt5.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QGroupBox,
        QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
        QScrollArea, QSizePolicy, QSpinBox, QStatusBar, QTabWidget,
        QVBoxLayout, QWidget, QTextEdit,
    )
    HAS_QT = True
except ImportError:
    HAS_QT = False
    class _DummyType(type):
        def __new__(mcs, name, bases, attrs):
            return super().__new__(mcs, name, bases, attrs)
    class _DummyBase(metaclass=_DummyType):
        def __init__(self, *a, **kw): pass
        def __getattr__(self, n):
            def _f(*a, **kw): return None
            return _f
    Qt = QTimer = QObject = QBrush = QColor = QFont = QPainter = QPen = QRadialGradient = _DummyBase  # type: ignore
    QApplication = QComboBox = QFrame = QGridLayout = QGroupBox = _DummyBase  # type: ignore
    QHBoxLayout = QLabel = QLineEdit = QMainWindow = QPushButton = _DummyBase  # type: ignore
    QScrollArea = QSizePolicy = QSpinBox = QStatusBar = QTabWidget = _DummyBase  # type: ignore
    QVBoxLayout = QWidget = QTextEdit = _DummyBase  # type: ignore
    def pyqtSignal(*a, **kw):  # type: ignore
        class _DummySig:
            def emit(self, *a, **kw): pass
            def connect(self, *a, **kw): pass
        return _DummySig()

# ════════════════════════════════════════════════════════════════════════════
# تنظیمات REST — MEMORY
# ════════════════════════════════════════════════════════════════════════════
REST_HOST  = "127.0.0.1"
REST_PORT  = 9001
REST_BASE  = "/api"
REST_TOKEN = "RBUS-TEST-TOKEN-0001"
BRIDGES    = ["Bridge-1", "Bridge-2", "Bridge-3", "Bridge-4", "Bridge-5"]
NODE_COUNT = 12

CLR_BG, CLR_PANEL, CLR_BORDER = "#0D1117", "#161B22", "#30363D"
CLR_NODE_IDLE, CLR_NODE_BORDER = "#1C2128", "#3D444D"
CLR_ONLINE, CLR_OFFLINE, CLR_TEXT, CLR_TEXT_DIM, CLR_ACTIVE = "#3FB950", "#F85149", "#E6EDF3", "#7D8590", "#1A6FFF"

def mask_to_nodes(s: str) -> Optional[List[int]]:
    if not s or len(s) != 1:
        return None
    try:
        v = int(s, 16)
    except ValueError:
        return None
    nodes: List[int] = []
    if v & 0b0001:
        nodes += [1, 2, 3, 4]
    if v & 0b0010:
        nodes += [5, 6, 7, 8, 9, 10]
    if v & 0b0100:
        nodes.append(12)
    return nodes or None

MASK_OPTIONS: List[Tuple[str, str]] = [
    ("1", "بخش۱  →  نودهای 1..4"),
    ("2", "بخش۲  →  نودهای 5..10"),
    ("4", "بخش۳  →  نود 12"),
    ("3", "بخش۱+۲  →  نودهای 1..10"),
    ("5", "بخش۱+۳  →  نودهای 1..4 + 12"),
    ("6", "بخش۲+۳  →  نودهای 5..10 + 12"),
    ("7", "همهٔ بخش‌ها  →  نودهای 1..10 + 12"),
]
COLOR_MAP: Dict[str, Tuple[int, int, int, str]] = {
    "1": (0, 0, 255, "آبی"),
    "2": (255, 200, 0, "زرد"),
    "3": (255, 0, 128, "صورتی"),
    "4": (255, 165, 0, "نارنجی"),
    "5": (255, 255, 255, "سفید"),
}
HEX_NODE: Dict[str, int] = {**{str(i): i for i in range(10)}, "A": 10, "B": 11, "C": 12}
FADE_DURATION_MS, FADE_STEPS = 350, 14  # v6.2 fast fade — همگام با تاچ (قبلاً 1200ms کند بود)

# ════════════════════════════════════════════════════════════════════════════
# REST_HTTP — pure python, no Qt, headless testable
# ════════════════════════════════════════════════════════════════════════════
class REST_HTTP:
    def __init__(self, host: str = REST_HOST, port: int = REST_PORT, token: str = REST_TOKEN):
        self.host, self.port, self.token = host, port, token
        self.base = f"http://{host}:{port}{REST_BASE}"

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Tuple[int, dict]:
        import urllib.request, urllib.error
        url = self.base + path
        data = None
        headers = {"Content-Type": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                obj = json.loads(e.read().decode("utf-8"))
            except Exception:
                obj = {"ok": False, "reason": f"HTTP {e.code}"}
            return e.code, obj
        except Exception as e:
            return 0, {"ok": False, "reason": f"network: {e}"}

    def login(self, username: str = "admin", password: str = "Admin@123") -> bool:
        st, body = self._request("POST", "/auth/login", {"username": username, "password": password})
        if st == 200 and body.get("token"):
            self.token = body["token"]
            return True
        return False

    def _node_body(self, general: str = "", set_key: str = "", bridge_id: Optional[str] = None, active: bool = True, reset: bool = False, idle: bool = False) -> dict:
        """✅ v6.9: بدنهٔ قرارداد v5.1 — Game اجباری + Bridge عددی (0=همه)."""
        body = {"Game": "Action", "General": general, "SetKey": set_key,
                "ActiveBoard": active, "Reset": reset, "Idle": idle}
        try:
            body["Bridge"] = int(str(bridge_id).split("-")[-1]) if bridge_id else 0
        except ValueError:
            body["Bridge"] = 0
        return body

    def send_general(self, c: str, s: str, bridge_id: Optional[str] = None) -> Tuple[int, dict]:
        p = chr(ord(c) + ord(s))
        return self._request("PUT", "/nodes/set", self._node_body(general=f"L{c}{s}{p}", bridge_id=bridge_id))

    def send_setkey(self, node: str, c: str, bridge_id: Optional[str] = None) -> Tuple[int, dict]:
        return self._request("PUT", "/nodes/set", self._node_body(set_key=f"S{node}{c}", bridge_id=bridge_id))

    def send_reset(self, bridge_id: str) -> Tuple[int, dict]:
        """✅ v6.3: Reset=true → برودکست 0x02 (ری‌استارت نودهای بریج) — تنها فیلد."""
        return self._request("PUT", "/nodes/set",
                             self._node_body(bridge_id=bridge_id, reset=True))

    def send_idle(self, bridge_id: str) -> Tuple[int, dict]:
        """✅ v6.3: Idle=true → برودکست 0x27[0x01] رنگین‌کمان (NNN) — تنها فیلد."""
        return self._request("PUT", "/nodes/set",
                             self._node_body(bridge_id=bridge_id, idle=True))

    def send_unified(self, body: dict) -> Tuple[int, dict]:
        """✅ v6.9: قرارداد v5.1 — تنها زبان: PUT /api/nodes/set با فیلد Game.
        body: {Game: Action|Hide|Vibron|Tesla, Bridge: 0..5, ...fringe fields}"""
        return self._request("PUT", "/nodes/set", body)

    def get_nodes(self) -> Tuple[int, dict]:
        return self._request("GET", "/nodes")

    def get_events(self, wait_ms: int = 0) -> Tuple[int, dict]:
        path = "/events/drain"
        if wait_ms > 0:
            path += f"?wait={int(wait_ms)}"
        return self._request("GET", path)

# ════════════════════════════════════════════════════════════════════════════
# ActionClient — thin wrapper, no lock (MEMORY realtime)
# ════════════════════════════════════════════════════════════════════════════
class ActionClient:
    def __init__(self, host: str = REST_HOST, port: int = REST_PORT, token: str = REST_TOKEN):
        self.host, self.port, self.token = host, port, token
        self._http = REST_HTTP(host, port, token)
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self, host: str, port: int) -> bool:
        self.host, self.port = host, port
        self._http = REST_HTTP(host, port, self.token)
        ok = self._http.login()
        self._connected = ok
        return ok

    def disconnect(self) -> None:
        self._connected = False

    def send_general(self, c: str, s: str, bridge_id: Optional[str] = None) -> Tuple[int, dict]:
        try:
            return self._http.send_general(c, s, bridge_id)
        except Exception as e:
            return 0, {"ok": False, "reason": str(e)}

    def send_setkey(self, node: str, c: str, bridge_id: Optional[str] = None) -> Tuple[int, dict]:
        try:
            return self._http.send_setkey(node, c, bridge_id)
        except Exception as e:
            return 0, {"ok": False, "reason": str(e)}

    # ✅ v6.4 (باگ گزارش‌شده کاربر 2026-09-05: «ActionClient has no attribute send_reset»)
    # متدها اشتباهاً فقط به REST_HTTP اضافه شده بودند؛ GUI از این wrapper استفاده می‌کند.
    def send_reset(self, bridge_id: str) -> Tuple[int, dict]:
        try:
            return self._http.send_reset(bridge_id)
        except Exception as e:
            return 0, {"ok": False, "reason": str(e)}

    def send_idle(self, bridge_id: str) -> Tuple[int, dict]:
        try:
            return self._http.send_idle(bridge_id)
        except Exception as e:
            return 0, {"ok": False, "reason": str(e)}

    # ✅ v6.5: فرمان تکی نود — PUT /api/node/cmd (ring/all_off/foot/…)
    # ✅ v6.9: قرارداد v5.1 — PUT /api/nodes/set (تنها زبان)
    def send_unified(self, body: dict) -> Tuple[int, dict]:
        try:
            return self._http.send_unified(body)
        except Exception as e:
            return 0, {"ok": False, "reason": str(e)}

    def get_nodes(self) -> Tuple[int, dict]:
        try:
            return self._http.get_nodes()
        except Exception as e:
            return 0, {"ok": False, "reason": str(e), "nodes": []}

    # ═══════════════════════════════════════════════════════════════════════
    # ═══ ✅ v7.0 — بلوک بازی‌سازها: کلاینت اختصاصی هر بازی (قرارداد v5.1) ═══
    # هر بازی‌ساز فقط متدهای بازی خودش را صدا می‌زند؛ این کلاس‌ها Game/Bridge
    # و شکل بدنه را خودشان می‌سازند — سوکت/فریم/بایت این‌جا غایب است (سند:
    # docs/GAME_DEV_GUIDE.md).
    # ═══════════════════════════════════════════════════════════════════════

    def game_client(self, game: str):
        """✅ v7.0: کلاینت آمادهٔ بازی — game: action|hide|vibron|tesla"""
        game = game.lower()
        cls = {"action": ActionGameClient, "hide": HideGameClient,
               "vibron": VibronGameClient, "tesla": TeslaGameClient}.get(game)
        if cls is None:
            raise ValueError(f"unknown game: {game}")
        return cls(self)

    @staticmethod
    def _chk_R(color: str, ones: int, tens: int) -> str:
        """چک‌سام R-format قرارداد: chr((Σ کاراکترها) & 0x7F)"""
        return chr((ord(str(color)) + ord(str(ones)) + ord(str(tens))) & 0x7F)

    def get_events(self, wait_ms: int = 80) -> Tuple[int, dict]:
        try:
            return self._http.get_events(wait_ms=wait_ms)
        except Exception as e:
            return 0, {"ok": False, "reason": str(e), "events": []}

# ════════════════════════════════════════════════════════════════════════════
# Poll signal — lives in GUI thread, emitted from worker thread via queued
# ════════════════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════════════════
# ✅ v7.0 — کلاینت‌های ۴ بازی (بلوک بازی‌سازها — هر کلاس مستقل قابل واگذاری)
# قاعده: این کلاس‌ها فقط «قرارداد v5.1» را حرف می‌زنند؛ هیچ فریمی نمی‌سازند.
# ════════════════════════════════════════════════════════════════════════════

def _bridge_num(bname: str) -> int:
    """'Bridge-3' → 3 (برای تب‌های بازی)"""
    try:
        return int(str(bname).split("-")[-1])
    except ValueError:
        return 1


class GameClientBase:
    """پایهٔ کلاینت بازی — Game و Bridge را خودش ست می‌کند؛ پاسخ applied.results."""
    GAME = ""

    def __init__(self, client):          # client = ActionClient (wrapper امن)
        self._c = client
        self.bridge = 1                  # Bridge عددی: 1..5 یا 0=همه

    def set_bridge(self, n):
        self.bridge = int(n)

    def _put(self, **fields) -> Tuple[int, dict]:
        body = {"Game": self.GAME, "Bridge": self.bridge}
        body.update(fields)
        return self._c.send_unified(body)

    # ── مشترک همهٔ بازی‌ها (قرارداد v5.1) ──
    def reset(self) -> Tuple[int, dict]:
        """ری‌استارت نودهای بریج → 0x02 (برودکست)"""
        return self._put(Reset=True)

    def idle(self) -> Tuple[int, dict]:
        """رنگین‌کمان/آماده‌سازی → 0x27[01] (برودکست — فیرمور ≥7.12)"""
        return self._put(Idle=True)

    def events(self, wait_ms: int = 80) -> list:
        """رویدادهای میکرو (kind=game|touch|feedback) — فقط یک مصرف‌کننده!"""
        st, b = self._c.get_events(wait_ms)
        return b.get("events", []) if st == 200 else []


class ActionGameClient(GameClientBase):
    """🎯 بازی‌ساز اکشن — General=L{c}{s}{p} · SetKey=S{s}{c}"""
    GAME = "Action"

    def general(self, color: str, mask: str) -> Tuple[int, dict]:
        """L{c}{s}{p} — رنگ c (پالت 1..5) + ماسک s + چک‌سام خودکار"""
        p = chr(ord(str(color)) + ord(str(mask)))
        return self._put(General=f"L{color}{mask}{p}")

    def setkey(self, node: str, color: str) -> Tuple[int, dict]:
        """S{s}{c} — نود s (0..12، B=استیج) + رنگ c"""
        return self._put(SetKey=f"S{node}{color}")


class HideGameClient(GameClientBase):
    """🔢 بازی‌ساز قایم‌موشک — عدد/مسلح/شروع/شکست/لغو/حرکت"""
    GAME = "Hide"

    def display(self, number: int, color: str = "1", key: int = 0) -> Tuple[int, dict]:
        """نمایش عدد 0..99 روی کلید → 0x50 [key][R][G][B][tens][ones]"""
        ones, tens = number % 10, number // 10
        chk = ActionClient._chk_R(color, ones, tens)
        return self._put(General=f"R{color}{ones}{tens}{chk}", key=key)

    def arm(self, color: str = "1", sec: int = 30, key: int = 0) -> Tuple[int, dict]:
        """مسلح‌سازی → 0x51 [key][R][G][B][sec] (فیرمور ۵ بایت الزامی)"""
        return self._put(Arm=True, color=color, sec=sec, key=key)

    def start(self, key: int = 0) -> Tuple[int, dict]:
        """شروع → 0x52 [key]"""
        return self._put(Start=True, key=key)

    def fail(self, key: int = 0) -> Tuple[int, dict]:
        """شکست → 0x53 [key]"""
        return self._put(Fail=True, key=key)

    def cancel(self) -> Tuple[int, dict]:
        """لغو → 0x56"""
        return self._put(Cancel=True)

    def motion(self, n: int) -> Tuple[int, dict]:
        """مانیتور حرکت 1→0x54 / 2→0x55"""
        return self._put(Motion=int(n))

    def token(self, general: str) -> Tuple[int, dict]:
        """توکن‌های General: RRR(ریست) · NNN(رنگین‌کمان) · QQQ/WWW(حرکت)"""
        return self._put(General=general.upper())


class VibronGameClient(GameClientBase):
    """🌀 بازی‌ساز وایبرون — گرید + تایمر + کلید"""
    GAME = "Vibron"

    def grid(self, token: str, drops: int = None, trail: int = None) -> Tuple[int, dict]:
        """EEE خطا→0x60 · SSS انتخاب→0x61 · LLL باران→0x62 (drops/trail اختیاری 1..8) · MMM توقف→0x63"""
        fields = {"GridEffect": token.upper()}
        if drops is not None and trail is not None:
            fields.update({"drops": int(drops), "trail": int(trail)})
        elif drops is not None or trail is not None:
            raise ValueError("drops و trail با هم (هرکدام 1..8) یا هیچ‌کدام")
        return self._put(**fields)

    def timer_start(self, color: str = "1", sec: int = 10, key: int = 0) -> Tuple[int, dict]:
        """شروع تایمر → 0x65 [key][R][G][B][sec]"""
        return self._put(TimerStart=True, color=color, sec=sec, key=key)

    def timer_stop(self) -> Tuple[int, dict]:
        """توقف تایمر → 0x66 (فیرمور پارامتر نمی‌خواند)"""
        return self._put(TimerStop=True)

    def setkey(self, key: int, color: str) -> Tuple[int, dict]:
        """حلقهٔ کلید → 0x64 [key][R][G][B] — اینجا s=کلید است نه نود!"""
        return self._put(SetKey=f"S{key}{color}")


class TeslaGameClient(GameClientBase):
    """⚡ بازی‌ساز تسلا — رعد + قطره"""
    GAME = "Tesla"

    def thunder_start(self) -> Tuple[int, dict]:
        """رعد → 0x70"""
        return self._put(TeslaStart=True)

    def thunder_stop(self) -> Tuple[int, dict]:
        """توقف رعد → 0x71 (فقط با نیت صدا بزن — هر فراخوانی می‌ایستد)"""
        return self._put(LightningFx=False)

    def drop_arm(self, color: str = "1", sec: int = 10, drops: int = 1,
                 trail: int = 7, act_pct: int = 85, flags: int = 1,
                 length: int = 13, key: int = 0) -> Tuple[int, dict]:
        """مسلح‌سازی قطره → 0x72 [key][R][G][B][sec][drops][trail][act][flags][len]"""
        return self._put(DropArm=True, color=color, sec=sec, drops=drops,
                         trail=trail, act_pct=act_pct, flags=flags,
                         length=length, key=key)

    def drop_status(self) -> Tuple[int, dict]:
        """وضعیت قطره → 0x73"""
        return self._put(DropStatus=True)

    def drop_cancel(self) -> Tuple[int, dict]:
        """لغو قطره → 0x74"""
        return self._put(DropCancel=True)


class PollSignaler(QObject):
    events_ready = pyqtSignal(list)
    error = pyqtSignal(str)
    connected = pyqtSignal()
    disconnected = pyqtSignal()

# ════════════════════════════════════════════════════════════════════════════
# NodeCircle / NodeGrid
# ════════════════════════════════════════════════════════════════════════════
class NodeCircle(QWidget):
    clicked = pyqtSignal(int)
    def __init__(self, addr: int, parent=None):
        super().__init__(parent)
        self.addr = addr
        self.active = False
        self._r, self._g, self._b = 28, 33, 40
        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._fade_step)
        self._fade_step_n = 0
        self._fade_from = (self._r, self._g, self._b)
        self.setMinimumSize(72, 92)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)
    def set_color(self, r: int, g: int, b: int, fade_back=False):
        try:
            self._fade_timer.stop()
            self._r, self._g, self._b = int(r), int(g), int(b)
            self.active = True
            if fade_back:
                self._fade_from = (r, g, b)
                self._fade_step_n = 0
                self._fade_timer.start(FADE_DURATION_MS // FADE_STEPS)
            self.update()
        except Exception:
            pass
    def set_idle(self):
        try:
            self._fade_timer.stop()
            self._r, self._g, self._b = 28, 33, 40
            self.active = False
            self.update()
        except Exception:
            pass
    def _fade_step(self):
        try:
            self._fade_step_n += 1
            t = min(self._fade_step_n / FADE_STEPS, 1.0)
            t_e = t * t
            sr, sg, sb = self._fade_from
            self._r = int(sr + (28 - sr) * t_e)
            self._g = int(sg + (33 - sg) * t_e)
            self._b = int(sb + (40 - sb) * t_e)
            self.update()
            if self._fade_step_n >= FADE_STEPS:
                self._fade_timer.stop()
        except Exception:
            pass
    def paintEvent(self, _):
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            label_h = 22
            cx = w // 2
            cy = (h - label_h) // 2
            radius = min(cx - 6, cy - 6)
            ring_col = QColor(CLR_ACTIVE if self.active else CLR_NODE_BORDER)
            ring_col.setAlpha(200 if self.active else 100)
            p.setPen(QPen(ring_col, 3 if self.active else 1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(cx - radius - 4, cy - radius - 4, (radius + 4) * 2, (radius + 4) * 2)
            grad = QRadialGradient(cx - radius * 0.3, cy - radius * 0.3, radius * 1.2)
            base = QColor(self._r, self._g, self._b)
            bright = base.lighter(170)
            grad.setColorAt(0.0, bright)
            grad.setColorAt(1.0, base)
            p.setPen(QPen(QColor(CLR_NODE_BORDER), 1))
            p.setBrush(QBrush(grad))
            p.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)
            p.setPen(QColor(CLR_TEXT))
            p.setFont(QFont("Segoe UI", 11, QFont.Bold))
            p.drawText(cx - radius, cy - radius, radius * 2, radius * 2, Qt.AlignCenter, f"{self.addr:02d}")
            p.setPen(QColor(CLR_TEXT_DIM if not self.active else CLR_ACTIVE))
            p.setFont(QFont("Segoe UI", 7))
            p.drawText(0, h - label_h, w, label_h, Qt.AlignCenter, f"n{self.addr:02d}")
        except Exception:
            pass
    def mousePressEvent(self, _):
        try:
            self.clicked.emit(self.addr)
        except Exception:
            pass

class NodeGrid(QWidget):
    node_clicked = pyqtSignal(int)
    def __init__(self, bridge_id: str, parent=None):
        super().__init__(parent)
        self.bridge_id = bridge_id
        self._circles: Dict[int, NodeCircle] = {}
        self._layout = QGridLayout(self)
        self._layout.setSpacing(8)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self.setStyleSheet(f"background: {CLR_BG};")
        cols = 4
        for addr in range(1, NODE_COUNT + 1):
            c = NodeCircle(addr)
            c.clicked.connect(self.node_clicked)
            self._circles[addr] = c
            self._layout.addWidget(c, (addr - 1) // cols, (addr - 1) % cols)
    def set_nodes_active(self, nodes: List[int], r: int, g: int, b: int):
        try:
            active = set(nodes or [])
            for addr, c in self._circles.items():
                if addr in active:
                    c.active = True
                    c.set_color(r, g, b)
                else:
                    c.active = False
                    c.set_idle()
        except Exception:
            pass
    def set_node_color(self, addr: int, r: int, g: int, b: int):
        try:
            c = self._circles.get(int(addr))
            if c:
                c.set_color(r, g, b)
        except Exception:
            pass
    def clear(self):
        for c in self._circles.values():
            try: c.set_idle()
            except: pass
    def flash_touch(self, addr: int):
        try:
            c = self._circles.get(int(addr))
            if c:
                c.set_color(0, 220, 100, fade_back=True)
        except Exception:
            pass

# ════════════════════════════════════════════════════════════════════════════
# Send panels — MEMORY: General L{c}{s}{p}, SetKey S{s}{c}
# ════════════════════════════════════════════════════════════════════════════
class SendLPanel(QGroupBox):
    def __init__(self, client: ActionClient, grid: NodeGrid, log_fn, parent=None):
        super().__init__("📤 ارسال General  (روشن‌کردن بخش/ماسک)", parent)
        self._client, self._grid, self._log = client, grid, log_fn
        self._current_bridge = "Bridge-1"
        self._build()
    def set_bridge(self, bname: str):
        self._current_bridge = bname
    def _build(self):
        root = QVBoxLayout(self)
        row1 = QHBoxLayout(); row1.addWidget(QLabel("ماسک (s):"))
        self.cb_mask = QComboBox()
        for ch, label in MASK_OPTIONS:
            self.cb_mask.addItem(f"{ch}  →  {label}", ch)
        self.cb_mask.setFixedWidth(240)
        row1.addWidget(self.cb_mask); row1.addStretch(); root.addLayout(row1)
        row2 = QHBoxLayout(); row2.addWidget(QLabel("رنگ (c):"))
        self.cb_color = QComboBox()
        for ch, (r, g, b, name) in COLOR_MAP.items():
            self.cb_color.addItem(f"{ch}  →  {name}", ch)
        self.cb_color.setFixedWidth(150)
        row2.addWidget(self.cb_color); row2.addStretch(); root.addLayout(row2)
        prev = QHBoxLayout(); prev.addWidget(QLabel("Body:"))
        self.lbl_frame = QLabel("—")
        self.lbl_frame.setStyleSheet(f"color: {CLR_ACTIVE}; font-family: monospace; font-size: 11px;")
        prev.addWidget(self.lbl_frame); prev.addStretch(); root.addLayout(prev)
        self.cb_mask.currentIndexChanged.connect(self._update_preview)
        self.cb_color.currentIndexChanged.connect(self._update_preview)
        self._update_preview()
        btn = QPushButton("📤 ارسال General"); btn.setObjectName("success")
        btn.clicked.connect(self._send); root.addWidget(btn)
    def _update_preview(self):
        try:
            s = self.cb_mask.currentData(); c = self.cb_color.currentData()
            if s and c:
                p = chr(ord(c) + ord(s))
                self.lbl_frame.setText(f'{{"General":"L{c}{s}{p}", "SetKey":"", "bridge_id":"{self._current_bridge}", ...}}')
        except Exception:
            pass
    def _send(self):
        try:
            s = self.cb_mask.currentData(); c = self.cb_color.currentData()
            if not s or not c:
                self._log("⚠️ ماسک یا رنگ انتخاب نشده"); return
            r, g, b, name = COLOR_MAP.get(c, (0, 0, 0, "نامشخص"))
            nodes = mask_to_nodes(s) or []
            try:
                self._grid.set_nodes_active(nodes, r, g, b)
            except Exception as e:
                self._log(f"⚠️ grid: {e}")
            st, body = self._client.send_general(c, s, self._current_bridge)
            ok = (st == 200 and body.get("ok") is not False)
            self._log(f"📤 General L{c}{s}{chr(ord(c)+ord(s))} → {self._current_bridge} | رنگ {name} | s={s}→{nodes} | {'✅ '+json.dumps(body, ensure_ascii=False)[:120] if ok else '❌ HTTP '+str(st)+' '+str(body)[:180]}")
            if not ok and "action_game_not_active" in str(body):
                self._log("💡 سرویس می‌گوید ActionGame فعال نیست — در تب Games → Action → فعال‌سازی را بزن")
            if st == 0:
                self._log("💡 سرویس در دسترس نیست — curl http://"+self._client.host+":"+str(self._client.port)+"/api/auth/login را تست کن")
        except Exception as e:
            import traceback; traceback.print_exc()
            try: self._log(f"❌ General خطا: {e}")
            except: pass

class SendSPanel(QGroupBox):
    def __init__(self, client: ActionClient, grid: NodeGrid, log_fn, parent=None):
        super().__init__("📤 ارسال SetKey  (رنگ یک نود)", parent)
        self._client, self._grid, self._log = client, grid, log_fn
        self._current_bridge = "Bridge-1"
        self._build()
    def set_bridge(self, bname: str):
        self._current_bridge = bname
    def _build(self):
        root = QVBoxLayout(self)
        row = QHBoxLayout(); row.addWidget(QLabel("نود (hex):"))
        self.cb_node = QComboBox()
        for addr in range(1, NODE_COUNT + 1):
            hx = format(addr, 'X') if addr >= 10 else str(addr)
            self.cb_node.addItem(f"{hx}  →  نود {addr:02d}", hx)
        self.cb_node.setFixedWidth(130); row.addWidget(self.cb_node)
        row.addWidget(QLabel("رنگ (c):"))
        self.cb_color = QComboBox()
        for ch, (r, g, b, name) in COLOR_MAP.items():
            self.cb_color.addItem(f"{ch}  →  {name}", ch)
        self.cb_color.setFixedWidth(150); row.addWidget(self.cb_color); row.addStretch(); root.addLayout(row)
        prev = QHBoxLayout(); prev.addWidget(QLabel("Body:"))
        self.lbl_frame = QLabel("—"); self.lbl_frame.setStyleSheet(f"color: {CLR_ACTIVE}; font-family: monospace; font-size: 11px;")
        prev.addWidget(self.lbl_frame); prev.addStretch(); root.addLayout(prev)
        self.cb_node.currentIndexChanged.connect(self._update_preview)
        self.cb_color.currentIndexChanged.connect(self._update_preview)
        self._update_preview()
        btn = QPushButton("📤 ارسال SetKey"); btn.setObjectName("success")
        btn.clicked.connect(self._send); root.addWidget(btn)
    def _update_preview(self):
        try:
            n = self.cb_node.currentData(); c = self.cb_color.currentData()
            if n and c:
                self.lbl_frame.setText(f'{{"General":"", "SetKey":"S{n}{c}", "bridge_id":"{self._current_bridge}", ...}}')
        except: pass
    def _send(self):
        try:
            n = self.cb_node.currentData(); c = self.cb_color.currentData()
            if not n or not c:
                self._log("⚠️ نود یا رنگ انتخاب نشده"); return
            addr = HEX_NODE.get(n, 0)
            r, g, b, nm = COLOR_MAP.get(c, (0, 0, 0, "نامشخص"))
            try: self._grid.set_node_color(addr, r, g, b)
            except Exception as e: self._log(f"⚠️ grid: {e}")
            st, body = self._client.send_setkey(n, c, self._current_bridge)
            ok = (st == 200 and body.get("ok") is not False)
            self._log(f"📤 SetKey S{n}{c} → {self._current_bridge} | نود {addr:02d} | {nm} | {'✅ '+json.dumps(body, ensure_ascii=False)[:120] if ok else '❌ HTTP '+str(st)+' '+str(body)[:180]}")
            if not ok and "action_game_not_active" in str(body):
                self._log("💡 ActionGame سرویس غیرفعال — Games → Action → فعال‌سازی")
            if st == 0:
                self._log("💡 curl http://"+self._client.host+":"+str(self._client.port)+"/api/auth/login را تست کن")
        except Exception as e:
            import traceback; traceback.print_exc()
            try: self._log(f"❌ SetKey خطا: {e}")
            except: pass

# ════════════════════════════════════════════════════════════════════════════
# BridgeStatePanel — ✅ v6.3: Reset (RRR → 0x02) / Idle (NNN → رنگین‌کمان)
# ════════════════════════════════════════════════════════════════════════════
class BridgeStatePanel(QGroupBox):
    def __init__(self, client: ActionClient, grid_fn, log_fn, parent=None):
        super().__init__("🎛 حالت بریج  (Reset=RRR · Idle=NNN)", parent)
        self._client, self._grid_fn, self._log = client, grid_fn, log_fn
        self._current_bridge = "Bridge-1"
        root = QVBoxLayout(self)
        row = QHBoxLayout()
        self.btn_reset = QPushButton("🔴 ریست بریج (RRR → 0x02)")
        self.btn_reset.setObjectName("danger")
        self.btn_reset.clicked.connect(self._reset)
        row.addWidget(self.btn_reset)
        self.btn_idle = QPushButton("🌈 حالت انتظار (NNN → رنگین‌کمان)")
        self.btn_idle.clicked.connect(self._idle)
        row.addWidget(self.btn_idle)
        row.addStretch()
        root.addLayout(row)
        note = QLabel('Body: {"Reset":true,"bridge_id":"…"} — تنها فیلد؛ با L/S هم‌زمان = 400')
        note.setStyleSheet("color: #7D8590; font-size: 11px;")
        root.addWidget(note)

    def set_bridge(self, bname: str):
        self._current_bridge = bname

    def _reset(self):
        try:
            bid = self._current_bridge
            self._log(f"🔴 Reset → {bid} | بدنه {{\"Reset\":true,\"bridge_id\":\"{bid}\"}}")
            st, body = self._client.send_reset(bid)
            ok = (st == 200 and body.get("applied", {}).get("ok") is True)
            self._log(f"🔴 Reset {bid} → {'✅ ' + json.dumps(body, ensure_ascii=False)[:140] if ok else '❌ HTTP ' + str(st) + ' ' + str(body)[:180]}")
            if ok:
                try:
                    g = self._grid_fn()
                    if g: g.clear()
                except Exception:
                    pass
                self._log("⏳ نودها ری‌استارت می‌شوند — چند ثانیه بعد برخط (discovery تا 15s)")
            if st == 0:
                self._log("💡 سرویس در دسترس نیست — curl http://" + self._client.host + ":" + str(self._client.port) + "/api/auth/login")
        except Exception as e:
            try: self._log(f"❌ Reset خطا: {e}")
            except: pass

    def _idle(self):
        try:
            bid = self._current_bridge
            self._log(f"🌈 Idle → {bid} | بدنه {{\"Idle\":true,\"bridge_id\":\"{bid}\"}}")
            st, body = self._client.send_idle(bid)
            ok = (st == 200 and body.get("applied", {}).get("ok") is True)
            self._log(f"🌈 Idle {bid} → {'✅ ' + json.dumps(body, ensure_ascii=False)[:140] if ok else '❌ HTTP ' + str(st) + ' ' + str(body)[:180]}")
            if st == 0:
                self._log("💡 سرویس در دسترس نیست — curl http://" + self._client.host + ":" + str(self._client.port) + "/api/auth/login")
        except Exception as e:
            try: self._log(f"❌ Idle خطا: {e}")
            except: pass

# ════════════════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════════════════
# ✅ v7.0 — تب هر بازی (هر تب = یک بازی‌ساز؛ فقط فیلدهای همان بازی)
# ════════════════════════════════════════════════════════════════════════════

def _log_game_result(log, game: str, what: str, st: int, resp: dict):
    """لاگ یکنواخت نتایج بازی‌ها — applied.results واقعی."""
    ap = resp.get("applied", {}) if isinstance(resp, dict) else {}
    if st == 200 and ap.get("ok"):
        rs = ap.get("results", [])
        parts = []
        for r in rs:
            if r.get("opcode"):
                parts.append(f"{r.get('bridge', '?')}:{r.get('opcode')}"
                             f"[{r.get('payload', '')}]")
            elif r.get("nodes") is not None:
                parts.append(f"{r.get('bridge', '?')}:نودها={r.get('nodes')}")
            elif r.get("addr") is not None:
                parts.append(f"{r.get('bridge', '?')}:نود {r.get('addr')}")
            else:
                parts.append(f"{r.get('bridge', '?')}:ok")
        log(f"🎮 {game}.{what} → {ap.get('bridges')} | {' '.join(parts)} ✅")
    else:
        log(f"❌ {game}.{what} → HTTP {st} {str(resp)[:170]}")


class ActionTab(QGroupBox):
    """🎯 تب بازی اکشن — General(L)/SetKey(S)/Reset/Idle"""
    def __init__(self, client: ActionClient, log_fn, parent=None):
        super().__init__("🎯 Action — بازی‌ساز اکشن", parent)
        self._client, self._log = client, log_fn
        self.gc = ActionGameClient(client)
        root = QHBoxLayout(self)
        root.addWidget(QLabel("General L:"))
        self.ed_L = QLineEdit("L11b"); self.ed_L.setFixedWidth(70); root.addWidget(self.ed_L)
        root.addWidget(QLabel("SetKey S:"))
        self.ed_S = QLineEdit("S41"); self.ed_S.setFixedWidth(50); root.addWidget(self.ed_S)
        b1 = QPushButton("L"); b1.clicked.connect(self._do_L); root.addWidget(b1)
        b2 = QPushButton("S"); b2.clicked.connect(self._do_S); root.addWidget(b2)
        b3 = QPushButton("Reset 0x02"); b3.setObjectName("danger"); b3.clicked.connect(self._do_reset); root.addWidget(b3)
        b4 = QPushButton("Idle رنگین‌کمان"); b4.clicked.connect(self._do_idle); root.addWidget(b4)
        root.addStretch()

    def set_bridge(self, n): self.gc.set_bridge(n)
    def _do_L(self):
        raw = self.ed_L.text().strip()
        st, r = self.gc.general(raw[1], raw[2]) if len(raw) >= 3 else (0, {"applied": {"ok": False}})
        _log_game_result(self._log, "Action", f"General {raw}", st, r)
    def _do_S(self):
        raw = self.ed_S.text().strip()
        st, r = self.gc.setkey(raw[1], raw[2]) if len(raw) >= 3 else (0, {"applied": {"ok": False}})
        _log_game_result(self._log, "Action", f"SetKey {raw}", st, r)
    def _do_reset(self):
        st, r = self.gc.reset(); _log_game_result(self._log, "Action", "Reset", st, r)
    def _do_idle(self):
        st, r = self.gc.idle(); _log_game_result(self._log, "Action", "Idle", st, r)


class HideTab(QGroupBox):
    """🔢 تب بازی قایم‌موشک — عدد/Arm/Start/Fail/Cancel/Motion/توکن"""
    def __init__(self, client: ActionClient, log_fn, parent=None):
        super().__init__("🔢 Hide — بازی‌ساز قایم‌موشک", parent)
        self._client, self._log = client, log_fn
        self.gc = HideGameClient(client)
        root = QVBoxLayout(self)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("عدد:")); self.sp_num = QSpinBox(); self.sp_num.setRange(0, 99); self.sp_num.setValue(23); r1.addWidget(self.sp_num)
        r1.addWidget(QLabel("رنگ:")); self.ed_color = QLineEdit("1"); self.ed_color.setFixedWidth(56); r1.addWidget(self.ed_color)
        r1.addWidget(QLabel("key:")); self.sp_key = QSpinBox(); self.sp_key.setRange(0, 15); r1.addWidget(self.sp_key)
        r1.addWidget(QLabel("sec:")); self.sp_sec = QSpinBox(); self.sp_sec.setRange(1, 255); self.sp_sec.setValue(30); r1.addWidget(self.sp_sec)
        r1.addWidget(QLabel("توکن:"))
        self.cb_tok = QComboBox()
        for t in ("", "RRR", "NNN", "QQQ", "WWW"):
            self.cb_tok.addItem(t)
        r1.addWidget(self.cb_tok)
        r1.addStretch(); root.addLayout(r1)
        r2 = QHBoxLayout()
        for txt, fn in (("📋 display عدد", self._do_display), ("Arm", self._do_arm),
                        ("Start", self._do_start), ("Fail", self._do_fail),
                        ("Cancel", self._do_cancel), ("Motion", self._do_motion)):
            b = QPushButton(txt); b.clicked.connect(fn); r2.addWidget(b)
        r2.addStretch(); root.addLayout(r2)

    def set_bridge(self, n): self.gc.set_bridge(n)
    def _do_display(self):
        st, r = self.gc.display(self.sp_num.value(), self.ed_color.text().strip(),
                                self.sp_key.value())
        _log_game_result(self._log, "Hide", f"display {self.sp_num.value()}", st, r)
    def _do_arm(self):
        st, r = self.gc.arm(self.ed_color.text().strip(), self.sp_sec.value(),
                            self.sp_key.value())
        _log_game_result(self._log, "Hide", "arm", st, r)
    def _do_start(self):
        st, r = self.gc.start(self.sp_key.value())
        _log_game_result(self._log, "Hide", "start", st, r)
    def _do_fail(self):
        st, r = self.gc.fail(self.sp_key.value())
        _log_game_result(self._log, "Hide", "fail", st, r)
    def _do_cancel(self):
        st, r = self.gc.cancel(); _log_game_result(self._log, "Hide", "cancel", st, r)
    def _do_motion(self):
        st, r = self.gc.motion(1); _log_game_result(self._log, "Hide", "motion1", st, r)


class VibronTab(QGroupBox):
    """🌀 تب بازی وایبرون — گرید/تایمر/کلید"""
    def __init__(self, client: ActionClient, log_fn, parent=None):
        super().__init__("🌀 Vibron — بازی‌ساز وایبرون", parent)
        self._client, self._log = client, log_fn
        self.gc = VibronGameClient(client)
        root = QVBoxLayout(self)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("گرید:"))
        self.cb_grid = QComboBox()
        for t in ("EEE", "SSS", "LLL", "MMM"):
            self.cb_grid.addItem(t)
        r1.addWidget(self.cb_grid)
        r1.addWidget(QLabel("drops:")); self.sp_drops = QSpinBox(); self.sp_drops.setRange(1, 8); self.sp_drops.setValue(5); r1.addWidget(self.sp_drops)
        r1.addWidget(QLabel("trail:")); self.sp_trail = QSpinBox(); self.sp_trail.setRange(1, 8); self.sp_trail.setValue(2); r1.addWidget(self.sp_trail)
        r1.addWidget(QLabel("رنگ:")); self.ed_color = QLineEdit("1"); self.ed_color.setFixedWidth(56); r1.addWidget(self.ed_color)
        r1.addWidget(QLabel("key:")); self.sp_key = QSpinBox(); self.sp_key.setRange(0, 15); r1.addWidget(self.sp_key)
        r1.addWidget(QLabel("sec:")); self.sp_sec = QSpinBox(); self.sp_sec.setRange(1, 255); self.sp_sec.setValue(10); r1.addWidget(self.sp_sec)
        r1.addWidget(QLabel("SetKey:")); self.ed_sk = QLineEdit("S13"); self.ed_sk.setFixedWidth(46); r1.addWidget(self.ed_sk)
        r1.addStretch(); root.addLayout(r1)
        r2 = QHBoxLayout()
        for txt, fn in (("گرید", self._do_grid), ("گرید+پارامتر", self._do_grid_par),
                        ("TimerStart", self._do_tstart), ("TimerStop", self._do_tstop),
                        ("SetKey", self._do_setkey)):
            b = QPushButton(txt); b.clicked.connect(fn); r2.addWidget(b)
        r2.addStretch(); root.addLayout(r2)

    def set_bridge(self, n): self.gc.set_bridge(n)
    def _do_grid(self):
        st, r = self.gc.grid(self.cb_grid.currentText())
        _log_game_result(self._log, "Vibron", f"grid {self.cb_grid.currentText()}", st, r)
    def _do_grid_par(self):
        st, r = self.gc.grid(self.cb_grid.currentText(), self.sp_drops.value(),
                             self.sp_trail.value())
        _log_game_result(self._log, "Vibron", f"grid+par {self.cb_grid.currentText()}", st, r)
    def _do_tstart(self):
        st, r = self.gc.timer_start(self.ed_color.text().strip(),
                                    self.sp_sec.value(), self.sp_key.value())
        _log_game_result(self._log, "Vibron", "timer_start", st, r)
    def _do_tstop(self):
        st, r = self.gc.timer_stop(); _log_game_result(self._log, "Vibron", "timer_stop", st, r)
    def _do_setkey(self):
        raw = self.ed_sk.text().strip()
        st, r = self.gc.setkey(int(raw[1]), raw[2]) if len(raw) >= 3 else (0, {"applied": {"ok": False}})
        _log_game_result(self._log, "Vibron", f"setkey {raw}", st, r)


class TeslaTab(QGroupBox):
    """⚡ تب بازی تسلا — رعد/قطره"""
    def __init__(self, client: ActionClient, log_fn, parent=None):
        super().__init__("⚡ Tesla — بازی‌ساز تسلا", parent)
        self._client, self._log = client, log_fn
        self.gc = TeslaGameClient(client)
        root = QVBoxLayout(self)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("رنگ:")); self.ed_color = QLineEdit("1"); self.ed_color.setFixedWidth(56); r1.addWidget(self.ed_color)
        r1.addWidget(QLabel("key:")); self.sp_key = QSpinBox(); self.sp_key.setRange(0, 15); r1.addWidget(self.sp_key)
        r1.addWidget(QLabel("sec:")); self.sp_sec = QSpinBox(); self.sp_sec.setRange(1, 255); self.sp_sec.setValue(10); r1.addWidget(self.sp_sec)
        r1.addWidget(QLabel("drops:")); self.sp_drops = QSpinBox(); self.sp_drops.setRange(1, 8); r1.addWidget(self.sp_drops)
        r1.addWidget(QLabel("trail:")); self.sp_trail = QSpinBox(); self.sp_trail.setRange(1, 16); self.sp_trail.setValue(7); r1.addWidget(self.sp_trail)
        r1.addWidget(QLabel("act%:")); self.sp_act = QSpinBox(); self.sp_act.setRange(1, 100); self.sp_act.setValue(85); r1.addWidget(self.sp_act)
        r1.addWidget(QLabel("flags:")); self.sp_flags = QSpinBox(); self.sp_flags.setRange(0, 255); self.sp_flags.setValue(1); r1.addWidget(self.sp_flags)
        r1.addWidget(QLabel("len:")); self.sp_len = QSpinBox(); self.sp_len.setRange(1, 32); self.sp_len.setValue(13); r1.addWidget(self.sp_len)
        r1.addStretch(); root.addLayout(r1)
        r2 = QHBoxLayout()
        for txt, fn in (("⚡ رعد", self._do_start), ("توقف رعد", self._do_stop),
                        ("💧 DropArm", self._do_arm), ("DropStatus", self._do_status),
                        ("DropCancel", self._do_cancel)):
            b = QPushButton(txt); b.clicked.connect(fn); r2.addWidget(b)
        r2.addStretch(); root.addLayout(r2)

    def set_bridge(self, n): self.gc.set_bridge(n)
    def _do_start(self):
        st, r = self.gc.thunder_start(); _log_game_result(self._log, "Tesla", "thunder_start", st, r)
    def _do_stop(self):
        st, r = self.gc.thunder_stop(); _log_game_result(self._log, "Tesla", "thunder_stop", st, r)
    def _do_arm(self):
        st, r = self.gc.drop_arm(self.ed_color.text().strip(), self.sp_sec.value(),
                                 self.sp_drops.value(), self.sp_trail.value(),
                                 self.sp_act.value(), self.sp_flags.value(),
                                 self.sp_len.value(), self.sp_key.value())
        _log_game_result(self._log, "Tesla", "drop_arm", st, r)
    def _do_status(self):
        st, r = self.gc.drop_status(); _log_game_result(self._log, "Tesla", "drop_status", st, r)
    def _do_cancel(self):
        st, r = self.gc.drop_cancel(); _log_game_result(self._log, "Tesla", "drop_cancel", st, r)


class GamesTabs(QTabWidget):
    """✅ v7.0: هر بازی یک تب — به بازی‌ساز X فقط تب X را بدهید.
    (مشترک: Bridge از تب بریج بالای پنجره؛ رویدادها در لاگ پایین)"""
    def __init__(self, client: ActionClient, log_fn, parent=None):
        super().__init__(parent)
        self.action_tab = ActionTab(client, log_fn)
        self.hide_tab = HideTab(client, log_fn)
        self.vibron_tab = VibronTab(client, log_fn)
        self.tesla_tab = TeslaTab(client, log_fn)
        self.addTab(self.action_tab, "🎯 Action")
        self.addTab(self.hide_tab, "🔢 Hide")
        self.addTab(self.vibron_tab, "🌀 Vibron")
        self.addTab(self.tesla_tab, "⚡ Tesla")

    def set_bridge(self, n):
        for t in (self.action_tab, self.hide_tab, self.vibron_tab,
                  self.tesla_tab):
            t.set_bridge(n)


# ════════════════════════════════════════════════════════════════════════════
# Main Window — v6 crash-free: QTimer + one-shot thread per poll
# ════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
class ActionSimWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Action Game — REST v6.0 MEMORY")
        self.resize(1120, 760)
        self._client = ActionClient()
        self._signaler = PollSignaler()
        self._build_ui()
        self._connect_signals()
        self._apply_style()
        # poll state
        self._poll_active = False
        self._poll_busy = False
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(10)
        self._poll_timer.timeout.connect(self._poll_tick)
        self._signaler.events_ready.connect(self._on_poll_events)
        self._signaler.error.connect(lambda e: self._log(f"⚠️ poll: {e}"))

    def _current_grid(self) -> NodeGrid:
        try:
            return self._grids[BRIDGES[self.tab_bridge.currentIndex()]]
        except: return list(self._grids.values())[0]

    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setSpacing(8); root.setContentsMargins(8,8,8,8)
        conn_box = QGroupBox("🔌 اتصال به REST (port 9001) — MEMORY")
        cl = QHBoxLayout(conn_box)
        cl.addWidget(QLabel("Host:")); self.ed_host = QLineEdit(REST_HOST); self.ed_host.setFixedWidth(120); cl.addWidget(self.ed_host)
        cl.addWidget(QLabel("Port:")); self.sp_port = QSpinBox(); self.sp_port.setRange(1,65535); self.sp_port.setValue(REST_PORT); self.sp_port.setFixedWidth(75); cl.addWidget(self.sp_port)
        cl.addWidget(QLabel("Token:")); self.ed_token = QLineEdit(REST_TOKEN); self.ed_token.setFixedWidth(180); cl.addWidget(self.ed_token)
        self.btn_conn = QPushButton("🔗 اتصال"); self.btn_conn.setObjectName("connect"); self.btn_conn.clicked.connect(self._toggle_connect); cl.addWidget(self.btn_conn)
        self.lbl_conn = QLabel("🔴 قطع"); cl.addWidget(self.lbl_conn); cl.addStretch(); root.addWidget(conn_box)
        self.tab_bridge = QTabWidget(); self._grids: Dict[str, NodeGrid] = {}
        for bname in BRIDGES:
            grid = NodeGrid(bname); grid.node_clicked.connect(self._sim_touch)
            self._grids[bname] = grid; self.tab_bridge.addTab(grid, bname)
        root.addWidget(self.tab_bridge)
        right = QHBoxLayout()
        self.send_l = SendLPanel(self._client, self._current_grid(), self._log)
        self.send_s = SendSPanel(self._client, self._current_grid(), self._log)
        right.addWidget(self.send_l, stretch=1); right.addWidget(self.send_s, stretch=1)
        root.addLayout(right)
        # ✅ v6.3: پنل حالت بریج — Reset (RRR → 0x02) / Idle (NNN → رنگین‌کمان)
        self.bridge_state = BridgeStatePanel(self._client, self._current_grid, self._log)
        root.addWidget(self.bridge_state)
        # ✅ v7.0: تب هر بازی — هر تب = یک بازی‌ساز (ActionGameClient/HideGameClient/…)
        self.games_tabs = GamesTabs(self._client, self._log)
        root.addWidget(self.games_tabs)
        log_grp = QGroupBox("📥 لاگ / پاسخ REST — v6")
        ll = QVBoxLayout(log_grp)
        self.log_box = QTextEdit(); self.log_box.setReadOnly(True); self.log_box.setMaximumHeight(170)
        self.log_box.setStyleSheet(f"background: #0D1117; color: {CLR_TEXT}; font-family: monospace; font-size: 11px; border: none;")
        ll.addWidget(self.log_box)
        btn_clear = QPushButton("🗑️ پاک کردن لاگ"); btn_clear.clicked.connect(self.log_box.clear); ll.addWidget(btn_clear)
        root.addWidget(log_grp)
        self.status_bar = QStatusBar(); self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("آماده — اتصال بزنید (MEMORY v5.0)")

    def _connect_signals(self):
        try: self.tab_bridge.currentChanged.connect(self._on_bridge_changed)
        except: pass
        self._update_bridge_view()

    def _on_bridge_changed(self, _idx):
        self._update_bridge_view()
    def _update_bridge_view(self):
        try:
            bname = BRIDGES[self.tab_bridge.currentIndex()]
            self.send_l.set_bridge(bname); self.send_s.set_bridge(bname)
            self.bridge_state.set_bridge(bname)
            self.games_tabs.set_bridge(_bridge_num(bname))
            self.send_l._grid = self._current_grid(); self.send_s._grid = self._current_grid()
            self.status_bar.showMessage(f"بریج فعال: {bname}")
        except: pass

    def _toggle_connect(self):
        try:
            if self._client.connected:
                self._do_disconnect()
            else:
                host = self.ed_host.text().strip() or REST_HOST
                port = int(self.sp_port.value())
                tok = self.ed_token.text().strip() or REST_TOKEN
                self._client.token = tok
                self.status_bar.showMessage(f"در حال اتصال {host}:{port} ...")
                ok = self._client.connect(host, port)
                if ok:
                    self._do_connect()
                else:
                    self.lbl_conn.setText("🔴 خطا"); self.status_bar.showMessage("❌ اتصال/ورود ناموفق — /api/auth/login")
                    self._log("❌ login ناموفق — token/host/port را چک کن")
        except Exception as e:
            self._log(f"❌ _toggle_connect: {e}")

    def _do_connect(self):
        try:
            self.lbl_conn.setText("🟢 متصل"); self.btn_conn.setText("⛔ قطع اتصال")
            self.btn_conn.setObjectName("danger"); self.btn_conn.style().unpolish(self.btn_conn); self.btn_conn.style().polish(self.btn_conn)
            self.status_bar.showMessage("✅ متصل به REST (login OK) — poll wait80")
            self._log("🟢 اتصال برقرار شد (POST /api/auth/login OK)")
            self._poll_active = True; self._poll_busy = False
            self._poll_timer.start()
            self._signaler.connected.emit()
        except Exception as e:
            self._log(f"⚠️ _do_connect: {e}")

    def _do_disconnect(self):
        try:
            self._poll_active = False
            try: self._poll_timer.stop()
            except: pass
            self._client.disconnect()
            self.lbl_conn.setText("🔴 قطع"); self.btn_conn.setText("🔗 اتصال")
            self.btn_conn.setObjectName("connect"); self.btn_conn.style().unpolish(self.btn_conn); self.btn_conn.style().polish(self.btn_conn)
            self.status_bar.showMessage("🔴 قطع شد"); self._log("🔴 قطع شد")
            self._signaler.disconnected.emit()
        except Exception as e:
            self._log(f"⚠️ _do_disconnect: {e}")

    def _poll_tick(self):
        # one-shot: اگر قبلی هنوز در thread است، صبر کن
        if not self._poll_active or not self._client.connected or self._poll_busy:
            return
        self._poll_busy = True
        try:
            th = threading.Thread(target=self._poll_job, daemon=True)
            th.start()
        except Exception as e:
            self._poll_busy = False
            self._log(f"⚠️ poll start: {e}")

    def _poll_job(self):
        try:
            st, body = self._client.get_events(wait_ms=80)  # MEMORY wait80
            if not self._poll_active:
                return
            if st != 200:
                # 401/0 → قطع
                if st in (401, 0):
                    pass
                return
            evs = body.get("events", []) or []
            if evs:
                # NoFilter — همه پاس
                try:
                    self._signaler.events_ready.emit(evs)
                except Exception:
                    pass
        except Exception as e:
            try: self._signaler.error.emit(str(e))
            except: pass
        finally:
            self._poll_busy = False

    def _sim_touch(self, addr: int):
        try:
            grid = self._current_grid()
            grid.flash_touch(addr)
            bname = BRIDGES[self.tab_bridge.currentIndex()]
            self._log(f"🖱️  تاچ محلی → {bname} نود {addr:02d}")
            self.status_bar.showMessage(f"🖱️  {bname} نود {addr:02d}")
        except: pass

    def _on_poll_events(self, events: list):
        try:
            for ev in events:
                kind = ev.get("kind"); d = ev.get("data", {})
                bname = d.get("bridge_id") or ""
                addr = int(d.get("addr", 0) or 0)
                if kind == "touch":
                    grid = self._grids.get(bname)
                    if grid is None: continue
                    try: grid.flash_touch(addr)
                    except: pass
                    evt = d.get("event", "?")
                    seq = d.get("seq", "-"); ch = d.get("ch", "-")
                    self.status_bar.showMessage(f"👆 TOUCH → {bname} نود {addr:02d} ({evt})")
                    self._log(f"👆 TOUCH({evt}) → {bname} نود {addr:02d} [ch={ch} seq={seq}]")
                elif kind == "feedback":
                    tag = d.get("tag"); code = d.get("code")
                    try: c = f"0x{int(code):02X}" if isinstance(code, int) else str(code)
                    except: c = str(code)
                    # ✅ v6.4: تفسیر صریح ACK/NACK — «0x00=ACK» برای اپراتور گیج‌کننده بود
                    try:
                        verdict = "ACK ✅" if int(code) == 0 else f"NACK ❌ ({c})"
                    except Exception:
                        verdict = str(c)
                    self.status_bar.showMessage(f"🔄 feedback {tag} → {bname} نود {addr:02d} {verdict}")
                    self._log(f"🔄 feedback {tag} → {bname} نود {addr:02d} {verdict}")
                elif kind == "adc":
                    self._log(f"📈 ADC → {bname} نود {addr:02d} val={d.get('value')}")
                elif kind == "motion":
                    self._log(f"🏃 motion → {bname} نود {addr:02d}")
        except Exception as e:
            self._log(f"⚠️ _on_poll_events: {e}")

    def closeEvent(self, event):
        try:
            self._poll_active = False
            try: self._poll_timer.stop()
            except: pass
        except: pass
        try: self._client.disconnect()
        except: pass
        try: super().closeEvent(event)
        except: 
            try: event.accept()
            except: pass

    def _log(self, msg: str):
        try:
            import datetime as _dt
            ts = _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self.log_box.append(f"[{ts}]  {msg}")
            try:
                import pathlib
                pathlib.Path("ActionGame_debug.log").write_text(self.log_box.toPlainText()[-5000:], encoding="utf-8")
            except: pass
        except: pass

    def _apply_style(self):
        try:
            self.setStyleSheet(f"""
                QMainWindow, QWidget {{ background: {CLR_BG}; color: {CLR_TEXT}; font-family: "Segoe UI", sans-serif; font-size: 13px; }}
                QGroupBox {{ border: 1px solid {CLR_BORDER}; border-radius: 6px; margin-top: 8px; padding-top: 6px; background: {CLR_PANEL}; }}
                QGroupBox::title {{ subcontrol-origin: margin; left: 10px; color: {CLR_TEXT}; }}
                QPushButton {{ background: #21262D; border: 1px solid {CLR_BORDER}; border-radius: 5px; padding: 5px 14px; color: {CLR_TEXT}; }}
                QPushButton:hover {{ background: #30363D; }} QPushButton:pressed {{ background: #161B22; }}
                QPushButton#connect {{ background: #1A4FBF; border-color: {CLR_ACTIVE}; }}
                QPushButton#danger {{ background: #6E1A1A; border-color: {CLR_OFFLINE}; }}
                QPushButton#success {{ background: #196C2E; border-color: {CLR_ONLINE}; }} QPushButton#success:hover {{ background: #2EA043; }}
                QLineEdit, QSpinBox, QComboBox {{ background: #0D1117; border: 1px solid {CLR_BORDER}; border-radius: 4px; padding: 3px 6px; color: {CLR_TEXT}; }}
                QTabWidget::pane {{ border: 1px solid {CLR_BORDER}; background: {CLR_BG}; }}
                QTabBar::tab {{ background: {CLR_PANEL}; color: {CLR_TEXT_DIM}; padding: 6px 14px; border: 1px solid {CLR_BORDER}; }}
                QTabBar::tab:selected {{ background: {CLR_ACTIVE}; color: #fff; }}
                QScrollArea {{ border: none; }} QStatusBar {{ background: {CLR_PANEL}; border-top: 1px solid {CLR_BORDER}; color: {CLR_TEXT_DIM}; }}
            """)
        except: pass

# ════════════════════════════════════════════════════════════════════════════
def _selftest() -> int:
    print("=== ActionGame RESTful v6.0 — SELFTEST (MEMORY) ===")
    fails = 0
    def check(name, cond, detail=""):
        nonlocal fails
        print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))
        if not cond: fails+=1
    check("ماسک s=1", mask_to_nodes("1")==[1,2,3,4], str(mask_to_nodes("1")))
    check("ماسک s=2", mask_to_nodes("2")==[5,6,7,8,9,10], str(mask_to_nodes("2")))
    check("ماسک s=4", mask_to_nodes("4")==[12], str(mask_to_nodes("4")))
    check("ماسک s=3", mask_to_nodes("3")==[1,2,3,4,5,6,7,8,9,10], str(mask_to_nodes("3")))
    check("ماسک s=7", mask_to_nodes("7")==[1,2,3,4,5,6,7,8,9,10,12], str(mask_to_nodes("7")))
    check("ماسک s=0 → نامعتبر", mask_to_nodes("0") is None)
    check("چک‌سام L11b", chr(ord('1')+ord('1'))=='b')
    check("چک‌سام L14e", chr(ord('1')+ord('4'))=='e')
    check("پالت 1=آبی", COLOR_MAP["1"][:3]==(0,0,255))
    check("پالت 4=نارنجی", COLOR_MAP["4"][:3]==(255,165,0))
    http = REST_HTTP()
    body = http._node_body(general="L11b", bridge_id="Bridge-3")
    check("body شامل General/SetKey/ActiveBoard/Reset/Idle", set(("General","SetKey","ActiveBoard","Reset","Idle")).issubset(set(body.keys())))
    check("body v5.1: Game=Action + Bridge عددی", body.get("Game")=="Action" and body.get("Bridge")==3)
    print(f"\n  نتیجه: {'PASS' if fails==0 else f'FAIL ({fails})'}")
    return 0 if fails==0 else 1

if __name__ == "__main__":
    if any(a.startswith("--selftest") for a in sys.argv):
        sys.exit(_selftest())
    if not HAS_QT:
        print("❌ PyQt5 نصب نیست. برای headless: python ActionGame.py --selftest")
        print("   برای GUI: pip install PyQt5")
        sys.exit(1)
    app = QApplication(sys.argv); app.setStyle("Fusion")
    win = ActionSimWindow(); win.show()
    sys.exit(app.exec_())
