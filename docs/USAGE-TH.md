# คู่มือใช้งาน central-monitor และการเพิ่มเครื่อง/log

คู่มือนี้ต่อจาก `INSTALL-TH.md` (ติดตั้งครั้งแรก) — เน้น **ใช้งานประจำวัน** และ **เพิ่มเครื่องที่ต้องการดึง log**

> คำสั่ง `docker compose ...` ให้รันในโฟลเดอร์ `/opt/central-monitor/docker` (มี `docker-compose.yml` และ `.env` อยู่ที่นั่น) ส่วน `./scripts/...` เรียกจากที่ไหนก็ได้ ดูแผนผังโครงสร้างใน `README-TH.md`

---

## 1. ระบบทำงานยังไง (สั้น ๆ)

```
เครื่องปลายทาง (web-xx)                 server กลาง (central-monitor)
┌────────────────────┐   SSH ทุก 1 ชม.   ┌──────────────┐  ไฟล์ chunk   ┌───────────┐   ┌────────────┐
│ /var/log/.../*.log │ ───────────────►  │ central-puller│ ───────────► │ data/buffer│──►│ otel        │
│ node_exporter :9100│                   └──────────────┘               └───────────┘   └─────┬──────┘
└─────────┬──────────┘                          │ สถานะเครื่อง (load/mem/disk/service)         ▼
          │ scrape ทุก 15 วิ                    ▼ data/state/system/*.json             ┌────────────┐
          ▼                              ┌────────────┐                               │ OpenSearch │
   ┌────────────┐                        │ central-api │◄──────── query ──────────────┤ logs-central-*
   │ Prometheus │◄───── metrics ─────────┤ + เว็บ UI   │                               └────────────┘
   └────────────┘                        └────────────┘
```

- **log** → puller เข้า SSH ไป `tail -c` เฉพาะส่วนที่โตขึ้น (มี checkpoint) → เก็บเป็นไฟล์ใน `data/buffer/` → otel อ่านแล้วส่งเข้า OpenSearch
- **metrics (CPU/RAM/Disk/Network)** → Prometheus scrape `node_exporter` ของแต่ละเครื่องโดยตรง (ไม่ผ่าน SSH)
- **สถานะเครื่อง (service, process, event)** → puller ดึงผ่าน SSH รอบเดียวกับ log
- **UI** → `central-api` รวม 3 แหล่งข้างบนเข้าด้วยกัน

## 2. เริ่มใช้งานครั้งแรก (ลำดับคำสั่ง)

```bash
cd /opt/central-monitor
sudo sysctl -w vm.max_map_count=262144          # ถาวร: ดู INSTALL-TH.md ข้อ 3
./scripts/init-env.sh                            # สร้าง .env (login เว็บ admin/admin)
# หรือทั้งหมดนี้ในคำสั่งเดียว: bash install.sh

ssh-keygen -t ed25519 -N "" -C central-monitor -f keys/central_ed25519
touch keys/known_hosts                           # เตรียมเครื่องปลายทางตามข้อ 3 ด้านล่างก่อน แล้วค่อย ssh-keyscan

./scripts/preflight.sh                           # ต้องไม่มี [FAIL]
cd docker
docker compose up -d --build
docker compose ps                                # central-puller / central-api ต้อง healthy
```

จากนั้นทำ ISM policy (INSTALL-TH.md ข้อ 8.4) **ครั้งเดียว** แล้วเปิดเว็บ

## 3. เพิ่มเครื่องใหม่ที่มี log ให้ดึง

> **ทางลัด:** เพิ่มจากหน้าเว็บได้เลย — เมนู **Settings** → **+ Add target** (มี Scan host key และคำสั่งสำหรับเครื่องปลายทางให้ในหน้าเดียว) ดู `docs/CONFIG-TH.md` ข้อ 3.1 ส่วนด้านล่างคือวิธีทำมือ

ทำครบ 3 ส่วนต่อเครื่อง

### 3.1 บนเครื่องปลายทาง (ทำครั้งเดียวต่อเครื่อง)

