#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : gui/main_window.py
توضیح   : پنجره اصلی برنامه
           - ترکیب تمام پنل‌های GUI
           - دریافت رویدادها از service (thread-safe با deque + lock)
           - پمپ رویداد هر 50ms در GUI thread
===============================================================================
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Deque, Dict, Tuple

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QMainWindow, QTabWidget, QVBoxLayout, QWidget,
)

from service import RBusService
from gui.log_panel import LogPanel
from gui.load_form import LoadForm
from gui.command_panel import CommandPanel
from gui.ota_panel import OtaPanel
from game_bridge import GameBridgeServer
from core.rest_api import RestApiServer, REST_HOST, REST_PORT
from gui.batch_ota_panel import BatchOtaPanel
from core.protocol import APP_TITLE, APP_VERSION  # اضافه کن
from gui.games_panel import GamesPanel


# استایل کلی برنامه (تم تاریک صنعتی)
STYLESHEET = """
QMainWindow { background: #14161B; }
QWidget { background: #14161B; color: #E8E8E8; font-size: 12px; }
QGroupBox {
    border: 1px solid #33363D; border-radius: 6px; margin-top: 10px;
    padding-top: 8px; font-weight: bold; color: #00E5FF;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton {
    background: #23262E; border: 1px solid #3A3D46; border-radius: 4px;
    padding: 6px 10px; color: #EEE;
}
QPushButton:hover { background: #2E323C; }
QPushButton#success { background: #1B5E20; border-color: #2E7D32; }
QPushButton#success:hover { background: #2E7D32; }
QPushButton#danger { background: #5A1A1A; border-color: #7A2222; }
QPushButton#danger:hover { background: #7A2222; }
QPushButton#connect { background: #0D47A1; border-color: #1565C0; }
QLineEdit, QComboBox, QSpinBox {
    background: #1B1E24; border: 1px solid #3A3D46;
    border-radius: 4px; padding: 4px;
}
QTableWidget, QPlainTextEdit {
    background: #101216; border: 1px solid #2A2D34;
    gridline-color: #2A2D34;
}
QTabBar::tab {
    background: #1E2128;
    color: #00E5FF;
    padding: 8px 16px;
    border: 1px solid #2A2D35;
    border-bottom: none;
    border-radius: 4px 4px 0 0;
    font-weight: bold;
    min-width: 120px;
}
QTabBar::tab:selected {
    background: #2E323C;
    color: #FFFFFF;
    border-bottom: 2px solid #00E5FF;
}
QTabBar::tab:hover:!selected {
    background: #252830;
    color: #80F0FF;
}

QHeaderView::section {
    background: #1B1E24; color: #00E5FF; border: none; padding: 4px;
}
QProgressBar {
    background: #1B1E24; border: 1px solid #3A3D46; border-radius: 4px;
    text-align: center;
}
QProgressBar::chunk { background: #1565C0; border-radius: 3px; }
"""


