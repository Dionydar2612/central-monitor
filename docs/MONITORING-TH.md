# Windows Monitoring & Bottleneck Detection

ที่เก็บ metrics มีที่เดียวคือ **Prometheus** (อ่านด้วย PromQL) ไม่ใช้ InfluxDB / Zabbix / Grafana
ค่าที่อ่านไม่ได้จะไม่แสดง (ซ่อนแถว / คอลัมน์ / การ์ดนั้น) ไม่มีค่าสมมติ

## 1. เส้นทางข้อมูล

```
Windows                                                          Central
windows_exporter 0.32.1 (127.0.0.1:9182 เท่านั้น)
   └─ Fluent Bit (cm-agent) prometheus_scrape ทุก 15 วิ ──HTTPS :4318 + token──▶ central-agent-gw ──remote write──▶ Prometheus
Linux
   node_exporter :9100 ◀── Prometheus scrape (job web-nodes) ────────────────────────────────────────────────────┘
Ping (ทุกเครื่องที่มี host)
   Prometheus job "ping" ──▶ blackbox_exporter (icmp) ──ICMP──▶ เครื่อง
```

ทำไมปลอดภัยกว่าเปิด windows_exporter ให้ Prometheus ดึงตรง:
- windows_exporter ฟังที่ `127.0.0.1` และไม่เปิด firewall (`REMOVE=FirewallException`) -> เครื่องอื่นในวง network ดึงหรือดักไม่ได้
- ข้อมูลออกจากเครื่องทางเดียวคือ Fluent Bit -> central แบบ TLS ที่ตรวจใบรับรองของ central + token
- ปิด label `cmdline` ของ process (`--no-collector.process.cmdline`) ไม่ให้ command line ที่อาจมีรหัสผ่านหลุดออกไป

## 2. ตั้งค่า windows_exporter (สคริปต์ install-fluentbit.ps1 ทำให้)

```
msiexec /i windows_exporter-0.32.1-amd64.msi /qn
  ENABLED_COLLECTORS=cpu,cpu_info,os,memory,logical_disk,physical_disk,net,tcp,system,service,process,diskdrive,time,gpu
  LISTEN_ADDR=127.0.0.1 LISTEN_PORT=9182
  EXTRA_FLAGS="--no-collector.process.cmdline" REMOVE=FirewallException
```
- exporter ไม่ตอบใน 40 วินาที -> ลองชุดพื้นฐาน (ไม่มี `diskdrive`, `gpu`) -> ยังไม่ได้ -> ใช้ตัวเก็บในตัว Fluent Bit (`windows_exporter_metrics`) แทน
- ไม่มีอินเทอร์เน็ต: วาง `windows_exporter-0.32.1-amd64.msi` ข้างสคริปต์ หรือ `-ExporterMsi <path>` · ไม่ต้องการ exporter: `-NoExporter`
- Fluent Bit อ่าน: `[INPUT] Name prometheus_scrape · Host 127.0.0.1 · Port 9182 · Scrape_Interval 15s`

## 3. Metrics ที่ใช้ (Windows)

