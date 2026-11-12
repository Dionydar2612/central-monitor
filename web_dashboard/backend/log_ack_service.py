"""
log_ack_service.py - Acknowledge log (รับทราบแล้ว) เพื่อไม่ให้ตาราง Logs รก
==========================================================================

log ที่ acknowledge แล้ว
    - ถูกซ่อนจากตาราง Logs, ไม่นับในตัวเลข All / Error / Warn, Problem logs, Errors 15m และจุดบนกราฟ
    - ยังอยู่ใน OpenSearch ตามเดิม (ลบเองตามอายุ OPENSEARCH_LOG_RETENTION)
    - ดูได้อีกโดยติ๊ก "Show acknowledged" (มีป้าย ACK + ใครรับทราบ + โน้ต) และยกเลิกได้
เก็บที่ data/state/log_acks.json
    {OpenSearch _id: {by, at, note, web}}                     ack ทีละบรรทัด (แบบเดิม)
    {"fp:<fp>": {by, at, note, web, until, fp}}               ack ทั้งกลุ่มตาม fingerprint (ขั้นที่ 3)
    เก็บไม่เกิน MAX_DAYS วัน / MAX_ITEMS รายการ (log หมดอายุใน OpenSearch แล้วก็ไม่ต้องจำ)

ack ตาม fingerprint (ack_fp)
    puller ดึงทุกชั่วโมง error เดิมจะเข้ามาเป็นบรรทัดใหม่ (_id ใหม่) เสมอ -> ack ตาม _id ต้องทำซ้ำทุกชั่วโมง
    ack ตาม fp ซ่อนทุกบรรทัดที่ fp ตรง รวมบรรทัดที่จะเข้ามาใหม่ จนถึงเวลา until
    หมดเวลา -> ปัญหาที่ยังไม่แก้โผล่กลับมาเอง  web = เฉพาะเครื่อง (คั่นด้วย , ได้) ว่าง = ทุกเครื่อง
ใช้โดย: logs_service.py (กรอง), api.py (/api/logs/ack, /api/logs/unack, /api/logs/ack-fp, /api/logs/unack-fp)
       สิทธิ์ operator ขึ้นไป
"""

import contextlib
import fcntl
import json
import os
import time

from settings import STATE_DIR

ACK_FILE = os.path.join(STATE_DIR, "log_acks.json")
LOCK_FILE = os.path.join(STATE_DIR, ".log_acks.lock")
MAX_DAYS = 40
MAX_ITEMS = 50000
MAX_PER_REQUEST = 1000
MAX_NOTE = 500

_CACHE = {"mtime": None, "data": {}}


@contextlib.contextmanager
def _locked():
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def load():
    """{id: ack}  อ่านซ้ำเฉพาะเมื่อไฟล์เปลี่ยน (หน้าเว็บเรียกบ่อย)"""
    try:
        mtime = os.path.getmtime(ACK_FILE)
    except OSError:
        return {}
    if _CACHE["mtime"] != mtime:
        try:
            with open(ACK_FILE) as f:
                data = json.load(f)
            _CACHE.update(mtime=mtime, data=data if isinstance(data, dict) else {})
        except (OSError, ValueError):
            return {}
    return _CACHE["data"]


def _save(data):
    tmp = ACK_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, ACK_FILE)
    try:                                   # อัปเดต cache ทันที (ไฟล์บางระบบ mtime ละเอียดแค่วินาที -> เขียน 2 ครั้งในวินาทีเดียวจะอ่านค่าเก่า)
        _CACHE.update(mtime=os.path.getmtime(ACK_FILE), data=data)
    except OSError:
        pass


def acknowledge(ids, user, note="", web=None):
    ids = [str(i) for i in (ids or []) if str(i).strip()][:MAX_PER_REQUEST]
    if not ids:
        raise ValueError("ไม่มีรายการที่เลือก")
    now = time.time()
    entry = {"by": user, "at": now, "note": str(note or "").strip()[:MAX_NOTE], "web": web}
    with _locked():
        data = _clean(dict(load()), now)
        for i in ids:
            data[i] = entry
        if len(data) > MAX_ITEMS:
            data = dict(sorted(data.items(), key=lambda kv: kv[1].get("at", 0))[-MAX_ITEMS:])
        _save(data)
    return len(ids)


FP_HOURS = (1, 24, 168)            # ตัวเลือกบนหน้าเว็บ: 1 ชม. / 24 ชม. / 7 วัน
MAX_FP_HOURS = 24 * 30


def _clean(data, now):
    """ตัดรายการเก่าเกิน MAX_DAYS และ ack ตาม fp ที่หมดเวลาแล้ว"""
    cutoff = now - MAX_DAYS * 86400
    return {k: v for k, v in data.items()
            if v.get("at", 0) >= cutoff and not (k.startswith("fp:") and (v.get("until") or 0) <= now)}


def ack_fp(fp, user, note="", until_hours=24, web=None):
    """รับทราบทั้งกลุ่ม (ทุกบรรทัดที่ fp ตรง รวมที่จะเข้ามาใหม่) จนถึง until  คืน dict ที่เก็บ"""
    fp = str(fp or "").strip().lower()
    if not fp or len(fp) > 40 or not all(c in "0123456789abcdef" for c in fp):
        raise ValueError("fp ไม่ถูกต้อง")
    try:
        hours = float(until_hours)
    except (TypeError, ValueError):
        raise ValueError("ระยะเวลาไม่ถูกต้อง")
    if not 0 < hours <= MAX_FP_HOURS:
        raise ValueError(f"ระยะเวลาต้องอยู่ระหว่าง 0-{MAX_FP_HOURS} ชั่วโมง")
    now = time.time()
    entry = {"by": user, "at": now, "note": str(note or "").strip()[:MAX_NOTE], "web": str(web or "").strip() or None,
             "until": now + hours * 3600, "fp": fp}
    with _locked():
        data = _clean(dict(load()), now)
        data["fp:" + fp] = entry
        _save(data)
    return entry


def unack_fp(fp):
    """ยกเลิก ack ตาม fp  คืน True ถ้ามีอยู่"""
    key = "fp:" + str(fp or "").strip().lower()
    with _locked():
        data = dict(load())
        found = key in data
        if found:
            del data[key]
            _save(data)
    return found


def ack_of(entry, acks=None, now=None):
    """ack ที่ครอบ log บรรทัดนี้: _id ตรง (แบบเดิม) หรือ fp ตรง + ยังไม่หมด until + เครื่องตรง  ไม่มี -> None"""
    acks = load() if acks is None else acks
    if not acks:
        return None
    hit = acks.get(entry.get("id"))
    if hit:
        return hit
    fp = entry.get("fp")
    if not fp:
        return None
    a = acks.get("fp:" + fp)
    if not a or (a.get("until") or 0) <= (now or time.time()):
        return None
    webs = [w.strip() for w in str(a.get("web") or "").split(",") if w.strip() and w.strip().lower() not in ("all", "*")]
    if webs and entry.get("web") not in webs:
        return None
    return a


def unacknowledge(ids):
    ids = {str(i) for i in (ids or [])}
    with _locked():
        data = dict(load())
        removed = [i for i in ids if i in data]
        for i in removed:
            del data[i]
        _save(data)
    return len(removed)
