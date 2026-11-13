# เพิ่มเติม (10 พ.ย. 2026): กฎแจ้งเตือนที่สร้างเอง (Custom alert rules) + ส่ง Teams ต่อกฎ
- Settings > **Custom alert rules**: เลือกค่าที่ดู (หรือเขียน PromQL) · เครื่อง / กลุ่ม / OS · ≥ / ≤ · warning / error · นานกี่นาที · ส่ง Teams ไหม · ข้อความของกฎ · Preview กับเครื่องตอนนี้
- เอา Apps & Web ออกจากตาราง WEB status ในหน้าแรก (ดูที่แถบ Apps & Web / หน้า /apps)
- ป๊อปอัปแจ้งเตือนมุมขวาล่างทุกหน้า ขึ้นเองอัตโนมัติ เรื่องเดียวกับ Teams · ตั้งค่า / แจ้งเตือนของ Windows / ทดลอง ที่ Settings > Notifications (เอาปุ่ม 🔔 ออก)
- Apps & Web: ป๊อปอัปรายละเอียด + uptime + กราฟทุกประเภท (ไม่ใช่แค่เว็บ) · metrics เปลี่ยนเป็น cm_app_*
- Monitor เว็บดูได้เหมือนเครื่อง: การ์ดมีกราฟเวลาตอบ + uptime 24h · หน้ารายละเอียด (กราฟ 1h-30d, uptime 24h/7d/30d, ประวัติปัญหา) · แถวในตาราง WEB status + กล่อง Web · เฝ้าดูหลายพอร์ตทีเดียวจากแท็บ Software · ตั้งค่าเพิ่มเติม (คำในหน้า, ตอบช้า, SSL ใกล้หมด, นาทีต่อเนื่อง) · puller ส่ง cm_app_* ให้ Prometheus
- Monitor เว็บ: เลือกโหมดจาก URL เอง (localhost = บนเครื่อง / อื่นๆ = จาก central) · normalize URL (`8090` -> `http://localhost:8090`) · ปุ่มเลือกพอร์ตจาก cm-probe (`GET /api/host/<เครื่อง>/probe-ports`) · ข้อความสถานะแยก 4 กรณี · 2xx/3xx = ทำงาน, 4xx/5xx/0 = ไม่ทำงาน · Monitor เก่าแปลงตอนโหลด
- Apps & Web: แก้ไขรายการได้ · เว็บบนเครื่อง (localhost) และ Docker container (Windows: cm-probe.ps1 ทุก 1 นาที, Linux: ผ่าน SSH) · แถบสรุปบนหน้าแรก · แท็บ Software มี Docker / เว็บบนเครื่อง
- หน้า Apps & Web (`/apps`): ภาพรวมแบบมินิมอลว่าโปรแกรม / เว็บ / พอร์ต / Service ยังทำงานไหม · admin เพิ่ม / ลบได้ในหน้าเดียว (สร้าง Custom alert rule ให้) · การ์ด Watched apps ในแท็บ Software
- Software (Windows): แท็บ Software ในหน้าเครื่อง (โปรแกรมที่ทำงาน / พอร์ตที่เปิดรับ / โปรแกรมที่ติดตั้ง / ประวัติติดตั้ง-ถอน-อัปเดต) · Custom alert rules ใหม่: โปรแกรมไม่ทำงาน, CPU / RAM ของโปรแกรม, พอร์ตไม่เปิดรับ, พอร์ตต่อจาก central ไม่ได้, เว็บไม่ตอบ · แจ้ง Teams เมื่อติดตั้ง / ถอน / อัปเดตโปรแกรม · agent Windows ต้องติดตั้งซ้ำ 1 ครั้ง (cm-inventory.ps1 + textfile collector)
- Daily Summary ใหม่ (Morning / Evening Report): **ห้ามใช้ค่าเฉลี่ย** · ค่าของแต่ละเครื่อง + ค่าสูงสุดในช่วง · Disk แยกไดรฟ์ · UP/DOWN/PAUSE/UNKNOWN · Incident / Downtime / Recovery · Group Summary = ค่าสูงสุดพร้อมชื่อเครื่อง · รอบเย็นเทียบกับรอบเช้า (ดู NOTIFY-TH.md)
- การ์ด Teams แบบ PRTG: หัวเรื่องสีตามระดับ `Central Monitor Notification: <เครื่อง> [<sensor>] <ระดับ>` + Device / Sensor / Status / Last Value / Threshold / Time (ข้อความของกฎ = บรรทัด Message และข้อความบนแถบแจ้งเตือน)
- Settings > Notifications: เอาตัวเลือกเรื่องที่ส่ง / เฉพาะเครื่อง / กลุ่ม / ข้อความการ์ด ออก (ใช้ Custom alert rules แทน) · ค่าเก่าที่บันทึกไว้ไม่ถูกใช้ และจะถูกลบเมื่อกดบันทึกครั้งถัดไป
- รายละเอียด: `docs/NOTIFY-TH.md`

