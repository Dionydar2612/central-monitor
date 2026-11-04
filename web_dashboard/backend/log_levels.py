"""
log_levels.py - จัดระดับความรุนแรงของข้อความ log (ERROR / WARN / INFO)
======================================================================

ทำไมต้องมีไฟล์นี้
    เดิมเดาระดับด้วยคำว่า "error" / "failed" ตรง ๆ ทำให้ข้อความปกติถูกนับเป็น error
    เช่น "0 errors", "errors=0", "error.log", "no failures"  และไม่ดูว่า log บอกระดับเองไว้แล้ว
    (เช่น level=info, [WARN], INFO) ตอนนี้ตัดสินตามลำดับ

    0) ระดับในวงเล็บถัดจากเวลาต้นบรรทัด "<เวลา> [ERROR] ..." (รูปแบบที่ puller เขียนให้ journal log)
       ถือเป็นค่าที่แน่นอนที่สุด ตัดสินก่อนข้ออื่น
    1) log บอกระดับเอง  : level=error / "level":"info" / [ERROR] / คำพิมพ์ใหญ่ ERROR ต้นข้อความ
    2) ตัดวลีที่ไม่ใช่ปัญหาออกก่อน : "0 errors", "no failures", "failed=0", "error.log" ...
    3) ค้นคำที่แปลว่าพัง/เตือน ด้วยขอบเขตคำ (\\b) กัน "room" / "zoom" ไม่ให้เป็น oom

    ทั้งรายการ log, ตัวนับ Problem logs / Errors 15m และตัวกรอง Level ใช้ฟังก์ชันเดียวกันนี้
    จึงตรงกันเสมอ (ที่นับว่า error = ที่แสดงว่า ERROR)

คืนค่า: "ERROR" | "WARN" | "INFO"

!! ไฟล์นี้มี 2 ชุดที่ต้องเหมือนกัน:
     web_dashboard/backend/log_levels.py   (หน้าเว็บจัดระดับ/กรอง)
     start_push_log/puller/log_levels.py   (puller ตัด INFO ทิ้งก่อนเก็บ)
   แก้ที่หนึ่ง ให้คัดลอกไปอีกที่ด้วย
"""

import re

LEVELS = ("ERROR", "WARN", "INFO")

_LEVEL_WORDS = {
    "error": "ERROR", "err": "ERROR", "critical": "ERROR", "crit": "ERROR",
    "fatal": "ERROR", "alert": "ERROR", "emerg": "ERROR", "emergency": "ERROR",
    "severe": "ERROR", "panic": "ERROR",
    "warn": "WARN", "warning": "WARN",
    "info": "INFO", "notice": "INFO", "debug": "INFO", "trace": "INFO",
}
_WORD_ALT = "|".join(sorted(_LEVEL_WORDS, key=len, reverse=True))

# 0) "<เวลา> [LEVEL] ..." ต้นบรรทัด
_LEAD_LEVEL = re.compile(r"^\S+\s+\[\s*(" + _WORD_ALT + r")\s*\]", re.I)

# 1) ระดับที่ log บอกเอง
_KV_LEVEL = re.compile(
    r"\b(?:level|severity|lvl|loglevel|log_level)[\"']?\s*[=:]\s*[\"']?(" + _WORD_ALT + r")\b",
    re.I,
)
_BRACKET_LEVEL = re.compile(r"\[\s*(" + _WORD_ALT + r")\s*\]", re.I)
_UPPER_LEVEL = re.compile(
    r"\b(" + "|".join(w.upper() for w in _LEVEL_WORDS) + r")\b"
)   # ตัวพิมพ์ใหญ่เท่านั้น และดูเฉพาะต้นข้อความ

# 2) วลีที่ "มีคำว่า error แต่ไม่ใช่ปัญหา"
_BENIGN = re.compile(
    r"\b(?:no|0|zero|without|non|never)[\s-]+(?:errors?|failures?|failed|exceptions?|warnings?)\b"
    r"|\b(?:errors?|failures?|failed|exceptions?|warnings?)(?:_count|s)?\s*[=:]\s*0\b"
    r"|\b(?:errors?|exceptions?)[\"']?\s*[=:]\s*(?:null|none|nil|false|undefined|0|\"\"|'')(?![a-z0-9])"
    r"|\berror[_.-]?(?:log|handler|page|rate|code)\b"
    r"|\b(?:log|handle|handler)[_ -]?errors?\b(?=\s*(?:registered|enabled|configured))",
    re.I,
)

# 3) คำที่แปลว่าพัง / เตือน
_ERROR_WORDS = re.compile(
    r"\b(?:errors?|exceptions?|fail(?:ed|ure|ures|s|ing)?|fatal|critical|panic|traceback"
    r"|segfault|refused|timed out|denied|unreachable|out of memory"
    r"|no space left|call trace|oops)\b"
    r"|(?<![a-z0-9])oom(?![a-z0-9])"
    r"|\b(?:request|connection|read|write|gateway|operation|query|socket|upstream)\s+time-?out\b"
    r"|\btime-?out\s+(?:exceeded|expired|reached|waiting|error|occurred)\b",      # oom_reaper / oom-killer ได้ แต่ room / zoom ไม่ใช่
    re.I,
)
_WARN_WORDS = re.compile(r"\b(?:warn(?:ing)?s?|deprecated)\b", re.I)

# log ที่ขึ้นต้นด้วย timestamp ยาว ๆ ต้องเว้นให้ก่อนมองหาคำ LEVEL พิมพ์ใหญ่
_UPPER_SCAN_CHARS = 160


def _explicit_level(text):
    m = _LEAD_LEVEL.match(text)
    if m:
        return _LEVEL_WORDS[m.group(1).lower()]

    m = _KV_LEVEL.search(text) or _BRACKET_LEVEL.search(text)
    if m:
        return _LEVEL_WORDS[m.group(1).lower()]

    m = _UPPER_LEVEL.search(text[:_UPPER_SCAN_CHARS])
    if m:
        return _LEVEL_WORDS[m.group(1).lower()]

    return None


def detect_log_level(body):
    """ระดับของข้อความ log: ERROR / WARN / INFO"""
    text = str(body or "")

    explicit = _explicit_level(text)
    if explicit:
        return explicit

    cleaned = _BENIGN.sub(" ", text)

    if _ERROR_WORDS.search(cleaned):
        return "ERROR"

    if _WARN_WORDS.search(cleaned):
        return "WARN"

    return "INFO"
