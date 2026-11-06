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

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

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
# กลุ่มของเครื่อง: path คั่นด้วย / ลึกได้ 5 ชั้น เช่น "TQ/IT" หรือ "ThaiQuest/TQ/PP/Server" (ไทยได้)
GROUP_PART = r"[A-Za-z0-9฀-๿ _.()&+-]{1,40}"
GROUP_RE = re.compile(rf"^{GROUP_PART}(?:/{GROUP_PART}){{0,4}}$")
# เครื่อง Windows (agent): path ไฟล์ log เช่น C:\inetpub\logs\LogFiles\*\*.log (ห้าม , ; " ' เพราะใช้คั่น/ใส่ใน config)
WINPATH_RE = re.compile(r"^[A-Za-z]:\\[^\x00-\x1f,;\"'<>|]{1,300}$")
CHANNEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,99}$")
TARGET_TYPES = ("ssh", "agent")


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
        errors[field] = "กลุ่มคั่นชั้นด้วย / (ไม่เกิน 5 ชั้น) ใช้ตัวอักษร ตัวเลข ไทย ช่องว่าง _ . ( ) & + -"
    return group


THRESHOLD_KINDS = ("cpu", "memory", "disk")


def clean_thresholds(value, errors):
    """เกณฑ์เฉพาะเครื่อง {cpu: {warning, error}, ...} ช่องว่าง = ใช้ค่าทั้งระบบ  คืน dict (อาจว่าง)"""
    out = {}
    if not isinstance(value, dict):
        return out
    for kind in THRESHOLD_KINDS:
        item = value.get(kind) or {}
        if not isinstance(item, dict):
            continue
        clean = {}
        for lv in ("warning", "error"):
            v = item.get(lv)
            if v in (None, ""):
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                errors[f"thresholds.{kind}.{lv}"] = "ต้องเป็นตัวเลข 1-100"
                continue
            if not 1 <= v <= 100:
                errors[f"thresholds.{kind}.{lv}"] = "ต้องอยู่ระหว่าง 1-100"
                continue
            clean[lv] = int(v) if v.is_integer() else v
        if "warning" in clean and "error" in clean and clean["warning"] >= clean["error"]:
            errors[f"thresholds.{kind}.warning"] = "warning ต้องน้อยกว่า error"
        if clean:
            out[kind] = clean
    return out


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


def validate_agent_target(data, existing_names, editing=None):
    """เครื่อง Windows ที่ส่งข้อมูลเองผ่าน agent (Fluent Bit) ไม่ใช้ SSH
    puller.yaml:  type: agent / os: windows / agent: {logs: [C:\...], channels: [System, Application]}"""
    errors = {}
    name = editing or _text(data, "name")
    if not NAME_RE.match(name):
        errors["name"] = "ใช้ a-z 0-9 และ - เท่านั้น (ขึ้นต้นด้วยตัวอักษร/ตัวเลข ยาวไม่เกิน 40)"
    elif editing is None and name in existing_names:
        errors["name"] = "มีชื่อนี้อยู่แล้ว"

    host = _text(data, "host")                      # ไม่บังคับ (ใช้แสดงในหน้าเว็บ)
    if host and not _valid_host(host):
        errors["host"] = "ต้องเป็น IP หรือชื่อโดเมนที่ถูกต้อง (หรือเว้นว่าง)"

    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    logs_in = agent.get("logs") if isinstance(agent.get("logs"), list) else []
    logs = []
    if len(logs_in) > MAX_LOGS_PER_TARGET:
        errors["agent.logs"] = f"เพิ่มได้ไม่เกิน {MAX_LOGS_PER_TARGET} path"
    for p in logs_in[:MAX_LOGS_PER_TARGET]:
        p = str(p or "").strip()
        if not p:
            continue
        if not WINPATH_RE.match(p):
            errors["agent.logs"] = f"path ไม่ถูกต้อง: {p[:80]} (ต้องขึ้นต้นด้วยไดรฟ์ เช่น C:\\ ห้ามมี , ; \" ')"
        logs.append(p)

    ch_in = agent.get("channels") if isinstance(agent.get("channels"), list) else ["System", "Application"]
    channels = []
    for c in ch_in[:20]:
        c = str(c or "").strip()
        if not c:
            continue
        if not CHANNEL_RE.match(c):
            errors["agent.channels"] = f"ชื่อ channel ไม่ถูกต้อง: {c[:60]}"
        channels.append(c)

    group = clean_group(data.get("group"), errors)
    paused = bool(data.get("paused", False))
    thresholds = clean_thresholds(data.get("thresholds"), errors)
    if errors:
        raise ValidationError(errors)
    return {"name": name, "type": "agent", "os": "windows", "host": host, "group": group, "paused": paused,
            "thresholds": thresholds, "agent": {"logs": logs, "channels": channels}}


