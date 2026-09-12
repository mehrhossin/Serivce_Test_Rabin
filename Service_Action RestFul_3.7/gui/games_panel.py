#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Optional

from PyQt5.QtCore    import pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from gui.games.action_game import ActionGame


class GamesPanel(QWidget):

    game_changed = pyqtSignal(str)

    GAMES = ["Action", "Tesla", "Hide", "Vibron"]

    def __init__(self, service, log_panel, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.service = service
        self.log     = log_panel
        self._action: ActionGame | None = None
        self._build_ui()

        # اتصال رویداد تاچ از service
        self.service.on_touch_event = self._on_touch_event

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)

        # ── گروه انتخاب بازی ──────────────────────────────────────────────
        grp_select = QGroupBox("🎮 انتخاب بازی")
        gl = QHBoxLayout(grp_select)
        gl.setSpacing(20)

        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)

        for idx, name in enumerate(self.GAMES):
            rb = QRadioButton(name)
            if idx == 0:
                rb.setChecked(True)
            self._btn_group.addButton(rb, idx)
            gl.addWidget(rb)

        gl.addStretch()
        root.addWidget(grp_select)

        # ── بخش Action Game ───────────────────────────────────────────────
        self._grp_action = QGroupBox("⚙️ Action Game — سرور منتظر اتصال بازی")
        al = QHBoxLayout(self._grp_action)
        al.setSpacing(10)

        # نمایش اطلاعات سرور (فقط read-only — چون سرور از game_bridge میاد)
        al.addWidget(QLabel("سرور:"))
        self.lbl_server_info = QLabel("127.0.0.1 : 9000")
        self.lbl_server_info.setStyleSheet("color: #7D8590; font-family: monospace;")
        al.addWidget(self.lbl_server_info)

        self.btn_action_connect = QPushButton("▶ فعال‌سازی")
        self.btn_action_connect.setObjectName("connect")
        self.btn_action_connect.setFixedWidth(120)
        self.btn_action_connect.clicked.connect(self._toggle_action)
        al.addWidget(self.btn_action_connect)

        self.lbl_action_status = QLabel("⚫ غیرفعال")
        al.addWidget(self.lbl_action_status)

        al.addStretch()
        root.addWidget(self._grp_action)

        # ── بخش Hide Game ───────────────────────────────────────────────────
        self._grp_hide = QGroupBox("⚙️ Hide Game — سرور منتظر اتصال بازی")
        hl = QHBoxLayout(self._grp_hide)
        hl.setSpacing(10)

        hl.addWidget(QLabel("سرور:"))
        self.lbl_hide_server_info = QLabel("127.0.0.1 : 9000")
        self.lbl_hide_server_info.setStyleSheet("color: #7D8590; font-family: monospace;")
        hl.addWidget(self.lbl_hide_server_info)

        self.btn_hide_connect = QPushButton("▶ فعال‌سازی")
        self.btn_hide_connect.setObjectName("connect")
        self.btn_hide_connect.setFixedWidth(120)
        self.btn_hide_connect.clicked.connect(self._toggle_hide)
        hl.addWidget(self.btn_hide_connect)

        self.lbl_hide_status = QLabel("⚫ غیرفعال")
        hl.addWidget(self.lbl_hide_status)

        hl.addStretch()
        root.addWidget(self._grp_hide)

              
        root.addStretch()

        # اتصال سیگنال‌ها
        self._btn_group.buttonClicked.connect(self._on_game_clicked)
        self._update_game_sections("Action")

    # ------------------------------------------------------------------ API

    @property
    def selected_game(self) -> str:
        btn = self._btn_group.checkedButton()
        return btn.text() if btn else ""

    @property
    def action_active(self) -> bool:
        return self._action is not None

    # ------------------------------------------------------------------ Slots

    def _on_game_clicked(self, btn: QRadioButton) -> None:
        name = btn.text()
        self._update_game_sections(name)
        self.game_changed.emit(name)

    def _update_game_sections(self, name: str) -> None:
        self._grp_action.setVisible(name == "Action")
        self._grp_hide.setVisible(name == "Hide")

    # ── Action Game ───────────────────────────────────────────────────────

    def _toggle_action(self) -> None:
        """فعال/غیرفعال کردن Action Game."""

        # ── غیرفعال‌سازی ──────────────────────────────────────────────────
        if self._action is not None:
            self._action.stop()
            self._action = None
            self._set_action_ui(active=False)
            return

        # ── فعال‌سازی ──────────────────────────────────────────────────────
        bridge_server = getattr(self.service, "game_bridge", None)
        if bridge_server is None:
            self.log.log("❌ game_bridge در service پیدا نشد.")
            self.lbl_action_status.setText("🔴 خطا: game_bridge نیست")
            return

        # نمایش آدرس واقعی سرور
        host = getattr(bridge_server, "host", "127.0.0.1")
        port = getattr(bridge_server, "port", 9000)
        self.lbl_server_info.setText(f"{host} : {port}")

        self._action = ActionGame(
            service       = self.service,
            bridge_server = bridge_server,
            on_log        = lambda m: self.log.log(m),
        )

        if self._action.start():
            self.service._action_game = self._action   # ← اضافه کن
            self._set_action_ui(active=True)
        else:
            self._action = None
            self.lbl_action_status.setText("🔴 خطای راه‌اندازی")

    def _set_action_ui(self, active: bool) -> None:
        """همگام‌سازی UI با وضعیت Action Game."""
        if active:
            self.btn_action_connect.setText("⏹ غیرفعال‌سازی")
            self.btn_action_connect.setObjectName("danger")
            self.lbl_action_status.setText("🟢 فعال — منتظر اتصال بازی")
        else:
            self.btn_action_connect.setText("▶ فعال‌سازی")
            self.btn_action_connect.setObjectName("connect")
            self.lbl_action_status.setText("⚫ غیرفعال")

        # اعمال استایل جدید
        self.btn_action_connect.setStyle(self.btn_action_connect.style())

    # ── رویداد تاچ (از service) ───────────────────────────────────────────

    def _on_touch_event(self, bridge_id: str, addr: int, ch: int) -> None:
        """از service فراخوانی میشه — به ActionGame پاس میده."""
        if self.selected_game == "Action" and self._action is not None:
            self._action.on_touch(addr)

    # ── Hide Game ───────────────────────────────────────────────────────────

    def _toggle_hide(self) -> None:
        """فعال/غیرفعال کردن آمادگی سرور برای Hide Game."""
        is_active = self.btn_hide_connect.text().startswith("⏹")

        if is_active:
            self._set_hide_ui(active=False)
            return

        bridge_server = getattr(self.service, "game_bridge", None)
        if bridge_server is None:
            self.log.log("❌ game_bridge در service پیدا نشد.")
            self.lbl_hide_status.setText("🔴 خطا: game_bridge نیست")
            return

        # نمایش آدرس واقعی سرور
        host = getattr(bridge_server, "host", "127.0.0.1")
        port = getattr(bridge_server, "port", 9000)
        self.lbl_hide_server_info.setText(f"{host} : {port}")

        if not bridge_server.is_running():
            ok = bridge_server.start()
            if not ok:
                self.lbl_hide_status.setText("🔴 خطای راه‌اندازی")
                return

        self._set_hide_ui(active=True)
        self.log.log("🎮 Hide Game سرور آماده است. منتظر دستورات یونیتی...")

    def _set_hide_ui(self, active: bool) -> None:
        """همگام‌سازی UI با وضعیت Hide Game."""
        if active:
            self.btn_hide_connect.setText("⏹ غیرفعال‌سازی")
            self.btn_hide_connect.setObjectName("danger")
            self.lbl_hide_status.setText("🟢 فعال — منتظر دستور بازی")
        else:
            self.btn_hide_connect.setText("▶ فعال‌سازی")
            self.btn_hide_connect.setObjectName("connect")
            self.lbl_hide_status.setText("⚫ غیرفعال")

        # اعمال استایل جدید
        self.btn_hide_connect.setStyle(self.btn_hide_connect.style())
