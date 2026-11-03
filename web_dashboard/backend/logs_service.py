"""
logs_service.py - ค้น/นับ log จาก OpenSearch และผูกกับ metrics ของเครื่อง
========================================================================

หน้าที่
    ดึง log ที่ otel ส่งเข้า OpenSearch (index logs-central-*) มาให้หน้าเว็บ
    โดยแต่ละแถว log จะแนบ "metrics ตอนเกิด log" (correlated_metrics)

log เป็นของเครื่องไหน?
    ไม่มี field "เครื่อง" ใน OpenSearch แต่ชื่อไฟล์ใน buffer มีชื่อเครื่องฝังอยู่
        <site>__<เครื่อง>__<source>__...log   (puller ตั้งชื่อ ดู log_puller.py)
    otel เก็บชื่อไฟล์ไว้ที่ attributes["log.file.name"]  -> is_web_file() เช็ค "__<เครื่อง>__"

วิธีทำงาน (เปลี่ยนจากเดิม)
    เดิม: ยิง OpenSearch 3 แบบต่างกัน (รายการ 5000 / นับ problem 1000 / นับ error 1000)
          แล้วกรองเครื่องฝั่ง Python -> เครื่องที่ log น้อยเห็นไม่ครบ และตัวเลขไม่ตรงกับรายการ
    ใหม่: _scan(web) อ่านเป็นหน้า ๆ (หน้าละ 2000 สูงสุด 10000 เอกสาร) จนได้ log ของเครื่องนี้ครบ
          แล้วจัดระดับด้วย log_levels.detect_log_level แล้ว "ทุกอย่างคำนวณจากผลสแกนเดียวกัน"
            - รายการ log (+ตัวกรอง level/type)   get_logs
            - ตัวนับ Problem logs / Errors 15m     get_problem_count / get_errors_15m
            - จำนวนต่อ level/type บนปุ่มตัวกรอง    get_log_stats
          ผลสแกนเก็บ cache สั้น ๆ (SCAN_TTL วินาที) ไม่ให้ยิง OpenSearch ซ้ำทุกคำขอ

เวลาของ log (timestamp)
    ใช้ @timestamp ถ้าถูกต้อง ถ้าเป็น 1970 (otel อ่านเวลาไม่ได้) ใช้เวลาจากต้นข้อความ
    ถ้ายังไม่ได้ ใช้เวลาดึงไฟล์ (อยู่ในชื่อไฟล์ buffer) แทน -> ไม่ตกไปท้ายรายการ

เชื่อมกับอะไร
    - clients.opensearch_request  : ยิง query
    - log_levels                  : จัดระดับ ERROR / WARN / INFO
    - metrics_service             : metrics ตอนเกิด log
    - system_state                : service/process ตอนเกิด log (หน้ารายละเอียด)
    - ผู้เรียก: api.py (/api/problems, /api/problems/detail, /api/log-stats, /api/summary)
"""

import re
import time
from datetime import datetime, timezone

from clients import opensearch_request
from log_levels import LEVELS, detect_log_level
from metrics_service import get_metrics_at_time
from settings import resolve_web_name
from system_state import load_system_snapshot_at
from timeutil import to_unix

LOG_INDEX_PATTERN = "/logs-central-*/_search"

# field ที่ต้องใช้จาก OpenSearch (ไม่ดึงทั้งก้อน)
SOURCE_FIELDS = ["@timestamp", "body", "message", "attributes", "resource"]

# การสแกน
SCAN_PAGE = 2000            # เอกสารต่อหน้า
SCAN_MAX_DOCS = 10000       # index.max_result_window ปริยายของ OpenSearch = 10000
SCAN_KEEP = 3000            # ได้ log ของเครื่องนี้ครบเท่านี้แล้วหยุดอ่านหน้าถัดไป
SCAN_TTL = 8                # วินาทีที่ผลสแกนใช้ซ้ำได้

# ฟิลด์จาก snapshot ที่แนบในหน้ารายละเอียด log
DETAIL_SNAPSHOT_KEYS = (
    "load_1m", "load_5m", "memory_used", "memory_available", "swap",
    "disk_available", "inode", "net_rx_errors", "net_tx_errors", "uptime",
)

# เวลาจากต้นข้อความ log เช่น 2026-10-05T09:12:58.321282+07:00 (หรือ Z)
_BODY_TS = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))"
)
# เวลาดึงไฟล์ในชื่อไฟล์ buffer: __20261005T013000Z__
_FILE_TS = re.compile(r"__(\d{8}T\d{6}Z)__")

_SCAN_CACHE = {}     # web -> (หมดอายุเมื่อ, [entry])


# ---------------------------------------------------------------------------
# ตัวช่วย
# ---------------------------------------------------------------------------

def is_web_file(web, source_file):
    """ไฟล์ log นี้เป็นของเครื่องนี้หรือไม่ (ตรงชื่อเครื่องทั้งก้อน web-01 จึงไม่ปนกับ web-011)

    web อาจเป็น hostname จริง (ตั้ง aliases ใน puller.yaml) -> แปลงเป็นชื่อ target ก่อน
    """
    web = resolve_web_name(web)
    return f"__{web}__" in str(source_file or "")


