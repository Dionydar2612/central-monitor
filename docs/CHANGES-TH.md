# สรุปการเปลี่ยนแปลง (รอบ 4 พ.ย. 2026)

## 1) ไม่เก็บ log ระดับ INFO + ปุ่ม "Load INFO log"
| ไฟล์ | เปลี่ยน |
|---|---|
| `puller/log_filter.py` (ใหม่), `puller/log_levels.py` (สำเนาของฝั่งเว็บ) | แยก log เป็นรายการ (บรรทัดต่อ เช่น stack trace ไปกับบรรทัดก่อนหน้า) แล้วจัดระดับด้วยตัวเดียวกับหน้าเว็บ |
| `puller/log_puller.py` | ตัด INFO ทิ้งก่อนเขียน `data/buffer` → INFO ไม่เข้า OpenSearch · ก้อนที่เป็น INFO ล้วนไม่เขียนไฟล์ แต่ checkpoint ยังเดินต่อ |
| `config/puller.yaml` | `storage.store_info_logs: false` (เปิดเป็น `true` ถ้าอยากกลับไปเก็บ INFO) |
| `puller/ondemand.py` (ใหม่), `puller.py` | HTTP ภายในพอร์ต 8001 (`/info-logs`) ดึง N บรรทัดท้ายของทุก log ของเครื่อง คืนเฉพาะ INFO · ไม่เขียนไฟล์/ไม่เก็บ · ต้องมี header token |
| `backend/api.py, logs_service.py, clients.py, settings.py` | `/api/info-logs` ส่งต่อไป puller · `/api/problems`, `/api/log-stats`, ตัวนับ แสดงเฉพาะ ERROR/WARN (INFO เก่าที่ค้างใน OpenSearch ถูกซ่อนจนหมดอายุ) |
| `docker/docker-compose.yml` | `CENTRAL_PULLER_TOKEN` (= `CENTRAL_SECRET_KEY`) ให้ทั้ง puller และ api · `PULLER_URL` ของ api (ปริยาย `http://central-puller:8001`; ถ้ารัน puller ด้วย systemd ให้ตั้งเป็น `http://host.docker.internal:8001`) |
| `frontend` | ลบแท็บ Info · เพิ่มเลือกจำนวนบรรทัด + ปุ่ม **Load INFO log** / **Close INFO** (ผลอยู่ในหน้าเว็บอย่างเดียว ถ้าเลือกช่วงเวลาไว้จะกรองตามช่วงนั้น) |

## 2) journal log
| ไฟล์ | เปลี่ยน |
|---|---|
| `puller/journal.py` (ใหม่), `log_puller.py` | `logs[].path: journal` หรือ `journal:nginx.service,ssh.service` · จำตำแหน่งด้วย cursor ของ journal · ครั้งแรกดึงย้อนหลัง 1 รอบ `scheduler.interval` · เก็บเฉพาะ priority 0-4 (ERROR/WARN) · 1 รายการ = 1 บรรทัด `<เวลา UTC> [LEVEL] <host> <โปรแกรม>[pid]: ข้อความ` |
| `backend/targets_service.py`, `settings.html/js` | รับ path `journal...` · ปุ่ม **+ Add journal log** · คำสั่งตั้งค่าเครื่องเพิ่ม `usermod -aG systemd-journal` |
| `otel/otelcol.yaml` | ชื่อ source `journal` ใช้ parser `application` |
| `log_levels.py` (ทั้ง 2 ชุด) | `<เวลา> [LEVEL]` ต้นบรรทัดถือเป็นระดับที่แน่นอนที่สุด |

## 3) การ์ด KPI
- ชื่อใหม่: **CPU average 5m**, **Memory Usage %**, **Disk Usage % (Per Mount Point)**, **Disk I/O utilization (%)**
- Disk: ตัวเลขใหญ่ = mount point ที่เต็มที่สุด (เดิมดูแค่ `/`) + รายการแต่ละ mount point ใต้การ์ด · สถานะเครื่อง (WARNING/PROBLEM) ใช้ค่าเดียวกันนี้
- กราฟ System Load แท็บ **Load** มีคำอธิบายใต้กราฟ

## 4) CPU process per CPU core
- `puller/system_info.py`: อ่าน `/proc/stat` และ `/proc/<pid>/task/<tid>/stat` 2 ครั้งห่างกัน 1 วินาที → `cpu_cores` ใน snapshot: จำนวน core, % ของแต่ละ core, process ที่รันบน core นั้นและกิน CPU ของ core กี่ % (นับทีละ thread จึงเห็น process เดียวกระจายหลาย core ได้)
- หน้าเว็บ: การ์ด 1 กล่องต่อ core · % หัวกล่องเป็นค่าเฉลี่ย 5 นาทีจาก Prometheus · รายการ process มาจาก snapshot รอบดึงล่าสุด

## 5) อัปเดตด้วยคำสั่งเดียว `cm-update`
- `scripts/update.sh` (ใหม่) + `install.sh` สร้างลิงก์ `/usr/local/bin/cm-update` → พิมพ์ `cm-update` จากที่ไหนก็ได้
- `config/puller.yaml` ออกจาก git (ต้นแบบ = `config/puller.example.yaml`, install.sh คัดลอกให้ถ้ายังไม่มี) → pull แล้วไม่ทับรายชื่อเครื่อง
- update.sh สำรอง `puller.yaml` + `.env` ไว้ที่ `data/state/update-backup/` (เก็บ 10 ชุดล่าสุด) ทุกครั้ง

