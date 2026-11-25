Central Monitor - Monitor เครื่อง Windows แบบไม่ต้องลง agent (Agentless / Pull)
=====================================================================

central ดึงข้อมูลเองผ่าน WinRM HTTPS (พอร์ต 5986) เข้า JEA endpoint "CentralMonitor"
ด้วยบัญชี cm-monitor (ไม่ใช่ admin) ที่เรียกได้แค่ฟังก์ชันอ่าน Get-CM* 6 ตัว
บนเครื่อง Windows ไม่มี service / scheduled task / agent / ไฟล์ข้อมูล ของเรา
เครื่อง Windows ไม่ต้องต่อกลับไปหา central (เหมาะกับ network ที่เปิดทางเดียว)

ไฟล์ในโฟลเดอร์นี้ (ต้องอยู่ด้วยกัน)
  setup-cm-monitor.bat    ติดตั้ง / อัปเดต (รันซ้ำได้)   ·  "setup-cm-monitor.bat resetpw" = ออกรหัสใหม่
  check-cm-monitor.bat    ตรวจสุขภาพ อ่านอย่างเดียว ไม่แก้อะไร
  remove-cm-monitor.bat   ถอนเฉพาะสิ่งที่ setup สร้าง (ตาม manifest.json)
  cm-monitor-config.txt   ตั้งค่า: CentralIP, Port, LogPaths, DenyLogon
  *.ps1, CentralMonitor.psm1   ตัวทำงานจริง (ไม่ต้องเปิดเอง)

ขั้นตอน (PoC เครื่อง ALBEN_2016)
  1) แก้ cm-monitor-config.txt  (CentralIP=172.27.2.22, LogPaths= โฟลเดอร์ log ที่ต้องการ ถ้ามี)
  2) ดับเบิลคลิก setup-cm-monitor.bat  -> จด Host / Port / User / Thumbprint / Password ที่แสดงตอนจบ
     (รหัสบันทึกไว้ที่ C:\ProgramData\CentralMonitor\cm-monitor-credential.txt อ่านได้เฉพาะ admin
      คัดลอกแล้วลบไฟล์นี้ทิ้ง)
     หมายเหตุ: setup จะ restart service WinRM 1 ครั้ง (ไม่กี่วินาที)
  3) บน central (ในโฟลเดอร์ central-monitor):
       scripts/win-poc.sh --host 192.168.181.101 --thumbprint <Thumbprint> --once
     ผ่านแล้วรันวัดผล 24 ชม. (ใช้ tmux / screen หรือ nohup):
       CM_WIN_PASSWORD='<Password>' nohup scripts/win-poc.sh --host 192.168.181.101 --thumbprint <Thumbprint> --hours 24 > win-poc.log 2>&1 &
     ผลสรุปอยู่ใน win-poc-192.168.181.101.json (ค่าสูงสุด ไม่มีค่าเฉลี่ย)
  4) ระหว่างทดสอบ ดับเบิลคลิก check-cm-monitor.bat บน Windows ดู RAM / CPU ของ session ได้

เกณฑ์ผ่าน PoC
  - wsmprovhost CPU สูงสุด < 2% ของ 1 core  ·  RAM สูงสุด < 150 MB
  - ไม่มีไฟล์ข้อมูลเพิ่มบนดิสก์ (check-cm-monitor.bat แสดงขนาด)
  - คำสั่งนอกรายการถูกปฏิเสธ (JEA)  ·  ไม่มี error ของโปรแกรมอื่นใน Event log

ต้องมี: Windows Server 2016 ขึ้นไป (PowerShell 5.1) · สิทธิ์ admin ตอนติดตั้ง · ไม่ต้องมีอินเทอร์เน็ต
ถ้า GPO บังคับ ExecutionPolicy = AllSigned ตัว .bat จะรัน .ps1 ไม่ได้ (ต้อง sign ไฟล์ก่อน)
