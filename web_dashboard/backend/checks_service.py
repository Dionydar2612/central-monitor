"""
checks_service.py - Web uptime + SSL checks (ตั้งค่า + อ่านผล)
=============================================================

ตั้งค่าอยู่ใน config/puller.yaml หัวข้อ checks: (หน้า Settings -> Web checks เขียนให้)

    checks:
      - name: tq-website              # a-z 0-9 - (ไม่ซ้ำ)
        url: https://www.example.com/health
        group: TQ/IT                  # (ไม่บังคับ) แสดงในกลุ่มเดียวกับเครื่อง
        interval: 60                  # วินาที ระหว่างการเช็ค (30-3600)
        timeout: 10                   # วินาที (1-30)
        expect_status: 200-399        # code ที่ถือว่า UP: "200" / "200,301" / "200-399"
        keyword: OK                   # (ไม่บังคับ) ต้องเจอคำนี้ในหน้าเว็บ
        ssl_warn_days: 14             # https: เตือนเมื่อใบรับรองเหลือน้อยกว่านี้ (วัน)
        verify_tls: true              # false = ไม่ตรวจความถูกต้องของใบรับรอง (เช่น self-signed ภายใน)
        paused: true                  # (ไม่บังคับ) หยุดเช็คชั่วคราว

ผู้เช็คจริง: start_push_log/puller/web_checks.py (thread ใน central-puller)
    เขียนผลที่ data/state/checks.json -> ไฟล์นี้อ่านไปแสดง
"""

import json
import os
import re
from urllib.parse import urlparse

import settings
from targets_service import (
    ValidationError,
    _load_doc,
    _locked,
    _set_or_drop,
    _write_doc,
    _yaml,
    clean_group,
)
from ruamel.yaml.comments import CommentedMap, CommentedSeq

RESULTS_FILE = os.path.join(settings.STATE_DIR, "checks.json")
CHECK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
STATUS_RE = re.compile(r"^\d{3}(-\d{3})?(,\d{3}(-\d{3})?)*$")
MAX_CHECKS = 300

DEFAULTS = {"interval": 60, "timeout": 10, "expect_status": "200-399", "ssl_warn_days": 14,
            "verify_tls": True}


def _int(value, field, lo, hi, default, errors):
    if value in (None, ""):
        return default
    try:
        v = int(value)
    except (TypeError, ValueError):
        errors[field] = f"ต้องเป็นตัวเลข {lo}-{hi}"
        return default
    if not lo <= v <= hi:
        errors[field] = f"ต้องอยู่ระหว่าง {lo}-{hi}"
    return v


def validate_check(data, existing, editing=None):
    errors = {}
    if not isinstance(data, dict):
        raise ValidationError({"_": "invalid body"})

    name = editing or str(data.get("name", "")).strip()
    if not CHECK_NAME_RE.match(name):
        errors["name"] = "ใช้ a-z 0-9 และ - (ยาวไม่เกิน 40)"
    elif editing is None and name in existing:
        errors["name"] = "มีชื่อนี้แล้ว"

    url = str(data.get("url", "")).strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or len(url) > 500 \
            or any(c.isspace() for c in url):
        errors["url"] = "ต้องเป็น http:// หรือ https:// ที่ถูกต้อง (ไม่มีช่องว่าง)"

    expect = str(data.get("expect_status") or DEFAULTS["expect_status"]).replace(" ", "")
    if not STATUS_RE.match(expect):
        errors["expect_status"] = 'เช่น "200" / "200,301" / "200-399"'

    keyword = str(data.get("keyword") or "").strip()
    if len(keyword) > 200:
        errors["keyword"] = "ยาวไม่เกิน 200 ตัวอักษร"

    check = {
        "name": name,
        "url": url,
        "group": clean_group(data.get("group"), errors),
        "interval": _int(data.get("interval"), "interval", 30, 3600, DEFAULTS["interval"], errors),
        "timeout": _int(data.get("timeout"), "timeout", 1, 30, DEFAULTS["timeout"], errors),
        "expect_status": expect,
        "keyword": keyword,
        "ssl_warn_days": _int(data.get("ssl_warn_days"), "ssl_warn_days", 1, 365,
                              DEFAULTS["ssl_warn_days"], errors),
        "verify_tls": data.get("verify_tls", True) is not False,
        "paused": bool(data.get("paused", False)),
    }
    if errors:
        raise ValidationError(errors)
    return check


def _checks_seq(doc):
    seq = doc.get("checks")
    if not isinstance(seq, CommentedSeq):
        seq = CommentedSeq()
        doc["checks"] = seq
    elif seq.fa.flow_style():
        seq.fa.set_block_style()
    return seq


def _write_node(node, c):
    """เขียนเฉพาะค่าที่ไม่ใช่ค่าปริยาย -> puller.yaml อ่านง่าย"""
    node["name"] = c["name"]
    node["url"] = c["url"]
    _set_or_drop(node, "group", c["group"] or None)
    for key in ("interval", "timeout", "expect_status", "ssl_warn_days"):
        _set_or_drop(node, key, None if c[key] == DEFAULTS[key] else c[key])
    _set_or_drop(node, "keyword", c["keyword"] or None)
    _set_or_drop(node, "verify_tls", None if c["verify_tls"] else False)
    _set_or_drop(node, "paused", True if c["paused"] else None)


def _plain_check(node):
    c = dict(DEFAULTS)
    c.update({"group": "", "keyword": "", "paused": False})
    c.update({k: v for k, v in dict(node).items()})
    c["group"] = c.get("group") or ""
    c["paused"] = bool(c.get("paused"))
    c["verify_tls"] = c.get("verify_tls", True) is not False
    return c


def load_check_configs():
    doc = _load_doc(_yaml())
    return [_plain_check(n) for n in (doc.get("checks") or []) if isinstance(n, dict) and n.get("name")]


def load_results():
    try:
        with open(RESULTS_FILE) as f:
            data = json.load(f)
        return data.get("checks", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_checks():
    """ตั้งค่า + ผลล่าสุดของทุก check (สำหรับหน้าเว็บ)"""
    results = load_results()
    out = []
    for c in load_check_configs():
        r = results.get(c["name"]) or {}
        status = "paused" if c["paused"] else (r.get("status") or "pending")
        out.append({**c, **{k: v for k, v in r.items() if k != "status"}, "status": status})
    return out


def add_check(payload):
    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        seq = _checks_seq(doc)
        names = {n.get("name") for n in seq if isinstance(n, dict)}
        if len(names) >= MAX_CHECKS:
            raise ValidationError({"_": f"มี check ครบ {MAX_CHECKS} แล้ว"})
        c = validate_check(payload, names)
        node = CommentedMap()
        _write_node(node, c)
        seq.append(node)
        _write_doc(y, doc)
    return c


def update_check(name, payload):
    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        node = next((n for n in _checks_seq(doc) if isinstance(n, dict) and n.get("name") == name), None)
        if node is None:
            return None
        c = validate_check(payload, set(), editing=name)
        _write_node(node, c)
        _write_doc(y, doc)
    return c


def set_check_paused(name, paused):
    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        node = next((n for n in _checks_seq(doc) if isinstance(n, dict) and n.get("name") == name), None)
        if node is None:
            return None
        _set_or_drop(node, "paused", True if paused else None)
        _write_doc(y, doc)
    return {"name": name, "paused": bool(paused)}


def delete_check(name):
    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        seq = _checks_seq(doc)
        idx = next((i for i, n in enumerate(seq) if isinstance(n, dict) and n.get("name") == name), None)
        if idx is None:
            return False
        del seq[idx]
        _write_doc(y, doc)
    return True
