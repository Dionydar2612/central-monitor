"""
journal.py - อ่าน systemd journal (journalctl) ของเครื่องปลายทาง
===============================================================

ใช้เมื่อ logs[].path ใน puller.yaml เป็น
    journal                        ทั้ง journal ของเครื่อง
    journal:nginx.service          เฉพาะ unit
    journal:nginx.service,ssh      หลาย unit (คั่นด้วย ,)

วิธีดึง (log_puller.pull_journal)
    - จำตำแหน่งด้วย "cursor" ของ journal (เก็บใน checkpoints.json แทน byte offset)
      รอบถัดไปดึงต่อจาก cursor เดิม (--after-cursor) ไม่ซ้ำ ไม่หาย
    - ครั้งแรก (ยังไม่มี cursor) ดึงย้อนหลังเท่ากับ scheduler.interval
    - ไม่เก็บ INFO: กรองที่ต้นทางด้วย -p 0..4 (emerg..warning) เลย ส่งข้อมูลข้ามเครือข่ายน้อยลง
    - แต่ละรายการถูกเขียนเป็น 1 บรรทัด:
          <เวลา UTC ISO> [ERROR|WARN|INFO] <host> <โปรแกรม>[<pid>]: <ข้อความ>
      ระดับมาจาก PRIORITY ของ journal (0-3 = ERROR, 4 = WARN, 5-7 = INFO)
      log_levels.py เชื่อ "[LEVEL]" ที่อยู่ถัดจากเวลาต้นบรรทัดเป็นอันดับแรก

สิทธิ์ที่เครื่องปลายทาง
    user ที่ใช้ SSH (เช่น central-pull) ต้องอ่าน journal ของระบบได้:
        sudo usermod -aG systemd-journal central-pull     (หรือกลุ่ม adm)
"""

import json
import re
import shlex
from datetime import datetime, timezone

UNIT_RE = re.compile(r"^[A-Za-z0-9@._:-]{1,128}$")

MAX_ENTRIES_PER_PULL = 20000     # กันรอบเดียวดึงมหาศาล (ที่เหลือดึงรอบถัดไปต่อจาก cursor)

STORE_PRIORITY = "0..4"          # emerg..warning  (ไม่เก็บ INFO)
INFO_PRIORITY = "5..7"           # notice..debug   (ปุ่ม Load INFO log)


def is_journal(path):
    return path == "journal" or str(path).startswith("journal:")


def journal_units(path):
    """"journal:a.service,b" -> ["a.service", "b"]  (ชื่อไม่ถูกต้อง -> ValueError)"""
    if ":" not in path:
        return []

    units = [u.strip() for u in path.split(":", 1)[1].split(",") if u.strip()]

    for unit in units:
        if not UNIT_RE.match(unit):
            raise ValueError(f"invalid journal unit: {unit!r}")

    return units


def build_command(path, cursor=None, since=None, until=None, priority=None,
                  lines=None, limit=None):
    """คำสั่ง journalctl (ข้อความเดียว ส่งผ่าน ssh)

    since/until = unix วินาที, lines = เอา N รายการล่าสุด, limit = ตัดที่ N บรรทัดแรก
    """
    cmd = ["journalctl", "--no-pager", "-o", "json"]

    for unit in journal_units(path):
        cmd += ["-u", unit]

    if cursor:
        cmd.append("--after-cursor=" + cursor)
    if since is not None:
        cmd.append(f"--since=@{int(since)}")
    if until is not None:
        cmd.append(f"--until=@{int(until)}")
    if priority:
        cmd += ["-p", priority]
    if lines:
        cmd += ["-n", str(int(lines))]

    text = " ".join(shlex.quote(c) for c in cmd)

    if limit:
        text += f" | head -n {int(limit)}"

    return text


def _priority_level(value):
    try:
        prio = int(value)
    except (TypeError, ValueError):
        return "INFO"

    if prio <= 3:
        return "ERROR"
    if prio == 4:
        return "WARN"
    return "INFO"


def _text(value):
    """MESSAGE ที่ไม่ใช่ UTF-8 journal ส่งมาเป็น array ของ byte"""
    if value is None:
        return ""
    if isinstance(value, list):
        try:
            return bytes(value).decode("utf-8", errors="replace")
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def format_entry(obj):
    """1 รายการ journal (dict จาก -o json) -> dict {line, level, cursor, ts, unit}"""
    try:
        ts = int(obj.get("__REALTIME_TIMESTAMP")) / 1_000_000
    except (TypeError, ValueError):
        ts = None

    when = (
        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if ts is not None else "1970-01-01T00:00:00Z"
    )
    level = _priority_level(obj.get("PRIORITY"))
    host = _text(obj.get("_HOSTNAME")) or "-"
    ident = _text(obj.get("SYSLOG_IDENTIFIER")) or _text(obj.get("_COMM")) or "-"
    pid = _text(obj.get("_PID") or obj.get("SYSLOG_PID"))
    message = _text(obj.get("MESSAGE")).replace("\r", "").replace("\n", " ⏎ ")

    proc = f"{ident}[{pid}]" if pid else ident

    return {
        "line": f"{when} [{level}] {host} {proc}: {message}",
        "level": level,
        "cursor": obj.get("__CURSOR"),
        "ts": ts,
        "unit": _text(obj.get("_SYSTEMD_UNIT")),
    }


def parse_output(text):
    """ผล journalctl -o json (1 JSON ต่อบรรทัด) -> list ของ format_entry"""
    entries = []

    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(format_entry(json.loads(raw)))
        except ValueError:
            continue

    return entries
