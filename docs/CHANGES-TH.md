# เพิ่มเติม (26 พ.ย. 2026): Windows WinRM แบบไม่สร้าง user - ใช้บัญชีที่มีอยู่แล้ว
- Settings > การเชื่อมต่อ: "บัญชีที่ central ใช้อ่านข้อมูล" = สร้าง cm-monitor (แนะนำ) / ใช้บัญชีที่มีอยู่แล้ว (local หรือ DOMAIN\name + รหัสผ่าน)
- setup: `Account=` ใน cm-monitor-config.txt -> ไม่สร้าง user, ไม่เปลี่ยนรหัส, ไม่ปิด logon · ให้สิทธิ์อ่านเท่าที่จำเป็น + JEA endpoint เดิม
- remove: คงบัญชีไว้ เอาออกเฉพาะสิทธิ์ที่ setup เพิ่ม · check-cm-monitor รู้จักบัญชีนี้ · รายละเอียด docs/WINDOWS-TH.md

---

# แก้ไข (26 พ.ย. 2026): cm-agent โหมด pull CPU ค้าง 100% (เช่น abc86 ที่ตั้ง metrics_interval 600)
- สาเหตุ: central อ่าน windows_exporter ทุก 600 วิ แต่ตัวแปลงสะสมค่าเฉพาะช่วงห่าง ≤ 300 วิ -> ค่า CPU (และ disk / network) ไม่เพิ่มเลย
  -> 100 - idle 0 = 100% · และ sensor ใช้ rate(...[5m]) ถ้าอ่านห่างกว่า 5 นาทีจะมีช่วงที่ไม่มีค่าใหม่เสมอ
- แก้: โหมด pull อ่านหน้า /metrics จริงไม่เกินทุก 60 วิ (อ่านหน้าเดียว เบามาก) · CPU ใช้ windows_cpu_time_total สะสมตรง ๆ
  ทุก core (ไม่ประมาณจาก %) · รุ่น windows_exporter ที่ไม่มี mode interrupt / dpc ก็นับ core ได้ครบ · ช่วงสะสมปรับตามรอบที่ตั้ง
- โหมด push ที่ตั้ง metrics_interval เกิน ~120 วิ ยังมีปัญหา rate[5m] แบบเดียวกัน แนะนำ ≤ 60 วิ

---

# แก้ไข (26 พ.ย. 2026): Windows (WinRM) CPU ขึ้น 100% ตลอด ทั้งที่เครื่องไม่ได้ทำงานหนัก (module 1.1.5)
- สาเหตุ: CPU ของเครื่อง WinRM คำนวณจาก performance counter "% Processor Time" ที่อ่านผ่านสิทธิ์ cm-monitor ได้ค่าผิด
  (central เห็นแค่ core เดียว และค่า idle ไม่เพิ่มเลย -> 100 - 0 = 100%) · ผลพ่วง: central คิดว่าเครื่อง busy จึงข้ามการเก็บ event / log
- แก้: module อ่าน CPU time จริงต่อ logical processor จาก Windows (NtQuerySystemInformation ผ่านตัวช่วยที่ setup คอมไพล์)
  แล้วส่งเป็นค่าสะสม -> node_cpu_seconds_total ตรง ๆ ทุก core (เหมือน node_exporter / Task Manager) · % ต่อ core / รวม คำนวณจากค่าเดียวกัน
- ต้องกด **อัปเดตบนเครื่อง** (Settings > Edit เครื่อง) หลัง `cm-update --rebuild` · เครื่องที่ยังไม่อัปเดตทำงานแบบเดิม

---

# เพิ่มเติม (26 พ.ย. 2026): Windows แบบ cm-agent กลับมา (คู่กับแบบ WinRM) · ติดตั้งจากหน้าเว็บ · ตั้ง interval ก่อนติดตั้ง
- ชนิดเครื่องใหม่ "Windows (cm-agent)" (`type: agent`): Fluent Bit + windows_exporter เป็น service LocalSystem ไม่สร้าง user ใหม่
- ตั้งก่อนติดตั้ง: เปิด / ปิด Metrics / Logs / Software / Process + interval (metrics, heartbeat, Event Log, ไฟล์ log, ส่งข้อมูล, inventory) ค่าปริยายเบากว่ารุ่นเก่า (metrics 60 วิ, process ปิด, ไม่มี Netstat / probe / netsec)
- ติดตั้ง / อัปเดต / ถอนจากหน้าเว็บผ่าน WinRM (admin ใช้ครั้งเดียว) หรือ zip ดับเบิลคลิก · ไฟล์ Fluent Bit / windows_exporter วางที่ data/agent-bin
- central-agent-gw (:4318) กลับมา: log -> OpenSearch · metrics -> puller agent_bridge.py แปลงเป็นชื่อแบบ node_exporter (/win/<เครื่อง>) หน้าเว็บ / sensor / กฎ ใช้ร่วมกับเครื่องอื่น
- แท็บ Software (โปรแกรมที่ติดตั้ง + พอร์ต) สำหรับเครื่อง cm-agent · คู่มือ docs/WINDOWS-AGENT-TH.md
- โหมด pull กลับมา: central ดึง windows_exporter ของเครื่องเอง (0.0.0.0:9182 + firewall เฉพาะ IP central) ไม่ใช้ token / Fluent Bit · log ผ่าน cm-logs.ps1 (textfile) อ่านพร้อม metrics (agent_logs.py)

---

# เพิ่มเติม (26 พ.ย. 2026): NTP Monitor (หน้า /ntp)
- ตรวจ NTP server จาก central ทุก 30 วินาที (SNTP UDP 123): สถานะ / offset / RTT / stratum / ref + กราฟย้อนหลัง 1h – 30d + uptime
- แจ้งเตือน Teams: ไม่ตอบ / server ไม่ sync เอง / offset ≥ 500 / 2000 ms / RTT ≥ 200 ms (ยืนยันตาม sensors.confirm_checks)
- กราฟเวลาคลาดของทุกเครื่อง Linux เทียบกันในหน้าเดียว + แท็บ Time / NTP ในกราฟ System Load ของเครื่อง
- ไฟล์ใหม่: start_push_log/puller/ntp_monitor.py, web_dashboard/backend/ntp_service.py, frontend ntp.html / ntp.js

---

# เพิ่มเติม (26 พ.ย. 2026): Windows - Services / Task Scheduler / Programs (exe) + อัปเดตบนเครื่องจากหน้าเว็บ (module 1.1.4)
- รายละเอียดเครื่อง Windows มีแท็บเพิ่ม: **Services** (exe + arguments / บัญชีที่รัน / PID / คำอธิบาย), **Task Scheduler**
  (คำสั่งที่รัน / ผลรอบล่าสุด / รอบถัดไป / trigger) และ **Programs** (exe ที่รันอยู่ทั้งหมด + path / ผู้ผลิต / service ในนั้น)
  ดึงสดเมื่อเปิดแท็บ ไม่เก็บลงดิสก์ · Task Scheduler ดูได้เฉพาะ operator ขึ้นไป
