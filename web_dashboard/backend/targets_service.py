"""
targets_service.py - เพิ่ม/แก้/ลบ target ใน config/puller.yaml จากหน้า Settings
==============================================================================

หน้าที่
    อ่านและเขียน "targets:" ใน puller.yaml โดย
      - คงคอมเมนต์/การจัดบรรทัดเดิมของไฟล์ (ใช้ ruamel.yaml แบบ round-trip)
      - ตรวจค่าทุกช่องอย่างเข้ม (ค่า path/user/host ถูกส่งต่อไปเป็นคำสั่ง SSH
        ฝั่ง puller จึงห้ามมีอักขระพิเศษของ shell เด็ดขาด)
      - เขียนแบบ atomic + สำรองไฟล์เดิมเป็น puller.yaml.bak + คงเจ้าของ/สิทธิ์ไฟล์
      - ล็อกไฟล์ (flock) กัน gunicorn 2 worker เขียนชนกัน
      - สร้าง data/prometheus/web-nodes.json ใหม่ทันที (Prometheus เห็นเครื่องใหม่เลย)
      - ขอให้ puller ดึงเครื่องที่เพิ่ง เพิ่ม/แก้ ทันที (ไฟล์ data/state/pull-now.json)
      - สแกน/เก็บ host key ลง keys/known_hosts (ผู้ใช้ต้องยืนยัน fingerprint ก่อน)

      - วาง public key ลงเครื่อง target ให้อัตโนมัติ (deploy_key: เข้าด้วยรหัสผ่านครั้งเดียว ไม่เก็บรหัส)

ใครเรียก : api.py (/api/targets*)
อ่าน/เขียน: config/puller.yaml (ต้อง mount แบบเขียนได้), keys/known_hosts, data/*
"""

import base64
import contextlib
import fcntl
import hashlib
import io
import ipaddress
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.parse

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

import cm_secrets
import settings

MAX_TARGETS = 200
MAX_LOGS_PER_TARGET = 30


class ValidationError(Exception):
    """ข้อมูลที่ส่งมาไม่ถูกต้อง  .fields = {ชื่อช่อง: ข้อความ}"""

    def __init__(self, fields, status=400):
        super().__init__("validation")
        self.fields = fields
        self.status = status


# ------------------------------------------------------------------
# กฎตรวจค่า (เข้มโดยตั้งใจ: path/user/host ไปอยู่ในคำสั่ง SSH ฝั่ง puller)
# ------------------------------------------------------------------

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")
JOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
INSTANCE_RE = re.compile(r"^([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\]):\d{1,5}$")
LOGNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
LOGTYPE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# path: ขึ้นต้น / ใช้ได้เฉพาะอักขระปลอดภัย (ห้าม ' " $ ` ; | & ช่องว่าง ขึ้นบรรทัดใหม่)
PATH_RE = re.compile(r"^/[A-Za-z0-9._/*?+@%=:,~\[\]-]{1,300}$")
WILDCARDS = "*?["
# กลุ่มของเครื่อง: path คั่นด้วย / ลึกได้ 10 ชั้น เช่น "TQ/IT" หรือ "ThaiQuest/TQ/PP/Server" (ไทยได้)
GROUP_PART = r"[A-Za-z0-9฀-๿ _.()&+-]{1,40}"
GROUP_RE = re.compile(rf"^{GROUP_PART}(?:/{GROUP_PART}){{0,9}}$")
GROUP_MAX_DEPTH = 10
TARGET_TYPES = ("ssh", "winrm", "agent")   # winrm = Windows ไม่ลง agent (windows_pull.py) · agent = Windows ลง cm-agent (agent_bridge.py)
WINPATH_RE = re.compile(r'^[A-Za-z]:\\[^\x00-\x1f"<>|:]{1,300}$')
WINUSER_RE = re.compile(r"^(?:[A-Za-z0-9._-]{1,15}\\)?[A-Za-z0-9 _.@-]{1,64}$")
THUMB_RE = re.compile(r"^[0-9A-F]{40}$")
EVENTLOG_RE = re.compile(r"^[A-Za-z0-9 ._/-]{1,200}$")
DEFAULT_EVENT_LOGS = ["System", "Application"]


PRIORITY_DEFAULT = 3


def clean_priority(value, errors):
    """ความสำคัญของเครื่อง (แบบ PRTG) 1-5 ดาว ปริยาย 3 · 5 = สำคัญสุด (ขึ้นก่อนในมุมมองสไลด์)"""
    if value in (None, ""):
        return PRIORITY_DEFAULT
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = 0
    if not 1 <= v <= 5:
        errors["priority"] = "เลือก 1-5"
        return PRIORITY_DEFAULT
    return v


def clean_pause(data, paused, errors):
    """เหตุผลที่ Pause (บังคับเมื่อ Pause) -> {reason, at, by} หรือ None"""
    if not paused:
        return None
    reason = str(data.get("pause_reason") or "").strip()[:200]
    if not reason:
        errors["pause_reason"] = "ใส่เหตุผลที่ Pause (เช่น ปิดซ่อมบำรุง, ย้ายเครื่อง)"
        return None
    try:
        at = float(data.get("paused_at")) if data.get("paused_at") not in (None, "") else time.time()
    except (TypeError, ValueError):
        at = time.time()
    out = {"reason": reason, "at": int(at)}
    if data.get("paused_by"):
        out["by"] = str(data["paused_by"])[:60]
    return out


def clean_group(value, errors, field="group"):
    """"  TQ / IT " -> "TQ/IT"  ว่าง -> ""  ผิดรูปแบบ -> ใส่ errors"""
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        errors[field] = "ต้องเป็นข้อความ"
        return ""
    parts = [p.strip() for p in value.strip().strip("/").split("/")]
    group = "/".join(p for p in parts if p)
    if group and not GROUP_RE.match(group):
        errors[field] = "กลุ่มคั่นชั้นด้วย / (ไม่เกิน 10 ชั้น) ใช้ตัวอักษร ตัวเลข ไทย ช่องว่าง _ . ( ) & + -"
    return group


THRESHOLD_KINDS = ("cpu", "memory", "disk", "disk_latency", "packet_loss", "net_latency", "mem_available", "time_offset")
# (ต่ำสุด, สูงสุด, หน่วย)  mem_available = RAM ว่างเหลือ % (ยิ่งต่ำยิ่งแย่ -> warning ต้องมากกว่า error)
THRESHOLD_RANGES = {
    "cpu": (1, 100, "%"), "memory": (1, 100, "%"), "disk": (1, 100, "%"),
    "disk_latency": (1, 10000, "ms"), "packet_loss": (0.1, 100, "%"), "net_latency": (1, 10000, "ms"),
    "mem_available": (1, 99, "%"), "time_offset": (10, 600000, "ms"),
}
LOW_IS_BAD = ("mem_available",)
SERVICE_RE = re.compile(r"^[A-Za-z0-9_.$@ -]{1,80}$")


def clean_thresholds(value, errors, prefix="thresholds"):
    """เกณฑ์ {cpu: {warning, error}, ...} ช่องว่าง = ใช้ค่าทั้งระบบ  คืน dict (อาจว่าง)"""
    out = {}
    if not isinstance(value, dict):
        return out
    for kind in THRESHOLD_KINDS:
        item = value.get(kind) or {}
        if not isinstance(item, dict):
            continue
        lo, hi, unit = THRESHOLD_RANGES[kind]
        clean = {}
        for lv in ("warning", "error"):
            v = item.get(lv)
            if v in (None, ""):
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                errors[f"{prefix}.{kind}.{lv}"] = f"ต้องเป็นตัวเลข {lo:g}-{hi:g}"
                continue
            if not lo <= v <= hi:
                errors[f"{prefix}.{kind}.{lv}"] = f"ต้องอยู่ระหว่าง {lo:g}-{hi:g} {unit}"
                continue
            clean[lv] = int(v) if v.is_integer() else v
        if "warning" in clean and "error" in clean:
            if kind in LOW_IS_BAD and clean["warning"] <= clean["error"]:
                errors[f"{prefix}.{kind}.warning"] = "RAM ว่าง: warning ต้องมากกว่า error (เช่น 20 / 10)"
            elif kind not in LOW_IS_BAD and clean["warning"] >= clean["error"]:
                errors[f"{prefix}.{kind}.warning"] = "warning ต้องน้อยกว่า error"
        if clean:
            out[kind] = clean
    return out


def clean_alert(value, errors, prefix="alert"):
    """ค่าแจ้งเตือนเฉพาะเครื่อง {minutes, hysteresis, bottleneck}  ช่องว่าง/None = ใช้ค่าทั้งระบบ  คืน dict (อาจว่าง)"""
    out = {}
    if not isinstance(value, dict):
        return out
    for key, lo, hi in (("minutes", 0.5, 1440), ("hysteresis", 0, 20)):
        v = value.get(key)
        if v in (None, ""):
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            errors[f"{prefix}.{key}"] = f"ต้องเป็นตัวเลข {lo:g}-{hi:g}"
            continue
        if not lo <= v <= hi:
            errors[f"{prefix}.{key}"] = f"ต้องอยู่ระหว่าง {lo:g}-{hi:g}"
            continue
        out[key] = int(v) if v.is_integer() else v
    b = value.get("bottleneck")
    if b in (True, False):
        out["bottleneck"] = b
    elif b not in (None, ""):
        errors[f"{prefix}.bottleneck"] = "เลือก ตรวจ / ไม่ตรวจ / ค่าทั้งระบบ"
    # sensor ที่ปิดไว้ (เช่น ping ไปไม่ถึงเครื่อง): ส่วนท้ายของ sensor id เช่น net_latency, packet_loss, disk:C:
    #   หรือชนิด (kind) หรือขึ้นต้นแล้วตามด้วย * เช่น bottleneck:*  -> puller แสดงเป็น Paused ไม่แจ้งเตือน
    ds = value.get("disabled_sensors")
    if isinstance(ds, str):
        ds = [x for x in ds.split(",")]
    if ds:
        clean = []
        for x in ds if isinstance(ds, list) else []:
            x = str(x or "").strip()
            if not x:
                continue
            if not SENSOR_KEY_RE.match(x):
                errors[f"{prefix}.disabled_sensors"] = f"ชื่อ sensor ไม่ถูกต้อง: {x[:60]}"
                continue
            if x not in clean:
                clean.append(x)
        if clean:
            out["disabled_sensors"] = clean[:100]
    # ค่าเฉพาะ sensor: {"<sensor>": {"priority": 1-5, "minutes": นานกี่นาทีก่อนแจ้ง}}
    #   ชื่อ sensor แบบเดียวกับ disabled_sensors · ระหว่างยังไม่ครบเวลา = บันทึกใน System log แต่ไม่แจ้ง
    #   priority สูง = แจ้งก่อนเสมอเมื่อเกิดพร้อมกัน (ไม่ใส่ = Priority ของเครื่อง)
    out_s = clean_sensor_settings(value.get("sensors"), errors, f"{prefix}.sensors")
    if out_s:
        out["sensors"] = out_s
    return out