| หมวด | metric | ใช้ทำอะไร |
|---|---|---|
| CPU | `windows_cpu_time_seconds_total{core,mode}` | usage, user, privileged, interrupt+DPC, ต่อ core |
| | `windows_system_processor_queue_length` | งานที่รอ CPU (bottleneck) |
| | `windows_system_context_switches_total`, `windows_system_processes`, `windows_system_threads` | Performance |
| Memory | `windows_memory_physical_total_bytes`, `windows_memory_available_bytes` | used %, available % |
| | `windows_memory_committed_bytes`, `windows_memory_commit_limit_bytes` | committed % |
| | `windows_memory_swap_pages_read_total`, `windows_memory_swap_page_operations_total`, `windows_memory_page_faults_total`, `windows_memory_cache_bytes`, `windows_memory_pool_*` | pages/sec, faults |
| Disk (ต่อไดรฟ์) | `windows_logical_disk_size_bytes`, `windows_logical_disk_free_bytes`, `windows_logical_disk_info` | used %, free, label, file system |
| | `windows_logical_disk_{reads,writes}_total`, `..._{read,write}_bytes_total` | IOPS, MB/s |
| | `windows_logical_disk_{read,write}_seconds_total` ÷ ops | latency เฉลี่ย (ms) |
| | `windows_logical_disk_requests_queued`, `windows_logical_disk_idle_seconds_total`, `windows_logical_disk_readonly` | queue, busy %, status |
| | `windows_diskdrive_info / size_bytes / status` | Hardware: รุ่น, ขนาด, สถานะดิสก์ |
| Network (ต่อการ์ด) | `windows_net_bytes_{received,sent}_total`, `windows_net_current_bandwidth_bytes` | Mbps, bandwidth % |
| | `windows_net_packets_{received,outbound}_{errors,discarded}_total`, `windows_net_output_queue_length_packets` | errors, discards, queue |
| | `windows_net_nic_operation_status`, `windows_net_nic_info`, `windows_net_nic_address_info` | สถานะ, MAC, IP |
| | `windows_tcp_connections_established`, `windows_tcp_connection_failures_total`, `windows_tcp_connections_reset_total`, `windows_tcp_segments_{retransmitted,sent}_total` | TCP |
| Service | `windows_service_state{service,state}`, `windows_service_start_mode`, `windows_service_info` | รายการ service, service สำคัญ |
| Process | `windows_process_cpu_time_seconds_total`, `windows_process_working_set_private_bytes`, `windows_process_io_bytes_total` | top CPU / RAM / Disk I/O |
| System | `windows_os_hostname`, `windows_os_info`, `windows_os_install_time_timestamp_seconds`, `windows_system_boot_time_timestamp`, `windows_time_timezone`, `windows_time_computed_time_offset_seconds`, `windows_time_clock_sync_source` | แท็บ System |
| Hardware | `windows_cpu_info`, `windows_cpu_info_core`, `windows_cpu_info_logical_processor`, `windows_cpu_core_frequency_hertz`, `windows_gpu_temperature_celsius`, `windows_gpu_fan_speed_rpm` | แท็บ Hardware |
| Agent | `fluentbit_uptime{cm_web}`, `windows_exporter_build_info`, `windows_exporter_collector_success` | Online/Down, collector ที่ล้มเหลว |
| Ping | `probe_success`, `probe_icmp_duration_seconds{phase="rtt"}` (label `cm_target`) | latency, packet loss |

รองรับชื่อเก่าด้วย (เครื่องที่ยังใช้ตัวเก็บในตัว Fluent Bit / exporter รุ่นเก่า):
`windows_cpu_time_total`, `windows_cs_physical_memory_bytes`, `windows_os_physical_memory_free_bytes`, label service `name`

## 4. PromQL หลัก (L = `cm_web="<เครื่อง>"`)

```
CPU %              100 - avg(rate({__name__=~"windows_cpu_time(_seconds)?_total",L,mode="idle"}[5m])) * 100
Memory used %      (1 - (max(windows_memory_available_bytes{L}) or max(windows_os_physical_memory_free_bytes{L}))
                        / max({__name__=~"windows_memory_physical_total_bytes|windows_cs_physical_memory_bytes",L})) * 100
Disk used % (ไดรฟ์) (1 - windows_logical_disk_free_bytes{L,volume!~"HarddiskVolume.*|_Total"} / windows_logical_disk_size_bytes{...}) * 100
Disk latency ms    (rate(windows_logical_disk_read_seconds_total[5m]) + rate(..._write_seconds_total[5m]))
                   / (rate(windows_logical_disk_reads_total[5m]) + rate(..._writes_total[5m])) * 1000   (เฉพาะไดรฟ์ที่มี I/O >= 1/วินาที)
Disk busy %        100 - clamp_max(rate(windows_logical_disk_idle_seconds_total[5m]), 1) * 100
CPU queue / core   max(windows_system_processor_queue_length{L}) / count(count by(core)(cpu idle))
Bandwidth %        max(rx หรือ tx ที่มากกว่า) / windows_net_current_bandwidth_bytes * 100
Packet loss %      (1 - avg(avg_over_time(probe_success{cm_target="<เครื่อง>"}[5m]))) * 100
Ping latency ms    avg(avg_over_time(probe_icmp_duration_seconds{cm_target="<เครื่อง>",phase="rtt"}[5m])) * 1000
```
Linux ใช้ node_exporter ในรูปแบบเดียวกัน (`node_cpu_seconds_total`, `node_memory_MemAvailable_bytes`, `node_disk_*`, `node_network_*`, `node_netstat_Tcp_*`, `node_load1`)
โค้ด: `web_dashboard/backend/metrics_service.py`, `host_details.py`, `overview_service.py`, `start_push_log/puller/sensor_monitor.py`