- Settings > Edit เครื่อง Windows > **อัปเดตบนเครื่อง**: ส่ง module รุ่นใหม่ไปติดตั้งทับ (admin ใช้ครั้งเดียว ไม่บันทึก) รหัส cm-monitor / ใบรับรองคงเดิม
  รายการเครื่องแสดงรุ่น module ของแต่ละเครื่อง + "มีรุ่นใหม่"
- module 1.1.4: Get-CMTasks (COM Schedule.Service), Get-CMServices -Detail, Get-CMProcesses -All · setup คอมไพล์ตัวช่วย
  CentralMonitor.Native.<รุ่น>.dll บนเครื่อง (NtQuerySystemInformation / EnumServicesStatusEx อ่านอย่างเดียว) -> CPU ต่อ process แม่นขึ้นด้วย
- เครื่องเดิมต้องกด **อัปเดตบนเครื่อง** 1 ครั้ง (หลัง `cm-update --rebuild`) แท็บใหม่จึงมีข้อมูล · รายละเอียด docs/WINDOWS-TH.md

---

# เพิ่มเติม (25 พ.ย. 2026): รายละเอียดเครื่องแบบแท็บ กลับมาใช้กับทั้ง Linux และ Windows
- แท็บ Overview / Performance / Bottleneck / Processes / Hardware / System / Alerts & Events ใช้กับทุกเครื่อง (เดิมเหลือหน้าเดียวแบบ Linux)
- Windows (WinRM): Hardware / System เติมผู้ผลิต / รุ่น / CPU / RAM / การ์ดเครือข่าย / OS / build / วันติดตั้ง / domain / IP / Windows updates จาก inventory
- แท็บ Software เดิม (โปรแกรมที่ติดตั้ง / พอร์ต) ยังไม่กลับมา เพราะตัวเก็บแบบ WinRM ยังไม่อ่านรายการโปรแกรม

---

# เพิ่มเติม (25 พ.ย. 2026): ถอนการติดตั้ง Windows จากหน้าเว็บ + ตัวถอนลบครบทุกอย่าง
- Settings > Edit เครื่อง Windows > การเชื่อมต่อ > **ถอนการติดตั้งออกจากเครื่อง** (admin ใช้ครั้งเดียว) + เลือกลบเครื่องออกจาก central
- remove-cm-monitor.ps1 เขียนใหม่: ลบ profile ของ cm-monitor ด้วย, หาของเราจากชื่อเมื่อไม่มี manifest, คืนค่า WinRM ถ้า setup เปิดเอง,
  log ใน logs\remove-*.log, ตรวจซ้ำตอนจบว่าไม่มีอะไรค้าง, remove-result.json ให้หน้าเว็บ
- ปุ่ม Delete ของเครื่อง Windows เตือนว่าไม่ได้ถอนออกจากเครื่อง

---

# เพิ่มเติม (25 พ.ย. 2026): sensor Time sync (NTP) ของเครื่อง Linux
- ไม่ sync NTP = Error · นาฬิกาคลาดจาก central เกิน 500 / 2000 ms = Warning / Error (ปรับได้ที่ Settings > Alerts และฟอร์มเครื่อง)
- ใช้ค่าจาก node_exporter (timex) ที่มีอยู่แล้ว ไม่ต้องติดตั้งหรือเปิดพอร์ตเพิ่ม · แจ้งเตือนผ่าน Alert / การ์ด Teams เดิม

---

# เพิ่มเติม (25 พ.ย. 2026): ติดตั้งเครื่อง Windows ง่ายขึ้น (ไม่ต้องคัดลอกรหัส / Thumbprint)
- Settings > การเชื่อมต่อ (Windows) มี 2 วิธี: **ติดตั้งจากหน้าเว็บ** (กรอก admin ของเครื่องครั้งเดียว ไม่บันทึก central ติดตั้งให้ผ่าน WinRM)
  หรือ **Download zip แล้วดับเบิลคลิก** (รหัสของเครื่องอยู่ใน zip, central จำใบรับรองตอนล็อกอินสำเร็จครั้งแรก)
- รหัสผ่าน / Thumbprint ในฟอร์มไม่บังคับแล้ว (ย้ายไป "ตั้งค่าเอง (ขั้นสูง)") · ติดตั้ง / ดาวน์โหลด = บันทึกเครื่องให้เอง
- setup: รับ `Password=` จาก config แล้วลบออกจากไฟล์หลังใช้ · เขียน `setup-result.json` (ไม่มีรหัส) ให้ตัวติดตั้งจากหน้าเว็บ
- ต้อง `cm-update --rebuild` · รายละเอียด docs/WINDOWS-TH.md ข้อ 1

---

# เพิ่มเติม (25 พ.ย. 2026): เครื่อง Windows แบบไม่ลง agent (WinRM · อ่านอย่างเดียว) ตั้งค่าจากหน้าเว็บทั้งหมด
- Settings > + Add target > **ชนิดเครื่อง = Windows**: กรอก Host / Password / Thumbprint, **Scan certificate**, **ทดสอบการเชื่อมต่อ**,
  **Download ชุดติดตั้ง (.zip)** ที่กรอก CentralIP + LogPaths ให้ (บนเครื่อง Windows: ดับเบิลคลิก `setup-cm-monitor.bat` ครั้งเดียว)
- บน Windows ไม่มี service / task / agent: JEA endpoint `CentralMonitor` + user `cm-monitor` (ไม่ใช่ admin) + WinRM HTTPS 5986 จาก IP central เท่านั้น
- puller `windows_pull.py`: metric ชื่อแบบ node_exporter (หน้าแรก / sensor / bottleneck / กฎ / Teams / Daily Summary ใช้ได้ทันที),
  Event log + ไฟล์ log (Warning ขึ้นไป) -> OpenSearch, service / process / event -> หน้ารายละเอียดเครื่อง, Load INFO log / Process สด / ตรวจ log
- รหัสผ่านเก็บแยกแบบเข้ารหัส `data/state/secrets/winrm/` (ไม่อยู่ใน puller.yaml) · กฎแจ้งเตือนเลือก OS windows ได้ · หน้าแรกแสดง OS Windows
- **ต้อง `cm-update --rebuild`** (puller ใช้ pypsrp จาก docker/wheels ไม่ต้องออกเน็ตตอน build, central-api มีชุดติดตั้ง) · รายละเอียด: docs/WINDOWS-TH.md

---

