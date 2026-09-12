#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bus_logger.py — لاگ کامل ترافیک RS485 برای عیب‌یابی
هر خط لاگ: timestamp | جهت | bridge | addr | cmd | data (hex)
"""

import time
import threading
import os
from core.protocol import CMD_NAMES

_lock    = threading.Lock()
_logfile = None

def init(path: str = "bus_traffic.log") -> None:
    """یک بار در ابتدای برنامه فراخوانی کن."""
    global _logfile
    _logfile = open(path, "w", encoding="utf-8", buffering=1)
    _write("─" * 80)
    _write(f"  BUS LOGGER START — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    _write("─" * 80)
    print(f"[BusLogger] لاگ در: {os.path.abspath(path)}")

def _write(line: str) -> None:
    ts = time.strftime("%H:%M:%S") + f".{int(time.monotonic() * 1000) % 1000:03d}"
    with _lock:
        if _logfile:
            _logfile.write(f"{ts}  {line}\n")

def log_tx(bridge_id: str, addr: int, cmd: int,
           data: bytes, tag: str = "", ok: bool = True) -> None:
    cmd_name = CMD_NAMES.get(cmd, f"0x{cmd:02X}")
    data_hex = data.hex(" ").upper() if data else "—"
    status   = "✓" if ok else "✗ FAIL"
    _write(
        f"TX {status}  [{bridge_id}]  "
        f"addr=0x{addr:02X}  cmd={cmd_name}({cmd:#04x})  "
        f"data=[{data_hex}]  tag={tag}"
    )

def log_rx(bridge_id: str, addr: int, cmd: int, data: bytes) -> None:
    cmd_name = CMD_NAMES.get(cmd, f"0x{cmd:02X}")
    data_hex = data.hex(" ").upper() if data else "—"
    _write(
        f"RX      [{bridge_id}]  "
        f"addr=0x{addr:02X}  cmd={cmd_name}({cmd:#04x})  "
        f"data=[{data_hex}]"
    )

def log_event(kind: str, info: str) -> None:
    _write(f"EVT     [{kind}]  {info}")

def log_warn(msg: str) -> None:
    _write(f"⚠ WARN  {msg}")

def close() -> None:
    global _logfile
    with _lock:
        if _logfile:
            _logfile.close()
            _logfile = None
