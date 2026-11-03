# central-monitor — flow การทำงาน และแผนผังโครงสร้าง

เอกสารนี้อธิบาย 2 อย่าง: (1) ระบบทำงานยังไงตั้งแต่ต้นจนจบ (2) แต่ละโฟลเดอร์มีหน้าที่อะไร ถ้าจะแก้เรื่องไหนให้ไปที่ไหน

---

## 1. Flow การทำงาน

```
 เครื่อง web (หลายเครื่อง)                     server กลาง (central-monitor)
┌────────────────────────┐
│ /logs/*.log            │ ◄── SSH (key + known_hosts) ──┐
│ /proc/loadavg,meminfo… │                               │
│ node_exporter :9100    │ ◄──── scrape ───────────┐     │
└────────────────────────┘                         │     │
                                                   │     │
  ┌─────────────────────── start_push_log ─────────┼─────┴──────────────────────────────┐
  │                                                │                                     │
  │  ① puller.py (container: central-puller)       │                                     │
  │     อ่าน config/puller.yaml ทุก 1 ชม.          │                                     │
  │     ├─ log: ssh → stat ขนาดไฟล์ → tail -c ส่วนที่โต                                  │
  │     │       (checkpoint ใน data/state/checkpoints.json)                              │
  │     │       → เขียนไฟล์ data/buffer/<site>__<เครื่อง>__<source>__<เวลา>__…log         │
  │     ├─ system info: ssh อ่าน load/mem/disk/service/process/event                     │
  │     │       → data/state/system/<เครื่อง>.json (ล่าสุด) + history/<เครื่อง>/*.json   │
  │     └─ สร้าง data/prometheus/web-nodes.json (รายชื่อเครื่องให้ Prometheus scrape)    │
  │                                                │                                     │
  │  ② otel (central-otel) filelog อ่าน data/buffer/*__logapp__*.log ฯลฯ                 │
  │     แยก parser ตามชื่อ source → ส่งเข้า OpenSearch (index logs-central-รายชั่วโมง)   │
  │                                                                                      │
  │  ③ OpenSearch เก็บ log + ISM policy ลบ index ตาม OPENSEARCH_LOG_RETENTION (7d)                         │
  │                                                                                      │
  │  ④ buffer-cleaner (cleanup-buffer.py --loop) ลบไฟล์ใน data/buffer ที่เกิน           │
  │     storage.buffer_retention ใน puller.yaml                                          │
  └──────────────────────────────────────────────────────────────────────────────────────┘
                                │                         │
                                ▼                         ▼
  ┌─────────── metrics ──────────────┐     ┌──────────────── web_dashboard ───────────────┐
  │ Prometheus (bundled หรือของเดิม)  │────►│ central-api (Flask + gunicorn, :8090)         │
  │ scrape node_exporter + puller    │     │  ├─ metrics  ← Prometheus                      │
  └──────────────────────────────────┘     │  ├─ logs     ← OpenSearch                      │
                                           │  ├─ สถานะเครื่อง ← data/state/system/*.json    │
                                           │  └─ หน้าเว็บ (frontend/) + login              │
                                           └───────────────────────────────────────────────┘
```

สรุปเป็นประโยค:

1. **start_push_log** — puller เข้า SSH ไปดึง log ที่โตขึ้นจากแต่ละเครื่อง วางเป็นไฟล์พักใน `data/buffer` → otel อ่านไฟล์ → ส่งเข้า OpenSearch → ตัว cleaner ลบไฟล์พักเมื่อครบเวลา
2. **metrics** — Prometheus scrape `node_exporter` ของแต่ละเครื่องตรง ๆ (ไม่ผ่าน SSH) โดยรายชื่อเครื่องมาจากไฟล์ที่ puller สร้างให้
3. **web_dashboard** — `central-api` รวม metrics (Prometheus) + log (OpenSearch) + สถานะเครื่อง (ไฟล์ JSON จาก puller) แสดงบนหน้าเว็บ

ข้อมูลที่ส่งต่อกันระหว่างระบบ (จุดเชื่อมต่อ):