# เพิ่มเติม (24 พ.ย. 2026): ไม่เปิดพอร์ต 9090 · ช่วงเวลาด่วนเพิ่ม 12 ชม. / 1 วัน / 3 วัน
- Prometheus ไม่เปิดพอร์ต 9090 ออก host แล้ว (ระบบใช้ทาง network ภายใน) ต้องการ UI -> `docker/docker-compose.prometheus-ui.yml`
- หน้าแรก: ช่วงเวลาด่วน 15 นาที, 1 ชม., 6 ชม., 12 ชม., 1 วัน, 3 วัน, 7 วัน, 30 วัน และจำช่วงที่เลือกไว้ใน browser

---

# เพิ่มเติม (24 พ.ย. 2026): เอาส่วนของเครื่อง Windows ออกทั้งหมด (รองรับเฉพาะ Linux)
- ลบ: `agent/windows/` (ตัวติดตั้ง / สคริปต์ .ps1 / .bat ทั้งหมด), Netstat Monitor, central-agent-gw (:4318),
  Windows โหมด pull (`puller/agent_logs.py`), ตรวจโปรแกรมติดตั้ง (`software_watch.py`), แท็บรายละเอียดเครื่องแบบ Windows,
  รูปแบบการใช้งาน Windows / Linux / ทั้งคู่ (install.sh + Settings), เกณฑ์ `thresholds_windows`, `sensors.critical_services`,
  กฎ / Apps & Web ชนิด service / โปรแกรม / พอร์ตบนเครื่อง, การ์ด Teams หัวข้อ Netstat / Software
- เครื่อง `type: agent` ที่ยังค้างใน puller.yaml: ไม่ตรวจแล้ว · หน้า Settings แสดง "Windows (เลิกรองรับ)" ให้กด Delete
- ข้อมูลเดิมใน OpenSearch / Prometheus ไม่ถูกแก้ / ลบ
- หลัง cm-update: install.sh ลบ container `central-agent-gw` ที่ค้างให้เอง

---

# เพิ่มเติม (19 พ.ย. 2026): รูปแบบการใช้งาน Windows / Linux / ทั้งคู่ · Application Queue จาก log
- `install.sh` ถามครั้งแรกว่าจะใช้ Windows อย่างเดียว / Linux อย่างเดียว / ทั้งคู่ (`PLATFORMS=` ข้ามการถาม) เก็บที่ puller.yaml `ui.platforms`
  เปลี่ยนได้ที่ Settings (admin) · Windows อย่างเดียว: ซ่อนชนิดเครื่อง Linux, ตัวกรอง OS, Load INFO log · Linux อย่างเดียว: ซ่อน Windows และ Netstat Monitor
- Netstat Monitor: **Application Queue** อ่านคิวของโปรแกรมจากไฟล์ log ที่ตั้งไว้ (เช่น THMux Output[n]: queue exceed / queue decrease)
  แสดงสถานะต่อ Output + log WARN / ERROR / กลับปกติ (ไม่แสดง INFO) · แจ้งเมื่อเกินนาน / size เกินที่ตั้ง · ดูย้อนหลังใน History
- ต้องติดตั้ง agent Windows ซ้ำ 1 ครั้ง (cm-netstat.ps1 ใหม่)

---

# เพิ่มเติม (19 พ.ย. 2026): Netstat Monitor (Windows Server)
- Apps & Web มีเมนูย่อย **Applications Monitor / Web Monitor / Netstat Monitor** · หน้าใหม่ `/netstat` (Dashboard real-time, Hosts & Ports, Alerts, History)
- agent: `cm-netstat.ps1` process เดียวรันค้าง เก็บทุก 1 / 2 / 5 วิ (`-NetstatInterval`) · scheduled task `CentralMonitorNetstat` · โหมด push ส่ง cm_netstat_* ทุก 5 วิ
- puller: `netstat.py` อ่านสด + ตัวแจ้งเตือน (กันซ้ำ, Recovery) · backend: `netstat_service.py` + `/api/netstat/*` + SSE · central-api threads 4 -> 8
- Recv-Q / Send-Q บน Windows = Unsupported · Listening Queue อ่านได้เฉพาะพอร์ตของ IIS / HTTP.sys · รายละเอียด `docs/NETSTAT-TH.md`

---

# เพิ่มเติม (19 พ.ย. 2026): Acknowledge = รับทราบ ไม่ซ่อน · เลือกหลายรายการ
- เอาเมนู "Ack · ซ่อน…" (ซ่อนตามเวลา) ออก · ตาราง Logs ทั้งแบบรวมกลุ่มและไม่รวม มีช่องติ๊กเลือกหลายแถว -> **Acknowledge selected** / **Unacknowledge**
- log ที่ ack แล้วยังแสดงในตาราง (ป้าย ACK สีจาง) ไม่ต้องติ๊ก Show acknowledged อีก (เอาช่องนี้ออก) · ไม่นับเป็นปัญหาใหม่ (New Log Entries / Problem logs / จุดบนกราฟ)
- แบบรวมกลุ่ม: ack = ทุกบรรทัดที่มีอยู่ตอนนี้ของปัญหานั้น (`POST /api/logs/ack {"fps": [...], "scope": เครื่อง}`) · บรรทัดใหม่ที่เข้ามาหลัง ack แสดง "ACK 2/3" = ยังมีบรรทัดใหม่ที่ยังไม่รับทราบ
- ack แบบเดิมตามเวลา (ack-fp) ที่บันทึกไว้แล้วยังมีผลจนหมดเวลา · Unacknowledge ของกลุ่มล้างให้ด้วย

---

# เพิ่มเติม (19 พ.ย. 2026): Logs แสดง / กรองตาม "ชื่อ log"
- ตาราง Logs คอลัมน์ **ชื่อ log** = ชื่อเรียกที่ตั้งใน Settings > Log (เช่น ImportArkheion, auth) แทนชนิด parser (application / loginuser / other) · ชี้ดูชนิด parser ได้
- ตัวกรอง **ชื่อ log** แสดงชื่อทุกชื่อที่ตั้งไว้ของเครื่อง (log ที่เพิ่งเพิ่ม = ขึ้นทันทีเป็น (0)) + ชื่อที่พบใน log · เปลี่ยนเครื่องแล้วรายการเปลี่ยนตาม
- puller ต่อท้ายชื่อไฟล์ buffer ด้วย `__=<ชื่อเรียก>` เมื่อชื่อเรียกต่างจาก source (ไม่กระทบ receiver ของ otel) · log เก่าใน OpenSearch ใช้ชื่อจาก puller.yaml ตอนแสดงผล (ไม่แก้ข้อมูลเดิม) · ถ้าเครื่องเดียวกันตั้งชื่อเรียกต่างกันใน source เดียวกัน log เก่าจะแสดงชื่อ source
- Settings > Log: ช่องชื่อเรียกมีรายการชื่อที่เคยตั้งให้เลือก (พิมพ์ชื่อใหม่ได้) · ชนิด parser เพิ่ม system / kernel ในรายการ

