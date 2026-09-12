#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : core/tcp_link.py
توضیح   : پوشش socket TCP برای اتصال به یک مبدل USR-DR134 (RS485↔TCP)
           اصلاح v4.2: lock روی self.sock در send() و read()
           تا از AttributeError در محیط چند-thread جلوگیری شود
===============================================================================
"""

from __future__ import annotations

import socket
import threading
from typing import Optional


class TCPLink:
    """
    مدیریت یک اتصال TCP به مبدل RS485↔Ethernet.
    thread-safe: send() و read() و disconnect() همگی با lock محافظت می‌شوند.
    """

    def __init__(self) -> None:
        self.sock: Optional[socket.socket] = None
        self.connected = False
        # این lock از دسترسی همزمان چند thread به self.sock جلوگیری می‌کند
        self._lock = threading.Lock()

    def connect(self, host: str, port: int, timeout: float = 3.0) -> bool:
        """
        اتصال به host:port.
        اگر قبلاً متصل بود، ابتدا قطع می‌کند.
        v5.1 FIX B1: KeepAlive + TCP_NODELAY برای جلوگیری از قطعِ هر 2 ثانیه توسط USR-DR134
        """
        self.disconnect()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, port))
            # timeout کوتاه برای حلقه read تا blocking نشود
            s.settimeout(0.002)
            # غیرفعال کردن Nagle برای کاهش تأخیر در RS485
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # v6.0: KeepAlive لینوکس+ویندوز (USR هر 2 ثانیه قطع می‌کرد)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                if hasattr(socket, "TCP_KEEPIDLE"):
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                # ویندوز: SIO_KEEPALIVE_VALS
                if hasattr(socket, "SIO_KEEPALIVE_VALS"):
                    # onoff, keepalivetime, keepaliveinterval in ms
                    s.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 30000, 10000))
            except OSError:
                pass
            with self._lock:
                self.sock = s
                self.connected = True
            return True
        except OSError:
            with self._lock:
                self.sock = None
                self.connected = False
            return False

    def disconnect(self) -> None:
        """قطع اتصال و آزادسازی socket."""
        with self._lock:
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock = None
            self.connected = False

    def send(self, data: bytes) -> bool:
        """
        ارسال داده روی socket.
        اصلاح v4.2: کل عملیات داخل lock انجام می‌شود تا
        بین چک connected و استفاده از sock، thread دیگری
        نتواند sock را None کند.
        """
        with self._lock:
            if not self.connected or self.sock is None:
                return False
            try:
                self.sock.sendall(data)
                return True
            except OSError:
                self.connected = False
                self.sock = None
                return False

    def read(self, maxlen: int = 4096) -> bytes:
        """
        خواندن داده موجود از socket (non-blocking با timeout=0.05s).
        اصلاح v4.2: کل عملیات داخل lock انجام می‌شود.
        خروجی: بایت‌های دریافتی یا b"" در صورت timeout یا خطا
        """
        with self._lock:
            if not self.connected or self.sock is None:
                return b""
            try:
                chunk = self.sock.recv(maxlen)
                if chunk == b"":
                    # peer اتصال را بسته است
                    self.connected = False
                    self.sock = None
                    return b""
                return chunk
            except socket.timeout:
                return b""
            except OSError:
                self.connected = False
                self.sock = None
                return b""
