#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : gui/load_form.py
نسخه    : v4.3.0
توضیح   : فرم اصلی مدیریت bridge‌ها و کشف نودها

           تغییرات v4.3.0:
           - رویکرد کامل auto-assign تغییر کرد:
             قبلاً: همه نودها اول CLEAR می‌شدند → همه addr=0x00 → MAC نامشخص
             الان:  هیچ CLEAR اولیه‌ای نیست → نود آدرس دارد → MAC از _addr_to_mac
           - CLEAR فقط برای نود لمس‌شده، لحظه‌ای قبل از SET_ADDRESS
           - on_touch_event از addr معتبر نود برای پیدا کردن MAC استفاده می‌کند
           - متد get_by_addr به NodeRegistry اضافه شد
===============================================================================
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.protocol import (
    ADDR_BROADCAST,
    CMD_CLEAR_ADDRESS,
    CMD_DISCOVERY_REQ,
    mac_bytes_from_str,
)
from service import RBusService
from gui.log_panel import LogPanel


# ---------------------------------------------------------------------------
DEFAULT_BRIDGES: List[tuple] = [
    ("Bridge-1", "192.168.1.235",  5000),
    ("Bridge-2", "192.168.1.8",  5000),
    ("Bridge-3", "192.168.1.9",  5000),
    ("Bridge-4", "192.168.1.10", 5000),
    ("Bridge-5", "192.168.1.11", 5000),
]

_BG_DEFAULT = QColor("#101216")
_BG_DUP     = QColor("#5A1A1A")