```bash
# (ก) node_exporter สำหรับกราฟ CPU/RAM/Disk
sudo apt install -y prometheus-node-exporter
sudo systemctl enable --now prometheus-node-exporter
sudo ufw allow from <IP_server_กลาง> to any port 9100 proto tcp

# (ข) user สำหรับดึง log (อ่านอย่างเดียว)
sudo adduser --disabled-password --gecos "" central-pull
sudo usermod -aG adm,systemd-journal central-pull

# (ค) วาง public key ของ server กลาง (ได้จาก: cat keys/central_ed25519.pub)
sudo -u central-pull mkdir -p /home/central-pull/.ssh
echo 'from="<IP_server_กลาง>" ssh-ed25519 AAAA... central-monitor' | \
  sudo -u central-pull tee -a /home/central-pull/.ssh/authorized_keys
sudo chmod 700 /home/central-pull/.ssh && sudo chmod 600 /home/central-pull/.ssh/authorized_keys

# (ง) เช็คว่า user นี้อ่านไฟล์ log ได้จริง (แก้ path ตามจริง)
sudo -u central-pull ls -l /var/log/myapp/
sudo -u central-pull tail -n 1 /var/log/myapp/app.log
```

> ถ้า log อยู่ในโฟลเดอร์ที่ group `adm` อ่านไม่ได้ ให้ `setfacl -R -m u:central-pull:rX /path/to/logs` หรือเพิ่ม user เข้า group ของ log นั้น — ห้ามใช้ root

### 3.2 บน server กลาง — เก็บ host key แล้วแก้ `config/puller.yaml`