---

# เพิ่มเติม (6 พ.ย. 2026): แจ้งเตือน Microsoft Teams
- Settings > **Notifications (Microsoft Teams)**: วาง webhook URL (Teams Workflows) เลือกระดับ (Down / Error / Warning / Recovered / Web-SSL check) ทดสอบส่งได้
- ส่งการ์ด Adaptive Card ครั้งเดียวต่อการเปลี่ยนสถานะ รวมหลายเรื่องของเครื่องเดียวเป็นการ์ดเดียว มีปุ่มเปิด dashboard
- รายละเอียด: `docs/NOTIFY-TH.md`

---

# สรุปการเปลี่ยนแปลง (6 พ.ย. 2026): Windows Monitoring & Bottleneck Detection

รายละเอียดเต็ม: `docs/MONITORING-TH.md`

| เรื่อง | เปลี่ยน |
|---|---|
| ตัวเก็บ metrics ของ Windows | `install-fluentbit.ps1` ติดตั้ง **windows_exporter 0.32.1** เป็น service `cm-exporter` (ไฟล์ .exe ไม่ใช้ MSI, ทดลองเก็บก่อนเลือกชุด collector) ฟังเฉพาะ `127.0.0.1:9182` ไม่เปิด firewall, ปิด process cmdline · Fluent Bit อ่านด้วย `prometheus_scrape` แล้วส่ง HTTPS + token · ติดตั้งไม่ได้ -> ใช้ตัวในตัว Fluent Bit อัตโนมัติ · พารามิเตอร์ใหม่ `-ExporterExe`, `-ExporterVersion`, `-NoExporter` · `uninstall-fluentbit.ps1` ถอน exporter ด้วย (`-KeepExporter` = เก็บไว้) |
| ชื่อ metric | backend + sensor รองรับทั้งชื่อใหม่ (`windows_cpu_time_seconds_total`, `windows_memory_physical_total_bytes`, label `service`) และชื่อเก่า |
| Ping | container ใหม่ `blackbox` (blackbox_exporter 0.28.0, icmp) + Prometheus job `ping` จาก `data/prometheus/ping.json` (puller สร้างจากทุกเครื่องที่มี host) -> latency / packet loss |
| Sensor ใหม่ | Available RAM (Windows), Disk latency, Packet loss, Network latency, Service สำคัญของ Windows, Bottleneck CPU / Memory / Disk / Network |
| เกณฑ์ | Windows ใช้ค่าตามสเปก (CPU 80/95, RAM 80/95, Disk 80/90, RAM ว่าง 20/10, latency 20/50 ms, loss 1/5 %, ping 100/250 ms) · Linux คงค่าเดิม · `thresholds_windows:` ใหม่ใน puller.yaml |
| ระยะเวลาก่อนแจ้งเตือน | ค่าเริ่มต้น **3 นาที** (`sensors.confirm_checks: 6`) ปรับได้ที่ Settings > Alerts |
| Settings > Alerts | การ์ดใหม่: นาทีก่อนแจ้งเตือน, hysteresis, เปิด/ปิด bottleneck, service สำคัญ, ตารางเกณฑ์ Linux / Windows (`GET/PUT /api/alert-settings`, admin, บันทึก Activity log) · ฟอร์มเครื่องมีเกณฑ์ชนิดใหม่ + service สำคัญเฉพาะเครื่อง |
| WEB status | ไทล์ Up / Down / Warning / Critical · คอลัมน์ OS / Network / Alerts · คลิกหัวคอลัมน์เพื่อเรียง · ตัวกรอง OS / สถานะ · ถาม Prometheus แบบรวม (จำนวน query ไม่ขึ้นกับจำนวนเครื่อง) |
| รายละเอียดเครื่อง | แท็บใช้กับเครื่อง Windows เท่านั้น เครื่อง Linux ใช้หน้าแบบเดิม · ค่าที่อ่านไม่ได้ไม่แสดง · 8 แท็บ: Overview, Performance, Bottleneck, Processes, Services, Hardware, System, Alerts & Events (`machine.js`, `host_details.py`, `GET /api/host/<เครื่อง>/<แท็บ>`) · ค่าที่ไม่มี = Unavailable, ที่อ่านไม่ได้ = Unsupported |

