#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : core/batch_ota.py
نسخه    : 1.0.0
توضیح   : BatchOtaManager — فلش گروهی موازی نودها
           هر bridge همزمان یک نود را فلش می‌کند (تا 5 همزمان)
           وقتی یک نود تمام شد، نود بعدی از صف همان bridge شروع می‌شود

           نصب: این فایل را در پوشه core/ کپی کنید
===============================================================================
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.protocol import OTA_PASSWORD


@dataclass
class BatchNodeStatus:
    """وضعیت فلش یک نود در عملیات گروهی."""
    bridge_id: str
    mac: str
    addr: int
    status: str = "PENDING"       # PENDING / FLASHING / DONE / FAILED / SKIPPED
    progress: int = 0
    message: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""


class BatchOtaManager:
    """
    فلش گروهی موازی: هر bridge یک نود در لحظه، bridge‌های مختلف همزمان.

    Thread-safety:
    - _lock از تغییر همزمان _nodes و counter‌ها جلوگیری می‌کند
    - on_ota_done / on_ota_error / on_ota_progress از thread‌های
      BridgeWorker فراخوانی می‌شوند — داخل lock
    """

    def __init__(
        self,
        service,
        on_event: Callable[[str, Dict[str, Any]], None],
    ) -> None:
        self.service = service
        self._on_event = on_event

        self._firmware: bytes = b""
        self._password: bytes = OTA_PASSWORD

        # صف هر bridge: bridge_id -> deque of (mac, addr)
        self._queues: Dict[str, deque] = {}

        # وضعیت هر نود: (bridge_id, mac) -> BatchNodeStatus
        self._nodes: Dict[Tuple[str, str], BatchNodeStatus] = {}

        # وضعیت کلی
        self._active: bool = False
        self._total: int = 0
        self._done_count: int = 0
        self._failed_count: int = 0
        self._started_at: float = 0.0
        self._finished_at: float = 0.0

        self._lock = threading.Lock()

    # =========================================================================
    # خواص
    # =========================================================================

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def total(self) -> int:
        return self._total

    @property
    def done_count(self) -> int:
        return self._done_count

    @property
    def failed_count(self) -> int:
        return self._failed_count

    @property
    def remaining(self) -> int:
        return self._total - self._done_count - self._failed_count

    @property
    def percent(self) -> int:
        if self._total == 0:
            return 0
        return int((self._done_count + self._failed_count) / self._total * 100)

    @property
    def elapsed(self) -> float:
        if self._started_at == 0:
            return 0.0
        end = self._finished_at if self._finished_at else time.time()
        return end - self._started_at

    @property
    def eta(self) -> float:
        """تخمین زمان باقی‌مانده (ثانیه)."""
        finished = self._done_count + self._failed_count
        if finished == 0 or not self._active:
            return 0.0
        avg = self.elapsed / finished
        return avg * self.remaining

    # =========================================================================
    # API
    # =========================================================================

    def set_firmware(self, firmware: bytes) -> None:
        self._firmware = firmware

    def set_password(self, password: bytes) -> None:
        self._password = password

    def get_node(self, bridge_id: str, mac: str) -> Optional[BatchNodeStatus]:
        return self._nodes.get((bridge_id, mac))

    def get_all_status(self) -> List[BatchNodeStatus]:
        with self._lock:
            return list(self._nodes.values())

    # =========================================================================
    # شروع / توقف
    # =========================================================================

    def start(
        self,
        firmware: bytes,
        targets: List[Dict[str, Any]],
        password: bytes = OTA_PASSWORD,
    ) -> Tuple[bool, str]:
        """
        شروع فلش گروهی.
        targets: [{"bridge_id": str, "mac": str, "addr": int}, ...]
        """
        if self._active:
            return False, "قبلاً در حال اجرا است"
        if not firmware:
            return False, "Firmware انتخاب نشده"
        if len(firmware) < 1024:
            return False, "Firmware خیلی کوچک (<1KB)"
        if firmware[0] != 0xE9:
            return False, "Firmware نامعتبر (magic ≠ 0xE9)"
        if not targets:
            return False, "نودی انتخاب نشده"

        # بررسی bridge‌ها
        connected = set()
        for t in targets:
            w = self.service.bridges.get(t["bridge_id"])
            if w and w.link.connected:
                connected.add(t["bridge_id"])
        if not connected:
            return False, "هیچ bridge متصلی یافت نشد"

        self._firmware = firmware
        self._password = password
        self._active = True
        self._done_count = 0
        self._failed_count = 0
        self._total = len(targets)
        self._started_at = time.time()
        self._finished_at = 0.0
        self._queues.clear()
        self._nodes.clear()

        # پر کردن صف‌ها
        for t in targets:
            bid = t["bridge_id"]
            mac = t["mac"]
            addr = t["addr"]
            self._nodes[(bid, mac)] = BatchNodeStatus(
                bridge_id=bid, mac=mac, addr=addr
            )
            if bid not in self._queues:
                self._queues[bid] = deque()
            self._queues[bid].append((mac, addr))

        # شروع اولین نود روی هر bridge
        started = 0
        for bid in list(self._queues.keys()):
            if self._kick_bridge(bid):
                started += 1

        if started == 0:
            self._active = False
            return False, "شروع روی هیچ bridge‌ای ممکن نبود"

        self._emit("batch_started", {
            "total": self._total,
            "bridges": len(self._queues),
        })
        return True, f"{self._total} نود روی {len(self._queues)} bridge"

    def stop(self) -> None:
        """توقف کامل."""
        if not self._active:
            return
        self._active = False
        self._finished_at = time.time()

        # لغو OTA روی همه bridge‌ها
        for bid in list(self._queues.keys()):
            w = self.service.bridges.get(bid)
            if w and w.is_ota_active():
                w.abort_ota()

        with self._lock:
            for ns in self._nodes.values():
                if ns.status in ("PENDING", "FLASHING"):
                    ns.status = "SKIPPED"
                    ns.message = "لغو شد"
                    ns.finished_at = time.time()

        self._emit("batch_stopped", {
            "done": self._done_count,
            "failed": self._failed_count,
            "total": self._total,
            "elapsed": self.elapsed,
        })

    # =========================================================================
    # فراخوانی از MainWindow
    # =========================================================================

    def on_ota_done(self, bridge_id: str, mac: str) -> None:
        """وقتی OTA یک نود موفق شد."""
        with self._lock:
            ns = self._nodes.get((bridge_id, mac))
            if ns and ns.status == "FLASHING":
                ns.status = "DONE"
                ns.progress = 100
                ns.finished_at = time.time()
                ns.message = "موفق"
                self._done_count += 1

        self._emit("batch_node_done", {
            "bridge_id": bridge_id, "mac": mac,
            "done": self._done_count, "failed": self._failed_count,
            "total": self._total,
        })

        if self._active:
            self._kick_bridge(bridge_id)
        else:
            self._check_finished()

    def on_ota_error(self, bridge_id: str, mac: str, reason: str) -> None:
        """وقتی OTA یک نود خطا داد."""
        with self._lock:
            ns = self._nodes.get((bridge_id, mac))
            if ns and ns.status == "FLASHING":
                ns.status = "FAILED"
                ns.error = reason
                ns.finished_at = time.time()
                ns.message = f"خطا: {reason}"
                self._failed_count += 1

        self._emit("batch_node_error", {
            "bridge_id": bridge_id, "mac": mac, "reason": reason,
            "done": self._done_count, "failed": self._failed_count,
            "total": self._total,
        })

        if self._active:
            self._kick_bridge(bridge_id)
        else:
            self._check_finished()

    def on_ota_progress(self, bridge_id: str, mac: str,
                         status: Dict[str, Any]) -> None:
        """وقتی پیشرفت OTA گزارش شد."""
        with self._lock:
            ns = self._nodes.get((bridge_id, mac))
            if ns and ns.status in ("PENDING", "FLASHING"):
                ns.status = "FLASHING"
                ns.progress = status.get("progress", 0)
                ns.message = status.get("message", "")
                if ns.started_at == 0:
                    ns.started_at = time.time()

    # =========================================================================
    # منطق داخلی
    # =========================================================================

    def _kick_bridge(self, bridge_id: str) -> bool:
        """شروع نود بعدی روی یک bridge."""
        q = self._queues.get(bridge_id)
        if not q or len(q) == 0:
            self._check_finished()
            return False

        w = self.service.bridges.get(bridge_id)
        if not w or not w.link.connected:
            # bridge قطع — نودهایش failed
            while q:
                mac, addr = q.popleft()
                with self._lock:
                    ns = self._nodes.get((bridge_id, mac))
                    if ns and ns.status == "PENDING":
                        ns.status = "FAILED"
                        ns.error = "bridge قطع"
                        ns.finished_at = time.time()
                        self._failed_count += 1
            self._check_finished()
            return False

        if w.is_ota_active():
            return False

        mac, addr = q.popleft()

        with self._lock:
            ns = self._nodes.get((bridge_id, mac))
            if ns:
                ns.status = "FLASHING"
                ns.started_at = time.time()
                ns.progress = 0
                ns.message = "در حال فلش..."

        self._emit("batch_node_start", {
            "bridge_id": bridge_id, "mac": mac, "addr": addr,
            "remaining": len(q),
        })

        ok, reason = self.service.start_ota(
            bridge_id, mac, addr, self._firmware, self._password
        )

        if not ok:
            with self._lock:
                ns = self._nodes.get((bridge_id, mac))
                if ns:
                    ns.status = "FAILED"
                    ns.error = reason
                    ns.finished_at = time.time()
                    self._failed_count += 1

            self._emit("batch_node_error", {
                "bridge_id": bridge_id, "mac": mac, "reason": reason,
                "done": self._done_count, "failed": self._failed_count,
                "total": self._total,
            })

            if q:
                return self._kick_bridge(bridge_id)
            else:
                self._check_finished()
                return False

        return True

    def _check_finished(self) -> None:
        """بررسی اتمام کل عملیات."""
        with self._lock:
            if not self._active:
                return
            empty = all(len(q) == 0 for q in self._queues.values())
            idle = all(
                not self.service.bridges[bid].is_ota_active()
                for bid in self._queues
                if bid in self.service.bridges
            )
            if empty and idle:
                self._active = False
                self._finished_at = time.time()
                self._emit("batch_completed", {
                    "done": self._done_count,
                    "failed": self._failed_count,
                    "total": self._total,
                    "elapsed": self.elapsed,
                })

    def _emit(self, kind: str, data: Dict[str, Any]) -> None:
        """ارسال رویداد به GUI."""
        self._on_event(kind, data)
