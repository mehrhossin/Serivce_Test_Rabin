#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : gui/log_panel.py
توضیح   : پنل لاگ — نمایش رویدادهای ACK/NACK/TIMEOUT و تشخیصی
           ACK  → کد "OO"  (✅)
           NACK → کد "PP"  (❌)
           بدون پاسخ → "TIMEOUT" (⏱️)
===============================================================================
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from PyQt5.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)


class LogPanel(QWidget):
    """
    پنل نمایش لاگ رویدادها.
    حداکثر 5000 خط نگه می‌دارد تا مصرف حافظه کنترل شود.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)

        # ---- نوار بالایی: عنوان + دکمه پاک کردن ---------------------------
        top = QHBoxLayout()
        top.addWidget(QLabel(
            "لاگ رویدادها و تشخیص (ACK=OO / NACK=PP / TIMEOUT)"
        ))
        top.addStretch()
        btn_clear = QPushButton("پاک کردن")
        btn_clear.clicked.connect(self.clear)
        top.addWidget(btn_clear)
        v.addLayout(top)

        # ---- ناحیه متن لاگ -------------------------------------------------
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        v.addWidget(self.text)

    def clear(self) -> None:
        """پاک کردن تمام لاگ‌ها."""
        self.text.clear()

    def log(self, msg: str) -> None:
        """اضافه کردن یک پیام با timestamp به لاگ."""
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.text.appendPlainText(f"[{ts}] {msg}")

    def log_feedback(self, r: Dict[str, Any]) -> None:
        """نمایش نتیجه ACK/NACK/TIMEOUT یک دستور."""
        if r["code"] == "OO":
            icon = "✅"
        elif r["code"] == "TIMEOUT":
            icon = "⏱️"
        else:
            icon = "❌"
        extra = f" err={r['err_name']}" if r.get("err_name") else ""
        self.log(
            f"{icon} {r['code']} | {r['bridge_id']} | "
            f"Addr 0x{r['addr']:02X} | {r['cmd_name']}{extra} | "
            f"{r['latency_ms']:.0f}ms | tag={r['tag']}"
        )