## 5. เกณฑ์และการแจ้งเตือน

| เกณฑ์ (warning / error) | Linux (ค่าเดิม) | Windows (ตามสเปก) |
|---|---|---|
| CPU % | 75 / 90 | 80 / 95 |
| Memory used % | 80 / 90 | 80 / 95 |
| Disk used % | 80 / 90 | 80 / 90 |
| Available RAM % (ต่ำกว่า) | - | 20 / 10 |
| Disk latency ms | 20 / 50 | 20 / 50 |
| Packet loss % | 1 / 5 | 1 / 5 |
| Ping latency ms | 100 / 250 | 100 / 250 |

- ปรับได้ที่ **Settings > Alerts** (ทั้งระบบ แยก Linux / Windows) และฟอร์มของเครื่อง (เฉพาะเครื่อง)
  เก็บใน `puller.yaml`: `thresholds:` (Linux), `thresholds_windows:` (Windows), `targets[].thresholds`
- **แจ้งเตือนเมื่อเกินต่อเนื่อง** ค่าเริ่มต้น **3 นาที** (`sensors.confirm_checks: 6` × 30 วินาที) ปรับได้ 0.5–60 นาที
- **Service สำคัญ** (Windows) ค่าเริ่มต้น `WinDefend, wuauserv, TermService, Dnscache, Dhcp, EventLog`
  Error เมื่อหยุดทั้งที่ start mode = Automatic · start mode Manual/Disabled ที่หยุดอยู่ = ปกติ (เช่น wuauserv เริ่มเมื่อมีงาน)
  service ที่ไม่มีบนเครื่องจะไม่ถูกสร้าง sensor · ตั้งเฉพาะเครื่องได้ (`targets[].critical_services`, ใส่ `-` = ไม่ตรวจ)

## 6. Bottleneck (หลายค่าพร้อมกัน)

แต่ละหมวดมีสัญญาณหลายตัว ถ้าเกินเกณฑ์ **ตั้งแต่ 2 ตัว** = **Bottleneck** (Warning หรือ Error ถ้าตัวใดถึงระดับ error)
เกิน **ตัวเดียว** = **Trend** (สถานะ OK ไม่แจ้งเตือน แต่แสดงในแท็บ Bottleneck) · Memory used % กับ Available RAM % นับเป็นตัวเดียวกัน

| หมวด | สัญญาณ |
|---|---|
| CPU | CPU % ≥ warning · คิว CPU ต่อ core ≥ 2 (Windows: Processor Queue Length, Linux: load1) |
| Memory | Memory % ≥ warning · Available RAM ≤ warning · Pages input/sec ≥ 100 (Linux: swap pages/sec ≥ 100) · Committed ≥ 90% |
| Disk | latency ≥ warning · disk queue ≥ 2 · busy ≥ 90% |
| Network | bandwidth ≥ 80% · errors+discards ≥ 1/s · output queue ≥ 2 · TCP retransmit ≥ 2% · packet loss ≥ warning · ping latency ≥ warning |

แต่ละผลแสดง: ความรุนแรง, metric, ค่า, เกณฑ์, ระยะเวลา (duration), คำแนะนำ — แท็บ **Bottleneck** ของเครื่อง และหน้า Events
ปิดการตรวจได้ที่ Settings > Alerts (`sensors.bottleneck: false`)

## 7. หน้าเว็บ

