#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui/command_panel.py  —  v4.7.0
تغییرات:
  - حذف پریست‌های سریع از بخش سون‌سگمنت
  - حذف تب حرکت PIR
  - بهبود دیباگ فریم خام (لاگ دقیق‌تر)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSlider,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from service import RBusService
from gui.log_panel import LogPanel
from gui.load_form import LoadForm, DEFAULT_BRIDGES
from core.protocol import (
    CMD_NAMES, CMD_PING, CMD_GET_STATUS, CMD_IDENTIFY, CMD_RESET,
    CMD_DISCOVERY_REQ, CMD_SET_CHANNEL_COLOR, CMD_CLEAR_ALL,
    CMD_PIXEL_EFFECT, CMD_STAGE_LOCK, CMD_STAGE_UNLOCK,
    CMD_CLEAR_ADDRESS,
)
from core.led_mapper import led_mapper
from core.effect_engine import (
    EffectEngine, EFFECTS, EFFECT_LABELS,
    EFFECT_RAINBOW, DEFAULT_SPEED_MS, MIN_SPEED_MS, MAX_SPEED_MS,
)


class CommandPanel(QWidget):

    def __init__(
        self,
        service: RBusService,
        load_form: LoadForm,
        log: LogPanel,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.service   = service
        self.load_form = load_form
        self.log       = log

        self._engine = EffectEngine(send_fn=self._effect_send)

        self._build_ui()
        self._sync_mapping_ui()

    # =========================================================================
    # ساخت UI اصلی
    # =========================================================================

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)

        root.addWidget(self._build_target_box())

        self._tabs = QTabWidget()
        self._tabs.addTab(self._make_scroll(self._build_tab_quick()),    "⚡ دستورات سریع")
        self._tabs.addTab(self._make_scroll(self._build_tab_effects()),  "✨ افکت‌ها")
        self._tabs.addTab(self._make_scroll(self._build_tab_segment()),  "🔢 سون‌سگمنت")
        self._tabs.addTab(self._make_scroll(self._build_tab_settings()), "🎨 تنظیمات")
        self._tabs.addTab(self._make_scroll(self._build_tab_action()),  "🎯 اکشن")
        self._tabs.addTab(self._make_scroll(self._build_tab_hide()),    "🔢 هاید")
        self._tabs.addTab(self._make_scroll(self._build_tab_vibron()),  "🌀 وایبرون")
        self._tabs.addTab(self._make_scroll(self._build_tab_tesla()),   "💧 تسلا")
        self._tabs.addTab(self._make_scroll(self._build_tab_custom()),   "🛠️ فریم خام")
        root.addWidget(self._tabs)

    def _make_scroll(self, widget: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(widget)
        return sa

    # ── بخش هدف ──────────────────────────────────────────────────────────────
    def _build_target_box(self) -> QGroupBox:
        box = QGroupBox("🎯 هدف")
        g   = QGridLayout(box)

        g.addWidget(QLabel("Bridge:"), 0, 0)
        self.cb_bridge = QComboBox()
        self.cb_bridge.addItem("همه Bridge‌ها")
        for bid, _, _ in DEFAULT_BRIDGES:
            self.cb_bridge.addItem(bid)
        g.addWidget(self.cb_bridge, 0, 1)

        g.addWidget(QLabel("آدرس نود (hex، FF=broadcast):"), 0, 2)
        self.ed_addr = QLineEdit("FF")
        self.ed_addr.setFixedWidth(60)
        g.addWidget(self.ed_addr, 0, 3)

        btn = QPushButton("استفاده از نود انتخاب‌شده")
        btn.clicked.connect(self._use_selected)
        g.addWidget(btn, 0, 4)
        return box

    # =========================================================================
    # تب ۱ — دستورات سریع
    # =========================================================================

    def _build_tab_quick(self) -> QWidget:
        w    = QWidget()
        root = QVBoxLayout(w)
        root.setSpacing(6)
        root.addWidget(self._build_system_box())
        root.addWidget(self._build_color_box())
        root.addWidget(self._build_broadcast_box())
        root.addWidget(self._build_hw_test_box())
        root.addStretch()
        return w

    def _build_system_box(self) -> QGroupBox:
        box = QGroupBox("🔧 دستورات عمومی سیستم  (CH1=GPIO13 | CH2=GPIO2 | CH3=GPIO16)")
        g   = QGridLayout(box)

        def btn(r, c, text, slot, obj=""):
            b = QPushButton(text)
            b.clicked.connect(slot)
            if obj:
                b.setObjectName(obj)
            g.addWidget(b, r, c)

        def sep(row):
            s = QFrame()
            s.setFrameShape(QFrame.HLine)
            s.setStyleSheet("color:#2A2D35;")
            g.addWidget(s, row, 0, 1, 5)

        def lbl(row, text, color="#94A3B8"):
            l = QLabel(text)
            l.setStyleSheet(f"color:{color}; font-weight:bold;")
            g.addWidget(l, row, 0, 1, 5)

        lbl(0, "🔧 دستورات پایه")
        btn(1, 0, "PING",             lambda: self._send(CMD_PING))
        btn(1, 1, "GET_STATUS",       lambda: self._send(CMD_GET_STATUS))
        btn(1, 2, "IDENTIFY (5s)",    lambda: self._send(CMD_IDENTIFY))
        btn(1, 3, "RESET",            lambda: self._send(CMD_RESET, expect_feedback=False))
        btn(1, 4, "DISCOVERY",        lambda: self._send(CMD_DISCOVERY_REQ, expect_feedback=False))

        btn(2, 0, "Stage LOCK",       lambda: self._send(CMD_STAGE_LOCK, bytes([0x01])))
        btn(2, 1, "Stage UNLOCK",     lambda: self._send(CMD_STAGE_UNLOCK, bytes([0x01])))
        btn(2, 2, "CLEAR_ADDRESS ⚠️", lambda: self._confirm_clear_address())
        btn(2, 3, "روشنایی 100%",    lambda: self._send_raw(0xFF, 0x05, bytes([0xFF, 0xFF])))
        btn(2, 4, "روشنایی 50%",     lambda: self._send_raw(0xFF, 0x05, bytes([0xFF, 0x80])))

        sep(3)
        lbl(4, "🔌 خاموشی کانال‌ها (0x06)")
        btn(5, 0, "📴 خاموش همه",    lambda: self._send_raw(0xFF, 0x06, bytes([0xFF])), "danger")
        btn(5, 1, "خاموش CH1",       lambda: self._send_raw(0xFF, 0x06, bytes([0x01])))
        btn(5, 2, "خاموش CH2",       lambda: self._send_raw(0xFF, 0x06, bytes([0x02])))
        btn(5, 3, "خاموش CH3",       lambda: self._send_raw(0xFF, 0x06, bytes([0x03])))

        return box

    def _build_color_box(self) -> QGroupBox:
        box = QGroupBox("🎨 رنگ ثابت  (CMD=01 | Addr از فیلد هدف)")
        g   = QGridLayout(box)

        def btn(r, c, text, data):
            b = QPushButton(text)
            b.clicked.connect(lambda: self._send_raw_addr(0x01, data))
            g.addWidget(b, r, c)

        btn(0, 0, "🔵 آبی",       bytes([0xFF, 0xFF, 0x00, 0x00, 0xFF]))
        btn(0, 1, "🔴 قرمز",      bytes([0xFF, 0xFF, 0xFF, 0x00, 0x00]))
        btn(0, 2, "🟢 سبز",       bytes([0xFF, 0xFF, 0x00, 0xFF, 0x00]))
        btn(0, 3, "⚪ سفید",      bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))
        btn(0, 4, "🟡 زرد",       bytes([0xFF, 0xFF, 0xFF, 0xFF, 0x00]))

        btn(1, 0, "🩵 فیروزه‌ای",  bytes([0xFF, 0xFF, 0x00, 0xFF, 0xFF]))
        btn(1, 1, "🟣 بنفش",      bytes([0xFF, 0xFF, 0xFF, 0x00, 0xFF]))
        btn(1, 2, "🟠 نارنجی",    bytes([0xFF, 0xFF, 0xFF, 0x80, 0x00]))
        btn(1, 3, "🤍 سفید گرم",  bytes([0xFF, 0xFF, 0xFF, 0xD7, 0xA0]))
        btn(1, 4, "⬛ خاموش",     bytes([0xFF, 0xFF, 0x00, 0x00, 0x00]))

        btn(2, 0, "CH1 آبی",      bytes([0x01, 0xFF, 0x00, 0x00, 0xFF]))
        btn(2, 1, "CH2 آبی",      bytes([0x02, 0xFF, 0x00, 0x00, 0xFF]))
        btn(2, 2, "CH1 قرمز",     bytes([0x01, 0xFF, 0xFF, 0x00, 0x00]))
        btn(2, 3, "CH2 قرمز",     bytes([0x02, 0xFF, 0xFF, 0x00, 0x00]))
        btn(2, 4, "CH1 سبز",      bytes([0x01, 0xFF, 0x00, 0xFF, 0x00]))

        return box

    def _build_broadcast_box(self) -> QGroupBox:
        box = QGroupBox("📡 Broadcast همگانی  (Addr=FF — اجرا روی همه نودها)")
        g   = QGridLayout(box)

        def btn(r, c, text, slot, obj=""):
            b = QPushButton(text)
            b.clicked.connect(slot)
            if obj:
                b.setObjectName(obj)
            g.addWidget(b, r, c)

        btn(0, 0, "🔵 همه آبی",
            lambda: self._send_raw(0xFF, 0x01, bytes([0xFF, 0xFF, 0x00, 0x00, 0xFF])))
        btn(0, 1, "🔴 همه قرمز",
            lambda: self._send_raw(0xFF, 0x01, bytes([0xFF, 0xFF, 0xFF, 0x00, 0x00])))
        btn(0, 2, "🟢 همه سبز",
            lambda: self._send_raw(0xFF, 0x01, bytes([0xFF, 0xFF, 0x00, 0xFF, 0x00])))
        btn(0, 3, "⚪ همه سفید",
            lambda: self._send_raw(0xFF, 0x01, bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF])))
        btn(0, 4, "📴 Blackout سراسری",
            lambda: self._send_raw(0xFF, 0x06, bytes([0xFF])), "danger")

        return box

    def _build_hw_test_box(self) -> QGroupBox:
        box = QGroupBox("🛠️ تست سخت‌افزار و بازی (Firmware v5.7)")
        g   = QGridLayout(box)

        def btn(r, c, text, slot, obj=""):
            b = QPushButton(text)
            b.clicked.connect(slot)
            if obj:
                b.setObjectName(obj)
            g.addWidget(b, r, c)

        def lbl(row, text, color="#94A3B8"):
            l = QLabel(text)
            l.setStyleSheet(f"color:{color}; font-weight:bold;")
            g.addWidget(l, row, 0, 1, 5)

        # تست خودکار (0x59)
        lbl(0, "تست خودکار (0x59)")
        btn(1, 0, "⭐ تست همه فازها", lambda: self._send_raw_addr(0x59, bytes([0x07])))
        btn(1, 1, "فقط سون‌سگمنت",   lambda: self._send_raw_addr(0x59, bytes([0x01])))
        btn(1, 2, "فقط پیکسل کلید", lambda: self._send_raw_addr(0x59, bytes([0x02])))
        btn(1, 3, "فقط جاروی RGB",  lambda: self._send_raw_addr(0x59, bytes([0x04])))
        btn(1, 4, "توقف تست",       lambda: self._send_raw_addr(0x59, bytes([0x00])), "danger")

        # بازی (0x51 - 0x56)
        lbl(2, "دستورات بازی (0x51..)")
        btn(3, 0, "ARM (15s نارنجی)", lambda: self._send_raw_addr(0x51, bytes([0x00, 0xFF, 0x3C, 0x00, 0x0F])))
        btn(3, 1, "START",            lambda: self._send_raw_addr(0x52, bytes([0x00])))
        btn(3, 2, "FAIL",             lambda: self._send_raw_addr(0x53, bytes([0x00])))
        btn(3, 3, "STATUS",           lambda: self._send_raw_addr(0x58, b""))
        btn(3, 4, "CANCEL",           lambda: self._send_raw_addr(0x56, b""), "danger")

        return box
    # =========================================================================
    # تب ۲ — افکت‌ها
    # =========================================================================

    def _build_tab_effects(self) -> QWidget:
        w    = QWidget()
        root = QVBoxLayout(w)
        root.setSpacing(6)
        root.addWidget(self._build_hw_effect_box())
        root.addWidget(self._build_effect_engine_box())
        root.addStretch()
        return w

    def _build_hw_effect_box(self) -> QGroupBox:
        box = QGroupBox("✨ افکت‌های سخت‌افزاری نود  (CMD=27 | Addr از فیلد هدف)")
        g   = QGridLayout(box)

        def btn(r, c, text, slot, obj=""):
            b = QPushButton(text)
            b.clicked.connect(slot)
            if obj:
                b.setObjectName(obj)
            g.addWidget(b, r, c)

        btn(0, 0, "🌈 Rainbow",
            lambda: self._send_raw_addr(0x27, bytes([0x01, 0xFF, 0xFF, 0xFF])))
        btn(0, 1, "🌀 Spin آبی",
            lambda: self._send_raw_addr(0x27, bytes([0x02, 0x00, 0x00, 0xFF])))
        btn(0, 2, "🌀 Spin قرمز",
            lambda: self._send_raw_addr(0x27, bytes([0x02, 0xFF, 0x00, 0x00])))
        btn(0, 3, "💚 Pulse سبز",
            lambda: self._send_raw_addr(0x27, bytes([0x03, 0x00, 0xFF, 0x00])))
        btn(0, 4, "💙 Pulse آبی",
            lambda: self._send_raw_addr(0x27, bytes([0x03, 0x00, 0x00, 0xFF])))

        btn(1, 0, "⚠️ Error قرمز",
            lambda: self._send_raw_addr(0x27, bytes([0x04, 0xFF, 0x00, 0x00])))
        btn(1, 1, "🌫️ Fade Out",
            lambda: self._send_raw_addr(0x27, bytes([0x05, 0xFF, 0xFF, 0xFF])))
        btn(1, 2, "🟩 Matrix سبز",
            lambda: self._send_raw_addr(0x27, bytes([0x06, 0x00, 0xFF, 0x00])))
        btn(1, 3, "⚡ Lightning سفید",
            lambda: self._send_raw_addr(0x27, bytes([0x07, 0xFF, 0xFF, 0xFF])))
        btn(1, 4, "🌧️ Rain فیروزه‌ای",
            lambda: self._send_raw_addr(0x27, bytes([0x08, 0x00, 0x64, 0xFF])))

        btn(2, 0, "💤 Idle (fade+قطره)",
            lambda: self._send_raw_addr(0x27, bytes([0x09])))
        btn(2, 1, "🔢 تست سون‌سگمنت",
            lambda: self._send_raw_addr(0x27, bytes([0x0A])))
        btn(2, 3, "🛑 Stop Effect (این نود)",
            lambda: self._send_raw_addr(0x27, bytes([0x00])), "danger")
        btn(2, 4, "🛑 توقف همه (Broadcast)",
            lambda: self._send_raw(0xFF, 0x27, bytes([0x00])), "danger")

        return box

    def _build_effect_engine_box(self) -> QGroupBox:
        box  = QGroupBox("🖥️ Effect Engine  —  افکت نرم‌افزاری (اجرا در سرویس)")
        root = QVBoxLayout(box)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("افکت:"))
        self.cb_effect = QComboBox()
        for key in EFFECTS:
            self.cb_effect.addItem(EFFECT_LABELS[key], key)
        row1.addWidget(self.cb_effect)

        for lbl_text, attr, default in [
            ("R:", "sp_r", 0), ("G:", "sp_g", 100), ("B:", "sp_b", 255)
        ]:
            row1.addWidget(QLabel(lbl_text))
            sp = QSpinBox()
            sp.setRange(0, 255)
            sp.setValue(default)
            sp.setFixedWidth(60)
            setattr(self, attr, sp)
            row1.addWidget(sp)

        self.chk_random = QCheckBox("🎲 رنگ تصادفی")
        self.chk_random.toggled.connect(lambda on: self._engine.set_random_color(on))
        row1.addWidget(self.chk_random)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("سرعت:"))
        self.sld_speed = QSlider(Qt.Horizontal)
        self.sld_speed.setRange(MIN_SPEED_MS, MAX_SPEED_MS)
        self.sld_speed.setValue(DEFAULT_SPEED_MS)
        self.sld_speed.setTickInterval(50)
        self.sld_speed.setTickPosition(QSlider.TicksBelow)
        self.sld_speed.valueChanged.connect(self._on_speed_changed)
        row2.addWidget(self.sld_speed, stretch=1)
        self.lbl_speed = QLabel(f"{DEFAULT_SPEED_MS} ms")
        self.lbl_speed.setFixedWidth(60)
        self.lbl_speed.setStyleSheet("color:#00E5FF; font-weight:bold;")
        row2.addWidget(self.lbl_speed)
        row2.addWidget(QLabel("کند ◄"))
        row2.addWidget(QLabel("► تند"))
        root.addLayout(row2)

        row3 = QHBoxLayout()
        self.btn_effect_start = QPushButton("▶ شروع افکت")
        self.btn_effect_start.setObjectName("success")
        self.btn_effect_start.clicked.connect(self._start_effect)
        row3.addWidget(self.btn_effect_start)

        self.btn_effect_stop = QPushButton("⏹ توقف افکت")
        self.btn_effect_stop.setObjectName("danger")
        self.btn_effect_stop.setEnabled(False)
        self.btn_effect_stop.clicked.connect(self._stop_effect)
        row3.addWidget(self.btn_effect_stop)

        self.lbl_effect_status = QLabel("متوقف")
        self.lbl_effect_status.setStyleSheet("color:#888;")
        row3.addWidget(self.lbl_effect_status)
        row3.addStretch()
        root.addLayout(row3)

        return box

    # =========================================================================
    # تب ۳ — سون‌سگمنت  (بدون پریست)
    # =========================================================================

    def _build_tab_segment(self) -> QWidget:
        w    = QWidget()
        root = QVBoxLayout(w)
        root.addWidget(self._build_segment_box())
        root.addStretch()
        return w

    def _build_segment_box(self) -> QGroupBox:
        box = QGroupBox("🔢 کلید + سون‌سگمنت  (CMD=0x50)")
        g   = QGridLayout(box)

        g.addWidget(QLabel("keyNum:"), 0, 0)
        self.sp_key = QSpinBox()
        self.sp_key.setRange(0, 1)
        self.sp_key.setFixedWidth(55)
        self.sp_key.setToolTip("0=کلید اول | 1=کلید دوم")
        g.addWidget(self.sp_key, 0, 1)

        g.addWidget(QLabel("رنگ:"), 0, 2)
        self.cb_seg_color = QComboBox()
        for label, code in [
            ("00 — خاموش", 0x00), ("01 — آبی",    0x01),
            ("02 — زرد",   0x02), ("03 — صورتی",  0x03),
            ("04 — نارنجی",0x04), ("05 — سفید",   0x05),
            ("06 — قرمز",  0x06),
        ]:
            self.cb_seg_color.addItem(label, code)
        self.cb_seg_color.setCurrentIndex(1)
        g.addWidget(self.cb_seg_color, 0, 3)

        g.addWidget(QLabel("دهگان (0-9):"), 0, 4)
        self.sp_tens = QSpinBox()
        self.sp_tens.setRange(0, 9)
        self.sp_tens.setFixedWidth(55)
        g.addWidget(self.sp_tens, 0, 5)

        g.addWidget(QLabel("یکان (0-9):"), 0, 6)
        self.sp_ones = QSpinBox()
        self.sp_ones.setRange(0, 9)
        self.sp_ones.setFixedWidth(55)
        g.addWidget(self.sp_ones, 0, 7)

        btn_send = QPushButton("⚡ ارسال 0x50")
        btn_send.setObjectName("success")
        btn_send.clicked.connect(self._send_segment)
        g.addWidget(btn_send, 0, 8)

        return box

    # =========================================================================
    # تب ۴ — تنظیمات
    # =========================================================================

    def _build_tab_settings(self) -> QWidget:
        w    = QWidget()
        root = QVBoxLayout(w)
        root.setSpacing(6)
        root.addWidget(self._build_rgb_mapping_box())
        root.addWidget(self._build_eeprom_box())
        root.addStretch()
        return w

    def _build_rgb_mapping_box(self) -> QGroupBox:
        box = QGroupBox("🎨 RGB Channel Mapping  —  جابجایی کانال‌های رنگ")
        g   = QGridLayout(box)

        g.addWidget(QLabel("R خروجی:"), 0, 0)
        self.cb_r = QComboBox()
        for ch in ["R (0)", "G (1)", "B (2)"]:
            self.cb_r.addItem(ch)
        self.cb_r.setCurrentIndex(0)
        g.addWidget(self.cb_r, 0, 1)

        g.addWidget(QLabel("G خروجی:"), 0, 2)
        self.cb_g = QComboBox()
        for ch in ["R (0)", "G (1)", "B (2)"]:
            self.cb_g.addItem(ch)
        self.cb_g.setCurrentIndex(1)
        g.addWidget(self.cb_g, 0, 3)

        g.addWidget(QLabel("B خروجی:"), 0, 4)
        self.cb_b = QComboBox()
        for ch in ["R (0)", "G (1)", "B (2)"]:
            self.cb_b.addItem(ch)
        self.cb_b.setCurrentIndex(2)
        g.addWidget(self.cb_b, 0, 5)

        btn_apply = QPushButton("✅ اعمال Mapping")
        btn_apply.setObjectName("success")
        btn_apply.clicked.connect(self._apply_mapping)
        g.addWidget(btn_apply, 0, 6)

        btn_reset = QPushButton("↩ ریست (R→R G→G B→B)")
        btn_reset.clicked.connect(self._reset_mapping)
        g.addWidget(btn_reset, 0, 7)

        self.lbl_mapping = QLabel(led_mapper.mapping_label)
        self.lbl_mapping.setStyleSheet("color:#00E5FF; font-weight:bold;")
        g.addWidget(self.lbl_mapping, 1, 0, 1, 8)

        return box

    def _build_eeprom_box(self) -> QGroupBox:
        box = QGroupBox("⚙️ پیکربندی EEPROM")
        g   = QGridLayout(box)

        def btn(r, c, text, slot):
            b = QPushButton(text)
            b.clicked.connect(slot)
            g.addWidget(b, r, c)

        btn(0, 0, "Config پاها (14+14+8)",
            lambda: self._send_raw_addr(0x40, bytes([0x0E, 0x0E, 0x08])))
        btn(0, 1, "روشنایی 100% همه",
            lambda: self._send_raw_addr(0x05, bytes([0xFF, 0xFF])))
        btn(0, 2, "روشنایی 50% همه",
            lambda: self._send_raw_addr(0x05, bytes([0xFF, 0x80])))
        btn(0, 3, "روشنایی 100% CH1",
            lambda: self._send_raw_addr(0x05, bytes([0x01, 0xFF])))
        btn(0, 4, "روشنایی 100% CH3",
            lambda: self._send_raw_addr(0x05, bytes([0x03, 0xFF])))

        return box

    # =========================================================================
    # تب ۵ — فریم خام
    # =========================================================================

    # =========================================================================
    # ✅ v7.1 — چهار تب اختصاصی: 🎯اکشن | 🔢هاید | 🌀وایبرون | 💧تسلا
    # =========================================================================
    def _mk_group(self, title):
        box = QGroupBox(title)
        return box, QGridLayout(box)

    def _mk_btns(self, g):
        def btn(r, c, text, slot, obj=""):
            b = QPushButton(text)
            b.clicked.connect(slot)
            if obj:
                b.setObjectName(obj)
            g.addWidget(b, r, c)
            return b
        return btn

    def _mk_rgb_row(self, g, row, prefix, defaults=(255, 0, 0)):
        """سه QSpinBox رنگی با پیشوند نام — برمی‌گرداند (r,g,b) getter"""
        self._rgb_spins = getattr(self, "_rgb_spins", {})
        widgets = []
        for i, (lbl, dv) in enumerate((("R", defaults[0]), ("G", defaults[1]), ("B", defaults[2]))):
            sp = QSpinBox(); sp.setRange(0, 255); sp.setValue(dv); sp.setFixedWidth(60)
            g.addWidget(QLabel(lbl + ":"), row, i * 2)
            g.addWidget(sp, row, i * 2 + 1)
            widgets.append(sp)
        self._rgb_spins[prefix] = widgets
        return lambda: (widgets[0].value(), widgets[1].value(), widgets[2].value())

    # ─── 🎯 اکشن ───────────────────────────────────────────────────────────
    def _build_tab_action(self) -> QWidget:
        w = QWidget(); root = QVBoxLayout(w); root.setSpacing(6)
        root.addWidget(self._build_action_box()); root.addStretch(); return w

    def _build_action_box(self) -> QGroupBox:
        box, g = self._mk_group("🎯 اکشن — 0x42 ACTION_RING [key R G B] | "
                                "لمس → فید‌اوت 180ms → بازگشت به رنگ | 0x43 خاموشی")
        btn = self._mk_btns(g)
        rgb = self._mk_rgb_row(g, 0, "act", (255, 0, 0))
        def send():
            r_, g_, b_ = rgb()
            self._send_raw_addr(0x42, bytes([0, r_, g_, b_]))
        def preset(r_, g_, b_):
            sp = self._rgb_spins["act"]
            return lambda: (sp[0].setValue(r_), sp[1].setValue(g_),
                            sp[2].setValue(b_),
                            self._send_raw_addr(0x42, bytes([0, r_, g_, b_])))
        btn(1, 0, "✅ روشن حلقه (0x42)", send, "success")
        btn(1, 1, "آبی",   preset(0, 0, 255))
        btn(1, 2, "زرد",   preset(255, 255, 0))
        btn(1, 3, "قرمز",  preset(255, 0, 0))
        btn(1, 4, "سبز",   preset(0, 255, 0))
        btn(1, 5, "سفید",  preset(255, 255, 255))
        btn(2, 0, "خاموشی (0x43)", lambda: self._send_raw_addr(0x43, bytes([0])), "danger")

        # ✅ v7.6: نود پا (نودهای ۱۱/۱۲ استیج — استثنای اکشن)
        btn(3, 0, "🦶 حالت پا فعال (0x44)",
            lambda: self._send_raw_addr(0x44, bytes([1])), "success")
        btn(3, 1, "🦶 حالت پا خاموش",
            lambda: self._send_raw_addr(0x44, bytes([0])), "danger")
        def foot(side):
            sp = self._rgb_spins["act"]
            self._send_raw_addr(0x42, bytes([side, sp[0].value(),
                                             sp[1].value(), sp[2].value()]))
        btn(3, 2, "🦶 هر دو پا همزمان (FF)", lambda: foot(0xFF), "success")
        btn(3, 3, "پای چپ CH2 (key=0)", lambda: foot(0))
        btn(3, 4, "پای راست (1)", lambda: foot(1))
        btn(3, 5, "🦶 خاموشی هر دو پا (FF)",
            lambda: self._send_raw_addr(0x43, bytes([0xFF])), "danger")
        # ✅ v7.10: نود ۱۱ — رنگ ثابت ۳۳px دور استیج (0x45)
        def stage():
            sp = self._rgb_spins["act"]
            self._send_raw_addr(0x45, bytes([sp[0].value(), sp[1].value(), sp[2].value()]))
        btn(5, 0, "⭕ رنگ دور استیج — نود ۱۱ (0x45)", stage, "success")
        btn(5, 1, "خاموشی استیج",
            lambda: self._send_raw_addr(0x45, bytes([0, 0, 0])), "danger")
        lbl = QLabel("نود پا (نود12): 0x44 ماندگار → FF=هر دو پا (0=چپ→CH2 · 1=راست→CH1) | 0x43=خاموشی کامل نود | "
                     "تاچ BS814A-2 (کلاک GPIO5/دیتا GPIO4 — Holtek): کلید۱=چپ، کلید۴=راست | نود11: CH3=حلقه عادی + CH1=۳۳px تزئینی")
        lbl.setStyleSheet("color:#64748B; font-size:11px;")
        lbl.setWordWrap(True)
        g.addWidget(lbl, 4, 0, 1, 6)
        return box

    # ─── 🔢 هاید ───────────────────────────────────────────────────────────
    def _build_tab_hide(self) -> QWidget:
        w = QWidget(); root = QVBoxLayout(w); root.setSpacing(6)
        root.addWidget(self._build_hide_display_box())
        root.addWidget(self._build_hide_game_box())
        root.addStretch(); return w

    def _build_hide_display_box(self) -> QGroupBox:
        box, g = self._mk_group("🔢 هاید — 0x50 [key R G B tens ones] | "
                                "لمس → «00» + حلقه سفید")
        btn = self._mk_btns(g)
        rgb = self._mk_rgb_row(g, 0, "hide", (0, 0, 255))
        g.addWidget(QLabel("عدد:"), 0, 6)
        self.sp_hd_num = QSpinBox(); self.sp_hd_num.setRange(0, 99)
        self.sp_hd_num.setValue(66)
        g.addWidget(self.sp_hd_num, 0, 7)
        def send():
            r_, g_, b_ = rgb(); n = self.sp_hd_num.value()
            self._send_raw_addr(0x50, bytes([0, r_, g_, b_, n // 10, n % 10]))
        btn(1, 0, "✅ نمایش عدد + حلقه (0x50)", send, "success")
        btn(1, 1, "پنجره حرکت ۱ (0x54)", lambda: self._send_raw_addr(0x54, b""))
        btn(1, 2, "پنجره حرکت ۲ (0x55)", lambda: self._send_raw_addr(0x55, b""))
        return box

    def _build_hide_game_box(self) -> QGroupBox:
        box, g = self._mk_group("🎮 هاید — بازی شمارش معکوس (0x51..0x58) [key R G B sec]")
        btn = self._mk_btns(g)
        rgb = self._rgb_spins["hide"]
        get_rgb = lambda: (rgb[0].value(), rgb[1].value(), rgb[2].value())
        g.addWidget(QLabel("ثانیه:"), 0, 0)
        self.sp_hd_sec = QSpinBox(); self.sp_hd_sec.setRange(1, 99)
        self.sp_hd_sec.setValue(15)
        g.addWidget(self.sp_hd_sec, 0, 1)
        def arm():
            r_, g_, b_ = get_rgb()
            self._send_raw_addr(0x51, bytes([0, r_, g_, b_, self.sp_hd_sec.value()]))
        btn(0, 2, "ARM (0x51)", arm, "success")
        btn(0, 3, "START (0x52)", lambda: self._send_raw_addr(0x52, bytes([0])))
        btn(0, 4, "FAIL (0x53)",  lambda: self._send_raw_addr(0x53, bytes([0])))
        btn(0, 5, "CANCEL (0x56)", lambda: self._send_raw_addr(0x56, b""), "danger")
        btn(0, 6, "WAIT (0x57)",  lambda: self._send_raw_addr(0x57, b""))
        btn(0, 7, "STATUS (0x58)", lambda: self._send_raw_addr(0x58, b""))
        return box

    # ─── 🌀 وایبرون ────────────────────────────────────────────────────────
    def _build_tab_vibron(self) -> QWidget:
        w = QWidget(); root = QVBoxLayout(w); root.setSpacing(6)
        root.addWidget(self._build_vibron_timer_box())
        root.addWidget(self._build_vibron_grid_box())
        root.addStretch(); return w

    def _build_vibron_timer_box(self) -> QGroupBox:
        box, g = self._mk_group("🌀 وایبرون — مرحله۱: 0x65 [key R G B sec] تایمر ۲۰px | "
                                "نوار از پیکسل 20 به 1 پر می‌شود")
        btn = self._mk_btns(g)
        rgb = self._mk_rgb_row(g, 0, "vib", (255, 255, 0))
        g.addWidget(QLabel("ثانیه:"), 0, 6)
        self.sp_vb_sec = QSpinBox(); self.sp_vb_sec.setRange(1, 99)
        self.sp_vb_sec.setValue(10)
        g.addWidget(self.sp_vb_sec, 0, 7)
        def start():
            r_, g_, b_ = rgb()
            self._send_raw_addr(0x65, bytes([0, r_, g_, b_, self.sp_vb_sec.value()]))
        def ring():
            r_, g_, b_ = rgb()
            self._send_raw_addr(0x64, bytes([0, r_, g_, b_]))
        btn(1, 0, "✅ مرحله ۱: تایمر (0x65)", start, "success")
        btn(1, 1, "✅ مرحله ۲: حلقه تا لمس (0x64)", ring, "success")
        btn(1, 2, "توقف (0x66)", lambda: self._send_raw_addr(0x66, b""), "danger")
        btn(1, 3, "طول نوار=20", lambda: self._send_raw_addr(0x67, bytes([20])))
        btn(1, 4, "طول نوار=100", lambda: self._send_raw_addr(0x67, bytes([100])))
        lbl = QLabel("لمس → تایمر در مسیر برگشت (1→20) نرم خالی می‌شود | 0x64 بدون تایمر = NACK 04")
        lbl.setStyleSheet("color:#64748B; font-size:11px;")
        g.addWidget(lbl, 2, 0, 1, 8)
        return box

    def _build_vibron_grid_box(self) -> QGroupBox:
        box, g = self._mk_group("🟢 ابزارهای گرید (0x60-0x63)")
        btn = self._mk_btns(g)
        btn(0, 0, "چشمک خطا (0x60)", lambda: self._send_raw_addr(0x60, b""))
        btn(0, 1, "انتخاب کلید (0x61)", lambda: self._send_raw_addr(0x61, b""))
        btn(0, 2, "باران (0x62)", lambda: self._send_raw_addr(0x62, b""))
        btn(0, 3, "باران 6/3", lambda: self._send_raw_addr(0x62, bytes([6, 3])))
        btn(0, 4, "توقف گرید (0x63)", lambda: self._send_raw_addr(0x63, b""), "danger")
        return box

    # ─── 💧 تسلا ───────────────────────────────────────────────────────────
    def _build_tab_tesla(self) -> QWidget:
        w = QWidget(); root = QVBoxLayout(w); root.setSpacing(6)
        root.addWidget(self._build_tesla_drop_box())
        root.addWidget(self._build_tesla_fx_box())
        root.addStretch(); return w

    def _build_tesla_drop_box(self) -> QGroupBox:
        box, g = self._mk_group("💧 تسلا — 0x72 [key R G B sec drops trail act% flags len] | "
                                "قطره از پیکسل 100 وارد → به 1 پیمایش | لمس=سبز")
        btn = self._mk_btns(g)
        rgb = self._mk_rgb_row(g, 0, "tsl", (0, 0, 255))
        row = QHBoxLayout()
        self.sp_ts_sec   = QSpinBox(); self.sp_ts_sec.setRange(1, 99);    self.sp_ts_sec.setValue(10)
        self.sp_ts_drops = QSpinBox(); self.sp_ts_drops.setRange(1, 8);   self.sp_ts_drops.setValue(1)
        self.sp_ts_trail = QSpinBox(); self.sp_ts_trail.setRange(1, 16);  self.sp_ts_trail.setValue(7)
        self.sp_ts_act   = QSpinBox(); self.sp_ts_act.setRange(1, 100);   self.sp_ts_act.setValue(85)
        self.sp_ts_len   = QSpinBox(); self.sp_ts_len.setRange(1, 32);    self.sp_ts_len.setValue(13)
        self.chk_ts_ring = QCheckBox("حلقه"); self.chk_ts_ring.setChecked(True)
        self.chk_ts_miss = QCheckBox("MISS")
        for sp, lb in ((self.sp_ts_sec, "ثانیه"), (self.sp_ts_drops, "قطره"),
                       (self.sp_ts_trail, "دنباله"), (self.sp_ts_act, "فعال%"),
                       (self.sp_ts_len, "بدنه")):
            row.addWidget(sp); row.addWidget(QLabel(lb))
        row.addWidget(self.chk_ts_ring); row.addWidget(self.chk_ts_miss)
        wrap = QWidget(); wrap.setLayout(row)
        g.addWidget(wrap, 1, 0, 1, 8)
        def arm():
            r_, g_, b_ = rgb()
            flags = (1 if self.chk_ts_ring.isChecked() else 0) | \
                    (2 if self.chk_ts_miss.isChecked() else 0)
            self._send_raw_addr(0x72, bytes([0, r_, g_, b_, self.sp_ts_sec.value(),
                                             self.sp_ts_drops.value(), self.sp_ts_trail.value(),
                                             self.sp_ts_act.value(), flags, self.sp_ts_len.value()]))
        btn(2, 0, "✅ قطره ARM (0x72)", arm, "success")
        btn(2, 1, "STATUS (0x73)", lambda: self._send_raw_addr(0x73, b""))
        btn(2, 2, "CANCEL (0x74)", lambda: self._send_raw_addr(0x74, b""), "danger")
        return box

    def _build_tesla_fx_box(self) -> QGroupBox:
        box, g = self._mk_group("⚡ افکت رعد (0x70/0x71)")
        btn = self._mk_btns(g)
        btn(0, 0, "تسلا شروع (0x70)", lambda: self._send_raw_addr(0x70, b""))
        btn(0, 1, "تسلا توقف (0x71)", lambda: self._send_raw_addr(0x71, b""), "danger")
        return box

    def _build_tab_custom(self) -> QWidget:
        w    = QWidget()
        root = QVBoxLayout(w)
        root.addWidget(self._build_custom_box())
        root.addStretch()
        return w

    def _build_custom_box(self) -> QGroupBox:
        box = QGroupBox("🛠️ فریم RBUS خام دلخواه")
        g   = QGridLayout(box)

        g.addWidget(QLabel("کد دستور (hex):"), 0, 0)
        self.ed_cmd = QLineEdit("03")
        self.ed_cmd.setFixedWidth(60)
        g.addWidget(self.ed_cmd, 0, 1)

        g.addWidget(QLabel("بایت‌های data (hex، جداشده با فاصله):"), 0, 2)
        self.ed_data = QLineEdit("")
        self.ed_data.setPlaceholderText("مثال: 01 FF 00 00 FF")
        g.addWidget(self.ed_data, 0, 3)

        btn = QPushButton("⚡ ارسال فریم خام")
        btn.setObjectName("success")
        btn.clicked.connect(self._send_custom)
        g.addWidget(btn, 0, 4)

        # ── راهنمای سریع ─────────────────────────────────────────────────────
        hint = QLabel(
            "📋  مقادیر آماده را از فایل <b>RBUS_COMMANDS.html</b> (بخش 🖥️ تب فریم خام) "
            "کپی کنید و اینجا بچسبانید — کلیک روی هر مقدار در HTML = کپی  |  "
            "نمونه:  cmd=<b>50</b>  data=<b>00 00 00 FF 06 06</b>  →  «66» آبی (هاید)  |  "
            "cmd=<b>42</b>  data=<b>00 FF 00 00</b>  →  حلقه اکشن قرمز |  "
            "cmd=<b>65</b>  data=<b>00 FF FF 00 0A</b>  →  تایمر زرد 10s (وایبرون)"
        )
        hint.setStyleSheet("color:#64748B; font-size:11px;")
        hint.setWordWrap(True)
        g.addWidget(hint, 1, 0, 1, 5)

        return box


    # =========================================================================
    # RGB Mapping
    # =========================================================================

    def _sync_mapping_ui(self) -> None:
        m = led_mapper.mapping
        self.cb_r.setCurrentIndex(m[0])
        self.cb_g.setCurrentIndex(m[1])
        self.cb_b.setCurrentIndex(m[2])
        self.lbl_mapping.setText(led_mapper.mapping_label)

    def _apply_mapping(self) -> None:
        try:
            led_mapper.set_mapping(
                self.cb_r.currentIndex(),
                self.cb_g.currentIndex(),
                self.cb_b.currentIndex(),
            )
            led_mapper.save_to_config()
            self.lbl_mapping.setText(led_mapper.mapping_label)
            self.log.log(f"🎨 RGB Mapping اعمال شد: {led_mapper.mapping_label}")
        except ValueError as e:
            QMessageBox.warning(self, "Mapping نامعتبر", str(e))

    def _reset_mapping(self) -> None:
        led_mapper.set_mapping(0, 1, 2)
        self.cb_r.setCurrentIndex(0)
        self.cb_g.setCurrentIndex(1)
        self.cb_b.setCurrentIndex(2)
        self.lbl_mapping.setText(led_mapper.mapping_label)
        self.log.log("🎨 RGB Mapping ریست شد: R→R G→G B→B")

    # =========================================================================
    # Effect Engine
    # =========================================================================

    def _on_speed_changed(self, val: int) -> None:
        self.lbl_speed.setText(f"{val} ms")
        self._engine.set_speed(val)

    def _start_effect(self) -> None:
        nodes = self._collect_effect_nodes()
        if not nodes:
            QMessageBox.warning(
                self, "نودی یافت نشد",
                "هیچ نود آنلاینی برای اجرای افکت یافت نشد.\n"
                "ابتدا bridge را متصل کرده و Discovery را اجرا کنید.",
            )
            return
        effect = self.cb_effect.currentData()
        speed  = self.sld_speed.value()

        self._engine.set_nodes(nodes)
        self._engine.set_effect(effect)
        self._engine.set_color(self.sp_r.value(), self.sp_g.value(), self.sp_b.value())
        self._engine.set_random_color(self.chk_random.isChecked())
        self._engine.set_speed(speed)

        if not self._engine.is_running:
            self._engine.start()

        self.btn_effect_start.setEnabled(False)
        self.btn_effect_stop.setEnabled(True)
        self.lbl_effect_status.setText(
            f"▶ {EFFECT_LABELS.get(effect, effect)} | {len(nodes)} نود | {speed}ms"
        )
        self.lbl_effect_status.setStyleSheet("color:#3FB950; font-weight:bold;")
        self.log.log(
            f"✨ افکت شروع شد: {EFFECT_LABELS.get(effect, effect)} "
            f"| {len(nodes)} نود | {speed}ms"
        )

    def _stop_effect(self) -> None:
        self._engine.stop_and_clear()
        self.btn_effect_start.setEnabled(True)
        self.btn_effect_stop.setEnabled(False)
        self.lbl_effect_status.setText("متوقف")
        self.lbl_effect_status.setStyleSheet("color:#888;")
        self.log.log("⏹ افکت متوقف شد")

    def _collect_effect_nodes(self) -> List[Dict[str, Any]]:
        bid_filter = self.cb_bridge.currentText()
        nodes = []
        for n in self.service.registry.all():
            if n.addr == 0 or n.in_ota:
                continue
            w = self.service.bridges.get(n.bridge_id)
            if not w or not w.link.connected:
                continue
            if bid_filter != "همه Bridge‌ها" and n.bridge_id != bid_filter:
                continue
            nodes.append({"bridge_id": n.bridge_id, "addr": n.addr})
        return nodes

    def _effect_send(
        self, bridge_id: str, addr: int, r: int, g: int, b: int
    ) -> None:
        payload = led_mapper.apply_bytes(1, r, g, b)
        w = self.service.bridges.get(bridge_id)
        if w and w.link.connected:
            w.send(addr, CMD_SET_CHANNEL_COLOR, payload,
                   tag="effect", expect_feedback=False)

    # =========================================================================
    # ارسال دستور
    # =========================================================================

    def _use_selected(self) -> None:
        n = self.load_form.selected_node()
        if not n:
            QMessageBox.information(
                self, "انتخابی نیست",
                "ابتدا یک نود در تب Discovery انتخاب کنید.",
            )
            return
        self.ed_addr.setText(f"{n.addr:02X}" if n.addr else "FF")
        idx = self.cb_bridge.findText(n.bridge_id)
        if idx >= 0:
            self.cb_bridge.setCurrentIndex(idx)

    def _targets(self) -> List[str]:
        bid = self.cb_bridge.currentText()
        if bid == "همه Bridge‌ها":
            return list(self.service.bridges.keys())
        return [bid]

    def _send(self, cmd: int, data: bytes = b"",
              expect_feedback: bool = True) -> None:
        try:
            addr = int(self.ed_addr.text().strip(), 16)
        except ValueError:
            QMessageBox.warning(self, "آدرس نامعتبر",
                                "یک آدرس hex معتبر وارد کنید.")
            return
        name = CMD_NAMES.get(cmd, f"0x{cmd:02X}")
        if addr == 0xFF and cmd in (CMD_GET_STATUS, CMD_PING):
            # ✅ v4.4.2: نود به broadcast پاسخ نمی‌دهد (قانون پروتکل)
            self.log.log("⚠️ GET_STATUS/PING به broadcast پاسخ نمی‌گیرد — "
                         "آدرس نود مشخص را در فیلد هدف بگذارید")
        for bid in self._targets():
            ok = self.service.send_cmd(
                bid, addr, cmd, data,
                tag=name, expect_feedback=expect_feedback,
            )
            if ok:
                self.log.log(
                    f"➡️ TX {bid} | Addr 0x{addr:02X} | {name} | "
                    f"data={data.hex(' ') if data else '-'}"
                )
            else:
                self.log.log(f"⚠️ {bid} متصل نیست")

    def _send_color(self, ch: int, r: int, g: int, b: int) -> None:
        payload = led_mapper.apply_bytes(ch, r, g, b)
        self._send(CMD_SET_CHANNEL_COLOR, payload)

    def _send_raw(self, addr: int, cmd: int, data: bytes) -> None:
        """ارسال فریم خام با آدرس ثابت — RGB mapping اعمال می‌شود."""
        if cmd == 0x01 and len(data) >= 5:
            ch, bri = data[0], data[1]
            r, g, b = led_mapper.apply(data[2], data[3], data[4])
            data = bytes([ch, bri, r, g, b])
        elif cmd == 0x27 and len(data) >= 4:
            effect_id = data[0]
            r, g, b = led_mapper.apply(data[1], data[2], data[3])
            data = bytes([effect_id, r, g, b])

        name = CMD_NAMES.get(cmd, f"0x{cmd:02X}")

        # ✅ v7.1 GUARD: opcodeهای با payload تغییرکرده — اگر نود فیرمور v6.x دارد
        # هشدار بده (ریشه «هاید ارسال نمی‌کند» و «فید 250ms» = فیرمور قدیمی نود)
        if cmd in (0x42, 0x43, 0x50, 0x51, 0x64, 0x65, 0x72):
            for bid in self._targets():
                node = None
                try:
                    node = self.service.registry.get_by_addr(bid, addr)
                except Exception:
                    pass
                if node is not None and node.fw_major and node.fw_major < 7:
                    self.log.log(
                        f"⚠️ نود 0x{addr:02X} فیرمور v{node.fw_major}.{node.fw_minor} دارد "
                        f"— دستور 0x{cmd:02X} فرمت v7 دارد و روی v6 درست کار نمی‌کند. "
                        f"اول نود را با فیرمور v7.1 فلش/OTA کن (GET_STATUS باید 7.1 بدهد)"
                    )

        for bid in self._targets():
            ok = self.service.send_cmd(
                bid, addr, cmd, data,
                tag=name, expect_feedback=False,
            )
            if ok:
                self.log.log(
                    f"➡️ TX {bid} | Addr 0x{addr:02X} | {name} | "
                    f"data={data.hex(' ') if data else '-'}"
                )
            else:
                self.log.log(f"⚠️ {bid} متصل نیست")

    def _send_raw_addr(self, cmd: int, data: bytes) -> None:
        """ارسال فریم خام — آدرس از فیلد ed_addr."""
        try:
            addr = int(self.ed_addr.text().strip(), 16)
        except ValueError:
            QMessageBox.warning(self, "آدرس نامعتبر",
                                "یک آدرس hex معتبر وارد کنید.")
            return
        self._send_raw(addr, cmd, data)

    def _send_segment(self) -> None:
        """ارسال دستور 0x50 با مقادیر فیلدهای سون‌سگمنت."""
        try:
            addr = int(self.ed_addr.text().strip(), 16)
        except ValueError:
            QMessageBox.warning(self, "آدرس نامعتبر",
                                "یک آدرس hex معتبر وارد کنید.")
            return
        code = self.cb_seg_color.currentData() or 0
        r_, g_, b_ = self._PAL_RGB.get(code, (0, 0, 0))   # ✅ v7.0: RGB یکپارچه
        data = bytes([
            self.sp_key.value(), r_, g_, b_,
            self.sp_tens.value(),
            self.sp_ones.value(),
        ])
        self._send_raw(addr, 0x50, data)

    def _confirm_clear_address(self) -> None:
        try:
            addr = int(self.ed_addr.text().strip(), 16)
        except ValueError:
            QMessageBox.warning(self, "آدرس نامعتبر",
                                "یک آدرس hex معتبر وارد کنید.")
            return
        if addr == 0xFF:
            ret = QMessageBox.warning(
                self, "⚠️ Broadcast",
                "CLEAR_ADDRESS به همه نودها ارسال می‌شود!\nادامه؟",
                QMessageBox.Yes | QMessageBox.No,
            )
        else:
            ret = QMessageBox.question(
                self, "تأیید CLEAR_ADDRESS",
                f"آدرس 0x{addr:02X} پاک می‌شود.\nادامه؟",
                QMessageBox.Yes | QMessageBox.No,
            )
        if ret == QMessageBox.Yes:
            self._send(CMD_CLEAR_ADDRESS, expect_feedback=False)

    def _send_custom(self) -> None:
        """ارسال فریم خام دلخواه — آدرس از فیلد هدف | بدون انتظار feedback."""
        try:
            cmd      = int(self.ed_cmd.text().strip(), 16)
            data_str = self.ed_data.text().strip()
            data     = (bytes(int(x, 16) for x in data_str.split())
                        if data_str else b"")
            addr     = int(self.ed_addr.text().strip(), 16)
        except ValueError as e:
            QMessageBox.warning(self, "مقدار نامعتبر", str(e))
            return

        self.log.log(
            f"🛠️ RAW SEND | cmd=0x{cmd:02X} | "
            f"data=[{data.hex(' ') if data else 'empty'}] | "
            f"addr=0x{addr:02X}"
        )
        # ← از _send_raw استفاده میکنه تا mapping اعمال بشه
        self._send_raw(addr, cmd, data)
