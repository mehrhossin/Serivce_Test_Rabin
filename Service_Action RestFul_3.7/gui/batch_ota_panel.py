#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : gui/batch_ota_panel.py
نسخه    : 1.0.1
توضیح   : پنل فلش گروهی — فلش موازی نودها از چندین bridge

           نصب: این فایل را در پوشه gui/ کپی کنید

           سپس در main_window.py تغییرات لازم را اعمال کنید:
           1. import: from gui.batch_ota_panel import BatchOtaPanel
           2. ایجاد: self.batch_panel = BatchOtaPanel(self.service, self.log_panel)
           3. تب: tabs.addTab(self.batch_panel, "🚀 فلش گروهی")
           4. رویدادها: در _handle_event برای ota_done/error/progress
              اضافه کنید: self.batch_panel.on_service_ota_*(data)
===============================================================================
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QCheckBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core.batch_ota import BatchOtaManager
from core.protocol import OTA_PASSWORD
from service import RBusService

# رنگ‌ها
_C_PENDING  = "#7D8590"
_C_FLASHING = "#1A6FFF"
_C_DONE     = "#3FB950"
_C_FAILED   = "#F85149"


# =============================================================================
# سیگنال‌های thread-safe
# =============================================================================

class _Sig(QObject):
    log_msg       = pyqtSignal(str)
    overall       = pyqtSignal(int, int, int, int)     # done, failed, total, pct
    batch_started = pyqtSignal(int, int)                # total, bridges
    batch_done    = pyqtSignal(int, int, int, float)    # done, failed, total, elapsed
    batch_stopped = pyqtSignal(int, int, int)           # done, failed, total
    node_start    = pyqtSignal(str, str, int)           # bid, mac, addr
    node_done     = pyqtSignal(str, str)                # bid, mac
    node_error    = pyqtSignal(str, str, str)           # bid, mac, reason


# =============================================================================
# پنل
# =============================================================================