def clean_sensor_one(v, errors, field):
    """{"priority", "minutes", "warning", "error", "notify"} ของ sensor เดียว  ช่องว่าง = ไม่ตั้ง  คืน dict (อาจว่าง)
    warning / error = เกณฑ์แจ้งเตือนของ sensor นี้ (แทนค่าของเครื่อง) · notify: "" ปกติ / "error" แจ้งเฉพาะ Error-Down / "off" ไม่แจ้ง (บันทึก log อย่างเดียว)"""
    o = {}
    if not isinstance(v, dict):
        return o
    p = v.get("priority")
    if p not in (None, ""):
        try:
            p = int(p)
        except (TypeError, ValueError):
            p = 0
        if 1 <= p <= 5:
            o["priority"] = p
        else:
            errors[f"{field}.priority"] = "เลือก 1-5"
    m = v.get("minutes")
    if m not in (None, ""):
        try:
            m = float(m)
        except (TypeError, ValueError):
            m = -1
        if 0.5 <= m <= 1440:
            o["minutes"] = int(m) if m.is_integer() else m
        else:
            errors[f"{field}.minutes"] = "ต้องอยู่ระหว่าง 0.5-1440 นาที"
    for k in ("warning", "error"):
        x = v.get(k)
        if x in (None, ""):
            continue
        try:
            x = float(x)
        except (TypeError, ValueError):
            x = None
        if x is None or not (-1e9 <= x <= 1e9) or x != x:
            errors[f"{field}.{k}"] = "ใส่ตัวเลข"
        else:
            o[k] = int(x) if x.is_integer() else x
    if "warning" in o and "error" in o:
        low = bool(v.get("low"))
        if (o["warning"] < o["error"]) if low else (o["warning"] > o["error"]):
            errors[f"{field}.warning"] = "warning ต้องสูงกว่า error (ค่ายิ่งต่ำยิ่งแย่)" if low else "warning ต้องไม่เกิน error"
    n = str(v.get("notify") or "").strip()
    if n:
        if n in ("error", "off"):
            o["notify"] = n
        else:
            errors[f"{field}.notify"] = "เลือกวิธีแจ้ง"
    return o


def clean_sensor_settings(value, errors, field="alert.sensors"):
    out = {}
    if not isinstance(value, dict):
        return out
    for k, v in list(value.items())[:200]:
        k = str(k or "").strip()
        if not SENSOR_KEY_RE.match(k):
            errors[field] = f"ชื่อ sensor ไม่ถูกต้อง: {k[:60]}"
            continue
        sub = {}
        o = clean_sensor_one(v, sub, k)
        if sub:
            errors[field] = f"{k}: " + " · ".join(sub.values())
        if o:
            out[k] = o
    return out


SENSOR_KEY_RE = re.compile(r"^[A-Za-z0-9_.:$@ /\\()&+-]{1,120}\*?$")


def set_sensor_disabled(name, sensor, disabled):
    """ปิด / เปิด sensor เดียวของเครื่อง (targets[].alert.disabled_sensors)  ไม่มีเครื่อง -> None"""
    sensor = str(sensor or "").strip()
    if not SENSOR_KEY_RE.match(sensor):
        raise ValidationError({"sensor": "ชื่อ sensor ไม่ถูกต้อง"})
    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        node = _find_target(doc, name)
        if node is None:
            return None
        alert = dict(_plain(node.get("alert") or {}))
        cur = [str(x) for x in alert.get("disabled_sensors") or []]
        if disabled and sensor not in cur:
            cur.append(sensor)
        if not disabled:
            cur = [x for x in cur if x != sensor]
        if cur:
            alert["disabled_sensors"] = cur
        else:
            alert.pop("disabled_sensors", None)
        _set_or_drop(node, "alert", _flow(alert) if alert else None)
        _write_doc(y, doc)
    return {"name": name, "disabled_sensors": cur}


def set_sensor_settings(name, sensor, priority=None, minutes=None, warning=None, error=None, notify=None, low=False):
    """Priority / นานกี่นาทีก่อนแจ้ง ของ sensor เดียว (targets[].alert.sensors)  ว่างทั้งคู่ = ลบ  ไม่มีเครื่อง -> None"""
    sensor = str(sensor or "").strip()
    errors = {}
    if not SENSOR_KEY_RE.match(sensor):
        errors["sensor"] = "ชื่อ sensor ไม่ถูกต้อง"
    one = clean_sensor_one({"priority": priority, "minutes": minutes, "warning": warning, "error": error,
                            "notify": notify, "low": low}, errors, "sensor")
    if errors:
        raise ValidationError({k.replace("sensor.", ""): v for k, v in errors.items()})
    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        node = _find_target(doc, name)
        if node is None:
            return None
        alert = _plain(node.get("alert") or {})
        alert = dict(alert) if isinstance(alert, dict) else {}
        cur = dict(alert.get("sensors") or {})
        if one:
            cur[sensor] = one
        else:
            cur.pop(sensor, None)
        if cur:
            alert["sensors"] = cur
        else:
            alert.pop("sensors", None)
        _set_or_drop(node, "alert", _flow(alert) if alert else None)
        _write_doc(y, doc)
    return {"name": name, "sensor": sensor, "settings": one, "sensors": cur}


# systemd journal แทนไฟล์: "journal" หรือ "journal:nginx.service,ssh.service" (ดู puller/journal.py)
JOURNAL_RE = re.compile(r"^journal(?::[A-Za-z0-9@._:-]{1,128}(?:,[A-Za-z0-9@._:-]{1,128}){0,9})?$")


def _valid_host(host):
    if HOST_RE.match(host):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _as_int_port(value, field, errors, default=None):
    if value in (None, ""):
        return default
    try:
        port = int(value)
    except (TypeError, ValueError):
        errors[field] = "ต้องเป็นตัวเลข 1-65535"
        return None
    if not 1 <= port <= 65535:
        errors[field] = "ต้องเป็นตัวเลข 1-65535"
        return None
    return port


def _text(data, key):
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def is_agent(t):
    """เครื่อง Windows แบบ cm-agent (type: agent): Fluent Bit + windows_exporter ส่งข้อมูลเข้า central-agent-gw :4318"""
    return isinstance(t, dict) and str(t.get("type") or "ssh") == "agent"


# cm-agent: ค่าที่ตั้งก่อนติดตั้ง (ยิ่งนาน = เครื่องยิ่งเบา) -> (ค่าปริยาย, ต่ำสุด, สูงสุด)
AGENT_INTERVALS = {
    "metrics_interval": (60, 15, 600),       # วินาที อ่าน windows_exporter
    "heartbeat_interval": (60, 15, 600),     # วินาที สัญญาณ "ยังทำงาน"
    "event_interval": (30, 5, 600),          # วินาที อ่าน Event Log
    "log_refresh": (60, 10, 600),            # วินาที หาไฟล์ log ใหม่ / หมุนไฟล์
    "flush": (15, 1, 120),                   # วินาที ส่งข้อมูลที่พักไว้
    "inventory_minutes": (60, 15, 1440),     # นาที รายการโปรแกรม / พอร์ต
}
AGENT_FEATURES = {"metrics": True, "logs": True, "software": True, "processes": False}


def agent_defaults():
    return {**{k: v[0] for k, v in AGENT_INTERVALS.items()}, "features": dict(AGENT_FEATURES),
            "event_logs": list(DEFAULT_EVENT_LOGS), "port": 4318, "central": "", "skip_cert": False}


def _clean_event_logs(evlogs, errors, field):
    if isinstance(evlogs, str):
        evlogs = [x.strip() for x in evlogs.split(",")]
    evlogs = [str(x).strip() for x in (evlogs if isinstance(evlogs, list) else DEFAULT_EVENT_LOGS) if str(x).strip()]
    if len(evlogs) > 10 or any(not EVENTLOG_RE.match(x) for x in evlogs):
        errors[field] = "ชื่อ Event log คั่นด้วย , (ไม่เกิน 10) เช่น System, Application"
    return evlogs


def _clean_win_logs(data, errors):
    logs_in = data.get("logs") if isinstance(data.get("logs"), list) else []
    logs = []
    if len(logs_in) > MAX_LOGS_PER_TARGET:
        errors["logs"] = f"เพิ่มได้ไม่เกิน {MAX_LOGS_PER_TARGET} ไฟล์ต่อเครื่อง"
    for i, item in enumerate(logs_in[:MAX_LOGS_PER_TARGET]):
        item = item if isinstance(item, dict) else {}
        l_name, l_path = _text(item, "name"), _text(item, "path")
        l_type = _text(item, "type") or "application"
        if not l_path and not l_name:
            continue
        prefix = f"logs.{i}."
        if not LOGNAME_RE.match(l_name):
            errors[prefix + "name"] = "ใช้ A-Z a-z 0-9 _ - เท่านั้น"
        folder = l_path.rsplit("\\", 1)[0]
        if not WINPATH_RE.match(l_path) or ".." in l_path.split("\\") or l_path.endswith("\\"):
            errors[prefix + "path"] = "path ของ Windows เช่น D:\\App\\logs\\app.log หรือ D:\\App\\logs\\*.log"
        elif any(ch in folder for ch in "*?"):
            errors[prefix + "path"] = "wildcard ใช้ได้เฉพาะชื่อไฟล์ (ส่วนท้าย) ไม่ใช่ชื่อโฟลเดอร์"
        if not LOGTYPE_RE.match(l_type):
            errors[prefix + "type"] = "ใช้ A-Z a-z 0-9 _ - เท่านั้น"
        logs.append({"name": l_name, "path": l_path, "type": l_type})
    return logs


def validate_agent(data, existing_names, editing=None):
    """เครื่อง Windows แบบ cm-agent (type: agent)  ไม่มีรหัสผ่านเก็บที่ central (ติดตั้งด้วย admin ครั้งเดียว / zip)"""
    errors = {}
    name = editing or _text(data, "name")
    if not NAME_RE.match(name):
        errors["name"] = "ใช้ a-z 0-9 และ - เท่านั้น (ขึ้นต้นด้วยตัวอักษร/ตัวเลข ยาวไม่เกิน 40)"
    elif editing is None and name in existing_names:
        errors["name"] = "มีชื่อนี้อยู่แล้ว"
    host = _text(data, "host")
    if not _valid_host(host):
        errors["host"] = "ต้องเป็น IP หรือชื่อโดเมนที่ถูกต้อง (ใช้ติดตั้งผ่าน WinRM และ ping)"
    a = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    out = agent_defaults()
    for k, (_, lo, hi) in AGENT_INTERVALS.items():
        if a.get(k) in (None, ""):
            continue
        try:
            v = int(float(a[k]))
            if not lo <= v <= hi:
                raise ValueError
            out[k] = v
        except (TypeError, ValueError):
            errors[f"agent.{k}"] = f"{lo}-{hi}"
    f = a.get("features") if isinstance(a.get("features"), dict) else {}
    out["features"] = {k: bool(f.get(k, v)) for k, v in AGENT_FEATURES.items()}
    if out["features"]["software"] and not out["features"]["metrics"]:
        errors["agent.features.software"] = "Software ต้องเปิด Metrics ด้วย (ส่งผ่าน windows_exporter)"
    if not any(out["features"][k] for k in ("metrics", "logs")):
        errors["agent.features.metrics"] = "เปิดอย่างน้อย Metrics หรือ Logs"
    out["event_logs"] = _clean_event_logs(a.get("event_logs"), errors, "agent.event_logs")
    out["port"] = _as_int_port(a.get("port"), "agent.port", errors, default=4318)
    central = _text(a, "central")
    if central and not _valid_host(central):
        errors["agent.central"] = "IP / ชื่อของ central ที่เครื่อง Windows เห็น (เว้นว่าง = ตรวจหาให้ตอนติดตั้ง)"
    out["central"] = central
    out["skip_cert"] = bool(a.get("skip_cert"))
    logs = _clean_win_logs(data, errors)
    group = clean_group(data.get("group"), errors)
    priority = clean_priority(data.get("priority"), errors)
    paused = bool(data.get("paused", False))
    pause = clean_pause(data, paused, errors)
    thresholds = clean_thresholds(data.get("thresholds"), errors)
    alert = clean_alert(data.get("alert"), errors)
    if errors:
        raise ValidationError(errors)
    return {
        "name": name, "type": "agent", "host": host, "group": group, "priority": priority,
        "paused": paused, "pause": pause, "thresholds": thresholds, "alert": alert,
        "prometheus": {"job": f"{name}-agent", "instance": f"agent:{name}"},
        "agent": out, "logs": logs,
    }


