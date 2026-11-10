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

## Software บนเครื่อง (แท็บ Software ในหน้าเครื่อง)

| ส่วน | มาจาก | อัปเดต |
|---|---|---|
| Running programs (จำนวน process, CPU, RAM, พอร์ตที่เปิด) | windows_exporter collector `process` | ~15 วินาที |
| Listening TCP ports (พอร์ต / address / โปรแกรม) | `cm-inventory.ps1` (Get-NetTCPConnection) | 15 นาที |
| Installed software (ชื่อ / เวอร์ชัน / ผู้ผลิต / วันที่ติดตั้ง / x64-x86) | `cm-inventory.ps1` (registry Uninstall) | 15 นาที |
| Software changes (ติดตั้ง / ถอน / อัปเดต) | central เทียบรายการ (`software_watch.py`) | 5 นาที |

- `cm-inventory.ps1` อยู่ใน `C:\Program Files\central-monitor-agent\` รันด้วย scheduled task `CentralMonitorInventory` (SYSTEM)
  เขียน `C:\ProgramData\central-monitor-agent\textfile\cm_inventory.prom` ให้ windows_exporter (collector `textfile`) ส่งต่อ
- เครื่องที่ติดตั้ง agent ไว้ก่อนหน้า: **รันคำสั่งติดตั้งซ้ำ 1 ครั้ง** (Make command เดิม) แท็บ Software จึงจะมีรายการโปรแกรม / พอร์ต
- แจ้งเตือนโปรแกรมหยุด / CPU / RAM ของโปรแกรม / พอร์ต / เว็บ: Settings > Custom alert rules (ดู `docs/NOTIFY-TH.md`)

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
