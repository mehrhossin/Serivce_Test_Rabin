#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run.py — اجرای سبک سرویس «بدون GUI» (بدون نیاز به PyQt5)

هدف: بالا آوردن بک‌اند + REST برای تست، بدون پنجرهٔ Qt و بدون سخت‌افزار.

    python run.py                        بریج‌های واقعی (192.168.1.7..11) را وصل می‌کند
    python run.py --demo                 + ۵ نود ساختگی (پاسخ GET /nodes پر شود)
    python run.py --no-bridge            پرش از اتصال به سخت‌افزار (برای تست/دمو)
    python run.py --demo --no-bridge     حالت دموی کامل بدون سخت‌افزار (توصیه برای تست)

سرویس‌هایی که بالا می‌آیند:
    9000  GameBridge (پل TCP بازی/قرارداد قدیمی)
    9001  REST (قرارداد جدید ApiEndpoint.txt) — همان‌جا که تست می‌گیری

بعد از اجرا، مرورگر → http://127.0.0.1:9001/
"""

from __future__ import annotations

import sys
import time
import core.bus_logger as bus_logger
bus_logger.init("bus_traffic.log")   # ← قبل از ساخت RBusService

from service import RBusService
from game_bridge import GameBridgeServer
from core.rest_api import RestApiServer

# بریج‌های واقعی (طبق مموری): Bridge-1..5 → 192.168.1.7..11
BRIDGES = [
    ("Bridge-1", "192.168.1.211",  5000),
     ("Bridge-2", "192.168.1.212",  5000),
     ("Bridge-3", "192.168.1.213",  5000),
     ("Bridge-4", "192.168.1.214", 5000),
    # ("Bridge-5", "192.168.1.214", 5000),
]

# نمونه‌های ساختگی (فقط برای دمو — زمانی که سخت‌افزار نیست)
DEMO_NODES = [
    ("Bridge-1", "DE:AD:BE:01", 1, True,  4, 101),
    ("Bridge-1", "DE:AD:BE:02", 2, True,  2, 102),
    ("Bridge-1", "DE:AD:BE:03", 3, True,  0, 103),
    ("Bridge-2", "DE:AD:BE:11", 1, True,  3, 201),
    ("Bridge-2", "DE:AD:BE:12", 2, False, 0, 202),
]

_gb = None    # game_bridge — که بعد از ساخت ست می‌شود
_rest = None  # RestApiServer — برای enqueue رویداد (لمس/فیدبک) به صف REST


def _log(msg: str) -> None:
    print(msg, flush=True)


def _on_event(kind: str, data: dict) -> None:
    """رویدادهای سرویس (kind, data):
       - به game_bridge push می‌شود (برای کلاینت‌های TCP قدیمی)؛
       - و به صف رویداد REST اضافه می‌شود تا «لمس/فیدبک میکرو → بازی» از طریق
         REST هم در دسترس باشد (کلاینت ActionGame با GET /api/events/drain poll)."""
    if _gb is not None:
        _gb.on_service_event(kind, data)
    if _rest is not None:
        _rest.enqueue_event(kind, data)


def _seed_demo(svc) -> None:
    """ثبت نودهای ساختگی تا پاسخ GET /nodes پر دیده شود (فقط دمو)."""
    for bid, mac, addr, has_action, tmask, adc in DEMO_NODES:
        n = svc.registry.upsert_status(mac, bid, addr, in_ota=False,
                                       touch_mask=tmask, adc=adc,
                                       fw_major=7, fw_minor=11)
        n.has_code = has_action
    _log(f"  🧪 {len(DEMO_NODES)} نود ساختگی ثبت شد (دمو)")


def main() -> int:
    args = sys.argv
    use_demo    = "--demo" in args
    use_bridges = "--no-bridge" not in args

    # ۱) هستهٔ سرویس
    svc = RBusService(_on_event)
    _log("🧩 سرویس ساخته شد")

    # ۲) بریج‌ها + نودهای دمو (اختیاری)
    if use_bridges:
        for bid, host, port in BRIDGES:
            svc.add_bridge(bid, host, port)
        _log("🔌 بریج‌های 1..5 (192.168.1.7..11) معرفی شدند")
    if use_demo:
        _seed_demo(svc)

    # ۳) پل TCP بازی (پورت 9000) — باید اول ساخته شود تا رویدادها push شوند
    global _gb
    _gb = GameBridgeServer(service=svc, on_log=_log)
    svc.game_bridge = _gb
    _gb.start()
    _log("🎮 GameBridge (TCP) روی پورت 9000")

    # ۳.۵) فعال‌سازی ActionGame (معادل تب «Games → Action Game = ON» در GUI)
    #       بدون آن، پیام‌های action_L/action_S با «action_game_not_active» رد می‌شوند.
    # ActionGame با پشتیبانی REST-only (bridge_server اختیاری)
    try:
        from action_game import ActionGame
    except ImportError:
        from fixed.action_game_FIXED import ActionGame  # fallback
    action = ActionGame(service=svc, bridge_server=_gb, on_log=_log)
    # rest_server بعداً ست می‌شود؛ برای on_touch REST هم لازم است
    # (اگر GameBridge None هم باشد کار می‌کند)
    if action.start():
        svc._action_game = action
        _log("🎯 Action Game فعال شد (پردازش Lcsp / Ssc → میکرو)")
    else:
        _log("⚠️  Action Game فعال نشد — بدون آن، اکشن به میکرو نمی‌رود")

    # اتصال خودکار به سخت‌افزار (تنها اگر خواسته شده)
    if use_bridges:
        for bid, _, _ in BRIDGES:
            try:
                svc.connect_bridge(bid)
            except Exception as e:
                _log(f"⚠️  اتصال {bid} ناموفق: {e}")

    # ۴) REST (پورت 9001) — جایی که تست می‌گیری
    global _rest
    rest = RestApiServer(service=svc, on_log=_log)
    _rest = rest
    # وصل کردن rest_server به ActionGame برای on_touch REST
    try:
        if svc._action_game is not None:
            svc._action_game.rest_server = rest
    except Exception:
        pass
    if not rest.start():
        _log("❌ REST bind نشد — پورت 9001 اشغال است؟")
    else:
        _log("🔗 REST روی http://127.0.0.1:9001/api")

    _log("\n✅ بالا آمد. مرورگر → http://127.0.0.1:9001/   (توقف: Ctrl+C)")

    # حلقهٔ نگه‌دار
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        _log("\n⏹  در حال توقف…")
        rest.stop()
        _gb.stop()
        svc.shutdown()
        _log("🔴 متوقف شد.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
