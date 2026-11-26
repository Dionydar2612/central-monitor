# Monitoring & Bottleneck Detection

เครื่อง Linux (SSH + node_exporter) และ Windows แบบไม่ลง agent (WinRM -> JEA, ดู [WINDOWS-TH.md](WINDOWS-TH.md))
เครื่อง Windows ส่ง metric ชื่อเดียวกับ node_exporter จึงใช้เกณฑ์ / sensor / bottleneck ด้านล่างชุดเดียวกัน

## 1. เส้นทางข้อมูล

```
เครื่อง Linux                                      Central
node_exporter :9100   <── Prometheus ดึง (job web-nodes, file_sd: data/prometheus/web-nodes.json)
log / journal / สถานะ  <── puller ผ่าน SSH (user central-pull) ทุกรอบ scheduler.interval
                      <── blackbox_exporter ping (ICMP) ทุก 15 วินาที (job ping)
```

## 2. เกณฑ์และการแจ้งเตือน

| เกณฑ์ (warning / error) | ค่าเริ่มต้น |
|---|---|
| CPU % | 75 / 90 |
| Memory used % | 80 / 90 |
| Disk used % | 80 / 90 |
| Disk latency ms | 20 / 50 |
| Packet loss % | 1 / 5 |
| Ping latency ms | 100 / 250 |
| เวลาคลาด (NTP) ms | 500 / 2000 |

- ปรับได้ที่ **Settings > Alerts** (ทั้งระบบ) และฟอร์มของเครื่อง (เฉพาะเครื่อง)
  เก็บใน `puller.yaml`: `thresholds:`, `targets[].thresholds`
- **แจ้งเตือนเมื่อเกินต่อเนื่อง** ค่าเริ่มต้น **3 นาที** (`sensors.confirm_checks: 6` × 30 วินาที) ปรับได้ 0.5–60 นาที

### Time sync (NTP)

sensor `Time sync (NTP)` ของทุกเครื่อง Linux ใช้ค่าจาก node_exporter (timex collector เปิดอยู่แล้ว ไม่ต้องติดตั้งเพิ่ม)

- **ไม่ sync** (`node_timex_sync_status = 0` เช่น chrony / systemd-timesyncd หยุด หรือไปไม่ถึง NTP server UDP 123) = **Error**
- **sync แล้ว** ดูว่านาฬิกาคลาดจาก central เท่าไร (`node_time_seconds` เทียบเวลาที่ Prometheus scrape) เกินเกณฑ์ = Warning / Error
- แจ้งเตือน / กลับปกติ ผ่าน Alert และการ์ด Teams เดิม · ปิดรายเครื่องได้ (ปิด sensor > Time sync) · node_exporter ที่ไม่มีค่า timex จะไม่มี sensor นี้
- ถ้าทุกเครื่องเตือนพร้อมกัน ให้ตรวจนาฬิกาของ central ก่อน

### NTP Monitor (หน้า /ntp)

- **NTP server**: admin เพิ่มที่หน้า NTP Monitor (หรือ `ntp.servers` ใน puller.yaml) · central ถาม NTP (UDP 123) ทุก 30 วินาที
  - วัด offset (นาฬิกา server - central), RTT, stratum, ref · กราฟย้อนหลัง 1h – 30d จาก Prometheus (`cm_ntp_offset_seconds`, `cm_ntp_rtt_seconds`, `cm_ntp_up`, `cm_ntp_stratum` จาก job central-puller)
  - สถานะ: ไม่ตอบ = Down · stratum 16 / leap alarm หรือ |offset| ≥ 2000 ms = Error · |offset| ≥ 500 ms หรือ RTT ≥ 200 ms = Warning (ปรับที่ปุ่ม "เกณฑ์")
  - แจ้ง Teams เมื่อสถานะเปลี่ยนและกลับปกติ (การ์ดหัวข้อ Alert) · ปิดการแจ้งรายตัวได้
  - central ต้องออก UDP 123 ไปหา server ได้ · โหมด Prometheus external ต้อง scrape central-puller:8000 ถึงจะมีกราฟ
