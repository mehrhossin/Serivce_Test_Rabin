#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""checklist_reset.py — ✅ v6.9 (سفارش کاربر)
چک‌لیست مهاجرت v5.1 را «ریست» می‌کند: همهٔ [x] → [ ] (بازبینی مجدد از صفر).
استفاده:  python3 checklist_reset.py          # ریست
          python3 checklist_reset.py --status  # فقط شمارش
"""
import re
import sys

PATH = "CHECKLIST_V5_1_MIGRATION.md"
import sys as _sys
_args = [a for a in _sys.argv[1:] if not a.startswith("--")]
if _args:
    PATH = _args[0]          # ✅ v7.0: مسیر چک‌لیست از آرگومان

def main():
    s = open(PATH, encoding="utf-8").read()
    done = len(re.findall(r"- \[x\]", s))
    todo = len(re.findall(r"- \[ \]", s))
    if "--status" in sys.argv:
        print(f"وضعیت چک‌لیست: {done} انجام‌شده · {todo} باز · جمع {done+todo}")
        return 0
    s2 = re.sub(r"- \[x\]", "- [ ]", s)
    open(PATH, "w", encoding="utf-8").write(s2)
    print(f"♻️ ریست شد: {done} علامت پاک شد → همهٔ {done+todo} بند از [ ] شروع می‌کنند")
    print("   حالا هر بند را دوباره از صفر بررسی کن (نه از حافظه) — سپس [x] بزن")
    return 0

if __name__ == "__main__":
    sys.exit(main())
