# เพิ่มเครื่อง Windows (agent / Fluent Bit)

เครื่อง Windows ส่งข้อมูลเข้ามาเอง ไม่ต้องเปิด SSH

```
เครื่อง Windows                                   Central
Fluent Bit (service: cm-agent)  ──HTTPS :4318──▶  central-agent-gw ──▶ OpenSearch ──▶ หน้า Logs
  Event Log (Critical/Error/Warning)  + token                      └──▶ Prometheus ──▶ สถานะ / Last seen
  ไฟล์ log (เฉพาะ ERROR/WARN)
  สัญญาณ "ยังทำงาน" ทุก 30 วินาที
```

ขั้นนี้ (ขั้นที่ 1) ได้: เครื่องขึ้นใน WEB status (OK / Down + Last seen) และ log ขึ้นในตาราง Logs
ยังไม่มี: CPU / Memory / Disk / Service / Process ของ Windows (ขั้นที่ 2)

---

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
   - เครื่องออกอินเทอร์เน็ตไม่ได้: ดาวน์โหลด `fluent-bit-5.1.5-win64.zip` จาก
     https://packages.fluentbit.io/windows/ มาวางในโฟลเดอร์เดียวกัน
2. ใส่ IP/ชื่อของ central ที่เครื่อง Windows ใช้เรียก แล้วกด **Make command** -> **Copy command**
3. บนเครื่อง Windows: เปิด PowerShell แบบ **Run as Administrator** ในโฟลเดอร์ที่แตก แล้ววางคำสั่ง
   ```
   powershell -ExecutionPolicy Bypass -File .\install-fluentbit.ps1 -Central 10.0.0.5 -Token <token> -Name win-app-01 -Channels "System,Application"
   ```
   สคริปต์จะ: ทดสอบพอร์ต -> ติดตั้ง Fluent Bit -> เขียน config -> ตรวจ config -> สร้าง service `cm-agent` -> เริ่มทำงาน

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

ถอนการติดตั้ง: `powershell -ExecutionPolicy Bypass -File .\uninstall-fluentbit.ps1 [-Purge]`

## ไฟล์ที่เกี่ยวข้อง

- `agent/windows/install-fluentbit.ps1`  สคริปต์ติดตั้ง (config + Lua ที่ตัด INFO / จัดรูปแบบ)
- `start_push_log/otel/agent-gateway.yaml`  ตัวรับฝั่ง central (token, TLS, แปลงให้เหมือน log ของ puller)
- `web_dashboard/backend/agent_service.py`  สถานะ / Last seen / คำสั่งติดตั้ง / zip
- puller.yaml: `type: agent`, `os: windows`, `agent: {logs: [...], channels: [...]}` (puller ข้ามเครื่องนี้)
