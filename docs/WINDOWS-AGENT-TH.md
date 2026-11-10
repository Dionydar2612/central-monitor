# เพิ่มเครื่อง Windows (agent / Fluent Bit)

เครื่อง Windows ส่งข้อมูลเข้ามาเอง ไม่ต้องเปิด SSH

```
เครื่อง Windows                                   Central
Fluent Bit (service: cm-agent)  ──HTTPS :4318──▶  central-agent-gw ──▶ OpenSearch ──▶ หน้า Logs
  Event Log (Critical/Error/Warning)  + token                      └──▶ Prometheus ──▶ สถานะ / Last seen
  ไฟล์ log (เฉพาะ ERROR/WARN)
  สัญญาณ "ยังทำงาน" ทุก 30 วินาที
  metrics จาก windows_exporter (ฟังเฉพาะ 127.0.0.1:9182) ทุก 15 วินาที
```

ได้อะไร
- สถานะ OK / Warning / Down + Last seen ในหน้า WEB status (ป้าย Win)
- log ERROR/WARN ในตาราง Logs
- CPU / Memory / Disk (แยกไดรฟ์ C: D:) / Disk I/O / Network / กราฟ System Load
- Service (running / stopped) และ Process ที่ใช้ CPU / RAM สูงสุด
- sensor + แจ้งสถานะตามเกณฑ์ + Incident history เหมือนเครื่อง Linux
- Performance / Hardware / System / Bottleneck / Service สำคัญ (รายละเอียด metrics / เกณฑ์ / ข้อจำกัด: `docs/MONITORING-TH.md`)

ต่างจาก Linux: ไม่มี Load average (แท็บ Load = processor queue length) · CPU breakdown "iowait" = interrupt + DPC
· Load INFO log ใช้ไม่ได้ (ไม่มี SSH) · นาฬิกาเครื่อง Windows ต้องตรง (เวลาของ log มาจากเครื่อง)

---

## วิธีง่าย: คำสั่งบรรทัดเดียว (แนะนำ)

```
หน้า Settings ─ สร้างคำสั่ง (enroll code 24 ชม.) ─▶ คัดลอกไปวางบนเครื่อง Windows (เครื่องไหนก็ได้ในวง network)
เครื่อง Windows ──HTTPS :4319──▶ central-agent-enroll   ลงทะเบียน -> token เฉพาะเครื่อง + ไฟล์ติดตั้ง + ค่าตั้ง
                ──HTTPS :4318──▶ central-agent-gw       ส่งข้อมูล (token เฉพาะเครื่อง)
scheduled task ทุก 5 นาที ─▶ :4319/agent/config         ค่าตั้งเปลี่ยน -> เขียน config ใหม่เอง
```

1. Settings > **+ Add target** > ชนิด **Windows (agent)** > ใส่ชื่อ / กลุ่ม / Event channel / ไฟล์ log > **Save**
2. ใส่ IP หรือชื่อของ central ที่เครื่อง Windows ใช้เรียก > **สร้างคำสั่งติดตั้ง** > **Copy command**
3. บนเครื่อง Windows เปิด **Windows PowerShell** (ไม่ใช่ PowerShell 7) แบบ **Run as Administrator** > วางคำสั่ง > Enter
4. ~1 นาที เครื่องขึ้น OK ในหน้า Dashboard

- **ไม่ต้องโหลด zip / ไม่ต้องย้ายไฟล์**: เครื่อง Windows โหลดสคริปต์ + Fluent Bit + windows_exporter จาก central
  (central โหลดจากอินเทอร์เน็ตครั้งแรกแล้วเก็บไว้ที่ `data/agent-files/` · central ออกเน็ตไม่ได้ วางไฟล์
  `fluent-bit-5.1.5-win64.zip` และ `windows_exporter-0.32.1-amd64.exe` ไว้ในโฟลเดอร์นั้นเอง)
- **ปลอดภัย**: คำสั่งตรวจใบรับรองของ central ด้วย thumbprint (กันเครื่องปลอม) · code ใช้ได้ 24 ชม. กับ 1 เครื่อง
  (รันซ้ำบนเครื่องเดิมได้) · ไม่มี token กลางในคำสั่ง
- **token เฉพาะเครื่อง**: ยกเลิกได้ทีละเครื่อง (ฟอร์มเครื่อง > Revoke token) · ลบเครื่อง = ยกเลิก token อัตโนมัติ
  · ติดตั้งใหม่ (สร้างคำสั่งใหม่) = token เดิมใช้ไม่ได้
- **ค่าตั้งอัปเดตเอง**: แก้ไฟล์ log / Event channel ในหน้า Settings แล้วบันทึก เครื่องเปลี่ยนตามภายใน ~5 นาที
  (scheduled task `CentralMonitorAgentSync` · log: `C:\ProgramData\central-monitor-agent\sync.log`)