class BatchOtaPanel(QWidget):

    def __init__(
        self,
        service: RBusService,
        log_panel,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.service   = service
        self.log_panel = log_panel

        # Manager
        self.manager = BatchOtaManager(service, self._on_event)

        # سیگنال‌ها — نام متدها باید دقیقاً مطابقت داشته باشد
        self._sig = _Sig()
        self._sig.log_msg.connect(self._slot_log)
        self._sig.overall.connect(self._slot_overall)
        self._sig.batch_started.connect(self._slot_started)
        self._sig.batch_done.connect(self._slot_completed)
        self._sig.batch_stopped.connect(self._slot_stopped)
        self._sig.node_start.connect(self._slot_node_start)
        self._sig.node_done.connect(self._slot_node_done)
        self._sig.node_error.connect(self._slot_node_error)

        # Firmware
        self._fw_data: bytes = b""
        self._fw_path: str   = ""

        # ردیف‌های جدول: [{bridge_id, mac, addr, row}, ...]
        self._rows: List[Dict[str, Any]] = []

        # تایمرها
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._tick_poll)
        self._poll_timer.start(400)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)

        # ساخت UI
        self._build_ui()

    # =========================================================================
    # ساخت UI
    # =========================================================================

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)

        # ── Firmware ─────────────────────────────────────────────────────────
        fw_box = QGroupBox("📂 Firmware")
        fw_l   = QHBoxLayout(fw_box)
        self.lbl_fw = QLabel("فایلی انتخاب نشده")
        self.lbl_fw.setStyleSheet("color:#888;")
        fw_l.addWidget(self.lbl_fw, stretch=1)
        btn_fw = QPushButton("انتخاب .bin ...")
        btn_fw.clicked.connect(self._browse_fw)
        fw_l.addWidget(btn_fw)
        root.addWidget(fw_box)

        # ── کنترل ────────────────────────────────────────────────────────────
        ctrl = QGroupBox("⚡ کنترل عملیات")
        cg   = QGridLayout(ctrl)

        cg.addWidget(QLabel("رمز OTA:"), 0, 0)
        self.ed_pw = QLineEdit("DE AD BE EF")
        self.ed_pw.setFixedWidth(140)
        cg.addWidget(self.ed_pw, 0, 1)

        self.btn_start = QPushButton("🚀 شروع فلش گروهی")
        self.btn_start.setObjectName("success")
        self.btn_start.clicked.connect(self._do_start)
        cg.addWidget(self.btn_start, 0, 2)

        self.btn_stop = QPushButton("⛔ توقف")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._do_stop)
        cg.addWidget(self.btn_stop, 0, 3)

        self.btn_reset = QPushButton("🔄 بارگذاری مجدد")
        self.btn_reset.clicked.connect(self._do_reset)
        cg.addWidget(self.btn_reset, 0, 4)

        root.addWidget(ctrl)

        # ── آمار ─────────────────────────────────────────────────────────────
        st = QGroupBox("📊 وضعیت کلی")
        sg = QGridLayout(st)

        sg.addWidget(QLabel("کل:"), 0, 0)
        self.lbl_total = QLabel("0")
        self.lbl_total.setStyleSheet("font-weight:bold; font-size:15px;")
        sg.addWidget(self.lbl_total, 0, 1)

        sg.addWidget(QLabel("✅:"), 0, 2)
        self.lbl_done = QLabel("0")
        self.lbl_done.setStyleSheet(
            f"font-weight:bold; font-size:15px; color:{_C_DONE};"
        )
        sg.addWidget(self.lbl_done, 0, 3)

        sg.addWidget(QLabel("❌:"), 0, 4)
        self.lbl_fail = QLabel("0")
        self.lbl_fail.setStyleSheet(
            f"font-weight:bold; font-size:15px; color:{_C_FAILED};"
        )
        sg.addWidget(self.lbl_fail, 0, 5)

        sg.addWidget(QLabel("⏱️:"), 0, 6)
        self.lbl_time = QLabel("00:00")
        self.lbl_time.setStyleSheet("font-weight:bold;")
        sg.addWidget(self.lbl_time, 0, 7)

        sg.addWidget(QLabel("⏳:"), 0, 8)
        self.lbl_eta = QLabel("—")
        self.lbl_eta.setStyleSheet("font-weight:bold; color:#D29922;")
        sg.addWidget(self.lbl_eta, 0, 9)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFormat("%v%")
        self.bar.setFixedHeight(26)
        sg.addWidget(self.bar, 1, 0, 1, 10)

        root.addWidget(st)

        # ── جدول ─────────────────────────────────────────────────────────────
        tbl_box = QGroupBox("📋 نودهای هدف")
        tl = QVBoxLayout(tbl_box)

        # ابزار
        tools = QHBoxLayout()
        b_all = QPushButton("انتخاب همه")
        b_all.clicked.connect(self._sel_all)
        tools.addWidget(b_all)
        b_none = QPushButton("لغو انتخاب")
        b_none.clicked.connect(self._sel_none)
        tools.addWidget(b_none)
        b_load = QPushButton("🔄 بارگذاری نودها")
        b_load.clicked.connect(self._load_nodes)
        tools.addWidget(b_load)
        tools.addStretch()
        self.lbl_sel = QLabel("0 انتخاب")
        self.lbl_sel.setStyleSheet("color:#888;")
        tools.addWidget(self.lbl_sel)
        tl.addLayout(tools)

        # جدول
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "انتخاب", "Bridge", "MAC", "آدرس", "وضعیت", "پیشرفت"
        ])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.table.setColumnWidth(0, 55)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        tl.addWidget(self.table)

        root.addWidget(tbl_box, stretch=3)

        # ── لاگ ──────────────────────────────────────────────────────────────
        lg_box = QGroupBox("📝 لاگ عملیات گروهی")
        ll = QVBoxLayout(lg_box)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(2000)
        ll.addWidget(self.log_text)
        root.addWidget(lg_box, stretch=1)

    # =========================================================================
    # Firmware
    # =========================================================================

    def _browse_fw(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Firmware", "", "Binary (*.bin);;All (*)"
        )
        if not path:
            return
        data, reason = self.service.load_firmware_file(path)
        if data is None:
            QMessageBox.warning(self, "Firmware نامعتبر", reason)
            return
        self._fw_path = path
        self._fw_data = data
        self.manager.set_firmware(data)
        self.lbl_fw.setText(
            f"{os.path.basename(path)}  [{len(data) / 1024:.1f} KB]"
        )
        self.lbl_fw.setStyleSheet("color:#00E5FF; font-weight:bold;")
        self._log(f"Firmware: {os.path.basename(path)}")

    def _parse_pw(self) -> Optional[bytes]:
        try:
            parts = self.ed_pw.text().strip().split()
            pw = bytes(int(x, 16) for x in parts)
            if len(pw) != 4:
                raise ValueError("دقیقاً 4 بایت hex لازم است")
            return pw
        except Exception as e:
            QMessageBox.warning(self, "رمز نامعتبر", str(e))
            return None

    # =========================================================================
    # جدول نودها
    # =========================================================================

    def _load_nodes(self) -> None:
        """بارگذاری نودهای آنلاین."""
        # پاک‌سازی امن — blockSignals جلوگیری از crash
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
        finally:
            self.table.blockSignals(False)

        self._rows.clear()

        nodes = self.service.registry.all()
        nodes.sort(key=lambda n: (n.bridge_id, n.addr))

        r = 0
        for n in nodes:
            if n.addr == 0:
                continue
            w = self.service.bridges.get(n.bridge_id)
            if not w or not w.link.connected:
                continue

            self.table.insertRow(r)

            # Checkbox — بدون اتصال signal (جلوگیری از crash)
            chk = QCheckBox()
            chk.setChecked(True)
            self.table.setCellWidget(r, 0, chk)

            # Bridge
            i1 = QTableWidgetItem(n.bridge_id)
            i1.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 1, i1)

            # MAC
            self.table.setItem(r, 2, QTableWidgetItem(n.mac))

            # آدرس
            i3 = QTableWidgetItem(f"0x{n.addr:02X}")
            i3.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 3, i3)

            # وضعیت
            i4 = QTableWidgetItem("⏳ در صف")
            i4.setForeground(QBrush(QColor(_C_PENDING)))
            i4.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 4, i4)

            # پیشرفت
            i5 = QTableWidgetItem("—")
            i5.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 5, i5)

            self._rows.append({
                "bridge_id": n.bridge_id,
                "mac":       n.mac,
                "addr":      n.addr,
                "row":       r,
            })
            r += 1

        self._update_sel_count()
        self._log(f"{len(self._rows)} نود بارگذاری شد")

    def _sel_all(self) -> None:
        for info in self._rows:
            w = self.table.cellWidget(info["row"], 0)
            if w and isinstance(w, QCheckBox):
                w.setChecked(True)
        self._update_sel_count()

    def _sel_none(self) -> None:
        for info in self._rows:
            w = self.table.cellWidget(info["row"], 0)
            if w and isinstance(w, QCheckBox):
                w.setChecked(False)
        self._update_sel_count()

    def _get_selected(self) -> List[Dict[str, Any]]:
        """نودهای انتخاب‌شده."""
        out = []
        for info in self._rows:
            w = self.table.cellWidget(info["row"], 0)
            if w and isinstance(w, QCheckBox) and w.isChecked():
                out.append({
                    "bridge_id": info["bridge_id"],
                    "mac":       info["mac"],
                    "addr":      info["addr"],
                })
        return out

    def _update_sel_count(self) -> None:
        n = len(self._get_selected())
        self.lbl_sel.setText(f"{n} انتخاب")
        self.lbl_total.setText(str(n))

    # =========================================================================
    # شروع / توقف / ریست
    # =========================================================================

    def _do_start(self) -> None:
        if not self._fw_data:
            QMessageBox.warning(self, "Firmware", "فایل firmware انتخاب نشده")
            return

        targets = self._get_selected()
        if not targets:
            QMessageBox.warning(self, "نود", "نودی انتخاب نشده")
            return

        pw = self._parse_pw()
        if pw is None:
            return

        bridges = set(t["bridge_id"] for t in targets)
        ok = QMessageBox.question(
            self, "تأیید فلش گروهی",
            f"فلش {len(targets)} نود روی {len(bridges)} bridge\n\n"
            f"Firmware: {os.path.basename(self._fw_path)}\n"
            f"({len(self._fw_data) / 1024:.1f} KB)\n\n"
            f"ادامه؟",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ok != QMessageBox.Yes:
            return

        self.manager.set_password(pw)
        success, msg = self.manager.start(self._fw_data, targets, pw)
        if not success:
            QMessageBox.critical(self, "خطا", msg)
            return

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_reset.setEnabled(False)
        self._log(f"🚀 {msg}")

    def _do_stop(self) -> None:
        ok = QMessageBox.warning(
            self, "توقف",
            "عملیات متوقف می‌شود.\nادامه؟",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ok != QMessageBox.Yes:
            return
        self.manager.stop()
        self._restore_buttons()
        self._log("⛔ متوقف شد")

    def _do_reset(self) -> None:
        self._load_nodes()
        self.bar.setValue(0)
        self.lbl_done.setText("0")
        self.lbl_fail.setText("0")
        self.lbl_time.setText("00:00")
        self.lbl_eta.setText("—")
        self._restore_buttons()
        self._log("🔄 ریست")

    def _restore_buttons(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_reset.setEnabled(True)

    # =========================================================================
    # رویدادها از BatchOtaManager (thread BridgeWorker) → signal → GUI slot
    # =========================================================================

    def _on_event(self, kind: str, data: Dict[str, Any]) -> None:
        """
        Callback از BatchOtaManager — از thread دیگری فراخوانی می‌شود.
        فقط سیگنال emit می‌کنیم — هیچ عملیات GUI مستقیم.
        """
        try:
            if kind == "batch_started":
                self._sig.batch_started.emit(
                    data["total"], data["bridges"]
                )

            elif kind == "batch_node_start":
                self._sig.node_start.emit(
                    data["bridge_id"], data["mac"], data["addr"]
                )

            elif kind == "batch_node_done":
                self._sig.node_done.emit(
                    data["bridge_id"], data["mac"]
                )
                pct = int(
                    (data["done"] + data["failed"])
                    / max(data["total"], 1) * 100
                )
                self._sig.overall.emit(
                    data["done"], data["failed"], data["total"], pct
                )

            elif kind == "batch_node_error":
                self._sig.node_error.emit(
                    data["bridge_id"], data["mac"],
                    data.get("reason", "?")
                )
                pct = int(
                    (data["done"] + data["failed"])
                    / max(data["total"], 1) * 100
                )
                self._sig.overall.emit(
                    data["done"], data["failed"], data["total"], pct
                )

            elif kind == "batch_completed":
                self._sig.batch_done.emit(
                    data["done"], data["failed"],
                    data["total"], data["elapsed"]
                )

            elif kind == "batch_stopped":
                self._sig.batch_stopped.emit(
                    data["done"], data["failed"], data["total"]
                )
        except Exception:
            pass

    # =========================================================================
    # Slot‌های GUI (thread اصلی) — نام‌ها باید با connect بالا مطابقت داشته باشند
    # =========================================================================

    def _slot_log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_text.appendPlainText(f"[{ts}] {msg}")

    def _slot_overall(self, done: int, failed: int,
                       total: int, pct: int) -> None:
        self.lbl_done.setText(str(done))
        self.lbl_fail.setText(str(failed))
        self.bar.setValue(pct)

    def _slot_started(self, total: int, bridges: int) -> None:
        self._log(f"🚀 شروع: {total} نود / {bridges} bridge")

    def _slot_node_start(self, bid: str, mac: str, addr: int) -> None:
        self._set_row(bid, mac, "🔄 در حال فلش...", _C_FLASHING, "0%")
        self._log(f"🔄 [{bid}] 0x{addr:02X} — {mac}")

    def _slot_node_done(self, bid: str, mac: str) -> None:
        self._set_row(bid, mac, "✅ موفق", _C_DONE, "100%")
        self._log(f"✅ [{bid}] {mac}")
        self.log_panel.log(f"✅ BATCH OTA → {mac} on {bid}")

    def _slot_node_error(self, bid: str, mac: str, reason: str) -> None:
        self._set_row(bid, mac, f"❌ {reason}", _C_FAILED, "خطا")
        self._log(f"❌ [{bid}] {mac}: {reason}")
        self.log_panel.log(f"❌ BATCH OTA → {mac} on {bid}: {reason}")

    def _slot_completed(self, done: int, failed: int,
                         total: int, elapsed: float) -> None:
        rate = int(done / max(total, 1) * 100)
        self.bar.setValue(100)
        self._log(
            f"🏁 کامل: {done}/{total} موفق ({rate}%) | "
            f"{failed} خطا | {elapsed:.0f}s"
        )
        self.log_panel.log(
            f"🏁 BATCH DONE: {done}/{total} ({rate}%) in {elapsed:.0f}s"
        )
        self._restore_buttons()

        if failed == 0:
            QMessageBox.information(
                self, "✅ موفق",
                f"همه {total} نود با موفقیت فلش شدند!\n"
                f"زمان: {elapsed:.0f}s"
            )
        else:
            QMessageBox.warning(
                self, "تمام شد",
                f"✅ {done} موفق | ❌ {failed} خطا\n"
                f"زمان: {elapsed:.0f}s"
            )

    def _slot_stopped(self, done: int, failed: int, total: int) -> None:
        self._log(
            f"⛔ {done}✅ {failed}❌ {total - done - failed} باقی‌مانده"
        )
        self._restore_buttons()

    # =========================================================================
    # به‌روزرسانی ردیف جدول
    # =========================================================================

    def _set_row(self, bridge_id: str, mac: str,
                  text: str, color: str, prog_text: str) -> None:
        """به‌روزرسانی وضعیت یک نود در جدول."""
        for info in self._rows:
            if info["bridge_id"] == bridge_id and info["mac"] == mac:
                row = info["row"]
                if row >= self.table.rowCount():
                    return

                i4 = QTableWidgetItem(text)
                i4.setForeground(QBrush(QColor(color)))
                i4.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 4, i4)

                i5 = QTableWidgetItem(prog_text)
                i5.setTextAlignment(Qt.AlignCenter)
                i5.setForeground(QBrush(QColor(color)))
                self.table.setItem(row, 5, i5)
                return

    # =========================================================================
    # لاگ کمکی
    # =========================================================================

    def _log(self, msg: str) -> None:
        self._sig.log_msg.emit(msg)

    # =========================================================================
    # تایمرها
    # =========================================================================

    def _tick_poll(self) -> None:
        """پیشرفت نودهای در حال فلش (هر 400ms)."""
        if not self.manager.is_active:
            return
        for info in self._rows:
            ns = self.manager.get_node(info["bridge_id"], info["mac"])
            if ns and ns.status == "FLASHING":
                self._set_row(
                    info["bridge_id"], info["mac"],
                    ns.message or "در حال فلش...",
                    _C_FLASHING,
                    f"{ns.progress}%"
                )

    def _tick_clock(self) -> None:
        """زمان سپری‌شده + ETA (هر 1s)."""
        m = self.manager
        if m.is_active or m.total > 0:
            e = m.elapsed
            self.lbl_time.setText(
                f"{int(e) // 60:02d}:{int(e) % 60:02d}"
            )
            if m.is_active:
                eta = m.eta
                if eta > 0:
                    self.lbl_eta.setText(
                        f"~{int(eta) // 60:02d}:{int(eta) % 60:02d}"
                    )
                else:
                    self.lbl_eta.setText("...")

    # =========================================================================
    # فراخوانی از MainWindow (رویدادهای OTA سرویس اصلی)
    # =========================================================================

    def on_service_ota_done(self, data: Dict[str, Any]) -> None:
        """از MainWindow._handle_event فراخوانی می‌شود."""
        if not self.manager.is_active:
            return
        bid = data.get("bridge_id", "")
        mac = data.get("mac", "")
        if mac and bid:
            self.manager.on_ota_done(bid, mac)

    def on_service_ota_error(self, data: Dict[str, Any]) -> None:
        """از MainWindow._handle_event فراخوانی می‌شود."""
        if not self.manager.is_active:
            return
        bid = data.get("bridge_id", "")
        mac = data.get("mac", "")
        reason = data.get("reason", "?")
        if mac and bid:
            self.manager.on_ota_error(bid, mac, reason)

    def on_service_ota_progress(self, data: Dict[str, Any]) -> None:
        """از MainWindow._handle_event فراخوانی می‌شود."""
        if not self.manager.is_active:
            return
        bid = data.get("bridge_id", "")
        mac = data.get("mac", "")
        status = data.get("status", {})
        if mac and bid:
            self.manager.on_ota_progress(bid, mac, status)