class MainWindow(QMainWindow):
    """
    پنجره اصلی برنامه RBUS.
    رویدادهای service از thread‌های BridgeWorker می‌رسند و
    از طریق deque thread-safe به GUI thread منتقل می‌شوند.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(
            "RBUS Industrial Node System v4.2 — 75 Node Master"
        )
        self.resize(1280, 900)
        # در __init__:
        self.setWindowTitle(APP_TITLE)
        # ---- صف رویداد thread-safe ------------------------------------------
        self._eq: Deque[Tuple[str, Dict[str, Any]]] = deque()
        self._eq_lock = threading.Lock()

        # ---- ساخت اجزا (ترتیب مهم است) -------------------------------------
        # مرحله 1: log_panel اول ساخته می‌شود چون بقیه به آن نیاز دارند
        self.log_panel = LogPanel()

        # مرحله 2: service ساخته می‌شود
        self.service = RBusService(self._on_service_event)

        # مرحله 3: game_bridge بعد از service و log_panel ساخته می‌شود
        self.game_bridge = GameBridgeServer(
            service=self.service,
            on_log=self.log_panel.log,
        )
        self.service.game_bridge = self.game_bridge   # ← این خط کم بود
        self.game_bridge.start()

        # مرحله 3.5: REST API برای «نرم‌افزار ↔ سرویس» (پل به game_bridge)
        self.rest_api = RestApiServer(
            service=self.service,
            host=REST_HOST, port=REST_PORT,
            on_log=self.log_panel.log,
        )
        self.rest_api.start()

        # مرحله 4: بقیه پنل‌ها
        self.load_form = LoadForm(self.service, self.log_panel)
        self.cmd_panel = CommandPanel(self.service, self.load_form, self.log_panel)
        self.games_panel = GamesPanel(
                service   = self.service,
                log_panel = self.log_panel,
            )
        self.ota_panel = OtaPanel(self.service, self.load_form, self.log_panel)
        self.batch_panel = BatchOtaPanel(self.service, self.log_panel)  # ← تغییر ۲
        

        # ---- چیدمان UI ------------------------------------------------------
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        tabs = QTabWidget()
        tabs.addTab(self.load_form, "📡 Discovery & Bridges")
        tabs.addTab(self.cmd_panel, "🎮 Commands")
        tabs.addTab(self.games_panel,  "🕹️ Games")          # ← تب جدید
        tabs.addTab(self.ota_panel, "📦 OTA Flash")
        tabs.addTab(self.batch_panel, "🚀 فلش گروهی")  # ← تغییر ۳
        tabs.addTab(self.log_panel, "📝 Log")
        root.addWidget(tabs)

        # ---- تایمر پمپ رویداد (هر 50ms) ------------------------------------
        self._pump_timer = QTimer(self)
        self._pump_timer.timeout.connect(self._pump)
        self._pump_timer.start(10)

    # ---- دریافت رویداد از service (از thread‌های BridgeWorker) --------------

    def _on_service_event(self, kind: str, data: Dict[str, Any]) -> None:
        """
        این متد از thread‌های BridgeWorker فراخوانی می‌شود.
        FIX v5.2 realtime: برای REST، مستقیم enqueue کن تا بدون 10ms تاخیرِ pump به کلاینت برسد.
        صف GUI همچنان برای نمایش لاگ استفاده می‌شود.
        """
        # realtime path for REST clients (ActionGame) — bypass GUI pump
        try:
            if kind in ("touch","adc","feedback","node_online","motion") and hasattr(self, "rest_api") and self.rest_api:
                self.rest_api.enqueue_event(kind, data)
        except Exception:
            pass
        with self._eq_lock:
            self._eq.append((kind, data))

    # ---- پمپ رویداد (در GUI thread، هر 50ms) --------------------------------

    def _pump(self) -> None:
        """
        خالی کردن صف رویداد و پردازش در GUI thread.
        swap اتمیک با lock تضمین می‌کند هیچ رویدادی گم نشود.
        """
        with self._eq_lock:
            if not self._eq:
                return
            events, self._eq = self._eq, deque()

        for kind, data in events:
            self._handle_event(kind, data)

    # ---- پردازش رویدادها در GUI thread --------------------------------------

    def _handle_event(self, kind: str, data: Dict[str, Any]) -> None:
        """
        توزیع رویدادهای service به پنل‌های مناسب.
        همه عملیات GUI اینجا انجام می‌شود (در GUI thread).
        """
        # ---- push به کلاینت‌های Unity (اول از همه، بدون شرط) ---------------
        self.game_bridge.on_service_event(kind, data)

        # ---- توزیع به پنل‌های GUI -------------------------------------------
        bid = data.get("bridge_id", "?")

        if kind == "connected":
            self.log_panel.log(
                f"🟢 {bid} متصل شد "
                f"({data.get('host')}:{data.get('port')})"
            )

        elif kind == "conn_error":
            self.log_panel.log(
                f"🔴 {bid} اتصال ناموفق "
                f"({data.get('host')}:{data.get('port')})"
            )

        elif kind == "disconnected":
            self.log_panel.log(f"🔴 {bid} قطع شد")

        elif kind == "feedback":
            self.log_panel.log_feedback(data)

        elif kind == "touch":
            # v5.2: rest_api already enqueued in _on_service_event realtime path — no need duplicate here
            # اطمینان از وجود mac در data برای auto-assign
            # (bridge_worker باید mac را در event قرار دهد)
            self.load_form.on_touch_event(data)

            mac_str = data.get("mac", "N/A")
            self.log_panel.log(
                f"⚡ TOUCH {data.get('bridge_id')} | "
                f"MAC {mac_str} | "
                f"Addr 0x{data.get('addr', 0):02X} | "
                f"CH{data.get('ch', '?')} | {data.get('event', '?')} | seq={data.get('seq','-')}"
            )


        elif kind == "adc":
            self.log_panel.log(
                f"📈 ADC {bid} | "
                f"Addr 0x{data['addr']:02X} | "
                f"value={data['value']}"
            )

        elif kind == "rx_overflow":
            self.log_panel.log(
                f"⚠️ RX OVERFLOW {bid} — "
                f"{data.get('dropped_bytes', 0)} بایت دور انداخته شد"
            )

        elif kind == "ota_progress":
            self.ota_panel.on_ota_progress(data)
            self.batch_panel.on_service_ota_progress(data)   # ← تغییر ۴

        elif kind == "ota_done":
            self.ota_panel.on_ota_done(data)
            self.batch_panel.on_service_ota_done(data)       # ← تغییر ۴

        elif kind == "ota_error":
            self.ota_panel.on_ota_error(data)
            self.batch_panel.on_service_ota_error(data)      # ← تغییر ۴


        elif kind == "ota_ack":
            self.log_panel.log(
                f"📦 OTA_ACK {bid} | "
                f"data={data['data'].hex(' ')}"
            )

        elif kind == "ota_error_raw":
            self.log_panel.log(
                f"❌ BOOT_ERROR (raw) {bid} | "
                f"data={data['data'].hex(' ')}"
            )

        elif kind == "boot_ack":
            self.log_panel.log(
                f"🚀 BOOT_ACK {bid} | "
                f"data={data['data'].hex(' ')}"
            )

        elif kind == "node_updated":
            # فوری جدول رو آپدیت کن — بدون انتظار برای تایمر 500ms
            self.load_form._refresh_table()

        elif kind == "addr_confirmed":
            self.load_form._refresh_table()

        elif kind in ("status_rx", "tx", "rx"):
            pass


    def closeEvent(self, event) -> None:  # noqa: N802
        """هنگام بستن پنجره، سرویس را به‌درستی خاموش کن."""
        self.game_bridge.stop()
        self.rest_api.stop()
        self.service.shutdown()
        super().closeEvent(event)
