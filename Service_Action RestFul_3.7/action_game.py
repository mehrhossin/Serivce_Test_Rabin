"""
Action Game Logic — RBUS Industrial Node System
پروتکل: GameBridgeServer (TCP port 9000) ← Unity متصل میشه
"""
from __future__ import annotations

from core.protocol import (
    EFFECT_RAINBOW,        # 0x01 — Idle رنگین‌کمان (Action/Hide/Vibron)
    EFFECT_TESLA_IDLE,     # 0x08 — Idle اختصاصی Tesla  ✅ v7.3
    ADDR_BROADCAST,        # 0xFF
)

import logging
from typing import Optional, Callable, Dict, List

log = logging.getLogger(__name__)

# ── گروه‌بندی نودها ────────────────────────────────────────────────────────
_GROUPS: Dict[int, List[int]] = {
    1: [1, 2, 3, 4],
    2: [5, 6, 7, 8, 9, 10],
    3: [11],
    4: [12],
}

_GROUP_MAP: Dict[str, List[int]] = {
    "1": [1],
    "2": [2],
    "4": [3],
    "3": [1, 2],
    "5": [1, 3],
    "6": [2, 3],
    "7": [1, 2, 3],
}

_COLOR_MAP: Dict[str, tuple] = {
    "1": (0,   0,   255),   # آبی
    "2": (255, 200, 0),     # زرد
    "3": (255, 0,   128),   # صورتی
    "4": (255, 165, 0),     # نارنجی
    "5": (255, 255, 255),   # سفید
}

# ✅ v6.2 (تأیید کاربر 2026-09-05): کلیدهای رنگِ ثابتِ بازی اکشن — نودهای ۵..۱۰
# به‌عنوان «کلید رنگ» با رنگ ثابتِ خودشان روشن می‌شوند و رنگِ c در L/S برایشان
# نادیده گرفته می‌شود. سبز (0,255,0) جدید است و به‌عمد در پالت c=1..5 نیست
# (پالت قرارداد L/S است و دست‌نخورده ماند؛ این جدول فقط مخصوص اکشن است).
_ACTION_KEY_COLORS: Dict[int, tuple] = {
    5:  (255, 0,   128),   # صورتی FF0080
    6:  (255, 255, 255),   # سفید   FFFFFF
    7:  (0,   0,   255),   # آبی    0000FF
    8:  (230, 150, 0),     # زرد    FFC800
    9:  (0,   255, 0),     # سبز    00FF00
    10: (255, 50, 0),     # نارنجی FFA500
}
#"#f49507"
_HEX_NODE: Dict[str, int] = {
    **{str(i): i for i in range(10)},
    "A": 10, "B": 11, "C": 12,
    "D": 13, "E": 14, "F": 15,
}