```bash
cd /opt/central-monitor
ssh-keyscan -t ed25519 -p 22 10.0.10.13 >> keys/known_hosts
ssh-keygen -lf keys/known_hosts        # เทียบลายนิ้วมือกับเครื่องปลายทาง: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

เพิ่ม block ใต้ `targets:` (ใน `config/puller.yaml` มี template คอมเมนต์ไว้ให้ท้ายไฟล์):

```yaml
  - name: web-03
    host: 10.0.10.13                 # ต้องตรงกับที่ ssh-keyscan ทุกตัวอักษร
    prometheus:
      job: web-03-node
      instance: 10.0.10.13:9100
    ssh:
      port: 22
      user: central-pull
    logs:
      - name: application            # -> plain lines
        path: /var/log/myapp/app.log
        type: application
      - name: logapp                 # -> multiline (เริ่มบรรทัดด้วย YYYY-MM-DDTHH:MM:SS:)
        path: /var/log/myapp/logapp/*.log
        type: application
      - name: loginuser              # -> parse "YYYY-MM-DD HH:MM:SS,mmm, message"
        path: /var/log/myapp/loginuser/*.log
        type: login
```

กฎสำคัญ

| หัวข้อ | รายละเอียด |
|---|---|
| `name` (เครื่อง) | ใช้เป็นส่วนหนึ่งของชื่อไฟล์ใน buffer และเป็นตัวกรองใน UI → ใช้ตัวอักษร/ตัวเลข/`-` เท่านั้น ห้ามมี `__` |
| `host` | ต้องมีใน `keys/known_hosts` (พอร์ตไม่ใช่ 22 ต้องเป็น `[host]:port` — `ssh-keyscan -p` ทำให้เอง) |
| `logs[].name` | เป็นตัวเลือก parser ของ otel: `logapp`, `loginuser`, `application` เท่านั้นที่ส่งเข้า OpenSearch |
| `logs[].path` | ไฟล์เดียว หรือ wildcard ใน **โฟลเดอร์เดียว** (`*.log`, `app-?.log`) — ไม่รองรับ `**` |
| `prometheus.job/instance` | โหมด bundled: ตั้งอะไรก็ได้ที่ไม่ซ้ำ / โหมด external: ต้องตรงกับ label ใน Prometheus ตัวจริง |
| `type` | ใส่เพื่ออธิบายเฉย ๆ ไม่มีโค้ดใช้ |

### 3.3 นำไปใช้และตรวจ

```bash
../scripts/preflight.sh --ssh             # ลอง SSH + เช็คว่า path log มีไฟล์ที่อ่านได้
docker compose restart central-puller       # สร้าง web-nodes.json ใหม่ + เริ่มรอบดึงทันที
docker compose logs -f --tail=80 central-puller
```

ใน log ต้องเห็นตามลำดับ: `[TARGET] web-03` → `[WILDCARD] ... files=N` → `[OK] ... bytes=... lines=...` → `[TARGET OK]` → `[PULL] cycle completed`

```bash
# ผลลัพธ์รวมของ puller (ต้องเป็น 1)
docker compose exec -T central-api curl -s central-puller:8000/metrics | grep -E '^ssh_pull_(success|source_success)'

# Prometheus เห็นเครื่องแล้ว (โหมด bundled)
curl -sG http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=up{web="web-03"}'

# log เข้า OpenSearch แล้ว (รอ ~10-30 วิหลังมีไฟล์ใน data/buffer)
docker compose exec -T central-api sh -c \
  'curl -sk -u "admin:${OPENSEARCH_ADMIN_PASSWORD}" "https://opensearch:9200/_cat/indices/logs-central-*?v"'
```

> log ใหม่เข้ามาทุก `scheduler.log_interval` (ค่าเริ่มต้น 2 นาที) ส่วน service / process / load เก็บทุก `scheduler.interval` (1 ชม.) ดึงหลายเครื่องพร้อมกัน (`scheduler.parallel`) และใช้การเชื่อมต่อ SSH เดิมซ้ำ

### 3.4 ลบ / แก้เครื่อง

แก้หรือลบ block ใน `puller.yaml` แล้ว `docker compose restart central-puller` (checkpoint เก่าของเครื่องที่ลบยังค้างใน `data/state/checkpoints.json` ไม่มีผลเสีย)
ถ้าเปลี่ยน `name` ของเครื่อง/ของ source ระบบจะถือเป็นแหล่งใหม่และ **ดึง log ทั้งไฟล์ตั้งแต่ต้นอีกครั้ง**

## 4. เพิ่ม log ชนิดใหม่ (format ที่ยังไม่มี parser)

ตัวอย่าง: nginx access log ที่อยากได้ `log.source = nginx`

1) `puller.yaml` — ตั้ง `name: nginx`
```yaml
      - name: nginx
        path: /var/log/nginx/access.log
        type: web
```
2) `start_push_log/otel/otelcol.yaml` — เพิ่ม receiver แล้วใส่ใน pipeline
```yaml
receivers:
  filelog/nginx:
    include: [ /data/buffer/*__nginx__*.log ]
    start_at: beginning
    include_file_name: true
    include_file_path: true
    storage: file_storage
    operators:
      - type: add
        field: resource["log.source"]
        value: nginx

service:
  pipelines:
    logs:
      receivers: [ filelog/logapp, filelog/loginuser, filelog/application, filelog/nginx ]
```
3) `docker compose restart otel central-puller`

> ชื่อไฟล์ใน buffer คือ `<site>__<เครื่อง>__<source>__<เวลา>__<offset>__<id>.log` — glob `*__<source>__*.log` จึงแม่นยำ
> ตาราง Logs ใน UI แสดง log **ทุกบรรทัด**ของเครื่องที่เลือก (ไม่กรองตามคำหรือชนิด) — ระดับ ERROR = มีคำ error/exception/failed/timeout/refused, WARN = มีคำ warn, ที่เหลือ = INFO

## 5. ใช้งานหน้าเว็บ

- เข้า `http://127.0.0.1:8090` (หรือ HTTPS ผ่าน nginx ตามข้อ 9 ของ INSTALL-TH.md) → login ด้วย user/password จาก `.env`
- เลือกเครื่องด้านบน → เห็น CPU / Memory / Disk / Network, สถานะ online/offline, จำนวน problem, errors 15 นาทีล่าสุด, เวลาที่ดึงล่าสุด
- ตาราง Logs: log ทั้งหมดของเครื่องที่เลือก (ล่าสุด 300 รายการ, เลื่อนดูในกล่อง) พร้อมค่า metrics ตอนนั้น — คลิกแถวเพื่อดูรายละเอียด สถานะ service/process/system events และ raw log
  - **แสดงเฉพาะ log ที่มี Metrics at time เสมอ** (ทุกเครื่อง รวม Windows) — log ที่ Prometheus ไม่มีข้อมูลของเครื่อง ณ เวลานั้น (เก่ากว่าที่ Prometheus เก็บ / exporter ล่ม / Prometheus ติดต่อไม่ได้) ไม่แสดงและไม่นับ แต่ยังอยู่ใน OpenSearch ตามปกติ ใช้กับผลค้นหาและ INFO ด้วย
- กล่อง Service, System events, ตาราง process และ Logs เลื่อนดูในกล่องได้ ไม่ทำให้หน้าเว็บยาว
- ตัวเลข "Problem logs" / "Errors 15m" ด้านบนยังนับเฉพาะข้อความที่เป็น error/warn ตามเดิม และมี "(N ชนิด)" ต่อท้าย = จำนวนปัญหาที่ไม่ซ้ำกัน
- **Group duplicates** (ติ๊กไว้เป็นค่าเริ่มต้น): รวมบรรทัดที่เป็นปัญหาเดียวกัน (ต่างกันแค่เวลา / ตัวเลข / IP / id — ดู fingerprint) เป็นแถวเดียว
  - คอลัมน์ "ครั้ง" = จำนวนบรรทัด (ชี้ดูเวลาครั้งแรก / ล่าสุด) · "+N เครื่อง" = เจอที่เครื่องอื่นด้วย · คลิกแถว = รายละเอียดของบรรทัดล่าสุด
  - ตัวเลขบนปุ่ม All / Error / Warn = จำนวนชนิด (ชี้ดูจำนวนบรรทัด) · เอาติ๊กออก = กลับเป็น 1 บรรทัด = 1 แถวแบบเดิม (เลือกหลายบรรทัดเพื่อ Acknowledge ได้)
  - ตอนค้นหา (Search) และดู INFO แสดงทีละบรรทัดเสมอ
  - ปุ่ม **Ack กลุ่ม** ท้ายแถว (operator ขึ้นไป) เลือก 1 ชม. / 24 ชม. / 7 วัน: ซ่อนทุกบรรทัดของปัญหานี้ **รวมบรรทัดที่จะเข้ามาในรอบดึงถัดไป** จนหมดเวลา แล้วโผล่กลับมาเองถ้ายังไม่หาย · ติ๊ก Show acknowledged เพื่อดู / กด "ยกเลิก ack"
  - Acknowledge ทีละบรรทัดแบบเดิม (เอาติ๊ก Group duplicates ออก แล้วเลือกบรรทัด) ยังใช้ได้ แต่บรรทัดที่เข้ามาใหม่ต้อง ack ใหม่

**ข้อควรรู้เรื่องเวลา:** puller ดึงทุก 1 ชม. log จึงเข้าหน้าเว็บเป็นรอบ ๆ (ช้าได้สูงสุด 1 ชม.) และอยู่ใน OpenSearch ตาม `OPENSEARCH_LOG_RETENTION` (ปริยาย 7 วัน) แก้ค่านี้ใน docker/.env แล้วรัน `bash install.sh --keep`

## 6. อาการที่พบบ่อยตอนเพิ่มเครื่อง

| อาการใน log ของ puller | สาเหตุ / วิธีแก้ |
|---|---|
| `Host key verification failed` | ไม่มี `host:` นี้ใน `keys/known_hosts` หรือพิมพ์ไม่ตรง (IP vs DNS) |
| `Permission denied (publickey)` | public key ไม่อยู่ใน `authorized_keys`, สิทธิ์ `~/.ssh` ผิด, หรือ `from="..."` ไม่ตรง IP server กลาง |
| `Could not resolve hostname` | container resolve ชื่อไม่ได้ (ไม่ใช้ `/etc/hosts` ของ host) → ใช้ IP หรือ DNS จริง |
| `stat: cannot statx ... Permission denied` / `No such file` | user `central-pull` อ่าน path นั้นไม่ได้ หรือ path ผิด |
| `[NO FILES] pattern=...` | wildcard ไม่เจอไฟล์ (ไม่ถือว่า error) — เช็ค path |
| `[INCOMPLETE LINE] ... retry next cycle` | ไฟล์มีแต่บรรทัดที่ยังเขียนไม่จบ จะดึงรอบหน้า |
| `partial pull: expected=... actual=...` | ไฟล์ถูก rotate/ตัดระหว่างดึง — retry เองตาม `retry.max_attempts` |
| กราฟ CPU/Memory เป็น `-` | Prometheus เข้า `instance` ไม่ได้ (firewall 9100) หรือ `job/instance` ไม่ตรง |
| ไม่มี log ใน OpenSearch แต่มีไฟล์ใน `data/buffer` | `logs[].name` ไม่ใช่ `logapp`/`loginuser`/`application`/ชื่อที่เพิ่ม receiver ไว้ — ดู `docker compose logs otel` |
