"""
apply-ism.py - ตั้ง/อัปเดต ISM policy ลบ log อัตโนมัติใน OpenSearch
==================================================================
รันภายใน container central-api (install.sh เรียกให้):
    docker compose exec -T central-api python3 -c "$(cat scripts/apply-ism.py)" < policy.json

อ่าน   : policy JSON จาก stdin
ใช้ env : OPENSEARCH_ADMIN_PASSWORD (จำเป็น), OPENSEARCH_LOG_RETENTION (เช่น 90m, 12h, 7d ปริยาย 7d)
          ถ้าตั้งจากหน้า Settings > Data retention ไว้ (data/state/retention.json) ใช้ค่านั้นก่อน
ทำอะไร  : แทนค่า min_index_age ใน policy ด้วย OPENSEARCH_LOG_RETENTION
          - ยังไม่มี policy -> สร้าง
          - มีอยู่แล้ว      -> ดึง seq_no/primary_term แล้วอัปเดต (เดิมได้ 409 แล้วค่าใหม่ไม่ถูกใช้)
          policy ใหม่มีผลกับ index ที่สร้างหลังจากนี้ ส่วน index เดิมที่ผูก policy เก่าอยู่ ใช้ค่าเดิมจนครบอายุ
สำเร็จ  : พิมพ์บรรทัดขึ้นต้น ISM-OK (install.sh ตรวจคำนี้)
"""
import base64, json, os, re, ssl, sys, urllib.error, urllib.request

URL = "https://opensearch:9200/_plugins/_ism/policies/central-log-retention"
retention = os.environ.get("OPENSEARCH_LOG_RETENTION", "7d").strip() or "7d"
# ค่าที่ตั้งจากหน้า Settings > Data retention มาก่อน .env (ไม่งั้น cm-update จะเขียนทับกลับเป็นค่าเดิม)
try:
    with open(os.path.join(os.environ.get("CENTRAL_STATE_DIR", "/data/state"), "retention.json")) as _f:
        retention = (json.load(_f).get("log_retention") or retention).strip()
except (OSError, ValueError, AttributeError):
    pass

if not re.fullmatch(r"\d+(m|h|d)", retention):
    sys.exit(f"OPENSEARCH_LOG_RETENTION ไม่ถูกต้อง: {retention!r} (ตัวอย่าง 90m, 12h, 7d)")

policy = json.load(sys.stdin)
for state in policy["policy"]["states"]:
    for tr in state.get("transitions", []):
        cond = tr.get("conditions", {})
        if "min_index_age" in cond:
            cond["min_index_age"] = retention

auth = base64.b64encode(f"admin:{os.environ['OPENSEARCH_ADMIN_PASSWORD']}".encode()).decode()
ctx = ssl._create_unverified_context()     # OpenSearch ใช้ใบรับรอง self-signed ภายใน docker network


def call(method, url, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None)
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


status, existing = call("GET", URL)
if status == 200:
    status, out = call("PUT", f"{URL}?if_seq_no={existing['_seq_no']}&if_primary_term={existing['_primary_term']}", policy)
    action = "updated"
elif status == 404:
    status, out = call("PUT", URL, policy)
    action = "created"
else:
    sys.exit(f"GET policy -> HTTP {status}: {existing}")

if status in (200, 201):
    # index ที่มีอยู่แล้วให้ใช้ policy เวอร์ชันใหม่ด้วย
    call("POST", "https://opensearch:9200/_plugins/_ism/change_policy/logs-central-*", {"policy_id": "central-log-retention"})
    print(f"ISM-OK policy {action}: delete logs older than {retention}")
else:
    sys.exit(f"PUT policy -> HTTP {status}: {out}")