class ActionGame:
    """
    منطق بازی Action.
    این کلاس روی رویدادهای GameBridgeServer سوار میشه:
      - on_service_event() ← از GameBridgeServer فراخوانی میشه
      - on_touch()         ← از GamesPanel هنگام تاچ نود فراخوانی میشه

    هیچ TCP مستقیمی نداره — همه چیز از طریق game_bridge میره.
    """

    def __init__(
        self,
        service,                                    # RBusService
        bridge_server=None,                         # GameBridgeServer | None — طبق REST canonical اختیاری است
        on_log: Optional[Callable[[str], None]] = None,
        rest_server=None,                           # RestApiServer | None — اگر REST-only هستیم
    ) -> None:
        self.service       = service
        self.bridge_server = bridge_server
        self.rest_server   = rest_server
        self.on_log        = on_log or (lambda m: log.info(m))

        # نودهایی که الان فعال هستن (تاچشون باید ارسال بشه)
        self._active_nodes: set[int] = set()
        import threading
        self._lock = threading.Lock()

        # ✅ v6.2: قفل رنگ استیجِ نود ۱۱ (کلید استارت) به ازای هر بریج —
        # {bridge_id: (r,g,b)} — فقط Reset/Idle همان بریج قفل را باز می‌کند.
        self._stage_lock: Dict[str, tuple] = {}

    # ── API عمومی ─────────────────────────────────────────────────────────

    def start(self) -> bool:
        """فعال‌سازی — در حالت REST، GameBridge اختیاری است."""
        if self.bridge_server is not None:
            try:
                if not self.bridge_server.is_running():
                    ok = self.bridge_server.start()
                    if not ok:
                        self.on_log("❌ Action Game: نمیتونم GameBridgeServer رو شروع کنم")
                        return False
                self.on_log(
                    f"✅ Action Game فعال شد "
                    f"(سرور روی {self.bridge_server.host}:{self.bridge_server.port})"
                )
            except Exception as e:
                self.on_log(f"⚠️ bridge_server start خطا: {e} — ادامه با REST")
        else:
            self.on_log("✅ Action Game فعال شد (REST-only, بدون GameBridge)")

        return True

    def stop(self) -> None:
        with self._lock:
            self._active_nodes.clear()
        self.on_log("🔌 Action Game غیرفعال شد.")

    # ── رویداد تاچ (از GamesPanel) ────────────────────────────────────────

    def on_touch(self, addr: int, ch: int = 1, event: str = "PRESS") -> None:
        """
        وقتی نودی تاچ میشه صدا زده میشه.
        اگر نود در لیست فعال بود → به همه کلاینت‌های بازی push میشه.
        در حالت REST-only، به صف REST هم می‌رود.
        v5.1 FIX B3/B4: debounce 120ms + CH0/1 both accepted
        """
        # NoFilter: debounce removed — all touches pass

        with self._lock:
            if addr not in self._active_nodes:
                return

        # CH0/1 both valid (FIX B4) — if caller passes CH0 keep it
        if ch not in (0,1,2):
            ch = 1

        msg = f"A{addr:02d}"
        self.on_log(f"👆 تاچ نود {addr:02d} (ch={ch} {event}) → push به بازی: {msg!r}")

        data = {
            "addr":      addr,
            "bridge_id": self._find_bridge(addr),
            "ch":        ch,
            "event":     event,
        }
        # ارسال به GameBridge اگر هست
        if self.bridge_server is not None:
            try:
                self.bridge_server.push_event("touch_event", data)
            except Exception as e:
                self.on_log(f"⚠️ push_event GameBridge خطا: {e}")
        # ارسال به REST queue اگر هست (برای polling کلاینت REST)
        if self.rest_server is not None and hasattr(self.rest_server, "enqueue_event"):
            try:
                self.rest_server.enqueue_event("touch", data)
            except Exception:
                pass
        # همچنین از طریق service generic event (run.py _on_event) هم پوش داده می‌شود

    # ── رویداد دریافتی از GameBridgeServer ───────────────────────────────

    def on_service_event(self, msg_type: str, data: dict) -> dict:
        """
        GameBridgeServer این متد رو برای دستورات بازی صدا میزنه.
        خروجی: dict با کلید "ok" و در صورت نیاز داده اضافه.
        """
        if msg_type == "action_L":
            return self._handle_L(data)

        elif msg_type == "action_S":
            return self._handle_S(data)

        elif msg_type == "action_reset":
            return self._handle_reset(data)

        elif msg_type == "action_idle":
            return self._handle_idle(data)

        elif msg_type == "action_all_off":
            return self._handle_all_off(data)

        # ═════ ✅ v6.9 — قرارداد v5.1 (CHANGES_v5.1.md) ═════
        elif msg_type == "hide_cmd":

            return self._handle_hide_cmd(data)
        elif msg_type == "hide_arm":
            return self._handle_hide_arm(data)
        elif msg_type == "hide_start":
            return self._handle_hide_key(data, 0x52, "hide_start")
        elif msg_type == "hide_fail":
            return self._handle_hide_fail(data)
        elif msg_type == "hide_cancel":
            return self._u_send(data.get("bridges") or [], 0x56, b"",
                                "hide_cancel")
        elif msg_type == "hide_motion":
            op = 0x54 if int(data.get("motion", 1)) == 1 else 0x55
            return self._u_send(data.get("bridges") or [], op, b"",
                                "hide_motion")
        elif msg_type == "vibron_general":
            # قرارداد: «مثل hide_cmd» — همان توکن‌ها
            return self._handle_hide_cmd(data)
        elif msg_type == "vibron_grid":
            return self._handle_vibron_grid(data)
        elif msg_type == "vibron_timer_start":
            return self._handle_vibron_timer_start(data)
        elif msg_type == "vibron_timer_stop":
            # فیرمور v7.12 پارامتر نمی‌خواند — سند [key] می‌گوید؛ منبع بایت‌ها فیرمور
            return self._u_send(data.get("bridges") or [], 0x66, b"",
                                "vibron_timer_stop")
        elif msg_type == "vibron_setkey":
            return self._handle_vibron_setkey(data)
        elif msg_type == "tesla_start":
            return self._u_send(data.get("bridges") or [], 0x70, b"",
                                "tesla_start")
        elif msg_type == "tesla_stop":
            return self._u_send(data.get("bridges") or [], 0x71, b"",
                                "tesla_stop")
        elif msg_type == "tesla_drop_arm":
            return self._handle_tesla_drop_arm(data)
        elif msg_type == "tesla_drop_cancel":
            return self._u_send(data.get("bridges") or [], 0x74, b"",
                                "tesla_drop_cancel")
        elif msg_type == "tesla_drop_status":
            return self._u_send(data.get("bridges") or [], 0x73, b"",
                                "tesla_drop_status")
        elif msg_type == "tesla_Rainbow":
                    return self._handle_idle(data)
        
        elif msg_type == "action_get_groups":
            return self._handle_get_groups()

        return {"ok": False, "reason": f"unknown action cmd: {msg_type}"}

    # ── پردازش دستور L ────────────────────────────────────────────────────

    def _handle_L(self, data: dict) -> dict:
        """
        data: {"g": "1", "c": "2", "chk": "c"}
        یا raw string: {"raw": "L12c"}
        """
        # پشتیبانی از raw string — پروتکل «نرم‌افزار↔سرویس»:
        #   General = L{c}{s}{p}  →  c=رنگ(raw[1]) · s=ماسک(raw[2]) · p=چک‌سام(raw[3])
        #   ⚠️ (قبلاً raw[1] ماسک و raw[2] رنگ خوانده می‌شد — برعکس؛ همان که باعث
        #       «ماسک/رنگ جابه‌جا» روی مسیر semantic و هر کلاینت raw-only می‌شد. فیکس شد.)
        # در مسیر اصلی (PUT /nodes/set) سرویس مستقیماً `nodes` می‌دهد؛ در این‌جا
        #   raw برای سازگاری است و `direct_nodes` اولویت دارد.
        raw = data.get("raw", "")
        if raw and len(raw) >= 4 and raw[0].upper() == "L":
            c_char = raw[1]   # رنگ
            g_char = raw[2]   # ماسک s
            chk    = raw[3]
        else:
            g_char = str(data.get("g", ""))
            c_char = str(data.get("c", ""))
            chk    = str(data.get("chk", ""))

        # ── checksum ──
        expected = chr(ord(g_char) + ord(c_char))
        if chk and chk != expected:
            self.on_log(
                f"⚠️ Checksum اشتباه L: دریافتی='{chk}' انتظار='{expected}'"
            )
            return {"ok": False, "reason": "bad_checksum"}

        # ── گروه‌ها یا نودهای مستقیم (ماسک) ──
        nodes: List[int] = []
        direct_nodes = data.get("nodes")
        if direct_nodes:
            nodes = [int(n) for n in direct_nodes]
            g_char = str(data.get("g", g_char))   # برای لاگ
        else:
            group_ids = _GROUP_MAP.get(g_char)
            if group_ids is None:
                return {"ok": False, "reason": f"invalid_group:{g_char}"}
            for gid in group_ids:
                nodes.extend(_GROUPS.get(gid, []))

        # ── رنگ ──
        color = _COLOR_MAP.get(c_char)
        if color is None:
            return {"ok": False, "reason": f"invalid_color:{c_char}"}

        with self._lock:
            self._active_nodes = set(nodes)

        r, g, b = color
        # ✅ v4.4.11: در مسیر ماسک (direct_nodes) `g_char` درواقع «ماسک s» است نه گروه؛
        #   برچسب لاگ را صادقانه کردیم تا لاگ گمراه نکند.
        label = "ماسک" if direct_nodes else "گروه"
        self.on_log(
            f"💡 L → {label} '{g_char}' | رنگ '{c_char}' {color} | نودها: {nodes}"
        )

        # ✅ Node Bus: هر IP = یک بریج، هر بریج = ۱۲ نود.
        # اگر Unity `bridge_id` بفرستد → همان بریج هدف است (مهم: آدرس در هر
        # بریج مستقل است). اگر ندهد → _find_node با addr در همه بریج‌ها می‌گردد.
        bid = data.get("bridge_id") or None
        for addr in nodes:
            self._set_node_color(addr, r, g, b, bid)

        return {"ok": True, "nodes": nodes}

    # ── پردازش دستور S ────────────────────────────────────────────────────

    def _handle_S(self, data: dict) -> dict:
        """
        data: {"n": "C", "c": "2"}
        یا raw string: {"raw": "SC2"}
        """
        raw = data.get("raw", "")
        if raw and len(raw) >= 3 and raw[0].upper() == "S":
            n_char = raw[1].upper()
            c_char = raw[2]
        else:
            n_char = str(data.get("n", "")).upper()
            c_char = str(data.get("c", ""))

        addr = _HEX_NODE.get(n_char)
        if addr is None:
            return {"ok": False, "reason": f"invalid_node:{n_char}"}

        color = _COLOR_MAP.get(c_char)
        if color is None:
            return {"ok": False, "reason": f"invalid_color:{c_char}"}

        r, g, b = color
        self.on_log(
            f"💡 S → نود {addr} (0x{n_char}) | رنگ '{c_char}' {color}"
        )
        bid = data.get("bridge_id") or None   # ✅ هر IP = یک بریج (Node Bus)
        self._set_node_color(addr, r, g, b, bid)

        return {"ok": True, "addr": addr}

    # ── Reset / Idle — برودکست به کل بریج (✅ v6.3) ────────────────────

    def _handle_reset(self, data: dict) -> dict:
        """
        ✅ v6.3 (طبق نکتهٔ صریح کاربر 2026-09-05): Reset=true → ارسال
        «ریست» به نودها با opcode=0x02 (CMD_RESET) برودکست به کل بریج.
        در فیرمور 0x02 = ری‌استارت ESP ( reboot سخت) → همهٔ افکت‌ها/بازی‌ها/
        رنگ استیج/حالت پا پاک و نود تازه بوت می‌شود (چند ثانیه قطع، سپس برخط).
        قفل استیج سرویس هم همین‌جا آزاد می‌شود.
        قانون پروتکل: برودکست 0xFF هرگز ACK نمی‌گیرد → expect_feedback=False.
        """
        from core.protocol import CMD_RESET
        bridge_id = str(data.get("bridge_id", ""))
        if not bridge_id:
            return {"ok": False, "reason": "bridge_id_required"}
        if bridge_id not in self.service.bridges:
            return {"ok": False, "reason": f"bridge_not_found:{bridge_id}"}
        ok = self.service.send_cmd(
            bridge_id, 0xFF, CMD_RESET, b"",
            tag="action_reset", expect_feedback=False,
        )
        with self._lock:
            self._stage_lock.pop(bridge_id, None)
            self._active_nodes.clear()
        if ok:
            self.on_log(
                f"🔴 Reset → برودکست 0x02 (ری‌استارت نودها) روی {bridge_id} "
                f"+ آزادسازی قفل استیج — نودها چند ثانیه بعد برخط می‌شوند"
            )
        else:
            self.on_log(f"⚠️ Reset → {bridge_id} در دسترس نیست (ارسال نشد)")
        return {"ok": bool(ok), "bridge_id": bridge_id, "effect": "reset",
                "opcode": "0x02"}

    def _handle_idle(self, data: dict) -> dict:
        bridges = data.get("bridges") or []
        bid = data.get("bridge_id") or (bridges[0] if bridges else None)
        self._stage_lock.pop(bid, None)

        target_bridges = bridges if bridges else ([bid] if bid else [])

        # ✅ v7.3: pixel_count اختیاری — فقط اگر صریحاً در data باشد ارسال می‌شود
        pixel_count = data.get("pixel_count")
        if pixel_count is not None:
            try:
                pc = max(1, min(255, int(pixel_count)))
                pc_payload = bytes([pc, pc, pc])
                for b in target_bridges:
                    self.service.send_cmd(
                        b, ADDR_BROADCAST, 0x40, pc_payload, tag="pixel_count"
                    )
                    self.on_log(f"🔢 pixel_count={pc} → [{b}] قبل از idle")
            except (TypeError, ValueError):
                self.on_log(f"⚠️ pixel_count نامعتبر: {pixel_count!r} — نادیده گرفته شد")

        # ✅ v7.3: Tesla → 0x27[08] · بقیه بازی‌ها → 0x27[01] (رفتار قبلی حفظ)
        game = str(data.get("game", "") or "").strip().capitalize()
        effect = EFFECT_TESLA_IDLE if game == "Tesla" else EFFECT_RAINBOW

        return self._u_send(
            target_bridges,
            0x27,
            bytes([effect]),
            "action_idle",
        )

    def _handle_all_off(self, data: dict) -> list:
        """
        AllOff=true → opcode 0x06 [0xFF] broadcast به همه بریج‌های هدف.
        معادل دکمه «📴 خاموش همه» در تب دستورات سریع command_panel.
        همه کانال‌های پیکسلی (CH1/CH2/CH3) روی همه نودهای بریج خاموش می‌شوند.
        قانون پروتکل: broadcast 0xFF → expect_feedback=False.
        """
        bridges = data.get("bridges") or []
        if not bridges:
            return [{"ok": False, "reason": "bridges_required"}]
        return self._u_send(bridges, 0x06, bytes([0xFF]), "all_off")



    # ── ✅ v6.5: فرمان تکی نود — PUT /api/node/cmd ──────────────────────
    #
    # زبان مشترک اکشن-کامل (مرجع: uploads/RBUS_COMMANDS.html v7.11):
    # بازی دستور «خودش» را می‌فرستد؛ این‌جا به فریم میکرو ترجمه می‌شود.
    #
    #   cmd           → opcode  payload                      ملاحظات
    #   ───────────────────────────────────────────────────────────────────
    #   ring          → 0x42    [key][R][G][B]               RGB خام — بدون پالت و بدون رنگِ ثابت ۵..۱۰
    #                 نود ۱۲: key=0 چپ(CH2) / 1 راست(CH1) / FF هر دو (پیش‌فرض FF) — دیگران key=0
    #   all_off       → 0x43    —                             خاموشی کامل نود (پا+استیج+حلقه) + آزادسازی قفل استیج سرویس
    #   foot          → 0x44    [enable]                     حالت پا نود ۱۲ (ماندگار EEPROM)
    #   stage_rgb     → 0x45    [R][G][B]                    استیج نود ۱۱ — قفل سرویس به همین رنگ به‌روز می‌شود
    #   brightness    → 0x05    [ch][value]                  ch=1|2|3|FF(همه) · value=0..255 (EEPROM)
    #   identify      → 0x07    —                             چشمک شناسایی نود
    #   pixel_count   → 0x40    [c1][c2][c3]                 هر کدام 1..128 (پیش‌فرض c3=4)
    #   stage_lock    → 0x28    —                             قفل صحنهٔ «فیرمور» (متمایز از قفل استیج سرویس)
    #   stage_unlock  → 0x29    —
    #
    # node: 1..12 یا "all" (= برودکست 0xFF — بدون ACK طبق قانون پروتکل)

    _NODE_CMDS = ("ring", "all_off", "foot", "stage_rgb", "brightness",
                  "identify", "pixel_count", "stage_lock", "stage_unlock")

    # ── ✅ v6.8: زبان قرارداد جامع بازی‌ها — PUT /api/game/cmd ─────────────
    #
    # «Game Contract v1» — سند کامل: docs/CONTRACT.md (مرجع: RBUS_COMMANDS v7.12)
    # بازی فقط این زبان را می‌فهمد؛ این‌جا «تنها نقطهٔ ترجمه» به بایت‌های میکروست.
    #
    #   game/cmd                        → opcode  payload
    #   ─────────────────────────────────────────────────────────────────────
    #   action/ring                     → 0x42  [key][R][G][B]
    #   action/all_off                  → 0x43  —
    #   action/foot                     → 0x44  [enable]
    #   action/stage_rgb                → 0x45  [R][G][B]
    #   hide/display   (node اجباری)    → 0x50  [key][R][G][B][tens][ones]
    #   hide/arm                        → 0x51  [key][R][G][B][sec]
    #   hide/start | fail               → 0x52|0x53  [key]
    #   hide/motion1 | motion2          → 0x54|0x55  —
    #   hide/cancel | wait | status     → 0x56|0x57|0x58  —
    #   hide/test                       → 0x59  [mode]
    #   vibron/timer_start              → 0x65  [key][R][G][B][sec]   (مرحله ۱)
    #   vibron/ring                     → 0x64  [key][R][G][B]        (مرحله ۲)
    #   vibron/timer_stop               → 0x66  —
    #   vibron/set_count                → 0x67  [count]
    #   tesla/thunder_start | stop      → 0x70|0x71  —
    #   tesla/drop_arm                  → 0x72  [key][R][G][B][sec][drops][trail][act][flags][len؟]
    #   tesla/drop_status | cancel      → 0x73|0x74  —
    #   system/reset                    → 0x02  برودکست (ری‌استارت نودها)
    #   system/idle                     → 0x27  [01] برودکست (رنگین‌کمان)
    #   + brightness/identify/pixel_count/stage_lock/stage_unlock (مثل node/cmd)

    # ═══════════════════════════════════════════════════════════════════════
    # ✅ v6.9 — قرارداد v5.1 (CHANGES_v5.1.md): مترجم واحد Hide/Vibron/Tesla
    #
    # همهٔ فرمان‌ها «سطح بریج» هستند: یک فریم برودکست (0xFF) به هر بریج هدف —
    # قانون 14: برودکست هرگز feedback نمی‌گیرد (expect_feedback=False).
    #
    #   Hide:    RRR→0x02 · NNN→0x27[01] · QQQ→0x54 · WWW→0x55
    #            R{c}{ones}{tens}{chk} → 0x50[key,R,G,B,tens,ones]
    #            Arm→0x51[key,R,G,B,sec] · Start/Fail→0x52/0x53[key]
    #            Cancel→0x56 · Motion 1/2→0x54/0x55
    #   Vibron:  EEE/SSS/LLL/MMM → 0x60..0x63 (LLL: drops/trail اختیاری 1..8 —
    #            غایب → payload خالی، فیرمور پیش‌فرض 5/2)
    #            TimerStart→0x65[key,R,G,B,sec] · TimerStop→0x66 (خالی)
    #            SetKey S{key}{color} → 0x64[key,R,G,B]
    #   Tesla:   Start→0x70 · Stop→0x71 · DropArm→0x72 (10B) · Status→0x73 ·
    #            Cancel→0x74
    #   رنگ دوگانه (تصمیم کاربر v6.9): «1» پالت _COLOR_MAP · «FF0080» hex6 خام
    # ═══════════════════════════════════════════════════════════════════════

    _TOKEN_OPS = {
        "RRR": (0x02, b""),       # ری‌استارت نودها (ری‌بوت ESP)
        "NNN": (0x27, b"\x08"),   # افکت رنگین‌کمان (فیرمور ≥7.12)
        "QQQ": (0x54, b""),        # MOTION_MONITOR_1
        "WWW": (0x55, b""),        # MOTION_MONITOR_2
    }
    _GRID_OPS = {"EEE": 0x60, "SSS": 0x61, "LLL": 0x62, "MMM": 0x63}

    def _u_send(self, bridges, opcode: int, payload: bytes, tag: str) -> list:
        """ارسال برودکست 0xFF به هر بریج هدف — خروجی: نتایج per-bridge."""
        out = []
        for bid in bridges:
            ok = self.service.send_cmd(bid, 0xFF, opcode, payload,
                                       tag=tag, expect_feedback=False)
            out.append({"ok": bool(ok), "bridge": bid,
                        "opcode": f"0x{opcode:02X}",
                        "payload": payload.hex()})
        self.on_log(f"🎮 v5.1 {tag} → {list(bridges)} "
                    f"opcode=0x{opcode:02X} payload={payload.hex() or '—'}")
        return out

    def _u_color(self, value, default="1"):
        """«1» → پالت _COLOR_MAP · «FF0080» → RGB خام (تصمیم کاربر v6.9)."""
        s = str(default if value in (None, "") else value).strip().lstrip("#")
        if len(s) == 6:
            try:
                return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                return None
        return _COLOR_MAP.get(s)

    def _u_int(self, data: dict, field: str, lo: int, hi: int, default):
        v = data.get(field, default)
        try:
            v = int(v)
        except (TypeError, ValueError):
            return None
        return v if lo <= v <= hi else None

    def _u_fail(self, bridges, reason: str) -> list:
        return [{"ok": False, "bridge": b, "reason": reason} for b in bridges]

    # ── Hide ───────────────────────────────────────────────────────────────
    def _handle_hide_cmd(self, data: dict) -> list:
        general = str(data.get("general", ""))
        bridges = data.get("bridges") or []

        if general in self._TOKEN_OPS:
            op, pl = self._TOKEN_OPS[general]
            return self._u_send(bridges, op, pl, f"hide_{general}")

        rgb = _COLOR_MAP.get(general[1])
        key = self._u_int(data, "key", 0, 15, 0)
        if rgb is None or key is None:
            return self._u_fail(bridges,
                                f"bad_color_or_key:{general[1]}/{data.get('key')}")

        payload = (bytes([key]) + bytes(rgb) +
                bytes([int(general[3]), int(general[2])]))

        # ✅ v7.3: اگر addr مشخص شد → unicast به نود خاص؛ در غیر این صورت → broadcast
        target_addr = data.get("addr")
        if target_addr is not None:
            target_addr = int(target_addr)
            out = []
            for bid in bridges:
                node = self.service.registry.get_by_addr(bid, target_addr)
                if node and node.addr:
                    ok = self.service.send_cmd(
                        bid, node.addr, 0x50, payload,
                        tag="hide_display", expect_feedback=True,
                    )
                    out.append({"ok": bool(ok), "bridge": bid,
                                "opcode": "0x50",
                                "payload": payload.hex(),
                                "field": "General"})
                    self.on_log(
                        f"🎮 v5.1 hide_display → [{bid}] "
                        f"opcode=0x50 payload={payload.hex()} addr={target_addr}"
                    )
                else:
                    out.append({"ok": False, "bridge": bid,
                                "reason": f"node_not_found:{target_addr}"})
                    self.on_log(f"⚠️ hide_display: نود {target_addr} در {bid} پیدا نشد")
            return out

        # broadcast — همه نودها (رفتار قبلی)
        return self._u_send(bridges, 0x50, payload, "hide_display")

    def _handle_hide_arm(self, data: dict) -> list:
        """Arm → 0x51 [key][R][G][B][sec] — فیرمور ۵ بایت الزامی (dLen<5 → NACK)."""
        bridges = data.get("bridges") or []
        rgb = self._u_color(data.get("color"))
        sec = self._u_int(data, "sec", 1, 255, 30)
        key = self._u_int(data, "key", 0, 15, 0)
        if rgb is None or sec is None or key is None:
            return self._u_fail(bridges, "bad color/sec(1..255)/key(0..15)")
        payload = bytes([key]) + bytes(rgb) + bytes([sec])
        return self._u_send(bridges, 0x51, payload, "hide_arm")

    def _handle_hide_key(self, data: dict, opcode: int, tag: str) -> list:
        """Start/Fail → 0x52/0x53 [key]."""
        bridges = data.get("bridges") or []
        key = self._u_int(data, "key", 0, 15, 0)
        if key is None:
            return self._u_fail(bridges, "bad key(0..15)")
        return self._u_send(bridges, opcode, bytes([key]), tag)

    # ── Vibron ─────────────────────────────────────────────────────────────
    def _handle_vibron_grid(self, data: dict) -> list:
        """EEE/SSS/LLL/MMM → 0x60..0x63 — LLL پارامتر اختیاری [drops][trail]."""
        token = str(data.get("grid", "")).upper()
        bridges = data.get("bridges") or []
        op = self._GRID_OPS.get(token)
        if op is None:
            return self._u_fail(bridges, f"unknown_grid:{token}")
        payload = b""
        if token == "LLL":
            has_d = data.get("drops") is not None
            has_t = data.get("trail") is not None
            if has_d or has_t:
                drops = self._u_int(data, "drops", 1, 8, None)
                trail = self._u_int(data, "trail", 1, 8, None)
                if drops is None or trail is None:
                    return self._u_fail(bridges,
                                        "drops+trail both required (1..8)")
                payload = bytes([drops, trail])
        return self._u_send(bridges, op, payload, f"vibron_{token}")

    def _handle_vibron_timer_start(self, data: dict) -> list:
        """TimerStart → 0x65 [key][R][G][B][sec]."""
        bridges = data.get("bridges") or []
        rgb = self._u_color(data.get("color"))
        sec = self._u_int(data, "sec", 1, 255, 10)
        key = self._u_int(data, "key", 0, 15, 0)
        if rgb is None or sec is None or key is None:
            return self._u_fail(bridges, "bad color/sec/key")
        payload = bytes([key]) + bytes(rgb) + bytes([sec])
        return self._u_send(bridges, 0x65, payload, "vibron_timer_start")

    def _handle_vibron_setkey(self, data: dict) -> list:
        """SetKey = S{key}{color} → 0x64 [key][R][G][B] (کلید ویبرون، نه نود)."""
        raw = str(data.get("setkey", ""))
        bridges = data.get("bridges") or []
        if len(raw) < 3 or raw[0].upper() != "S" or not raw[1].isdigit():
            return self._u_fail(bridges, f"bad_setkey:{raw}")
        key = int(raw[1])
        rgb = _COLOR_MAP.get(raw[2])
        if rgb is None or not (0 <= key <= 15):
            return self._u_fail(bridges, f"bad_setkey:{raw}")
        payload = bytes([key]) + bytes(rgb)
        return self._u_send(bridges, 0x64, payload, "vibron_setkey")

    # ── Tesla ──────────────────────────────────────────────────────────────
    def _handle_tesla_drop_arm(self, data: dict) -> list:
        bridges = data.get("bridges") or []
        rgb = self._u_color(data.get("color"))
        key = self._u_int(data, "key", 0, 15, 0)
        sec = self._u_int(data, "sec", 1, 255, 10)
        drops = self._u_int(data, "drops", 1, 8, 1)
        trail = self._u_int(data, "trail", 1, 16, 7)
        act = self._u_int(data, "act_pct", 1, 100, 85)
        flags = self._u_int(data, "flags", 0, 255, 1)
        length = self._u_int(data, "length", 1, 32, 13)
        vals = (rgb, key, sec, drops, trail, act, flags, length)
        if any(v is None for v in vals):
            return self._u_fail(bridges,
                                "bad color/key/sec/drops(1..8)/trail(1..16)"
                                "/act_pct(1..100)/flags/length(1..32)")
        payload = (bytes([key]) + bytes(rgb) +
                bytes([sec, drops, trail, act, flags, length]))

        # ✅ v7.3: unicast به نود خاص اگر addr داده شد؛ در غیر این صورت broadcast
        target_addr = data.get("addr")
        if target_addr is not None:
            target_addr = int(target_addr)
            out = []
            for bid in bridges:
                node = self.service.registry.get_by_addr(bid, target_addr)
                if node and node.addr:
                    ok = self.service.send_cmd(
                        bid, node.addr, 0x72, payload,
                        tag="tesla_drop_arm", expect_feedback=True,
                    )
                    out.append({"ok": bool(ok), "bridge": bid,
                                "opcode": "0x72",
                                "payload": payload.hex(),
                                "field": "DropArm"})
                    self.on_log(
                        f"⚡ tesla_drop_arm unicast → [{bid}] "
                        f"addr={target_addr} payload={payload.hex()}"
                    )
                else:
                    out.append({"ok": False, "bridge": bid,
                                "reason": f"node_not_found:{target_addr}"})
                    self.on_log(
                        f"⚠️ tesla_drop_arm: نود {target_addr} در {bid} پیدا نشد"
                    )
            return out

        return self._u_send(bridges, 0x72, payload, "tesla_drop_arm")


    def _broadcast_effect(self, bridge_id: str, effect_id: int,
                          label: str) -> dict:
        """
        ارسال PIXEL_EFFECT (0x27) برودکست به «همهٔ نودهای یک بریج»:
          Idle = 0x27 [0x01] → افکت رنگین‌کمان (حالت انتظار NNN)
        قانون پروتکل: برودکست 0xFF هرگز ACK نمی‌گیرد → expect_feedback=False.
        Idle قفل استیج همان بریج را نیز باز می‌کند.
        """
        from core.protocol import CMD_PIXEL_EFFECT
        if not bridge_id:
            return {"ok": False, "reason": "bridge_id_required"}
        if bridge_id not in self.service.bridges:
            return {"ok": False, "reason": f"bridge_not_found:{bridge_id}"}
        ok = self.service.send_cmd(
            bridge_id, 0xFF, CMD_PIXEL_EFFECT, bytes([effect_id & 0xFF]),
            tag=f"action_{label}", expect_feedback=False,
        )
        with self._lock:
            self._stage_lock.pop(bridge_id, None)
            self._active_nodes.clear()
        if ok:
            self.on_log(
                f"🌈 {label} → برودکست 0x27[0x{effect_id:02X}] روی {bridge_id} "
                f"+ آزادسازی قفل استیج"
            )
        else:
            self.on_log(f"⚠️ {label} → {bridge_id} در دسترس نیست (ارسال نشد)")
        return {"ok": bool(ok), "bridge_id": bridge_id, "effect": label}

    # ── دریافت وضعیت گروه‌ها ─────────────────────────────────────────────

    def _handle_get_groups(self) -> dict:
        with self._lock:
            active = list(self._active_nodes)
        return {
            "ok":          True,
            "groups":      _GROUPS,
            "group_map":   _GROUP_MAP,
            "active_nodes": active,
        }

    # ── ارسال رنگ به نود ──────────────────────────────────────────────────

    def _set_node_color(self, addr: int, r: int, g: int, b: int,
                        bridge_id: Optional[str] = None,
                        foot_key: Optional[int] = None) -> None:
        """ارسال رنگ به «یک نود» — اما نه یک‌شکل برای همه! (v7.11 — نودهای خاص)
        مطابق RBUS_COMMANDS / FW_CODE_MAP v7.11:
          · نود ۱۱ (کلید استارت + استیج):  0x45 STAGE_RGB [R G B] (۳۳px) +
                                             0x42 ACTION_RING [key][R][G][B] (۴px استارت)
          · نود ۱۲ (پا):                      0x44 FOOT_MODE [enable=1] (چپ→CH2/راست→CH1)
                                             سپس 0x42 ACTION_RING [key][R][G][B]
                                             key=0 چپ / 1 راست / 0xFF هر دو
          · بقیه (۱..۱۰):                    0x42 ACTION_RING [key=0][R][G][B] (۴px)
        قانون درس #۱۰: رنگ فقط در send_cmd مپ می‌شود؛ اینجا RGB خام می‌دهیم."""
        from core.protocol import (CMD_ACTION_RING, CMD_ACTION_STAGE_RGB,
                                   CMD_ACTION_FOOT_MODE)
        node = self._find_node(addr, bridge_id)
        if node is None:
            self.on_log(f"⚠️ نود {addr} (bridge={bridge_id or '*'}) در هیچ بریجی نیست")
            return
        if r is None or g is None or b is None:
            return
        rgb = (r & 0xFF, g & 0xFF, b & 0xFF)

        # ─── ✅ v6.2: کلیدهای رنگ ثابت (نودهای ۵..۱۰) — رنگ دستور نادیده ───
        # این نودها «کلید رنگ» بازی اکشن‌اند و همیشه با رنگ ثابت خودشان
        # روشن می‌شوند (تأیید کاربر 2026-09-05). نودهای ۱..۴ رنگ دستور می‌گیرند.
        fixed = _ACTION_KEY_COLORS.get(addr)
        if fixed is not None:
            if rgb != fixed:
                self.on_log(
                    f"🔑 نود {addr} کلید رنگ است → رنگ ثابت {fixed} "
                    f"به‌جای رنگ دستور {rgb}"
                )
            rgb = fixed

        # ─── نود ۱۲ = پا (چپ/راست/هر دو) ───
        if addr == 12:
            # ۱) فعال‌سازی نود پا (ماندگار EEPROM — idempotent)
            self._send_frame(node, CMD_ACTION_FOOT_MODE, bytes([0x01]),
                             "foot_enable")
            # ۲) روشن‌کردن پاها — پیش‌فرض: هر دو (0xFF)، قابل تنظیم با foot_key
            key = 0xFF if foot_key is None else (foot_key & 0xFF)
            self._send_frame(node, CMD_ACTION_RING,
                             bytes([key]) + bytes(rgb), "action_color")
            return

        # ─── نود ۱۱ = استارت/استیج (۳۳px دور + حلقه ۴px) ───
        if addr == 11:
            # ✅ v6.2 قفل استیج: رنگ ۳۳px استیج با اولین SetKey قفل می‌شود و
            # تا Reset/Idleِ همان بریج عوض نمی‌شود — دستورات بعدی L/S روی این
            # نود فقط حلقهٔ ۴px را به‌روز می‌کنند («فقط پیکسل‌های استیج» قفل‌اند).
            with self._lock:
                locked = self._stage_lock.get(node.bridge_id)
                if locked is None:
                    self._stage_lock[node.bridge_id] = rgb
                    do_stage = True
                else:
                    do_stage = False
            if do_stage:
                # الف) رنگ ثابت ۳۳px دور استیج (بدون بایت key) — قفل شد
                self._send_frame(node, CMD_ACTION_STAGE_RGB, bytes(rgb),
                                 "stage_rgb")
                stage_note = "قفل شد 🔒"
            else:
                stage_note = f"قفل فعال {locked} — 0x45 ارسال نشد"
            # ب) حلقهٔ ۴pxِ کلید استارت (آزاد — خارج از قفل)
            self._send_frame(node, CMD_ACTION_RING,
                             bytes([0x00]) + bytes(rgb), "action_color")
            self.on_log(f"⭕ استارت نود 11 ({node.bridge_id}): استیج {stage_note}")
            return

        # ─── بقیهٔ کلیدها (۱..۱۰): حلقهٔ ۴px ───
        self._send_frame(node, CMD_ACTION_RING,
                         bytes([0x00]) + bytes(rgb), "action_color")

    def _send_frame(self, node, cmd: int, data: bytes, tag: str) -> None:
        """ارسال یک فریم به نود با قوانین ثابت (ACK/NACK + retry×۲)."""
        self.service.send_cmd(
            node.bridge_id, node.addr, cmd, data,
            tag=tag, expect_feedback=True,   # قانون: ACK/NACK + retry×2
        )

    def _find_node(self, addr: int, bridge_id: Optional[str] = None):
        """پیدا کردن نود با addr. اگر bridge_id داده شود → همان بریج (Node Bus)،
        وگرنه در همه بریج‌ها می‌گردد (get_by_addr_any معادل)."""
        if bridge_id:
            node = self.service.registry.get_by_addr(bridge_id, addr)
            return node
        for bid in self.service.bridges:
            node = self.service.registry.get_by_addr(bid, addr)
            if node is not None:
                return node
        return None

    def _find_bridge(self, addr: int, bridge_id: Optional[str] = None) -> str:
        node = self._find_node(addr, bridge_id)
        return node.bridge_id if node else ""
        
    def _handle_hide_fail(self, data: dict) -> list:
        """Fail → ابتدا 0x53[key] سپس 0x27[04,FF,00,00] (افکت Error قرمز)."""
        bridges = data.get("bridges") or []
        key = self._u_int(data, "key", 0, 15, 0)
        if key is None:
            return self._u_fail(bridges, "bad key(0..15)")
        # ① دستور پروتکل Fail
        #self._u_send(bridges, 0x53, bytes([key]), "hide_fail_cmd")
        # ② افکت Error قرمز (0x27 [04 FF 00 00])
        return self._u_send(bridges, 0x27, bytes([0x04, 0xFF, 0x00, 0x00]), "hide_fail_effect")