| จาก → ไป | ผ่านอะไร |
|---|---|
| puller → otel | โฟลเดอร์ `data/buffer/*.log` |
| puller → Prometheus | `data/prometheus/web-nodes.json` |
| puller → web_dashboard | `data/state/system/<เครื่อง>.json` (+ `history/`) และ `data/state/checkpoints.json` |
| ทุกส่วน → config กลาง | `config/puller.yaml` (puller, api, cleaner อ่านไฟล์เดียวกัน) |

---

## 2. โครงสร้างโฟลเดอร์

```
central-monitor/
├── config/
│   └── puller.yaml              # รายชื่อเครื่อง/path log/ความถี่/retention (ไฟล์เดียวที่แก้บ่อยที่สุด)
│
├── start_push_log/              # ★ หัวใจหลัก: ดึงข้อมูลจากเครื่องอื่น → otel → OpenSearch
│   ├── puller/
│   │   ├── puller.py            #   จุดเริ่มต้น (เปิด /metrics แล้วเรียก scheduler)
│   │   ├── scheduler.py         #   วนรอบตาม interval: อ่าน config -> ดึง log -> เก็บสถานะ
│   │   ├── ssh_client.py        #   ช่องทาง SSH ไปเครื่องปลายทาง (stat/tail/find/cat)
│   │   ├── log_puller.py        #   ดึง log ส่วนที่โต -> data/buffer (ส่งต่อ otel)
│   │   ├── checkpoint.py        #   จำ byte offset ที่ดึงแล้ว
│   │   ├── system_info.py       #   เก็บ load/memory/service/process/event + history
│   │   ├── prometheus_targets.py#   สร้าง web-nodes.json จาก puller.yaml
│   │   ├── config.py            #   path + อ่าน puller.yaml
│   │   └── metrics.py           #   ตัวชี้วัดสุขภาพของ puller เอง
│   ├── otel/
│   │   └── otelcol.yaml         #   filelog receiver แต่ละ source → OpenSearch
│   ├── opensearch/
│   │   └── central-log-retention-ism-policy.json   # ลบ index อัตโนมัติ
│   └── cleanup/
│       └── cleanup-buffer.py    #   ลบไฟล์ใน data/buffer ตาม storage.buffer_retention (--loop ใน Docker)
│
├── web_dashboard/               # ส่วนแสดงผล
│   ├── backend/
│   │   ├── api.py               #   routes ทั้งหมด (บาง: รับ request -> เรียก service)
│   │   ├── settings.py          #   environment/path/รายชื่อเครื่อง
│   │   ├── auth.py              #   ตรวจรหัสผ่าน
│   │   ├── clients.py           #   เรียก Prometheus / OpenSearch
│   │   ├── metrics_service.py   #   CPU/Memory/Disk (ตอนนี้/กราฟ/ณ เวลา log)
│   │   ├── logs_service.py      #   ค้น/นับ log + ผูกกับ metrics
│   │   ├── system_state.py      #   อ่านไฟล์สถานะที่ puller เขียน
│   │   └── timeutil.py          #   แปลงเวลา ISO
│   └── frontend/                #   index.html, login.html, dashboard.js, dashboard.css (เดิมคือ central/static)
│
├── metrics/
│   └── prometheus/
│       └── prometheus.yml       # ใช้เฉพาะโหมด bundled-prometheus
│
├── docker/                      # ★ ทุกอย่างของ Docker อยู่ที่นี่
│   ├── docker-compose.yml       #   รัน docker compose จากโฟลเดอร์นี้
│   ├── .env.example             #   (.env จริงอยู่ที่นี่ สร้างด้วย scripts/init-env.sh)
│   ├── dockerfiles/
│   │   ├── puller.Dockerfile    #   image ของ central-puller และ buffer-cleaner (python + ssh)
│   │   └── web.Dockerfile       #   image ของ central-api (flask + gunicorn)
│   └── systemd/                 #   unit ทางเลือก (รัน puller แบบ native / timer ลบ buffer)
│
├── scripts/
│   ├── init-env.sh              # สร้าง docker/.env (login admin/admin + secret สุ่ม)
│   └── preflight.sh             # เช็คก่อนสตาร์ท (--ssh = ลอง SSH ทุกเครื่อง)
│
├── data/                        # ข้อมูลที่เกิดตอนรัน (ไม่เข้า git)
│   ├── buffer/                  #   ไฟล์ log พักรอ otel
│   ├── state/                   #   checkpoints.json + system/<เครื่อง>.json
│   └── prometheus/              #   web-nodes.json
├── keys/                        # central_ed25519 + known_hosts (ไม่เข้า git)
└── docs/                        # INSTALL-TH.md, USAGE-TH.md, README-TH.md (ไฟล์นี้)
```

