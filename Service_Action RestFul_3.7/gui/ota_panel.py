#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : gui/ota_panel.py
توضیح   : پنل OTA Flash — به‌روزرسانی firmware نودهای ESP8266
           فقط یک session OTA در هر لحظه روی هر bridge مجاز است
           (RS485 half-duplex است — دو flash همزمان روی یک bus تداخل ایجاد می‌کند)
           در طول OTA، دستورات عادی آن bridge توسط service.py نگه داشته می‌شوند.
===============================================================================
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QVBoxLayout, QWidget,
)

from service import RBusService
from gui.log_panel import LogPanel
from gui.load_form import LoadForm
from core.protocol import OTA_CHUNK_SIZE


class OtaPanel(QWidget):
    """
    پنل مدیریت OTA Flash.
    نود هدف از تب Discovery انتخاب می‌شود.
    """

    def __init__(self, service: RBusService, load_form: LoadForm,
                 log: LogPanel, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.service   = service
        self.load_form = load_form
        self.log       = log

        # وضعیت جاری session
        self._fw_data:    bytes          = b""
        self._fw_path:    str            = ""
        self._sel_mac:    Optional[str]  = None
        self._sel_bridge: Optional[str]  = None
        self._sel_addr:   int            = 0

        self._build_ui()

        # polling وضعیت OTA هر 250ms
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_timer.start(250)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ---- انتخاب نود هدف -------------------------------------------------
        tgt_box = QGroupBox(
            "🎯 نود هدف (در تب Discovery انتخاب کنید، سپس 'استفاده از نود انتخاب‌شده')"
        )
        tg = QGridLayout(tgt_box)

        tg.addWidget(QLabel("Bridge:"), 0, 0)
        self.lbl_bridge = QLabel("—")
        self.lbl_bridge.setStyleSheet("color:#00E5FF; font-weight:bold;")
        tg.addWidget(self.lbl_bridge, 0, 1)

        tg.addWidget(QLabel("MAC:"), 0, 2)
        self.lbl_mac = QLabel("—")
        self.lbl_mac.setStyleSheet("color:#FFD700; font-weight:bold;")
        tg.addWidget(self.lbl_mac, 0, 3)

        tg.addWidget(QLabel("آدرس:"), 0, 4)
        self.lbl_addr = QLabel("—")
        tg.addWidget(self.lbl_addr, 0, 5)

        btn_use = QPushButton("↩ استفاده از نود انتخاب‌شده")
        btn_use.clicked.connect(self._use_selected)
        tg.addWidget(btn_use, 0, 6)
        root.addWidget(tgt_box)

        # ---- انتخاب فایل firmware -------------------------------------------
        fw_box = QGroupBox(
            f"📂 فایل Firmware (.bin)  —  "
            f"chunk size={OTA_CHUNK_SIZE}B، magic byte=0xE9 الزامی است"
        )
        fw_l = QHBoxLayout(fw_box)
        self.lbl_fw = QLabel("فایلی انتخاب نشده")
        self.lbl_fw.setStyleSheet("color:#888;")
        fw_l.addWidget(self.lbl_fw, stretch=1)
        btn_browse = QPushButton("انتخاب فایل .bin...")
        btn_browse.clicked.connect(self._browse_fw)
        fw_l.addWidget(btn_browse)
        root.addWidget(fw_box)

        # ---- رمز عبور OTA ---------------------------------------------------
        pw_box = QGroupBox("🔑 رمز عبور OTA (hex، 4 بایت)")
        pw_l = QHBoxLayout(pw_box)
        pw_l.addWidget(QLabel("رمز:"))
        self.ed_pw = QLineEdit("DE AD BE EF")
        self.ed_pw.setEchoMode(QLineEdit.Password)
        pw_l.addWidget(self.ed_pw, stretch=1)
        btn_show = QPushButton("👁")
        btn_show.setCheckable(True)
        btn_show.setFixedWidth(32)
        btn_show.toggled.connect(
            lambda on: self.ed_pw.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password
            )
        )
        pw_l.addWidget(btn_show)
        root.addWidget(pw_box)

        # ---- دکمه‌های عملیات ------------------------------------------------
        act_box = QGroupBox("⚡ عملیات")
        act_l = QHBoxLayout(act_box)

        self.btn_flash = QPushButton("🚀 Flash نود")
        self.btn_flash.setObjectName("success")
        self.btn_flash.clicked.connect(self._do_flash)
        act_l.addWidget(self.btn_flash)

        self.btn_abort = QPushButton("⛔ لغو OTA روی این Bridge")
        self.btn_abort.setObjectName("danger")
        self.btn_abort.clicked.connect(self._do_abort)
        act_l.addWidget(self.btn_abort)
        act_l.addStretch()
        root.addWidget(act_box)

        # ---- نوار پیشرفت ----------------------------------------------------
        prog_box = QGroupBox("📊 وضعیت Session جاری")
        pg = QVBoxLayout(prog_box)
        self.lbl_phase = QLabel("بیکار")
        pg.addWidget(self.lbl_phase)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        pg.addWidget(self.bar)
        root.addWidget(prog_box)

        # ---- لاگ OTA --------------------------------------------------------
        log_box = QGroupBox("📝 لاگ OTA")
        log_l = QVBoxLayout(log_box)
        self.ota_log = QPlainTextEdit()
        self.ota_log.setReadOnly(True)
        self.ota_log.setMaximumBlockCount(3000)
        log_l.addWidget(self.ota_log)
        root.addWidget(log_box)

    # ---- کمک‌کننده‌ها --------------------------------------------------------

    def _log(self, msg: str) -> None:
        """اضافه کردن پیام با timestamp به لاگ OTA."""
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.ota_log.appendPlainText(f"[{ts}] {msg}")

    def _use_selected(self) -> None:
        """دریافت اطلاعات نود انتخاب‌شده از تب Discovery."""
        n = self.load_form.selected_node()
        if not n:
            QMessageBox.information(
                self, "انتخابی نیست",
                "ابتدا یک نود در تب Discovery انتخاب کنید.",
            )
            return
        self._sel_mac    = n.mac
        self._sel_bridge = n.bridge_id
        self._sel_addr   = n.addr
        self.lbl_bridge.setText(n.bridge_id)
        self.lbl_mac.setText(n.mac)
        self.lbl_addr.setText(n.addr_str if n.addr else "⚠️ آدرس ندارد")

    def _browse_fw(self) -> None:
        """انتخاب فایل firmware و بررسی اولیه آن."""
        path, _ = QFileDialog.getOpenFileName(
            self, "انتخاب Firmware", "",
            "Binary Files (*.bin);;All Files (*)",
        )
        if not path:
            return
        data, reason = self.service.load_firmware_file(path)
        if data is None:
            QMessageBox.warning(self, "Firmware نامعتبر", reason)
            return
        self._fw_path = path
        self._fw_data = data
        self.lbl_fw.setText(
            f"{os.path.basename(path)}  [{len(data)/1024:.1f} KB]"
        )
        self.lbl_fw.setStyleSheet("color:#00E5FF;")
        self._log(f"Firmware بارگذاری شد: {path} ({len(data)} بایت)")


    def _parse_password(self) -> Optional[bytes]:
        """تجزیه رمز عبور OTA از فیلد متنی (4 بایت hex)."""
        try:
            parts = self.ed_pw.text().strip().split()
            pw = bytes(int(x, 16) for x in parts)
            if len(pw) != 4:
                raise ValueError("دقیقاً 4 بایت لازم است")
            return pw
        except Exception as e:
            QMessageBox.warning(
                self, "رمز عبور نامعتبر",
                f"دقیقاً 4 بایت hex وارد کنید (مثال: DE AD BE EF):\n{e}",
            )
            return None

    def _do_flash(self) -> None:
        """شروع عملیات OTA پس از تأیید کاربر."""
        if not self._sel_mac or not self._sel_bridge:
            QMessageBox.warning(self, "نودی انتخاب نشده",
                                 "ابتدا 'استفاده از نود انتخاب‌شده' را بزنید.")
            return
        if not self._fw_data:
            QMessageBox.warning(self, "Firmware انتخاب نشده",
                                 "ابتدا یک فایل .bin انتخاب کنید.")
            return
        if self._sel_addr == 0:
            QMessageBox.warning(self, "آدرس ندارد",
                                 "نود باید قبل از OTA آدرس منطقی داشته باشد.")
            return
        pw = self._parse_password()
        if pw is None:
            return

        confirm = QMessageBox.question(
            self, "تأیید Flash",
            f"آیا مطمئن هستید؟\n\n"
            f"Firmware: {len(self._fw_data)/1024:.1f} KB\n"
            f"نود: {self._sel_mac} (0x{self._sel_addr:02X})\n"
            f"Bridge: {self._sel_bridge}\n\n"
            f"⚠️ در طول OTA تمام ترافیک عادی {self._sel_bridge} متوقف می‌شود.\n"
            f"نود پس از flash موفق ریبوت می‌کند.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        ok, reason = self.service.start_ota(
            self._sel_bridge, self._sel_mac,
            self._sel_addr, self._fw_data, pw,
        )
        if not ok:
            QMessageBox.critical(self, "خطای شروع OTA", reason)
            return

        self._log(
            f"🚀 OTA شروع شد → {self._sel_mac} "
            f"(0x{self._sel_addr:02X}) روی {self._sel_bridge}"
        )
        self.log.log(
            f"🚀 OTA شروع شد → {self._sel_mac} "
            f"(0x{self._sel_addr:02X}) روی {self._sel_bridge}"
        )
        self.bar.setValue(0)
        self.lbl_phase.setText("ENTER_BOOT...")

    def _do_abort(self) -> None:
        """درخواست لغو OTA جاری روی bridge انتخاب‌شده."""
        if not self._sel_bridge:
            QMessageBox.information(self, "Bridge انتخاب نشده",
                                     "ابتدا یک نود انتخاب کنید.")
            return
        self.service.abort_ota(self._sel_bridge)
        self._log(f"⛔ درخواست لغو OTA روی {self._sel_bridge}")

    def _poll_status(self) -> None:
        """به‌روزرسانی نوار پیشرفت از وضعیت جاری OTA (هر 250ms)."""
        if not self._sel_bridge:
            return
        snap = self.service.get_ota_status(self._sel_bridge)
        if snap is None:
            return
        self.lbl_phase.setText(
            f"{snap.get('phase', '?')} — {snap.get('message', '')}"
        )
        self.bar.setValue(snap.get("progress", 0))

    # ---- رویدادهای push از MainWindow ---------------------------------------

    def on_ota_progress(self, data: Dict[str, Any]) -> None:
        """دریافت رویداد پیشرفت OTA از service."""
        snap = data.get("status", {})
        self._log(
            f"[{data.get('bridge_id')}] {data.get('mac')} | "
            f"{snap.get('phase')} | {snap.get('message')}"
        )

    def on_ota_done(self, data: Dict[str, Any]) -> None:
        """دریافت رویداد اتمام موفق OTA."""
        msg = f"✅ OTA کامل شد → {data.get('mac')} روی {data.get('bridge_id')}"
        self._log(msg)
        self.log.log(msg)
        self.bar.setValue(100)
        self.lbl_phase.setText("DONE ✅")

    def on_ota_error(self, data: Dict[str, Any]) -> None:
        """دریافت رویداد خطا یا لغو OTA."""
        msg = (
            f"❌ خطای OTA → {data.get('mac')} "
            f"روی {data.get('bridge_id')}: {data.get('reason', '?')}"
        )
        self._log(msg)
        self.log.log(msg)
        self.lbl_phase.setText(f"ERROR ❌ — {data.get('reason', '?')}")
