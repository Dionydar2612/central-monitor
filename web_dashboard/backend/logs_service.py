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
            - รวมบรรทัดซ้ำเป็น "1 ปัญหา = 1 แถว"   group_entries / get_grouped_logs (ตาม fp จาก fingerprint.py)
          ผลสแกนเก็บ cache สั้น ๆ (SCAN_TTL วินาที) ไม่ให้ยิง OpenSearch ซ้ำทุกคำขอ

ไม่แสดง INFO
    ระบบไม่เก็บ INFO แล้ว (puller ตัดทิ้ง) แต่ใน OpenSearch อาจยังมี INFO เก่าค้างอยู่จนกว่า
    index จะหมดอายุ -> _light_entry ตัด INFO ทิ้งทุกครั้ง หน้าเว็บจึงเห็นแค่ ERROR / WARN
    INFO ดูได้จากปุ่ม "Load INFO log" -> get_info_logs (ดึงสดผ่าน puller ไม่เก็บ)

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

import log_ack_service
from clients import opensearch_request, prometheus_query_range, puller_info_logs
from fingerprint import fingerprint
from log_levels import detect_log_level
from metrics_service import _labels, get_metrics_at_time, get_web_prometheus_config
from settings import load_web_targets, resolve_web_name
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

# ระดับที่เก็บ/แสดงจาก OpenSearch (INFO ไม่เก็บ ดูได้เฉพาะตอนกด Load INFO log)
LEVELS = ("ERROR", "WARN")


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

    level = detect_log_level(body)
    if level not in LEVELS:          # INFO เก่าที่ค้างใน OpenSearch -> ไม่แสดง
        return None

    return {
        "id": hit.get("_id"),
        "index": hit.get("_index"),
        "timestamp": timestamp,
        "_ts": to_unix(timestamp) or 0.0,
        "web": web,
        "level": level,
        "type": resource.get("log.source", "") or "application",
        "message": body,
        "source_file": filename,
        "fp": fingerprint(body),     # ลายนิ้วมือ: บรรทัดที่ต่างกันแค่เวลา / ตัวเลข / IP = ปัญหาเดียวกัน
    }


# ---------------------------------------------------------------------------
# แสดงเฉพาะ log ที่มี metrics (Prometheus มีข้อมูลของเครื่อง ณ เวลานั้น)
# log ที่ไม่มี metrics = เก่ากว่าที่ Prometheus เก็บ / เครื่องที่ Prometheus ไม่ได้เก็บ / ช่วงที่ exporter ล่ม
#   -> ไม่แสดงและไม่นับเลย (ยังอยู่ใน OpenSearch จนหมดอายุตามปกติ)
# ถามว่า "นาทีไหนมีข้อมูล" ด้วย range query ครั้งเดียวต่อเครื่อง แทนการถามทีละแถว
# ---------------------------------------------------------------------------
_MINUTES_CACHE = {}           # web -> (หมดอายุ, t0, t1, set ของนาทีที่มีข้อมูล)
MINUTES_TTL = 60
PRESENCE_QUERY = "count(node_memory_MemTotal_bytes{%s})"
# Windows (agent): metrics ของ windows_exporter (รองรับชื่อใหม่ / เก่า) label cm_web="<เครื่อง>"
PRESENCE_QUERY_WINDOWS = 'count({__name__=~"windows_cpu_time_total|windows_cpu_time_seconds_total",%s})'
METRIC_KEYS = ("cpu", "memory", "disk")       # ต้องมีอย่างน้อย 1 ค่า จึงถือว่า "มี Metrics at time"


def _has_metrics(m):
    """correlated_metrics มีค่าจริง (CPU / Memory / Disk อย่างน้อย 1 ค่า)"""
    return isinstance(m, dict) and any(m.get(k) is not None for k in METRIC_KEYS)