def _resolve_timestamp(timestamp, body, filename):
    """เวลาที่ใช้แสดง/ผูกกับ metrics (ดูหัวไฟล์)"""
    if timestamp and not str(timestamp).startswith("1970-01-01"):
        return timestamp

    match = _BODY_TS.match(str(body or ""))
    if match:
        return match.group(1)

    match = _FILE_TS.search(str(filename or ""))
    if match:
        try:
            pulled = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ")
            return pulled.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass

    return timestamp


def _hit_fields(hit):
    source = hit.get("_source", {}) or {}
    attributes = source.get("attributes", {}) or {}
    resource = source.get("resource", {}) or {}
    body = source.get("body") or source.get("message", "") or ""
    filename = attributes.get("log.file.name")
    timestamp = _resolve_timestamp(source.get("@timestamp"), body, filename)

    return source, resource, body, filename, timestamp


# cache ของ "metrics ตอนเกิด log" แยกตาม (เครื่อง, นาที) เพราะรายการ log
# ยาวหลายร้อยแถวส่วนใหญ่อยู่ในนาทีเดียวกัน ไม่ต้องยิง Prometheus ซ้ำทุกแถว
_METRIC_CACHE = {}


def _metrics_cached(timestamp, web):
    key = (web, str(timestamp or "")[:16])    # ตัดถึงระดับนาที

    if key not in _METRIC_CACHE:
        if len(_METRIC_CACHE) > 2000:
            _METRIC_CACHE.clear()

        try:
            metrics = get_metrics_at_time(timestamp, web)
            _METRIC_CACHE[key] = {k: v for k, v in metrics.items() if v is not None}
        except Exception:
            _METRIC_CACHE[key] = {}

    return _METRIC_CACHE[key]


def _light_entry(web, marker, hit):
    """แถวย่อสำหรับสแกน (ยังไม่แนบ metrics) ไม่ใช่ log ของเครื่องนี้ -> None

    marker = "__<ชื่อ target>__" คำนวณครั้งเดียวต่อการสแกน (อ่าน puller.yaml ต่อเอกสารจะช้ามาก)
    """
    source, resource, body, filename, timestamp = _hit_fields(hit)

    if marker not in str(filename or ""):
        return None

    return {
        "id": hit.get("_id"),
        "index": hit.get("_index"),
        "timestamp": timestamp,
        "_ts": to_unix(timestamp) or 0.0,
        "web": web,
        "level": detect_log_level(body),
        "type": resource.get("log.source", "") or "application",
        "message": body,
        "source_file": filename,
    }


def _public(entry):
    """แถวที่ส่งให้หน้าเว็บ: ตัดคีย์ภายใน + แนบ metrics ตอนเกิด log"""
    out = {k: v for k, v in entry.items() if not k.startswith("_")}
    out["correlated_metrics"] = _metrics_cached(entry["timestamp"], entry["web"])
    return out


# ---------------------------------------------------------------------------
# สแกน log ของเครื่อง (ใช้ร่วมกันทุกฟังก์ชัน)
# ---------------------------------------------------------------------------

def _scan(web):
    """log ของเครื่องนี้ ใหม่ -> เก่า (ผ่าน cache สั้น ๆ) OpenSearch ล่ม -> ใช้ผลเก่าที่มี หรือ []"""
    now = time.time()
    cached = _SCAN_CACHE.get(web)

    if cached and cached[0] > now:
        return cached[1]

    entries = []
    marker = f"__{resolve_web_name(web)}__"

    try:
        for offset in range(0, SCAN_MAX_DOCS, SCAN_PAGE):
            result = opensearch_request(LOG_INDEX_PATTERN, {
                "from": offset,
                "size": min(SCAN_PAGE, SCAN_MAX_DOCS - offset),
                "_source": SOURCE_FIELDS,
                "query": {"match_all": {}},
                "sort": [{"@timestamp": {"order": "desc", "unmapped_type": "date"}}],
            })
            hits = result.get("hits", {}).get("hits", [])

            for hit in hits:
                entry = _light_entry(web, marker, hit)
                if entry:
                    entries.append(entry)

            if len(hits) < SCAN_PAGE or len(entries) >= SCAN_KEEP:
                break

    except Exception as e:
        print("[LOGS] OpenSearch query error:", repr(e))
        return cached[1] if cached else []

    # @timestamp ที่แก้แล้ว (จากต้นข้อความ/ชื่อไฟล์) อาจไม่เรียงตามที่ OpenSearch เรียงมา
    entries.sort(key=lambda e: e["_ts"], reverse=True)

    _SCAN_CACHE[web] = (now + SCAN_TTL, entries)
    return entries


def _clean_levels(levels):
    """รับ list/ข้อความ level -> set ที่ถูกต้อง หรือ None (= ไม่กรอง)"""
    if not levels:
        return None

    if isinstance(levels, str):
        levels = levels.split(",")

    wanted = {str(v).strip().upper() for v in levels}
    wanted &= set(LEVELS)

    return wanted or None


