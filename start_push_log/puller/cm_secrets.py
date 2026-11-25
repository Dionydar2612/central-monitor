"""
cm_secrets.py - เก็บรหัสผ่านของเครื่อง Windows (WinRM) แบบเข้ารหัส
====================================================================

ไฟล์นี้มี 2 สำเนาที่ต้องเหมือนกันทุกตัวอักษร (มี test เทียบ):
    start_push_log/puller/cm_secrets.py      (puller อ่านรหัสไปเชื่อมต่อ)
    web_dashboard/backend/cm_secrets.py      (หน้า Settings เขียนรหัส)

ที่เก็บ    data/state/secrets/winrm/<ชื่อเครื่อง>.json   (สิทธิ์ 0600)
           ไม่อยู่ใน puller.yaml / ไม่ส่งออก API / ไม่ลง log / ไม่อยู่ใน backup ของ config
กุญแจ     SHA-256 ของ CENTRAL_SECRET_KEY (docker/.env) -> container ทั้งสองได้ค่าเดียวกัน
           (central-api: CENTRAL_SECRET_KEY, central-puller: CENTRAL_PULLER_TOKEN = ค่าเดียวกัน)
           เปลี่ยน CENTRAL_SECRET_KEY = รหัสเดิมอ่านไม่ได้ -> ใส่รหัสใหม่ในหน้า Settings
วิธีเข้ารหัส  stdlib ล้วน: keystream = HMAC-SHA256(key_enc, nonce || counter) XOR ข้อความ
           + แท็ก HMAC-SHA256(key_mac, nonce || ciphertext)  (แก้ไฟล์ = อ่านไม่ผ่าน ไม่ได้รหัสผิด ๆ)
"""

import base64
import hashlib
import hmac
import json
import os
import re

STATE_DIR = os.environ.get("CENTRAL_STATE_DIR", "/data/state")
SECRET_DIR = os.path.join(STATE_DIR, "secrets", "winrm")
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


class SecretError(Exception):
    pass


def _keys():
    raw = os.environ.get("CENTRAL_SECRET_KEY") or os.environ.get("CENTRAL_PULLER_TOKEN") or ""
    if not raw:
        raise SecretError("CENTRAL_SECRET_KEY is not set - cannot store / read WinRM passwords")
    base = hashlib.sha256(("cm-winrm-v1|" + raw).encode()).digest()
    return hmac.new(base, b"enc", hashlib.sha256).digest(), hmac.new(base, b"mac", hashlib.sha256).digest()


def _stream(key, nonce, n):
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:n])


def encrypt(text):
    k_enc, k_mac = _keys()
    data = text.encode("utf-8")
    nonce = os.urandom(16)
    ct = bytes(a ^ b for a, b in zip(data, _stream(k_enc, nonce, len(data))))
    tag = hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()
    return base64.b64encode(nonce + tag + ct).decode()


def decrypt(blob):
    k_enc, k_mac = _keys()
    try:
        raw = base64.b64decode(blob.encode(), validate=True)
    except (ValueError, AttributeError) as e:
        raise SecretError("stored secret is damaged") from e
    if len(raw) < 48:
        raise SecretError("stored secret is damaged")
    nonce, tag, ct = raw[:16], raw[16:48], raw[48:]
    if not hmac.compare_digest(tag, hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()):
        raise SecretError("stored secret cannot be read (CENTRAL_SECRET_KEY changed?) - enter the password again")
    return bytes(a ^ b for a, b in zip(ct, _stream(k_enc, nonce, len(ct)))).decode("utf-8")


def _path(name):
    if not _NAME.match(str(name or "")):
        raise SecretError("invalid machine name")
    return os.path.join(SECRET_DIR, f"{name}.json")


def set_password(name, password):
    path = _path(name)
    os.makedirs(SECRET_DIR, mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"v": 1, "password": encrypt(password)}, f)
    os.replace(tmp, path)


def get_password(name):
    """คืนรหัสผ่าน หรือ None ถ้ายังไม่ได้ตั้ง  (อ่านไม่ได้ -> SecretError)"""
    try:
        with open(_path(name)) as f:
            doc = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise SecretError(f"cannot read stored secret: {e}") from e
    return decrypt(str(doc.get("password") or ""))


def has_password(name):
    try:
        return os.path.isfile(_path(name))
    except SecretError:
        return False


def delete(name):
    try:
        os.remove(_path(name))
        return True
    except (FileNotFoundError, SecretError):
        return False


def prune_orphans(valid_names):
    """ลบรหัสของเครื่องที่ไม่อยู่ใน puller.yaml แล้ว คืนรายชื่อที่ลบ"""
    removed = []
    if not os.path.isdir(SECRET_DIR):
        return removed
    valid = set(valid_names)
    for fn in os.listdir(SECRET_DIR):
        if fn.endswith(".json") and fn[:-5] not in valid:
            try:
                os.remove(os.path.join(SECRET_DIR, fn))
                removed.append(fn[:-5])
            except OSError:
                pass
    return removed
