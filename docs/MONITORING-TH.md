# Monitoring & Bottleneck Detection (Linux)

รองรับเฉพาะเครื่อง Linux (SSH + node_exporter) · ส่วนของเครื่อง Windows (agent, windows_exporter, Netstat Monitor) เอาออกแล้ว

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

- ปรับได้ที่ **Settings > Alerts** (ทั้งระบบ) และฟอร์มของเครื่อง (เฉพาะเครื่อง)
  เก็บใน `puller.yaml`: `thresholds:`, `targets[].thresholds`
- **แจ้งเตือนเมื่อเกินต่อเนื่อง** ค่าเริ่มต้น **3 นาที** (`sensors.confirm_checks: 6` × 30 วินาที) ปรับได้ 0.5–60 นาที

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
- **รายละเอียดเครื่อง**: KPI, กราฟ System Load คู่การ์ด Service, CPU / Memory process, System events
- API: `GET /api/host/<เครื่อง>/{performance|hardware|system|bottleneck}`, `GET/PUT /api/alert-settings` (admin)

## 5. ข้อจำกัด

- อุณหภูมิ / พัดลมมีเมื่อ node_exporter เห็น hwmon (เครื่องจริง) · SMART ต้องติดตั้ง smartctl_exporter เพิ่ม
- Packet loss ประมาณจาก ping 1 ครั้งทุก 15 วินาที (20 ครั้ง / 5 นาที) วัดจาก central ไปเครื่อง
  ต้องมี `host` ในการตั้งค่าเครื่อง และ ICMP ต้องไม่ถูก firewall บล็อก
- blackbox_exporter มากับโหมด bundled-prometheus เท่านั้น · โหมด external ต้องเพิ่ม job `ping` (ดู metrics/prometheus/prometheus.yml) และรัน blackbox_exporter เอง
- latency ของดิสก์คิดเฉพาะดิสก์ที่มี I/O ≥ 1 ครั้ง/วินาที · แสดง I/O ต่อดิสก์ (sda) ไม่ใช่ต่อ partition
- เครื่อง Windows ที่ยังค้างใน `puller.yaml` (`type: agent`) จะไม่ถูกตรวจ และขึ้นในหน้า Settings ว่า "Windows (เลิกรองรับ)" ให้ลบได้
