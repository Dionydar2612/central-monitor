# เครื่อง Windows แบบไม่ลง agent (WinRM · อ่านอย่างเดียว)

central ดึงข้อมูลเองผ่าน **WinRM HTTPS (5986)** เข้า **JEA endpoint `CentralMonitor`** ด้วยบัญชี `cm-monitor` (ไม่ใช่ admin)
ซึ่งเรียกได้แค่ฟังก์ชันอ่าน `Get-CM*` 7 ตัว · บนเครื่อง Windows **ไม่มี service / scheduled task / agent / ไฟล์ข้อมูล** ของเรา
เครื่อง Windows ไม่ต้องต่อกลับไปหา central (เหมาะกับ network ที่ central → Windows ได้ทางเดียว)

ทุกอย่างทำจากหน้าเว็บ: **Settings > + Add target > ชนิดเครื่อง = Windows** (admin)

## 1. ขั้นตอน

Settings > **+ Add target** > ชนิดเครื่อง *Windows (WinRM · ไม่ลง agent)* > ใส่ชื่อ + Host (+ ไฟล์ log ในแท็บ **Log** ถ้ามี) > แท็บ **การเชื่อมต่อ** เลือกวิธีใดวิธีหนึ่ง
(ทั้งสองวิธีบันทึกเครื่องให้เอง · รหัสผ่านของ cm-monitor และใบรับรองระบบตั้งให้ **ไม่ต้องคัดลอกอะไร**)

**วิธีที่ 1: ติดตั้งจากหน้าเว็บ (ไม่ต้องไปที่เครื่อง)**
1. กรอก *Admin user / Admin password* ของเครื่อง Windows และ WinRM ที่เครื่องเปิดอยู่ (ปกติ 5985) > **ติดตั้งให้เครื่องนี้**
2. รอ ~1-3 นาที (หน้าเว็บแสดงขั้นตอน + log ของ setup) > เสร็จ · central เริ่มเก็บข้อมูลเอง
- รหัส admin ใช้ครั้งนั้นครั้งเดียว: ไม่บันทึกลงดิสก์ / log / puller.yaml
- ใช้บัญชี **Administrator** ของเครื่อง หรือ **domain admin** (local admin อื่นถูก UAC remote restriction บล็อกทาง network)
- IP ของ central เว้นว่างได้: ระบบดูจากการเชื่อมต่อจริงว่าเครื่อง Windows เห็น central เป็น IP อะไร
- ทำอย่างไร: puller เขียนชุดติดตั้งลง `C:\ProgramData\CentralMonitor-setup` (admin เท่านั้น) เริ่ม setup เป็น process แยก
  (setup restart WinRM) รอผล แล้วลบโฟลเดอร์ทิ้ง · thumbprint ได้จากผล setup ผ่านช่องทางที่ล็อกอินแล้ว

**วิธีที่ 2: ดาวน์โหลดแล้วดับเบิลคลิก (เมื่อ central ใช้บัญชี admin ของเครื่องไม่ได้)**
1. ใส่ *IP ของ central ที่เครื่อง Windows เห็น* > **Download ชุดติดตั้ง (.zip)**
2. คัดลอกไปเครื่อง Windows > แตก zip > ดับเบิลคลิก **`setup-cm-monitor.bat`** > จบ
- รหัสของ cm-monitor อยู่ใน `cm-monitor-config.txt` ของ zip (setup ลบออกจากไฟล์หลังใช้) · **ลบ zip ทิ้งหลังติดตั้ง**
- ใบรับรอง: central จำ thumbprint เองตอนล็อกอินสำเร็จครั้งแรก (trust on first use) แล้วตรวจทุกครั้งต่อไป
  (ดูค่าที่จำไว้ได้ใน *ตั้งค่าเอง (ขั้นสูง)* เทียบกับที่ setup แสดงได้)

ภายใน ~1 นาที CPU / RAM / Disk / Network ขึ้นในหน้าแรก · Service / Process / Event ขึ้นในรายละเอียดเครื่อง (ทุก 5 นาที)

