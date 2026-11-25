Central Monitor - Monitor เครื่อง Windows แบบไม่ต้องลง agent (WinRM · อ่านอย่างเดียว)
=====================================================================

ปกติไม่ต้องใช้โฟลเดอร์นี้โดยตรง: หน้าเว็บ Settings > + Add target > ชนิดเครื่อง = Windows
> แท็บ การเชื่อมต่อ > "Download ชุดติดตั้ง (.zip)" จะได้ไฟล์ชุดนี้พร้อม cm-monitor-config.txt ที่กรอกให้แล้ว
คู่มือเต็ม: docs/WINDOWS-TH.md

central ดึงข้อมูลเองผ่าน WinRM HTTPS (พอร์ต 5986) เข้า JEA endpoint "CentralMonitor"
ด้วยบัญชี cm-monitor (ไม่ใช่ admin) ที่เรียกได้แค่ฟังก์ชันอ่าน Get-CM* 7 ตัว
บนเครื่อง Windows ไม่มี service / scheduled task / agent / ไฟล์ข้อมูล ของเรา
เครื่อง Windows ไม่ต้องต่อกลับไปหา central (เหมาะกับ network ที่เปิดทางเดียว)

ไฟล์ (ต้องอยู่ด้วยกัน)
  setup-cm-monitor.bat    ติดตั้ง / อัปเดต (รันซ้ำได้)  ·  "setup-cm-monitor.bat resetpw" = ออกรหัสใหม่
  check-cm-monitor.bat    ตรวจสุขภาพ อ่านอย่างเดียว ไม่แก้อะไร
  remove-cm-monitor.bat   ถอนเฉพาะสิ่งที่ setup สร้าง (ตาม manifest.json)
  cm-monitor-config.txt   ตั้งค่า: CentralIP, Port, LogPaths, DenyLogon
  *.ps1, CentralMonitor.psm1   ตัวทำงานจริง (ไม่ต้องเปิดเอง)

ขั้นตอน
  1) ตรวจ cm-monitor-config.txt (CentralIP = IP ของ central, LogPaths = โฟลเดอร์ log ที่ให้อ่าน)
  2) ดับเบิลคลิก setup-cm-monitor.bat -> ตอนจบจะแสดง Thumbprint และ Password
     (รหัสบันทึกไว้ที่ C:\ProgramData\CentralMonitor\cm-monitor-credential.txt อ่านได้เฉพาะ admin
      คัดลอกแล้วลบไฟล์นี้ทิ้ง) · setup จะ restart service WinRM 1 ครั้ง (ไม่กี่วินาที)
  3) หน้าเว็บ Settings > เครื่องนี้ > แท็บ การเชื่อมต่อ: ใส่ Password + Thumbprint > ทดสอบการเชื่อมต่อ > Save
  4) ตรวจภาระของ session บนเครื่องนี้ได้ด้วย check-cm-monitor.bat (RAM / CPU ของ wsmprovhost)

ต้องมี: Windows Server 2016 ขึ้นไป (PowerShell 5.1) · สิทธิ์ admin ตอนติดตั้ง · ไม่ต้องมีอินเทอร์เน็ต
ถ้า GPO บังคับ ExecutionPolicy = AllSigned ตัว .bat จะรัน .ps1 ไม่ได้ (ต้อง sign ไฟล์ก่อน)