---

# เพิ่มเติม (19 พ.ย. 2026): Ack log ชัดเจน · กล่องสถานะกดได้ · ตั้งค่า sensor เต็ม · ล้างปุ่มใน Settings
- Logs (แบบกลุ่ม): คอลัมน์ **Acknowledge** = เมนูเดียว "Ack · ซ่อน…" เลือก ซ่อน 1 ชม. / 8 ชม. / 1 วัน / 7 วัน (รับทราบแล้วซ่อนปัญหานี้ รวมบรรทัดใหม่ หมดเวลาแล้วถ้ายังเกิดจะกลับมา) · ack แล้วแสดง "ซ่อนถึง …" + ปุ่มยกเลิก (เดิมเป็นกล่อง "24 ชม." ที่ไม่บอกว่าคืออะไร)
- WEB status: กล่อง Up / Down / Warning / Critical / Paused กดแล้วแสดงเฉพาะเครื่องสถานะนั้น (กดซ้ำ / Total = ทั้งหมด) · ตัวกรองสถานะมี "Up"
- กด Status ของเครื่อง (ตาราง / การ์ด) -> หน้าต่างบอก sensor ที่มีปัญหา (กดไปหน้า sensor) + ปุ่ม รายละเอียดเครื่อง / Logs ของเครื่องนี้ / Network
- หน้า sensor > ตั้งค่า sensor นี้: Priority, เกณฑ์ Warning / Error ของ sensor นี้, แจ้งเมื่อเป็นต่อเนื่องกี่นาที, วิธีแจ้ง (Warning ขึ้นไป / เฉพาะ Error-Down / ไม่แจ้ง บันทึก log อย่างเดียว) เก็บใน `targets[].alert.sensors`
- Priority มีผลกับการแสดงผล: ★5 ป๊อปอัปค้างจนกดปิด · ★4 60 วิ · ★3 15-30 วิ · ★2 8 วิ · ★1 ไม่ขึ้นป๊อปอัป (ยังส่ง Teams / บันทึก log) · Priority สูงแจ้งก่อนและอยู่บนสุดเสมอ
- Settings > Targets: เอาปุ่ม Install command / Deploy key ออกจากรายการ (ยังทำได้ในฟอร์ม Edit แท็บ การเชื่อมต่อ)

---

# เพิ่มเติม (18 พ.ย. 2026): เหตุผลที่ Pause · Pause ไม่นับ downtime · ตรวจ log ใน Settings
- Pause เครื่องต้องใส่เหตุผล (ปุ่ม Pause หน้าแรก / Settings / ช่อง "เหตุผลที่ Pause" ในฟอร์ม) เก็บใน puller.yaml `targets[].pause: {reason, at, by}` · แสดงในตาราง / การ์ด / สไลด์ / Settings · ลง Activity log
- Availability: ช่วงที่ Pause ไม่นับเป็นทั้ง uptime และ downtime (ตัดออกจากตัวหาร) · puller บันทึกช่วง Pause ตอน Resume ลง `data/state/pauses.json` · หน้า sensor Availability แสดงเวลา Pause + ตารางช่วง Pause พร้อมเหตุผล
  (เดิม: ช่วง Pause ถูกนับเป็น uptime)
- Settings > Log: หัวคอลัมน์ "ชนิด log (parser)" / path / "ชื่อเรียก" · ใต้แต่ละแถวบอกดึงล่าสุดเมื่อไหร่ / กี่ไฟล์ · ปุ่ม **ตรวจ log** ให้ puller SSH ไปดูว่ามีโฟลเดอร์ / มีไฟล์ตรง path / อ่านได้ไหม (`GET /api/targets/<เครื่อง>/log-status?check=1` -> puller `/log-check` ตรวจเฉพาะ path ที่บันทึกใน puller.yaml)

---

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

## Network & Security: คลิกดูว่ามีอะไรอยู่ข้างใน
- คลิกกล่อง Internet / LAN อื่น = ตาราง IP ทั้งหมด (ต้นทาง / ปลายทาง), เครื่องของเรา, พอร์ต (พร้อมชื่อบริการ เช่น 443 HTTPS, 3389 Remote Desktop, 5432 PostgreSQL), โปรแกรมบนเครื่องเรา, จำนวน + สรุปพอร์ตที่ใช้
- คลิกเส้น = รายละเอียดการเชื่อมต่อของเส้นนั้น (ต้นทาง / ปลายทาง / พอร์ต / โปรแกรม / จำนวน) · คลิกชื่อเครื่องในตาราง = เปิดรายละเอียดเครื่อง

## Network & Security: แผนผังการเชื่อมต่อแบบซ้าย -> ขวา (เอา gateway / switch ออก)
- เหลือแค่ "เครื่องไหนต่อหาเครื่องไหน": ซ้าย = ผู้เริ่มเชื่อมต่อ, ขวา = ปลายทาง · จัดชั้นอัตโนมัติ + เรียงในชั้นให้เส้นตัดกันน้อย · จุดต่อเส้นกระจายตามขอบกล่อง (ไม่ทับกัน)
- กล่องเครื่อง = ชื่อ, OS, IP, แถบสีตามผลตรวจ (FW ปิด / ไม่มีข้อมูล = ตัวแดง) · เส้นหนาตามจำนวนการเชื่อมต่อ · ป้ายพอร์ตที่ปลายลูกศร
- Internet / LAN อื่น แยก "ต่อเข้ามา" (ซ้ายสุด) กับ "ต่อออกไป" (ขวาสุด) · ปิดได้ด้วย "แสดง Internet / LAN อื่น"
- เครื่องที่ไม่มีการเชื่อมต่อ = แถวล่าง · คลิกเครื่อง = ไฮไลต์เส้นของเครื่องนั้น (ที่เหลือจาง) + รายละเอียด
- เอาแผนผัง topology (gateway / switch / subnet) และมุมมองรายการแบบต้นไม้ออก

## Network & Security: แผนผังจัดใหม่ให้เป็นระเบียบ + มุมมองรายการ
- แผนผัง: 1 คอลัมน์ต่อ Gateway (เรียงตาม IP) · สายหลักซ้ายคอลัมน์แตกเข้า switch ของแต่ละวง (ไม่มีเส้นอ้อม / ทับกรอบ) · Internet ต่อทุก gateway ผ่านสาย WAN ขอบขวา · กว้างเกินหน้า = ขึ้นแถวใหม่ · วงเรียงตาม network · เครื่องในวงเรียงตาม IP · วงที่ไม่รู้ gateway = คอลัมน์ "ไม่ทราบ Gateway"
- มุมมอง "รายการ": ต้นไม้ Internet -> Gateway -> วง -> เครื่อง (IP, OS, ผลตรวจ, Firewall, ขาเข้า / ขาออก) + IP ที่ไม่ได้ monitor ของแต่ละวง · คลิกเครื่อง = รายละเอียด