def norm_thumbprint(value):
    return "".join(ch for ch in str(value or "") if ch.isalnum()).upper()


def validate_winrm(data, existing_names, editing=None):
    """เครื่อง Windows แบบไม่ลง agent (type: winrm)  รหัสผ่านไม่อยู่ในผลลัพธ์ (เก็บแยกด้วย cm_secrets)"""
    errors = {}
    name = editing or _text(data, "name")
    if not NAME_RE.match(name):
        errors["name"] = "ใช้ a-z 0-9 และ - เท่านั้น (ขึ้นต้นด้วยตัวอักษร/ตัวเลข ยาวไม่เกิน 40)"
    elif editing is None and name in existing_names:
        errors["name"] = "มีชื่อนี้อยู่แล้ว"
    host = _text(data, "host")
    if not _valid_host(host):
        errors["host"] = "ต้องเป็น IP หรือชื่อโดเมนที่ถูกต้อง"
    w = data.get("winrm") if isinstance(data.get("winrm"), dict) else {}
    port = _as_int_port(w.get("port"), "winrm.port", errors, default=5986)
    user = _text(w, "user") or "cm-monitor"
    if not WINUSER_RE.match(user):
        errors["winrm.user"] = "ชื่อ user ไม่ถูกต้อง (เช่น cm-monitor หรือ DOMAIN\\cm-monitor)"
    thumb = norm_thumbprint(w.get("thumbprint"))
    if thumb and not THUMB_RE.match(thumb):            # ว่างได้: ติดตั้งจากหน้าเว็บ / zip -> ระบบตั้งให้เอง
        errors["winrm.thumbprint"] = "Thumbprint ต้องยาว 40 ตัวอักษร (หรือเว้นว่างให้ระบบตั้งให้)"
    evlogs = w.get("event_logs")
    if isinstance(evlogs, str):
        evlogs = [x.strip() for x in evlogs.split(",")]
    evlogs = [str(x).strip() for x in (evlogs if isinstance(evlogs, list) else DEFAULT_EVENT_LOGS) if str(x).strip()]
    if len(evlogs) > 10 or any(not EVENTLOG_RE.match(x) for x in evlogs):
        errors["winrm.event_logs"] = "ชื่อ Event log คั่นด้วย , (ไม่เกิน 10) เช่น System, Application"
    password = data.get("password")
    password = password if isinstance(password, str) else ""
    if len(password) > 128:
        errors["password"] = "รหัสผ่านยาวเกินไป"
    elif password and len(password) < 8:
        errors["password"] = "รหัสผ่านสั้นเกินไป (เว้นว่างได้: ติดตั้งจากหน้าเว็บ / ชุดติดตั้งที่ดาวน์โหลดตั้งให้เอง)"

    logs_in = data.get("logs") if isinstance(data.get("logs"), list) else []
    logs = []
    if len(logs_in) > MAX_LOGS_PER_TARGET:
        errors["logs"] = f"เพิ่มได้ไม่เกิน {MAX_LOGS_PER_TARGET} ไฟล์ต่อเครื่อง"
    for i, item in enumerate(logs_in[:MAX_LOGS_PER_TARGET]):
        item = item if isinstance(item, dict) else {}
        l_name, l_path = _text(item, "name"), _text(item, "path")
        l_type = _text(item, "type") or "application"
        if not l_path and not l_name:
            continue
        prefix = f"logs.{i}."
        if not LOGNAME_RE.match(l_name):
            errors[prefix + "name"] = "ใช้ A-Z a-z 0-9 _ - เท่านั้น"
        folder = l_path.rsplit("\\", 1)[0]
        if not WINPATH_RE.match(l_path) or ".." in l_path.split("\\") or l_path.endswith("\\"):
            errors[prefix + "path"] = "path ของ Windows เช่น D:\\App\\logs\\app.log หรือ D:\\App\\logs\\*.log"
        elif any(ch in folder for ch in "*?"):
            errors[prefix + "path"] = "wildcard ใช้ได้เฉพาะชื่อไฟล์ (ส่วนท้าย) ไม่ใช่ชื่อโฟลเดอร์"
        if not LOGTYPE_RE.match(l_type):
            errors[prefix + "type"] = "ใช้ A-Z a-z 0-9 _ - เท่านั้น"
        enc = _text(item, "encoding")
        entry = {"name": l_name, "path": l_path, "type": l_type}
        if enc in ("unicode", "default"):
            entry["encoding"] = enc
        logs.append(entry)

    group = clean_group(data.get("group"), errors)
    priority = clean_priority(data.get("priority"), errors)
    paused = bool(data.get("paused", False))
    pause = clean_pause(data, paused, errors)
    thresholds = clean_thresholds(data.get("thresholds"), errors)
    alert = clean_alert(data.get("alert"), errors)
    if errors:
        raise ValidationError(errors)
    instance = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return {
        "name": name, "type": "winrm", "host": host, "group": group, "priority": priority,
        "paused": paused, "pause": pause, "thresholds": thresholds, "alert": alert,
        "prometheus": {"job": f"{name}-win", "instance": instance},
        "winrm": {"port": port, "user": user, "thumbprint": thumb, "event_logs": evlogs},
        "logs": logs,
        "_password": password,
    }


def is_winrm(t):
    return isinstance(t, dict) and str(t.get("type") or "ssh") == "winrm"


def winrm_prometheus_entry(web, environment):
    """ต้องตรงกับ start_push_log/puller/prometheus_targets.py: winrm_entry (ใช้กับ type winrm และ agent)"""
    prom = web.get("prometheus") or {}
    if is_agent(web) and web.get("name") and not prom.get("job"):
        prom = {"job": f"{web['name']}-agent", "instance": f"agent:{web['name']}"}
    if not web.get("name") or not prom.get("job") or not prom.get("instance"):
        return None
    return {"targets": [os.environ.get("CENTRAL_WINRM_EXPORTER", "central-puller:9110")],
            "labels": {"job": prom["job"], "instance": prom["instance"], "web": web["name"], "environment": environment,
                       "__metrics_path__": f"/win/{web['name']}"}}


def validate_target(data, existing_names, editing=None):
    """ตรวจ + จัดรูปข้อมูล target หนึ่งเครื่อง  คืน dict ที่พร้อมเขียนลง yaml
    ผิด -> raise ValidationError พร้อมข้อความรายช่อง"""
    errors = {}
    if not isinstance(data, dict):
        raise ValidationError({"_": "invalid body"})
    if str(data.get("type") or "ssh") not in TARGET_TYPES:
        raise ValidationError({"type": "ชนิดเครื่องต้องเป็น ssh (Linux), winrm (Windows ไม่ลง agent) หรือ agent (Windows cm-agent)"})
    if str(data.get("type") or "ssh") == "winrm":
        return validate_winrm(data, existing_names, editing)
    if str(data.get("type") or "ssh") == "agent":
        return validate_agent(data, existing_names, editing)

    name = editing or _text(data, "name")
    if not NAME_RE.match(name):
        errors["name"] = "ใช้ a-z 0-9 และ - เท่านั้น (ขึ้นต้นด้วยตัวอักษร/ตัวเลข ยาวไม่เกิน 40)"
    elif editing is None and name in existing_names:
        errors["name"] = "มีชื่อนี้อยู่แล้ว"

    host = _text(data, "host")
    if not _valid_host(host):
        errors["host"] = "ต้องเป็น IP หรือชื่อโดเมนที่ถูกต้อง"

    ssh = data.get("ssh") if isinstance(data.get("ssh"), dict) else {}
    ssh_port = _as_int_port(ssh.get("port"), "ssh.port", errors, default=22)
    ssh_user = _text(ssh, "user")
    if not USER_RE.match(ssh_user):
        errors["ssh.user"] = "ชื่อ user ไม่ถูกต้อง (a-z A-Z 0-9 _ . -)"

    prom = data.get("prometheus") if isinstance(data.get("prometheus"), dict) else {}
    job = _text(prom, "job") or (f"{name}-node" if NAME_RE.match(name) else "")
    if not JOB_RE.match(job):
        errors["prometheus.job"] = "ใช้ A-Z a-z 0-9 _ . - เท่านั้น"

    instance = _text(prom, "instance")
    scheme = (_text(prom, "scheme") or "http").lower()
    metrics_path = _text(prom, "metrics_path") or "/metrics"
    # ใส่ URL เต็มได้ เช่น https://web.com/metrics -> instance web.com:443, scheme https, path /metrics
    if instance.lower().startswith(("http://", "https://")):
        try:
            u = urllib.parse.urlsplit(instance)
            scheme = u.scheme.lower()
            metrics_path = u.path if u.path not in ("", "/") else "/metrics"
            h = u.hostname or ""
            port = u.port or (443 if scheme == "https" else 80)
            instance = f"[{h}]:{port}" if ":" in h else f"{h}:{port}"
        except ValueError:
            pass        # ปล่อยให้ INSTANCE_RE แจ้ง error
    if not instance and _valid_host(host):
        instance = f"[{host}]:9100" if ":" in host else f"{host}:9100"
    if not INSTANCE_RE.match(instance) or int(instance.rsplit(":", 1)[1]) > 65535:
        errors["prometheus.instance"] = "ใส่ host:port เช่น 10.0.10.11:9100 หรือ URL เช่น https://web.com/metrics"
    if scheme not in ("http", "https"):
        errors["prometheus.scheme"] = "ใช้ http หรือ https"
    if not re.match(r"^/[A-Za-z0-9._/-]*$", metrics_path):
        errors["prometheus.metrics_path"] = "ต้องขึ้นต้นด้วย / เช่น /metrics"

    logs_in = data.get("logs")
    logs = []
    if not isinstance(logs_in, list):
        logs_in = []
    if len(logs_in) > MAX_LOGS_PER_TARGET:
        errors["logs"] = f"เพิ่มได้ไม่เกิน {MAX_LOGS_PER_TARGET} ไฟล์ต่อเครื่อง"
    for i, item in enumerate(logs_in[:MAX_LOGS_PER_TARGET]):
        item = item if isinstance(item, dict) else {}
        l_name = _text(item, "name")
        l_path = _text(item, "path")
        l_type = _text(item, "type") or "application"
        prefix = f"logs.{i}."
        if not LOGNAME_RE.match(l_name):
            errors[prefix + "name"] = "ใช้ A-Z a-z 0-9 _ - เท่านั้น"
        if JOURNAL_RE.match(l_path):
            pass        # journal (journalctl) ไม่ใช่ไฟล์
        elif l_path.startswith("journal"):
            errors[prefix + "path"] = "journal ใช้รูปแบบ journal หรือ journal:nginx.service,ssh.service"
        elif not PATH_RE.match(l_path) or ".." in l_path.split("/"):
            errors[prefix + "path"] = "path ต้องขึ้นต้นด้วย / (หรือ journal) ใช้ได้เฉพาะ A-Z a-z 0-9 . _ / * ? - (ไม่มีช่องว่าง/เครื่องหมายคำพูด)"
        elif any(ch in os.path.dirname(l_path) for ch in WILDCARDS):
            errors[prefix + "path"] = "wildcard ใช้ได้เฉพาะชื่อไฟล์ (ส่วนท้าย) ไม่ใช่ชื่อโฟลเดอร์"
        if not LOGTYPE_RE.match(l_type):
            errors[prefix + "type"] = "ใช้ A-Z a-z 0-9 _ - เท่านั้น"
        logs.append({"name": l_name, "path": l_path, "type": l_type})

    group = clean_group(data.get("group"), errors)
    priority = clean_priority(data.get("priority"), errors)
    paused = bool(data.get("paused", False))
    pause = clean_pause(data, paused, errors)
    thresholds = clean_thresholds(data.get("thresholds"), errors)
    alert = clean_alert(data.get("alert"), errors)

    if errors:
        raise ValidationError(errors)

    return {
        "name": name,
        "host": host,
        "group": group,
        "priority": priority,
        "paused": paused,
        "pause": pause,
        "thresholds": thresholds,
        "alert": alert,
        "prometheus": {"job": job, "instance": instance,
                       "scheme": scheme, "metrics_path": metrics_path},
        "ssh": {"port": ssh_port, "user": ssh_user},
        "logs": logs,
    }


