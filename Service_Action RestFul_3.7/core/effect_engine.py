#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/effect_engine.py
موتور افکت‌های LED — اجرا در سرویس (حالت B)
هر tick یک دستور SET_CHANNEL_COLOR به نودها می‌فرستد.
"""
from __future__ import annotations

import colorsys
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional


# ── نام افکت‌ها ───────────────────────────────────────────────────────────────
EFFECT_RAINBOW  = "rainbow"    # رنگین‌کمان — هر نود رنگ متفاوت، همه shift
EFFECT_CHASE    = "chase"      # یک نود روشن، بقیه خاموش، حرکت به ترتیب
EFFECT_DISCO    = "disco"      # رنگ تصادفی روی نودهای تصادفی
EFFECT_BREATHE  = "breathe"    # نفس کشیدن — fade in/out با رنگ انتخابی

EFFECTS = [EFFECT_RAINBOW, EFFECT_CHASE, EFFECT_DISCO, EFFECT_BREATHE]

EFFECT_LABELS = {
    EFFECT_RAINBOW: "🌈 رنگین‌کمان",
    EFFECT_CHASE:   "💫 Chase (یکی حرکت کنه)",
    EFFECT_DISCO:   "🪩 دیسکو (تصادفی)",
    EFFECT_BREATHE: "🌬️ Breathe (fade)",
}

# ── پیش‌فرض سرعت ─────────────────────────────────────────────────────────────
DEFAULT_SPEED_MS = 80    # میلی‌ثانیه بین هر tick
MIN_SPEED_MS     = 20
MAX_SPEED_MS     = 800


class EffectEngine:
    """
    موتور افکت LED.
    یک thread جداگانه دارد که هر speed_ms میلی‌ثانیه tick می‌زند
    و دستورات رنگ را از طریق send_fn به نودها می‌فرستد.

    send_fn(bridge_id, addr, r, g, b) → None
    """

    def __init__(
        self,
        send_fn: Callable[[str, int, int, int, int], None],
    ) -> None:
        self._send   = send_fn
        self._lock   = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._alive  = False

        # وضعیت جاری
        self._effect:   str   = EFFECT_RAINBOW
        self._speed_ms: int   = DEFAULT_SPEED_MS
        self._color:    tuple = (0, 100, 255)   # R, G, B پیش‌فرض
        self._random_color: bool = False

        # لیست نودهای هدف: [{"bridge_id": str, "addr": int}, ...]
        self._nodes: List[Dict[str, Any]] = []

        # وضعیت داخلی افکت‌ها
        self._step      = 0
        self._hue_offset = 0.0

    # ------------------------------------------------------------------ API

    def set_nodes(self, nodes: List[Dict[str, Any]]) -> None:
        """تنظیم لیست نودهای هدف."""
        with self._lock:
            self._nodes = list(nodes)
            self._step  = 0

    def set_effect(self, effect: str) -> None:
        """تغییر افکت جاری."""
        with self._lock:
            self._effect = effect
            self._step   = 0
            self._hue_offset = 0.0

    def set_speed(self, speed_ms: int) -> None:
        """تنظیم سرعت (میلی‌ثانیه بین هر tick)."""
        with self._lock:
            self._speed_ms = max(MIN_SPEED_MS, min(MAX_SPEED_MS, speed_ms))

    def set_color(self, r: int, g: int, b: int) -> None:
        """تنظیم رنگ پایه برای افکت‌هایی که رنگ ثابت دارند."""
        with self._lock:
            self._color = (r & 0xFF, g & 0xFF, b & 0xFF)
            self._random_color = False

    def set_random_color(self, enabled: bool) -> None:
        """فعال/غیرفعال کردن رنگ تصادفی."""
        with self._lock:
            self._random_color = enabled

    @property
    def is_running(self) -> bool:
        return self._alive

    @property
    def current_effect(self) -> str:
        return self._effect

    @property
    def speed_ms(self) -> int:
        return self._speed_ms

    def start(self) -> None:
        """شروع موتور افکت."""
        if self._alive:
            return
        self._alive  = True
        self._step   = 0
        self._thread = threading.Thread(
            target=self._loop,
            name="EffectEngine",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """توقف موتور افکت."""
        self._alive = False

    def stop_and_clear(self) -> None:
        """توقف + خاموش کردن همه نودها."""
        self._alive = False
        time.sleep(0.05)
        with self._lock:
            nodes = list(self._nodes)
        for n in nodes:
            self._send(n["bridge_id"], n["addr"], 0, 0, 0)

    # ------------------------------------------------------------------ loop

    def _loop(self) -> None:
        while self._alive:
            t0 = time.time()

            with self._lock:
                effect   = self._effect
                nodes    = list(self._nodes)
                speed_ms = self._speed_ms
                color    = self._color
                rand_col = self._random_color
                step     = self._step

            if nodes:
                self._tick(effect, nodes, step, color, rand_col)

            with self._lock:
                self._step = (step + 1) % max(len(nodes), 1)
                self._hue_offset = (self._hue_offset + 0.01) % 1.0

            elapsed = (time.time() - t0) * 1000
            wait    = max(0, speed_ms - elapsed) / 1000
            time.sleep(wait)

    # ------------------------------------------------------------------ tick

    def _tick(
        self,
        effect: str,
        nodes: List[Dict[str, Any]],
        step: int,
        color: tuple,
        rand_col: bool,
    ) -> None:
        n = len(nodes)
        if n == 0:
            return

        if effect == EFFECT_RAINBOW:
            self._tick_rainbow(nodes, step)

        elif effect == EFFECT_CHASE:
            self._tick_chase(nodes, step, color, rand_col)

        elif effect == EFFECT_DISCO:
            self._tick_disco(nodes)

        elif effect == EFFECT_BREATHE:
            self._tick_breathe(nodes, step, color, rand_col)

    # ── Rainbow ──────────────────────────────────────────────────────────────
    def _tick_rainbow(self, nodes: List[Dict], step: int) -> None:
        """هر نود رنگ متفاوت از طیف — همه با هم shift می‌کنند."""
        n = len(nodes)
        with self._lock:
            hue_off = self._hue_offset
        for i, node in enumerate(nodes):
            hue = (i / n + hue_off) % 1.0
            r, g, b = self._hsv_to_rgb(hue, 1.0, 1.0)
            self._send(node["bridge_id"], node["addr"], r, g, b)

    # ── Chase ─────────────────────────────────────────────────────────────────
    def _tick_chase(
        self,
        nodes: List[Dict],
        step: int,
        color: tuple,
        rand_col: bool,
    ) -> None:
        """یک نود روشن، بقیه خاموش — به ترتیب حرکت می‌کند."""
        active = step % len(nodes)
        for i, node in enumerate(nodes):
            if i == active:
                if rand_col:
                    r, g, b = self._random_rgb()
                else:
                    r, g, b = color
            else:
                r, g, b = 0, 0, 0
            self._send(node["bridge_id"], node["addr"], r, g, b)

    # ── Disco ─────────────────────────────────────────────────────────────────
    def _tick_disco(self, nodes: List[Dict]) -> None:
        """رنگ تصادفی روی نودهای تصادفی."""
        # هر tick تعداد تصادفی از نودها رنگ تصادفی می‌گیرند
        count = random.randint(1, max(1, len(nodes) // 2))
        targets = random.sample(nodes, count)
        for node in targets:
            r, g, b = self._random_rgb()
            self._send(node["bridge_id"], node["addr"], r, g, b)

    # ── Breathe ───────────────────────────────────────────────────────────────
    def _tick_breathe(
        self,
        nodes: List[Dict],
        step: int,
        color: tuple,
        rand_col: bool,
    ) -> None:
        """fade in/out — همه نودها با هم نفس می‌کشند."""
        import math
        # یک سینوس کامل در 100 step
        brightness = (math.sin(step * 2 * math.pi / 100) + 1) / 2
        if rand_col:
            # رنگ هر سیکل تغییر می‌کند — با hue_offset
            with self._lock:
                hue = self._hue_offset
            r, g, b = self._hsv_to_rgb(hue, 1.0, brightness)
        else:
            r = int(color[0] * brightness)
            g = int(color[1] * brightness)
            b = int(color[2] * brightness)
        for node in nodes:
            self._send(node["bridge_id"], node["addr"], r, g, b)

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        return int(r * 255), int(g * 255), int(b * 255)

    @staticmethod
    def _random_rgb() -> tuple[int, int, int]:
        return random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)
