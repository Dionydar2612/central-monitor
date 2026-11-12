"""
fingerprint.py - ลายนิ้วมือของ log ("ปัญหาเดียวกัน" = fp เดียวกัน)
================================================================

ทำไมต้องมีไฟล์นี้
    error เดียวกันพ่นออกมาเป็นพันบรรทัด ต่างกันแค่เวลา / ตัวเลข / IP / id
    ถ้านับทีละบรรทัด ตารางจะรกและตัวนับพุ่ง จึงต้องรู้ก่อนว่าบรรทัดไหนคือ "ปัญหาเดียวกัน"

วิธีคิด fp
    1) ใช้บรรทัดแรก + 2 บรรทัดถัดไป (ส่วนต้นของ stack trace) บรรทัดว่างไม่นับ
    2) ตัดเวลาต้นบรรทัด  เช่น "2026-10-05T09:12:58.123+07:00" / "[2026-10-05 09:12:58]" / "Oct  5 09:12:58"
    3) แทนค่าที่เปลี่ยนทุกครั้งด้วย placeholder (ตามลำดับ)
         UUID -> <uuid> · IPv4 / IPv6 (+พอร์ต) -> <ip> · 0x1f2e / hex ยาว ๆ -> <hex>
         pid=123 / pid 123 / name[123]: -> <pid> · ตัวเลขที่เหลือ -> <n>
    4) ช่องว่างหลายตัวรวมเป็นตัวเดียว ไม่สนตัวพิมพ์เล็ก/ใหญ่
    5) sha1 ของข้อความที่ได้ ย่อ 12 ตัวอักษร

    คำนวณตอนอ่าน ไม่แตะข้อมูลที่เก็บใน OpenSearch (เปลี่ยนสูตรได้โดยไม่ต้อง reindex)

คืนค่า: fingerprint(message) -> "a1b2c3d4e5f6"  (ข้อความว่าง -> "")

!! ไฟล์นี้มี 2 ชุดที่ต้องเหมือนกัน:
     web_dashboard/backend/fingerprint.py   (หน้าเว็บรวมกลุ่ม / ack ตามกลุ่ม)
     start_push_log/puller/fingerprint.py   (puller ยุบบรรทัดซ้ำ / แจ้งเตือนจาก log)
   แก้ที่หนึ่ง ให้คัดลอกไปอีกที่ด้วย (มีเทสต์เทียบใน tests/test_puller.py)
"""

import hashlib
import re

# เวลาต้นบรรทัด (อาจอยู่ในวงเล็บ [] และมีเลขเศษวินาที / โซนเวลา)
_LEAD_TIME = re.compile(
    r"^\s*\[?\s*(?:"
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"   # 2026-10-05T09:12:58.123+07:00
    r"|\d{2}/\d{2}/\d{4}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"                          # 05/10/2026 09:12:58
    r"|[A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2}"                                     # Oct  5 09:12:58
    r"|\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"                                               # 09:12:58.123
    r")\s*\]?\s*"
)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
# IPv6: กลุ่ม hex คั่นด้วย : ต้องมี "::" หรือมีตัว a-f (กันเวลา 09:12:58 ไม่ให้เป็น IP)
_IPV6 = re.compile(r"(?<![\w:])(?=[0-9a-f:]*(?:::|[a-f]))[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,7}(?![\w:])", re.I)
_HEX0X = re.compile(r"\b0x[0-9a-f]+\b", re.I)
_HEXLONG = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,}\b", re.I)   # hash / id ยาว ๆ (มีทั้งตัวเลขและตัวอักษร)
_PID = re.compile(r"\b(pid|tid|process|thread)([\s=:#]+)\d+\b|(?<=[A-Za-z_\-.])\[\d+\](?=:)", re.I)
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_SPACE = re.compile(r"\s+")


def _pid_sub(m):
    return f"{m.group(1)}{m.group(2)}<pid>" if m.group(1) else "[<pid>]"


def normalize_line(line):
    """บรรทัดเดียว -> รูปแบบกลาง (ไม่มีเวลา / ตัวเลข / IP / id)"""
    s = _LEAD_TIME.sub("", str(line or ""), count=1)
    s = _UUID.sub("<uuid>", s)
    s = _IPV4.sub("<ip>", s)
    s = _IPV6.sub("<ip>", s)
    s = _HEX0X.sub("<hex>", s)
    s = _HEXLONG.sub("<hex>", s)
    s = _PID.sub(_pid_sub, s)
    s = _NUM.sub("<n>", s)
    return _SPACE.sub(" ", s).strip().lower()


def normalize(message):
    """ข้อความ log (อาจหลายบรรทัด) -> บรรทัดแรก + 2 บรรทัดถัดไป ที่ผ่าน normalize_line แล้ว"""
    lines = [ln for ln in str(message or "").splitlines() if ln.strip()][:3]
    return "\n".join(normalize_line(ln) for ln in lines)


def fingerprint(message):
    """ลายนิ้วมือ 12 ตัวอักษร (sha1 ย่อ) ข้อความว่าง -> "" """
    text = normalize(message)
    if not text:
        return ""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