เพิ่มโฟลเดอร์ log ทีหลัง: แก้แท็บ Log > ติดตั้งซ้ำ (วิธี 1 หรือ 2) หรือแก้ `LogPaths=` เองแล้วรัน `setup-cm-monitor.bat`
เปลี่ยนรหัส: ติดตั้งซ้ำด้วยวิธี 1 (ออกรหัสใหม่ให้) หรือ `reset-password-cm-monitor.bat` แล้วใส่รหัสใน *ตั้งค่าเอง (ขั้นสูง)*
ถอน: `remove-cm-monitor.bat` · ตรวจ: `check-cm-monitor.bat` (อ่านอย่างเดียว) · รัน setup เองแบบเดิม (ไม่ใช้ zip จากหน้าเว็บ): กรอกรหัส + Thumbprint ที่ setup แสดงใน *ตั้งค่าเอง (ขั้นสูง)*

## 2. ความปลอดภัย

| เรื่อง | ทำอย่างไร |
| --- | --- |
| บัญชี | local user `cm-monitor` ไม่ใช่ admin · อยู่แค่ Performance Monitor Users + Event Log Readers · ห้าม logon หน้าเครื่อง / RDP (WinRM ยังใช้ได้) |
| คำสั่งที่ทำได้ | JEA โหมด NoLanguage: เห็นแค่ Get-CMVersion / Metrics / Services / Events / LogTail / Inventory / Processes · คำสั่งอื่นถูกปฏิเสธ |
| ไฟล์ log | อ่านได้เฉพาะ path ที่ตรงกับ `C:\ProgramData\CentralMonitor\allowed-paths.txt` (แก้ได้เฉพาะ admin ของเครื่อง) · ไม่ผ่าน link / junction |
| การเชื่อมต่อ | HTTPS + NTLM · central ตรวจ thumbprint ก่อนส่งรหัสทุกครั้ง (ไม่ตรง = ไม่เชื่อมต่อ) · firewall รับ 5986 จาก IP central เท่านั้น · ไม่เปิด Basic / AllowUnencrypted / TrustedHosts |
| รหัสบน central | `data/state/secrets/winrm/<ชื่อ>.json` สิทธิ์ 0600 เข้ารหัสด้วย `CENTRAL_SECRET_KEY` · ไม่อยู่ใน puller.yaml / API / log · ลบเครื่อง = ลบรหัส · ติดตั้งจากหน้าเว็บ: รหัสใหม่ใช้จริงเมื่อ setup สำเร็จ |
| module | setup เซ็นไฟล์ด้วยใบรับรอง code signing ของเครื่องนั้นเอง (private key ส่งออกไม่ได้) ใช้ได้กับ RemoteSigned / AllSigned และแก้ไฟล์ = ลายเซ็นเสีย โหลดไม่ได้ |
| สิทธิ์ในหน้าเว็บ | เพิ่ม / แก้การเชื่อมต่อ / ดาวน์โหลดชุดติดตั้ง = admin · operator แก้ได้แค่แท็บการแจ้งเตือน + Log |

## 3. ไม่รบกวนโปรแกรมอื่น

- บนเครื่องมีแค่ process `wsmprovhost.exe` 1 ตัวของ session (priority BelowNormal) · จำกัด RAM 150 MB, 2 session, ปิดเองเมื่อว่าง 5 นาที · central เปิดใหม่ทุก 6 ชม.
- รอบถี่ (ทุก 30 วิ) ใช้ .NET PerformanceCounter ที่สร้างครั้งเดียว ไม่ผ่าน WMI · WMI ใช้แค่ inventory ชั่วโมงละครั้ง
- อ่านไฟล์ log แบบ FileShare ReadWrite + Delete (โปรแกรมเจ้าของหมุน / ลบไฟล์ได้ตลอด) ครั้งละไม่เกิน 1 MB แล้วปิดทันที
- CPU ของเครื่อง ≥ 90% → รอบนั้นเก็บแค่ metric (ข้าม event / log / process) · ติดต่อไม่ได้ → รอ 30 / 60 / 120 / 300 วิ ก่อนลองใหม่
- setup ไม่แตะ listener 5985 เดิม, SFTPServer, IIS, sshd, windows_exporter · ไม่ reboot · restart WinRM 1 ครั้งตอนติดตั้ง

## 4. ประหยัดดิสก์