## Network & Security: แผนผังแบบ topology
- Internet (เมฆ) ── Gateway (router ของแต่ละ default gateway) ── switch ของแต่ละวง ── เครื่อง (ไอคอน server = Linux, PC = Windows, สีตามผลตรวจ) พร้อมชื่อ + IP
- จัดกรอบตาม Subnet (ปริยาย) หรือ ตามกลุ่ม · IP ภายในที่คุยกับเครื่องเราแต่ไม่ได้ monitor แสดงเป็นไอคอนสีเทาในวงเดียวกัน (ไม่อยู่ในวงไหน = กรอบ "LAN อื่น")
- คลิกเครื่อง = เส้นการเชื่อมต่อของเครื่องนั้น (เครื่องอื่น / IP ที่ไม่ได้ monitor / Internet) + รายละเอียด · ติ๊ก "แสดงเส้นการเชื่อมต่อระหว่างเครื่องทั้งหมด" ได้
- backend: `/api/netsec` machines[] เพิ่ม ip / subnet / gateway / iface (เลือกการ์ดที่อยู่วงเดียวกับ default gateway ไม่นับ docker / bridge / loopback · ไม่มีข้อมูล = Host + /24)

## เครื่องที่ไม่มีกลุ่ม กับกลุ่ม สลับลำดับกันได้
- ทุกชั้น (รวมชั้นบนสุด) เครื่องกับกลุ่มเรียงปนกันได้อิสระ เช่น กลุ่ม Intellex อยู่บนสุด แล้วค่อยเครื่องที่ไม่มีกลุ่ม หรือเครื่องคั่นระหว่างกลุ่ม (เครื่องที่ติดกันรวมเป็นตาราง / ไทล์ / การ์ดเดียว)
- ลากกลุ่มไปวางบนแถวเครื่องได้ (ก่อน / หลังเครื่องนั้น อยู่ชั้นเดียวกับเครื่อง) · ลากเครื่องไปขอบบนของหัวกลุ่ม = ไปอยู่ก่อนกลุ่มนั้น
- ลำดับรวมเก็บใน `ui_order.json` -> `items` ("m:<เครื่อง>" / "g:<กลุ่ม>") · สไลด์ทีละกลุ่มใช้ลำดับเดียวกัน · กดหัวคอลัมน์เรียงชั่วคราว = เครื่องก่อนแล้วกลุ่ม

## ลากย้ายได้อิสระ (โหมดจัดลำดับ)
- เครื่อง -> เครื่องในกลุ่มอื่น = ย้ายเข้ากลุ่มนั้น + วางก่อน / หลัง · เครื่อง -> หัวกลุ่ม = ย้ายเข้ากลุ่ม (ต่อท้าย) · เครื่อง -> แถบ "ระดับบนสุด / ไม่มีกลุ่ม" = เอาออกจากกลุ่ม
- กลุ่ม -> หัวกลุ่มอื่น: ขอบบน = ไปอยู่ก่อน, ขอบล่าง = ไปอยู่หลัง (ย้ายข้ามชั้นได้), กลางหัว = เข้าไปเป็นกลุ่มย่อย · กลุ่ม -> "ระดับบนสุด" = ขึ้นชั้นบนสุด
- ย้ายกลุ่ม = เปลี่ยน path ของเครื่องทั้งหมดในกลุ่ม + กลุ่มย่อย + `alert_rules[].groups` ที่อ้างถึง (กฎแจ้งเตือนตามไปด้วย) · กันย้ายเข้าไปในตัวเอง / เกิน 10 ชั้น
- ย้ายข้ามกลุ่ม (แก้ puller.yaml) = admin · operator จัดลำดับในกลุ่มเดียวกันได้
- API: `POST /api/targets/<เครื่อง>/group` {group} · `POST /api/groups/move` {from, parent} (Activity log `target.group`, `group.move`)

## จัดลำดับเครื่อง / กลุ่มเอง + สไลด์ภาพรวมกลุ่ม
- WEB status (ตาราง / ไทล์ / การ์ด): ปุ่ม "↕ จัดลำดับ" (operator ขึ้นไป) -> ลากเครื่องวางบนเครื่อง / ลากหัวกลุ่มวางบนกลุ่มชั้นเดียวกัน (ครึ่งบน = ก่อน, ครึ่งล่าง = หลัง) · "รีเซ็ตลำดับ" = กลับเป็นปกติ
  · ลำดับใช้ร่วมกันทุกคน เก็บที่ data/state/ui_order.json (`GET / PUT /api/ui-order`, Activity log `ui.order`) ไม่แตะ puller.yaml
  · กดหัวคอลัมน์เพื่อเรียงชั่วคราวยังใช้ได้ (กดซ้ำจนไม่เรียง = กลับเป็นลำดับที่จัดไว้) · ไทล์ / การ์ด: มีลำดับที่จัดไว้ = ใช้ลำดับนั้น ไม่งั้นเรียงตามความรุนแรงเหมือนเดิม
- สไลด์: เลือก "ทีละกลุ่ม" (ปริยาย) / "ทีละเครื่อง"
  · ทีละกลุ่ม: 1 สไลด์ต่อกลุ่ม (ตามลำดับกลุ่ม) = จำนวนเครื่องแยกสถานะ, CPU / Memory / Disk สูงสุดพร้อมชื่อเครื่อง, Availability ต่ำสุด 24h (ไม่ใช้ค่าเฉลี่ย), การ์ดย่อยทุกเครื่อง, log 5 รายการล่าสุดของทั้งกลุ่ม (ERROR ก่อนเสมอ พร้อมชื่อเครื่อง)

## agent (push) ส่งเข้า central ผ่านพอร์ตที่ 2 ได้ (เช่น 9182)
- docker/.env: `CENTRAL_AGENT_BIND2=0.0.0.0` + `CENTRAL_AGENT_PORT2=9182` -> central-agent-gw รับทั้ง 4318 และ 9182 (ปริยาย 127.0.0.1:4319 = ไม่เปิดออกนอก)
- Settings > เครื่อง (Windows, push) > การเชื่อมต่อ: "พอร์ตที่ส่งไป central" (ว่าง = 4318) -> puller.yaml `agent.port` · Make command / precheck ใส่ `-Port 9182` ให้เอง · เครื่องอื่นยังใช้ 4318 ได้เหมือนเดิม

