# Windows แบบ cm-agent (ลง agent · ส่งข้อมูลเข้ามาเอง)

ใช้คู่กับแบบ WinRM (ไม่ลง agent) ได้ · ตอนเพิ่มเครื่องเลือก **ชนิดเครื่อง = Windows (cm-agent)**

```
เครื่อง Windows                                              Central
cm-exporter (windows_exporter, 127.0.0.1:9182 เท่านั้น)
cm-agent (Fluent Bit) ──HTTPS :4318 + token──▶ central-agent-gw ──▶ OpenSearch (log ERROR / WARN)
  metrics ทุก MetricsInterval                                 └──▶ agent-gw:8889 ──▶ puller (agent_bridge.py)
  Event Log ทุก EventInterval / ไฟล์ log                            แปลงเป็น metric แบบ node_exporter ──▶ Prometheus
  heartbeat ทุก HeartbeatInterval                                   + snapshot (service / process / software)
task CentralMonitorInventory (Software) ทุก InventoryMinutes
```

## 2 วิธีรับข้อมูล (เลือกในหน้า Settings > การเชื่อมต่อ > วิธีรับข้อมูล)

| | push (ปริยาย) | pull |
|---|---|---|
| ใช้เมื่อ | เครื่อง Windows ต่อไปหา central :4318 ได้ | เครื่องต่อหา central ไม่ได้ (firewall ระหว่างวง) แต่ central ต่อเข้าเครื่องได้ |
| บนเครื่อง | cm-agent (Fluent Bit) + cm-exporter (127.0.0.1) | cm-exporter อย่างเดียว ฟัง 0.0.0.0:9182 + firewall เปิดให้เฉพาะ IP ของ central |
| token / ใบรับรอง | ใช้ | ไม่ใช้ |
| log | Fluent Bit ส่งเข้า OpenSearch ทันที | task CentralMonitorLogs (cm-logs.ps1) ทุก EventInterval (อย่างน้อย 1 นาที) เขียนลง textfile · central อ่านพร้อม metrics ทุก MetricsInterval |
| สถานะ Down | ไม่มี heartbeat ~3 รอบ | central อ่าน http://<host>:9182/metrics ไม่ได้ ~3 รอบ |

- **ไม่สร้าง user ใหม่**: service `cm-agent` / `cm-exporter` และ task รันด้วย LocalSystem
- หน้าเว็บเหมือนเครื่อง Linux / WinRM: Performance / Bottleneck / Hardware / System / Alerts + แท็บ **Software** (โปรแกรมที่ติดตั้ง + พอร์ตที่เปิด)
- เกณฑ์ / sensor / Custom alert rules / การ์ด Teams ใช้ชุดเดียวกัน · availability = heartbeat (ขาด ~3 รอบ = Down)

## ตั้งค่าก่อนติดตั้ง (Settings > เครื่อง > การเชื่อมต่อ)

| ค่า | ปริยาย | ช่วง | ผล |
|---|---|---|---|
| Metrics + สถานะ | เปิด | | windows_exporter (cpu, memory, disk, net, tcp, system, service) |
| Event Log + ไฟล์ log | เปิด | | ERROR / WARN เท่านั้น |
| Software / พอร์ต | เปิด | | ต้องเปิด Metrics · task priority ต่ำ อ่าน registry (ไม่ใช้ WMI) |
| Process | ปิด | | collector ที่หนักที่สุด เปิดเมื่อจำเป็น |
| อ่าน metrics ทุก | 60 วิ | 15-600 | windows_exporter ทำงานเฉพาะตอนถูกอ่าน -> ยิ่งนานยิ่งเบา |
| heartbeat ทุก | 60 วิ | 15-600 | ขาด ~3 รอบ = Down |
| อ่าน Event Log ทุก | 30 วิ | 5-600 | |
| หาไฟล์ log ใหม่ทุก | 60 วิ | 10-600 | |
| ส่งข้อมูลทุก | 15 วิ | 1-120 | |
| รายการโปรแกรม / พอร์ตทุก | 60 นาที | 15-1440 | |

หน่วยความจำ: Fluent Bit จำกัด buffer ในหน่วยความจำ (Event Log 4 MB, ไฟล์ log 8 MB, backlog 8 MB) ที่เหลือพักลงดิสก์ (สูงสุด 256 MB)
เปลี่ยนค่าแล้ว **บันทึก แล้วกด ติดตั้ง / อัปเดต cm-agent อีกครั้ง** (หรือรัน install-cm-agent.bat ใหม่)

## ติดตั้ง

**วิธีที่ 1 จากหน้าเว็บ**: กรอก Admin user / password ของเครื่อง (ใช้ครั้งเดียว ไม่บันทึก) + WinRM 5985/5986 ที่เครื่องเปิดอยู่ -> ติดตั้ง / อัปเดต cm-agent
ใช้ Administrator ของเครื่อง หรือ domain admin (local admin อื่นมักถูก UAC remote restriction บล็อก)

**วิธีที่ 2 zip**: Download ชุดติดตั้ง -> แตก zip บนเครื่อง -> ดับเบิลคลิก `install-cm-agent.bat` (zip มี token ลบทิ้งหลังติดตั้ง)

ถอน: ปุ่ม "ถอน cm-agent ออกจากเครื่อง" หรือ `uninstall-cm-agent.bat`

## เตรียม central (ครั้งเดียว)

1. `cm-update` -> สร้าง `CENTRAL_AGENT_TOKEN` ใน docker/.env + ใบรับรอง `keys/agent-tls/` + container `central-agent-gw` (พอร์ต 4318)
2. เปิด firewall ของ central ให้เครื่อง Windows เข้าพอร์ต 4318 (เฉพาะวงภายใน) · central บน WSL: ทำ port forward 4318 ที่ Windows host
3. (แนะนำ) วางไฟล์ `fluent-bit-<ver>-win64.zip` และ `windows_exporter-<ver>-amd64.exe` ไว้ที่ `data/agent-bin/` บน central
   ปุ่มติดตั้งจะส่งไฟล์ไปให้เอง (เครื่อง Windows ไม่ต้องออกอินเทอร์เน็ต) · ถ้าไม่มี ตัวติดตั้งบนเครื่องจะลองดาวน์โหลดเอง
4. ใบรับรองต้องมี IP / ชื่อที่เครื่อง Windows ใช้เรียก central (ถ้าไม่มี ตัวติดตั้งตรวจด้วยชื่อในใบรับรองแทน) สร้างใหม่: `bash scripts/make-cert.sh agent <IP> <ชื่อ>` แล้ว `cm-update`

## ตรวจปัญหา

- บนเครื่อง: `Get-Service cm-agent, cm-exporter` · log `C:\ProgramData\central-monitor-agent\fluent-bit.log`
- pull: จาก central `docker exec central-puller wget -qO- http://<IP เครื่อง>:9182/metrics | head` ต้องได้ข้อมูล
- central: `docker logs central-agent-gw --tail 50` · `docker exec central-puller wget -qO- http://agent-gw:8889/metrics | grep cm_web | head`