def _metric_minutes(web, t0, t1):
    """set ของนาที (unix // 60) ในช่วง t0..t1 ที่ Prometheus มีข้อมูลของเครื่อง  None = ถาม Prometheus ไม่ได้"""
    now = time.time()
    hit = _MINUTES_CACHE.get(web)
    if hit and hit[0] > now and hit[1] <= t0 and hit[2] >= min(t1, hit[0] - MINUTES_TTL):
        return hit[3]
    config = get_web_prometheus_config(web)
    if not config:
        return set()
    t0 = int(t0 // 60 * 60)
    t1 = int(min(t1, now) // 60 * 60 + 60)
    minutes = set()
    try:
        start = t0
        while start <= t1:                          # Prometheus รับไม่เกิน 11000 จุดต่อครั้ง
            end = min(t1, start + 10000 * 60)
            query = PRESENCE_QUERY_WINDOWS if config.get("os") == "windows" else PRESENCE_QUERY
            res = prometheus_query_range(query % _labels(config), start, end, 60)
            for series in (res.get("data") or {}).get("result", []):
                minutes.update(int(float(ts)) // 60 for ts, _ in series.get("values", []))
            start = end + 60
    except Exception as e:
        print("[LOGS] metrics presence error:", repr(e))
        return hit[3] if hit else None
    _MINUTES_CACHE[web] = (now + MINUTES_TTL, t0, t1, minutes)
    return minutes


def _is_agent(web):
    return any(t.get("name") == web and str(t.get("type") or "ssh") == "agent" for t in load_web_targets())


def _with_metrics(entries, web):
    """เอาเฉพาะ log ที่มี metrics ณ เวลานั้น — log ที่แสดงทุกแถวต้องมี Metrics at time เสมอ
    ใช้กับทุกเครื่อง รวม Windows (agent: windows_exporter)
    Prometheus ถามไม่ได้และไม่มีผลเก่าใน cache -> ไม่แสดง (เดิมแสดงทั้งหมดแม้ไม่มี metrics)"""
    stamps = [e["_ts"] for e in entries if e.get("_ts")]
    if not stamps:
        return []
    minutes = _metric_minutes(web, min(stamps), max(stamps))
    if minutes is None:
        return []
    return [e for e in entries if e.get("_ts") and
            (int(e["_ts"]) // 60 in minutes or int(e["_ts"]) // 60 + 1 in minutes)]


def is_all(web):
    """web ว่าง / "all" / "*" = ทุกเครื่อง"""
    return str(web or "").strip().lower() in ("", "all", "*")


def all_webs():
    """ชื่อเครื่องทั้งหมดใน puller.yaml (ตามลำดับในไฟล์)"""
    return [str(t.get("name")) for t in load_web_targets() if t.get("name")]


def _visible(web, include_acked=False):
    """log ที่แสดงได้: มี metrics + (ยังไม่ acknowledge)   web ว่าง/"all" = รวมทุกเครื่อง ใหม่ -> เก่า"""
    if is_all(web):
        merged = []
        for w in all_webs():
            merged += _visible(w, include_acked)
        merged.sort(key=lambda e: e["_ts"], reverse=True)
        return merged
    entries = _with_metrics(_scan(web), web)
    return entries if include_acked else _not_acked(entries)


def _not_acked(entries):
    """ตัด log ที่ acknowledge แล้วออก: _id ตรง (แบบเดิม) หรือ fp ตรงและยังไม่หมดเวลา (log_ack_service.ack_of)"""
    acks = log_ack_service.load()
    if not acks:
        return entries
    now = time.time()
    return [e for e in entries if not log_ack_service.ack_of(e, acks, now)]


def _public(entry):
    """แถวที่ส่งให้หน้าเว็บ: ตัดคีย์ภายใน + แนบ metrics ตอนเกิด log (+ ข้อมูล acknowledge ถ้ามี)"""
    out = {k: v for k, v in entry.items() if not k.startswith("_")}
    ack = log_ack_service.ack_of(entry)
    if ack:
        out["ack"] = ack
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




def get_logs(web, size=200, levels=None, types=None, start=None, end=None, with_metrics=False,
             include_acked=False):
    """log ล่าสุดของเครื่อง เรียงใหม่ -> เก่า ไม่เกิน size รายการ

    levels : เช่น "ERROR,WARN" หรือ ["ERROR"]  (ว่าง = ทุกระดับ)
    types  : เช่น "application,loginuser"      (ว่าง = ทุกชนิด)  = resource["log.source"]
    with_metrics : ไม่ใช้แล้ว (คงไว้ให้เรียกแบบเดิมได้) -> แสดงเฉพาะ log ที่มี metrics เสมอ (_visible)
    """
    wanted_levels = _clean_levels(levels)

    if isinstance(types, str):
        types = types.split(",")
    wanted_types = {str(t).strip() for t in (types or []) if str(t).strip()} or None

    output = []

    for entry in _visible(web, include_acked):
        if not _in_range(entry, start, end):
            continue

        if wanted_levels and entry["level"] not in wanted_levels:
            continue

        if wanted_types and entry["type"] not in wanted_types:
            continue

        row = _public(entry)
        if not _has_metrics(row.get("correlated_metrics")):      # กันอีกชั้น: ถามค่าจริงแล้วว่างเปล่า -> ไม่แสดง
            continue
        output.append(row)

        if len(output) >= size:
            break

    return output


def _filtered(web, levels=None, types=None, start=None, end=None, include_acked=False):
    """log ที่แสดงได้ตามตัวกรองเดียวกับ get_logs (ไม่จำกัดจำนวน)"""
    wanted_levels = _clean_levels(levels)
    if isinstance(types, str):
        types = types.split(",")
    wanted_types = {str(t).strip() for t in (types or []) if str(t).strip()} or None
    return [e for e in _visible(web, include_acked)
            if _in_range(e, start, end)
            and (not wanted_levels or e["level"] in wanted_levels)
            and (not wanted_types or e["type"] in wanted_types)]


def group_entries(entries):
    """รวมบรรทัดที่ fp เดียวกัน = 1 ปัญหา  entries เรียงใหม่ -> เก่า
    คืน list ของกลุ่ม ใหม่ -> เก่า (ตามเวลาที่เห็นล่าสุด):
        fp, level (แย่สุดในกลุ่ม), sample (บรรทัดล่าสุด), count, first, last (เวลา), machines (จำนวนเครื่อง), webs, types"""
    groups = {}
    for e in entries:
        key = e.get("fp") or e.get("id")          # fp ว่าง (ข้อความว่าง) -> แถวเดี่ยว
        g = groups.get(key)
        if g is None:
            groups[key] = g = {"fp": e.get("fp") or "", "level": e["level"], "sample": e, "count": 0,
                               "_first": e, "_webs": {}, "_types": set(), "_entries": []}
        g["count"] += 1
        g["_entries"].append(e)
        if e["level"] == "ERROR":
            g["level"] = "ERROR"
        if e["_ts"] >= g["sample"]["_ts"]:
            g["sample"] = e
        if e["_ts"] <= g["_first"]["_ts"]:
            g["_first"] = e
        g["_webs"][e["web"]] = g["_webs"].get(e["web"], 0) + 1
        g["_types"].add(e["type"])
    out = []
    for g in groups.values():
        out.append({"fp": g["fp"], "level": g["level"], "sample": g["sample"], "count": g["count"],
                    "first": g["_first"]["timestamp"], "last": g["sample"]["timestamp"],
                    "_first_ts": g["_first"]["_ts"], "_last_ts": g["sample"]["_ts"],
                    "machines": len(g["_webs"]), "webs": sorted(g["_webs"], key=lambda w: -g["_webs"][w])[:20],
                    "types": sorted(g["_types"]), "_entries": g["_entries"]})
    out.sort(key=lambda g: g["_last_ts"], reverse=True)
    return out


def get_grouped_logs(web, size=200, levels=None, types=None, start=None, end=None, include_acked=False):
    """รายการ log แบบรวมกลุ่ม (1 ปัญหา = 1 แถว) ตัวกรองเหมือน get_logs  -> /api/logs/grouped"""
    out = []
    for g in group_entries(_filtered(web, levels, types, start, end, include_acked)):
        if len(out) >= size:
            break
        row = {k: v for k, v in g.items() if not k.startswith("_")}
        # ตัวอย่าง = บรรทัดล่าสุดที่มี Metrics at time (ไม่มีเลยทั้งกลุ่ม -> ไม่แสดงกลุ่มนี้)
        sample = None
        for e in g["_entries"][:20]:
            pub = _public(e)
            if _has_metrics(pub.get("correlated_metrics")):
                sample = pub
                break
        if sample is None:
            continue
        row["sample"] = sample
        fa = log_ack_service.load().get("fp:" + g["fp"]) if g["fp"] else None     # ack ทั้งกลุ่มที่ยังไม่หมดเวลา
        if fa and (fa.get("until") or 0) > time.time():
            row["ack"] = fa
        elif row["sample"].get("ack"):
            row["ack"] = row["sample"]["ack"]
        out.append(row)
    return out


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
    for entry in _visible(web):
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

    {"levels": {"ERROR": 3, "WARN": 1}, "types": {"application": 3, ...}, "total": 4}
    """
    levels = {name: 0 for name in LEVELS}
    types = {}

    in_range = [e for e in _visible(web, include_acked=True) if _in_range(e, start, end)]
    entries = _not_acked(in_range)
    acked = len(in_range) - len(entries)

    for entry in entries:
        levels[entry["level"]] += 1
        types[entry["type"]] = types.get(entry["type"], 0) + 1

    # จำนวน "ชนิด" (fp ไม่ซ้ำ) ข้างจำนวนบรรทัด: error เดียวพ่นพันบรรทัด = 1 ชนิด
    kinds = {name: len({e.get("fp") or e.get("id") for e in entries if e["level"] == name}) for name in LEVELS}
    kind_types = {}
    for t in types:
        kind_types[t] = len({e.get("fp") or e.get("id") for e in entries if e["type"] == t})

    return {"levels": levels, "types": types, "total": len(entries), "acknowledged": acked,
            "kinds": {"levels": kinds, "types": kind_types, "total": len({e.get("fp") or e.get("id") for e in entries})}}


# ---------------------------------------------------------------------------
# ค้นหาข้อความใน log (ช่อง Search ของการ์ด Logs)
# ---------------------------------------------------------------------------
# ไวยากรณ์ (simple_query_string ของ OpenSearch: พิมพ์ผิดก็ไม่ error)
#   timeout nginx        ต้องมีทุกคำ (AND)
#   "connection reset"   ทั้งวลี
#   timeout | refused    คำใดคำหนึ่ง (OR)
#   error -cron          มี error แต่ไม่มี cron
#   time*                ขึ้นต้นด้วย time
# ค้นทั้ง index (ไม่จำกัด 10,000 รายการล่าสุดแบบ _scan) แล้วกรองเครื่อง/level/type ฝั่ง Python
# ส่วนที่ตรงคำค้นถูกครอบด้วย \x02 ... \x03 (หน้าเว็บ escape ข้อความก่อน แล้วค่อยแปลงเป็น <mark>)

SEARCH_FETCH = 2000           # เอกสารสูงสุดที่ขอจาก OpenSearch ต่อการค้น 1 ครั้ง
HL_PRE, HL_POST = "\u0002", "\u0003"


def _web_of(filename):
    """ชื่อเครื่องจากชื่อไฟล์ buffer  <site>__<เครื่อง>__<source>__..."""
    parts = str(filename or "").split("__")
    return parts[1] if len(parts) > 2 else ""


def search_logs(query, web=None, levels=None, types=None, start=None, end=None, size=300):
    """ค้นข้อความใน log  web=None = ทุกเครื่อง
    คืน {"results": [...], "total": จำนวนที่ OpenSearch เจอ (ก่อนกรองเครื่อง/level), "truncated": bool}
    """
    query = str(query or "").strip()
    if not query:
        return {"results": [], "total": 0, "truncated": False}

    filters = []
    if start is not None or end is not None:
        rng = {}
        if start is not None:
            rng["gte"] = int(start * 1000)
        if end is not None:
            rng["lte"] = int(end * 1000)
        filters.append({"range": {"@timestamp": {**rng, "format": "epoch_millis"}}})

    result = opensearch_request(LOG_INDEX_PATTERN, {
        "size": SEARCH_FETCH,
        "_source": SOURCE_FIELDS,
        "track_total_hits": True,
        "sort": [{"@timestamp": {"order": "desc", "unmapped_type": "date"}}],
        "query": {"bool": {
            "must": [{"simple_query_string": {
                "query": query,
                "fields": ["body", "message"],
                "default_operator": "and",
                "analyze_wildcard": True,
                "lenient": True,
            }}],
            "filter": filters,
        }},
        "highlight": {
            "fields": {"body": {"number_of_fragments": 0}, "message": {"number_of_fragments": 0}},
            "pre_tags": [HL_PRE], "post_tags": [HL_POST],
        },
    })

    hits = result.get("hits", {}).get("hits", [])
    total = result.get("hits", {}).get("total", {})
    total = total.get("value", 0) if isinstance(total, dict) else int(total or 0)

    wanted_levels = _clean_levels(levels)
    if isinstance(types, str):
        types = types.split(",")
    wanted_types = {str(t).strip() for t in (types or []) if str(t).strip()} or None
    target = resolve_web_name(web) if web else None

    out = []
    for hit in hits:
        source, resource, body, filename, timestamp = _hit_fields(hit)
        hit_web = _web_of(filename)
        if target and hit_web != target:
            continue
        level = detect_log_level(body)
        if level not in LEVELS:
            continue
        if wanted_levels and level not in wanted_levels:
            continue
        log_type = resource.get("log.source", "") or "application"
        if wanted_types and log_type not in wanted_types:
            continue
        hl = (hit.get("highlight") or {})
        marked = (hl.get("body") or hl.get("message") or [None])[0]
        out.append({
            "id": hit.get("_id"),
            "index": hit.get("_index"),
            "timestamp": timestamp,
            "web": web or hit_web,
            "level": level,
            "type": log_type,
            "message": body,
            "highlight": marked,
            "source_file": filename,
            "correlated_metrics": {},
            "ack": log_ack_service.load().get(hit.get("_id")),
            "_ts": to_unix(timestamp) or 0.0,
        })

    # เอาเฉพาะ log ที่มี metrics (แยกตามเครื่อง) + แนบ Metrics at time ทุกแถว (ว่าง -> ไม่แสดง)
    kept = []
    for w in {e["web"] for e in out}:
        kept += _with_metrics([e for e in out if e["web"] == w], w)
    out = []
    for e in kept:
        e = {k: v for k, v in e.items() if k != "_ts"}
        e["correlated_metrics"] = _metrics_cached(e["timestamp"], e["web"])
        if _has_metrics(e["correlated_metrics"]):
            out.append(e)

    out.sort(key=lambda e: to_unix(e["timestamp"]) or 0, reverse=True)
    return {"results": out[:size], "total": total, "matched": len(out),
            "truncated": len(hits) >= SEARCH_FETCH or len(out) > size}


def get_info_logs(web, lines=500, source=None, start=None, end=None):
    """INFO สดจากเครื่อง (ผ่าน puller/ondemand.py) - แสดงอย่างเดียว ไม่เก็บ ไม่ cache

    คืน {"entries": [...แถวรูปแบบเดียวกับ get_logs...], "errors": [...]}
    puller ติดต่อไม่ได้ -> raise (api.py ตอบ 502)
    """
    params = {"target": resolve_web_name(web), "lines": int(lines)}
    if source:
        params["source"] = source
    if start is not None:
        params["start"] = int(start)
    if end is not None:
        params["end"] = int(end)

    result = puller_info_logs(params)

    entries = []
    for i, item in enumerate(result.get("entries") or []):
        entries.append({
            "id": f"info-{i}",
            "on_demand": True,
            "timestamp": item.get("timestamp"),
            "web": web,
            "level": "INFO",
            "type": item.get("type") or "application",
            "message": item.get("message", ""),
            "source_file": item.get("source_file"),
            "correlated_metrics": _metrics_cached(item.get("timestamp"), web),
        })
    # INFO ก็เช่นกัน: แสดงเฉพาะบรรทัดที่มี Metrics at time
    entries = [e for e in entries if _has_metrics(e["correlated_metrics"])]

    return {"entries": entries, "errors": result.get("errors") or [], "lines": result.get("lines")}


def get_problem_count(web, kinds=False):
    """จำนวน log ระดับ ERROR + WARN (ทั้งหมดที่สแกนได้)  -> ช่อง "Problem logs"
    kinds=True -> นับจำนวนชนิด (fp ไม่ซ้ำ) แทนจำนวนบรรทัด"""
    rows = [e for e in _visible(web) if e["level"] in ("ERROR", "WARN")]
    return len({e.get("fp") or e.get("id") for e in rows}) if kinds else len(rows)


def get_errors_15m(web, kinds=False):
    """จำนวน log ระดับ ERROR ที่เวลาของ log อยู่ใน 15 นาทีล่าสุด -> ช่อง "Errors 15m"  kinds=True -> จำนวนชนิด"""
    cutoff = time.time() - 15 * 60
    rows = [e for e in _visible(web) if e["level"] == "ERROR" and e["_ts"] >= cutoff]
    return len({e.get("fp") or e.get("id") for e in rows}) if kinds else len(rows)