- Windows: ไฟล์ตั้งค่า < 50 KB ไม่เขียนข้อมูลระหว่างทำงาน (ตำแหน่ง event / offset ไฟล์เก็บที่ central: `data/state/winrm-state.json`)
- Prometheus: ~40–80 series ต่อเครื่อง (ไม่มี label ที่เปลี่ยนบ่อยเช่น PID) · อายุตาม `PROMETHEUS_RETENTION`
- OpenSearch: เฉพาะ Warning / Error / Critical (ตาม `storage.store_info_logs`) · ข้อความ event ตัดที่ 2 KB · ใช้ `collapse_repeats` ได้เหมือน Linux

## 5. ทำงานอย่างไร (ฝั่ง central)

```
puller (windows_pull.py) ── WinRM HTTPS 5986 ──> JEA CentralMonitor (Windows)
   ├─ metric แบบ node_exporter ──> http://central-puller:9110/win/<ชื่อ> <── Prometheus (job web-nodes, label job/instance/web ของเครื่อง)
   ├─ Event log + ไฟล์ log ──> data/buffer ──> otel ──> OpenSearch   (source: system / application / ชื่อใน Log)
   └─ service / process / event 15 นาที ──> data/state/system/<ชื่อ>.json (หน้ารายละเอียดเครื่อง)
```

- metric ใช้ชื่อเดียวกับ node_exporter (`node_cpu_seconds_total`, `node_memory_MemAvailable_bytes`, `node_filesystem_*` ไดรฟ์ = `C:` ...)
  -> หน้าแรก, sensor, bottleneck, custom alert rules, การ์ด Teams, Daily Summary ใช้เกณฑ์ / query เดิมของ Linux ได้ทั้งหมด
- load1 ของ Windows = งานที่รอ CPU (Processor Queue Length) + core ที่กำลังทำงาน เฉลี่ยแบบ 1 / 5 / 15 นาที
- service ที่ตั้งเป็น Automatic แต่หยุดอยู่ = `failed` (แดง) · Manual ที่หยุด = `inactive`
- กฎแจ้งเตือน (Custom alert rules) เลือก OS ได้: any / linux / windows
- sensor *WinRM collection* แทน *Log pull (SSH)* · Availability ของ Windows = WinRM ตอบภายใน 120 วินาที

## 6. แก้ปัญหา

| อาการ | สาเหตุ / ทางแก้ |
| --- | --- |
| ทดสอบ: เชื่อมต่อ ...:5986 ไม่ได้ | ยังไม่ได้รัน setup / firewall ระหว่างวงบล็อก 5986 (ทดสอบ: `nc -vz <ip> 5986` บน central) |
| ใบรับรองไม่ตรง | ติดตั้งใหม่แล้วใบรับรองเปลี่ยน -> Scan certificate แล้วเทียบกับที่ setup แสดง |
| รหัสผ่าน / user ไม่ถูกต้อง | ใส่รหัสใหม่ หรือ `setup-cm-monitor.bat resetpw` |
| `... CentralMonitor.psm1 is not digitally signed` | เครื่องบังคับลายเซ็น / ไฟล์ติดป้ายจากอินเทอร์เน็ต -> รัน `setup-cm-monitor.bat` รุ่นที่เซ็น module เอง (ใบรับรอง CN=Central Monitor Module Signing เชื่อถือเฉพาะเครื่องนั้น ถอนด้วย remove-cm-monitor.bat) |
| ไม่พบ endpoint CentralMonitor | รัน setup-cm-monitor.bat (ต้องการ PowerShell 5.1 = Server 2016 ขึ้นไป) |
| log ไฟล์ไม่เข้า / ตรวจ log บอกไม่พบไฟล์ | โฟลเดอร์ไม่อยู่ใน LogPaths -> แก้ config แล้วรัน setup ซ้ำ |
| puller log: `pypsrp not installed` | `cm-update --rebuild` (image ของ puller ต้อง build ใหม่) |
| รหัสอ่านไม่ได้หลังเปลี่ยน CENTRAL_SECRET_KEY | ใส่รหัสผ่านใหม่ในฟอร์มของเครื่องนั้น |

ทดสอบโดยไม่ต้องเพิ่มเครื่อง (วัด CPU / RAM ของ session บน Windows): `scripts/win-poc.sh --host <ip> --thumbprint <..> --once`