## Apps & Web กลับมาที่หน้าหลัก · Network & Security แสดงเฉพาะที่ดึงได้
- หน้าหลักแสดงแถบ / ไทล์ Apps & Web เหมือนเดิม (ยังเปิดจาก Settings ได้ด้วย) · Network & Security เปิดจาก Settings
- Network & Security: เอากล่อง "วิธีแก้" / คำสั่ง และข้อความสาเหตุยาว ๆ ออก · ส่วนที่ดึงไม่ได้แสดงสั้น ๆ ว่า "ดึงข้อมูลไม่ได้" / "ไม่สามารถดึงข้อมูล ... ได้" / "ไม่มีข้อมูล" · การ์ดผลตรวจแสดงเฉพาะข้อที่รู้ผล

## ตรวจเครื่อง Windows ก่อนติดตั้ง agent (precheck)
- `agent/windows/precheck.ps1` / `precheck.bat` (อยู่ใน zip ชุดติดตั้ง) อ่านอย่างเดียว: ไม่ติดตั้ง ไม่หยุด ไม่แก้ service / ไฟล์ / firewall
- ตรวจ: Admin, พอร์ต 9182 ว่างไหม (ใครใช้อยู่), windows_exporter ตัวเดิม (MSI / service ที่ตัวติดตั้งจะถอน / ปิด), cm-agent / cm-exporter / task เดิม, Fluent Bit ตัวอื่น, disk / RAM / CPU / จำนวน process, Windows Update ปิดไหม, Windows Firewall (pull), WMI, ต่อ central:4318 ได้ไหม (push), ดาวน์โหลด github / packages.fluentbit.io ได้ไหม (หรือมีไฟล์ข้างสคริปต์), Event Log channel, ไฟล์ log (Fluent Bit เปิดค้าง -> โปรแกรมที่หมุนไฟล์ด้วย rename อาจ error), Security log ใหญ่, Antivirus
- ผล [ OK ] / [WARN] / [FAIL] + สรุป · exit code 0 / 1 / 2
- Settings > เครื่อง (Windows) > การเชื่อมต่อ: ช่อง "ตรวจก่อนติดตั้ง" + Copy precheck (คำสั่งเดียวกับ Make command แต่ไม่มี token) · `setup_info.precheck`

## Apps & Web / Network & Security ย้ายไปเปิดจาก Settings + รายละเอียด Apps เพิ่ม + วิธีแก้ขั้นตอนเดียว
- หน้าหลักไม่แสดงแถบ Apps & Web / ไทล์ Apps & Web / ปุ่ม Network & Security แล้ว · เปิดจากการ์ดบนสุดของหน้า Settings
- หน้าต่างรายละเอียดใน /apps: ตรวจกี่ครั้ง / ไม่ผ่านกี่ครั้งในช่วงที่เลือก, ตรวจล่าสุด, วิธีตรวจ, ที่มา, Priority
  · Docker: Image, State, Status, Container ID, สร้างเมื่อ, Ports, Networks, Mounts, Command + CPU / RAM / Network I/O / Disk I/O / จำนวน process (Linux: docker stats ผ่าน SSH)
  · โปรแกรม (Windows): CPU 5 นาที, RAM, ตาราง process (PID / RAM) · Service: ชื่อที่แสดง, รันในนาม, Path, Start mode (Windows) / ข้อมูลจาก systemctl (Linux)
  · พอร์ต / เว็บ: โปรแกรมที่เปิดรับพอร์ตนั้น + ใครต่อเข้าพอร์ตนั้นอยู่ (จาก Network & Security) · เว็บ: เร็วสุดในช่วง, SSL เหลือกี่วัน
  · เหตุการณ์ล่าสุดของรายการนี้จาก System log
- Network & Security: กล่อง "วิธีแก้ (ขั้นตอนเดียว)" เมื่ออ่านข้อมูลไม่ได้
  · Linux: คำสั่งเดียว (ปุ่มคัดลอก) สร้าง /etc/sudoers.d/central-monitor ให้ user ของ puller รันได้เฉพาะ iptables -S / ufw status / firewall-cmd --state / --list-all (หา path จริงเอง, ตรวจด้วย visudo ผิด = ลบทิ้ง) + เพิ่มเข้ากลุ่ม adm / systemd-journal
  · Windows ไม่มีข้อมูล: รันคำสั่งติดตั้งของเครื่องซ้ำ 1 ครั้ง (ติดตั้ง cm-netsec.ps1 + task ให้เอง)

## หน้า Network & Security (/network)
- ปุ่ม "Network & Security" ที่หัวหน้า Dashboard / Status & Logs
- ไทล์: จำนวนเครื่อง, Firewall ปิด, Login ไม่สำเร็จ 24h, การเชื่อมต่อขาเข้า / ขาออก, IP สาธารณะที่คุยกับเครื่องเรา
- แผนผังการเชื่อมต่อ (ใครต่อหาใคร): วงกลม = เครื่องที่ monitor (สีตามผลตรวจ) · ลูกศร A → B = A ต่อเข้าพอร์ตของ B (ป้าย = พอร์ต) · Internet (IP สาธารณะ) / LAN อื่น (IP ภายในที่ไม่ได้ monitor) · คลิกเครื่อง = ไฮไลต์เส้นของเครื่องนั้น + รายละเอียด
- ตารางทุกเครื่อง: ผลตรวจ, Firewall, ขาเข้าปริยาย, พอร์ตเปิดรับ, ขาเข้า / ขาออก, Login ไม่สำเร็จ, Traffic, Ping / loss, Availability · ตารางปลายทางภายนอก
- เครื่องเดียว: ผลตรวจ (firewall เปิดไหม, ขาเข้าปริยาย deny ไหม, login ไม่สำเร็จ ≥ 20 / ≥ 100 ใน 24 ชม., พอร์ตเสี่ยงที่เปิดรับจากเครือข่าย, IP สาธารณะต่อเข้ามา) · การเชื่อมต่อขาเข้า / ขาออก (IP, พอร์ต, โปรแกรม, ชื่อเครื่องถ้าเป็นเครื่องที่ monitor) · Firewall (สถานะ / ค่าปริยาย / กฎ) · Route · พอร์ตที่เปิดรับ · Login (ไม่สำเร็จแยกต้นทาง / เข้าสำเร็จล่าสุด)
- Linux: `puller/netsec.py` ผ่าน SSH ทุกรอบสถานะ (ip addr / ip route / ss / ufw / firewalld / iptables / journalctl sshd หรือ auth.log) · ปุ่ม "ดึงใหม่" (operator) · `ufw status` ต้อง root: ถ้าไม่ได้ตั้ง sudo -n จะได้แค่ เปิด/ปิด + ค่าปริยายจาก /etc/ufw/ufw.conf, /etc/default/ufw · ให้เห็นกฎ: sudoers `<user> ALL=(root) NOPASSWD: /usr/sbin/ufw status *` · log ของ sshd: user อยู่กลุ่ม adm / systemd-journal
- Windows: `agent/windows/cm-netsec.ps1` (scheduled task CentralMonitorNetSec ทุก 5 นาที ทั้ง push / pull) -> textfile: cm_net_conn, cm_net_addr, cm_net_route, cm_fw_service, cm_fw_profile, cm_fw_rule, cm_login_failed_24h, cm_login_failed_by, cm_login_ok · ต้องติดตั้ง agent ใหม่จาก zip ชุดล่าสุด
- ข้อจำกัด: เป็นภาพ ณ เวลาที่เก็บจากตัวเครื่อง (การเชื่อมต่อที่ค้างอยู่) ไม่ใช่ flow ทุก packet จาก switch / router · อ่านอย่างเดียว ไม่แก้ firewall / route
- API: `GET /api/netsec`, `GET /api/netsec/<เครื่อง>`, `POST /api/netsec/<เครื่อง>/refresh` (operator)

