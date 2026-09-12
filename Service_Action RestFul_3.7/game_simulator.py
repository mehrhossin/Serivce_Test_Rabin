#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
game_simulator.py — ✅ v6.9 — شبیه‌ساز ۴ بازی روی قرارداد v5.1 (سفارش کاربر)

«برای هر قرارداد یک بازی شبیه‌ساز بساز که بتوانم تست اولیه بگیرم
 برای صفر کردن درصد خطا»

هر بازی = سناریوی واقعی و کامل (همان چیزی که Unity می‌فرستد) روی
 PUT /api/nodes/set با فیلد Game — هر گام:
   ۱) فراخوانی قرارداد (HTTP)
   ۲) assert پاسخ (200 + applied.ok=true / 400 موردنظر)
   ۳) assert فریم روی وایر (opcode/payload/addr — حالت virtual)
   ۴) assert رویداد میکرو در drain (GAME_EVENT 0x13)

گزارش: هر گام ✅/❌ + درصد خطا هر بازی + درصد خطا کل.
خروجی غیرصفر اگر خطا > 0 — هدف: صفر کردن درصد خطا.

اجرا:
  python game_simulator.py                 # خودکار روی شبیه‌ساز (12 نود مجازی)
  python game_simulator.py --list          # فهرست سناریوها
  python game_simulator.py --game Hide     # فقط یک بازی
  python game_simulator.py --live --host 192.168.1.7 --port 9001 \
         --token RBUS-TEST-TOKEN-0001      # روی سرویس واقعی روی میز شما
                                            # (چک وایر/رویداد شبیه‌سازی‌شده → SKIP)