# ------------------------------------------------------------------
# อ่าน/เขียน puller.yaml
# ------------------------------------------------------------------

def _yaml():
    y = YAML()                       # round-trip: คงคอมเมนต์
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)    # ตรงกับรูปแบบใน puller.yaml
    y.width = 4096
    return y


@contextlib.contextmanager
def _locked():
    os.makedirs(os.path.dirname(settings.TARGETS_LOCK_FILE), exist_ok=True)
    with open(settings.TARGETS_LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _load_doc(y):
    with open(settings.CONFIG_FILE, "r", encoding="utf-8") as f:
        doc = y.load(f)
    if doc is None:
        doc = CommentedMap()
    return doc


def _targets_seq(doc):
    seq = doc.get("targets")
    if not isinstance(seq, CommentedSeq):
        seq = CommentedSeq()
        doc["targets"] = seq
    elif seq.fa.flow_style():        # "targets: []" -> เปลี่ยนเป็นแบบ block ก่อนเพิ่มรายการ
        seq.fa.set_block_style()
    return seq


def _to_node(t):
    node = CommentedMap()
    node["name"] = t["name"]
    node["host"] = t["host"]
    if t.get("group"):
        node["group"] = t["group"]
    if t.get("priority") and t["priority"] != PRIORITY_DEFAULT:
        node["priority"] = t["priority"]
    if t.get("paused"):
        node["paused"] = True
        if t.get("pause"):
            node["pause"] = _flow(t["pause"])
    if t.get("thresholds"):
        node["thresholds"] = _thresholds_node(t["thresholds"])
    if t.get("alert"):
        node["alert"] = _flow(t["alert"])
    prom = CommentedMap(t["prometheus"])
    node["prometheus"] = prom
    if t.get("type") == "winrm":
        node.insert(1, "type", "winrm")
        node["winrm"] = _winrm_node(t["winrm"])
    elif t.get("type") == "agent":
        node.insert(1, "type", "agent")
        node["agent"] = _agent_node(t["agent"])
    else:
        node["ssh"] = CommentedMap(t["ssh"])
    logs = CommentedSeq()
    for item in t["logs"]:
        logs.append(CommentedMap(item))
    node["logs"] = logs
    return node


def _apply(node, t):
    """แก้ node เดิมในที่ (คงคอมเมนต์ของ node) ยกเว้น name ที่ไม่ให้เปลี่ยน"""
    for key in ("type", "os", "agent", "critical_services", "token"):     # ชนิดเครื่องเปลี่ยน -> ล้างค่าของชนิดเดิม
        if key in node:
            del node[key]
    kind = t.get("type") or "ssh"
    win = kind in ("winrm", "agent")
    if kind == "winrm":
        node.insert(1, "type", "winrm")
        node["winrm"] = _winrm_node(t["winrm"])
        node["prometheus"] = CommentedMap(t["prometheus"])
    elif kind == "agent":
        node.insert(1, "type", "agent")
        node["agent"] = _agent_node(t["agent"])
        node["prometheus"] = CommentedMap(t["prometheus"])
    for key in {"ssh": ("winrm",), "winrm": ("ssh",), "agent": ("ssh", "winrm")}[kind]:
        if key in node:
            del node[key]
    node["host"] = t["host"]
    _set_or_drop(node, "group", t.get("group") or None)
    _set_or_drop(node, "priority", t["priority"] if t.get("priority") and t["priority"] != PRIORITY_DEFAULT else None)
    _set_or_drop(node, "paused", True if t.get("paused") else None)
    _set_or_drop(node, "pause", _flow(t["pause"]) if t.get("paused") and t.get("pause") else None)
    _set_or_drop(node, "thresholds", _thresholds_node(t["thresholds"]) if t.get("thresholds") else None)
    _set_or_drop(node, "alert", _flow(t["alert"]) if t.get("alert") else None)
    for key in (() if win else ("prometheus", "ssh")):
        if not isinstance(node.get(key), CommentedMap):
            node[key] = CommentedMap()
        for k, v in t[key].items():
            node[key][k] = v
    logs = CommentedSeq()
    for item in t["logs"]:
        logs.append(CommentedMap(item))
    node["logs"] = logs


def _winrm_node(w):
    m = CommentedMap((k, v) for k, v in w.items() if k != "event_logs")
    ev = CommentedSeq(w.get("event_logs") or [])
    ev.fa.set_flow_style()
    m["event_logs"] = ev
    return m


def _agent_node(a):
    m = CommentedMap()
    for k, v in a.items():
        if k in ("features",):
            m[k] = _flow(v)
        elif k == "event_logs":
            ev = CommentedSeq(v or [])
            ev.fa.set_flow_style()
            m[k] = ev
        elif v not in ("", None):
            m[k] = v
    return m


def _flow(d):
    m = CommentedMap(d)
    m.fa.set_flow_style()
    return m


def _thresholds_node(th):
    """{disk: {warning: 90, error: 95}} -> เขียนบรรทัดเดียวต่อชนิด อ่านง่ายใน puller.yaml"""
    node = CommentedMap()
    for kind, levels in th.items():
        m = CommentedMap(levels)
        m.fa.set_flow_style()
        node[kind] = m
    return node


def _set_or_drop(node, key, value):
    """ตั้งค่า key หรือลบทิ้งถ้า value เป็น None (ไม่ทิ้ง key ว่าง ๆ ไว้ใน yaml)"""
    if value is None:
        if key in node:
            del node[key]
    else:
        node[key] = value


def _write_doc(y, doc):
    buf = io.StringIO()
    y.dump(doc, buf)
    text = buf.getvalue()

    path = settings.CONFIG_FILE
    tmp = path + ".tmp"
    st = os.stat(path) if os.path.exists(path) else None

    if st is not None:
        shutil.copy2(path, path + ".bak")        # สำรองเวอร์ชันก่อนหน้า 1 ชุด

    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())

    if st is not None:
        os.chmod(tmp, st.st_mode & 0o777)
        with contextlib.suppress(PermissionError, OSError):
            os.chown(tmp, st.st_uid, st.st_gid)   # ให้ผู้ใช้บน host ยังแก้ไฟล์เองได้

    os.replace(tmp, path)


def _plain(node):
    """ทำให้ node ของ ruamel เป็น dict/list ธรรมดา"""
    return json.loads(json.dumps(node, default=lambda o: dict(o) if isinstance(o, dict) else list(o)))


# ------------------------------------------------------------------
# Prometheus file_sd + สั่ง puller ดึงทันที
# ------------------------------------------------------------------

def regenerate_prometheus_targets(doc):
    environment = (doc.get("site") or {}).get("environment", "production")
    out = []
    for web in doc.get("targets") or []:
        prom = web.get("prometheus") or {}
        if is_winrm(web) or is_agent(web):
            entry = winrm_prometheus_entry(web, environment)
            if entry:
                out.append(entry)
            continue
        if web.get("name") and prom.get("job") and prom.get("instance"):
            labels = {"job": prom["job"], "web": web["name"], "environment": environment}
            if str(prom.get("scheme") or "http").lower() == "https":
                labels["__scheme__"] = "https"
            if (prom.get("metrics_path") or "/metrics") != "/metrics":
                labels["__metrics_path__"] = prom["metrics_path"]
            out.append({"targets": [prom["instance"]], "labels": labels})
    path = settings.PROMETHEUS_TARGET_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, path)


def request_pull(name):
    """ขอให้ puller ดึงเครื่อง name ทันที (puller เฝ้าไฟล์นี้ทุก ~2 วินาที)"""
    path = settings.PULL_TRIGGER_FILE
    try:
        with open(path) as f:
            names = json.load(f)
        if not isinstance(names, list):
            names = []
    except (OSError, ValueError):
        names = []
    if name not in names:
        names.append(name)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(names, f)
    os.replace(tmp, path)


# ------------------------------------------------------------------
# known_hosts / host key
# ------------------------------------------------------------------

def _hostkey_name(host, port):
    return host if int(port) == 22 else f"[{host}]:{int(port)}"


def host_key_known(host, port):
    if not os.path.isfile(settings.KNOWN_HOSTS_FILE):
        return False
    r = subprocess.run(
        ["ssh-keygen", "-F", _hostkey_name(host, port), "-f", settings.KNOWN_HOSTS_FILE],
        capture_output=True, timeout=10,
    )
    return r.returncode == 0 and bool(r.stdout.strip())


def _fingerprint(line):
    r = subprocess.run(
        ["ssh-keygen", "-l", "-f", "-"], input=line.encode(),
        capture_output=True, timeout=10,
    )
    parts = r.stdout.decode(errors="replace").split()
    return parts[1] if r.returncode == 0 and len(parts) >= 2 else None


