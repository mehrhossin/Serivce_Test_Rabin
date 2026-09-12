"""
Action Game Logic — RBUS Industrial Node System
پروتکل: GameBridgeServer (TCP port 9000) ← Unity متصل میشه
"""
from __future__ import annotations

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
    "3": (255, 0,   128),   # صورتی — canonical (fix: قبلاً بنفش بود)
    "4": (255, 165, 0),     # نارنجی — canonical (fix: قبلاً صورتی بود)
    "5": (255, 255, 255),   # سفید
}

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
        bridge_server,                              # GameBridgeServer
        on_log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.service       = service
        self.bridge_server = bridge_server
        self.on_log        = on_log or (lambda m: log.info(m))

        # نودهایی که الان فعال هستن (تاچشون باید ارسال بشه)
        self._active_nodes: set[int] = set()

        import threading
        self._lock = threading.Lock()

    # ── API عمومی ─────────────────────────────────────────────────────────

    def start(self) -> bool:
        """فعال‌سازی — GameBridgeServer باید از قبل در حال اجرا باشه."""
        if not self.bridge_server.is_running():
            ok = self.bridge_server.start()
            if not ok:
                self.on_log("❌ Action Game: نمیتونم GameBridgeServer رو شروع کنم")
                return False

        self.on_log(
            f"✅ Action Game فعال شد "
            f"(سرور روی {self.bridge_server.host}:{self.bridge_server.port})"
        )
        return True

    def stop(self) -> None:
        with self._lock:
            self._active_nodes.clear()
        self.on_log("🔌 Action Game غیرفعال شد.")

    # ── رویداد تاچ (از GamesPanel) ────────────────────────────────────────

    def on_touch(self, addr: int) -> None:
        """
        وقتی نودی تاچ میشه صدا زده میشه.
        اگر نود در لیست فعال بود → به همه کلاینت‌های بازی push میشه.
        """
        with self._lock:
            if addr not in self._active_nodes:
                return

        msg = f"A{addr:02d}"
        self.on_log(f"👆 تاچ نود {addr:02d} → push به بازی: {msg!r}")

        # ارسال به همه کلاینت‌های متصل از طریق GameBridgeServer
        self.bridge_server.push_event("touch_event", {
            "addr":      addr,
            "bridge_id": self._find_bridge(addr),
            "ch":        1,
            "event":     "TOUCH",
        })

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

        elif msg_type == "action_get_groups":
            return self._handle_get_groups()

        return {"ok": False, "reason": f"unknown action cmd: {msg_type}"}

    # ── پردازش دستور L ────────────────────────────────────────────────────

    def _handle_L(self, data: dict) -> dict:
        """
        data: {"g": "1", "c": "2", "chk": "c"}
        یا raw string: {"raw": "L12c"}
        """
        # پشتیبانی از raw string — پروتکل «نرم‌افزار↔سرویس»: L{c}{s}{p}
        # c=رنگ(raw[1]) · s=ماسک(raw[2]) · p=چک‌سام(raw[3]) — fix: قبلاً برعکس بود
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

        # ── گروه‌ها یا نودهای مستقیم (ماسک) — پشتیبانی REST direct_nodes ──
        nodes: List[int] = []
        direct_nodes = data.get("nodes")
        if direct_nodes:
            nodes = [int(n) for n in direct_nodes]
            g_char = str(data.get("g", g_char))
        else:
            group_ids = _GROUP_MAP.get(g_char)
            if group_ids is None:
                return {"ok": False, "reason": f"invalid_group:{g_char}"}
            # ── رنگ ── (در مسیر direct_nodes هم چک می‌شود پایین)
            # ── جمع‌آوری نودها ──
            for gid in group_ids:
                nodes.extend(_GROUPS.get(gid, []))

        # ── رنگ ──
        color = _COLOR_MAP.get(c_char)
        if color is None:
            return {"ok": False, "reason": f"invalid_color:{c_char}"}

        with self._lock:
            self._active_nodes = set(nodes)

        r, g, b = color
        self.on_log(
            f"💡 L → گروه '{g_char}' | رنگ '{c_char}' {color} | نودها: {nodes}"
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
                        bridge_id: Optional[str] = None) -> None:
        # ✅ بازسازی بر اساس opcode اکشن: 0x42 ACTION_RING [key][r][g][b]
        # (قبلاً 0x26 SET_CHANNEL_COLOR [ch][idx][r][g][b] فرستاده می‌شد که
        #  ماشین‌حالت لمسِ اکشن را هرگز فعال نمی‌کرد → فید 200ms اجرا نمی‌شد —
        #  دقیقاً باگ v7.3 و نقض درس #۱۰ مموری.)
        #
        # قانون درس #۱۰: تبدیل رنگ فقط در send_cmd انجام می‌شود.
        # پس اینجا RGB خام داده می‌شود (بدون led_mapper.apply) — خودِ send_cmd
        # چون 0x42 را در _GAME_RGB_OPS دارد، آن را مپ می‌کند.
        from core.protocol import CMD_ACTION_RING
        data = bytes([0x00, r, g, b])   # key=0 → حلقه اکشن (بایت 1..3 = RGB)

        node = self._find_node(addr, bridge_id)
        if node is None:
            self.on_log(f"⚠️ نود {addr} (bridge={bridge_id or '*'}) در هیچ بریجی نیست")
            return

        self.service.send_cmd(
            node.bridge_id, addr,
            CMD_ACTION_RING, data,
            tag="action_color", expect_feedback=True,   # قانون: ACK/NACK + retry×2
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
