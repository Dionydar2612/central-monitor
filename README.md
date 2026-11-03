# Central Monitor

ระบบรวมศูนย์สำหรับเฝ้าดู **log** และ **metrics** ของเว็บเซิร์ฟเวอร์หลายเครื่องจากจุดเดียว เครื่องปลายทางไม่ต้องติดตั้ง agent ส่ง log เพราะ server กลางจะดึง log เองผ่าน SSH แล้วส่งต่อเข้า OpenSearch ส่วน CPU, Memory และ Disk เก็บด้วย Prometheus ทั้งหมดแสดงรวมกันบนหน้าเว็บที่ต้อง login

---

## คุณสมบัติหลัก

- **ไม่ต้องติดตั้ง agent ส่ง log** — ดึง log ผ่าน SSH ด้วย user สิทธิ์อ่านอย่างเดียว และดึงเฉพาะส่วนที่เพิ่มขึ้น (จำตำแหน่งด้วย checkpoint)
- **รวม log, metrics และสถานะเครื่องไว้ที่เดียว** — คลิก log แต่ละบรรทัดเพื่อดู CPU/Memory และสถานะ service/process ณ เวลานั้น
- **จัดการเครื่องผ่านหน้าเว็บ** — หน้า Settings ใช้เพิ่ม/แก้/ลบเครื่อง และวาง public key ให้เครื่องปลายทางได้เลย (ไม่เก็บรหัสผ่าน)
- **ลบข้อมูลเก่าอัตโนมัติ** — OpenSearch ใช้ ISM policy ลบ index ตามอายุที่ตั้งไว้ (ปริยาย 7 วัน)
- **ใช้กับ Prometheus ที่มีอยู่แล้วได้** — เลือกใช้ Prometheus ที่มากับระบบ หรือต่อกับตัวที่มีอยู่บน server
- **ปลอดภัยตั้งแต่ค่าเริ่มต้น** — ทุกพอร์ตผูกกับ `127.0.0.1` และ container ของระบบเอง (puller, cleaner, api) รันด้วย user ปกติ ไม่ใช่ root

## สถาปัตยกรรม

```
 เว็บเซิร์ฟเวอร์ปลายทาง                      Central Monitor server
┌──────────────────────┐   SSH (read-only)  ┌──────────────┐   ┌──────┐   ┌────────────┐
│ /logs/*.log          │ ◄───────────────── │ central-     │──►│ otel │──►│ OpenSearch │
│ /proc (load/mem/...) │                    │ puller       │   └──────┘   └─────┬──────┘
│ node_exporter :9100  │ ◄──── scrape ──┐   └──────┬───────┘                    │
└──────────────────────┘                │          │ สถานะเครื่อง (JSON)          │ log
                                        │          ▼                            ▼
                                 ┌──────┴─────┐  metrics  ┌────────────────────────────┐
                                 │ Prometheus │ ────────► │ central-api (Flask + Web)  │
                                 └────────────┘           │ 127.0.0.1:8090             │
                                                          └────────────────────────────┘
```

| Service | หน้าที่ | การเข้าถึง |
|---|---|---|
| `central-puller` | ดึง log และข้อมูลระบบจากเครื่องปลายทางผ่าน SSH ตามรอบที่ตั้งไว้ | ภายใน Docker |
| `otel` | OpenTelemetry Collector อ่านไฟล์พักใน `data/buffer` แล้วส่งเข้า OpenSearch | ภายใน Docker |
| `opensearch` | เก็บและค้นหา log | ภายใน Docker |
| `buffer-cleaner` | ลบไฟล์พักที่เก่ากว่าอายุที่ตั้งไว้ | ภายใน Docker |
| `central-api` | REST API + หน้าเว็บ dashboard (ต้อง login) | `127.0.0.1:8090` |
| `prometheus`, `node-exporter` | เก็บ metrics (เฉพาะโหมด bundled) | `127.0.0.1:9090` |
| `cadvisor` | metrics ของ container (ทางเลือก ปิดไว้เป็นค่าเริ่มต้น) | ภายใน Docker |

## ความต้องการของระบบ

**Server กลาง**

- Ubuntu หรือ Debian ที่มีสิทธิ์ `sudo` และต่ออินเทอร์เน็ตได้ (ตอนติดตั้ง)
- Docker และ Docker Compose v2 (`install.sh` ติดตั้งให้ถ้ายังไม่มี)
- ตั้งค่า `vm.max_map_count=262144` (`install.sh` ตั้งให้)

**เว็บเซิร์ฟเวอร์ปลายทาง**

- เปิด SSH ให้ server กลางเข้าได้ และมี user สำหรับดึง log ที่มีสิทธิ์อ่านอย่างเดียว
- ติดตั้ง `node_exporter` (พอร์ต `9100`) สำหรับ metrics

## เริ่มต้นใช้งาน

```bash
cd /opt/central-monitor
bash install.sh
```

สคริปต์จะติดตั้ง Docker, สร้าง `docker/.env` และ SSH key, สตาร์ททุก container และตั้ง ISM policy ให้ครบในคำสั่งเดียว

