# Netstat Monitor (Windows Server)

เมนู **Apps & Web > Netstat Monitor** (`/netstat`) ดูการเชื่อมต่อ TCP / UDP และคิวของแต่ละพอร์ตบนเครื่อง Windows แบบ real-time

## ทำงานอย่างไร

```
Windows: cm-netstat.ps1 (process เดียวรันค้าง, เก็บทุก 1 / 2 / 5 วิ) -> textfile cm_netstat.prom -> windows_exporter :9182
   โหมด pull : central-puller อ่าน http://<เครื่อง>:9182/metrics?collect[]=textfile ทุก interval วินาที
   โหมด push : Fluent Bit ส่ง cm_netstat_* ทุก 5 วินาที -> Prometheus -> central-puller ถามทุก 2 วินาที
central-puller (netstat.py) -> data/state/netstat/live.json (ค่าล่าสุด) + alerts.json (alert) + System log / Teams / ป๊อปอัป
central-api   -> /api/netstat/stream (Server-Sent Events) -> หน้าเว็บอัปเดตเองโดยไม่ต้อง refresh
ย้อนหลัง       -> Prometheus (scrape 15 วิ, agent ส่งค่าสูงสุดใน 15 วิ -> ไม่พลาดค่าพุ่ง) เก็บตาม retention ของ Prometheus
```

- **Collection interval** = agent เก็บถี่แค่ไหน (ติดตั้งด้วย `-NetstatInterval 1|2|5` ปริยาย 1) และ central อ่านถี่แค่ไหน (ตั้งต่อเครื่องในหน้า Hosts & Ports)
- **Dashboard update interval** = หน้าจออัปเดตทุก 1 / 2 / 5 / 10 วิ (เลือกมุมขวาบน จำต่อ browser)
- การเชื่อมต่อหลุด: browser ต่อใหม่เอง (EventSource) · central ตัด stream ทุก ~55 วิให้ต่อใหม่ · คนดูพร้อมกันเกินที่รับได้ -> หน้าเว็บถอยไปใช้ polling เอง

## ตั้งค่า

1. ติดตั้ง / อัปเดต agent บนเครื่อง Windows ด้วยชุด zip ใหม่ (ติดตั้งทับได้ ไม่ต้องถอนของเดิม) -> มี scheduled task `CentralMonitorNetstat`
2. หน้า Netstat Monitor > **Hosts & Ports**: เปิด Monitoring ของเครื่อง · กด **พอร์ต** -> ติ๊กพอร์ตที่ต้องการ + ใส่เกณฑ์ Queue / Connections (Warning, Critical) -> บันทึก
3. ค่าเก็บใน `config/puller.yaml`

```yaml
targets:
  - name: db-server-01
    type: agent
    netstat:
      enabled: true
      interval: 1
      ports:
        - {port: 1433, proto: tcp, queue: {warning: 50, critical: 80}, conn: {warning: 200, critical: 300}}
        - {port: 443, proto: tcp, queue: {warning: 100, critical: 150}, conn: {warning: 500, critical: 800}}
        - {port: 5432, proto: tcp, dir: out, enabled: false}     # ขาออก / ปิดกฎไว้
```

สิทธิ์: viewer ดูได้ · operator / admin เปิด-ปิด Monitoring เลือกพอร์ต และตั้งเกณฑ์ได้ (บันทึกใน Activity log)

## ค่าที่ได้ และข้อจำกัดของ Windows

| ค่า | ที่มา | หมายเหตุ |
|---|---|---|
| Connections ตามสถานะ (Established, Time Wait, Close Wait, Syn Sent, Syn Received, ...) | .NET `IPGlobalProperties` ทุกรอบ | ขาเข้า = ต่อเข้าพอร์ตที่เครื่องเปิดรับ · ขาออก = พอร์ตปลายทาง (60 อันดับแรก) |
| Listening / Process / PID | `Get-NetTCPConnection` / `Get-NetUDPEndpoint` ทุก 15 วิ | |
| **Listening Queue** + Queue Limit + Utilization | perf counter `HTTP Service Request Queues\CurrentQueueSize` + `queueLength` ของ app pool | เฉพาะพอร์ตของ IIS / HTTP.sys เท่านั้น |
| **Receive Queue / Send Queue** | - | **Unsupported**: Windows ไม่มี Recv-Q / Send-Q ต่อ socket ใน API ปกติ |

ค่าที่อ่านไม่ได้แสดง **Unavailable / Unsupported** ไม่ใช่ 0 และไม่ใช้ประเมินเกณฑ์
พอร์ตที่ไม่ใช่ HTTP.sys: แจ้ง "คิวพุ่งผิดปกติ" จากจำนวน SYN_RECEIVED (คิว handshake ของ listen) แทน

## การแจ้งเตือน

Queue เกิน Warning / Critical · Connections เกิน Warning / Critical · พอร์ตที่เลือกไม่ LISTEN · เครื่องไม่ตอบ (Host offline)
· agent หยุดส่ง (heartbeat ไม่เปลี่ยน > max(10 วิ, 3 x interval) วัดด้วยนาฬิกาของ central) · คิวพุ่ง >= 3 เท่าของค่าเฉลี่ยเคลื่อนที่

- ค่า >= Critical = Critical · >= Warning = Warning · ต่ำกว่า = Normal (Critical ต้องมากกว่า Warning)
- ต้องเป็นต่อเนื่อง 3 ครั้งก่อนเปิด alert · เปิดแล้วไม่สร้างซ้ำ (อัปเดต Last Detected / ค่าสูงสุด) · ปกติ 3 ครั้ง -> Recovery
- ส่งเข้า System log + Microsoft Teams + ป๊อปอัป แบบเดียวกับ sensor อื่น · ประวัติเก็บ 30 วัน (`data/state/netstat/alerts.json`)

## API

| | |
|---|---|
| `GET /api/netstat/hosts` | เครื่อง Windows + สถานะ agent |
| `GET/PUT /api/netstat/hosts/<เครื่อง>` | พอร์ตที่พบ + ที่เลือก / บันทึก (operator) |
| `GET /api/netstat/dashboard?host=&port=&proto=&status=` | ค่าปัจจุบัน + สรุป |
| `GET /api/netstat/stream?every=2` | Server-Sent Events (`event: snapshot`) |
| `GET /api/netstat/history?web=&key=tcp/in/1433&range=15m|1h|6h|24h` หรือ `&start=&end=` | ย้อนหลังจาก Prometheus |
| `GET /api/netstat/alerts?status=active|all&web=` | alert |
