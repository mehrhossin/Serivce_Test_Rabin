#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : core/protocol.py
توضیح   : تمام ثابت‌های پروتکل، محاسبه CRC16، ساخت و تجزیه فریم RBUS
           این فایل باید دقیقاً با firmware/rbus_node.h یکسان باشد
===============================================================================
"""

from __future__ import annotations

import struct
from typing import Any, Dict, Optional, Tuple

# =============================================================================
# ثابت‌های پروتکل (باید با firmware/rbus_node.h یکسان باشد)
# =============================================================================
FRAME_START    = 0xAA   # بایت شروع هر فریم RBUS
ADDR_BROADCAST = 0xFF   # آدرس broadcast برای ارسال به همه نودها
ADDR_MASTER    = 0x00   # آدرس مستر (این سرویس)

# ---- دستورات عمومی ---------------------------------------------------------
CMD_SET_COLOR       = 0x01
CMD_RESET           = 0x02
CMD_GET_STATUS      = 0x03
CMD_PING            = 0x04
CMD_SET_BRIGHTNESS  = 0x05
CMD_CLEAR_ALL       = 0x06
CMD_IDENTIFY        = 0x07

# ---- دستورات رویداد (از نود به مستر) ---------------------------------------
CMD_TOUCH_EVENT  = 0x10
CMD_ADC_EVENT    = 0x11
CMD_MOTION_EVENT = 0x12
CMD_GAME_EVENT   = 0x13   # ✅ v6.8 — رویدادهای بازی از نود: [addr][key][evt][value][seq] (GEVT)

# ✅ v6.8 — نام رویدادهای بازی (GEVT — مطابق فیرمور v7.12)
GEVT_NAMES = {
    0x01: "KEY_HIT", 0x02: "TIMEOUT", 0x03: "WRONG_KEY",
    0x04: "SUCCESS_END", 0x05: "FAIL_END", 0x06: "ARMED",
    0x07: "STARTED", 0x08: "CANCELED",
    0x10: "DROP_ARMED", 0x11: "DROP_ACTIVE", 0x12: "DROP_HIT",
    0x13: "DROP_MISS", 0x14: "DROP_TIMEOUT", 0x15: "DROP_END",
    0x16: "DROP_CANCELED", 0x17: "GRID_KEY_DONE",
}

# ---- دستورات کشف و آدرس‌دهی -----------------------------------------------
CMD_DISCOVERY_REQ = 0x20
CMD_DISCOVERY_RES = 0x21
CMD_SET_ADDRESS   = 0x22
CMD_ADDRESS_ACK   = 0x23
CMD_CLEAR_ADDRESS = 0x24

# ---- دستورات LED و افکت ----------------------------------------------------
CMD_SET_PIXEL_COLOR    = 0x25
CMD_SET_CHANNEL_COLOR  = 0x26
CMD_PIXEL_EFFECT       = 0x27
# ✅ v6.2 — شناسهٔ افکت‌های 0x27 (PIXEL_EFFECT) که سرویس استفاده می‌کند:
EFFECT_NONE             = 0x00   # Reset نرم — لغو همهٔ افکت‌ها/بازی‌ها + خاموشی LED
EFFECT_RAINBOW          = 0x01   # Idle — افکت رنگین‌کمان
CMD_STAGE_LOCK          = 0x28
CMD_STAGE_UNLOCK        = 0x29
EFFECT_TESLA_IDLE       = 0x08   # ✅ v7.3: Idle اختصاصی Tesla

# ---- دستورات بوت‌لودر -------------------------------------------------------
CMD_ENTER_BOOT = 0x30
CMD_BOOT_ACK   = 0x31

# ---- دستورات پیکربندی نود --------------------------------------------------
CMD_SET_PIXEL_COUNT = 0x40
# NOTE: 0x41 (SET_NODE_CONFIG) در firmware/rbus_node.h وجود ندارد — حذف شد

# ---- v7.0: بلوک اکشن (0x4x) ----
CMD_ACTION_RING      = 0x42   # [key][r][g][b]
CMD_ACTION_RING_OFF  = 0x43   # v7.11: ACTION_ALL_OFF
CMD_ACTION_ALL_OFF   = CMD_ACTION_RING_OFF   # ✅ v6.5 — نام canonical
CMD_ACTION_FOOT_MODE = 0x44   # v7.6: [enable]
CMD_ACTION_STAGE_RGB = 0x45   # v7.10: [R G B]

# ---- بلوک هايد (0x5x) ----
CMD_SET_KEY_DISPLAY  = 0x50
CMD_GAME_ARM         = 0x51
CMD_GAME_START       = 0x52
CMD_GAME_FAIL        = 0x53
CMD_MOTION_MONITOR_1 = 0x54
CMD_MOTION_MONITOR_2 = 0x55
CMD_GAME_CANCEL      = 0x56
CMD_GAME_WAIT        = 0x57
CMD_GAME_STATUS      = 0x58
CMD_SELF_TEST        = 0x59

# ---- بلوک گرید (0x6x) ----
CMD_GRID_ERROR       = 0x60
CMD_GRID_SELECT      = 0x61
CMD_GRID_RAIN        = 0x62
CMD_GRID_STOP        = 0x63
CMD_VIBRON_RING      = 0x64
CMD_TIMER_KEY_START  = 0x65
CMD_TIMER_STOP       = 0x66
CMD_SET_TIMER_COUNT  = 0x67

# ---- بلوک تسلا/قطره (0x7x) ----
CMD_TESLA_START  = 0x70
CMD_TESLA_STOP   = 0x71
CMD_DROP_ARM     = 0x72
CMD_DROP_STATUS  = 0x73
CMD_DROP_CANCEL  = 0x74

# ---- دستورات OTA (به‌روزرسانی firmware) ------------------------------------
CMD_UPDATE_START = 0xA0
CMD_UPDATE_DATA  = 0xA1
CMD_UPDATE_END   = 0xA2
CMD_UPDATE_ACK   = 0xA3
CMD_BOOT_ERROR   = 0xA4
CMD_ABORT_BOOT   = 0xA5

# ---- پاسخ‌های ACK / NACK ---------------------------------------------------
CMD_ACK  = 0xAA
CMD_NACK = 0xBB

# ---- نگاشت کد دستور به نام متنی -------------------------------------------
CMD_NAMES: Dict[int, str] = {
    CMD_SET_COLOR: "SET_COLOR", CMD_RESET: "RESET",
    CMD_GET_STATUS: "GET_STATUS", CMD_PING: "PING",
    CMD_SET_BRIGHTNESS: "SET_BRIGHTNESS", CMD_CLEAR_ALL: "CLEAR_ALL",
    CMD_IDENTIFY: "IDENTIFY", CMD_TOUCH_EVENT: "TOUCH_EVENT",
    CMD_ADC_EVENT: "ADC_EVENT", CMD_DISCOVERY_REQ: "DISCOVERY_REQ",
    CMD_MOTION_EVENT: "MOTION_EVENT",
    CMD_DISCOVERY_RES: "DISCOVERY_RES", CMD_SET_ADDRESS: "SET_ADDRESS",
    CMD_ADDRESS_ACK: "ADDRESS_ACK", CMD_CLEAR_ADDRESS: "CLEAR_ADDRESS",
    CMD_SET_PIXEL_COLOR: "SET_PIXEL_COLOR",
    CMD_SET_CHANNEL_COLOR: "SET_CHANNEL_COLOR",
    CMD_PIXEL_EFFECT: "PIXEL_EFFECT", CMD_STAGE_LOCK: "STAGE_LOCK",
    CMD_STAGE_UNLOCK: "STAGE_UNLOCK", CMD_ENTER_BOOT: "ENTER_BOOT",
    CMD_BOOT_ACK: "BOOT_ACK", CMD_SET_PIXEL_COUNT: "SET_PIXEL_COUNT",
    CMD_ACTION_RING: "ACTION_RING", CMD_ACTION_RING_OFF: "ACTION_OFF",
    CMD_ACTION_FOOT_MODE: "FOOT_MODE", CMD_ACTION_STAGE_RGB: "STAGE_RGB",
    CMD_SET_KEY_DISPLAY: "SET_KEY_DISPLAY", CMD_GAME_ARM: "GAME_ARM",
    CMD_GAME_START: "GAME_START", CMD_GAME_FAIL: "GAME_FAIL",
    CMD_MOTION_MONITOR_1: "MOTION_MONITOR_1",
    CMD_MOTION_MONITOR_2: "MOTION_MONITOR_2",
    CMD_GAME_CANCEL: "GAME_CANCEL", CMD_GAME_WAIT: "GAME_WAIT",
    CMD_GAME_STATUS: "GAME_STATUS", CMD_SELF_TEST: "SELF_TEST",
    CMD_GRID_ERROR: "GRID_ERROR", CMD_GRID_SELECT: "GRID_SELECT",
    CMD_GRID_RAIN: "GRID_RAIN", CMD_GRID_STOP: "GRID_STOP",
    CMD_VIBRON_RING: "VIBRON_RING", CMD_TIMER_KEY_START: "TIMER_KEY_START",
    CMD_TIMER_STOP: "TIMER_STOP", CMD_SET_TIMER_COUNT: "SET_TIMER_COUNT",
    CMD_TESLA_START: "TESLA_START", CMD_TESLA_STOP: "TESLA_STOP",
    CMD_DROP_ARM: "DROP_ARM", CMD_DROP_STATUS: "DROP_STATUS",
    CMD_DROP_CANCEL: "DROP_CANCEL",
    CMD_UPDATE_START: "UPDATE_START", CMD_UPDATE_DATA: "UPDATE_DATA",
    CMD_UPDATE_END: "UPDATE_END", CMD_UPDATE_ACK: "UPDATE_ACK",
    CMD_BOOT_ERROR: "BOOT_ERROR", CMD_ABORT_BOOT: "ABORT_BOOT",
    CMD_ACK: "ACK(OO)", CMD_NACK: "NACK(PP)",
}

# ---- کدهای خطای NACK -------------------------------------------------------
NACK_ERR_NAMES: Dict[int, str] = {
    0x01: "INVALID_PARAM",
    0x02: "OUT_OF_BOUNDS",
    0x03: "STAGE_LOCKED",
    0x04: "NOT_ARMED",
    0x11: "AUTH_FAILED",
    0xFF: "UNKNOWN_CMD",
}

# ---- رمز عبور پیش‌فرض OTA --------------------------------------------------
OTA_PASSWORD = bytes([0xDE, 0xAD, 0xBE, 0xEF])

# ---- واقعیت سخت‌افزاری -----------------------------------------------------
ACTIVE_LED_CHANNELS    = (1, 2)
NODE13_ONLY_CHANNELS   = (2,)
ACTIVE_TOUCH_CHANNELS  = (0, 1)

# ---- تنظیمات زمان‌بندی سیستم -----------------------------------------------
POLL_INTERVAL_S      = 0.05
POLL_TIMEOUT_S       = 0.08
DISCOVERY_INTERVAL_S = 10.0
FEEDBACK_TIMEOUT_S   = 0.5

# ---- تنظیمات OTA -----------------------------------------------------------
OTA_CHUNK_SIZE         = 240
OTA_BOOT_TIMEOUT_S     = 3.0
OTA_ACK_TIMEOUT_S      = 3.0
OTA_DATA_ACK_TIMEOUT_S = 0.5
OTA_END_TIMEOUT_S      = 8.0
OTA_MAX_RETRIES        = 5

# ---- محدودیت بافر دریافت ---------------------------------------------------
MAX_RX_BUF = 65536

# ---- اولویت‌های صف TX (✅ patch: جلوگیری از تداخل poll با دستورات بازی) ----
TX_PRIORITY_GAME = 0    # دستورات بازی — بالاترین اولویت
TX_PRIORITY_SYS  = 5    # دستورات سیستمی (discovery, assign)
TX_PRIORITY_POLL = 10   # polling — پایین‌ترین اولویت


# =============================================================================
# CRC16-CCITT  (init=0xFFFF, poly=0x1021)
# =============================================================================
def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    """محاسبه CRC16-CCITT برای داده ورودی."""
    for byte in data:
        crc ^= (byte << 8) & 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


# =============================================================================
# ساخت فریم RBUS
# =============================================================================
def build_frame(addr: int, cmd: int, data: bytes = b"") -> bytes:
    """
    یک فریم RBUS کامل با CRC می‌سازد.
    ساختار: [START][ADDR][CMD][LEN][DATA...][CRC_LO][CRC_HI]
    """
    if len(data) > 255:
        raise ValueError("طول payload بیش از حد مجاز است (حداکثر 255 بایت)")
    header = bytes([FRAME_START, addr & 0xFF, cmd & 0xFF, len(data) & 0xFF])
    crc = crc16_ccitt(header + data)
    return header + data + struct.pack("<H", crc)


# =============================================================================
# تجزیه فریم RBUS از بافر دریافتی
# =============================================================================
def parse_frame(buf: bytes) -> Tuple[Optional[Dict[str, Any]], bytes]:
    """
    یک فریم RBUS معتبر را از ابتدای بافر استخراج می‌کند.
    بایت‌های garbage قبل از FRAME_START را نادیده می‌گیرد.
    خروجی: (دیکشنری فریم یا None، بافر باقی‌مانده)
    """
    while True:
        start = buf.find(bytes([FRAME_START]))
        if start < 0:
            return None, b""
        if start > 0:
            buf = buf[start:]
        if len(buf) < 4:
            return None, buf
        addr, cmd, dlen = buf[1], buf[2], buf[3]
        total = 4 + dlen + 2
        if len(buf) < total:
            return None, buf
        data = buf[4:4 + dlen]
        crc_rx   = struct.unpack("<H", buf[4 + dlen:4 + dlen + 2])[0]
        crc_calc = crc16_ccitt(buf[:4] + data)
        rest = buf[total:]
        if crc_rx != crc_calc:
            buf = buf[1:]
            continue
        return {
            "addr": addr,
            "cmd":  cmd,
            "data": bytes(data),
            "raw":  bytes(buf[:total]),
        }, rest


# =============================================================================
# ابزارهای تبدیل MAC
# =============================================================================
def mac_str(mac_bytes: bytes) -> str:
    """تبدیل 6 بایت MAC به رشته متنی (XX:XX:XX:XX:XX:XX)."""
    return ":".join(f"{b:02X}" for b in mac_bytes[:6])


def mac_bytes_from_str(mac: str) -> bytes:
    """تبدیل رشته MAC (XX:XX:XX:XX:XX:XX) به 6 بایت."""
    return bytes(int(x, 16) for x in mac.split(":"))


APP_VERSION = "4.4.20"   # ✅ patch: TX PriorityQueue + turnaround-per-frame + poll pause 150ms
APP_NAME    = "RBUS Industrial Node System"
APP_TITLE   = f"{APP_NAME} v{APP_VERSION} — 75 Node Master"