เสร็จแล้วให้เปิด **http://127.0.0.1:8090** แล้ว login ด้วย `admin` / `admin` จากนั้นไปที่หน้า **Settings → + Add target** เพื่อเพิ่มเครื่องปลายทาง

> **สำคัญ:** เปลี่ยนรหัสผ่านเริ่มต้นก่อนใช้งานจริง (ดู [`docs/CONFIG-TH.md`](docs/CONFIG-TH.md)) หรือตั้งตอนติดตั้งเลยด้วย `UI_USER=<user> UI_PASS=<password> bash install.sh`

| คำสั่ง | ใช้เมื่อ |
|---|---|
| `bash install.sh` | ติดตั้งครั้งแรก (สร้าง `.env` และ SSH key ใหม่) |
| `bash install.sh --keep` | อัปเกรดโค้ดโดยคง `.env` และ SSH key เดิมไว้ ไม่ต้อง deploy key ใหม่ |
| `bash install.sh --no-start` | เตรียมทุกอย่าง แต่ยังไม่สตาร์ท container |

ถ้าจะเข้าใช้จากเครื่องอื่น ให้ใช้ SSH tunnel (`ssh -L 8090:127.0.0.1:8090 <user>@<server>`) หรือตั้ง nginx + HTTPS ไว้ด้านหน้า ตามข้อ 9 ของ [`docs/INSTALL-TH.md`](docs/INSTALL-TH.md)

## การตั้งค่า

| ไฟล์ | ตั้งค่าอะไร |
|---|---|
| `config/puller.yaml` | รายชื่อเครื่อง, path ของ log, รอบการดึง, อายุไฟล์พัก |
| `docker/.env` | login เว็บ, รหัสผ่าน OpenSearch, secret, โหมด Prometheus (`COMPOSE_PROFILES`), อายุ log (`OPENSEARCH_LOG_RETENTION`) |
| `start_push_log/otel/otelcol.yaml` | วิธีแยก (parse) log แต่ละประเภท |
| `metrics/prometheus/prometheus.yml` | การ scrape ของ Prometheus (โหมด bundled) |

สั่งงานประจำได้จากโฟลเดอร์ `docker/`:

```bash
cd docker
docker compose ps                      # ดูสถานะทุก service
docker compose logs -f central-puller  # ดู log ของ service
docker compose up -d --remove-orphans  # ใช้ค่าจาก docker-compose.yml ล่าสุด
```

## โครงสร้างโปรเจกต์

```
central-monitor/
├── config/            puller.yaml — ไฟล์ตั้งค่าหลักของระบบ
├── start_push_log/    pipeline หลัก: puller → data/buffer → otel → OpenSearch (+ cleanup, ISM policy)
├── web_dashboard/     backend (Flask API) และ frontend (HTML/CSS/JS)
├── metrics/           ตั้งค่า Prometheus
├── docker/            docker-compose.yml, Dockerfiles, systemd units
├── scripts/           init-env.sh, preflight.sh, deploy-key.sh, apply-ism.py
├── docs/              คู่มือภาษาไทย
├── install.sh         ติดตั้งด้วยคำสั่งเดียว
├── data/              ข้อมูลที่เกิดตอนรัน (ไม่เข้า git)
└── keys/              SSH key และ known_hosts (ไม่เข้า git)
```

## เอกสาร

| เอกสาร | เนื้อหา |
|---|---|
| [`docs/CONFIG-TH.md`](docs/CONFIG-TH.md) | คู่มือฉบับย่อ: ติดตั้ง, เปลี่ยนรหัสผ่าน, เพิ่มเครื่อง |
| [`docs/README-TH.md`](docs/README-TH.md) | flow การทำงานแบบละเอียด และ "จะแก้เรื่องนี้ต้องไปที่ไฟล์ไหน" |
| [`docs/INSTALL-TH.md`](docs/INSTALL-TH.md) | คู่มือติดตั้งแบบเต็ม, การตั้งค่าความปลอดภัย, การแก้ปัญหา |
| [`docs/USAGE-TH.md`](docs/USAGE-TH.md) | การใช้งานประจำวัน และการเพิ่มเครื่อง |
| [`docs/CHANGES-TH.md`](docs/CHANGES-TH.md) | บันทึกการเปลี่ยนแปลง |

## ความปลอดภัย

- อย่า commit `docker/.env` หรือไฟล์ใน `keys/` และตั้งสิทธิ์ `.env` เป็น `600`
- ใช้ user บนเครื่องปลายทางที่อ่านได้อย่างเดียว และจำกัด `from="<IP>"` ใน `authorized_keys`
- ถ้าเข้าเว็บผ่าน HTTPS ให้ตั้ง `CENTRAL_COOKIE_SECURE=true`
- ดู checklist ก่อนเปิดใช้งานจริงได้ที่ข้อ 13 ของ [`docs/INSTALL-TH.md`](docs/INSTALL-TH.md)