- **เครื่อง Linux**: ตารางสถานะ Time sync + กราฟเวลาคลาดของทุกเครื่องเทียบกัน (คลิกชื่อเพื่อเน้นเส้น)
- หน้ารายละเอียดเครื่อง Linux: กราฟ System Load แท็บ **Time / NTP** (คลาดจาก central + คลาดจาก NTP server ตามที่ kernel ประเมิน)

## 3. Bottleneck (หลายค่าพร้อมกัน)

แต่ละหมวดมีสัญญาณหลายตัว ถ้าเกินเกณฑ์ **ตั้งแต่ 2 ตัว** = **Bottleneck** (Warning หรือ Error ถ้าตัวใดถึงระดับ error)
เกิน **ตัวเดียว** = **Trend** (สถานะ OK ไม่แจ้งเตือน) · Memory used % กับ Available RAM % นับเป็นตัวเดียวกัน

| หมวด | สัญญาณ |
|---|---|
| CPU | CPU % ≥ warning · load1 ต่อ core ≥ 2 |
| Memory | Memory % ≥ warning · Available RAM ≤ warning · swap pages/sec ≥ 100 · Committed ≥ 90% |
| Disk | latency ≥ warning · disk queue ≥ 2 · busy ≥ 90% |
| Network | bandwidth ≥ 80% · errors+discards ≥ 1/s · TCP retransmit ≥ 2% · packet loss ≥ warning · ping latency ≥ warning |

ดูผลได้ที่หน้า Events / หน้า sensor · ปิดการตรวจได้ที่ Settings > Alerts (`sensors.bottleneck: false`)

## 4. หน้าเว็บ

- **WEB status**: ไทล์ Total / Up / Down / Warning / Critical / Paused
  ตาราง: Name, IP, Status, CPU, Memory, Disk, Network (MB/s + ping ms / loss %), Alerts, Last pull
- **รายละเอียดเครื่อง** (แท็บ ใช้กับทั้ง Linux และ Windows): Overview (KPI + กราฟ System Load + Service) · Performance (CPU / Memory / ดิสก์ต่อไดรฟ์ / Network ต่อการ์ด / TCP / Ping)
  · Bottleneck · Processes · Hardware · System (OS / build / uptime / เวลา / IP + System events; Windows มี Windows updates ล่าสุด) · Alerts & Events (sensor + ประวัติ 7 วัน)
  ค่าที่เครื่องไม่ส่งมาจะไม่แสดง · Windows ใช้ข้อมูลเครื่องจาก inventory ที่ puller อ่านผ่าน WinRM ทุก 1 ชม.
- API: `GET /api/host/<เครื่อง>/{performance|hardware|system|bottleneck}`, `GET/PUT /api/alert-settings` (admin)

## 5. ข้อจำกัด

- อุณหภูมิ / พัดลมมีเมื่อ node_exporter เห็น hwmon (เครื่องจริง) · SMART ต้องติดตั้ง smartctl_exporter เพิ่ม
- Packet loss ประมาณจาก ping 1 ครั้งทุก 15 วินาที (20 ครั้ง / 5 นาที) วัดจาก central ไปเครื่อง
  ต้องมี `host` ในการตั้งค่าเครื่อง และ ICMP ต้องไม่ถูก firewall บล็อก
- blackbox_exporter มากับโหมด bundled-prometheus เท่านั้น · โหมด external ต้องเพิ่ม job `ping` (ดู metrics/prometheus/prometheus.yml) และรัน blackbox_exporter เอง
- latency ของดิสก์คิดเฉพาะดิสก์ที่มี I/O ≥ 1 ครั้ง/วินาที · แสดง I/O ต่อดิสก์ (sda) ไม่ใช่ต่อ partition
- เครื่อง Windows แบบเดิมที่ยังค้างใน `puller.yaml` (`type: agent`) จะไม่ถูกตรวจ และขึ้นในหน้า Settings ว่า "Windows (เลิกรองรับ)" ให้ลบได้
  (เพิ่มใหม่เป็นชนิด Windows แบบไม่ลง agent: `type: winrm`)
