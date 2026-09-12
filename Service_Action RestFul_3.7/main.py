#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : main.py
توضیح   : نقطه ورود برنامه — فقط راه‌اندازی QApplication و MainWindow
           اجرا: python main.py
===============================================================================
"""

import sys
import core.bus_logger as bus_logger
bus_logger.init("bus_traffic.log")   # ← قبل از ساخت RBusService

from PyQt5.QtWidgets import QApplication

from gui.main_window import MainWindow, STYLESHEET


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
