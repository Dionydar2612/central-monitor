# เพิ่มเติม: Acknowledge log
- ตาราง Logs: ติ๊กเลือกแถว (หรือเลือกทั้งหมด) -> **Acknowledge selected** + โน้ต (operator ขึ้นไป)
  log ที่รับทราบแล้วถูกซ่อนจากตาราง, ตัวเลข All/Error/Warn, Problem logs, Errors 15m และจุดบนกราฟ (ยังอยู่ใน OpenSearch ตามอายุเดิม)
- **Show acknowledged** = แสดงอีกครั้ง (ป้าย ACK + ใคร/เมื่อไหร่/โน้ต) และ **Unacknowledge** ได้ · บันทึกใน Activity log
- `data/state/log_acks.json` เก็บไม่เกิน 40 วัน · "Only logs with metrics" เปลี่ยนเป็นปิดเป็นค่าเริ่มต้น (แสดงทุก log)

# สรุปการเปลี่ยนแปลง (รอบ 4 พ.ย. 2026 ค่ำ): เติมส่วนที่ขาด (ยกเว้นแจ้งเตือนออกนอกเว็บ)

| เรื่อง | เปลี่ยน |
|---|---|
| log เกือบ real-time | `scheduler.log_interval` (ปริยาย 2m) แยกจากสถานะเครื่อง `scheduler.interval` (1h) · ดึงพร้อมกัน `scheduler.parallel` เครื่อง (ปริยาย 4) · SSH ใช้การเชื่อมต่อเดิมซ้ำ (ControlMaster 5 นาที, ปิดได้ `CENTRAL_SSH_MUX=0`) · `target_stagger` ไม่ใช้แล้ว |
| Acknowledge | หน้า Status & Logs: ปุ่ม Acknowledge + โน้ต (operator ขึ้นไป) · ไม่นับในป้าย ▼/W และไม่ทำให้สถานะเครื่องแดง/เหลือง · หมดอายุเองเมื่อสถานะเปลี่ยน · บันทึกใน Activity log · `data/state/acks.json` |
| เกณฑ์ต่อเครื่อง | Settings -> ฟอร์มเครื่อง: CPU / Memory / Disk warning-error เฉพาะเครื่อง (`targets[].thresholds`) · ใช้ทั้ง sensor, สถานะในต้นไม้กลุ่ม และสีการ์ด KPI |
| กราฟย้อนหลัง sensor | คลิกกราฟในการ์ด sensor -> 1h / 6h / 24h / 7d / 30d พร้อมเส้น warning/error และ min/avg/max |
| HTTPS ในตัว | `./scripts/make-cert.sh` สร้าง self-signed (หรือวางใบรับรองจริงใน `keys/tls/`) -> gunicorn เปิด https เอง (`gunicorn.conf.py`), cookie secure อัตโนมัติ |
| เตือนรหัสเริ่มต้น | (นำออกแล้วตามที่ขอ) |
| การ์ด CPU process | แสดงเวลาของข้อมูล เก่าเกิน 2 รอบเป็นสีแดง "outdated" |
| เทสต์อัตโนมัติ | `tests/` + `./scripts/test.sh` (หรือ `./scripts/test.sh docker`) 13 เทสต์ |
| แก้ bug | `[hidden]` ถูก CSS class ทับ (ปุ่ม INFO ของ viewer ไม่ซ่อน) |

---

# สรุปการเปลี่ยนแปลง (รอบ 4 พ.ย. 2026 เย็น): Sensor status + System log + Activity log

- `puller/sensor_monitor.py` (thread ทุก 30 วินาที): sensor ต่อเครื่อง Availability / CPU average 5m / Memory / Disk ต่อ mount / Log pull (SSH)
  สถานะ OK / Warning / Error / Down / Paused ตามเกณฑ์ (`thresholds:` ใน puller.yaml, ปริยาย CPU 75/90 · Memory 80/90 · Disk 80/90)
  -> `data/state/sensors.json` และทุกครั้งที่สถานะเปลี่ยนเขียน System log (`data/state/events.log`)
- Activity log (`data/state/audit.log`): ล็อกอิน (สำเร็จ/ไม่สำเร็จ), ออกจากระบบ, เพิ่ม/แก้/ลบ/Pause เครื่อง, Deploy key, ผู้ใช้ ฯลฯ พร้อมผู้ใช้ + IP (ไม่เก็บรหัสผ่าน) ดูได้เฉพาะ admin
- ป้ายบนหัวทุกหน้า: New Log Entries · ▼ Down/Error · W Warning · ✓ OK · II Paused -> หน้าใหม่ `/events` (การ์ด sensor ตามสถานะ + กราฟ 1 ชม., System log, Activity log)
- docker-compose: central-puller ได้ `PROMETHEUS_URL` + `host.docker.internal`