---

# สรุปการเปลี่ยนแปลง (รอบตรวจ 20 ต.ค. 2026)

## 1) log: เวลา, การกรอง error, การนับ
| ไฟล์ | เปลี่ยน |
|---|---|
| `start_push_log/otel/otelcol.yaml` | ทุก receiver อ่านเวลา ISO ต้นบรรทัด (Z หรือ +07:00, มี/ไม่มีเศษวินาที) เป็นเวลาของ record · เพิ่ม `filelog/other` รับไฟล์ที่ชื่อ source ไม่อยู่ใน 6 ชื่อที่รู้จัก (เดิมถูกลบทิ้งเงียบ ๆ) · multi-line ของ logapp คงเดิม · ตรวจกับ otelcol-contrib 0.160.0 จริงแล้ว |
| `web_dashboard/backend/log_levels.py` (ใหม่) | จัด ERROR/WARN/INFO: เชื่อระดับที่ log บอกเอง (`level=`, `[ERROR]`, `INFO`) ก่อน · ตัดวลีไม่ใช่ปัญหา (`0 errors`, `errors=0`, `error.log`) · `oom` ใช้ขอบเขตคำ |
| `web_dashboard/backend/logs_service.py` | เขียนใหม่: สแกนเป็นหน้า ๆ (สูงสุด 10,000 เอกสาร) แล้วกรองเครื่อง · รายการ/ตัวนับ/ตัวกรองคำนวณจากผลสแกนเดียวกัน · เวลาสำรองเมื่อ @timestamp เป็น 1970 (ต้นข้อความ → เวลาดึงไฟล์) · ลบ regex ซ้ำที่ไม่เคยทำงาน |
| `api.py` | `/api/problems` รับ `level=` และ `type=` · `/api/log-stats` ใหม่ · หน้า Settings แสดงสาเหตุจริงเมื่ออ่าน `puller.yaml` ไม่ได้ (เดิมได้แค่ INTERNAL SERVER ERROR) · `/api/web-status` แสดงเฉพาะเครื่องใน `puller.yaml` ตรงกับหน้า Settings (ไม่มี `central`) |
| `frontend/index.html, dashboard.js, dashboard.css` | แถบกรอง Level (All/Error/Warn/Info + จำนวน) และ Type ในการ์ด Logs |

## 2) การเคลียร์ข้อมูล
| ไฟล์ | เปลี่ยน |
|---|---|
| `config/puller.yaml` | `buffer_retention` 30m → 2h |
| `.env.example`, `scripts/apply-ism.py` (ใหม่), policy JSON, `install.sh` | อายุ log ใน OpenSearch ตั้งที่ `OPENSEARCH_LOG_RETENTION` (**ปริยาย 7d** เดิม 90m) · รันซ้ำจะอัปเดต policy จริง (เดิมได้ 409 แล้วค่าใหม่ไม่ถูกใช้) |
| `puller/scheduler.py, checkpoint.py, system_info.py` | รอบเต็มลบ checkpoint + snapshot + history ของเครื่องที่ถูกลบจาก `puller.yaml` |
| `puller/system_info.py` | เข้าเครื่องไม่ได้ → ไม่เขียนทับ snapshot เดิมด้วยค่าว่าง |

## 3) แก้ bug / ความเสี่ยง
- `init-env.sh`: รหัสผ่านที่มี `& | \` ไม่พัง `.env` แล้ว
- `install.sh --keep`: คง `.env` และ SSH key เดิมตอนอัปเกรด (ไม่ต้อง Deploy key ใหม่ทุกเครื่อง) · แก้เลขขั้นตอนในคอมเมนต์
- ชื่อเครื่อง `30K3PH4` ไม่ฝังในโค้ดแล้ว → ตั้ง `aliases: [<hostname>]` ใน `puller.yaml` ได้
- `config/puller.yaml` ที่มากับ zip เริ่มต้นว่าง (`targets: []`) มีตัวอย่างเป็นคอมเมนต์ · หน้า Dashboard ว่างแสดง "No WEB configured" แทนเครื่องตัวอย่าง
- `classify_event`: "room" / "zoom" ไม่ถูกจัดเป็น OOM อีก
- เพิ่ม `.dockerignore` (ไม่ส่ง `keys/`, `data/`, `.env` เข้า build context) · ลบไฟล์ `*.bak*` · `.gitignore` ครอบ `*.bak.*`

## ยังไม่ได้ทำ
- **Windows support** (ไม่มีเครื่องทดสอบ) — ทั้ง ssh_client / system_info / PATH validation / Deploy key / PromQL ยังเป็น Linux ล้วน
- สแกนดูได้สูงสุด 10,000 เอกสารล่าสุดต่อครั้ง (ข้อจำกัด `max_result_window`) ถ้าเครื่องอื่นส่ง log มากกว่านั้นแซงหน้า log เก่าของเครื่องนี้จะไม่ถูกเห็น