- **เครื่องที่ติดตั้งแบบเดิม (zip + token กลาง)** ยังใช้ได้ตามเดิม ย้ายมาแบบใหม่ = รันคำสั่งบรรทัดเดียวทับได้เลย
  เมื่อย้ายครบทุกเครื่องแล้ว ปิด "ยอมรับ token กลาง" ในส่วน "วิธีเดิม" ของฟอร์มเครื่อง
- **ต้องเปิดพอร์ต 4319** เพิ่มจาก 4318 (firewall / port forward แบบเดียวกัน) เช่น
  `sudo ufw allow from 10.0.0.0/24 to any port 4319 proto tcp` · central บน WSL:
  `netsh interface portproxy add v4tov4 listenport=4319 connectport=4319 connectaddress=<IP ของ WSL>`
  + เปิด Windows Firewall ขาเข้า TCP 4319 บนเครื่อง host
- ปิดช่องทางนี้: `docker/.env` -> `CENTRAL_ENROLL_BIND=127.0.0.1`

ปัญหาที่พบบ่อย
| อาการ | แก้ |
|---|---|
| `Use Windows PowerShell 5.1` | เปิด "Windows PowerShell" (powershell.exe) แทน PowerShell 7 |
| `ไม่สามารถเชื่อมต่อ` / timeout | เครื่อง Windows เข้า central:4319 ไม่ได้ (firewall / portproxy) ทดสอบ `Test-NetConnection <central> -Port 4319` |
| `Could not establish trust relationship` | ใบรับรองของ central เปลี่ยนหลังสร้างคำสั่ง -> สร้างคำสั่งใหม่ |
| `code หมดอายุ` / `ถูกใช้แล้วโดยเครื่อง ...` | สร้างคำสั่งใหม่ (1 คำสั่ง = 1 เครื่อง) |
| ติดตั้งผ่านแต่เครื่องไม่ขึ้น | `docker logs central-agent-gw` ถ้ามี 401 = gateway ยังไม่เห็น token ใหม่ -> `docker restart central-agent-gw` |

---

## วิธีเดิม (zip + token กลาง)

## ขั้นที่ 0: เตรียม central (ครั้งเดียว)

1. อัปเดตโค้ด: `cm-update`
   - สร้าง `CENTRAL_AGENT_TOKEN` ใน `docker/.env` และใบรับรอง `keys/agent-tls/` ให้อัตโนมัติ
   - เริ่ม container `central-agent-gw` (พอร์ต 4318)
2. ตรวจ: `docker ps | grep agent-gw` ต้องเป็น Up
3. เปิด firewall ของ central ให้เครื่อง Windows เข้าพอร์ต 4318 ได้ (เฉพาะวง network ภายใน) เช่น
   `sudo ufw allow from 10.0.0.0/24 to any port 4318 proto tcp`
4. ใบรับรองต้องมี IP/ชื่อที่เครื่อง Windows ใช้เรียก central
   ปริยายใส่ชื่อเครื่อง + IP ของ central ให้แล้ว ถ้าเรียกด้วยชื่ออื่น (เช่น DNS / IP ของ NAT) สร้างใหม่:
   ```
   bash scripts/make-cert.sh agent 10.0.0.5 central.thaiquest.local
   cd docker && docker compose up -d --force-recreate buffer-init central-agent-gw
   ```
   (แล้วดาวน์โหลดชุดติดตั้งใหม่ เพราะมี central-ca.pem ตัวใหม่)

**central บน WSL**: เครื่องอื่นเข้า WSL ตรง ๆ ไม่ได้ ต้องทำ port forward 4318 ที่ Windows host
(`netsh interface portproxy add v4tov4 listenport=4318 connectport=4318 connectaddress=<IP ของ WSL>`)
และใส่ IP ของ Windows host ในใบรับรอง

**Prometheus ของเครื่อง host (crawlercondutionpp)**: ต้องเพิ่ม flag `--web.enable-remote-write-receiver`
ไม่งั้นสถานะจะดูจาก log ล่าสุดแทน (เครื่องที่ไม่มี error นาน ๆ จะขึ้น Down)

## ขั้นที่ 1: เพิ่มเครื่องในหน้า Settings

Settings > **+ Add target**
- ชนิดเครื่อง: **Windows (agent / Fluent Bit)**
- ชื่อเครื่อง: เช่น `win-app-01` (a-z 0-9 -)
- กลุ่ม, Host (ไม่บังคับ)
- Event Log channels: ปริยาย `System, Application`
- ไฟล์ log: บรรทัดละ 1 path เช่น `C:\inetpub\logs\LogFiles\*\*.log`
- **Save** แล้วส่วน "ติดตั้ง agent บนเครื่อง Windows" จะขึ้น

## ขั้นที่ 2: ติดตั้งบนเครื่อง Windows