## Priority / เวลาแจ้งเฉพาะ sensor
- ตั้งได้ 2 ที่: หน้า sensor (กล่อง "ตั้งค่า sensor นี้", operator ขึ้นไป) และ Settings > เครื่อง > การแจ้งเตือน > "Priority / เวลาแจ้งเฉพาะ sensor"
- puller.yaml `targets[].alert.sensors: {"<sensor>": {priority: 1-5, minutes: N}}` ชื่อ sensor แบบเดียวกับ disabled_sensors (`cpu`, `disk:C:`, `service:Spooler`, ชนิด หรือ `xxx*`) · ไม่ตั้ง priority = Priority ของเครื่อง · ไม่ตั้ง minutes = ค่าของเครื่อง / ทั้งระบบ
- ยังไม่ครบเวลาที่ตั้ง: บันทึกลง System log ("ยังไม่แจ้ง: รอให้เป็นต่อเนื่อง ...", `notify: false`, `pending: true`) แต่ไม่ส่ง Teams / ป๊อปอัป · กลับปกติก่อนครบเวลา = บันทึก "กลับเป็น OK ก่อนครบเวลา (ไม่ได้แจ้ง)" · บันทึกครั้งเดียวต่อช่วง ไม่ซ้ำทุก 30 วินาที
- เกิดพร้อมกันหลายเรื่อง: เรียง Priority สูงก่อน (แล้วค่อยความรุนแรง) ทั้งลำดับส่ง Teams, System log และป๊อปอัป (Priority สูงอยู่บนสุดและไม่ถูกตัดเมื่อเกินจำนวนที่แสดง) · รายการ sensor ในหน้า Status เรียงสถานะ -> Priority
- API `POST /api/targets/<เครื่อง>/sensor-settings` {sensor, priority, minutes} (ว่างทั้งคู่ = ลบ, Activity log `target.sensor_settings`)

## มุมมองสไลด์ (ทีละเครื่อง) + Priority
- WEB status > ▶ สไลด์: 1 เครื่องต่อหน้า เลื่อนเองทุก 10 / 15 / 30 / 60 วินาที (จำค่าไว้) · ◀ ▶ / ปุ่มลูกศรบนคีย์บอร์ด / จุดด้านล่าง (สีตามสถานะ) · ชี้เมาส์ค้างหรือเปิดหน้าต่าง log = หยุดชั่วคราว
- ในสไลด์: สถานะ, Priority, กลุ่ม / IP, เกจ CPU / Memory / Disk, Availability 24h (คลิก -> หน้า sensor), Up/Down มานานเท่าไหร่, Down 24h, Network, Ping, Last pull, sensor ที่มีปัญหา, log 5 รายการล่าสุด (ERROR ขึ้นก่อนเสมอ แล้วค่อย WARN · คลิก = หน้าต่างรายละเอียด log) · คลิกชื่อเครื่อง = เปิดรายละเอียดเครื่อง
- ลำดับสไลด์: Priority มาก -> น้อย, แล้วความรุนแรง, แล้วชื่อ · ใช้ตัวกรองชื่อ / OS / สถานะเดียวกับมุมมองอื่น
- Settings > เครื่อง > ทั่วไป: Priority ★1-5 (แบบ PRTG) ปริยาย 3 · puller.yaml `targets[].priority` (3 ไม่เขียน) · `/api/overview` servers[].priority

## คอลัมน์ Availability แทน Alerts (ตาราง WEB status)
- แสดง Uptime 24 ชม. (%) + Up / Down มานานเท่าไหร่ + จำนวนครั้งที่ down ใน 24 ชม. · สี: ≥ 99.9% เขียว, ≥ 99% เหลือง, ต่ำกว่า / down อยู่ = แดง · เรียงตามคอลัมน์ได้
- คลิก -> หน้า sensor Availability ของเครื่อง: การ์ด สถานะตอนนี้ (มาแล้วนานเท่าไหร่, down นานสุด 30 วัน) + Uptime 24 ชม. / 7 วัน / 30 วัน (downtime + จำนวนครั้ง) + ประวัติ down
- มุมมองการ์ดแสดง uptime แทนจำนวน alert · จำนวน sensor ที่มีปัญหายังดูได้จาก tooltip ของ Status
- backend: `sensors_service.availability_by_web()` / `availability_stats()` (downtime = ช่วงที่ sensor availability เป็น down / error จาก incidents.json + ที่ยังเป็นอยู่) · `/api/overview` เพิ่ม `servers[].avail` · `/api/sensors/detail` เพิ่ม `availability`

