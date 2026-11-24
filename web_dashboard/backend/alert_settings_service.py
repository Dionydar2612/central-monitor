"""
alert_settings_service.py - ตั้งค่าการแจ้งเตือนทั้งระบบ (หน้า Settings > Alerts)
=============================================================================

เขียนลง config/puller.yaml (puller อ่านใหม่ทุกรอบ sensor 30 วินาที ไม่ต้องรีสตาร์ต)
    sensors:
      confirm_checks: 6          # แจ้งเตือนเมื่อเกินเกณฑ์ต่อเนื่อง 6 รอบ x 30 วินาที = 3 นาที
      hysteresis: 2
      bottleneck: true
    thresholds:          {cpu: {warning, error}, ...}

ค่าปริยายต้องตรงกับ start_push_log/puller/sensor_monitor.py (DEFAULT_THRESHOLDS)
"""

import copy

import targets_service
from targets_service import THRESHOLD_KINDS, clean_thresholds

INTERVAL = 30                    # วินาทีต่อรอบตรวจ sensor (sensor_monitor.INTERVAL)
MAX_MINUTES = 60
DEFAULT_CONFIRM = 6

DEFAULT_THRESHOLDS = {
    "cpu": {"warning": 75, "error": 90}, "memory": {"warning": 80, "error": 90},
    "disk": {"warning": 80, "error": 90}, "disk_latency": {"warning": 20, "error": 50},
    "packet_loss": {"warning": 1, "error": 5}, "net_latency": {"warning": 100, "error": 250},
    "mem_available": {"warning": 20, "error": 10},
}
LABELS = {
    "cpu": "CPU %", "memory": "Memory used %", "disk": "Disk used %", "disk_latency": "Disk latency ms",
    "packet_loss": "Packet loss %", "net_latency": "Ping latency ms", "mem_available": "Available RAM % (ต่ำ = แย่)",
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(v, dict):
            for lv in ("warning", "error"):
                if lv in v:
                    try:
                        out[k][lv] = float(v[lv])
                    except (TypeError, ValueError):
                        pass
    return out


def _doc():
    y = targets_service._yaml()
    return targets_service._load_doc(y)


def get_settings():
    doc = _doc()
    s = dict(doc.get("sensors") or {})
    try:
        confirm = int(s.get("confirm_checks", DEFAULT_CONFIRM))
    except (TypeError, ValueError):
        confirm = DEFAULT_CONFIRM
    return {
        "alert_minutes": round(confirm * INTERVAL / 60, 1),
        "confirm_checks": confirm,
        "interval": INTERVAL,
        "hysteresis": float(s.get("hysteresis", 2)),
        "bottleneck": s.get("bottleneck", True) is not False,
        "thresholds": _merge(DEFAULT_THRESHOLDS, targets_service._plain(doc.get("thresholds") or {})),
        "defaults": {"thresholds": DEFAULT_THRESHOLDS, "alert_minutes": DEFAULT_CONFIRM * INTERVAL / 60},
        "kinds": list(THRESHOLD_KINDS), "labels": LABELS,
    }


def update_settings(payload):
    """payload: {alert_minutes, hysteresis, bottleneck, thresholds}
    คืน (ok, ผล | {error, fields})"""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    payload = payload or {}
    errors, sensors = {}, {}
    if "alert_minutes" in payload:
        try:
            m = float(payload["alert_minutes"])
            if not 0.5 <= m <= MAX_MINUTES:
                raise ValueError
            sensors["confirm_checks"] = max(1, round(m * 60 / INTERVAL))
        except (TypeError, ValueError):
            errors["alert_minutes"] = f"ต้องเป็นนาที 0.5-{MAX_MINUTES}"
    if "hysteresis" in payload:
        try:
            h = float(payload["hysteresis"])
            if not 0 <= h <= 20:
                raise ValueError
            sensors["hysteresis"] = int(h) if h.is_integer() else h
        except (TypeError, ValueError):
            errors["hysteresis"] = "ต้องอยู่ระหว่าง 0-20"
    if "bottleneck" in payload:
        sensors["bottleneck"] = bool(payload["bottleneck"])
    th = {}
    for key in ("thresholds",):
        if key in payload:
            th[key] = clean_thresholds(payload[key], errors, prefix=key)
    if errors:
        return False, {"error": "ค่าไม่ถูกต้อง", "fields": errors}
    if not sensors and not th:
        return False, {"error": "ไม่มีค่าที่จะบันทึก"}

    y = targets_service._yaml()
    with targets_service._locked():
        doc = targets_service._load_doc(y)
        if sensors:
            if not isinstance(doc.get("sensors"), dict):
                doc["sensors"] = CommentedMap()
            for k, v in sensors.items():
                if isinstance(v, list):
                    v = CommentedSeq(v)
                    v.fa.set_flow_style()
                doc["sensors"][k] = v
        for key, val in th.items():
            if val:
                doc[key] = targets_service._thresholds_node(val)
            elif key in doc:
                del doc[key]
        targets_service._write_doc(y, doc)
    return True, get_settings()
