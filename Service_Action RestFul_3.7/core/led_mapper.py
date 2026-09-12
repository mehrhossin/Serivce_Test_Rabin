#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/led_mapper.py
مدیریت mapping کانال‌های RGB برای کل سیستم.
یک singleton — همه جا از همین نمونه استفاده می‌شود.
"""
from __future__ import annotations

from core.app_config import get as cfg_get, set_key as cfg_set


class LedMapper:
    """
    نگاشت کانال‌های R/G/B قبل از ارسال به نود.
    پیش‌فرض: R→R, G→G, B→B (بدون تغییر)
    """

    _CHANNELS = ("R", "G", "B")

    def __init__(self) -> None:
        self._map = [0, 1, 2]
        self.load_from_config()   # بارگذاری خودکار هنگام ساخت

    # ------------------------------------------------------------------ API

    def set_mapping(self, r_out: int, g_out: int, b_out: int) -> None:
        """
        تنظیم mapping جدید — فقط در حافظه.
        برای ذخیره دائمی باید save_to_config() صدا زده شود.
        """
        for v in (r_out, g_out, b_out):
            if v not in (0, 1, 2):
                raise ValueError(f"ایندکس کانال باید 0، 1 یا 2 باشد: {v}")
        self._map = [r_out, g_out, b_out]

    def apply(self, r: int, g: int, b: int) -> tuple[int, int, int]:
        """اعمال mapping روی یک رنگ."""
        src = (r, g, b)
        return (src[self._map[0]], src[self._map[1]], src[self._map[2]])

    def apply_bytes(self, ch: int, r: int, g: int, b: int) -> bytes:
        """ساخت payload بعد از اعمال mapping."""
        mr, mg, mb = self.apply(r, g, b)
        return bytes([ch & 0xFF, mr & 0xFF, mg & 0xFF, mb & 0xFF])

    @property
    def mapping(self) -> tuple[int, int, int]:
        return tuple(self._map)

    @property
    def mapping_label(self) -> str:
        names = ["R", "G", "B"]
        return (
            f"R→{names[self._map[0]]}  "
            f"G→{names[self._map[1]]}  "
            f"B→{names[self._map[2]]}"
        )

    # ------------------------------------------------------------------ config

    def load_from_config(self) -> None:
        """بارگذاری mapping از config.json."""
        saved = cfg_get("rgb_mapping")
        if (
            isinstance(saved, list)
            and len(saved) == 3
            and all(v in (0, 1, 2) for v in saved)
        ):
            self._map = list(saved)

    def save_to_config(self) -> None:
        """ذخیره mapping فعلی در config.json."""
        cfg_set("rgb_mapping", self._map)


# ── Singleton ────────────────────────────────────────────────────────────────
led_mapper = LedMapper()
