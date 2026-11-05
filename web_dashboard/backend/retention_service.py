"""
retention_service.py - ตั้งอายุการเก็บข้อมูลจากหน้า Settings
=============================================================

แต่ละอย่างเก็บที่ไหน / ตั้งค่าที่ไหน
    log (ERROR/WARN)   OpenSearch index logs-central-*   ISM policy "central-log-retention" (min_index_age)
                       ค่าที่เลือกจากหน้าเว็บจำไว้ที่ data/state/retention.json
                       -> scripts/apply-ism.py (ตอน install/cm-update) ใช้ค่านี้ก่อน OPENSEARCH_LOG_RETENTION
                       เปลี่ยนแล้วผูก policy ใหม่ให้ index เดิมด้วย (change_policy) -> มีผลกับ log ที่เก็บอยู่แล้ว
    ประวัติปัญหา        data/state/incidents.json         puller.yaml storage.incident_retention
    สถานะเครื่องย้อนหลัง data/state/system/history/       puller.yaml storage.system_history_retention
    metrics            Prometheus                        อ่านอย่างเดียว (--storage.tsdb.retention.time
                                                          = PROMETHEUS_RETENTION ใน docker/.env ต้องรีสตาร์ต prometheus)

หมายเหตุ: หน้า Logs แสดงเฉพาะ log ที่มี metrics -> log ที่เก่ากว่าอายุของ Prometheus จะไม่ถูกแสดง
          แม้ OpenSearch ยังเก็บอยู่ (ค้นได้จาก OpenSearch โดยตรง)
"""

import contextlib
import json
import os
import re
import time
import urllib.request

import settings
import targets_service
from clients import opensearch_call

POLICY_ID = "central-log-retention"
POLICY_PATH = f"/_plugins/_ism/policies/{POLICY_ID}"
INDEX_PATTERN = "logs-central-*"
RETENTION_FILE = os.path.join(settings.STATE_DIR, "retention.json")

_FMT = re.compile(r"^(\d+)([hd])$")
LIMITS = {                                # (ต่ำสุด, สูงสุด) วินาที
    "log_retention": (3600, 365 * 86400),
    "incident_retention": (86400, 365 * 86400),
    "system_history_retention": (3600, 90 * 86400),
}
DEFAULTS = {"log_retention": "7d", "incident_retention": "30d", "system_history_retention": "7d"}
STORAGE_KEYS = ("incident_retention", "system_history_retention")


def _seconds(value):
    m = _FMT.match(str(value or "").strip().lower())
    if not m:
        return None
    return int(m.group(1)) * (86400 if m.group(2) == "d" else 3600)


def clean(key, value, errors):
    """ตรวจรูปแบบ เช่น 12h / 7d / 30d และช่วงที่อนุญาต -> ข้อความมาตรฐาน หรือ None (ใส่ error)"""
    v = str(value or "").strip().lower()
    sec = _seconds(v)
    lo, hi = LIMITS[key]
    if sec is None:
        errors[key] = "รูปแบบ: ตัวเลข + h หรือ d เช่น 12h, 7d, 30d"
        return None
    if not lo <= sec <= hi:
        errors[key] = f"ต้องอยู่ระหว่าง {lo // 3600}h ถึง {hi // 86400}d"
        return None
    return v


def _policy(retention):
    """ISM policy เดียวกับ start_push_log/opensearch/central-log-retention-ism-policy.json"""
    return {"policy": {
        "description": f"Delete central log indices after {retention} (set from Settings > Data retention)",
        "ism_template": [{"index_patterns": [INDEX_PATTERN], "priority": 100}],
        "default_state": "hot",
        "states": [
            {"name": "hot", "actions": [],
             "transitions": [{"state_name": "delete", "conditions": {"min_index_age": retention}}]},
            {"name": "delete", "actions": [{"delete": {}}], "transitions": []},
        ],
    }}


def _policy_retention(policy):
    for st in (policy or {}).get("states", []):
        for tr in st.get("transitions", []):
            age = (tr.get("conditions") or {}).get("min_index_age")
            if age:
                return age
    return None