### เทียบกับโครงสร้างเดิม (V3)

| เดิม | ใหม่ |
|---|---|
| `central/puller.py` (1,279 บรรทัด) | `start_push_log/puller/` แยก 8 โมดูล + `puller.py` เป็นจุดเริ่มต้น |
| `central/prometheus_targets.py` | `start_push_log/puller/prometheus_targets.py` |
| `otel/otelcol.yaml` | `start_push_log/otel/otelcol.yaml` |
| `opensearch/*.json` | `start_push_log/opensearch/` |
| `scripts/cleanup-buffer.py` | `start_push_log/cleanup/cleanup-buffer.py` |
| `central/api.py` (1,276 บรรทัด), `auth.py`, `system_state.py` | `web_dashboard/backend/` แยกเป็น 8 โมดูล (routes อยู่ที่ `api.py`) |
| `central/static/*` | `web_dashboard/frontend/` |
| `central/Dockerfile` (image เดียวใช้ร่วม) | `docker/dockerfiles/puller.Dockerfile` + `web.Dockerfile` (แยก 2 image) |
| `docker-compose.yml`, `.env.example` (ราก) | `docker/` |
| `systemd/` | `docker/systemd/` |
| `prometheus/prometheus.yml` | `metrics/prometheus/prometheus.yml` |

ใน container ยังวางที่ `/app` และ `/app/static` เหมือนเดิม ทุกไฟล์มีคอมเมนต์หัวไฟล์บอก 4 อย่าง: ทำอะไร, ทำงานอย่างไร, มีไว้ทำไม, เชื่อมกับอะไร

---

## 3. อยากแก้เรื่องนี้ ไปที่ไหน

| ต้องการ | ไฟล์ |
|---|---|
| เพิ่ม/ลบเครื่อง, เพิ่ม path log, เปลี่ยนความถี่ดึง, เปลี่ยน retention ของ buffer | `config/puller.yaml` |
| เพิ่มชนิด log ใหม่ (ต้องมี parser) | `start_push_log/otel/otelcol.yaml` (ดูตัวอย่างใน `docs/USAGE-TH.md` ข้อ 4) |
| แก้วิธี SSH ดึง log / checkpoint / retry | `start_push_log/puller/puller.py` |
| เปลี่ยนอายุ log ใน OpenSearch | `start_push_log/opensearch/central-log-retention-ism-policy.json` |
| เปลี่ยนรหัสผ่านเว็บ, โหมด Prometheus, secret | `docker/.env` |
| เปลี่ยนพอร์ต, memory ของ OpenSearch, เวอร์ชัน image | `docker/docker-compose.yml` |
| เปลี่ยนแพ็กเกจที่ลงใน image | `docker/dockerfiles/*.Dockerfile` |
| แก้ API / query OpenSearch / Prometheus | `web_dashboard/backend/api.py` |
| แก้หน้าตาเว็บ | `web_dashboard/frontend/` |
| เพิ่ม/แก้/ลบเครื่องจากหน้าเว็บ (Settings) | `web_dashboard/backend/targets_service.py` + `frontend/settings.{html,js}` |
| เพิ่ม scrape job ของ Prometheus | `metrics/prometheus/prometheus.yml` |

---

## 4. คำสั่งที่ใช้บ่อย

```bash
cd /opt/central-monitor
bash install.sh                  # ติดตั้งทั้งระบบในคำสั่งเดียว (login admin/admin)
./scripts/init-env.sh            # (ถ้าทำเอง) สร้าง docker/.env
./scripts/preflight.sh --ssh     # เช็คความพร้อม

cd docker                        # คำสั่ง docker compose รันจากโฟลเดอร์นี้
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=100 central-puller
docker compose restart central-puller    # ถ้าอยากให้เริ่มรอบดึงทันที (config อ่านใหม่ทุกรอบอยู่แล้ว)
docker compose restart otel              # หลังแก้ otelcol.yaml
docker compose up -d --build central-api # หลังแก้ web_dashboard/
```

