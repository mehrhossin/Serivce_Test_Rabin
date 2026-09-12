#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : core/node_registry.py
توضیح   : ساختار داده NodeInfo و رجیستری thread-safe تمام نودهای شناخته‌شده
           کلید شناسایی هر نود آدرس MAC آن است (نه آدرس منطقی RBUS)
===============================================================================
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class NodeInfo:
    """
    اطلاعات یک نود الکترونیکی ESP8266.
    MAC آدرس = هویت یکتای نود (تغییر نمی‌کند)
    addr     = آدرس منطقی RBUS (قابل تغییر، اختصاص داده می‌شود)
    """
    mac:        str
    bridge_id:  str
    addr:       int   = 0
    has_code:   bool  = False   # آیا firmware روی نود نصب است؟
    in_ota:     bool  = False   # آیا در حال به‌روزرسانی firmware است؟
    touch_mask: int   = 0       # وضعیت کانال‌های لمسی (bitmask)
    adc:        int   = 0       # مقدار آخرین خواندن ADC
    fw_major:   int   = 0       # نسخه اصلی firmware
    fw_minor:   int   = 0       # نسخه فرعی firmware
    last_seen:  float = field(default_factory=time.time)
    dup_addr:   bool  = False   # آیا آدرس تکراری با نود دیگری دارد؟

    @property
    def status(self) -> str:
        """وضعیت کلی نود به صورت رشته متنی."""
        if self.in_ota:    return "OTA"
        if self.dup_addr:  return "DUP_ADDR"
        if not self.has_code: return "RAW"
        if self.addr == 0: return "NO_ADDR"
        return "ONLINE"

    @property
    def addr_str(self) -> str:
        """آدرس منطقی به صورت hex یا '-' اگر آدرس ندارد."""
        return f"0x{self.addr:02X}" if self.addr else "-"

    @property
    def fw_str(self) -> str:
        """نسخه firmware به صورت متنی."""
        return f"v{self.fw_major}.{self.fw_minor}" if (self.fw_major or self.fw_minor) else ""


class NodeRegistry:
    """
    رجیستری thread-safe تمام نودهای شناخته‌شده در تمام bridge‌ها.
    کلید: MAC address (هویت یکتا و دائمی هر نود)
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, NodeInfo] = {}
        self._lock = threading.Lock()

    def _recompute_dups(self) -> None:
        """
        بررسی و علامت‌گذاری نودهایی که آدرس منطقی تکراری دارند.
        باید داخل lock فراخوانی شود.
        """
        seen: Dict[Tuple[str, int], List[str]] = {}
        for mac, n in self._nodes.items():
            n.dup_addr = False
            if n.addr:
                seen.setdefault((n.bridge_id, n.addr), []).append(mac)
        for _, macs in seen.items():
            if len(macs) > 1:
                for m in macs:
                    self._nodes[m].dup_addr = True

    def upsert_discovery(self, mac: str, bridge_id: str,
                         addr: int, has_code: bool) -> NodeInfo:
        """ثبت یا به‌روزرسانی نود از پاسخ DISCOVERY_RES."""
        with self._lock:
            n = self._nodes.setdefault(mac, NodeInfo(mac=mac, bridge_id=bridge_id))
            n.bridge_id = bridge_id
            n.addr      = addr
            n.has_code  = has_code
            n.last_seen = time.time()
            self._recompute_dups()
            return n

    def upsert_addr_ack(self, mac: str, bridge_id: str, addr: int) -> NodeInfo:
        """ثبت یا به‌روزرسانی نود پس از تأیید آدرس‌دهی (ADDRESS_ACK)."""
        with self._lock:
            n = self._nodes.setdefault(mac, NodeInfo(mac=mac, bridge_id=bridge_id))
            n.bridge_id = bridge_id
            n.addr      = addr
            n.has_code  = True
            n.last_seen = time.time()
            self._recompute_dups()
            return n

    def upsert_status(self, mac: str, bridge_id: str, addr: int,
                      in_ota: bool, touch_mask: int, adc: int,
                      fw_major: int, fw_minor: int) -> NodeInfo:
        """به‌روزرسانی وضعیت نود از پاسخ GET_STATUS."""
        with self._lock:
            n = self._nodes.setdefault(mac, NodeInfo(mac=mac, bridge_id=bridge_id))
            n.bridge_id  = bridge_id
            n.addr       = addr
            n.has_code   = True
            n.in_ota     = in_ota
            n.touch_mask = touch_mask
            n.adc        = adc
            n.fw_major   = fw_major
            n.fw_minor   = fw_minor
            n.last_seen  = time.time()
            self._recompute_dups()
            return n

    def set_ota_flag(self, mac: str, in_ota: bool) -> None:
        """
        تغییر فوری وضعیت OTA نود بدون انتظار برای poll بعدی.
        (در طول OTA، polling متوقف است پس بدون این متد GUI به‌روز نمی‌شد)
        """
        with self._lock:
            n = self._nodes.get(mac)
            if n:
                n.in_ota = in_ota

    def get(self, mac: str) -> Optional[NodeInfo]:
        """دریافت اطلاعات یک نود با MAC آدرس."""
        with self._lock:
            return self._nodes.get(mac)

    def all(self) -> List[NodeInfo]:
        """لیست تمام نودهای ثبت‌شده."""
        with self._lock:
            return list(self._nodes.values())

    def by_bridge(self, bridge_id: str) -> List[NodeInfo]:
        """لیست نودهای یک bridge خاص."""
        with self._lock:
            return [n for n in self._nodes.values() if n.bridge_id == bridge_id]

    def remove(self, mac: str) -> None:
        """حذف یک نود از رجیستری."""
        with self._lock:
            self._nodes.pop(mac, None)
            self._recompute_dups()

    def next_free_addr(self, bridge_id: str) -> int:
        """پیشنهاد اولین آدرس منطقی آزاد برای یک bridge."""
        with self._lock:
            used = {n.addr for n in self._nodes.values()
                    if n.bridge_id == bridge_id and n.addr}
        for a in range(1, 0xFF):
            if a not in used:
                return a
        return 0   # همه آدرس‌ها پر هستند

    def can_assign(self, mac: str, bridge_id: str,
                   new_addr: int) -> Tuple[bool, str]:
        """
        بررسی اینکه آیا آدرس جدید قابل اختصاص است.
        آدرس‌های 0x00 و 0xFF رزرو هستند.
        """
        if new_addr in (0x00, 0xFF):
            return False, "آدرس‌های 0x00 و 0xFF رزرو هستند"
        with self._lock:
            for m, n in self._nodes.items():
                if m != mac and n.bridge_id == bridge_id and n.addr == new_addr:
                    return False, (f"آدرس 0x{new_addr:02X} قبلاً توسط {m} "
                                   f"روی {bridge_id} استفاده شده است")
        return True, "OK"

    def get_by_addr(self, bridge_id: str, addr: int) -> Optional[NodeInfo]:
        """پیدا کردن نود با bridge_id و addr — برای fallback در touch event."""
        with self._lock:
            for n in self._nodes.values():
                if n.bridge_id == bridge_id and n.addr == addr:
                    return n
            return None

    # در core/node_registry.py
    def remove_by_bridge(self, bridge_id: str) -> None:
        """حذف همه نودهای یک bridge از رجیستری."""
        with self._lock:
            to_remove = [
                mac for mac, node in self._nodes.items()
                if node.bridge_id == bridge_id
            ]
            for mac in to_remove:
                del self._nodes[mac]