"""
import json
import sys
import os
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─────────────────────────── سناریوها (قرارداد v5.1) ───────────────────────────
# هر گام: ("cmd", نام, بدنه, [(opcode, payload_hex), ...])
#         ("err", نام, بدنه)            → انتظار 400
#         ("evt", نام, addr, evt, val)  → رویداد میکرو → drain kind="game"
#         ("note", نام)                 → فقط حالت virtual/live فرق دارد

SCENARIOS = {

    "Action": [
        ("cmd", "L11b — ۴ نود اول آبی (0x42×4 نود 1..4)",
         {"Game": "Action", "Bridge": 1, "General": "L11b"}, "L"),
        ("cmd", "S41 — کلید نود ۴ آبی (0x42)",
         {"Game": "Action", "Bridge": 1, "SetKey": "S41"},
         [(0x42, "000000ff", 4)]),
        ("cmd", "Idle — رنگین‌کمان بریج (0x27[01])",
         {"Game": "Action", "Bridge": 1, "Idle": True},
         [(0x27, "01", 0xFF)]),
        ("cmd", "Reset — ری‌استارت بریج (0x02)",
         {"Game": "Action", "Bridge": 1, "Reset": True},
         [(0x02, "", 0xFF)]),
        ("err", "بدون Game → 400",
         {"Bridge": 1, "General": "L11b"}),
        ("err", "چک‌سام غلط L11x → 400",
         {"Game": "Action", "Bridge": 1, "General": "L11x"}),
    ],

    "Hide": [
        ("cmd", "NNN — رنگین‌کمان/آماده‌سازی (0x27[01])",
         {"Game": "Hide", "Bridge": 1, "General": "NNN"},
         [(0x27, "01", 0xFF)]),
        ("cmd", "display R123 — نمایش عدد ۲۳ (0x50)",
         {"Game": "Hide", "Bridge": 1, "General": "R123" + chr((ord("1") + ord("2") + ord("3")) & 0x7F), "key": 0},
         [(0x50, "000000ff0302", 0xFF)]),
        ("cmd", "Arm — مسلح‌سازی ۳۰s کلید۰ (0x51)",
         {"Game": "Hide", "Bridge": 1, "Arm": True, "color": "1", "sec": 30, "key": 0},
         [(0x51, "000000ff1e", 0xFF)]),
        ("cmd", "Start — شروع (0x52 [key])",
         {"Game": "Hide", "Bridge": 1, "Start": True, "key": 0},
         [(0x52, "00", 0xFF)]),
        ("evt", "KEY_HIT از نود ۷ → drain", 7, 0x01, 2),
        ("evt", "SUCCESS_END از نود ۷ → drain", 7, 0x04, 0),
        ("cmd", "Fail — مسیر شکست (0x53 [key])",
         {"Game": "Hide", "Bridge": 1, "Fail": True, "key": 5},
         [(0x53, "05", 0xFF)]),
        ("cmd", "Motion=1 (0x54)",
         {"Game": "Hide", "Bridge": 1, "Motion": 1},
         [(0x54, "", 0xFF)]),
        ("cmd", "Motion=2 / WWW (0x55)",
         {"Game": "Hide", "Bridge": 1, "General": "WWW"},
         [(0x55, "", 0xFF)]),
        ("cmd", "Cancel — لغو (0x56)",
         {"Game": "Hide", "Bridge": 1, "Cancel": True},
         [(0x56, "", 0xFF)]),
        ("cmd", "RRR — ریست (0x02)",
         {"Game": "Hide", "Bridge": 1, "General": "RRR"},
         [(0x02, "", 0xFF)]),
        ("err", "chk غلط R123z → 400",
         {"Game": "Hide", "Bridge": 1, "General": "R123z"}),
    ],

    "Vibron": [
        ("cmd", "SSS — انتخاب گرید (0x61)",
         {"Game": "Vibron", "Bridge": 1, "GridEffect": "SSS"},
         [(0x61, "", 0xFF)]),
        ("cmd", "TimerStart — مثال سند (0x65)",
         {"Game": "Vibron", "Bridge": 1, "TimerStart": True,
          "color": "1", "sec": 10, "key": 0},
         [(0x65, "000000ff0a", 0xFF)]),
        ("cmd", "SetKey S13 — حلقهٔ کلید۱ صورتی (0x64)",
         {"Game": "Vibron", "Bridge": 1, "SetKey": "S13"},
         [(0x64, "01ff0080", 0xFF)]),
        ("evt", "TIMEOUT (تایمر تمام) از نود ۳ → drain", 3, 0x02, 0),
        ("cmd", "TimerStop (0x66 — فیرمور پارامتر نمی‌خواند)",
         {"Game": "Vibron", "Bridge": 1, "TimerStop": True},
         [(0x66, "", 0xFF)]),
        ("cmd", "LLL — باران با پارامتر (0x62 [06 03])",
         {"Game": "Vibron", "Bridge": 1, "GridEffect": "LLL",
          "drops": 6, "trail": 3},
         [(0x62, "0603", 0xFF)]),
        ("cmd", "MMM — توقف گرید (0x63)",
         {"Game": "Vibron", "Bridge": 1, "GridEffect": "MMM"},
         [(0x63, "", 0xFF)]),
        ("cmd", "EEE — خطای گرید (0x60)",
         {"Game": "Vibron", "Bridge": 1, "GridEffect": "EEE"},
         [(0x60, "", 0xFF)]),
        ("err", "GridEffect نامعتبر → 400",
         {"Game": "Vibron", "Bridge": 1, "GridEffect": "XXX"}),
    ],

    "Tesla": [
        ("cmd", "TeslaStart — رعد (0x70)",
         {"Game": "Tesla", "Bridge": 1, "TeslaStart": True},
         [(0x70, "", 0xFF)]),
        ("cmd", "DropArm — مثال سند (0x72 ده‌بایتی)",
         {"Game": "Tesla", "Bridge": 1, "DropArm": True, "color": "1",
          "sec": 10, "drops": 1, "trail": 7, "act_pct": 85,
          "flags": 1, "length": 13, "key": 0},
         [(0x72, "000000ff0a010755010d", 0xFF)]),
        ("evt", "DROP_ACTIVE از نود ۹ → drain", 9, 0x11, 1),
        ("evt", "DROP_HIT از نود ۹ → drain", 9, 0x12, 1),
        ("evt", "DROP_END از نود ۹ → drain", 9, 0x15, 4),
        ("cmd", "DropStatus (0x73)",
         {"Game": "Tesla", "Bridge": 1, "DropStatus": True},
         [(0x73, "", 0xFF)]),
        ("cmd", "DropCancel (0x74)",
         {"Game": "Tesla", "Bridge": 1, "DropCancel": True},
         [(0x74, "", 0xFF)]),
        ("cmd", "LightningFx=false — توقف (0x71)",
         {"Game": "Tesla", "Bridge": 1, "LightningFx": False},
         [(0x71, "", 0xFF)]),
        ("err", "General در Tesla → 400",
         {"Game": "Tesla", "Bridge": 1, "General": "RRR"}),
    ],
}

PALETTE = {"1": (0, 0, 255), "2": (255, 200, 0), "3": (255, 0, 128),
           "4": (255, 165, 0), "5": (255, 255, 255)}
GEVT = {0x01: "KEY_HIT", 0x02: "TIMEOUT", 0x04: "SUCCESS_END",
        0x11: "DROP_ACTIVE", 0x12: "DROP_HIT", 0x15: "DROP_END"}


# ═══════════════════════════ HTTP helper ═══════════════════════════

class Http:
    def __init__(self, host, port, token):
        self.base, self.token = f"http://{host}:{port}/api", token

    def put_set(self, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + "/nodes/set", data=data,
                                     method="PUT",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + self.token})
        try:
            with urllib.request.urlopen(req, timeout=4) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {}
        except Exception as e:
            return 0, {"ok": False, "reason": str(e)}

    def drain(self):
        req = urllib.request.Request(self.base + "/events/drain?wait=0",
                                     headers={"Authorization": "Bearer " + self.token})
        try:
            with urllib.request.urlopen(req, timeout=4) as r:
                return json.loads(r.read().decode()).get("events", [])
        except Exception:
            return []


# ═══════════════════════════ runner ═══════════════════════════

def run_game(name, steps, http, vb, live):
    """اجرای سناریو — برمی‌گرداند (ok_count, total, جزئیات خطاها)"""
    ok_count, total, errors = 0, 0, []
    for step in steps:
        kind = step[0]
        total += 1
        if kind == "cmd":
            _, label, body, wants = step
            if vb is not None:
                vb.clear_frames()
            st, b = http.put_set(body)
            ap = b.get("applied", {}) if isinstance(b, dict) else {}
            ok = st == 200 and ap.get("ok") is True
            # چک وایر (فقط حالت شبیه‌ساز)
            if ok and vb is not None and wants != "L":
                for opcode, payload, addr in wants:
                    frames = []
                    t0 = time.time()
                    while time.time() - t0 < 4:
                        frames = [f for f in vb.wire(only_cmd=opcode)
                                  if f["addr"] == addr]
                        if frames and frames[-1]["data"] == payload:
                            break
                        time.sleep(0.05)
                    ok = ok and bool(frames) and frames[-1]["data"] == payload
                    if not ok:
                        label += f" [وایر: انتظار 0x{opcode:02X}={payload}، " \
                                 f"دریافت {frames[-1]['data'] if frames else '—'}]"
            elif ok and live and wants != "L":
                # حالت live: فقط applied.results را با انتظار مقایسه کن
                rs = ap.get("results", [])
                for opcode, payload, _addr in wants:
                    ok = ok and any(r.get("opcode") == f"0x{opcode:02X}"
                                    and r.get("payload") == payload
                                    for r in rs)
                    if not ok:
                        label += f" [applied: انتظار 0x{opcode:02X}={payload}]"
            if ok:
                ok_count += 1
                print(f"    ✅ {label}")
            else:
                why = str(b)[:110] if not ap.get("ok") else label
                errors.append(label)
                print(f"    ❌ {label}  →  HTTP {st} {why[:110]}")
        elif kind == "err":
            _, label, body = step
            st, b = http.put_set(body)
            if st == 400:
                ok_count += 1
                print(f"    ✅ {label}  (HTTP 400)")
            else:
                errors.append(label)
                print(f"    ❌ {label}  →  HTTP {st} (انتظار 400)")
        elif kind == "evt":
            _, label, addr, evt, value = step
            if vb is None:
                print(f"    ⏭️  {label}  (SKIP — فقط حالت شبیه‌ساز)")
                total -= 1          # خطا حساب نمی‌شود
                continue
            vb.game_event(addr=addr, evt=evt, value=value)
            got, ev = False, None
            t0 = time.time()
            while time.time() - t0 < 5 and not got:
                for e in http.drain():
                    d = e.get("data", {})
                    if e.get("kind") == "game" and d.get("addr") == addr \
                       and d.get("evt") == evt:
                        got, ev = True, d
                        break
                if not got:
                    time.sleep(0.05)
            if got and ev.get("evt_name") == GEVT.get(evt):
                ok_count += 1
                print(f"    ✅ {label}  ({ev['evt_name']} seq={ev.get('seq')})")
            else:
                errors.append(label)
                print(f"    ❌ {label}  →  {ev or 'رویداد در drain نبود'}")
    return ok_count, total, errors


def main():
    args = sys.argv[1:]
    live = "--live" in args
    only = None
    if "--game" in args:
        only = args[args.index("--game") + 1]
    if "--list" in args:
        for g, steps in SCENARIOS.items():
            n_cmd = sum(1 for s in steps if s[0] in ("cmd", "err"))
            n_evt = sum(1 for s in steps if s[0] == "evt")
            print(f"  {g:8s} — {n_cmd} فرمان + {n_evt} رویداد")
        return 0

    vb = None
    errors_all, grand_ok, grand_total = [], 0, 0

    if live:
        def _opt(flag, default):
            return args[args.index(flag) + 1] if flag in args else default
        http = Http(_opt("--host", "127.0.0.1"), int(_opt("--port", "9001")),
                    _opt("--token", "RBUS-TEST-TOKEN-0001"))
        print("⚡ حالت LIVE — سرویس واقعی (چک وایر = applied.results، "
              "رویداد شبیه‌سازی‌شده = SKIP)")
    else:
        from virtual_bridge import VirtualBridge
        from service import RBusService
        from core.rest_api import RestApiServer
        from game_bridge import GameBridgeServer
        from action_game import ActionGame
        VB_PORT, R_PORT, G_PORT = 19170, 19171, 19172
        vb = VirtualBridge(VB_PORT, nodes=12)
        assert vb.start(), "شبیه‌ساز بریج بالا نیامد"
        rest_srv = [None]

        def on_event(kind, data):
            try:
                rest_srv[0].enqueue_event(kind, data)
            except Exception:
                pass

        svc = RBusService(on_event)
        svc.add_bridge("Bridge-1", "127.0.0.1", VB_PORT)
        gb = GameBridgeServer(service=svc, host="127.0.0.1", port=G_PORT)
        gb.start()
        rest_srv[0] = RestApiServer(service=svc, host="127.0.0.1", port=R_PORT)
        rest_srv[0].start()
        ag = ActionGame(service=svc, bridge_server=gb, rest_server=rest_srv[0],
                        on_log=lambda m: None)
        ag.start()
        svc._action_game = ag
        assert svc.connect_bridge("Bridge-1")
        http = Http("127.0.0.1", R_PORT, "RBUS-TEST-TOKEN-0001")
        # کشف نودها
        t0 = time.time()
        while time.time() - t0 < 20:
            if len(http.drain()) >= 0 and _count(svc) >= 12:
                break
            time.sleep(0.2)
        print("🎮 حالت شبیه‌ساز — ۱۲ نود مجازی روی وایر (قانون ۱۸)")

    try:
        for gname, steps in SCENARIOS.items():
            if only and gname.lower() != only.lower():
                continue
            icons = {"Action": "🎯", "Hide": "🔢", "Vibron": "🌀", "Tesla": "⚡"}
            print(f"\n{icons.get(gname, '•')} {gname} — سناریوی کامل")
            ok, tot, errs = run_game(gname, steps, http, vb, live)
            grand_ok += ok
            grand_total += tot
            pct = 100.0 * (tot - ok) / tot if tot else 0.0
            mark = "✅" if pct == 0 else "❌"
            print(f"  → {gname}: {ok}/{tot} {mark} — درصد خطا: {pct:.1f}%")
            errors_all += [f"{gname}/{e}" for e in errs]
    finally:
        if vb is not None:
            try: ag.stop()
            except Exception: pass
            try: rest_srv[0].stop()
            except Exception: pass
            vb.stop()

    grand_total = max(grand_total, 1)
    pct = 100.0 * (grand_total - grand_ok) / grand_total
    print("\n" + "=" * 64)
    print(f"📊 جمع کل: {grand_ok}/{grand_total} — درصد خطا: {pct:.1f}%"
          + ("  ✅ صفر شد" if pct == 0 else "  ❌"))
    if errors_all:
        print(" خطاها:")
        for e in errors_all:
            print(f"   • {e}")
    return 0 if pct == 0 else 1


def _count(svc):
    try:
        return len([n for n in svc.registry.all()])
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
