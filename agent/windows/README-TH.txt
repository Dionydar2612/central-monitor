Central Monitor Agent สำหรับ Windows (Fluent Bit)
==================================================

ส่งอะไรไปที่ central
  - Windows Event Log (ปริยาย System, Application) เฉพาะ Critical / Error / Warning
  - ไฟล์ log ที่ระบุ (-Logs) เฉพาะบรรทัดที่เป็น ERROR / WARN (INFO ไม่ส่ง เหมือนเครื่อง Linux)
    IIS (path มี inetpub): ส่งเฉพาะ request ที่ได้ HTTP 5xx และใช้เวลาในไฟล์ (UTC)
  - สัญญาณ "agent ยังทำงานอยู่" ทุก 30 วินาที (สถานะ / Last seen ในหน้าเว็บ)
  - metrics ของเครื่อง (CPU / RAM / Disk / Network / Service / Process / Hardware) จาก windows_exporter
    windows_exporter ฟังเฉพาะ 127.0.0.1:9182 (ไม่เปิดพอร์ตออกนอกเครื่อง) Fluent Bit อ่านแล้วส่งต่อแบบเข้ารหัส
  ส่งผ่าน HTTPS พอร์ต 4318 ของ central พร้อม token  central ล่ม -> พักไว้ในดิสก์ (สูงสุด 512MB) แล้วส่งตามทีหลัง

ต้องมี
  - Windows Server 2016 ขึ้นไป / Windows 10 ขึ้นไป (PowerShell 5.1)
  - ต่อออกไปที่ <central>:4318 ได้ (TCP)
  - ถ้าเครื่องออกอินเทอร์เน็ตไม่ได้ วางไฟล์เหล่านี้ไว้ข้างสคริปต์
      fluent-bit-<version>-win64.zip       https://packages.fluentbit.io/windows/
      windows_exporter-0.32.1-amd64.exe   https://github.com/prometheus-community/windows_exporter/releases/tag/v0.32.1
    ไม่ต้องการ windows_exporter: ใส่ -NoExporter (ใช้ตัวเก็บในตัว Fluent Bit ข้อมูลน้อยกว่า)

ติดตั้ง (PowerShell แบบ Run as Administrator)
  1) ที่ central: Settings > Add target > Type = Windows (agent) > บันทึก
  2) กด "Install command" -> ดาวน์โหลดชุดติดตั้ง (zip นี้) + คัดลอกคำสั่ง
  3) แตก zip บนเครื่อง Windows (หรือวางบน share) แล้วรันคำสั่งที่คัดลอกมา เช่น
       powershell -ExecutionPolicy Bypass -File .\install-fluentbit.ps1 -Central 10.0.0.5 -Token xxxx -Name win-app-01 -Logs "D:\app\logs\*.log"
  4) ภายใน ~1 นาที เครื่องขึ้นสถานะ OK ในหน้า WEB status  ทดสอบ ERROR:
       eventcreate /T ERROR /ID 999 /L APPLICATION /SO CentralMonitorTest /D "test error"

หลายเครื่อง
  วางโฟลเดอร์นี้บน share แล้วสั่งรันผ่าน GPO (Computer Startup Script) / Intune / SCCM
  -Name ไม่ระบุ = ใช้ชื่อเครื่อง (ตัวเล็ก) ต้องเพิ่มชื่อเดียวกันในหน้า Settings ก่อน

ปัญหาที่พบบ่อย
  - certificate verify failed: ชื่อ/IP ที่ใส่ใน -Central ไม่อยู่ในใบรับรองของ central
      แก้ที่ central: bash scripts/make-cert.sh agent <ชื่อ/IP ที่ Windows ใช้ ...> แล้ว cm-update
      หรือชั่วคราว: ใส่ -SkipCertCheck (ยังเข้ารหัส แต่ไม่ตรวจว่าเป็น central ตัวจริง)
  - 401 Unauthorized: token ไม่ตรงกับ CENTRAL_AGENT_TOKEN ใน docker/.env ของ central
  - ดู log ของ agent: C:\ProgramData\central-monitor-agent\fluent-bit.log
  - สถานะ service: Get-Service cm-agent   เริ่มใหม่: Restart-Service cm-agent

ถอนการติดตั้ง
  powershell -ExecutionPolicy Bypass -File .\uninstall-fluentbit.ps1 [-Purge] [-KeepExporter]


โหมด pull (เครื่องนี้ต่อไปหา central ไม่ได้ แต่ central ต่อเข้ามาได้) - metrics + log (ERROR / WARN)
  log: scheduled task CentralMonitorLogs (cm-logs.ps1) ทุก 1 นาที เก็บไว้บนเครื่อง ~3 ชม. central ดึงไปทาง :9182 ทุกรอบ log
       เลือก log ได้เหมือน push: -Channels "System,Application" -Logs "C:\app\logs\*.log"
  ที่ central: Settings > เครื่อง > การเชื่อมต่อ > วิธีรับข้อมูล = central ดึงจาก Windows (pull) > Save
  แบบ PowerShell:  powershell -ExecutionPolicy Bypass -File .\install-fluentbit.ps1 -Pull -Central <IP central> -Name <ชื่อ>
  แบบไฟล์ .bat: แตก zip แล้วคลิกขวา install-pull.bat > Run as administrator ใส่ IP ของ central เมื่อถาม (ต้องเป็น IP)
      รัน PowerShell ได้ -> .bat เรียก install-fluentbit.ps1 -Pull ให้เอง (ครบที่สุด มีแท็บ Software)
      PowerShell ถูกบล็อก -> ติดตั้งด้วย cmd ล้วน (sc / netsh) ไม่มีแท็บ Software · บังคับแบบ cmd: install-pull.bat <IP> - nops
      ไม่มีอินเทอร์เน็ต: วาง windows_exporter-0.32.1-amd64.exe ไว้ข้างไฟล์ .bat
  ถอน: uninstall-pull.bat (คลิกขวา > Run as administrator)  หรือ uninstall-fluentbit.ps1