# ===========================================================================
class LoadForm(QWidget):

    def __init__(
        self,
        service: RBusService,
        log: LogPanel,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.log     = log

        self.bridge_rows: Dict[str, Dict[str, Any]] = {}

        # ---- وضعیت auto-assign -------------------------------------------
        self._auto_assign_active: bool     = False
        self._counter_address:    int      = 1
        self._assigned_macs:      Set[str] = set()

        # لیست MAC های هدف
        # هر آیتم: {"mac": str, "bridge_id": str}
        self._target_list: List[Dict] = []

        # صف پردازش: هر آیتم tuple (mac, bridge_id)
        self._pending_macs:    List     = []
        self._pending_mac_set: Set[str] = set()
        self._processing:      bool     = False

        self._build_ui()

    # =======================================================================
    # ساخت رابط کاربری
    # =======================================================================

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(self._build_bridge_dock())
        root.addWidget(self._build_node_table())
        self._build_timers()

    # -----------------------------------------------------------------------
    def _build_bridge_dock(self) -> QGroupBox:
        dock = QGroupBox(
            "🔗 Bridge Dock  —  مبدل Ethernet ↔ RS485  (USR-DR134)"
        )
        gl = QGridLayout(dock)
        gl.setColumnStretch(1, 1)

        for col, header in enumerate(
            ["Bridge", "Host / IP", "Port", "", "وضعیت"]
        ):
            gl.addWidget(QLabel(f"<b>{header}</b>"), 0, col)

        for row_idx, (bid, host, port) in enumerate(DEFAULT_BRIDGES, start=1):
            lbl_name   = QLabel(bid)
            ed_host    = QLineEdit(host)
            sp_port    = QSpinBox()
            sp_port.setRange(1, 65535)
            sp_port.setValue(port)
            btn_conn   = QPushButton("اتصال")
            btn_conn.setObjectName("connect")
            lbl_status = QLabel("🔴 آفلاین")

            gl.addWidget(lbl_name,   row_idx, 0)
            gl.addWidget(ed_host,    row_idx, 1)
            gl.addWidget(sp_port,    row_idx, 2)
            gl.addWidget(btn_conn,   row_idx, 3)
            gl.addWidget(lbl_status, row_idx, 4)

            self.service.add_bridge(bid, host, port)
            self.bridge_rows[bid] = {
                "host":   ed_host,
                "port":   sp_port,
                "btn":    btn_conn,
                "status": lbl_status,
            }
            btn_conn.clicked.connect(
                lambda _, b=bid: self._toggle_bridge(b)
            )

        return dock

    # -----------------------------------------------------------------------
    def _build_node_table(self) -> QGroupBox:
        box = QGroupBox("📡 نودهای کشف‌شده  —  MAC = هویت یکتای سخت‌افزاری")
        vl  = QVBoxLayout(box)
        vl.setSpacing(6)

        # ---- نوار فیلتر + اسکن -------------------------------------------
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("فیلتر Bridge:"))
        self.cb_filter = QComboBox()
        self.cb_filter.addItem("همه Bridge‌ها")
        for bid, _, _ in DEFAULT_BRIDGES:
            self.cb_filter.addItem(bid)
        self.cb_filter.currentTextChanged.connect(
            lambda _: self._refresh_table()
        )
        filter_row.addWidget(self.cb_filter)

        btn_scan = QPushButton("🔍 اسکن همه Bridge‌ها  (DISCOVERY_REQ)")
        btn_scan.setObjectName("success")
        btn_scan.clicked.connect(self._do_discovery)
        filter_row.addWidget(btn_scan)
        filter_row.addStretch()
        vl.addLayout(filter_row)

        # ---- جدول نودها ---------------------------------------------------
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "Bridge", "MAC", "آدرس", "وضعیت",
            "FW", "Touch", "ADC", "آخرین دیده‌شدن",
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #101216;
                color: #E0E0E0;
                gridline-color: #2A2D35;
                border: 1px solid #2A2D35;
            }
            QTableWidget::item {
                padding: 4px;
                color: #E0E0E0;
                background-color: #101216;
            }
            QTableWidget::item:selected {
                background-color: #1A3A4A;
                color: #00E5FF;
            }
            QTableWidget::item:alternate {
                background-color: #14161B;
            }
            QHeaderView::section {
                background-color: #1E2128;
                color: #00E5FF;
                padding: 5px;
                border: 1px solid #2A2D35;
                font-weight: bold;
            }
        """)
        vl.addWidget(self.table)

        # ---- نوار آدرس‌دهی -----------------------------------------------
        addr_row = QHBoxLayout()

        addr_row.addWidget(QLabel("آدرس جدید (hex):"))
        self.ed_new_addr = QLineEdit("01")
        self.ed_new_addr.setFixedWidth(52)
        addr_row.addWidget(self.ed_new_addr)

        btn_suggest = QPushButton("💡 پیشنهاد آدرس آزاد")
        btn_suggest.clicked.connect(self._suggest_addr)
        addr_row.addWidget(btn_suggest)

        btn_assign = QPushButton("🎯 اختصاص آدرس  (0x22)")
        btn_assign.setObjectName("success")
        btn_assign.clicked.connect(self._assign_addr)
        addr_row.addWidget(btn_assign)

        btn_clear = QPushButton("🗑 پاک کردن آدرس  (0x24)")
        btn_clear.setObjectName("danger")
        btn_clear.clicked.connect(self._clear_addr)
        addr_row.addWidget(btn_clear)

        btn_clear_all = QPushButton("🗑️ پاک کردن همه آدرس‌ها  (0x24 × همه)")
        btn_clear_all.setObjectName("danger")
        btn_clear_all.clicked.connect(self._clear_all_addresses)
        addr_row.addWidget(btn_clear_all)

        addr_row.addStretch()

        # ---- دکمه auto-assign --------------------------------------------
        self.btn_auto_assign = QPushButton(
            "🤖 شروع اختصاص اتوماتیک آدرس‌ها"
        )
        self.btn_auto_assign.setObjectName("success")
        self.btn_auto_assign.setCheckable(False)
        self.btn_auto_assign.clicked.connect(self._toggle_auto_assign)
        addr_row.addWidget(self.btn_auto_assign)

        # ---- دکمه اختصاص دستی -------------------------------------------
        self.btn_manual_next = QPushButton(
            "➡️ اختصاص آدرس بعدی به نود انتخاب‌شده"
        )
        self.btn_manual_next.setObjectName("connect")
        self.btn_manual_next.setVisible(False)
        self.btn_manual_next.clicked.connect(self._assign_next_to_selected)
        addr_row.addWidget(self.btn_manual_next)

        vl.addLayout(addr_row)
        return box

    # -----------------------------------------------------------------------
    def _build_timers(self) -> None:
        self._critical_timer = QTimer(self)
        self._critical_timer.timeout.connect(self._refresh_status_only)
        self._critical_timer.start(50)      # ← از 100ms به 50ms

        self._full_timer = QTimer(self)
        self._full_timer.timeout.connect(self._refresh_table)
        self._full_timer.start(500)         # ← از 1000ms به 500ms

        self._conn_timer = QTimer(self)
        self._conn_timer.timeout.connect(self._sync_conn_labels)
        self._conn_timer.start(300)         # ← از 400ms به 300ms


    # =======================================================================
    # مدیریت اتصال Bridge
    # =======================================================================

    def _toggle_bridge(self, bid: str) -> None:
        row = self.bridge_rows[bid]
        w   = self.service.bridges.get(bid)
        if not w:
            return
        if w.link.connected:
            self.service.disconnect_bridge(bid)
            # حذف مستقیم نودهای این bridge از registry
            with self.service.registry._lock:
                to_remove = [
                    mac for mac, node in self.service.registry._nodes.items()
                    if node.bridge_id == bid
                ]
                for mac in to_remove:
                    del self.service.registry._nodes[mac]
            self._refresh_table()
            self.log.log(f"🗑️  {len(to_remove)} نود از bridge {bid} حذف شد.")


        else:
            host = row["host"].text().strip()
            port = row["port"].value()
            self.service.set_bridge_endpoint(bid, host, port)
            ok = self.service.connect_bridge(bid)
            if ok:
                self.service.broadcast_discovery(bid)
            else:
                QMessageBox.warning(
                    self, "خطای اتصال",
                    f"اتصال به {bid}  ({host}:{port})  ناموفق بود.",
                )

    def _sync_conn_labels(self) -> None:
        for bid, row in self.bridge_rows.items():
            w         = self.service.bridges.get(bid)
            connected = bool(w and w.link.connected)
            row["status"].setText("🟢 آنلاین" if connected else "🔴 آفلاین")
            row["btn"].setText("قطع اتصال" if connected else "اتصال")

    def _do_discovery(self) -> None:
        for bid, w in self.service.bridges.items():
            if w.link.connected:
                w.send(ADDR_BROADCAST, CMD_DISCOVERY_REQ, b"",
                       tag="manual_disc", expect_feedback=False)
        self.log.log("🔍 DISCOVERY_REQ به همه bridge‌های متصل ارسال شد.")

    # =======================================================================
    # Auto-assign — روال v4.3.0
    # =======================================================================

    def _toggle_auto_assign(self) -> None:
        if self._auto_assign_active:
            self._stop_auto_assign()
        else:
            QTimer.singleShot(0, self._confirm_and_start)

    # -----------------------------------------------------------------------
    def _confirm_and_start(self) -> None:
        confirm = QMessageBox.question(
            self,
            "تأیید شروع اختصاص اتوماتیک",
            "⚠️  روش اختصاص — یکی یکی:\n\n"
            "  ۱.  نودی را که می‌خواهید آدرس بگیرد لمس کنید\n"
            "  ۲.  سیستم فقط همان نود را CLEAR می‌کند\n"
            "  ۳.  آدرس اختصاص می‌یابد\n"
            "  ۴.  نود بعدی را لمس کنید\n\n"
            "⚡ نودها باید قبلاً Discovery شده باشند.\n"
            "⚡ هیچ آدرسی از قبل پاک نمی‌شود.\n\n"
            "آیا ادامه می‌دهید؟",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._do_start_auto_assign()

    # -----------------------------------------------------------------------
    def _do_start_auto_assign(self) -> None:
        nodes = list(self.service.registry.all())

        if not nodes:
            QMessageBox.warning(self, "هیچ نودی یافت نشد",
                "ابتدا bridge را متصل کرده و Discovery را اجرا کنید.")
            return

        valid_nodes = [
            n for n in nodes
            if self.service.bridges.get(n.bridge_id)
            and self.service.bridges[n.bridge_id].link.connected
        ]

        if not valid_nodes:
            QMessageBox.warning(self, "هیچ bridge متصلی یافت نشد",
                "ابتدا bridge را متصل کنید.")
            return

        valid_nodes.sort(key=lambda n: n.mac)

        self._target_list = [
            {"mac": n.mac.upper().strip(), "bridge_id": n.bridge_id}
            for n in valid_nodes
        ]

        # ریست کامل وضعیت
        self._auto_assign_active = False
        self._counter_address    = 1
        self._assigned_macs      = set()
        self._pending_macs       = []
        self._pending_mac_set    = set()
        self._processing         = False

        total = len(valid_nodes)
        self.log.log(
            f"📋  لیست MAC ساخته شد — {total} نود:\n"
            + "\n".join(
                f"       {i+1:2d}. {e['mac']}  [{e['bridge_id']}]"
                for i, e in enumerate(self._target_list)
            )
        )

        # ── v4.3.2: آدرس موقت به همه نودها بده ──────────────────────────────
        # آدرس موقت از 0xE0 شروع می‌شود (خارج از محدوده کاری 0x01-0xFE)
        # این باعث می‌شود _addr_to_mac پر شود و touch قابل تشخیص باشد
        TEMP_BASE = 0xE0
        self.btn_auto_assign.setEnabled(False)
        self.btn_auto_assign.setText("⏳  در حال آدرس‌دهی موقت...")
        self.btn_auto_assign.setStyle(self.btn_auto_assign.style())

        self.log.log(
            f"🔧  آدرس موقت به {total} نود داده می‌شود "
            f"(0x{TEMP_BASE:02X}–0x{TEMP_BASE+total-1:02X})..."
        )

        DELAY_MS =1200  # فاصله بین هر SET_ADDRESS موقت

        for i, entry in enumerate(self._target_list):
            mac       = entry["mac"]
            bridge_id = entry["bridge_id"]
            temp_addr = TEMP_BASE + i

            QTimer.singleShot(
                i * DELAY_MS,
                lambda m=mac, b=bridge_id, a=temp_addr: (
                    self._assign_temp_address(m, b, a)
                )
            )

        # بعد از همه SET_ADDRESS موقت + 1 ثانیه اضافه → فعال کن
        finish_delay = total * DELAY_MS + 2000
        QTimer.singleShot(finish_delay, self._activate_auto_assign)
        self.log.log(f"⏱️  زمان تخمینی: {finish_delay}ms")

    def _assign_temp_address(self, mac: str, bridge_id: str,
                            temp_addr: int, retry: int = 0) -> None:
        """
        آدرس موقت به نود بده.
        v4.3.4: اگر bridge در لحظه اجرا متصل نبود، تا 5 بار retry کن.
        """
        MAX_RETRY = 5
        RETRY_DELAY_MS = 800

        w = self.service.bridges.get(bridge_id)
        if not w or not w.link.connected:
            if retry < MAX_RETRY:
                self.log.log(
                    f"⚠️  bridge متصل نیست — retry {retry+1}/{MAX_RETRY} "
                    f"برای {mac} (0x{temp_addr:02X})"
                )
                QTimer.singleShot(
                    RETRY_DELAY_MS,
                    lambda: self._assign_temp_address(mac, bridge_id,
                                                    temp_addr, retry + 1)
                )
            else:
                self.log.log(
                    f"❌  آدرس موقت 0x{temp_addr:02X} → {mac} "
                    f"ناموفق — bridge پس از {MAX_RETRY} تلاش متصل نشد"
                )
            return

        from core.protocol import CMD_SET_ADDRESS, mac_bytes_from_str
        mac_b   = mac_bytes_from_str(mac)
        payload = mac_b + bytes([temp_addr & 0xFF])

        # CLEAR → همه آدرس‌های قدیمی این MAC پاک می‌شوند
        w.send(ADDR_BROADCAST, CMD_CLEAR_ADDRESS, mac_b,
            tag=f"tmp_clr_{mac}", expect_feedback=False)
        w.notify_cleared(mac)
        self.log.log(f"🗑  CLEAR → {mac}  (600ms صبر...)")

        def _do_set() -> None:
            _w = self.service.bridges.get(bridge_id)
            if not _w or not _w.link.connected:
                # bridge در فاصله 600ms قطع شد → retry کل فرآیند
                self.log.log(
                    f"⚠️  bridge قطع شد حین SET موقت {mac} → retry"
                )
                QTimer.singleShot(
                    800,
                    lambda: self._assign_temp_address(mac, bridge_id,
                                                    temp_addr, retry + 1)
                )
                return
            _w.send(ADDR_BROADCAST, CMD_SET_ADDRESS, payload,
                    tag=f"tmp_set_{mac}", expect_feedback=False)
            # آپدیت مستقیم — بدون انتظار برای ADDRESS_ACK
            _w._addr_to_mac[temp_addr] = mac.upper().strip()
            _w._zero_addr_macs.discard(mac.upper().strip())
            # registry هم آپدیت کن
            self.service.registry.upsert_addr_ack(mac, bridge_id, temp_addr)
            self.log.log(f"🔧  آدرس موقت 0x{temp_addr:02X} → {mac} ✅")

        QTimer.singleShot(600, _do_set)

    # -----------------------------------------------------------------------
    def _activate_auto_assign(self) -> None:
        # بررسی نودهایی که آدرس موقت نگرفتند
        missing = []
        for entry in self._target_list:
            mac       = entry["mac"]
            bridge_id = entry["bridge_id"]
            w = self.service.bridges.get(bridge_id)
            if not w:
                continue
            # بررسی اینکه MAC در _addr_to_mac هست یا نه
            found = any(m == mac.upper().strip()
                        for m in w._addr_to_mac.values())
            if not found:
                missing.append(entry)

        if missing:
            self.log.log(
                f"⚠️  {len(missing)} نود آدرس موقت نگرفتند — retry خودکار:"
            )
            TEMP_BASE = 0xE0
            for entry in missing:
                mac       = entry["mac"]
                bridge_id = entry["bridge_id"]
                # پیدا کردن index اصلی برای temp_addr
                idx = next(
                    (i for i, e in enumerate(self._target_list)
                    if e["mac"] == mac),
                    None
                )
                if idx is None:
                    continue
                temp_addr = TEMP_BASE + idx
                self.log.log(
                    f"   🔄 retry آدرس موقت 0x{temp_addr:02X} → {mac}"
                )
                QTimer.singleShot(
                    200,
                    lambda m=mac, b=bridge_id, a=temp_addr: (
                        self._assign_temp_address(m, b, a)
                    )
                )

            # صبر بیشتر و دوباره بررسی
            QTimer.singleShot(3000, self._activate_auto_assign)
            return

        # همه نودها آدرس موقت دارند → فعال‌سازی
        self._auto_assign_active = True

        self.btn_auto_assign.setEnabled(True)
        self.btn_auto_assign.setText("⛔  توقف اختصاص اتوماتیک آدرس‌ها")
        self.btn_auto_assign.setObjectName("danger")
        self.btn_auto_assign.setStyle(self.btn_auto_assign.style())
        self.btn_manual_next.setVisible(True)

        for bid, w in self.service.bridges.items():
            if w.link.connected:
                self.log.log(
                    f"📊  [{bid}] وضعیت نهایی:\n"
                    f"       addr≠0x00 : {len(w._addr_to_mac)} نود "
                    f"→ {dict(w._addr_to_mac)}\n"
                    f"       addr=0x00 : {len(w._zero_addr_macs)} نود"
                )

        total = len(self._target_list)
        self.log.log(
            f"✅  آماده — {total} نود در لیست.\n"
            f"       هر نود را لمس کنید تا آدرس واقعی بگیرد."
        )

        # 🔴 همه نودها قرمز — منتظر تاچ
        QTimer.singleShot(200, lambda: self._set_all_nodes_color(
            255, 0, 0, "🔴  همه نودها قرمز — منتظر تاچ"
        ))


    # -----------------------------------------------------------------------
    def _stop_auto_assign(self) -> None:
        self._auto_assign_active = False
        self._processing         = False
        self._pending_macs.clear()
        self._pending_mac_set.clear()
        self.btn_manual_next.setVisible(False)
        self.btn_auto_assign.setText("🤖  شروع اختصاص اتوماتیک آدرس‌ها")
        self.btn_auto_assign.setObjectName("success")
        self.btn_auto_assign.setStyle(self.btn_auto_assign.style())

        assigned_count = len(self._assigned_macs)
        total_count    = len(self._target_list)
        remaining      = [
            e["mac"] for e in self._target_list
            if e["mac"] not in self._assigned_macs
        ]
        last = self._counter_address - 1
        self.log.log(
            f"⛔  Auto-assign متوقف شد — "
            f"اختصاص‌یافته: {assigned_count}/{total_count} — "
            f"آخرین آدرس: 0x{last:02X}"
        )
        if remaining:
            self.log.log(
                f"⚠️  نودهای بدون آدرس ({len(remaining)}):\n"
                + "\n".join(f"       • {m}" for m in remaining)
            )
    def _set_all_nodes_color(self, r: int, g: int, b: int,
                            log_msg: str = "") -> None:
        """ارسال SET_CHANNEL_COLOR به همه نودهای لیست target."""
        from core.protocol import CMD_SET_CHANNEL_COLOR
        if log_msg:
            self.log.log(log_msg)
        for entry in self._target_list:
            mac       = entry["mac"]
            bridge_id = entry["bridge_id"]
            w = self.service.bridges.get(bridge_id)
            if not w or not w.link.connected:
                continue
            node = self.service.registry.get(mac)
            if not node or node.addr == 0:
                continue
            w.send(
                node.addr, CMD_SET_CHANNEL_COLOR,
                bytes([1, r, g, b]),
                tag=f"color_{mac}", expect_feedback=False,
            )

    # -----------------------------------------------------------------------
    def on_touch_event(self, data: Dict[str, Any]) -> None:
        if not self._auto_assign_active:
            return
        if data.get("event") != "PRESS":
            return

        mac       = data.get("mac", "").upper().strip()
        bridge_id = data.get("bridge_id", "")
        addr      = data.get("addr", 0xFF)

        # فقط نودهایی که آدرس دارند قابل تشخیص هستند
        if addr == 0x00:
            # addr=0x00 → MAC نامشخص → از دکمه دستی استفاده کن
            return

        # اگر mac خالی است → از registry پیدا کن
        if not mac and addr not in (0x00, 0xFF):
            n = self.service.registry.get_by_addr(bridge_id, addr)
            if n:
                mac = n.mac.upper().strip()

        if not mac:
            return

        if not any(e["mac"] == mac for e in self._target_list):
            return

        if mac in self._assigned_macs:
            return

        if mac in self._pending_mac_set:
            return

        self._pending_macs.append((mac, bridge_id))
        self._pending_mac_set.add(mac)
        self.log.log(
            f"📥  در صف: {mac}  "
            f"[bridge={bridge_id}, addr=0x{addr:02X}]  "
            f"(موقعیت {len(self._pending_macs)})"
        )
        # 🟡 نود لمس‌شده زرد — در حال پردازش
        from core.protocol import CMD_SET_CHANNEL_COLOR
        w = self.service.bridges.get(bridge_id)
        if w and w.link.connected:
            w.send(
                addr, CMD_SET_CHANNEL_COLOR,
                bytes([1, 255, 200, 0]),
                tag=f"yellow_{mac}", expect_feedback=False,
            )

        if not self._processing:
            QTimer.singleShot(0, self._process_queue)

    # -----------------------------------------------------------------------
    def _process_queue(self) -> None:
        if not self._auto_assign_active or not self._pending_macs:
            self._processing = False
            return

        self._processing = True

        if self._counter_address > 0xFE:
            self.log.log("⚠️  آدرس‌ها تمام شدند!")
            self._pending_macs.clear()
            self._pending_mac_set.clear()
            self._stop_auto_assign()
            return

        mac, bridge_id = self._pending_macs.pop(0)
        self._pending_mac_set.discard(mac)
        new_addr = self._counter_address

        if mac in self._assigned_macs:
            QTimer.singleShot(0, self._process_queue)
            return

        # ثبت فوری تا touch تکراری رد شود
        self._assigned_macs.add(mac)

        # CLEAR فقط همین نود — نود ریست می‌کند
        w = self.service.bridges.get(bridge_id)
        if w and w.link.connected:
            w.send(
                ADDR_BROADCAST, CMD_CLEAR_ADDRESS,
                mac_bytes_from_str(mac),
                tag=f"pre_clr_{mac}", expect_feedback=False,
            )
            # notify_cleared تا _addr_to_mac پاک شود
            w.notify_cleared(mac)
            self.log.log(f"🗑  CLEAR_ADDRESS → {mac}")

        def _do_assign() -> None:
            ok, reason = self.service.assign_address(
                mac, bridge_id, new_addr
            )
            if ok:
                self._counter_address += 1
                self.log.log(
                    f"🤖  Auto-assign: {mac} → "
                    f"0x{new_addr:02X} @ {bridge_id} ✅"
                )
                # ⚪ نود آدرس گرفت → سفید
                from core.protocol import CMD_SET_CHANNEL_COLOR
                _w = self.service.bridges.get(bridge_id)
                if _w and _w.link.connected:
                    _w.send(
                        new_addr, CMD_SET_CHANNEL_COLOR,
                        bytes([1, 255, 255, 255]),
                        tag=f"white_{mac}", expect_feedback=False,
                    )

                remaining = [
                    e["mac"] for e in self._target_list
                    if e["mac"] not in self._assigned_macs
                ]
                if not remaining:
                    self.log.log(
                        f"🎉  همه {len(self._target_list)} نود "
                        f"آدرس گرفتند! Auto-assign کامل شد."
                    )
                    # 🟢 همه سبز — کامل شد
                    QTimer.singleShot(500, lambda: self._set_all_nodes_color(
                        0, 255, 0, "🟢  همه نودها سبز — آدرس‌دهی کامل شد"
                    ))
                    QTimer.singleShot(700, self._stop_auto_assign)

                    QTimer.singleShot(200, self._stop_auto_assign)
                    return
                else:
                    self.log.log(
                        f"       {len(remaining)} نود باقی‌مانده: "
                        + ", ".join(remaining)
                    )
            else:
                self._counter_address += 1
                self._assigned_macs.discard(mac)
                self.log.log(
                    f"❌  Auto-assign خطا برای {mac}: {reason} — retry"
                )
                self._pending_macs.insert(0, (mac, bridge_id))
                self._pending_mac_set.add(mac)

            # v4.3.3: 300ms صبر تا ADDRESS_ACK از نود برسد و باس آرام شود
            QTimer.singleShot(300, self._process_queue)

        # v4.3.3: 700ms صبر تا نود CLEAR را پردازش کند و ریست شود
        QTimer.singleShot(700, _do_assign)

    # -----------------------------------------------------------------------
    def _assign_next_to_selected(self) -> None:
        if not self._auto_assign_active:
            return

        n = self.selected_node()
        if not n:
            QMessageBox.information(
                self, "انتخابی نیست",
                "ابتدا یک نود از جدول انتخاب کنید.",
            )
            return

        mac       = n.mac.upper().strip()
        bridge_id = n.bridge_id

        if not any(e["mac"] == mac for e in self._target_list):
            QMessageBox.information(
                self, "خارج از لیست",
                f"{mac} در لیست auto-assign نیست.",
            )
            return

        if mac in self._assigned_macs:
            QMessageBox.information(
                self, "آدرس قبلاً اختصاص یافته",
                f"{mac} قبلاً آدرس گرفته است.",
            )
            return

        if mac in self._pending_mac_set:
            QMessageBox.information(
                self, "در صف است",
                f"{mac} در صف انتظار است.",
            )
            return

        if self._counter_address > 0xFE:
            self.log.log("⚠️  آدرس‌ها تمام شدند!")
            self._stop_auto_assign()
            return

        self._pending_macs.append((mac, bridge_id))
        self._pending_mac_set.add(mac)
        self.log.log(
            f"📥  دستی به صف اضافه شد: {mac} "
            f"(موقعیت {len(self._pending_macs)})"
        )

        if not self._processing:
            QTimer.singleShot(0, self._process_queue)

    # =======================================================================
    # Refresh جدول نودها
    # =======================================================================

    def _get_filtered_nodes(self) -> list:
        filt  = self.cb_filter.currentText()
        nodes = sorted(
            self.service.registry.all(),
            key=lambda n: (n.bridge_id, n.addr == 0, n.addr),
        )
        if filt != "همه Bridge‌ها":
            nodes = [n for n in nodes if n.bridge_id == filt]
        return nodes

    # -----------------------------------------------------------------------
    def _refresh_table(self) -> None:
        sel_mac  = None
        sel_rows = (
            self.table.selectionModel().selectedRows()
            if self.table.selectionModel() else []
        )
        if sel_rows:
            item = self.table.item(sel_rows[0].row(), 1)
            if item:
                sel_mac = item.text()

        nodes     = self._get_filtered_nodes()
        brush_dup = QBrush(_BG_DUP)

        self.table.setRowCount(len(nodes))

        for row, n in enumerate(nodes):
            vals = [
                n.bridge_id,
                n.mac,
                n.addr_str,
                n.status,
                n.fw_str,
                f"0x{n.touch_mask:02X}",
                str(n.adc),
                time.strftime("%H:%M:%S", time.localtime(n.last_seen)),
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if n.dup_addr:
                    item.setBackground(brush_dup)
                self.table.setItem(row, col, item)

            if n.mac == sel_mac:
                self.table.selectRow(row)

    # -----------------------------------------------------------------------
    def _refresh_status_only(self) -> None:
        nodes = self._get_filtered_nodes()

        if self.table.rowCount() != len(nodes):
            self._refresh_table()
            return

        brush_dup   = QBrush(_BG_DUP)
        brush_clear = QBrush(_BG_DEFAULT)

        for row, n in enumerate(nodes):
            s_item = self.table.item(row, 3)
            if s_item is not None:
                if s_item.text() != n.status:
                    s_item.setText(n.status)
                s_item.setBackground(
                    brush_dup if n.dup_addr else brush_clear
                )

            t_item = self.table.item(row, 7)
            if t_item is not None:
                t_item.setText(
                    time.strftime("%H:%M:%S", time.localtime(n.last_seen))
                )

    # =======================================================================
    # دریافت نود انتخاب‌شده از جدول
    # =======================================================================

    def selected_node(self):
        rows = (
            self.table.selectionModel().selectedRows()
            if self.table.selectionModel() else []
        )
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 1)
        if not item:
            return None
        return self.service.registry.get(item.text())

    # =======================================================================
    # آدرس‌دهی دستی
    # =======================================================================

    def _suggest_addr(self) -> None:
        n = self.selected_node()
        if not n:
            QMessageBox.information(
                self, "انتخابی نیست",
                "ابتدا یک نود از جدول انتخاب کنید.",
            )
            return
        free = self.service.registry.next_free_addr(n.bridge_id)
        if free:
            self.ed_new_addr.setText(f"{free:02X}")
        else:
            QMessageBox.information(
                self, "آدرس آزاد",
                "هیچ آدرس آزادی روی این bridge یافت نشد.",
            )

    # -----------------------------------------------------------------------
    def _assign_addr(self) -> None:
        n = self.selected_node()
        if not n:
            QMessageBox.information(
                self, "انتخابی نیست",
                "ابتدا یک نود از جدول انتخاب کنید.",
            )
            return

        try:
            new_addr = int(self.ed_new_addr.text().strip(), 16)
            if not (0x01 <= new_addr <= 0xFE):
                raise ValueError
        except ValueError:
            QMessageBox.warning(
                self, "آدرس نامعتبر",
                "یک آدرس hex معتبر بین 01 تا FE وارد کنید.",
            )
            return

        ok, reason = self.service.assign_address(
            n.mac, n.bridge_id, new_addr
        )
        if ok:
            self.log.log(
                f"🎯  اختصاص آدرس 0x{new_addr:02X} → "
                f"{n.mac} روی {n.bridge_id}"
            )
        else:
            QMessageBox.warning(self, "خطای آدرس‌دهی", reason)

    # -----------------------------------------------------------------------
    def _clear_addr(self) -> None:
        n = self.selected_node()
        if not n:
            QMessageBox.information(
                self, "انتخابی نیست",
                "ابتدا یک نود از جدول انتخاب کنید.",
            )
            return

        if self.service.clear_address(n.mac, n.bridge_id):
            self.log.log(
                f"🗑  پاک کردن آدرس → {n.mac} روی {n.bridge_id}"
            )
        else:
            QMessageBox.warning(
                self, "خطا", "پاک کردن آدرس ناموفق بود."
            )

    def _clear_all_addresses(self) -> None:
        """پاک کردن آدرس همه نودها — یکی یکی با فاصله ۱.۵ ثانیه."""
        from core.protocol import CMD_CLEAR_ADDRESS, mac_bytes_from_str

        # فقط نودهایی که آدرس دارند (addr != 0)
        targets = [
            n for n in self.service.registry.all()
            if n.addr != 0
            and self.service.bridges.get(n.bridge_id)
            and self.service.bridges[n.bridge_id].link.connected
        ]

        if not targets:
            QMessageBox.information(self, "پاک کردن آدرس‌ها",
                                    "هیچ نودی با آدرس فعال یافت نشد.")
            return

        confirm = QMessageBox.question(
            self,
            "تأیید پاک کردن همه آدرس‌ها",
            f"⚠️  {len(targets)} نود آدرس دارند.\n\n"
            f"آدرس همه یکی‌یکی با فاصله ۱.۵ ثانیه پاک می‌شود.\n\n"
            f"آیا ادامه می‌دهید؟",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self.log.log(f"🗑️  شروع پاک کردن آدرس {len(targets)} نود...")

        DELAY_MS = 1500

        for i, node in enumerate(targets):
            mac_b = mac_bytes_from_str(node.mac)
            w     = self.service.bridges.get(node.bridge_id)

            QTimer.singleShot(
                i * DELAY_MS,
                lambda _w=w, _mac_b=mac_b, _mac=node.mac, _i=i, _t=len(targets): (
                    _w.send(
                        ADDR_BROADCAST, CMD_CLEAR_ADDRESS, _mac_b,
                        tag=f"clear_all_{_mac}", expect_feedback=False,
                    ) if _w and _w.link.connected else None,
                    self.log.log(
                        f"   🗑️  [{_i+1}/{_t}] پاک کردن آدرس → {_mac}"
                    )
                )
            )

        # بعد از اتمام همه → رفرش جدول
        QTimer.singleShot(
            len(targets) * DELAY_MS + 500,
            self._refresh_table
        )
        self.log.log(
            f"⏱️  زمان تخمینی: "
            f"{(len(targets) * DELAY_MS + 500) / 1000:.1f} ثانیه"
        )