def _saved():
    try:
        with open(RETENTION_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _storage():
    try:
        y = targets_service._yaml()
        doc = targets_service._load_doc(y)
        return dict(doc.get("storage") or {})
    except Exception as e:
        print("[RETENTION] read puller.yaml:", repr(e))
        return {}


def _indices():
    """สรุป index ของ log: จำนวน, ขนาดรวม, วันที่สร้าง index เก่าสุด"""
    status, data = opensearch_call(
        "GET", f"/_cat/indices/{INDEX_PATTERN}?format=json&h=index,creation.date,docs.count,store.size&bytes=b")
    if status != 200 or not isinstance(data, list):
        return None
    created = [int(i.get("creation.date") or 0) / 1000 for i in data if i.get("creation.date")]
    return {
        "count": len(data),
        "docs": sum(int(i.get("docs.count") or 0) for i in data),
        "bytes": sum(int(i.get("store.size") or 0) for i in data),
        "oldest": min(created) if created else None,
    }


def _prometheus_retention():
    try:
        with urllib.request.urlopen(settings.PROMETHEUS + "/api/v1/status/flags", timeout=5) as r:
            flags = json.loads(r.read().decode("utf-8")).get("data", {})
        return flags.get("storage.tsdb.retention.time") or None
    except Exception:
        return None


def get_settings():
    status, pol = opensearch_call("GET", POLICY_PATH)
    current = _policy_retention(pol.get("policy")) if status == 200 else None
    storage = _storage()
    return {
        "log_retention": current or _saved().get("log_retention") or os.getenv("OPENSEARCH_LOG_RETENTION") or DEFAULTS["log_retention"],
        "log_policy_found": status == 200,
        "incident_retention": str(storage.get("incident_retention") or DEFAULTS["incident_retention"]),
        "system_history_retention": str(storage.get("system_history_retention") or DEFAULTS["system_history_retention"]),
        "prometheus_retention": _prometheus_retention(),
        "indices": _indices(),
        "limits": {k: [lo, hi] for k, (lo, hi) in LIMITS.items()},
    }


def _apply_log_retention(retention):
    """อัปเดต ISM policy + ผูกให้ index เดิมใช้ policy ใหม่  คืน (ok, ข้อความ)"""
    status, existing = opensearch_call("GET", POLICY_PATH)
    if status == 200:
        status, out = opensearch_call(
            "PUT", f"{POLICY_PATH}?if_seq_no={existing['_seq_no']}&if_primary_term={existing['_primary_term']}",
            _policy(retention))
    elif status == 404:
        status, out = opensearch_call("PUT", POLICY_PATH, _policy(retention))
    else:
        return False, f"อ่าน ISM policy ไม่ได้ (HTTP {status})"
    if status not in (200, 201):
        return False, f"บันทึก ISM policy ไม่ได้ (HTTP {status}): {str(out)[:200]}"

    # index ที่มีอยู่แล้วยังผูก policy เวอร์ชันเก่า -> เปลี่ยนให้ใช้เวอร์ชันใหม่ (มีผลรอบตรวจถัดไปของ ISM ~5 นาที)
    st2, ch = opensearch_call("POST", f"/_plugins/_ism/change_policy/{INDEX_PATTERN}", {"policy_id": POLICY_ID})
    updated = ch.get("updated_indices") if st2 == 200 else None
    return True, f"อัปเดต index เดิม {updated} index" if updated is not None else "policy ใหม่มีผลกับ index ใหม่ (index เดิมเปลี่ยนไม่สำเร็จ)"


def update_settings(payload, user=""):
    """payload: {log_retention?, incident_retention?, system_history_retention?}  คืน (ok, ผล/errors)"""
    payload = payload or {}
    errors, values = {}, {}
    for key in ("log_retention",) + STORAGE_KEYS:
        if key in payload and str(payload[key]).strip():
            v = clean(key, payload[key], errors)
            if v:
                values[key] = v
    if errors:
        return False, {"error": "ค่าไม่ถูกต้อง", "fields": errors}
    if not values:
        return False, {"error": "ไม่มีค่าที่จะบันทึก"}

    notes = []
    if "log_retention" in values:
        ok, msg = _apply_log_retention(values["log_retention"])
        if not ok:
            return False, {"error": msg}
        notes.append(msg)
        saved = _saved()
        saved.update({"log_retention": values["log_retention"], "updated_at": time.time(), "updated_by": user})
        tmp = RETENTION_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(saved, f, indent=2)
        os.replace(tmp, RETENTION_FILE)

    storage_vals = {k: values[k] for k in STORAGE_KEYS if k in values}
    if storage_vals:
        y = targets_service._yaml()
        with targets_service._locked():
            doc = targets_service._load_doc(y)
            if doc.get("storage") is None:
                from ruamel.yaml.comments import CommentedMap
                doc["storage"] = CommentedMap()
            for k, v in storage_vals.items():
                doc["storage"][k] = v
            targets_service._write_doc(y, doc)
        notes.append("บันทึกลง puller.yaml แล้ว (puller ใช้ค่าใหม่ในรอบถัดไป)")

    return True, {"saved": values, "notes": notes}


with contextlib.suppress(Exception):
    os.makedirs(settings.STATE_DIR, exist_ok=True)