## log ของ Windows โหมด pull
- เดิมโหมด pull ไม่มี log เพราะเครื่อง Windows ส่งออกไปหา central ไม่ได้ -> ใช้ช่องทางเดียวกับ metrics (central ต่อเข้า :9182) แทน
- `agent/windows/cm-logs.ps1` (scheduled task `CentralMonitorLogs` ทุก 1 นาที, SYSTEM): อ่าน Event Log (Critical / Error / Warning ต่อจาก RecordId ครั้งก่อน) + ไฟล์ log (ส่วนที่เพิ่ม เฉพาะบรรทัด error / warn) -> textfile `cm_logs.prom` ของ windows_exporter: `cm_log_entry{seq,type,body}` เก็บย้อนหลัง ~3 ชม. / 1000 รายการ · ตั้งค่าใน `%ProgramData%\central-monitor-agent\cm-logs.json`
- `install-fluentbit.ps1 -Pull` คัดลอก cm-logs.ps1 + ตั้ง task (ใช้ -Channels / -Logs เหมือนโหมด push) · Make command โหมด pull ใส่ -Channels / -Logs ให้ · ถอน: uninstall-fluentbit.ps1 / uninstall-pull.bat ลบ task ด้วย · .bat แบบ cmd ล้วน (PowerShell ถูกบล็อก) ไม่มี log
- puller `agent_logs.py`: ทุกรอบ log (scheduler.log_interval) อ่าน `http://<exporter>/metrics` เอาเฉพาะ seq ใหม่ -> `data/buffer/<site>__<เครื่อง>__eventlog|application__...log` (checkpoint `<เครื่อง>::agent-pull::<exporter>`, seq บนเครื่องเริ่มใหม่ = เก็บใหม่ทั้งหมด) · ไม่เก็บ INFO ตาม store_info_logs · ยุบซ้ำตาม collapse_repeats
- otel: receiver ใหม่ `filelog/eventlog` (`*__eventlog__*.log` -> log.source = eventlog เหมือนโหมด push) · filelog/other ไม่อ่านไฟล์ eventlog ซ้ำ
- Prometheus job `web-nodes`: `metric_relabel_configs` ทิ้ง `cm_log_entry` (ไม่เก็บ log เป็น metric)
- เครื่องที่ติดตั้งโหมด pull ไปแล้ว: รัน `install-pull.bat` / `install-fluentbit.ps1 -Pull` ซ้ำ 1 ครั้ง (zip ชุดใหม่)

## Windows โหมด pull (central ดึงจาก Windows)
- สำหรับเครื่อง Windows ที่ต่อไปหา central ไม่ได้ (firewall ระหว่างวง) แต่ central ต่อเข้าเครื่องได้ — ได้ metrics / service / process / software + log (ดูหัวข้อ "log ของโหมด pull" ด้านล่าง)
- puller.yaml: `targets[].agent: {mode: pull, exporter: "192.168.181.101:9182"}` (ไม่ใส่ exporter = host:9182) · ปริยาย `mode: push` (แบบเดิมทุกอย่าง)
- Prometheus ดึงผ่าน `web-nodes.json` job `cm-agent-pull` พร้อม label `cm_web=<เครื่อง>` -> query ของ Windows เดิมใช้ได้ทันที (puller `prometheus_targets.py` + backend `targets_service.pull_target`)
- สถานะ "ยังทำงาน" ของ agent ดูได้ทั้ง `fluentbit_uptime` (push) หรือ `up == 1` (pull): sensor Availability, Last seen, ตาราง WEB status, กราฟ Availability
- `install-fluentbit.ps1 -Pull -Central <IP central> -Name <ชื่อ>`: ไม่ต้องใช้ token / ไม่ติดตั้ง Fluent Bit · windows_exporter ฟัง 0.0.0.0:9182 + กฎ Windows Firewall เปิดรับเฉพาะ IP ของ central · หยุด cm-agent (push) ถ้ามี · วิธีติดตั้งแบบเดิม (ไม่ใส่ -Pull) ไม่เปลี่ยน แค่เพิ่มข้อความแนะนำ -Pull เมื่อขั้น 1/7 ต่อ central ไม่ได้
- `uninstall-fluentbit.ps1` ลบกฎ firewall ของโหมด pull
- Settings > เครื่อง (Windows) > แท็บ การเชื่อมต่อ: "วิธีรับข้อมูล" push / pull + ช่อง windows_exporter · Make command สร้างคำสั่งตามโหมด · แท็บ Log ใช้ได้ทั้ง 2 โหมด
- แก้ `targets_service.regenerate_prometheus_targets` ให้ใส่ `__scheme__` / `__metrics_path__` เหมือน puller (เดิมเขียน web-nodes.json จากหน้า Settings แล้วค่า https หายจนกว่า puller จะสร้างใหม่)
- เพิ่ม `agent/windows/install-pull.bat` / `uninstall-pull.bat` สำหรับเครื่องที่เปิด PowerShell ไม่ได้: ติดตั้งโหมด pull ด้วย cmd ล้วน (sc / netsh / copy) ถาม IP ของ central ตอนรัน · อยู่ใน zip ชุดติดตั้งอัตโนมัติ · ไม่มีแท็บ Software
- `install-pull.bat` เรียก `install-fluentbit.ps1 -Pull` ให้เองถ้าเครื่องรัน PowerShell ได้ (ได้แท็บ Software + ทดสอบ exporter จริง) · PowerShell ถูกบล็อก / ไม่มี -> ติดตั้งด้วย cmd ล้วนเหมือนเดิม · อาร์กิวเมนต์ `install-pull.bat <IP central> [ชื่อ] [nops]`
- `install-fluentbit.ps1`: ไม่ใส่ -Name แล้วชื่อ Windows มีอักษรที่ใช้ไม่ได้ (เช่น `ALBEN_2016`) -> แปลงเป็น `alben-2016` ให้เอง (เดิมหยุดด้วย error)
- `install-pull.bat`: ถาม "IP of central" (ไม่มีตัวอย่าง IP ในคำถามแล้ว) แล้วถามชื่อเครื่องแยก · พิมพ์ "IP ชื่อ" ในบรรทัดเดียวก็แยกให้ · ตรวจว่าเป็น IP จริงก่อนติดตั้ง
- `install-fluentbit.ps1`: เครื่องที่ปิด Windows Update (service wuauserv = Disabled) ไม่ใช้ collector `update` (เดิม windows_exporter เริ่มไม่ได้ทั้งตัว "windows updates service is disabled") · ถ้า service cm-exporter เริ่มไม่ได้ ลองใหม่อัตโนมัติโดยตัด collector update / gpu ก่อนแจ้ง error (ใช้ทั้งโหมด push และ pull)
- `install-fluentbit.ps1`: **แก้ติดตั้งซ้ำแล้ว service ไม่เปลี่ยนคำสั่ง** — เดิมใช้ `sc.exe config binPath=` ผ่าน PowerShell 5.1 ซึ่งส่งเครื่องหมาย " เพี้ยน sc ไม่เปลี่ยนอะไรแต่ไม่ฟ้อง (cm-exporter / cm-agent ยังใช้ค่าจากการติดตั้งครั้งแรก) ตอนนี้เขียน ImagePath ใน registry ตรง ๆ · ตรวจ Windows Update ปิดจาก registry ด้วย
- `install-fluentbit.ps1 -Pull`: service Windows Firewall (MpsSvc) ปิดอยู่ -> สร้างกฎไม่ได้ (error 1753 "no more endpoints available from the endpoint mapper") ตอนนี้เตือนแล้วติดตั้งต่อ (เดิมหยุด) · `install-pull.bat` เตือนแล้วไปต่ออยู่แล้ว