**ต้องทำหลังอัปเดต**: `cm-update` ที่ central (สร้าง container blackbox) แล้วรันคำสั่งติดตั้ง agent ซ้ำบนเครื่อง Windows ทุกเครื่อง

---

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







## เครื่อง Windows ผ่าน agent - ขั้นที่ 2: metrics

- agent เก็บ windows_exporter_metrics ทุก 15 วินาที (cpu, os, cs, memory, logical_disk, net, system, service, process ...)
  ถ้า Fluent Bit เวอร์ชันนั้นไม่รู้จัก collector บางตัว สคริปต์ถอยไปใช้ชุดปริยายเอง
- agent ส่งชื่อเครื่อง/ชนิดข้อมูลใน header X-CM-Web / X-CM-Source (Fluent Bit 5 ส่ง record มาแค่ข้อความ)
- central-agent-gw: ใส่ label cm_web ให้ทุก metric · เวลาของเครื่อง Windows คลาดเกิน 5 นาที -> ใช้เวลาที่รับแทน
- หน้า Dashboard / ภาพรวม / sensor / incident ใช้ PromQL ชุด Windows อัตโนมัติ (metrics_service.WIN_PROMQL)
  Service = windows_service_state · Process = windows_process_* · ไดรฟ์ = volume (C: D:)
- ระหว่างทดสอบแก้: zip ไม่ซ้อนโฟลเดอร์, dry-run ไม่หยุดเพราะ stderr, แสดงเฉพาะ log ของรอบนี้

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
- `targets[].group` (เช่น `TQ/IT`, ลึกได้ 10 ชั้น) และ `targets[].paused` ใน puller.yaml · ตั้งที่หน้า Settings ได้
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

## กลุ่มซ้อนกลุ่มย่อย (group หลายชั้น)
- กลุ่มซ้อนได้ **10 ชั้น** (เดิม 5) คั่นด้วย `/` เช่น `ThaiQuest/TQ/IT/Web/Prod`
- Settings > เครื่อง / Checks: ใต้ช่องกลุ่มมี **ต้นไม้กลุ่ม** — คลิกชื่อ = เลือกกลุ่มนั้น · ปุ่ม **+ ย่อย** = สร้างกลุ่มย่อยใต้กลุ่มนั้น (เติม `กลุ่ม/` ให้พิมพ์ชื่อต่อ) · แสดงเส้นทาง เช่น TQ › IT › Web (ชั้น 3)

## สิทธิ์ของแต่ละ role
| role | ทำอะไรได้ |
|---|---|
| viewer | ดูอย่างเดียว (หน้าแรก, Status & Logs, Apps & Web, รายละเอียดเครื่อง) |
| operator | viewer + Pause/Resume, Acknowledge, Load INFO log, เลือก service · **Settings > Targets**: ปุ่ม "การแจ้งเตือน / Log" แก้แท็บการแจ้งเตือน (เกณฑ์ นาที hysteresis bottleneck service สำคัญ) และ Log ของเครื่อง (มีผลจริงทั้งระบบ) · **Custom alert rules**: สร้าง/แก้/ลบได้ทุกกฎ |
| admin | ทุกอย่าง: เพิ่ม/ลบเครื่อง, ข้อมูลเครื่องและการเชื่อมต่อ (SSH, key, คำสั่งติดตั้ง agent), ผู้ใช้, Notifications (webhook หลัก), Data retention, Apps & Web |
- กฎเก็บ `alert_rules[].owner` = คนสร้าง (คอลัมน์ "สร้างโดย") · operator ส่งช่องอื่นของเครื่องมา (host, group, SSH ...) ระบบไม่รับ คงค่าเดิม · ทุกการแก้บันทึกใน Activity log