---

# สรุปการเปลี่ยนแปลง (รอบ 4 พ.ย. 2026 บ่าย)






## เครื่อง Windows ผ่าน agent (Fluent Bit) - ขั้นที่ 1: สถานะ + log

คู่มือทีละขั้น: docs/WINDOWS-AGENT-TH.md
- Settings > Add target > ชนิด "Windows (agent / Fluent Bit)": ระบุ Event Log channels + path ไฟล์ log
  บันทึกแล้วมีปุ่มดาวน์โหลดชุดติดตั้ง (zip) + คำสั่งติดตั้งที่ใส่ token ให้แล้ว
- agent/windows/install-fluentbit.ps1: ติดตั้ง Fluent Bit เป็น service `cm-agent` ส่งเฉพาะ ERROR/WARN
  (Event Log Critical/Error/Warning, ไฟล์ log, IIS เฉพาะ HTTP 5xx) + สัญญาณ "ยังทำงาน" ทุก 30 วินาที
  central ล่ม -> พักไว้บนดิสก์แล้วส่งตาม
- central-agent-gw (container ใหม่, พอร์ต 4318 HTTPS + token): log -> OpenSearch index เดิม, สัญญาณ -> Prometheus (remote write)
  แยกจาก otel เดิม -> ถ้ามีปัญหาไม่กระทบ log ของเครื่อง Linux
- install.sh สร้าง CENTRAL_AGENT_TOKEN + ใบรับรอง keys/agent-tls ให้ (make-cert.sh agent <IP/ชื่อ> สร้างใหม่ได้)
- Prometheus ที่มากับ stack เปิด --web.enable-remote-write-receiver
- หน้า Dashboard: เครื่อง Windows มีป้าย Win · สถานะ OK/Down จากสัญญาณของ agent · Last pull = เวลาที่ได้ข้อมูลล่าสุด
  log ของ Windows แสดงทั้งหมด (ยังไม่มี metrics ให้ผูก) · puller / sensor ข้ามเครื่องชนิด agent
- ยังไม่มี (ขั้นที่ 2): CPU / Memory / Disk / Service / Process ของ Windows

## หน้าแรกแสดงแค่ WEB status + Logs ทุกเครื่อง · ตั้งอายุข้อมูลจากหน้า Settings

- เปิดหน้าแรกเห็นแค่ WEB status และ Logs · คลิกเครื่องแล้วค่อยโหลด KPI / กราฟ / Service / Process ของเครื่องนั้น
  คลิกเครื่องเดิมซ้ำ หรือกด "Close details" = ปิด · ลิงก์ /?web=<ชื่อ> ยังเปิดเครื่องนั้นได้ตรง ๆ
- Logs: ปริยายแสดง log ของทุกเครื่อง · dropdown "Machine" เลือกดูทีละเครื่อง (แทน This WEB / All WEB เดิม ใช้กับ Search ด้วย)
  Load INFO log ต้องเลือกเครื่องก่อน (ดึงสดจากเครื่องเดียว)
- คอลัมน์ Metrics ย่อเหลือ CPU / Mem / Disk (สีตามเกณฑ์) ติ๊ก "Metrics" เพื่อซ่อน/แสดง (จำไว้ใน browser)
  Message ใช้ความกว้างที่เหลือทั้งหมด ชี้เมาส์ดูข้อความเต็ม · รายละเอียดเต็มอยู่ในหน้าต่าง log เหมือนเดิม
- Settings > Data retention: ตั้งอายุ log ใน OpenSearch, Incident history, Machine state history (ชั่วโมง/วัน)
  - log: อัปเดต ISM policy + ผูก index เดิมให้ใช้ค่าใหม่ด้วย (ลบของเก่าเกินในรอบตรวจถัดไป ~5 นาที)
    ค่าที่ตั้งจำไว้ที่ data/state/retention.json -> cm-update / install.sh ไม่เขียนทับกลับ
  - incident / machine state: เขียนลง puller.yaml (storage.*) คงคอมเมนต์เดิม
  - แสดงขนาด/จำนวน log ที่เก็บอยู่ และอายุ metrics ของ Prometheus (เตือนถ้า log เก็บนานกว่า metrics)
- อายุ metrics ของ Prometheus ที่มากับ stack ตั้งได้ที่ PROMETHEUS_RETENTION ใน docker/.env (ปริยาย 7d)
- API: /api/problems, /api/log-stats รับ web=all · GET/PUT /api/retention (admin)