def scan_host_key(host, port):
    """ssh-keyscan เครื่องปลายทาง คืน [{type, fingerprint, line}] (ยังไม่เก็บอะไรลงไฟล์)"""
    errors = {}
    if not _valid_host(host or ""):
        errors["host"] = "ต้องเป็น IP หรือชื่อโดเมนที่ถูกต้อง"
    port = _as_int_port(port, "ssh.port", errors, default=22)
    if errors:
        raise ValidationError(errors)

    try:
        r = subprocess.run(
            ["ssh-keyscan", "-T", "5", "-t", "ed25519,ecdsa,rsa", "-p", str(port), host],
            capture_output=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        raise ValidationError({"host": "เชื่อมต่อไม่ได้ (timeout)"}, status=502)

    keys = []
    for line in r.stdout.decode(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fp = _fingerprint(line)
        if fp:
            keys.append({"type": line.split()[1], "fingerprint": fp, "line": line})

    if not keys:
        raise ValidationError({"host": "ไม่พบ SSH host key ที่ host:port นี้ (เช็ค IP, พอร์ต, ไฟร์วอลล์)"}, status=502)
    return keys


def trust_host_keys(host, port, fingerprints):
    """สแกนใหม่แล้วเก็บเฉพาะ key ที่ fingerprint ตรงกับที่ผู้ใช้ยืนยัน (กันถูกสลับระหว่างทาง)"""
    keys = scan_host_key(host, port)
    wanted = set(fingerprints or [])
    chosen = [k for k in keys if k["fingerprint"] in wanted]
    if not chosen:
        raise ValidationError({"host_key": "host key เปลี่ยนไปจากที่ยืนยัน กรุณาสแกนและตรวจสอบใหม่"}, status=409)
    if host_key_known(host, port):
        return 0
    with open(settings.KNOWN_HOSTS_FILE, "a") as f:
        for k in chosen:
            f.write(k["line"] + "\n")
    return len(chosen)


def public_key():
    try:
        with open(settings.PUBLIC_KEY_FILE) as f:
            return f.read().strip()
    except OSError:
        return None


# ------------------------------------------------------------------
# การทำงานหลัก (ถูกเรียกจาก api.py)
# ------------------------------------------------------------------

def _pause_view(t):
    p = t.get("pause") if isinstance(t.get("pause"), dict) and t.get("paused") else {}
    return {"pause_reason": p.get("reason") or "", "paused_at": p.get("at"), "paused_by": p.get("by") or ""}


def _prio(t):
    try:
        v = int(t.get("priority") or PRIORITY_DEFAULT)
    except (TypeError, ValueError):
        v = PRIORITY_DEFAULT
    return v if 1 <= v <= 5 else PRIORITY_DEFAULT


def _view(t):
    t = _plain(t)
    if is_agent(t):
        a = {**agent_defaults(), **(t.get("agent") if isinstance(t.get("agent"), dict) else {})}
        a["features"] = {**AGENT_FEATURES, **(a.get("features") if isinstance(a.get("features"), dict) else {})}
        name = str(t.get("name") or "")
        return {"type": "agent", "name": name, "host": t.get("host") or "", "group": t.get("group") or "",
                "priority": _prio(t), "paused": bool(t.get("paused")), **_pause_view(t),
                "thresholds": t.get("thresholds") or {}, "alert": t.get("alert") or {},
                "prometheus": t.get("prometheus") or {"job": f"{name}-agent", "instance": f"agent:{name}"},
                "agent": a, "logs": [x for x in t.get("logs") or [] if isinstance(x, dict)],
                "watch_services": t.get("watch_services") or []}
    if is_winrm(t):
        w = t.get("winrm") or {}
        return {
            "type": "winrm", "name": t.get("name"), "host": t.get("host"), "group": t.get("group") or "",
            "priority": _prio(t), "paused": bool(t.get("paused")), **_pause_view(t),
            "thresholds": t.get("thresholds") or {}, "alert": t.get("alert") or {},
            "prometheus": t.get("prometheus") or {},
            "winrm": {"port": w.get("port", 5986), "user": w.get("user") or "cm-monitor",
                      "thumbprint": w.get("thumbprint") or "", "event_logs": list(w.get("event_logs") or DEFAULT_EVENT_LOGS)},
            "has_password": cm_secrets.has_password(str(t.get("name") or "")),
            # thumbprint ที่ puller จำไว้เองตอนเชื่อมต่อสำเร็จครั้งแรก (ติดตั้งด้วย zip ไม่ได้กรอก)
            "pinned_thumbprint": "" if w.get("thumbprint") else cm_secrets.get_pin(str(t.get("name") or "")),
            "logs": t.get("logs") or [],
            "watch_services": t.get("watch_services") or [],
        }
    ssh = t.get("ssh") or {}
    port = ssh.get("port", 22)
    return {
        "type": "ssh",
        "name": t.get("name"),
        "host": t.get("host"),
        "group": t.get("group") or "",
        "priority": _prio(t),
        "paused": bool(t.get("paused")),
        **_pause_view(t),
        "thresholds": t.get("thresholds") or {},
        "alert": t.get("alert") or {},
        "prometheus": t.get("prometheus") or {},
        "ssh": {"port": port, "user": ssh.get("user", "")},
        "logs": t.get("logs") or [],
        "watch_services": t.get("watch_services") or [],
        "host_key_known": host_key_known(str(t.get("host", "")), port) if t.get("host") else False,
    }


def list_targets():
    y = _yaml()
    doc = _load_doc(y)
    return [_view(t) for t in (doc.get("targets") or []) if isinstance(t, dict)]


def log_status(name):
    """สถานะของ log แต่ละรายการของเครื่อง (จาก checkpoints.json ของ puller) ไม่มีเครื่อง -> None
    -> [{name, path, type, files, last_pull, offset}]  files = จำนวนไฟล์ที่ puller เคยดึง (wildcard ได้หลายไฟล์)"""
    import fnmatch
    doc = _load_doc(_yaml())
    t = next((x for x in (doc.get("targets") or []) if isinstance(x, dict) and x.get("name") == name), None)
    if t is None:
        return None
    if is_winrm(t):
        return _winrm_log_status(t)
    try:
        with open(os.path.join(settings.STATE_DIR, "checkpoints.json")) as f:
            state = json.load(f)
        state = state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        state = {}
    out = []
    for src in t.get("logs") or []:
        if not isinstance(src, dict):
            continue
        lname, path = str(src.get("name") or ""), str(src.get("path") or "")
        rows = [v for k, v in state.items() if isinstance(v, dict) and k.startswith(f"{name}::{lname}::")
                and (v.get("path") == path or fnmatch.fnmatchcase(str(v.get("path") or k.split("::", 2)[2]), path))]
        times = sorted(str(v.get("updated_at")) for v in rows if v.get("updated_at"))
        out.append({"name": lname, "path": path, "type": str(src.get("type") or ""), "files": len(rows),
                    "last_pull": times[-1] if times else None, "offset": sum(int(v.get("offset") or 0) for v in rows)})
    return {"name": name, "logs": out}


def _store_password(t):
    pw = t.pop("_password", "")
    if t.get("type") == "winrm" and pw:
        try:
            cm_secrets.set_password(t["name"], pw)
        except cm_secrets.SecretError as e:
            raise ValidationError({"password": str(e)}) from e


def _winrm_log_status(t):
    """สถานะ log ของเครื่อง Windows จาก data/state/winrm-state.json (puller windows_pull.py)"""
    import fnmatch
    name = t.get("name")
    try:
        with open(os.path.join(settings.STATE_DIR, "winrm-state.json")) as f:
            st = (json.load(f) or {}).get(name) or {}
    except (OSError, ValueError, AttributeError):
        st = {}
    tails = st.get("tails") or {}
    out = []
    for src in t.get("logs") or []:
        lname, path = str(src.get("name") or ""), str(src.get("path") or "")
        rows = [v for k, v in tails.items() if isinstance(v, dict) and k.startswith(lname + "::")
                and fnmatch.fnmatch(k.split("::", 1)[1].lower(), path.lower())]
        times = sorted(str(v.get("updated_at")) for v in rows if v.get("updated_at"))
        out.append({"name": lname, "path": path, "type": str(src.get("type") or ""), "files": len(rows),
                    "last_pull": times[-1] if times else None, "offset": sum(int(v.get("offset") or 0) for v in rows)})
    for log_name, rid in (st.get("events") or {}).items():
        out.append({"name": "eventlog", "path": f"EventLog: {log_name}", "type": f"EventLog-{log_name}", "files": 1,
                    "last_pull": st.get("updated_at"), "offset": rid})
    return {"name": name, "logs": out}


def _confirm_host_key(t, payload):
    if t.get("type") in ("winrm", "agent"):
        return
    fps = payload.get("trust_fingerprints")
    if fps:
        trust_host_keys(t["host"], t["ssh"]["port"], fps)


def add_target(payload):
    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        seq = _targets_seq(doc)
        names = {t.get("name") for t in seq if isinstance(t, dict)}
        if len(names) >= MAX_TARGETS:
            raise ValidationError({"_": f"มีเครื่องครบ {MAX_TARGETS} แล้ว"})
        t = validate_target(payload, names)
        _confirm_host_key(t, payload)
        _store_password(t)
        seq.append(_to_node(t))
        _write_doc(y, doc)
        regenerate_prometheus_targets(doc)
        request_pull(t["name"])
    return _view(t)


def update_target(name, payload):
    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        seq = _targets_seq(doc)
        node = next((n for n in seq if isinstance(n, dict) and n.get("name") == name), None)
        if node is None:
            return None
        t = validate_target(payload, set(), editing=name)
        _confirm_host_key(t, payload)
        _store_password(t)
        _apply(node, t)
        _write_doc(y, doc)
        regenerate_prometheus_targets(doc)
        request_pull(name)
    return _view(t)


# operator แก้ได้เฉพาะส่วนนี้ของเครื่อง (การแจ้งเตือน + log) ส่วนอื่นคงค่าเดิมเสมอ
OPERATOR_FIELDS = ("thresholds", "alert", "logs")


def update_monitoring(name, payload):
    """แก้เฉพาะ เกณฑ์ / ค่าแจ้งเตือน / log ของเครื่อง (สิทธิ์ operator)  ไม่มีเครื่อง -> None"""
    payload = payload if isinstance(payload, dict) else {}
    cur = next((t for t in list_targets() if t.get("name") == name), None)
    if cur is None or is_agent(cur):
        return None
    body = {k: v for k, v in cur.items() if k not in ("host_key_known", "watch_services", "has_password")}
    for k in OPERATOR_FIELDS:
        if k in payload:
            body[k] = payload[k]
    return update_target(name, body)


# ------------------------------------------------------------------
# ย้ายเครื่อง / กลุ่ม (ลากวางในหน้า Dashboard โหมดจัดลำดับ)  admin
# ------------------------------------------------------------------

def regroup_path(path, old, new):
    """path ของกลุ่ม old (และกลุ่มย่อย) -> ย้ายไปเป็น new  ไม่เกี่ยว = คืนค่าเดิม"""
    path = str(path or "")
    if path == old:
        return new
    if old and path.startswith(old + "/"):
        rest = path[len(old) + 1:]
        return f"{new}/{rest}" if new else rest
    return path


def set_target_group(name, group):
    """ย้ายเครื่องเข้ากลุ่ม (ว่าง = ไม่มีกลุ่ม)  ไม่มีเครื่อง -> None"""
    errors = {}
    group = clean_group(group, errors)
    if errors:
        raise ValidationError(errors)
    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        node = _find_target(doc, name)
        if node is None:
            return None
        _set_or_drop(node, "group", group or None)
        _write_doc(y, doc)
    return {"name": name, "group": group}


def move_group(src, parent):
    """ย้ายกลุ่ม src (พร้อมกลุ่มย่อยและเครื่องทั้งหมด) ไปไว้ใต้ parent (ว่าง = ชั้นบนสุด)
    แก้ targets[].group และ alert_rules[].groups ที่อ้างถึงกลุ่มนี้ให้ตามไปด้วย  คืน {from, to, machines}"""
    errors = {}
    src = clean_group(src, errors, "from")
    parent = clean_group(parent, errors, "parent")
    if not src:
        errors["from"] = "ต้องระบุกลุ่ม"
    if errors:
        raise ValidationError(errors)
    if parent == src or parent.startswith(src + "/"):
        raise ValidationError({"parent": "ย้ายกลุ่มเข้าไปในตัวเอง / กลุ่มย่อยของตัวเองไม่ได้"})
    leaf = src.split("/")[-1]
    dst = f"{parent}/{leaf}" if parent else leaf
    if dst == src:
        return {"from": src, "to": dst, "machines": 0}
    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        nodes = [n for n in (doc.get("targets") or []) if isinstance(n, dict)]
        moved = 0
        for n in nodes:
            g = str(n.get("group") or "")
            ng = regroup_path(g, src, dst)
            if ng != g:
                if not GROUP_RE.match(ng):
                    raise ValidationError({"parent": f"กลุ่มลึกเกิน 10 ชั้น / ชื่อไม่ถูกต้อง: {ng[:80]}"})
                n["group"] = ng
                moved += 1
        for r in doc.get("alert_rules") or []:
            if isinstance(r, dict) and r.get("groups"):
                gs = [regroup_path(str(x).strip("/"), src, dst) for x in r["groups"]]
                if gs != [str(x).strip("/") for x in r["groups"]]:
                    seq = CommentedSeq(gs)
                    seq.fa.set_flow_style()
                    r["groups"] = seq
        _write_doc(y, doc)
    return {"from": src, "to": dst, "machines": moved}


def set_paused(name, paused, reason=None, user=None):
    """Pause / Resume เครื่อง (puller ข้ามเครื่องที่ pause: ไม่ดึง log ไม่เก็บสถานะ) ไม่มีเครื่อง -> None
    Pause ต้องมีเหตุผล (เก็บใน targets[].pause {reason, at, by}) · Resume = ลบทิ้ง"""
    reason = str(reason or "").strip()[:200]
    if paused and not reason:
        raise ValidationError({"reason": "ใส่เหตุผลที่ Pause (เช่น ปิดซ่อมบำรุง, ย้ายเครื่อง)"})
    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        node = _find_target(doc, name)
        if node is None:
            return None
        _set_or_drop(node, "paused", True if paused else None)
        info = {"reason": reason, "at": int(time.time()), **({"by": str(user)[:60]} if user else {})} if paused else None
        _set_or_drop(node, "pause", _flow(info) if info else None)
        _write_doc(y, doc)
        if not paused:
            request_pull(name)          # resume -> ดึงทันที
    return {"name": name, "paused": bool(paused), "reason": reason if paused else ""}


def delete_target(name):
    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        seq = _targets_seq(doc)
        idx = next((i for i, n in enumerate(seq) if isinstance(n, dict) and n.get("name") == name), None)
        if idx is None:
            return False
        del seq[idx]
        _write_doc(y, doc)
        regenerate_prometheus_targets(doc)
    cm_secrets.delete(name)
    return True


# ------------------------------------------------------------------
# เลือก service ที่จะแสดงในการ์ด Service ของหน้า dashboard (ต่อเครื่อง)
# ------------------------------------------------------------------
# เก็บใน puller.yaml ที่ targets[].watch_services (รายการชื่อ unit เช่น nginx.service)
#   - ว่าง/ไม่มีคีย์ = แสดงทุก service (ค่าเริ่มต้น)
#   - เป็นการตั้งค่าการแสดงผลอย่างเดียว puller ยังเก็บ service ทุกตัวเหมือนเดิม
#   - เก็บฝั่ง server -> ทุกคน/ทุก browser เห็นการเลือกเดียวกัน

MAX_WATCH_SERVICES = 300
_SERVICE_NAME = re.compile(r"^[A-Za-z0-9:_.@$ \\-]{1,256}$")


def _find_target(doc, name):
    return next((n for n in (doc.get("targets") or [])
                 if isinstance(n, dict) and n.get("name") == name), None)


def get_watch_services(name):
    """คืนรายชื่อ service ที่เลือกไว้ของเครื่อง name ([] = ทั้งหมด) หรือ None ถ้าไม่มีเครื่องนี้"""
    node = _find_target(_load_doc(_yaml()), name)
    if node is None:
        return None
    return [str(x) for x in (node.get("watch_services") or [])]


def set_watch_services(name, services):
    """บันทึกรายชื่อ service ที่จะแสดง ([] = แสดงทั้งหมด) คืนรายการที่บันทึก หรือ None ถ้าไม่มีเครื่องนี้"""
    if not isinstance(services, list):
        raise ValidationError({"services": "ต้องเป็น list ของชื่อ service"})
    clean = []
    for item in services:
        item = str(item).strip()
        if not _SERVICE_NAME.match(item):
            raise ValidationError({"services": f"ชื่อ service ไม่ถูกต้อง: {item[:80]!r}"})
        if item not in clean:
            clean.append(item)
    if len(clean) > MAX_WATCH_SERVICES:
        raise ValidationError({"services": f"เลือกได้ไม่เกิน {MAX_WATCH_SERVICES} service"})
    clean.sort(key=str.lower)

    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        node = _find_target(doc, name)
        if node is None:
            return None
        if clean:
            seq = CommentedSeq(clean)
            seq.fa.set_flow_style()          # เขียนบรรทัดเดียว: watch_services: [nginx.service, ssh.service]
            node["watch_services"] = seq
        elif "watch_services" in node:
            del node["watch_services"]
        _write_doc(y, doc)
    return clean


# ------------------------------------------------------------------
# วาง public key ลงเครื่อง target (ตอน Add target / Edit ในหน้า Settings)
# ------------------------------------------------------------------
# หลักการ
#   - ทำได้เฉพาะเครื่องที่อยู่ใน puller.yaml แล้ว (host/port/user อ่านจากไฟล์ ไม่รับจากผู้ใช้ -> ไม่เป็นช่อง SSRF)
#   - ต้องเก็บ host key เข้า known_hosts ก่อน (StrictHostKeyChecking=yes)
#   - รหัสผ่านใช้ครั้งเดียวผ่าน SSH_ASKPASS (ส่งทาง environment ของ process ลูก ไม่ขึ้น argv/ไฟล์/log และไม่ถูกบันทึก)
#   - ถ้า admin_user ต่างจาก ssh.user จะ sudo -S -u <ssh.user> ให้ (ใช้รหัสผ่านเดียวกัน)
#   - เขียน authorized_keys แบบไม่ซ้ำ และลบบรรทัด key เก่าของ server นี้ (จับจาก comment central-monitor@<hostname>)
#     จึงใช้ได้ทั้งตอนเพิ่มเครื่องใหม่ และหลัง install.sh สร้าง key ใหม่ (ไม่สะสม key เก่า)

PUBKEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/]+={0,2}(?: [A-Za-z0-9@._-]{1,128})?$")
FROM_RE = re.compile(r"^[A-Za-z0-9.:*?/,!-]{1,200}$")


def _remote_script(pub_line):
    """สคริปต์ sh ที่ไปรันบนเครื่องปลายทาง (ค่าทุกตัวผ่าน regex ตรวจแล้ว จึงฝังตรง ๆ ได้ปลอดภัย)"""
    parts = pub_line.split()
    pats = ["-e", shlex.quote(parts[1])]
    if len(parts) > 2:
        pats += ["-e", shlex.quote(parts[2])]
    line = pub_line
    if settings.KEY_FROM:
        if not FROM_RE.match(settings.KEY_FROM):
            raise ValidationError({"_": "CENTRAL_KEY_FROM ไม่ถูกต้อง"})
        line = f'from="{settings.KEY_FROM}" {pub_line}'
    return "\n".join([
        "set -e",
        "umask 077",
        'd="$HOME/.ssh"; f="$d/authorized_keys"',
        'mkdir -p "$d"; chmod 700 "$d"',
        'touch "$f"; chmod 600 "$f"',
        't="$(mktemp "$d/.cm.XXXXXX")"',
        f'grep -v -F {" ".join(pats)} "$f" > "$t" || true',
        "cat >> \"$t\" <<'CM_KEY'",
        line,
        "CM_KEY",
        'cat "$t" > "$f"',
        'rm -f "$t"',
        f'grep -qF {shlex.quote(parts[1])} "$f"',
        "",
    ])


def _friendly_ssh_error(stderr, password, admin, user, host):
    err = (stderr or "").replace(password, "***")
    low = err.lower()
    if "host key verification failed" in low:
        return "host key ไม่ตรงกับที่เก็บไว้ (กด Scan host key ใหม่ แล้วตรวจ fingerprint)"
    if "permission denied" in low and "sudo" not in low:
        return f"เข้า {admin}@{host} ไม่ได้: รหัสผ่านผิด หรือเครื่องนั้นปิด password login (ใช้บัญชี admin ที่ล็อกอินด้วยรหัสผ่านได้)"
    if "sudo" in low:
        return f"sudo ไม่ผ่าน: {admin} ต้องรัน sudo -u {user} ได้ (หรือเว้นช่อง admin ถ้า {user} ล็อกอินด้วยรหัสผ่านได้เอง)"
    if "no such file" in low and ".ssh" in low:
        return f"ไม่พบ user {user} บนเครื่องปลายทาง (สร้างก่อน: sudo adduser --disabled-password {user})"
    if "connection" in low or "timed out" in low or "no route" in low:
        return f"เชื่อมต่อ {host} ไม่ได้ (เช็ค IP, พอร์ต, ไฟร์วอลล์)"
    tail = err.strip().splitlines()[-1] if err.strip() else "ไม่ทราบสาเหตุ"
    return f"วาง key ไม่สำเร็จ: {tail[:200]}"


def deploy_key(name, payload):
    """วาง public key ลงเครื่อง target ชื่อ name  คืน {"ok": bool, "message": str} (ไม่เจอ name -> None)"""
    doc = _load_doc(_yaml())
    node = next((n for n in (doc.get("targets") or []) if isinstance(n, dict) and n.get("name") == name), None)
    if node is None:
        return None
    t = _view(node)
    host, port, user = t["host"], int(t["ssh"]["port"] or 22), t["ssh"]["user"]

    payload = payload if isinstance(payload, dict) else {}
    admin = _text(payload, "admin_user") or user
    password = payload.get("password")
    errors = {}
    if not USER_RE.match(admin):
        errors["admin_user"] = "ชื่อ user ไม่ถูกต้อง"
    if not isinstance(password, str) or not password or len(password) > 256 or "\n" in password or "\0" in password:
        errors["password"] = "ต้องกรอกรหัสผ่าน (ไม่เกิน 256 ตัวอักษร)"
    if errors:
        raise ValidationError(errors)
    if not host_key_known(host, port):
        raise ValidationError({"host_key": "ยังไม่ได้เก็บ SSH host key ของเครื่องนี้ (กด Scan host key แล้วบันทึกก่อน)"}, status=409)
    pub = public_key()
    if not pub or not PUBKEY_RE.match(pub):
        raise ValidationError({"_": "ไม่พบ/อ่าน public key (keys/central_ed25519.pub) ไม่ได้"}, status=500)

    b64 = base64.b64encode(_remote_script(pub).encode()).decode()
    if admin == user:
        remote, stdin = f"echo {b64} | base64 -d | sh", None
    else:
        remote = f'sudo -S -p "" -u {user} -H sh -c "$(echo {b64} | base64 -d)"'
        stdin = password + "\n"

    fd, askpass = tempfile.mkstemp(prefix="cm-askpass-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("#!/bin/sh\nprintf '%s\\n' \"$CM_SSH_PASS\"\n")     # รหัสมาจาก env ของ process ลูกเท่านั้น
        os.chmod(askpass, 0o700)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp", "DISPLAY": "",
            "SSH_ASKPASS": askpass, "SSH_ASKPASS_REQUIRE": "force", "CM_SSH_PASS": password,
        }
        cmd = [
            "ssh", "-F", "/dev/null", "-p", str(port),
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={settings.KNOWN_HOSTS_FILE}",
            "-o", "ConnectTimeout=10", "-o", "PubkeyAuthentication=no",
            "-o", "PreferredAuthentications=password,keyboard-interactive",
            "-o", "NumberOfPasswordPrompts=1", f"{admin}@{host}", remote,
        ]
        try:
            r = subprocess.run(cmd, input=stdin.encode() if stdin else b"", env=env,
                               capture_output=True, timeout=40, start_new_session=True)
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": f"หมดเวลารอ {host} (เช็ค IP, พอร์ต, ไฟร์วอลล์)"}
    finally:
        with contextlib.suppress(OSError):
            os.remove(askpass)

    if r.returncode != 0:
        return {"ok": False, "message": _friendly_ssh_error(r.stderr.decode(errors="replace"), password, admin, user, host)}
    request_pull(name)      # ให้ puller ลองดึงทันทีด้วย key ใหม่
    return {"ok": True, "message": f"วาง public key ให้ {user}@{host} แล้ว (ลบ key เก่าของ server นี้ออกให้แล้ว) puller จะดึงเครื่องนี้ภายในไม่กี่วินาที"}


# ------------------------------------------------------------------
# ชุดติดตั้งบนเครื่อง Windows (ปุ่มในหน้า Settings > การเชื่อมต่อ)
# ------------------------------------------------------------------

WINDOWS_SETUP_DIR = os.environ.get("CENTRAL_WINDOWS_SETUP_DIR",
                                   os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_windows"))
WINDOWS_SETUP_FILES = ("setup-cm-monitor.bat", "setup-cm-monitor.ps1", "check-cm-monitor.bat", "check-cm-monitor.ps1",
                       "remove-cm-monitor.bat", "remove-cm-monitor.ps1", "CentralMonitor.psm1", "README-TH.txt",
                       "reset-password-cm-monitor.bat")


def windows_log_folders(logs):
    """logs ของเครื่อง -> รายการ LogPaths ของ cm-monitor-config.txt (โฟลเดอร์\\ หรือ pattern ชื่อไฟล์)"""
    out = []
    for item in logs or []:
        path = str((item or {}).get("path") or "")
        if not WINPATH_RE.match(path):
            continue
        leaf = path.rsplit("\\", 1)[1] if "\\" in path else path
        rule = path if any(ch in leaf for ch in "*?") else path.rsplit("\\", 1)[0] + "\\"
        if rule not in out:
            out.append(rule)
    return out


def new_winrm_password():
    """รหัสของ cm-monitor ที่ central ตั้งเอง: 24 ตัว มีตัวใหญ่ / เล็ก / ตัวเลข / สัญลักษณ์ (ผ่าน password policy ของ Windows)"""
    import secrets
    sets = ("ABCDEFGHJKLMNPQRSTUVWXYZ", "abcdefghijkmnopqrstuvwxyz", "23456789", "-_.")
    chars = [secrets.choice(x) for x in sets] + [secrets.choice("".join(sets)) for _ in range(20)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def windows_config_text(central, log_paths, port=5986, password=""):
    lines = [
        "# =====================================================================",
        "# Central Monitor - agentless Windows monitoring: settings for setup-cm-monitor.bat",
        "# Generated by Central Monitor (Settings > Windows target). Run setup-cm-monitor.bat after any change.",
        "# =====================================================================",
        "",
        "# IP of the central server (the only address allowed through the firewall)",
        f"CentralIP={central}",
        "",
        "# WinRM HTTPS port (if this machine already has an HTTPS listener, that one is used)",
        f"Port={int(port)}",
        "",
        "# Log files central may read, separated by ;   (empty = Event Log only)",
        "#   D:\\THMux\\logs\\          = every file directly in that folder",
        "#   D:\\THMux\\logs\\*.log     = matching files only (wildcards only in the file name)",
        "LogPaths=" + ";".join(log_paths),
        "",
        "# yes = the cm-monitor user cannot log on at the console or by Remote Desktop (WinRM still works)",
        "DenyLogon=yes",
        "",
        "# Password of cm-monitor chosen by central (already saved there - nothing to copy).",
        "# setup-cm-monitor.bat removes it from this file after use. Empty = setup makes one and shows it.",
        f"Password={password}",
        "",
    ]
    return "\r\n".join(lines)


def windows_setup_zip(central, name="", logpaths=None):
    """zip ในหน่วยความจำ (ไม่เขียนไฟล์ลงดิสก์)  คืน (bytes, ชื่อไฟล์)"""
    import zipfile
    central = str(central or "").strip()
    try:
        ipaddress.ip_address(central)
    except ValueError as e:
        raise ValidationError({"central": "ใส่ IP ของ central ที่เครื่อง Windows เห็น (เช่น 172.27.2.22)"}) from e
    port, folders, password = 5986, [], ""
    if name:
        t = next((x for x in list_targets() if x.get("name") == name and x.get("type") == "winrm"), None)
        if t is None:
            raise ValidationError({"name": "ไม่พบเครื่อง Windows ชื่อนี้"})
        port = int((t.get("winrm") or {}).get("port") or 5986)
        folders = windows_log_folders(t.get("logs"))
        # รหัสของเครื่องนี้ใส่ใน zip เลย -> ดับเบิลคลิกแล้วจบ ไม่ต้องคัดลอกอะไรกลับมา (ใช้รหัสเดิมถ้ามี)
        try:
            password = cm_secrets.get_password(name) or ""
        except cm_secrets.SecretError:
            password = ""
        if not password:
            password = new_winrm_password()
            cm_secrets.set_password(name, password)
    if logpaths:
        folders = windows_log_folders([{"path": p.strip()} for p in str(logpaths).split(";") if p.strip()]) or folders
    buf = io.BytesIO()
    top = f"cm-monitor-{name}" if name else "cm-monitor"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in WINDOWS_SETUP_FILES:
            with open(os.path.join(WINDOWS_SETUP_DIR, fn), "rb") as f:
                z.writestr(f"{top}/{fn}", f.read())
        z.writestr(f"{top}/cm-monitor-config.txt", windows_config_text(central, folders, port, password).encode("utf-8-sig"))
    return buf.getvalue(), f"{top}.zip"


# ------------------------------------------------------------------
# ติดตั้งจากหน้าเว็บ: central ใช้บัญชี admin ของเครื่อง (ครั้งเดียว ไม่เก็บ) ติดตั้งให้ผ่าน WinRM
# งานจริงอยู่ที่ puller (start_push_log/puller/windows_install.py) เพราะ gunicorn ตัดคำขอที่เกิน 60 วินาที
# ------------------------------------------------------------------

def _winrm_target(name):
    t = next((x for x in list_targets() if x.get("name") == name), None)
    if t is None or t.get("type") != "winrm":
        return None
    return t


def winrm_install_request(name, payload):
    """คืน body ที่ส่งให้ puller POST /winrm-install  (รหัส admin อยู่ใน body นี้เท่านั้น)  ไม่มีเครื่อง -> None"""
    t = _winrm_target(name)
    if t is None:
        return None
    payload = payload if isinstance(payload, dict) else {}
    user, pw, port, errors = _admin_fields(payload)
    central = _text(payload, "central") or "auto"
    if central != "auto":
        try:
            ipaddress.ip_address(central)
        except ValueError:
            errors["central"] = "IP ของ central ไม่ถูกต้อง (เว้นว่าง = ตรวจหาให้เอง)"
    update = _text(payload, "mode") == "update"
    if update and not cm_secrets.has_password(name):
        errors["admin_password"] = "เครื่องนี้ยังไม่ได้ติดตั้ง ใช้ปุ่ม \"ติดตั้งให้เครื่องนี้\" ก่อน"
    if errors:
        raise ValidationError(errors)
    files = _setup_files("setup-cm-monitor.ps1", "CentralMonitor.psm1")
    if update:
        # อัปเดต module / ตัวช่วย บนเครื่อง: รหัสของ cm-monitor คงเดิม (setup ไม่แตะรหัสเมื่อไม่มี Password=)
        cm_secrets.clear_pending(name)
        new_pw = ""
    else:
        new_pw = new_winrm_password()
        cm_secrets.set_pending(name, new_pw)      # ใช้จริงเมื่อติดตั้งสำเร็จ (winrm_install_apply)
    config = windows_config_text(central, windows_log_folders(t.get("logs")), int(t["winrm"].get("port") or 5986), new_pw)
    return {"action": "install", "mode": "update" if update else "install", "name": name, "host": t["host"],
            "port": port, "user": user, "password": pw, "files_b64": files, "config": config}


def bundled_module_version():
    """รุ่นของ CentralMonitor.psm1 ที่อยู่ใน image นี้ (ตัวที่ปุ่ม ติดตั้ง / อัปเดต จะส่งไป)"""
    try:
        with open(os.path.join(WINDOWS_SETUP_DIR, "CentralMonitor.psm1"), encoding="ascii", errors="replace") as f:
            m = re.search(r"\$script:CMVersion\s*=\s*'([0-9.]+)'", f.read())
        return m.group(1) if m else ""
    except OSError:
        return ""


def _setup_files(*names):
    files = {}
    for fn in names:
        with open(os.path.join(WINDOWS_SETUP_DIR, fn), "rb") as f:
            files[fn] = base64.b64encode(f.read()).decode("ascii")
    return files


def _admin_fields(payload):
    errors = {}
    user = _text(payload, "admin_user")
    if not WINUSER_RE.match(user):
        errors["admin_user"] = "ใส่ user ที่เป็น admin ของเครื่อง เช่น Administrator หรือ DOMAIN\\admin"
    pw = payload.get("admin_password")
    if not isinstance(pw, str) or not pw or len(pw) > 256:
        errors["admin_password"] = "ใส่รหัสผ่านของ admin (ใช้ครั้งนี้ครั้งเดียว ไม่ถูกบันทึก)"
    try:
        port = int(payload.get("admin_port") or 5985)
    except (TypeError, ValueError):
        port = 0
    if port not in (5985, 5986):
        errors["admin_port"] = "5985 (HTTP เข้ารหัสด้วย NTLM/Kerberos) หรือ 5986 (HTTPS)"
    return user, pw, port, errors


def winrm_uninstall_request(name, payload, user_name=None):
    """ถอนการติดตั้งจากหน้าเว็บ: Pause เครื่องก่อน (puller ปิด session ของ cm-monitor) แล้วให้ puller รัน
    remove-cm-monitor.ps1 บนเครื่องด้วยบัญชี admin (ใช้ครั้งเดียว)  ไม่มีเครื่อง -> None"""
    t = _winrm_target(name)
    if t is None:
        return None
    payload = payload if isinstance(payload, dict) else {}
    user, pw, port, errors = _admin_fields(payload)
    if errors:
        raise ValidationError(errors)
    files = _setup_files("remove-cm-monitor.ps1")
    if not t.get("paused"):
        set_paused(name, True, reason="กำลังถอนการติดตั้ง Central Monitor ออกจากเครื่อง", user=user_name)
    return {"action": "uninstall", "name": name, "host": t["host"], "port": port, "user": user, "password": pw,
            "files_b64": files, "config": None, "delete_after": bool(payload.get("delete_target", True))}


def winrm_install_apply(name, status):
    """ผลจาก puller: ติดตั้งสำเร็จ -> ใช้รหัสใหม่ + ตั้ง thumbprint / port ใน puller.yaml + ดึงทันที
    ถอนสำเร็จ -> ลบเครื่องออกจาก central (ถ้าเลือก) หรือคงไว้แบบ Pause + ลบรหัส  (เรียกซ้ำได้)"""
    if status.get("state") != "done" or not status.get("ok"):
        return status
    if status.get("kind") == "uninstall":
        if status.get("delete_after"):
            deleted = delete_target(name)
            return {**status, "applied": True, "deleted": deleted or _winrm_target(name) is None}
        cm_secrets.delete(name)
        if _winrm_target(name) is not None:
            set_paused(name, True, reason="ถอนการติดตั้งออกจากเครื่องแล้ว")
        return {**status, "applied": True, "deleted": False}
    promoted = cm_secrets.promote_pending(name)
    thumb = norm_thumbprint(status.get("thumbprint"))
    if THUMB_RE.match(thumb):
        y = _yaml()
        with _locked():
            doc = _load_doc(y)
            node = _find_target(doc, name)
            if node is not None and isinstance(node.get("winrm"), dict):
                changed = node["winrm"].get("thumbprint") != thumb
                node["winrm"]["thumbprint"] = thumb
                try:
                    p = int(status.get("port") or 0)
                except (TypeError, ValueError):
                    p = 0
                if p and node["winrm"].get("port") != p:
                    node["winrm"]["port"] = p
                    host = str(node.get("host") or "")
                    if isinstance(node.get("prometheus"), dict):
                        node["prometheus"]["instance"] = f"[{host}]:{p}" if ":" in host else f"{host}:{p}"
                    changed = True
                if changed:
                    _write_doc(y, doc)
                    regenerate_prometheus_targets(doc)
    if promoted:
        request_pull(name)
    return {**status, "applied": True}


# ------------------------------------------------------------------
# cm-agent (Windows แบบลง agent): ชุดติดตั้ง + ติดตั้ง / ถอนจากหน้าเว็บ (ใช้ windows_install.py ของ puller ร่วมกับ WinRM)
#   ไฟล์ฝั่ง Windows: agent/windows/cm-agent/ (ใน image = agent_windows/cm-agent)
#   Fluent Bit / windows_exporter: วางไว้ที่ data/agent-bin บน central (ใส่ในชุดติดตั้งให้) ถ้าไม่มี
#   ตัวติดตั้งบนเครื่องจะลองดาวน์โหลดเองจากอินเทอร์เน็ต
# ------------------------------------------------------------------

AGENT_DIR = os.path.join(WINDOWS_SETUP_DIR, "cm-agent")
AGENT_FILES = ("install-cm-agent.bat", "install-cm-agent.ps1", "uninstall-cm-agent.bat", "uninstall-cm-agent.ps1",
               "cm-inventory.ps1", "cm-agent.lua")
AGENT_BIN_DIR = os.environ.get("CENTRAL_AGENT_BIN_DIR", "/run/agent-bin")
AGENT_CA_FILE = os.environ.get("CENTRAL_AGENT_CA_FILE", "/run/agent-tls/cert.pem")
AGENT_BIN_PATTERNS = (re.compile(r"^fluent-bit-[0-9.]+-win64\.zip$"), re.compile(r"^windows_exporter-[0-9.]+-amd64\.exe$"))


def agent_bins():
    """ไฟล์ Fluent Bit / windows_exporter ล่าสุดที่วางไว้ใน data/agent-bin -> {ชนิด: (ชื่อไฟล์, ขนาด)}"""
    out = {}
    try:
        names = sorted(os.listdir(AGENT_BIN_DIR), reverse=True)
    except OSError:
        names = []
    for kind, rx in zip(("fluent_bit", "windows_exporter"), AGENT_BIN_PATTERNS):
        fn = next((n for n in names if rx.match(n)), None)
        if fn:
            out[kind] = (fn, os.path.getsize(os.path.join(AGENT_BIN_DIR, fn)))
    return out


def agent_info():
    """ความพร้อมของ central สำหรับ cm-agent (ไม่ส่ง token ออกไป)"""
    bins = agent_bins()
    return {"token": bool(os.environ.get("CENTRAL_AGENT_TOKEN")), "ca": os.path.isfile(AGENT_CA_FILE),
            "bins": {k: {"file": v[0], "size": v[1]} for k, v in bins.items()},
            "defaults": agent_defaults(), "limits": {k: [v[1], v[2]] for k, v in AGENT_INTERVALS.items()}}


def agent_config_text(t, central):
    a = t.get("agent") or agent_defaults()
    f = a.get("features") or AGENT_FEATURES
    on = lambda k: "1" if f.get(k) else "0"            # noqa: E731
    paths = [str(x.get("path")) for x in t.get("logs") or [] if WINPATH_RE.match(str(x.get("path") or ""))]
    lines = [
        "# =====================================================================",
        "# cm-agent-config.txt - generated by Central Monitor (Settings > machine). Run install-cm-agent.bat after a change.",
        "# =====================================================================",
        f"Name={t['name']}", f"CentralIP={central}", f"Port={int(a.get('port') or 4318)}",
        f"Token={os.environ.get('CENTRAL_AGENT_TOKEN', '')}", f"SkipCertCheck={'1' if a.get('skip_cert') else '0'}",
        "",
        "# features: 1 = on, 0 = off (fewer = lighter)",
        f"Metrics={on('metrics')}", f"Logs={on('logs')}", f"Software={on('software')}", f"Processes={on('processes')}",
        "Channels=" + ",".join(a.get("event_logs") or DEFAULT_EVENT_LOGS),
        "LogPaths=" + ";".join(paths),
        "",
        "# intervals (seconds; InventoryMinutes in minutes) - longer = lighter",
        f"MetricsInterval={a.get('metrics_interval')}", f"HeartbeatInterval={a.get('heartbeat_interval')}",
        f"EventInterval={a.get('event_interval')}", f"LogRefresh={a.get('log_refresh')}", f"Flush={a.get('flush')}",
        f"InventoryMinutes={a.get('inventory_minutes')}", "",
    ]
    return "\r\n".join(lines)


def _agent_target(name):
    t = next((x for x in list_targets() if x.get("name") == name), None)
    return t if t is not None and t.get("type") == "agent" else None


def _agent_check_ready():
    errors = {}
    if not os.environ.get("CENTRAL_AGENT_TOKEN"):
        errors["agent"] = "central ยังไม่มี CENTRAL_AGENT_TOKEN (รัน cm-update ให้ install.sh สร้างให้)"
    if not os.path.isfile(AGENT_CA_FILE):
        errors["agent"] = "central ยังไม่มีใบรับรอง keys/agent-tls (รัน cm-update) หรือเลือก ไม่ตรวจใบรับรอง"
    return errors


def _agent_files(with_bins=True):
    files = {}
    for fn in AGENT_FILES:
        with open(os.path.join(AGENT_DIR, fn), "rb") as f:
            files[fn] = f.read()
    if os.path.isfile(AGENT_CA_FILE):
        with open(AGENT_CA_FILE, "rb") as f:
            files["central-ca.pem"] = f.read()
    if with_bins:
        for fn, _ in agent_bins().values():
            with open(os.path.join(AGENT_BIN_DIR, fn), "rb") as f:
                files[fn] = f.read()
    return files


def agent_package_zip(name, central):
    """zip ชุดติดตั้ง cm-agent ของเครื่องนี้ (มี token -> ส่งให้ admin เท่านั้น)  คืน (bytes, ชื่อไฟล์)"""
    import zipfile
    t = _agent_target(name)
    if t is None:
        raise ValidationError({"name": "ไม่พบเครื่องแบบ cm-agent ชื่อนี้"})
    central = str(central or (t.get("agent") or {}).get("central") or "").strip()
    if not _valid_host(central):
        raise ValidationError({"central": "ใส่ IP / ชื่อของ central ที่เครื่อง Windows เห็น (เช่น 172.27.2.22)"})
    errors = _agent_check_ready()
    if errors and not (t.get("agent") or {}).get("skip_cert"):
        raise ValidationError(errors)
    buf = io.BytesIO()
    top = f"cm-agent-{name}"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fn, data in _agent_files().items():
            z.writestr(f"{top}/{fn}", data)
        z.writestr(f"{top}/cm-agent-config.txt", agent_config_text(t, central).encode("utf-8-sig"))
    return buf.getvalue(), f"{top}.zip"


def agent_install_request(name, payload):
    """body ของ puller POST /winrm-install สำหรับติดตั้ง / อัปเดต cm-agent (admin ใช้ครั้งเดียว ไม่เก็บ)  ไม่มีเครื่อง -> None"""
    t = _agent_target(name)
    if t is None:
        return None
    payload = payload if isinstance(payload, dict) else {}
    user, pw, port, errors = _admin_fields(payload)
    central = _text(payload, "central") or (t.get("agent") or {}).get("central") or "auto"
    if central != "auto" and not _valid_host(central):
        errors["central"] = "IP / ชื่อของ central ไม่ถูกต้อง (เว้นว่าง = ตรวจหาให้เอง)"
    if not (t.get("agent") or {}).get("skip_cert"):
        errors.update(_agent_check_ready())
    elif not os.environ.get("CENTRAL_AGENT_TOKEN"):
        errors["agent"] = "central ยังไม่มี CENTRAL_AGENT_TOKEN (รัน cm-update)"
    if errors:
        raise ValidationError(errors)
    files = {k: base64.b64encode(v).decode("ascii") for k, v in _agent_files().items()}
    return {"action": "install", "mode": "install", "name": name, "host": t["host"], "port": port, "user": user,
            "password": pw, "files_b64": files, "config": agent_config_text(t, central),
            "config_name": "cm-agent-config.txt", "run_file": "install-cm-agent.ps1", "target_kind": "agent"}


def agent_uninstall_request(name, payload, user_name=None):
    t = _agent_target(name)
    if t is None:
        return None
    payload = payload if isinstance(payload, dict) else {}
    user, pw, port, errors = _admin_fields(payload)
    if errors:
        raise ValidationError(errors)
    files = {k: base64.b64encode(v).decode("ascii") for k, v in _agent_files(with_bins=False).items()
             if k == "uninstall-cm-agent.ps1"}
    if not t.get("paused"):
        set_paused(name, True, reason="กำลังถอน cm-agent ออกจากเครื่อง", user=user_name)
    return {"action": "uninstall", "name": name, "host": t["host"], "port": port, "user": user, "password": pw,
            "files_b64": files, "config": None, "delete_after": bool(payload.get("delete_target", True)),
            "run_file": "uninstall-cm-agent.ps1", "target_kind": "agent"}