1. กด **Download installer (zip)** แล้วคัดลอกไปที่เครื่อง Windows แตกไฟล์
   - เครื่องออกอินเทอร์เน็ตไม่ได้: วาง `windows_exporter-0.32.1-amd64.exe`
     (https://github.com/prometheus-community/windows_exporter/releases/tag/v0.32.1) และ `fluent-bit-5.1.5-win64.zip` จาก
     https://packages.fluentbit.io/windows/ มาวางในโฟลเดอร์เดียวกัน
2. ใส่ IP/ชื่อของ central ที่เครื่อง Windows ใช้เรียก แล้วกด **Make command** -> **Copy command**
3. บนเครื่อง Windows: เปิด PowerShell แบบ **Run as Administrator** ในโฟลเดอร์ที่แตก แล้ววางคำสั่ง
   ```
   powershell -ExecutionPolicy Bypass -File .\install-fluentbit.ps1 -Central 10.0.0.5 -Token <token> -Name win-app-01 -Channels "System,Application"
   ```
   สคริปต์จะ: ทดสอบพอร์ต -> ติดตั้ง Fluent Bit -> ติดตั้ง windows_exporter (127.0.0.1 เท่านั้น) -> เขียน config
   -> ตรวจ config -> สร้าง service `cm-agent` -> เริ่มทำงาน
   ติดตั้ง windows_exporter ไม่ได้ -> ใช้ตัวเก็บในตัว Fluent Bit แทน (ข้อมูลน้อยกว่า บางช่องขึ้น Unavailable)
   ใส่ Host (IP) ของเครื่องในหน้า Settings ด้วย ถ้าต้องการ ping / packet loss (และเปิด ICMP Echo ใน Windows Firewall)

## ขั้นที่ 3: ตรวจผล

- หน้า Dashboard: เครื่องขึ้น **OK** พร้อมป้าย **Win** ภายใน ~1 นาที
- ทดสอบ log: บนเครื่อง Windows
  `eventcreate /T ERROR /ID 999 /L APPLICATION /SO CentralMonitorTest /D "test error"`
  -> ตาราง Logs (Machine = win-app-01) ขึ้น `[ERROR] [Application] CentralMonitorTest (Event 999): test error`
- หน้า Settings > Install command: บอกว่าได้ข้อมูลล่าสุดเมื่อไหร่

## เครื่องเยอะ

วางโฟลเดอร์ที่แตกแล้วบน share (เช่น `\\fileserver\cm-agent\`) แล้วสั่งรันด้วย GPO / Intune / SCCM
- ไม่ใส่ `-Name` = ใช้ชื่อเครื่อง Windows (ตัวเล็ก) -> ต้องเพิ่มชื่อเดียวกันในหน้า Settings ก่อน
- ตัวอย่าง GPO Computer Startup Script (PowerShell):
  `\\fileserver\cm-agent\install-fluentbit.ps1 -Central 10.0.0.5 -Token <token> -ZipPath \\fileserver\cm-agent\fluent-bit-5.1.5-win64.zip`

## แก้ปัญหา

| อาการ | ตรวจ / แก้ |
|---|---|
| สคริปต์บอกต่อพอร์ต 4318 ไม่ได้ | firewall ของ central / network / `docker ps` ว่ามี central-agent-gw |
| `certificate verify failed` ใน `C:\ProgramData\central-monitor-agent\fluent-bit.log` | IP/ชื่อใน `-Central` ไม่อยู่ในใบรับรอง -> ขั้นที่ 0 ข้อ 4 (ชั่วคราว: `-SkipCertCheck`) |
| `401` | token ไม่ตรง `CENTRAL_AGENT_TOKEN` ใน docker/.env -> คัดลอกคำสั่งใหม่จากหน้า Settings |
| เครื่องขึ้น Down แต่ log มา | Prometheus ไม่รับ remote write (ดู "Prometheus ของเครื่อง host" ด้านบน) |
| central ไม่รับ log | `docker logs central-agent-gw --tail 50` |
| สถานะ agent บน Windows | `Get-Service cm-agent` · `Restart-Service cm-agent` |
| metrics ไม่ครบ / Unavailable | `Get-Service cm-exporter` · log `C:\ProgramData\central-monitor-agent\windows_exporter.log` · เปิด `http://127.0.0.1:9182/metrics` บนเครื่องนั้น · รันสคริปต์ติดตั้งซ้ำ |
| Packet loss 100% | Windows Firewall บล็อก ping: เปิดกฎ "File and Printer Sharing (Echo Request - ICMPv4-In)" |

ถอนการติดตั้ง: `powershell -ExecutionPolicy Bypass -File .\uninstall-fluentbit.ps1 [-Purge]`

## ไฟล์ที่เกี่ยวข้อง

- `agent/windows/install-fluentbit.ps1`  สคริปต์ติดตั้ง (config + Lua ที่ตัด INFO / จัดรูปแบบ)
- `start_push_log/otel/agent-gateway.yaml`  ตัวรับฝั่ง central (token, TLS, แปลงให้เหมือน log ของ puller)
- `web_dashboard/backend/agent_service.py`  สถานะ / Last seen / คำสั่งติดตั้ง / zip
- puller.yaml: `type: agent`, `os: windows`, `agent: {logs: [...], channels: [...]}` (puller ข้ามเครื่องนี้)