## ตั้งค่าการแจ้งเตือนรายเครื่อง + ฟอร์มเครื่องแบบแท็บ
- เอาการ์ด **Alerts** (ค่าทั้งระบบ) ออกจากหน้า Settings · ตั้งที่ฟอร์มของแต่ละเครื่องแทน: แท็บ **การแจ้งเตือน**
  - เงื่อนไข: แจ้งเมื่อเกินต่อเนื่อง (นาที) · Hysteresis · ตรวจ Bottleneck (ค่าเริ่มต้น / ตรวจ / ไม่ตรวจ)
  - เกณฑ์ Warning / Error ของ CPU, Memory, Disk, Available RAM (Windows), Disk latency, Packet loss, Ping latency
  - Service สำคัญของ Windows
  - **ช่องว่าง = ค่าเริ่มต้น** (แสดงเป็นตัวเลขจางในช่อง ตาม OS ของเครื่อง)
- puller.yaml: `targets[].alert: {minutes, hysteresis, bottleneck}` (ไม่ใส่ = ค่าทั้งระบบใน `sensors:`) · กฎที่สร้างเองที่ตั้ง "นาน" ไว้ ใช้ค่าของกฎ
- ฟอร์มเครื่องแบ่งเป็นแท็บ: **ทั่วไป** (ชนิด ชื่อ Host Paused กลุ่ม) · **การเชื่อมต่อ** (SSH / node_exporter / host key หรือคำสั่งติดตั้ง agent) · **Log** · **การแจ้งเตือน** · บันทึกแล้วมี error จะพาไปแท็บที่ผิดให้เอง

## หน้าแรก: เลือกมุมมอง WEB status
- ปุ่มมุมมอง: **ตาราง** (ตามกลุ่ม แบบเดิม) · **ไทล์** (เครื่องละกล่องเล็ก สีตามสถานะ CPU · Mem · Disk) · **การ์ด** (แถบ CPU/Memory/Disk + สถานะ + alert + Pause) · **ปัญหาก่อน** (ตารางเครื่องที่มีปัญหาเรียงตามความรุนแรง + ไทล์เครื่องปกติ) · browser จำมุมมองที่เลือก
- เอาปุ่ม Apps & Web ที่หัวหน้าออก (หน้าแรก / Status & Logs) · เข้า Apps & Web จากแถบ Apps & Web ด้านบนของหน้าแรก