## วงกลมที่มีเลขบนกราฟ -> เมนูเลือก log

- คลิกวงกลมที่มีเลข (หลาย log อยู่ใกล้กัน) จะขึ้นเมนูรายการ log ในกลุ่มนั้น: ERROR ก่อน ใหม่ก่อน, เวลา, ชนิด, ข้อความย่อ
- คลิกรายการ -> เปิดหน้าต่างรายละเอียด log เดิม · วงกลมที่มี log เดียว คลิกแล้วเปิดเลยเหมือนเดิม
- ปิดเมนู: คลิกที่อื่น / กด Esc / ปุ่ม ×

## ไม่แสดง log ที่ไม่มี metrics

- log ที่ Prometheus ไม่มีข้อมูลของเครื่อง ณ เวลานั้น (เก่ากว่าที่ Prometheus เก็บ / เครื่องที่ไม่ได้เก็บ metrics / ช่วง exporter ล่ม) ไม่แสดงและไม่นับแล้ว
  ทั้งตาราง Logs, ตัวเลขบนปุ่ม level, จุดบนกราฟ, Problem logs, Errors 15m และผล Search
- เอาช่องติ๊ก "Only logs with metrics" ออก (เป็นแบบนี้เสมอ)
- log ไม่ถูกลบ ยังอยู่ใน OpenSearch จนหมดอายุตามปกติ · ถ้าถาม Prometheus ไม่ได้ จะแสดงทั้งหมดแทน (กันหน้าโล่ง)

## ปัญหาที่เกิดแล้วหายเอง (incident) + กันเด้ง

- **บอกว่าเป็นอยู่นานเท่าไหร่**: ตอนกลับเป็น OK, System log จะขึ้น `↺ Recovered → OK after 12m (Error · peak 95.0%)`
- **ประวัติปัญหา**: หน้า Events มีแท็บ `↺ Recovered (24h)` และ `Incident history` (เริ่ม / จบ / นาน / สถานะแย่สุด / ค่าสูงสุด / ยังเป็นอยู่ไหม)
  เก็บที่ `data/state/incidents.json` ตาม `storage.incident_retention` (ปริยาย 30d, สูงสุด 20000 รายการ) · API `GET /api/incidents?hours=24`
- **จับ spike สั้นๆ**: memory/disk ใช้ค่าสูงสุดใน 1 นาทีล่าสุดจาก Prometheus (`max_over_time[1m:15s]`) ไม่ใช่แค่ค่า ณ ตอนตรวจ
- **กันเด้งไปมา**: `sensors.confirm_checks` (ปริยาย 2 รอบติด ≈ 1 นาที) และ `sensors.hysteresis` (ปริยาย 2 จุด: Warning 80% กลับ OK เมื่อ < 78%)
- ป้ายหัวเว็บมี `↺ N` = จำนวนปัญหาที่หายเองใน 24 ชม.

## กลุ่ม + Pause
- `targets[].group` (เช่น `TQ/IT`, ลึกได้ 5 ชั้น) และ `targets[].paused` ใน puller.yaml · ตั้งที่หน้า Settings ได้
- puller ข้ามเครื่องที่ pause (ไม่ SSH ไม่ดึง log) · Resume แล้วดึงทันที
- Dashboard การ์ด WEB status เป็นต้นไม้กลุ่ม: ป้ายนับสถานะต่อกลุ่ม (▼ Down/Problem · W Warning · ✓ OK · II Paused) ยุบ/ขยาย (จำใน browser), ช่องกรอง, ปุ่ม Pause/Resume ต่อแถว

## Web uptime + SSL
- `checks:` ใน puller.yaml + หน้า Settings -> Web checks · ตัวเช็ค `puller/web_checks.py` (thread ใน central-puller)
- ผล: UP/Warning/Down, HTTP code, เวลาตอบสนอง, uptime 24 ชม., วันหมดอายุใบรับรอง -> `data/state/checks.json`

## ค้นหา log
- ช่อง Search ในการ์ด Logs ค้นทั้ง OpenSearch · คำ / "วลี" / a | b / -ไม่เอา / prefix* · เครื่องนี้หรือทุกเครื่อง · ไฮไลต์คำที่เจอ

## ผู้ใช้ + สิทธิ์
- `data/state/users.json` (PBKDF2) · role viewer / operator / admin · หน้า Settings -> Users · ทุก API ตรวจสิทธิ์
- บัญชีจาก docker/.env เป็น admin เสมอ

---

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
