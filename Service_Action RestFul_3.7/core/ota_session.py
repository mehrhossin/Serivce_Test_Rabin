#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
پروژه   : RBUS Industrial Node System v4.2
فایل    : core/ota_session.py
توضیح   : ماشین حالت OTA برای به‌روزرسانی firmware یک نود ESP8266

           مهم: این کلاس thread ندارد و مستقیماً به socket دسترسی ندارد.
           BridgeWorker در هر iteration حلقه خود tick() را فراخوانی می‌کند
           و فریم‌های مرتبط با OTA را از طریق feed_frame() تحویل می‌دهد.
           این طراحی تضمین می‌کند دقیقاً یک reader/writer روی socket وجود دارد.

           جریان OTA:
           1. ENTER_BOOT (رمز 4 بایتی)    → GPIO0=LOW + ریبوت نود (500ms)
                                           → انتظار BOOT_ACK(0x01)
           2. UPDATE_START (اندازه فایل)   → انتظار UPDATE_ACK(0x00)
           3. برای هر chunk:
              UPDATE_DATA [pkt#, chunk, crc] → انتظار UPDATE_ACK([pkt#, status])
              در صورت خطای CRC: ارسال مجدد همان chunk
              در صورت timeout: retry تا OTA_MAX_RETRIES بار
           4. UPDATE_END                   → انتظار UPDATE_ACK(0x00) → ریبوت نود

           نکته مهم GPIO0:
           نود پس از دریافت ENTER_BOOT، پایه GPIO0 را LOW می‌کند و ریبوت
           می‌کند تا وارد bootloader شود. این فرآیند حداقل 500ms طول می‌کشد.
           deadline فاز ENTER_BOOT باید این تأخیر را در بر بگیرد.
===============================================================================
"""

from __future__ import annotations

import struct
import time
from typing import Any, Callable, Dict, Optional, Tuple

from core.protocol import (
    build_frame, crc16_ccitt,
    CMD_ENTER_BOOT, CMD_UPDATE_START, CMD_UPDATE_DATA, CMD_UPDATE_END,
    CMD_ABORT_BOOT, CMD_BOOT_ACK, CMD_UPDATE_ACK, CMD_BOOT_ERROR,
    CMD_NACK,
    OTA_CHUNK_SIZE, OTA_BOOT_TIMEOUT_S, OTA_ACK_TIMEOUT_S,
    OTA_DATA_ACK_TIMEOUT_S, OTA_END_TIMEOUT_S, OTA_MAX_RETRIES,
)

# تأخیر لازم بعد از ارسال ENTER_BOOT:
# نود GPIO0 را LOW می‌کند، ریبوت می‌کند و وارد bootloader می‌شود.
# این فرآیند حداقل 500ms طول می‌کشد — قبل از این زمان BOOT_ACK نمی‌رسد.
OTA_BOOT_GPIO_DELAY_S: float = 0.5


class OtaSession:
    """
    ماشین حالت کامل OTA برای یک نود.
    فازها: IDLE → ENTER_BOOT → START → FLASH → END → DONE / ERROR / ABORTED
    """

    def __init__(self, bridge_id: str, mac: str, addr: int,
                 firmware: bytes, password: bytes,
                 send_raw: Callable[[bytes], bool],
                 on_event: Callable[[str, Dict[str, Any]], None]) -> None:
        self.bridge_id  = bridge_id
        self.mac        = mac
        self.addr       = addr
        self.firmware   = firmware
        self.password   = password
        self._send_raw  = send_raw
        self._on_event  = on_event

        # وضعیت جاری ماشین حالت
        self.phase        = "IDLE"
        self.progress     = 0
        self.total_bytes  = len(firmware)
        self.sent_bytes   = 0
        self.message      = ""
        self.error        = ""

        # تقسیم firmware به chunk‌های کوچک
        self._chunks      = [firmware[i:i + OTA_CHUNK_SIZE]
                             for i in range(0, len(firmware), OTA_CHUNK_SIZE)]
        self._n_chunks    = len(self._chunks)
        self._pkt_index        = 0
        self._retries          = 0
        self._deadline         = 0.0
        self._abort_requested  = False
        self._finished         = False

    # ---- وضعیت session -------------------------------------------------------

    def is_finished(self) -> bool:
        """آیا session به پایان رسیده است (موفق، خطا، یا لغو)؟"""
        return self._finished

    def request_abort(self) -> None:
        """درخواست لغو OTA در اولین فرصت."""
        self._abort_requested = True

    def snapshot(self) -> Dict[str, Any]:
        """تصویر لحظه‌ای وضعیت جاری برای نمایش در GUI."""
        return {
            "phase":       self.phase,
            "progress":    self.progress,
            "total_bytes": self.total_bytes,
            "sent_bytes":  self.sent_bytes,
            "message":     self.message,
            "error":       self.error,
        }

    # ---- ارسال رویداد --------------------------------------------------------

    def _emit(self, kind: str, **extra: Any) -> None:
        self._on_event(kind, {
            "bridge_id": self.bridge_id,
            "mac":       self.mac,
            "addr":      self.addr,
            **extra,
        })

    def _emit_progress(self) -> None:
        self._emit("ota_progress", status=self.snapshot())

    def _set(self, phase: Optional[str] = None,
             message: str = "", error: str = "") -> None:
        """به‌روزرسانی وضعیت و ارسال رویداد progress."""
        if phase is not None:
            self.phase = phase
        if message:
            self.message = message
        if error:
            self.error = error
        self._emit_progress()

    def _fail(self, reason: str) -> None:
        """پایان دادن به session با خطا."""
        self._set(phase="ERROR", error=reason, message=reason)
        self._finished = True
        self._emit("ota_error", reason=reason)

    def _abort_finish(self) -> None:
        """لغو session و ارسال دستور ABORT_BOOT به نود."""
        self._send_raw(build_frame(self.addr, CMD_ABORT_BOOT, b""))
        self._set(phase="ABORTED", message="لغو شد توسط کاربر")
        self._finished = True
        self._emit("ota_error", reason="Aborted by user")

    def _done(self) -> None:
        """پایان موفق OTA."""
        self._set(phase="DONE", message="Flash کامل شد - نود در حال ریبوت است")
        self.progress = 100
        self._finished = True
        self._emit("ota_done")

    # ---- شروع session --------------------------------------------------------

    def start(self) -> Tuple[bool, str]:
        """
        شروع session OTA.
        بررسی اولیه firmware و ارسال اولین دستور ENTER_BOOT.
        """
        if self.total_bytes < 1024:
            return False, "firmware خیلی کوچک است (کمتر از 1KB)"
        if self.firmware[0] != 0xE9:
            return False, "تصویر ESP8266 نامعتبر است (بایت magic باید 0xE9 باشد)"
        self._send_enter_boot()
        return True, "started"

    # ---- ارسال دستورات OTA --------------------------------------------------

    def _send_enter_boot(self) -> None:
        """
        ارسال دستور ورود به حالت بوت‌لودر.

        جریان سخت‌افزاری پس از دریافت این دستور توسط نود:
          1. نود GPIO0 را LOW می‌کند
          2. نود ریبوت می‌کند
          3. ESP8266 bootloader روی UART بالا می‌آید
          4. BOOT_ACK(0x01) ارسال می‌شود

        این فرآیند حداقل OTA_BOOT_GPIO_DELAY_S = 500ms طول می‌کشد.
        deadline = تأخیر GPIO + timeout پاسخ bootloader
        """
        self.phase    = "ENTER_BOOT"
        self._retries = 0
        self._send_raw(build_frame(self.addr, CMD_ENTER_BOOT, self.password))
        self._deadline = (time.time()
                          + OTA_BOOT_GPIO_DELAY_S
                          + OTA_BOOT_TIMEOUT_S)
        self._set(
            message=f"ENTER_BOOT ارسال شد — "
                    f"منتظر bootloader ({int(OTA_BOOT_GPIO_DELAY_S * 1000)}ms + "
                    f"{int(OTA_BOOT_TIMEOUT_S * 1000)}ms)..."
        )

    def _send_update_start(self) -> None:
        """ارسال دستور شروع به‌روزرسانی با اندازه کل firmware."""
        self.phase    = "START"
        self._retries = 0
        payload = struct.pack("<I", self.total_bytes)
        self._send_raw(build_frame(self.addr, CMD_UPDATE_START, payload))
        self._deadline = time.time() + OTA_ACK_TIMEOUT_S
        self._set(message="در حال ارسال UPDATE_START...")

    def _send_chunk(self) -> None:
        """ارسال chunk جاری با شماره پکت و CRC."""
        self.phase = "FLASH"
        chunk = self._chunks[self._pkt_index]
        crc   = crc16_ccitt(chunk)
        # payload: [شماره پکت 2 بایت LE] + [داده chunk] + [CRC 2 بایت LE]
        payload = (struct.pack("<H", self._pkt_index)
                   + chunk
                   + struct.pack("<H", crc))
        self._send_raw(build_frame(self.addr, CMD_UPDATE_DATA, payload))
        # ✅ v4.3.10: timeout تطبیقی — retryهای اول سریع (0.5s)، بعد از پنجمین
        # retry کند (3s). retry امن است چون نود chunk تکراری را idempotent
        # جواب می‌دهد (پروتکل فیرمور: pkt+1 == expectPkt → ACK 0x00)
        tmo = OTA_DATA_ACK_TIMEOUT_S if self._retries < 5 else OTA_ACK_TIMEOUT_S
        self._deadline = time.time() + tmo
        pct = int((self._pkt_index / self._n_chunks) * 100) if self._n_chunks else 0
        self._set(
            message=f"در حال flash کردن chunk "
                    f"{self._pkt_index + 1}/{self._n_chunks} ({pct}%)"
        )
        self.progress = pct

    def _send_update_end(self) -> None:
        """ارسال دستور پایان به‌روزرسانی."""
        self.phase    = "END"
        self._retries = 0
        self._send_raw(build_frame(self.addr, CMD_UPDATE_END, b""))
        self._deadline = time.time() + OTA_END_TIMEOUT_S
        self._set(message="در حال ارسال UPDATE_END...")

    # ---- tick: مدیریت timeout و retry ----------------------------------------

    def tick(self, now: float) -> None:
        """
        بررسی timeout در فاز جاری و ارسال مجدد در صورت نیاز.
        باید در هر iteration حلقه BridgeWorker فراخوانی شود.

        نکته: در فاز ENTER_BOOT، deadline شامل OTA_BOOT_GPIO_DELAY_S است
        تا نود فرصت کافی برای ریبوت و ورود به bootloader داشته باشد.
        """
        if self._finished:
            return
        if self._abort_requested:
            self._abort_finish()
            return
        if now < self._deadline:
            return   # هنوز در زمان مجاز هستیم

        # timeout رخ داده است
        self._retries += 1
        # ✅ v4.3.10: بک‌آف تطبیقی در FLASH — ۵ retry سریع (0.5s) بعد ۵ retry
        # کند (3s) → تحمل کل ~17.5s مثل قبل، ولی ریکاوری لرزش‌های کوچک در 0.5s.
        # باگ v4.3.9: 5×0.5s=2.5s تحمل، وسط ترافیکِ نود‌های فیرمورقدیمی می‌مرد
        max_retries = OTA_MAX_RETRIES if self.phase != "FLASH" else 10
        if self._retries > max_retries:
            self._fail(
                f"{self.phase}: پس از {max_retries} تلاش پاسخی دریافت نشد"
            )
            return

        self._set(
            message=f"{self.phase}: timeout، تلاش مجدد "
                    f"{self._retries}/{OTA_MAX_RETRIES}"
        )

        # ارسال مجدد بر اساس فاز جاری
        if self.phase == "ENTER_BOOT":
            # هر retry هم باید تأخیر GPIO را در نظر بگیرد —
            # نود هر بار که ENTER_BOOT می‌گیرد دوباره ریبوت می‌کند
            self._send_raw(build_frame(self.addr, CMD_ENTER_BOOT, self.password))
            self._deadline = now + OTA_BOOT_GPIO_DELAY_S + OTA_BOOT_TIMEOUT_S

        elif self.phase == "START":
            payload = struct.pack("<I", self.total_bytes)
            self._send_raw(build_frame(self.addr, CMD_UPDATE_START, payload))
            self._deadline = now + OTA_ACK_TIMEOUT_S

        elif self.phase == "FLASH":
            self._send_chunk()

        elif self.phase == "END":
            self._send_raw(build_frame(self.addr, CMD_UPDATE_END, b""))
            self._deadline = now + OTA_END_TIMEOUT_S

    # ---- feed_frame: دریافت فریم‌های مرتبط با OTA ---------------------------

    def feed_frame(self, cmd: int, data: bytes) -> None:
        """
        پردازش فریم‌های BOOT_ACK / UPDATE_ACK / BOOT_ERROR دریافتی.
        فقط BridgeWorker این متد را فراخوانی می‌کند.
        """
        if self._finished or self._abort_requested:
            return

        # خطای گزارش‌شده توسط نود
        if cmd == CMD_BOOT_ERROR:
            code = data[0] if data else 0xFF
            self._fail(f"نود خطای BOOT_ERROR گزارش داد: 0x{code:02X}")
            return

        # ✅ v4.3.10: NACK فقط وقتی معتبر است که «همان فرمان فاز جاری» از
        # «همان نود هدف» NACK شده باشد — وگرنه NACK نامرتبط است و نادیده
        # می‌رود (باگ v4.3.9: NACK نامرتبط session را می‌کشت)
        # payload فیرمور: [forCmd, err, nodeAddr]
        if cmd == CMD_NACK:
            for_cmd = data[0] if len(data) >= 1 else 0xFF
            err     = data[1] if len(data) >= 2 else 0xFF
            src     = data[2] if len(data) >= 3 else 0xFF
            expected = {
                "ENTER_BOOT": CMD_ENTER_BOOT,
                "START":      CMD_UPDATE_START,
                "FLASH":      CMD_UPDATE_DATA,
                "END":        CMD_UPDATE_END,
            }.get(self.phase)
            if expected is None or for_cmd != expected or src != self.addr:
                return   # NACK نامرتبط (فرمان/نود دیگر) — نادیده بگیر
            if self.phase == "FLASH":
                self._set(message=f"chunk {self._pkt_index}: NACK(0x{err:02X})"
                                 " — ارسال مجدد فوری")
                self._send_chunk()          # resend فوری — بدون کسر از retry
            elif self.phase == "ENTER_BOOT":
                self._fail(f"ENTER_BOOT رد شد (نود {src}: cmd=0x{for_cmd:02X} "
                           f"err=0x{err:02X})")
            else:
                self._fail(f"{self.phase}: نود NACK داد (0x{err:02X})")
            return

        # پاسخ به ENTER_BOOT
        if self.phase == "ENTER_BOOT" and cmd == CMD_BOOT_ACK:
            if data and data[0] == 0x01:
                self._set(message="✅ BOOT_ACK دریافت شد — bootloader آماده است")
                self._send_update_start()
            else:
                self._fail("ENTER_BOOT توسط نود رد شد")
            return

        # پاسخ به UPDATE_START
        if self.phase == "START" and cmd == CMD_UPDATE_ACK:
            if data and data[0] == 0x00:
                self._set(message="UPDATE_START پذیرفته شد")
                self._pkt_index = 0
                self.sent_bytes = 0
                self._send_chunk()
            else:
                code = data[0] if data else 0xFF
                self._fail(f"UPDATE_START رد شد: code=0x{code:02X}")
            return

        # پاسخ به UPDATE_DATA (هر chunk)
        if self.phase == "FLASH" and cmd == CMD_UPDATE_ACK:
            if len(data) >= 3:
                ack_pkt = data[0] | (data[1] << 8)
                status  = data[2]
                if ack_pkt != self._pkt_index:
                    return   # ACK قدیمی یا تکراری؛ نادیده بگیر
                if status == 0x00:
                    # chunk با موفقیت دریافت شد
                    self._retries   = 0
                    self.sent_bytes = min(
                        self.total_bytes,
                        (self._pkt_index + 1) * OTA_CHUNK_SIZE,
                    )
                    self._pkt_index += 1
                    if self._pkt_index >= self._n_chunks:
                        self._send_update_end()
                    else:
                        self._send_chunk()
                elif status == 0x03:
                    # خطای CRC در نود؛ ارسال مجدد فوری همان chunk
                    self._set(
                        message=f"Chunk {self._pkt_index}: خطای CRC، ارسال مجدد"
                    )
                    self._send_chunk()
                else:
                    self._fail(
                        f"chunk {self._pkt_index}: خطای نود 0x{status:02X}"
                    )
            elif len(data) == 1 and data[0] != 0x00:
                self._fail(
                    f"chunk {self._pkt_index}: UPDATE_ACK خطا 0x{data[0]:02X}"
                )
            return

        # پاسخ به UPDATE_END
        if self.phase == "END" and cmd == CMD_UPDATE_ACK:
            if data and data[0] == 0x00:
                self._done()
            else:
                code = data[0] if data else 0xFF
                self._fail(f"UPDATE_END رد شد: code=0x{code:02X}")
            return
        # فریم‌های دیگر در این فاز نادیده گرفته می‌شوند
