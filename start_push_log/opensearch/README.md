# start_push_log/opensearch

ไฟล์ตั้งค่าฝั่ง OpenSearch (ที่เก็บ log) ของสายส่ง log

## central-log-retention-ism-policy.json

**ทำอะไร:** นโยบาย ISM (Index State Management) ของ OpenSearch ให้ลบ index `logs-central-*`
อัตโนมัติเมื่ออายุครบ 90 นาที

**ทำไมต้องมี:** otel เขียน log เข้า index รายชั่วโมง (`logs-central-YYYY.MM.DD.HH` —
ดู `../otel/otelcol.yaml`) ถ้าไม่มีนโยบายลบ ข้อมูลจะโตไม่จำกัด

**ทำงานอย่างไร:** มี 2 สถานะ `hot` (เก็บไว้) -> `delete` (ลบ index) เมื่อ `min_index_age` ครบ
ส่วน `ism_template` ผูกนโยบายกับทุก index ที่ชื่อ `logs-central-*` (ทั้งปัจจุบันและอนาคต)
จึงต้องส่งนโยบายเข้า OpenSearch **ครั้งเดียว** ตอนติดตั้ง (คำสั่งอยู่ใน `docs/INSTALL-TH.md`
หัวข้อ retention) — ไม่ใช่ไฟล์ที่ container อ่านเอง

**จะเปลี่ยนอายุ log:** แก้ `min_index_age` (เช่น `"7d"`) แล้วส่งนโยบายใหม่ตามคู่มือ
เมื่อแก้ ควรดู `docs/INSTALL-TH.md` เรื่อง `if_seq_no` / `if_primary_term` ตอนอัปเดตนโยบายเดิม

**เชื่อมกับ:** otel (`../otel/otelcol.yaml` ตั้งชื่อ index) -> OpenSearch (ใช้นโยบายนี้) ->
web_dashboard (`web_dashboard/backend/logs_service.py` ค้นด้วย `logs-central-*`)

> JSON ใส่คอมเมนต์ในไฟล์ไม่ได้ จึงอธิบายไว้ในไฟล์นี้แทน
