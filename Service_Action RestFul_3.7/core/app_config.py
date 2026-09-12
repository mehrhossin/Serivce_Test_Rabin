#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/app_config.py
ذخیره و بارگذاری تنظیمات برنامه در فایل config.json
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),  # کنار main.py
    "config.json"
)

_DEFAULTS: Dict[str, Any] = {
    "rgb_mapping": [0, 1, 2],   # R→R, G→G, B→B
}


def load() -> Dict[str, Any]:
    """بارگذاری config — اگر فایل نبود، مقادیر پیش‌فرض برمی‌گردد."""
    if not os.path.exists(CONFIG_PATH):
        return dict(_DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # مقادیر پیش‌فرض برای کلیدهای جدید که در فایل قدیمی نیستند
        for k, v in _DEFAULTS.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return dict(_DEFAULTS)


def save(data: Dict[str, Any]) -> None:
    """ذخیره config در فایل."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def get(key: str) -> Any:
    """خواندن یک کلید از config."""
    return load().get(key, _DEFAULTS.get(key))


def set_key(key: str, value: Any) -> None:
    """نوشتن یک کلید و ذخیره فوری."""
    data = load()
    data[key] = value
    save(data)