หมายเหตุ: ใน `docker-compose.yml` ตั้ง `name: central-monitor` ไว้ ชื่อ network/volume/container จึงเหมือนเดิม volume `central-monitor_opensearch-data` เดิมใช้ต่อได้

---

## 5. สิ่งที่แก้ในรอบนี้ (V5) และข้อจำกัดที่ยังเหลือ

### แก้แล้ว
1. **`system_history_retention` ใช้งานได้จริง** — puller เขียนสถานะเครื่องทุกรอบลง `data/state/system/history/<เครื่อง>/<เวลา>.json` และลบที่เก่ากว่าค่าใน `puller.yaml` (ปริยาย 7d) ตอนคลิก log เก่า dashboard จะหา snapshot ที่ใกล้เวลานั้นที่สุด (ดูจากชื่อไฟล์ ไม่ต้องเปิดทุกไฟล์) ถ้าห่างเกิน 2 เท่าของรอบดึง จะไม่แสดงข้อมูล แทนที่จะแสดงข้อมูลผิดเวลา
2. **retention ของ buffer เหลือที่เดียว** — `buffer-cleaner` รัน `cleanup-buffer.py --loop` อ่าน `storage.buffer_retention` และ `storage.cleanup_interval` จาก `puller.yaml` (เดิมฮาร์ดโค้ด 30 นาทีใน compose) cleaner ลบไฟล์ `*.log.tmp` ที่ค้างด้วย
3. **`puller.py` แยกเป็นโมดูล** — `puller.py` เหลือ ~45 บรรทัด ที่เหลือแยกเป็น `scheduler` / `ssh_client` / `log_puller` / `checkpoint` / `system_info` / `config` / `metrics` (ดูหัวข้อ 2)
4. **ค่าสถานะเครื่องไม่ขึ้นบน dashboard** (พบระหว่างแก้) — snapshot เก็บ load, uptime, memory_used, swap, inode ไว้ใต้คีย์ `resource` แต่ฝั่ง API อ่านจากระดับบน ทำให้ช่อง Load / Uptime และรายละเอียดในหน้าต่าง log ว่างตลอด `system_state.py` ย้ายค่าขึ้นมาให้แล้ว
5. **เวลาของ System events** — puller ดึงเวลาจากต้นบรรทัด journal ใส่เป็น `timestamp` (หน้าเว็บมีคอลัมน์เวลาอยู่แล้วแต่เดิมว่าง)
6. **ลบโค้ดที่ไม่มีใครเรียก** — `detect_level` ใน api, `login_required` / `protect_page` ใน auth และ `_require_env` ที่เขียนซ้ำ 2 ที่
7. **query Prometheus เหลือชุดเดียว** — เดิมเขียนซ้ำ 3 ที่ (ปัจจุบัน/กราฟ/ณ เวลา log) และตัวกรอง disk ไม่ตรงกัน ตอนนี้ใช้ `fstype!~"tmpfs|overlay"` ทุกที่

### ยังเหลือ (ตั้งใจไม่แก้ เพราะต้องรู้ข้อมูลจริงของระบบคุณ)
- **การกรอง log ตามเครื่องทำฝั่ง Python** — ดึง log ล่าสุด 5,000 รายการ (นับ problem 1,000) ของทุกเครื่องมากรอง ถ้ามีหลายเครื่องและเครื่องหนึ่ง log ถี่มาก เครื่องที่ log น้อยอาจเห็นรายการน้อยกว่าจริง ทางแก้คือใส่ filter ชื่อไฟล์ใน query ฝั่ง OpenSearch (ต้องรู้ mapping ของ field `attributes.log.file.name` ก่อน)
- **cleanup ลบตามเวลา ไม่ได้เช็คว่า otel ส่งเข้า OpenSearch แล้วหรือยัง** — ถ้า otel/OpenSearch ล่มนานกว่า `buffer_retention` ไฟล์ที่ยังไม่ถูกส่งอาจถูกลบก่อน ถ้ากังวลให้เพิ่ม `buffer_retention`
- **ผูก `ssh_pull_*` metric กับ dashboard** — ยังไม่มีช่องบนหน้าเว็บ ตรวจได้จาก Prometheus โดยตรง