def is_agent(t):
    return isinstance(t, dict) and str(t.get("type") or "ssh") == "agent"


def validate_target(data, existing_names, editing=None):
    """ตรวจ + จัดรูปข้อมูล target หนึ่งเครื่อง  คืน dict ที่พร้อมเขียนลง yaml
    ผิด -> raise ValidationError พร้อมข้อความรายช่อง"""
    errors = {}
    if not isinstance(data, dict):
        raise ValidationError({"_": "invalid body"})
    if str(data.get("type") or "ssh") not in TARGET_TYPES:
        raise ValidationError({"type": "ชนิดเครื่องต้องเป็น ssh หรือ agent"})
    if data.get("type") == "agent":
        return validate_agent_target(data, existing_names, editing)

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
    if not instance and _valid_host(host):
        instance = f"[{host}]:9100" if ":" in host else f"{host}:9100"
    if not INSTANCE_RE.match(instance) or int(instance.rsplit(":", 1)[1]) > 65535:
        errors["prometheus.instance"] = "รูปแบบ host:port เช่น 10.0.10.11:9100"

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
    paused = bool(data.get("paused", False))
    thresholds = clean_thresholds(data.get("thresholds"), errors)

    if errors:
        raise ValidationError(errors)

    return {
        "name": name,
        "host": host,
        "group": group,
        "paused": paused,
        "thresholds": thresholds,
        "prometheus": {"job": job, "instance": instance},
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


def _agent_node(t):
    a = CommentedMap()
    logs = CommentedSeq()
    logs.extend(t["agent"]["logs"])
    a["logs"] = logs
    ch = CommentedSeq(t["agent"]["channels"])
    ch.fa.set_flow_style()
    a["channels"] = ch
    return a


def _to_node(t):
    node = CommentedMap()
    node["name"] = t["name"]
    if is_agent(t):
        node["type"] = "agent"
        node["os"] = t.get("os", "windows")
        if t.get("host"):
            node["host"] = t["host"]
        if t.get("group"):
            node["group"] = t["group"]
        if t.get("paused"):
            node["paused"] = True
        if t.get("thresholds"):
            node["thresholds"] = _thresholds_node(t["thresholds"])
        node["agent"] = _agent_node(t)
        return node
    node["host"] = t["host"]
    if t.get("group"):
        node["group"] = t["group"]
    if t.get("paused"):
        node["paused"] = True
    if t.get("thresholds"):
        node["thresholds"] = _thresholds_node(t["thresholds"])
    prom = CommentedMap(t["prometheus"])
    node["prometheus"] = prom
    node["ssh"] = CommentedMap(t["ssh"])
    logs = CommentedSeq()
    for item in t["logs"]:
        logs.append(CommentedMap(item))
    node["logs"] = logs
    return node


def _apply(node, t):
    """แก้ node เดิมในที่ (คงคอมเมนต์ของ node) ยกเว้น name ที่ไม่ให้เปลี่ยน"""
    if is_agent(t) or is_agent(node):
        for key in ("ssh", "prometheus", "logs", "host", "type", "os", "agent"):
            if key in node:
                del node[key]
        for k, v in _to_node(t).items():
            if k != "name":
                node[k] = v
        return
    node["host"] = t["host"]
    _set_or_drop(node, "group", t.get("group") or None)
    _set_or_drop(node, "paused", True if t.get("paused") else None)
    _set_or_drop(node, "thresholds", _thresholds_node(t["thresholds"]) if t.get("thresholds") else None)
    for key in ("prometheus", "ssh"):
        if not isinstance(node.get(key), CommentedMap):
            node[key] = CommentedMap()
        for k, v in t[key].items():
            node[key][k] = v
    logs = CommentedSeq()
    for item in t["logs"]:
        logs.append(CommentedMap(item))
    node["logs"] = logs


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
        if is_agent(web):
            continue                    # agent ส่ง metrics เข้ามาเอง (remote write) ไม่ต้องให้ Prometheus ไปดึง
        if web.get("name") and prom.get("job") and prom.get("instance"):
            out.append({
                "targets": [prom["instance"]],
                "labels": {"job": prom["job"], "web": web["name"], "environment": environment},
            })
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

def _view(t):
    t = _plain(t)
    if is_agent(t):
        agent = t.get("agent") or {}
        return {"name": t.get("name"), "type": "agent", "os": t.get("os") or "windows", "host": t.get("host") or "",
                "group": t.get("group") or "", "paused": bool(t.get("paused")), "thresholds": t.get("thresholds") or {},
                "agent": {"logs": agent.get("logs") or [], "channels": agent.get("channels") or ["System", "Application"]},
                "watch_services": t.get("watch_services") or []}
    ssh = t.get("ssh") or {}
    port = ssh.get("port", 22)
    return {
        "type": "ssh",
        "name": t.get("name"),
        "host": t.get("host"),
        "group": t.get("group") or "",
        "paused": bool(t.get("paused")),
        "thresholds": t.get("thresholds") or {},
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


def _confirm_host_key(t, payload):
    if is_agent(t):
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
        seq.append(_to_node(t))
        _write_doc(y, doc)
        regenerate_prometheus_targets(doc)
        if not is_agent(t):
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
        _apply(node, t)
        _write_doc(y, doc)
        regenerate_prometheus_targets(doc)
        if not is_agent(t):
            request_pull(name)
    return _view(t)


def set_paused(name, paused):
    """Pause / Resume เครื่อง (puller ข้ามเครื่องที่ pause: ไม่ดึง log ไม่เก็บสถานะ) ไม่มีเครื่อง -> None"""
    y = _yaml()
    with _locked():
        doc = _load_doc(y)
        node = _find_target(doc, name)
        if node is None:
            return None
        _set_or_drop(node, "paused", True if paused else None)
        _write_doc(y, doc)
        if not paused:
            request_pull(name)          # resume -> ดึงทันที
    return {"name": name, "paused": bool(paused)}


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
    return True


# ------------------------------------------------------------------
# เลือก service ที่จะแสดงในการ์ด Service ของหน้า dashboard (ต่อเครื่อง)
# ------------------------------------------------------------------
# เก็บใน puller.yaml ที่ targets[].watch_services (รายการชื่อ unit เช่น nginx.service)
#   - ว่าง/ไม่มีคีย์ = แสดงทุก service (ค่าเริ่มต้น)
#   - เป็นการตั้งค่าการแสดงผลอย่างเดียว puller ยังเก็บ service ทุกตัวเหมือนเดิม
#   - เก็บฝั่ง server -> ทุกคน/ทุก browser เห็นการเลือกเดียวกัน

MAX_WATCH_SERVICES = 300
_SERVICE_NAME = re.compile(r"^[A-Za-z0-9:_.@\\-]{1,256}$")


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