- **WEB status**: ไทล์ Total / Up / Down / Warning / Critical / Paused + ค่าเฉลี่ย CPU / RAM / Disk ของเครื่องที่ online
  หัวกลุ่มแสดงค่าเฉลี่ยของกลุ่ม · ตาราง: Name, OS, IP, Status, CPU, Memory, Disk, Network (MB/s + ping ms / loss %), Alerts, Last pull
  คลิกหัวคอลัมน์เพื่อเรียง · ตัวกรองข้อความ / OS / สถานะ
- **รายละเอียดเครื่อง Windows** 8 แท็บ: Overview · Performance · Bottleneck · Processes · Services · Hardware · System · Alerts & Events
- **รายละเอียดเครื่อง Linux** ใช้หน้าแบบเดิม (ไม่มีแท็บ): KPI, กราฟ System Load คู่การ์ด Service, CPU / Memory process, System events
  (sensor ใหม่ของ Linux เช่น Disk latency, Packet loss, Bottleneck ยังทำงานและดูได้ที่หน้า Events)
- API: `GET /api/host/<เครื่อง>/{performance|hardware|system|bottleneck}`, `GET/PUT /api/alert-settings` (admin)

## 8. เพิ่มเครื่อง Windows

1. Settings > + Add target > ชนิด **Windows (agent)** > ใส่ชื่อ, กลุ่ม, **Host (IP)** (ใส่ host เพื่อให้มี ping / packet loss) > Save
2. Download installer (zip) + Make command > รันบน Windows (PowerShell Administrator)
3. สคริปต์ติดตั้ง windows_exporter + Fluent Bit ให้ แล้วเครื่องขึ้นใน WEB status ภายใน ~1 นาที
เครื่องที่ติดตั้ง agent รุ่นก่อนแล้ว: รันคำสั่งติดตั้งซ้ำ (อัปเกรดเป็น windows_exporter อัตโนมัติ)

## 9. ข้อจำกัด

- **อุณหภูมิ CPU / พัดลม / SMART บน Windows: อ่านไม่ได้ จึงไม่แสดง** — windows_exporter ไม่มีแหล่งข้อมูลมาตรฐาน (thermal zone มักไม่มีบน server/VM) ต้องใช้ iLO / iDRAC / เครื่องมือผู้ผลิต
  มีเฉพาะอุณหภูมิ / พัดลมของ GPU เมื่อ driver รองรับ (WDDM 2.4+) และสถานะดิสก์จาก `diskdrive`
- Linux: อุณหภูมิ / พัดลมมีเมื่อ node_exporter เห็น hwmon (เครื่องจริง) · SMART ต้องติดตั้ง smartctl_exporter เพิ่ม
- Packet loss ประมาณจาก ping 1 ครั้งทุก 15 วินาที (20 ครั้ง / 5 นาที) วัดจาก central ไปเครื่อง ไม่ใช่ระหว่างเครื่องกับผู้ใช้
  ต้องมี `host` ในการตั้งค่าเครื่อง และ ICMP ต้องไม่ถูก firewall บล็อก (Windows ปิด ping เป็นค่าเริ่มต้น -> เปิดกฎ "File and Printer Sharing (Echo Request - ICMPv4-In)")
- blackbox_exporter มากับโหมด bundled-prometheus เท่านั้น · โหมด external ต้องเพิ่ม job `ping` (ดู metrics/prometheus/prometheus.yml) และรัน blackbox_exporter เอง
- latency ของดิสก์คิดเฉพาะไดรฟ์ที่มี I/O ≥ 1 ครั้ง/วินาที (ไดรฟ์ว่างค่าไม่นิ่ง) · Linux แสดง I/O ต่อดิสก์ (sda) ไม่ใช่ต่อ partition
- process collector สร้าง series ต่อ process (ประมาณ 20 ต่อ process) — เครื่องที่มี process เยอะมาก Prometheus จะใช้พื้นที่มากขึ้น
- เครื่องที่ยังใช้ตัวเก็บในตัว Fluent Bit (ยังไม่อัปเกรด / ติดตั้ง exporter ไม่ได้) ไม่มี physical disk, TCP, hardware, latency บางส่วน -> แสดง Unavailable