## Sunburst + Overview (แบบ PRTG)
- หน้าแรก WEB status: มุมมองที่ 5 **◔ Sunburst** — วงใน = กลุ่ม (ทีละชั้น) → เครื่อง → วงนอก = sensor ของเครื่อง · สีตามสถานะแย่สุด (เขียว OK / เหลือง Warning / แดง Error-Down / ฟ้า Paused / เทา Unknown) · คลิกกลุ่ม = ซูมเข้า (คลิกวงกลางเพื่อกลับ) · คลิกเครื่อง/sensor = เปิดรายละเอียดเครื่อง
- หน้า Status & Logs: แท็บแรก **📈 Overview** (เปิดเป็นค่าเริ่มต้น) กราฟรวมทุกเครื่อง 2 days / 30 days / 365 days
  - เส้น: Alarms (จำนวนปัญหาที่เปิดอยู่, แกนขวา #) · CPU / Memory / Disk **สูงสุด** (ไม่ใช้ค่าเฉลี่ย, ชี้เส้นเพื่อดูชื่อเครื่อง) · Traffic รวม (แสดงเป็น % ของค่าสูงสุดในช่วง, tooltip เป็น MB/s)
  - ติ๊กเปิด/ปิดแต่ละเส้น · Show all / Hide all · การ์ดค่าสูงสุดของช่วงพร้อมเวลาและเครื่อง
  - API: `GET /api/overview/history?range=2d|30d|365d` (backend/overview_history.py, cache 60 วินาที)
- Overview: ช่อง **แสดง** = ทุกเครื่อง · ค่าสูงสุด / **เปรียบเทียบทุกเครื่อง** (เลือก CPU / Memory / Disk / Traffic / Alarms · 1 เส้น = 1 เครื่อง สีต่างกัน ติ๊กซ่อนเครื่องได้ ชี้กราฟเห็นค่าทุกเครื่องเรียงมากไปน้อย) / เครื่องเดียว · ช่วงที่ไม่มีข้อมูลไม่ลากเส้นต่อ · API เพิ่ม `webs: {web: {cpu, memory, disk, traffic, alarms}}`

## Reload แล้วอยู่หน้าเดิม
- ทุกหน้า: กลับไปตำแหน่ง scroll เดิมหลัง reload (รอข้อมูลโหลดก่อน)
- Status & Logs: แท็บอยู่ใน URL อยู่แล้ว + จำช่วงเวลา / โหมด "แสดง" / metric / เส้นที่ซ่อนของ Overview
- Settings: ฟอร์มเครื่องที่เปิดค้าง + แท็บที่ดูอยู่ เปิดกลับมาเหมือนเดิม (ฟอร์มเพิ่มเครื่องใหม่ที่ยังไม่บันทึกไม่จำ)
- หน้าแรก: เครื่องที่เปิดดู (?web=) แท็บรายละเอียด มุมมอง และกลุ่มที่ยุบ จำไว้อยู่แล้ว

## หน้าเครื่อง + หน้า sensor แบบ PRTG
- รายละเอียดเครื่อง (คลิกเครื่องในหน้าแรก) ส่วน "Sensors" ล่างสุดของรายละเอียดเครื่อง ก่อนตาราง Logs (แสดงทุกแท็บ):
  - กล่องข้อมูล: Status · Last Scan · Uptime / Downtime 30 วัน · Last Down · จำนวน sensor ตามสถานะ · OS / IP · Group
  - เกจใหญ่ CPU / Memory / Disk (ไดรฟ์ที่เต็มสุด) + เกจเล็กของ sensor อื่นที่มีค่า (สีตามสถานะ, แถบโซน OK / Warning / Error)
  - ตาราง Sensors: Pos · Sensor · Status · Message · Graph (แถบค่า) · Limits — คลิกชื่อเปิดหน้า sensor
- หน้า sensor ใหม่ `/sensor?id=...` (คลิกชื่อ sensor ในหน้าเครื่อง หรือการ์ดใน Status & Logs):
  - หัวสีตามสถานะ + เส้นทาง Devices » กลุ่ม » เครื่อง » sensor
  - แท็บ Overview · Live Data (2 ชม. รีเฟรช 30 วิ) · 2 days · 30 days · 365 days · Log (ประวัติปัญหา)
  - กล่องข้อมูล: Last Scan · Last Up · Last Down · Uptime / Downtime 30 วัน (นับเฉพาะ Down / Error) · Sensor Type · Status since · Interval · Limits · ID
  - Overview: เกจใหญ่ + ตาราง Channel (ค่าล่าสุด / ต่ำสุด / สูงสุด 2 วัน / Limits) + กราฟ 2 วัน
  - แท็บกราฟ: เส้นประ Warning / Error, จุด Min / Max, ตารางค่าย้อนหลัง (แถวสรุป ต่ำสุด / สูงสุด — ไม่มีค่าเฉลี่ย)
  - API: `GET /api/sensors/detail?id=` · `/api/sensors/history` รองรับ range live / 2d / 365d เพิ่ม + min / max / last
- กราฟมีเฉพาะ sensor CPU / Memory / Disk / Availability และ sensor ที่มี PromQL (latency, packet loss, กฎที่สร้างเอง) — ชนิดอื่นแท็บกราฟจะกดไม่ได้
- จัดส่วน Sensors ของเครื่องใหม่: หัว "Sensors" + จำนวนตามสถานะ + Uptime / Downtime / Down ล่าสุด / ตรวจล่าสุด · เกจขนาดเท่ากัน (เฉพาะค่าตัวเลข) · รายการแยกหมวด ประสิทธิภาพ / การเชื่อมต่อ-เครือข่าย / Service-โปรแกรม / Bottleneck / อื่น ๆ (พับได้ หมวดที่ปกติทั้งหมดพับไว้ จำการพับระหว่างรีเฟรช) · คลิกแถวหรือเกจ = หน้า sensor
- เอาข้อความอธิบายใต้ชื่อหน้า (หน้าแรก / Status & Logs / Apps & Web / Settings) และข้อความแนะนำวิธีคลิกออก
- คลิก sensor ที่ไหนก็เปิดหน้า sensor: วงนอกของ Sunburst · การ์ด sensor ใน Status & Logs (ทั้งการ์ด ยกเว้นปุ่ม/กราฟเล็ก) · ชื่อ sensor ในแท็บ Alerts & Events ของเครื่อง · ส่วน Sensors ของเครื่อง

## ลด log ซ้ำ ขั้นที่ 1: fingerprint (ลายนิ้วมือของ log)
- ไฟล์ใหม่ `fingerprint.py` 2 ชุดที่ต้องเหมือนกัน (`web_dashboard/backend/` และ `start_push_log/puller/`) มีเทสต์เทียบ
  - ใช้บรรทัดแรก + 2 บรรทัดถัดไป (ต้น stack trace) · ตัดเวลาต้นบรรทัด · แทน UUID / IP (v4, v6 + พอร์ต) / hex / PID / ตัวเลข ด้วย placeholder · sha1 ย่อ 12 ตัว
- `logs_service._light_entry` เพิ่ม field `fp` ให้ทุกแถว log (คำนวณตอนอ่าน ไม่แตะข้อมูลใน OpenSearch)
- ยังไม่มีอะไรเปลี่ยนบนหน้าจอ (ขั้นที่ 2 จะใช้ fp รวมกลุ่ม)

## ลด log ซ้ำ ขั้นที่ 2: รวมกลุ่ม + นับเป็นจำนวนชนิด
- `logs_service.group_entries()` / `get_grouped_logs()` · API ใหม่ `GET /api/logs/grouped` (ตัวกรองเหมือน `/api/problems`) คืน fp, level (แย่สุด), sample (บรรทัดล่าสุด), count, first, last, machines, webs, types
- `get_log_stats` เพิ่ม `kinds` (จำนวน fp ไม่ซ้ำ ต่อ level / type / ทั้งหมด) · `get_problem_count` / `get_errors_15m` รับ `kinds=True` · `/api/summary` เพิ่ม `problem_kinds`, `errors_15m_kinds`
- หน้าแรก ตาราง Logs: สวิตช์ **Group duplicates** (ค่าเริ่มต้นเปิด, จำใน browser `cm.logsGroup`) · ตัวเลขบนปุ่ม level = จำนวนชนิดเมื่อรวมกลุ่ม · แถบสรุปเครื่อง "Problem logs 5120 (3 ชนิด)"
- ไม่แตะข้อมูลใน OpenSearch และไม่แตะ puller · ปิดสวิตช์ = ตารางเดิมทุกอย่าง
- หน้า Status & Logs (events.js) ไม่ได้แก้: แท็บ System log เป็นเหตุการณ์ของ sensor ไม่ใช่ log จาก OpenSearch

## ลด log ซ้ำ ขั้นที่ 3: Acknowledge ตาม fingerprint
- `log_ack_service.ack_fp(fp, user, note, until_hours=24, web=None)` / `unack_fp(fp)` / `ack_of(entry)` · เก็บใน `data/state/log_acks.json` คีย์ `fp:<fp>` = {by, at, note, web, until, fp} (ไฟล์เดียวกับ ack ตาม _id)
- กรอง (`_not_acked` / `_public`): ack ถ้า _id ตรง (แบบเดิม) **หรือ** fp ตรง + ยังไม่หมด until + (ถ้าระบุ web) เครื่องตรง · หมดเวลา = กลับมาแสดงเอง และถูกลบออกจากไฟล์ตอนเขียนครั้งถัดไป
- API `POST /api/logs/ack-fp` {fp, hours, note, web} · `POST /api/logs/unack-fp` {fp} (operator ขึ้นไป, บันทึก Activity log `log.ack_fp` / `log.unack_fp`)
- หน้าแรก ตาราง Logs แบบรวมกลุ่ม: ปุ่ม Ack กลุ่ม + เลือก 1 ชม. / 24 ชม. / 7 วัน · แถวที่ ack แล้ว (Show acknowledged) มีป้าย ACK + "ยกเลิก ack"
- แก้ cache ของ log_acks.json: เขียนแล้วอัปเดต cache ทันที (เดิมเขียน 2 ครั้งในวินาทีเดียวอาจอ่านค่าเก่า)

## ลด log ซ้ำ ขั้นที่ 4: ยุบที่ puller (ค่าเริ่มต้นปิด)
- `log_filter.collapse_repeats(data, keep=20)` ใช้ `split_records` + `fingerprint` (puller): ต่อรอบดึง เก็บต่อ fp ไม่เกิน keep รายการ ที่เหลือยุบเป็นบรรทัดสรุปต่อท้าย `<เวลา> [LEVEL] (collapsed) ซ้ำอีก N ครั้ง: <บรรทัดแรก>`
  - เวลา = ของรายการสุดท้ายที่ถูกยุบ (ต้นฉบับไม่มีเวลา ISO -> เวลาตอนดึง) · `[LEVEL]` หลังเวลา = รูปแบบที่ log_levels ตัดสินแม่นที่สุด ระดับจึงตรงกับต้นฉบับ · stack trace ไม่ถูกตัดครึ่ง
- เปิดที่ `puller.yaml`: `storage.collapse_repeats: {enabled: false, keep: 20}` (ไม่ใส่ = ปิด) ใช้กับทั้งไฟล์ log และ journal
- ยุบแล้วบันทึกใน System log (`events.py`, source puller): "ยุบ log ซ้ำ N รายการ (M ชนิด) ของ <source> ..." และใน docker logs `[COLLAPSE]`
- ข้อมูลที่อยู่ใน OpenSearch แล้วไม่ถูกแตะ

## Log ทุกแถวต้องมี Metrics at time
- `logs_service._with_metrics` ใช้กับเครื่อง Windows (agent) ด้วย (ถามว่ามีข้อมูลจาก windows_exporter ณ นาทีนั้นไหม) — เดิมเครื่อง Windows แสดงทุก log แม้ไม่มี metrics
- Prometheus ถามไม่ได้ (และไม่มีผลเก่าใน cache) -> ไม่แสดง (เดิมแสดงทั้งหมด)
- กันอีกชั้นตอนส่งให้หน้าเว็บ: แถวที่ค่าจริง CPU / Memory / Disk ว่างทั้งหมดถูกตัด (`_has_metrics`) ทั้งตารางปกติ, แบบรวมกลุ่ม (ตัวอย่างของกลุ่มเลือกบรรทัดล่าสุดที่มี metrics), ผลค้นหา (แนบ metrics ให้ทุกแถวแล้ว) และ INFO
- ข้อควรรู้: ตัวเลขบนปุ่ม/ตัวนับคิดจาก "นาทีที่มีข้อมูล" ถ้าบางแถวถูกตัดตอนกันชั้นที่สอง ตัวเลขอาจมากกว่าแถวที่เห็นเล็กน้อย

## หน้าต่างรายละเอียด log: ข้อมูลประกอบเพิ่ม
- **ปัญหานี้ (fingerprint)**: เกิดทั้งหมดกี่ครั้ง · ครั้งแรก · ล่าสุด · กี่เครื่อง (นับจาก log ที่ระบบเห็นอยู่ รวมที่ ack แล้ว)
- **Sensor ที่มีปัญหาตอนนั้น**: incident ของเครื่องนี้ที่ครอบเวลาของ log (เริ่ม → จบ, ค่าสูงสุด)
- **Log อื่นของเครื่องนี้ช่วงเดียวกัน (±10 นาที)**: เรียงตามความใกล้ ป้าย "ปัญหาเดียวกัน" · คลิกเพื่อเปิด log นั้น
- **เครื่อง Windows (agent)**: ไม่มี snapshot จาก SSH -> ถาม Prometheus (windows_exporter) ณ เวลาของ log แทน: Service แบบ Automatic ที่ไม่ได้ทำงาน + process ที่ใช้ CPU สูงสุด (CPU / Memory %) — เดิมมีแค่ CPU / Memory / Disk / I/O
- `logs_service.get_log_detail` เพิ่ม `related` {repeat, sensors, nearby} · `services_note` · ฟังก์ชัน `_related`, `_win_services_at`, `_win_processes_at`

## ปิด sensor (เช่น ping ไปไม่ถึงเครื่อง)
- หน้า sensor: ปุ่ม **⏸ ปิด sensor / ▶ เปิด sensor** บนแถบหัว (operator ขึ้นไป) มีผลรอบตรวจถัดไป (~30 วินาที)
- Settings > เครื่อง > แท็บ การแจ้งเตือน > **ปิด sensor**: ติ๊ก Ping (Ping latency + Packet loss + Bottleneck: Network) · Disk latency · Bottleneck ทั้งหมด · Log pull · ช่อง "อื่น ๆ" ใส่ชื่อ sensor เอง (เช่น `disk:/boot`, `service:Spooler`)
- sensor ที่ปิด = สถานะ **Paused** ข้อความ "ปิด sensor นี้ไว้ ..." ไม่แจ้งเตือน (Teams / ป๊อปอัป) ไม่นับเป็นปัญหาของเครื่อง
- puller.yaml: `targets[].alert.disabled_sensors: [net_latency, packet_loss]` · ชื่อ = ส่วนหลัง `<เครื่อง>:` ของ sensor id, ชนิด (kind) หรือขึ้นต้นแล้วตามด้วย `*` (เช่น `bottleneck:*`)
- API `POST /api/targets/<เครื่อง>/sensor-toggle` {sensor, disabled} (operator ขึ้นไป, Activity log `target.sensor_toggle`) · puller `sensor_monitor.sensor_disabled` / `_apply_disabled`

## Windows โหมด pull (central ดึงจาก Windows)
- สำหรับเครื่อง Windows ที่ต่อไปหา central ไม่ได้ (firewall ระหว่างวง) แต่ central ต่อเข้าเครื่องได้ — ได้ metrics / service / process / software แต่**ไม่มี log**
- puller.yaml: `targets[].agent: {mode: pull, exporter: "192.168.181.101:9182"}` (ไม่ใส่ exporter = host:9182) · ปริยาย `mode: push` (แบบเดิมทุกอย่าง)
- Prometheus ดึงผ่าน `web-nodes.json` job `cm-agent-pull` พร้อม label `cm_web=<เครื่อง>` -> query ของ Windows เดิมใช้ได้ทันที (puller `prometheus_targets.py` + backend `targets_service.pull_target`)
- สถานะ "ยังทำงาน" ของ agent ดูได้ทั้ง `fluentbit_uptime` (push) หรือ `up == 1` (pull): sensor Availability, Last seen, ตาราง WEB status, กราฟ Availability
- `install-fluentbit.ps1 -Pull -Central <IP central> -Name <ชื่อ>`: ไม่ต้องใช้ token / ไม่ติดตั้ง Fluent Bit · windows_exporter ฟัง 0.0.0.0:9182 + กฎ Windows Firewall เปิดรับเฉพาะ IP ของ central · หยุด cm-agent (push) ถ้ามี · วิธีติดตั้งแบบเดิม (ไม่ใส่ -Pull) ไม่เปลี่ยน แค่เพิ่มข้อความแนะนำ -Pull เมื่อขั้น 1/7 ต่อ central ไม่ได้
- `uninstall-fluentbit.ps1` ลบกฎ firewall ของโหมด pull
- Settings > เครื่อง (Windows) > แท็บ การเชื่อมต่อ: "วิธีรับข้อมูล" push / pull + ช่อง windows_exporter · Make command สร้างคำสั่งตามโหมด · แท็บ Log บอกว่าโหมด pull ไม่มี log
- แก้ `targets_service.regenerate_prometheus_targets` ให้ใส่ `__scheme__` / `__metrics_path__` เหมือน puller (เดิมเขียน web-nodes.json จากหน้า Settings แล้วค่า https หายจนกว่า puller จะสร้างใหม่)
- เพิ่ม `agent/windows/install-pull.bat` / `uninstall-pull.bat` สำหรับเครื่องที่เปิด PowerShell ไม่ได้: ติดตั้งโหมด pull ด้วย cmd ล้วน (sc / netsh / copy) ถาม IP ของ central ตอนรัน · อยู่ใน zip ชุดติดตั้งอัตโนมัติ · ไม่มีแท็บ Software
- `install-pull.bat` เรียก `install-fluentbit.ps1 -Pull` ให้เองถ้าเครื่องรัน PowerShell ได้ (ได้แท็บ Software + ทดสอบ exporter จริง) · PowerShell ถูกบล็อก / ไม่มี -> ติดตั้งด้วย cmd ล้วนเหมือนเดิม · อาร์กิวเมนต์ `install-pull.bat <IP central> [ชื่อ] [nops]`
- `install-fluentbit.ps1`: ไม่ใส่ -Name แล้วชื่อ Windows มีอักษรที่ใช้ไม่ได้ (เช่น `ALBEN_2016`) -> แปลงเป็น `alben-2016` ให้เอง (เดิมหยุดด้วย error)
- `install-pull.bat`: ถาม "IP of central" (ไม่มีตัวอย่าง IP ในคำถามแล้ว) แล้วถามชื่อเครื่องแยก · พิมพ์ "IP ชื่อ" ในบรรทัดเดียวก็แยกให้ · ตรวจว่าเป็น IP จริงก่อนติดตั้ง
