#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : core/feedback.py
توضیح   : ردیابی ACK/NACK دستورات ارسال‌شده به نودها
           ACK(0xAA)  → کد "OO"
           NACK(0xBB) → کد "PP"
           بدون پاسخ در زمان مقرر → کد "TIMEOUT"

           v4.1: firmware آدرس نود پاسخ‌دهنده را به عنوان بایت آخر
           در فریم ACK/NACK اضافه می‌کند (قبلاً همیشه ADDR_MASTER بود)
===============================================================================
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.protocol import CMD_NAMES, NACK_ERR_NAMES, FEEDBACK_TIMEOUT_S  # ✅ v4.4.1: NACK_ERR_NAMES import نشده بود → NameError!


@dataclass
class PendingCmd:
    """اطلاعات یک دستور در انتظار پاسخ ACK/NACK."""
    bridge_id: str
    addr:      int
    cmd:       int
    tag:       str
    sent_at:   float
    data:      bytes = b""
    retries:   int = 0


class FeedbackTracker:
    """
    ردیابی دستورات ارسال‌شده و تطبیق آن‌ها با ACK/NACK دریافتی.

    هر دستور با کلید (bridge_id, addr, cmd) ردیابی می‌شود.
    اگر در مدت FEEDBACK_TIMEOUT_S پاسخی نرسد، TIMEOUT گزارش می‌شود.
    """

    def __init__(self, on_feedback: Callable[[Dict[str, Any]], None]) -> None:
        # دیکشنری دستورات در انتظار: کلید=(bridge_id, addr, cmd)، مقدار=لیست دستورات
        self._pending: Dict[Tuple[str, int, int], List[PendingCmd]] = {}
        self._lock = threading.Lock()
        self._on_feedback = on_feedback

    def expect(self, bridge_id: str, addr: int, cmd: int, tag: str = "",
               data: bytes = b"") -> None:
        """ثبت یک دستور جدید در انتظار پاسخ (با data برای ارسال مجدد خودکار)."""
        key = (bridge_id, addr, cmd)
        with self._lock:
            self._pending.setdefault(key, []).append(
                PendingCmd(bridge_id, addr, cmd, tag, time.time(), data, 0)
            )

    def on_ack(self, bridge_id: str, src_addr: int, for_cmd: int) -> bool:
        """پردازش ACK دریافتی از نود."""
        return self._resolve(bridge_id, src_addr, for_cmd, ok=True, err=None)

    def on_nack(self, bridge_id: str, src_addr: int,
                for_cmd: int, err_code: int) -> bool:
        """پردازش NACK دریافتی از نود."""
        return self._resolve(bridge_id, src_addr, for_cmd, ok=False, err=err_code)

    ##############################
    def _resolve(self, bridge_id: str, src_addr: int, for_cmd: int,
             ok: bool, err: Optional[int]) -> bool:
        from core.protocol import ADDR_BROADCAST
        key = (bridge_id, src_addr, for_cmd)
        
        # اول با آدرس واقعی چک کن
        with self._lock:
            lst = self._pending.get(key)
            if lst:
                pc = lst.pop(0)
                if not lst:
                    self._pending.pop(key, None)
            else:
                # اگر پیدا نشد، با broadcast چک کن
                bcast_key = (bridge_id, ADDR_BROADCAST, for_cmd)
                lst2 = self._pending.get(bcast_key)
                if lst2:
                    pc = lst2.pop(0)
                    if not lst2:
                        self._pending.pop(bcast_key, None)
                else:
                    return False
        
        # callback خارج از lock
        self._on_feedback({
            "bridge_id": bridge_id,
            "addr":      src_addr,
            "cmd":       for_cmd,
            "cmd_name":  CMD_NAMES.get(for_cmd, f"0x{for_cmd:02X}"),
            "tag":       pc.tag,
            "ok":        ok,
            "code":      "OO" if ok else "PP",
            "err_code":  err,
            "err_name":  (NACK_ERR_NAMES.get(err, f"0x{err:02X}")
                        if err is not None else ""),
            "latency_ms": (time.time() - pc.sent_at) * 1000.0,
        })
        return True

    ###############################

    def sweep_timeouts(self, max_retries: int = 2,
                       resend: Any = None) -> None:
        """
        ✅ v4.4.1: بررسی دستورات منقضی — «ارسال مجدد خودکار» تا max_retries بار
        (قانون پروژه: سرویس در صورت نبود تأیید، دوباره دستور را می‌فرستد)
        سپس گزارش TIMEOUT. باید به‌صورت دوره‌ای از poll loop فراخوانی شود.
        """
        now = time.time()
        expired: List[Tuple[Tuple[str, int, int], PendingCmd]] = []
        with self._lock:
            for key, lst in list(self._pending.items()):
                keep = []
                for pc in lst:
                    if now - pc.sent_at >= FEEDBACK_TIMEOUT_S:
                        expired.append((key, pc))
                    else:
                        keep.append(pc)
                if keep:
                    self._pending[key] = keep
                else:
                    self._pending.pop(key, None)

        from core.protocol import ADDR_BROADCAST
        for _, pc in expired:
            # ✅ v4.4.2: broadcast پاسخی ندارد (طبق پروتکل) → retry بی‌فایده =
            # اسپم باس. مستقیم TIMEOUT:
            if pc.addr == ADDR_BROADCAST:
                self._on_feedback({
                    "bridge_id": pc.bridge_id, "addr": pc.addr, "cmd": pc.cmd,
                    "cmd_name": CMD_NAMES.get(pc.cmd, f"0x{pc.cmd:02X}"),
                    "tag": pc.tag, "ok": False, "code": "TIMEOUT",
                    "err_code": None, "err_name": "broadcast (بدون پاسخ طبق پروتکل)",
                    "latency_ms": (time.time() - pc.sent_at) * 1000.0,
                })
                continue
            # ── تلاش ارسال مجدد خودکار ──
            if pc.retries < max_retries and resend is not None:
                try:
                    sent_ok = resend(pc.bridge_id, pc.addr, pc.cmd, pc.data)
                except Exception:
                    sent_ok = False
                if sent_ok:
                    pc.retries  += 1
                    pc.sent_at   = time.time()
                    with self._lock:
                        self._pending.setdefault(
                            (pc.bridge_id, pc.addr, pc.cmd), []).append(pc)
                    self._on_feedback({
                        "bridge_id": pc.bridge_id, "addr": pc.addr,
                        "cmd": pc.cmd,
                        "cmd_name": CMD_NAMES.get(pc.cmd, f"0x{pc.cmd:02X}"),
                        "tag": pc.tag, "ok": False,
                        "code": "RETRY",
                        "err_code": pc.retries, "err_name": f"auto-retry #{pc.retries}",
                        "latency_ms": 0.0,
                    })
                    continue
            # ── تلاش‌ها تمام شد → TIMEOUT ──
            self._on_feedback({
                "bridge_id": pc.bridge_id,
                "addr":      pc.addr,
                "cmd":       pc.cmd,
                "cmd_name":  CMD_NAMES.get(pc.cmd, f"0x{pc.cmd:02X}"),
                "tag":       pc.tag,
                "ok":        False,
                "code":      "TIMEOUT",
                "err_code":  None,
                "err_name":  "",
                "latency_ms": (time.time() - pc.sent_at) * 1000.0,
            })