# ---------------------------------------------------------------------------
# รายการ log / รายละเอียด
# ---------------------------------------------------------------------------

def _in_range(entry, start, end):
    """เวลาของ log อยู่ในช่วง [start, end] (unix วินาที; None = ไม่จำกัดด้านนั้น)"""
    return (start is None or entry["_ts"] >= start) and (end is None or entry["_ts"] <= end)


def get_logs(web, size=200, levels=None, types=None, start=None, end=None):
    """log ล่าสุดของเครื่อง เรียงใหม่ -> เก่า ไม่เกิน size รายการ

    levels : เช่น "ERROR,WARN" หรือ ["ERROR"]  (ว่าง = ทุกระดับ)
    types  : เช่น "application,loginuser"      (ว่าง = ทุกชนิด)  = resource["log.source"]
    """
    wanted_levels = _clean_levels(levels)

    if isinstance(types, str):
        types = types.split(",")
    wanted_types = {str(t).strip() for t in (types or []) if str(t).strip()} or None

    output = []

    for entry in _scan(web):
        if not _in_range(entry, start, end):
            continue

        if wanted_levels and entry["level"] not in wanted_levels:
            continue

        if wanted_types and entry["type"] not in wanted_types:
            continue

        output.append(entry)

        if len(output) >= size:
            break

    return [_public(e) for e in output]


def get_log_detail(web, log_id):
    """log 1 รายการตาม id พร้อมรายละเอียด ไม่พบ -> None"""
    try:
        result = opensearch_request(
            LOG_INDEX_PATTERN,
            {"size": 1, "query": {"ids": {"values": [log_id]}}},
        )
        hits = result.get("hits", {}).get("hits", [])
    except Exception as e:
        print("[LOGS] detail error:", repr(e))
        hits = []

    if not hits:
        return None

    hit = hits[0]
    source, resource, body, filename, timestamp = _hit_fields(hit)

    entry = {
        "id": hit.get("_id"),
        "index": hit.get("_index"),
        "timestamp": timestamp,
        "web": web,
        "level": detect_log_level(body),
        "type": resource.get("log.source", "") or "application",
        "message": body,
        "source_file": filename,
        "correlated_metrics": _metrics_cached(timestamp, web),
    }

    system = load_system_snapshot_at(web, timestamp)

    entry["raw"] = source
    entry["correlated_metrics"] = {
        **entry["correlated_metrics"],
        "load": (
            system.get("load")
            if system.get("load") is not None
            else entry["correlated_metrics"].get("load")
        ),
        **{key: system.get(key) for key in DETAIL_SNAPSHOT_KEYS},
    }
    entry["services"] = system.get("services", [])
    entry["processes"] = system.get("processes", [])
    entry["system_events"] = system.get("system_events", [])
    entry["system_snapshot_timestamp"] = system.get("system_timestamp")

    return entry


# ---------------------------------------------------------------------------
# ตัวนับ (คำนวณจากผลสแกนเดียวกับรายการ -> ตรงกันเสมอ)
# ---------------------------------------------------------------------------

def get_log_markers(web, start, end, limit=500):
    """จุด ERROR/WARN บนเส้นเวลาของกราฟ (ไม่แนบ metrics -> เบา) ใหม่ -> เก่า ไม่เกิน limit"""
    out = []
    for entry in _scan(web):
        if entry["level"] not in ("ERROR", "WARN") or not _in_range(entry, start, end):
            continue
        out.append({
            "id": entry["id"],
            "web": entry["web"],
            "ts": entry["_ts"],
            "timestamp": entry["timestamp"],
            "level": entry["level"],
            "type": entry["type"],
            "message": str(entry["message"] or "")[:300],
        })
        if len(out) >= limit:
            break
    return out


def get_log_stats(web, start=None, end=None):
    """จำนวน log แยกตาม level และ type  -> ใช้เป็นตัวเลขบนปุ่มตัวกรอง

    {"levels": {"ERROR": 3, "WARN": 1, "INFO": 120}, "types": {"application": 90, ...}, "total": 124}
    """
    levels = {name: 0 for name in LEVELS}
    types = {}

    entries = [e for e in _scan(web) if _in_range(e, start, end)]

    for entry in entries:
        levels[entry["level"]] += 1
        types[entry["type"]] = types.get(entry["type"], 0) + 1

    return {"levels": levels, "types": types, "total": len(entries)}


def get_problem_count(web):
    """จำนวน log ระดับ ERROR + WARN (ทั้งหมดที่สแกนได้)  -> ช่อง "Problem logs" """
    return sum(1 for e in _scan(web) if e["level"] in ("ERROR", "WARN"))


def get_errors_15m(web):
    """จำนวน log ระดับ ERROR ที่เวลาของ log อยู่ใน 15 นาทีล่าสุด -> ช่อง "Errors 15m" """
    cutoff = time.time() - 15 * 60
    return sum(1 for e in _scan(web) if e["level"] == "ERROR" and e["_ts"] >= cutoff)
