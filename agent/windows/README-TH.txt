Central Monitor Agent สำหรับ Windows (Fluent Bit)
==================================================

ส่งอะไรไปที่ central
  - Windows Event Log (ปริยาย System, Application) เฉพาะ Critical / Error / Warning
  - ไฟล์ log ที่ระบุ (-Logs) เฉพาะบรรทัดที่เป็น ERROR / WARN (INFO ไม่ส่ง เหมือนเครื่อง Linux)
    IIS (path มี inetpub): ส่งเฉพาะ request ที่ได้ HTTP 5xx และใช้เวลาในไฟล์ (UTC)
  - สัญญาณ "agent ยังทำงานอยู่" ทุก 30 วินาที (สถานะ / Last seen ในหน้าเว็บ)
  ส่งผ่าน HTTPS พอร์ต 4318 ของ central พร้อม token  central ล่ม -> พักไว้ในดิสก์ (สูงสุด 512MB) แล้วส่งตามทีหลัง

ต้องมี
  - Windows Server 2016 ขึ้นไป / Windows 10 ขึ้นไป (PowerShell 5.1)
  - ต่อออกไปที่ <central>:4318 ได้ (TCP)
  - ไฟล์ fluent-bit-<version>-win64.zip วางไว้ข้างสคริปต์ (ถ้าเครื่องออกอินเทอร์เน็ตไม่ได้)
    ดาวน์โหลดจาก https://packages.fluentbit.io/windows/  หรือ https://docs.fluentbit.io/manual/installation/windows

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
  powershell -ExecutionPolicy Bypass -File .\uninstall-fluentbit.ps1 [-Purge]
