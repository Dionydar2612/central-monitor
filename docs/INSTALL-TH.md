# คู่มือติดตั้ง central-monitor บน Server จริง

เอกสารนี้อธิบายการนำ `central-monitor` (เวอร์ชัน production-ready ที่เคลียร์ config ของ lab ออกแล้ว) ไปติดตั้งบน server จริง และเชื่อมกับ Prometheus

- ติดตั้งที่: `/opt/central-monitor`
- **โครงสร้างโฟลเดอร์ใหม่:** คำสั่ง `docker compose ...` ทุกคำสั่งในเอกสารนี้ให้รันในโฟลเดอร์ `/opt/central-monitor/docker` ส่วน `./scripts/...` และ `keys/` อยู่ที่โฟลเดอร์รากเหมือนเดิม ไฟล์ `.env` อยู่ที่ `docker/.env` (ดูแผนผังเต็มใน `docs/README-TH.md`)
- OS ที่ทดสอบแนวทางไว้: Ubuntu 22.04 / 24.04 (Debian ใช้ได้เหมือนกัน)

---

## 1. ภาพรวมระบบ

```
                    SSH (อ่าน log + system info)
 ┌─────────────┐  ───────────────────────────────►  ┌──────────────┐
 │ central-    │                                     │  Web server  │
 │ puller      │        ┌───────────────────────────►│  (node_exp.  │
 └──────┬──────┘        │ scrape :9100               │   :9100)     │
        │ เขียนไฟล์     │                            └──────────────┘
        ▼               │
   data/buffer ──► otel ──► OpenSearch ◄── central-api ──► เบราว์เซอร์
                                              │  (UI :8090 → nginx/HTTPS)
                                              ▼
                                         Prometheus  ◄── แบบ bundled (ใน stack)
                                                     หรือ  ตัวที่มีอยู่แล้วบน server
```

| ส่วน | หน้าที่ | เข้าถึงได้จาก |
|---|---|---|
| central-puller | SSH ไปดึง log/ข้อมูลระบบจากเว็บเซิร์ฟเวอร์ทุก 1 ชม. | ภายใน docker |
| otel | อ่านไฟล์ใน `data/buffer` แล้วส่งเข้า OpenSearch | ภายใน docker |
| OpenSearch | เก็บ log (ลบอัตโนมัติด้วย ISM policy) | ภายใน docker เท่านั้น |
| central-api | เว็บ UI + API (ต้อง login) | `127.0.0.1:8090` |
| Prometheus | เก็บ metrics (โหมด bundled) | `127.0.0.1:9090` |

ทุกพอร์ตที่เปิดออก host ผูกกับ `127.0.0.1` เท่านั้น การเข้าจากภายนอกต้องผ่าน nginx (ข้อ 9) หรือ SSH tunnel

---

## 2. สิ่งที่ต้องเตรียม

**Server กลาง (ที่จะลง central-monitor)**

- RAM ≥ 4 GB, CPU ≥ 2 core, Disk ว่าง ≥ 20 GB
- Docker Engine + Docker Compose plugin (v2)
- ออกอินเทอร์เน็ตได้ตอน pull image ครั้งแรก
- ออกไปหาเว็บเซิร์ฟเวอร์ได้ที่ SSH (22 หรือพอร์ตที่ใช้) และ `9100` (node_exporter)

**เว็บเซิร์ฟเวอร์แต่ละเครื่อง (เป้าหมายที่จะ monitor)**

- ติดตั้ง `node_exporter` (พอร์ต 9100)
- มี user สำหรับให้ central ดึงข้อมูล (แนะนำ `central-pull`) ที่มีสิทธิ์อ่านอย่างเดียว

**ข้อมูลที่ต้องรู้ก่อนเริ่ม**

- IP หรือ DNS ของเว็บเซิร์ฟเวอร์แต่ละเครื่อง (ต้องเป็นชื่อที่ resolve ได้ *จากภายใน container* — อย่าพึ่ง `/etc/hosts` ของ host)
- path ของ log ที่จะดึง
- ตัดสินใจว่าจะใช้ Prometheus แบบไหน (ข้อ 5)

---

## 3. ติดตั้ง Docker และตั้งค่าระบบ (ครั้งเดียว)

```bash
# ติดตั้ง Docker (ถ้ายังไม่มี)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER      # แล้ว logout/login ใหม่
docker compose version             # ต้องขึ้นเวอร์ชัน v2.x

# OpenSearch ต้องการค่านี้ ไม่งั้น container จะ crash ตอนสตาร์ท
echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-opensearch.conf
sudo sysctl --system
sysctl vm.max_map_count            # ต้องได้ 262144
```

---

## 4. นำไฟล์ขึ้น server

จากเครื่องของคุณ:

```bash
scp central-monitor-production.zip <user>@<server>:/tmp/
```

บน server:

```bash
sudo apt install -y unzip
sudo unzip /tmp/central-monitor-production.zip -d /opt
sudo chown -R $USER:$USER /opt/central-monitor
cd /opt/central-monitor
```

> **หมายเหตุ:** ในแพ็กเกจนี้ **ไม่มี** `.env`, private key, `known_hosts` และข้อมูลเก่า (`data/`) โดยตั้งใจ — ต้องสร้างใหม่บน server ตามข้อ 4–7 เพื่อไม่ให้รหัสผ่าน/คีย์ของ lab หลุดมาใช้ใน production และไม่ให้ checkpoint ของ lab ทำให้ puller ข้ามข้อมูลของเครื่องจริง

> **ทางลัด:** ข้อ 3–4.1, 7 (ส่วน key) และ 8.3–8.4 ทำอัตโนมัติได้ด้วย `bash install.sh` ดู [CONFIG-TH.md](CONFIG-TH.md)

### 4.1 สร้างไฟล์ `.env`

```bash
./scripts/init-env.sh
```

สคริปต์ไม่ถามอะไร: ตั้ง login เว็บ UI เป็น **admin / admin** (ค่าเริ่มต้น เปลี่ยนทีหลังได้ ดู CONFIG-TH.md ข้อ 2), สุ่ม secret และรหัส OpenSearch ให้ ไฟล์ `.env` จะมีสิทธิ์ `600`  อยากตั้งเองตั้งแต่แรก: `UI_USER=xxx UI_PASS=yyy ./scripts/init-env.sh`

ตรวจสอบค่าที่ต้องรู้:

| ตัวแปร | ความหมาย |
|---|---|
| `CENTRAL_AUTH_USERNAME` / `CENTRAL_AUTH_PASSWORD` | login เข้าเว็บ UI |
| `CENTRAL_SECRET_KEY` | ใช้เซ็น session cookie (ห้ามซ้ำกับรหัสผ่านอื่น) |
| `OPENSEARCH_ADMIN_PASSWORD` และ `OPENSEARCH_INITIAL_ADMIN_PASSWORD` | ต้องเหมือนกัน ถูกกำหนดครั้งแรกที่สร้าง volume ของ OpenSearch |
| `COMPOSE_PROFILES` | เลือกโหมด Prometheus (ข้อ 5) |
| `PROMETHEUS_URL`, `CENTRAL_NODE_JOB`, `CENTRAL_NODE_INSTANCE` | ใช้เฉพาะโหมด external (ข้อ 5.2) |
| `CENTRAL_COOKIE_SECURE` | เปลี่ยนเป็น `true` เมื่อเข้าผ่าน HTTPS (ข้อ 9) |

ถ้า `CENTRAL_SECRET_KEY` หรือรหัส OpenSearch ไม่ได้ตั้ง `central-api` จะไม่ยอมสตาร์ทและบอกชื่อตัวแปรที่ขาดใน log ส่วน `CENTRAL_AUTH_*` ถ้าไม่ตั้งจะใช้ `admin`/`admin`

---

## 5. เลือกโหมดการเชื่อมต่อ Prometheus

### 5.1 โหมด A — ใช้ Prometheus ที่มากับ stack (bundled) *(ค่าเริ่มต้น)*

เหมาะกับ server ที่ยังไม่มี Prometheus หรือต้องการแยกอิสระ

`.env` ไม่ต้องแก้อะไร (`COMPOSE_PROFILES=bundled-prometheus`) Prometheus จะสแกน

- เว็บเซิร์ฟเวอร์ตามที่ระบุใน `config/puller.yaml` (ไฟล์ `data/prometheus/web-nodes.json` ถูกสร้างอัตโนมัติโดย puller)
- node-exporter ของเครื่องกลางเอง และตัว puller (`central-puller:8000`)

> ถ้า server นี้มีอะไรใช้พอร์ต `9090` อยู่แล้ว (เช่น Prometheus ตัวจริง) พอร์ตจะชนกัน → ให้ใช้โหมด B แทน

### 5.2 โหมด B — เชื่อมกับ Prometheus ตัวที่มีอยู่แล้วบน server จริง (external)

**ขั้น 1 — แก้ `.env`**

```dotenv
COMPOSE_PROFILES=
PROMETHEUS_URL=http://host.docker.internal:9090
CENTRAL_NODE_JOB=<job ของเครื่องกลางใน Prometheus ตัวนั้น>
CENTRAL_NODE_INSTANCE=<instance ของเครื่องกลาง เช่น 10.0.10.5:9100>
```

`COMPOSE_PROFILES=` (ว่าง) = ไม่รัน Prometheus และ node-exporter ใน stack

**ขั้น 2 — ให้ container เข้าถึง Prometheus บน host ได้**

```bash
ss -ltnp | grep 9090
```

- ถ้าเห็น `127.0.0.1:9090` → container เข้าไม่ถึง ต้องให้ Prometheus listen บนที่ container เข้าได้ เช่น เพิ่ม flag `--web.listen-address=0.0.0.0:9090` แล้ว restart Prometheus
- เปิด firewall ให้เฉพาะเครือข่ายของ docker และปิดจากภายนอก:

```bash
sudo ufw allow from 172.16.0.0/12 to any port 9090 proto tcp
sudo ufw deny 9090/tcp        # ห้ามเปิดให้ภายนอก (ต้องเรียงกฎให้ allow อยู่ก่อน deny)
```

- ถ้า Prometheus อยู่คนละเครื่อง ให้ตั้ง `PROMETHEUS_URL=http://<ip>:9090`

> `central-api` เรียก Prometheus แบบ HTTP ธรรมดา **ไม่รองรับ** basic-auth/TLS ของ Prometheus จึงต้องควบคุมด้วย firewall/เครือข่ายภายใน

**ขั้น 3 — ให้ label ตรงกัน (สำคัญที่สุด)**

`central-api` ค้นหา metrics ด้วย `job="…"` และ `instance="…"` แบบตรงตัว โดยอ่านจาก `config/puller.yaml` ของแต่ละ target:

```yaml
prometheus:
  job: <ค่า label job ใน Prometheus>
  instance: <ค่า label instance ใน Prometheus>
```

มี 2 ทาง:

- **ทาง 1 (แนะนำ)** — Prometheus ตัวจริงสแกนเว็บเซิร์ฟเวอร์พวกนี้อยู่แล้ว: เปิด Prometheus UI แล้วรัน `up` ดูว่าแต่ละเครื่องมี `job` และ `instance` เป็นอะไร แล้วนำค่าเหล่านั้นมาใส่ใน `puller.yaml` **โดยไม่ต้องแก้ Prometheus เลย** (ไฟล์ `web-nodes.json` จะไม่ถูกใช้ในกรณีนี้)
- **ทาง 2** — ให้ Prometheus ตัวจริงสแกนเพิ่มโดยอ่านไฟล์ที่ puller สร้างให้ (ใช้ได้เมื่อ Prometheus รันบน host เดียวกันและอ่านไฟล์ได้):

```yaml
# ใน prometheus.yml ของ server จริง
scrape_configs:
  - job_name: central-monitor-web-nodes
    file_sd_configs:
      - files:
          - /opt/central-monitor/data/prometheus/web-nodes.json
```

  แล้ว `sudo systemctl reload prometheus` — label `job` ในไฟล์จะถูกใช้แทน `job_name`
  หรือถ้า Prometheus อยู่ที่อื่น ใช้ `static_configs` เอง โดย `job_name` ต้องเท่ากับ `prometheus.job` และ target ต้องเท่ากับ `prometheus.instance`

**ตรวจสอบ:** ใน Prometheus UI ต้องได้ผลลัพธ์จาก query นี้ทุกเครื่อง

```
up{job="<job>", instance="<instance>"}     → ค่า 1
node_memory_MemTotal_bytes{job="<job>", instance="<instance>"}
```

Metrics ที่ระบบใช้ทั้งหมดเป็นของ node_exporter มาตรฐาน: `node_cpu_seconds_total`, `node_memory_*`, `node_filesystem_*` (mountpoint `/`), `node_disk_*`, `node_network_*`

---


> **Ping / packet loss (โหมด external)**: container `blackbox` มากับโหมด bundled เท่านั้น
> ถ้าใช้ Prometheus ภายนอก ให้รัน blackbox_exporter (ตั้งค่าตาม `metrics/blackbox/blackbox.yml`) แล้วเพิ่ม job `ping`
> แบบเดียวกับใน `metrics/prometheus/prometheus.yml` โดยชี้ `file_sd_configs` ไปที่ `data/prometheus/ping.json` และ `replacement` เป็นที่อยู่ของ blackbox_exporter
> ไม่เพิ่ม = หน้าเว็บแสดง ping เป็น Unavailable (ส่วนอื่นทำงานปกติ)

## 6. เตรียมเว็บเซิร์ฟเวอร์ปลายทาง

ทำบน **เว็บเซิร์ฟเวอร์แต่ละเครื่อง**

### 6.1 node_exporter

```bash
sudo apt install -y prometheus-node-exporter
sudo systemctl enable --now prometheus-node-exporter
# อนุญาตเฉพาะเครื่องที่ scrape (Prometheus) เท่านั้น
sudo ufw allow from <IP_ของ_Prometheus_หรือ_server_กลาง> to any port 9100 proto tcp
```

### 6.2 สร้าง user สำหรับ puller (สิทธิ์อ่านอย่างเดียว)

```bash
sudo adduser --disabled-password --gecos "" central-pull
sudo usermod -aG adm central-pull              # อ่าน log ใน /var/log (Ubuntu)
sudo usermod -aG systemd-journal central-pull  # ให้ journalctl ใช้ได้ (ถ้าไม่ใส่ จะไม่เห็นข้อความ warning)
# ตรวจว่าอ่าน log ของแอปได้ (ปรับ path ตามจริง)
sudo -u central-pull tail -n 1 /var/log/myapp/app.log
```

puller รันคำสั่งเหล่านี้บนเครื่องปลายทาง: `stat`, `tail -c`, `cat /proc/{loadavg,meminfo,uptime}`, `df -P /`, `df -Pi /`, `ps`, `systemctl list-units`, `journalctl`, `cat /sys/class/net/*/statistics/*` — ไม่ต้องใช้ sudo/root

---

## 7. สร้าง SSH key และ known_hosts (บน server กลาง)

```bash
cd /opt/central-monitor

# 1) สร้าง key ใหม่สำหรับ production (ไม่ใช้ key ของ lab)
ssh-keygen -t ed25519 -N "" -C "central-monitor-production" -f keys/central_ed25519
chmod 600 keys/central_ed25519

# 2) ต้องสร้างไฟล์ known_hosts ก่อนสั่ง docker compose
#    ไม่งั้น Docker จะสร้างเป็น "โฟลเดอร์" แทนไฟล์ แล้วระบบพัง
touch keys/known_hosts

cat keys/central_ed25519.pub     # ← คัดลอกไปใส่ในเครื่องปลายทาง
```

บนเว็บเซิร์ฟเวอร์แต่ละเครื่อง วาง public key (แนะนำให้จำกัดเฉพาะ IP ของ server กลาง):

```bash
sudo -u central-pull mkdir -p /home/central-pull/.ssh
echo 'from="<IP_server_กลาง>" ssh-ed25519 AAAA... central-monitor-production' | \
  sudo -u central-pull tee -a /home/central-pull/.ssh/authorized_keys
sudo chmod 700 /home/central-pull/.ssh
sudo chmod 600 /home/central-pull/.ssh/authorized_keys
```

กลับมาที่ server กลาง เก็บ host key ของแต่ละเครื่อง (puller ใช้ `StrictHostKeyChecking=yes` จึงต้องมีก่อน):

```bash
# ชื่อที่ใส่ต้องเหมือนกับ `host:` ใน puller.yaml ทุกตัวอักษร (IP หรือ DNS)
ssh-keyscan -t ed25519 -p 22 10.0.10.11 >> keys/known_hosts

# ตรวจลายนิ้วมือให้ตรงกับที่ปลายทางจริง (ป้องกัน man-in-the-middle)
ssh-keygen -lf keys/known_hosts
#   บนเครื่องปลายทาง:  ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

ทดสอบ SSH จาก server กลาง (ควรได้ชื่อเครื่องกลับมา โดยไม่ถามอะไร):

```bash
ssh -i keys/central_ed25519 -o UserKnownHostsFile=keys/known_hosts \
    -o StrictHostKeyChecking=yes -o BatchMode=yes \
    central-pull@10.0.10.11 hostname
```

---

## 8. ตั้งค่า target และสตาร์ทระบบ

### 8.1 แก้ `config/puller.yaml`

ค่าเริ่มต้นคือ `targets: []` (ยังไม่มีเครื่องไหน) ให้เปิดไฟล์ตามตัวอย่างในไฟล์ แล้วเพิ่มเครื่องจริง:

```yaml
targets:

  - name: web-01                 # ชื่อที่แสดงใน UI
    host: 10.0.10.11             # ต้องตรงกับที่ใส่ใน known_hosts
    prometheus:
      job: web-01-node           # ต้องตรงกับ label ใน Prometheus (ข้อ 5)
      instance: 10.0.10.11:9100
    ssh:
      port: 22
      user: central-pull
    logs:
      - name: logapp                 # ชื่อนี้ otel จึงจะอ่าน (ดูหมายเหตุด้านล่าง)
        path: /var/log/myapp/logapp/*.log
        type: application
      - name: loginuser
        path: /var/log/myapp/loginuser/*.log
        type: login
```

**ข้อควรรู้เรื่องชื่อ source (`logs[].name`):** puller ตั้งชื่อไฟล์ใน `data/buffer` เป็น `<site>__<target>__<name>__…log` และ otel เลือกอ่านเฉพาะไฟล์ที่ชื่อมีคำว่า `logapp` หรือ `loginuser` (ดู `start_push_log/otel/otelcol.yaml`) ดังนั้น

- source ที่ตั้ง `name: logapp` / `name: loginuser` → ถูกส่งเข้า OpenSearch พร้อม parser ของมัน
- source ที่ตั้ง `name: application` → ส่งเข้า OpenSearch เป็นบรรทัดธรรมดา (ไม่ parse) ผ่าน receiver `filelog/application`
- source ชื่ออื่น → ถูกดึงมาไว้ใน buffer แต่ **ยังไม่ถูกส่งเข้า OpenSearch** จนกว่าจะเพิ่ม receiver ใน `otelcol.yaml`
- ฟิลด์ `type` ในไฟล์ config ไม่ถูกโค้ดใดนำไปใช้ (ใส่ไว้เป็นข้อมูลอธิบาย)

### 8.2 เตรียมโฟลเดอร์ข้อมูล

ไม่ต้องทำมือแล้ว — service `buffer-init` ใน compose จะสร้างและ chown `data/buffer/.otel-storage` ให้ uid 10001 (otel) ก่อน otel สตาร์ทเอง

ก่อนสตาร์ท ให้รันตัวเช็คความพร้อม (ตรวจ `.env`, key, known_hosts, `vm.max_map_count`, host ตรงกับ `puller.yaml`):

```bash
./scripts/preflight.sh          # เช็คแบบไม่ใช้เครือข่าย
./scripts/preflight.sh --ssh    # ลอง SSH เข้าทุกเครื่อง + เช็คว่าอ่าน path log ได้
```

ต้องไม่มีบรรทัด `[FAIL]` ก่อนไปข้อ 8.3

### 8.3 สตาร์ท

```bash
cd /opt/central-monitor/docker
docker compose config -q && echo "compose OK"    # ตรวจ syntax + ตัวแปรใน .env
docker compose up -d --build
docker compose ps
```

ทุก service ควรเป็น `running` (`central-puller` และ `central-api` มี healthcheck ต้องขึ้น `healthy` ภายใน ~1 นาที)

### 8.4 ตั้งค่า ISM policy ลบ log อัตโนมัติ (ทำครั้งเดียว)

OpenSearch ไม่เปิดพอร์ตออก host จึงสั่งผ่าน container `central-api` (มี curl และมีรหัสผ่านใน env อยู่แล้ว) รอให้ OpenSearch พร้อมประมาณ 1–2 นาทีหลังสตาร์ท:

```bash
docker compose exec -T central-api sh -c \
  'curl -sk -u "admin:${OPENSEARCH_ADMIN_PASSWORD}" \
     -X PUT https://opensearch:9200/_plugins/_ism/policies/central-log-retention \
     -H "Content-Type: application/json" -d @-' \
  < ../start_push_log/opensearch/central-log-retention-ism-policy.json
```

ควรได้ JSON ที่มี `"_id":"central-log-retention"` กลับมา policy จะผูกกับทุก index ที่ชื่อขึ้นต้น `logs-central-` ที่สร้างหลังจากนี้อัตโนมัติ

> **หมายเหตุเรื่อง retention:** policy นี้ลบ index หลังสร้างครบ 90 นาที และ index เป็นรายชั่วโมง ดังนั้น log ใน OpenSearch จะอยู่ประมาณ 30–90 นาทีเท่านั้น ถ้าต้องการเก็บนานกว่านี้ให้แก้ `min_index_age` ในไฟล์ policy แล้วสั่ง PUT ใหม่ (ต้องใส่ `?if_seq_no=…&if_primary_term=…` ตามที่ OpenSearch ร้องขอเมื่ออัปเดต)

### 8.5 เปิดให้ระบบเริ่มเองหลัง reboot

```bash
sudo systemctl enable docker
```

ทุก container ตั้ง `restart: unless-stopped` ไว้แล้ว จึงกลับมาเองหลังรีบูต ไม่ต้องใช้ไฟล์ใน `docker/systemd/` (เป็นทางเลือกสำหรับกรณีรัน puller แบบ native เท่านั้น ห้ามใช้ซ้อนกับ container)

---

## 9. เปิดใช้งานผ่านเว็บอย่างปลอดภัย

`central-api` ฟังที่ `127.0.0.1:8090` เท่านั้น ให้วาง nginx + HTTPS ไว้ด้านหน้า:

```nginx
# /etc/nginx/conf.d/central-monitor.conf
limit_req_zone $binary_remote_addr zone=cm_login:10m rate=5r/m;

server {
    listen 443 ssl;
    server_name monitor.your-domain.example;

    ssl_certificate     /etc/ssl/certs/monitor.crt;
    ssl_certificate_key /etc/ssl/private/monitor.key;

    # จำกัดการเดารหัสผ่าน (ระบบไม่มี lockout ในตัว)
    location = /api/login {
        limit_req zone=cm_login burst=5 nodelay;
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

เมื่อใช้ HTTPS แล้ว แก้ `.env` เป็น `CENTRAL_COOKIE_SECURE=true` แล้ว

```bash
docker compose up -d --force-recreate central-api
```

---

## 10. ตรวจสอบหลังติดตั้ง (Checklist)

```bash
cd /opt/central-monitor/docker

# 1) ทุก container ทำงาน
docker compose ps

# 2) API ตอบ
curl -s http://127.0.0.1:8090/health            # {"status":"ok"}

# 3) puller ดึงข้อมูลสำเร็จ (ดู log แล้วหา [PULL] cycle completed)
docker compose logs --tail=60 central-puller
docker compose exec -T central-api curl -s central-puller:8000/metrics | grep -E '^ssh_pull_success'
#   ssh_pull_success{target="web-01"} 1.0   ← ต้องเป็น 1

# 4) มีไฟล์ target สำหรับ Prometheus (โหมด bundled)
cat ../data/prometheus/web-nodes.json
curl -s 'http://127.0.0.1:9090/api/v1/query?query=up' | head -c 600

# 5) log เข้า OpenSearch แล้ว
docker compose exec -T central-api sh -c \
  'curl -sk -u "admin:${OPENSEARCH_ADMIN_PASSWORD}" "https://opensearch:9200/_cat/indices/logs-central-*?v"'

# 6) ISM policy ผูกกับ index แล้ว
docker compose exec -T central-api sh -c \
  'curl -sk -u "admin:${OPENSEARCH_ADMIN_PASSWORD}" "https://opensearch:9200/_plugins/_ism/explain/logs-central-*"' | head -c 600
```

จากนั้นเปิดหน้าเว็บ → login → เลือกเครื่อง ควรเห็น CPU/Memory/Disk และรายการ problems

> puller ดึง log ทุก `scheduler.log_interval` (ปริยาย 2 นาที) และเก็บสถานะเครื่องทุก `scheduler.interval` (ปริยาย 1 ชม.) ทำพร้อมกัน `scheduler.parallel` เครื่อง (ปริยาย 4) รอบแรกเริ่มทันทีที่สตาร์ท (`target_stagger` รุ่นเก่าไม่ใช้แล้ว)

---

## 11. งานประจำ

**เพิ่ม / ลบ / แก้ไขเว็บเซิร์ฟเวอร์**

1. ทำข้อ 6 และ 7 กับเครื่องใหม่ (node_exporter, user, key, `ssh-keyscan`)
2. แก้ `config/puller.yaml`
3. `docker compose restart central-puller` (จะสร้าง `web-nodes.json` ใหม่และเริ่มรอบดึงทันที)

**ดู log ของระบบ**

```bash
docker compose logs -f --tail=100 central-puller
docker compose logs -f --tail=100 central-api
docker compose logs -f --tail=100 otel
```

**เปลี่ยนรหัสผ่านเว็บ UI** — แก้ `CENTRAL_AUTH_*` ใน `.env` แล้ว `docker compose up -d --force-recreate central-api`

**เปลี่ยนรหัสผ่าน OpenSearch** — แก้ใน `.env` อย่างเดียวไม่พอ เพราะรหัสผ่านถูกเก็บใน volume ต้องเปลี่ยนผ่าน OpenSearch security API หรือถ้ายอมเสียข้อมูล log: `docker compose down && docker volume rm central-monitor_opensearch-data` แล้วสร้างใหม่ (ทำ ISM policy ข้อ 8.4 ซ้ำ)

**อัปเดตโค้ด** — พิมพ์ `cm-update` จากที่ไหนก็ได้ (ไม่ต้อง cd) ระบบจะ git pull, คืน `config/puller.yaml` ของเครื่อง, แล้ว `install.sh --keep` ให้เอง
(`cm-update --check` = ดูว่ามีอะไรใหม่, `cm-update --rebuild` = build ใหม่แม้ไม่มีโค้ดใหม่, `cm-update --where` = บอกว่าติดตั้งอยู่ที่ไหน, ค่าเดิมสำรองไว้ที่ `data/state/update-backup/`)

เครื่องที่ยังไม่มี `cm-update` (ติดตั้งจาก zip / ไม่รู้ path) — คำสั่งเดียว รันที่ไหนก็ได้:

```bash
curl -fsSL https://raw.githubusercontent.com/Dionydar2612/central-monitor/main/scripts/update.sh | bash
```

สคริปต์หาโฟลเดอร์จาก container `central-api` ที่รันอยู่ แปลงเป็น git clone ในที่เดิม (แทนเฉพาะไฟล์โค้ด
คง `config/puller.yaml`, `docker/.env`, `keys/`, `data/` และสำรอง puller.yaml/.env ไว้ที่ `data/state/update-backup/`)
แล้ว `install.sh --keep` ให้ ครั้งต่อไปใช้ `cm-update` ได้เลย  ถ้า container ไม่ได้รันอยู่ ระบุโฟลเดอร์เอง:
`... | bash -s -- --dir /path/to/central-monitor`

**สำรองข้อมูล** — ที่ต้องเก็บจริง ๆ คือ `docker/.env`, `keys/`, `config/` และ `data/state/checkpoints.json` (ตำแหน่งที่อ่าน log ถึงแล้ว) ส่วน log ใน OpenSearch เป็นข้อมูลชั่วคราวตามนโยบาย retention

**ปรับ retention ของ buffer** — แก้ `storage.buffer_retention` (และความถี่ `storage.cleanup_interval`) ใน `config/puller.yaml` ที่เดียว `buffer-cleaner` อ่านค่าจากไฟล์นี้เองทุกรอบ ไม่ต้อง restart และไม่ต้องแก้ docker-compose (ค่าเริ่มต้น 2 ชั่วโมง)

**ปรับอายุประวัติสถานะเครื่อง** — แก้ `storage.system_history_retention` (เริ่มต้น 7d) ใน `puller.yaml` puller จะลบประวัติใน `data/state/system/history/` ที่เก่ากว่านั้นเองทุกรอบ

---

## 12. แก้ปัญหาที่พบบ่อย

| อาการ | สาเหตุ / วิธีแก้ |
|---|---|
| `central-opensearch` restart วน / exit code 78 | ยังไม่ได้ตั้ง `vm.max_map_count` (ข้อ 3) |
| `central-api` restart วน, log ว่า `Missing required environment variable` | `.env` ขาดค่า หรือรัน `docker compose` ผิดโฟลเดอร์ |
| Docker สร้าง `keys/known_hosts` เป็นโฟลเดอร์ | ไม่ได้ `touch` ไฟล์ก่อน → `docker compose down; rm -rf ../keys/known_hosts; touch ../keys/known_hosts; docker compose up -d` |
| puller log: `Host key verification failed` | ยังไม่มี host ใน `known_hosts` หรือชื่อไม่ตรงกับ `host:` ใน puller.yaml |
| puller log: `Permission denied (publickey)` | public key ยังไม่อยู่ใน `authorized_keys` ของเครื่องปลายทาง หรือ `from="…"` ไม่ตรง IP ของ server กลาง |
| `ssh_pull_success = 0` แต่ SSH ทดสอบมือผ่าน | ทดสอบจากใน container: `docker compose exec central-puller ssh -i /run/keys/central_ed25519 -o UserKnownHostsFile=/run/keys/known_hosts central-pull@<host> hostname` (ปัญหา DNS ใน container) |
| UI เข้าได้แต่ CPU/Memory เป็น `-` | `job`/`instance` ใน `puller.yaml` ไม่ตรงกับ label ใน Prometheus (ข้อ 5.2 ขั้น 3) หรือ node_exporter ถูกบล็อกที่ firewall |
| โหมด external: metrics ว่างทั้งหมด | ทดสอบ `docker compose exec -T central-api curl -s $PROMETHEUS_URL/api/v1/query?query=up`; ถ้าไม่ตอบ ดูข้อ 5.2 ขั้น 2 |
| `port is already allocated` ที่ 9090 | มี Prometheus บน host อยู่แล้ว → ใช้โหมด B |
| otel log: `permission denied` ที่ `/data/buffer/.otel-storage` | `docker compose up -d buffer-init` แล้ว `docker compose restart otel` (หรือ `sudo chown -R 10001:10001 /opt/central-monitor/data/buffer/.otel-storage`) |
| ไม่มี index `logs-central-*` | ตรวจ `docker compose logs otel` และดูว่ามีไฟล์ใน `data/buffer/` หรือไม่ (ถูกลบทุก 30 นาที) |
| login แล้วเด้งกลับหน้า login ทันที | เปิด `CENTRAL_COOKIE_SECURE=true` แต่เข้าเว็บผ่าน `http://` → เข้าผ่าน HTTPS หรือตั้งเป็น `false` |

---

## 13. Checklist ก่อนเปิดใช้งานจริง

- [ ] รหัสผ่านเว็บ UI เปลี่ยนจาก `admin` แล้ว (ถ้าเปิดใช้งานนอกเครื่อง/เครือข่ายที่ไม่ไว้ใจ)
- [ ] `.env` สิทธิ์ `600` และไม่ถูกนำเข้า git / ไม่ถูกส่งต่อทางแชต
- [ ] ใช้ SSH key ใหม่ของ production (ไม่มีไฟล์ `*.lab`)
- [ ] `known_hosts` ตรวจลายนิ้วมือแล้ว
- [ ] user `central-pull` บนเครื่องปลายทางมีสิทธิ์อ่านอย่างเดียว และจำกัด `from=` แล้ว
- [ ] พอร์ต `9100` (และ `9090` ถ้าเป็นโหมด external) เปิดเฉพาะแหล่งที่จำเป็น
- [ ] เว็บ UI เข้าผ่าน HTTPS และตั้ง `CENTRAL_COOKIE_SECURE=true`
- [ ] ใช้ ISM policy แล้ว และตั้ง `OPENSEARCH_LOG_RETENTION` ใน docker/.env ตรงกับที่ต้องการแล้ว (ปริยาย 7d)
- [ ] ทดสอบ `docker compose down && up -d` และ reboot server แล้วระบบกลับมาปกติ

---

## ภาคผนวก A — สิ่งที่เคลียร์/แก้จากไฟล์ `central-monitor-arc1.zip`

| รายการที่พบ | การจัดการ |
|---|---|
| รหัสผ่านเว็บ UI เป็นค่าเริ่มต้น `admin/admin` และ `auth.py` มี default `admin/admin` ถ้าไม่ตั้งค่า | ลบ default ออก ต้องตั้งใน `.env` ไม่งั้นแอปไม่สตาร์ท, เทียบรหัสผ่านแบบ constant-time |
| `dashboards` ใช้ `admin/admin` ฮาร์ดโค้ดใน compose | ใช้ `${OPENSEARCH_ADMIN_PASSWORD}` |
| `api.py` ชี้ `https://node-0.example.com:9200` (ชื่อปลอมผ่าน alias ใน compose) | เปลี่ยนเป็น `OPENSEARCH_URL` (ค่าเริ่มต้น `https://opensearch:9200`) ลบ alias |
| `otelcol.yaml` ชี้ `node-0.example.com` | เปลี่ยนเป็น `opensearch` |
| `api.py` ฮาร์ดโค้ด `http://prometheus:9090` และ `node-exporter:9100` ของเครื่อง central | เป็น `PROMETHEUS_URL`, `CENTRAL_NODE_JOB`, `CENTRAL_NODE_INSTANCE` (รองรับ Prometheus ภายนอก) |
| `docker-compose.yml` อ้างเครือข่ายภายนอก `log-generator_default` (ของ lab) — บน server จริงจะ `up` ไม่ผ่าน | ลบออกจาก `central-puller`, `prometheus` และส่วน `networks` |
| `puller.yaml` ชี้ host ของ lab (`log-web`, `log-web-02`, `log-node-exporter*`, user `webuser`) | เหลือ `targets: []` พร้อมตัวอย่างในคอมเมนต์ |
| `known_hosts` เป็นของเครื่อง lab | ไม่รวมมาในแพ็กเกจ ต้องสร้างใหม่ (ข้อ 7) |
| มี private key ของ lab (`central_ed25519.lab`) และ key ปัจจุบันอยู่ใน zip | ไม่รวมมาในแพ็กเกจ ให้สร้าง key ใหม่บน server |
| `.env` จริงอยู่ใน zip | ไม่รวมมา ใช้ `.env.example` + `scripts/init-env.sh` |
| โฟลเดอร์ `backup-before-production/` มีรหัสผ่าน lab เดิม (`Admin123!LabPassword`) และ secret key แบบฮาร์ดโค้ด | ลบออกจากแพ็กเกจ |
| `data/` มีข้อมูลของ lab: `checkpoints.json` (offset ของ log lab — ถ้าชื่อ/path ตรงกับเครื่องจริงจะทำให้ข้ามข้อมูลช่วงต้น), snapshot ระบบ, `web-nodes.json` | เคลียร์ทั้งหมด เหลือโครงโฟลเดอร์เปล่า |
| `prometheus.yml` job `docker` ชี้ `host.docker.internal:9323` แต่ compose ไม่มี `extra_hosts` แล้ว (Linux resolve ไม่ได้ → target DOWN ตลอด) และต้องเปิด metrics ใน Docker daemon | ปิดเป็นคอมเมนต์พร้อมวิธีเปิดใช้ / คืน `extra_hosts` ให้ใช้ได้ |
| Prometheus ไม่ได้สแกน puller (`:8000`) ทั้งที่มี metrics `ssh_pull_*` | เพิ่ม job `central-puller` |
| `cadvisor` รันแบบ `privileged` เมาต์ `/var/lib/docker` แต่ไม่มีอะไรใช้ metrics ของมัน | ย้ายเป็น profile `cadvisor` (ปิดเป็นค่าเริ่มต้น) |
| `prometheus` / `node-exporter` บังคับรันเสมอ | ย้ายเป็น profile `bundled-prometheus` เพื่อสลับไปใช้ Prometheus ภายนอกได้ |
| `api.py`: ค่า current metrics ของ disk/network ไม่มี `sum()` (ส่วน history มี) — เครื่องจริงที่มีหลาย NIC/ดิสก์จะได้เลขของ interface แรกเท่านั้น | เพิ่ม `sum()` ให้ตรงกับ endpoint history |
| `api.py`: secret key fallback `CHANGE-ME`, cookie secure ปิดตายตัว | ต้องตั้ง `CENTRAL_SECRET_KEY`; เปิด/ปิด secure cookie ด้วย `CENTRAL_COOKIE_SECURE` |
| `prometheus_targets.py` default environment `lab` | เปลี่ยนเป็น `production` |
| ไฟล์ค้าง: `*.bak`, `api.py.bak`, `docker-compose.yml.bak`, `prometheus.yml.bak-*`, `real-dashboard.js.bak-*`, `index.html.bak-final-layout`, `prometheus/central-targets.json` (ไม่มีอะไรอ้างถึง), `scripts/cleanup-buffer.sh` (ระบุ deprecated), `data/requests/` (ไม่มีโค้ดใช้) | ลบทิ้ง |
| `.gitignore` ไม่ครอบคลุมคีย์ `*.lab` และไฟล์ `.bak` | เขียนใหม่ (`keys/*`, `data/**`, `*.bak*`) |
| เพิ่ม healthcheck ให้ `central-api` | ใช้ `/health` |

## ภาคผนวก B — ข้อควรรู้ที่ยังไม่ได้แก้ (ควรตัดสินใจเอง)

1. **การตรวจ TLS ระหว่างบริการภายใน** — `api.py` และ `otelcol.yaml` ปิดการ verify ใบรับรองของ OpenSearch (ใช้ self-signed ในเครือข่ายภายใน docker ที่ไม่เปิดพอร์ตออก) ยอมรับได้ในโครงสร้างนี้ แต่ถ้าย้าย OpenSearch ไปเครื่องอื่นต้องใส่ CA จริง
2. **node-exporter ใน stack (โหมด bundled)** รันใน bridge network จึงเห็น *network ของ container* ไม่ใช่ของ host ถ้าต้องการกราฟ network ของเครื่องกลางที่ถูกต้อง ให้ติดตั้ง `prometheus-node-exporter` บน host แล้วใช้โหมด B หรือแก้ job `central` ให้ชี้ `host.docker.internal:9100`
3. ~~`system_history_retention`~~ **แก้แล้ว** — puller เขียน/ลบประวัติสถานะเครื่องตามค่านี้ (ดู `start_push_log/puller/system_info.py`)
4. **หน้า login ไม่มี rate limit ในตัว** — ใช้ `limit_req` ของ nginx ตามข้อ 9
5. ~~นโยบายเก็บ log ~30–90 นาที~~ **แก้แล้ว** — อายุ log ตั้งที่ `OPENSEARCH_LOG_RETENTION` (ปริยาย 7d) และ `install.sh` อัปเดต policy ให้เมื่อรันซ้ำ
6. **source ชื่ออื่นนอกจาก `logapp` / `loginuser` / `application`** ยังไม่ถูกส่งเข้า OpenSearch — ดูข้อ 8.1
7. ~~`buffer-cleaner` ฮาร์ดโค้ด `-mmin +30`~~ **แก้แล้ว** — ตอนนี้อ่าน `buffer_retention` จาก `puller.yaml` ที่เดียว
8. **การนับ/ค้น log กรองตามเครื่องฝั่ง Python** — ดึง log ล่าสุด 5,000 รายการของทุกเครื่องมากรอง ถ้ามีหลายเครื่องและเครื่องหนึ่ง log ถี่มาก เครื่องที่ log น้อยอาจเห็นรายการน้อยกว่าจริง (รายละเอียดใน `web_dashboard/backend/logs_service.py`)

## ภาคผนวก C — สิ่งที่แก้จากการตรวจรอบล่าสุด (central-monitor-clean.zip)

> ประวัติการแก้ไขในอดีต: ชื่อไฟล์ในตารางนี้ (`api.py`, `puller.py`, ...) เป็นชื่อก่อนจัดโครงสร้างใหม่ ปัจจุบันไฟล์เหล่านั้นอยู่ใน `start_push_log/` และ `web_dashboard/backend/` ดูตารางเทียบใน `docs/README-TH.md`

| ปัญหา | ผลถ้าไม่แก้ | การแก้ |
|---|---|---|
| `docker-compose.yml` mount ไฟล์ `keys/central_ed25519` ไปที่ `/root/.ssh` (ซึ่งเป็นโฟลเดอร์) และไม่มี `known_hosts` เข้า container | `central-puller` สตาร์ทไม่ขึ้น หรือ SSH ใช้ key/known_hosts ไม่ได้ (ไม่ตรงกับคู่มือข้อ 7) | mount `./keys` → `/run/keys:ro` และตั้ง `CENTRAL_SSH_KEY` / `CENTRAL_SSH_KNOWN_HOSTS` |
| `otelcol.yaml` `line_start_pattern` มี backslash ซ้อน (`\\d`) | regex ไม่มีวัน match → ทั้งไฟล์ logapp กลายเป็น record ก้อนเดียว | แก้เป็น `\d` |
| otel ต้อง chown `data/buffer` ด้วยมือ | ลืมแล้ว otel crash (`permission denied`) | เพิ่ม service `buffer-init` |
| source `application` ไม่มี receiver | log ถูกดึงมาแล้วถูกลบทิ้งใน 30 นาที ไม่เคยเข้า OpenSearch | เพิ่ม `filelog/application` |
| glob `*logapp*` / `*loginuser*` กว้างเกินไป | ชื่อเครื่องที่มีคำเหล่านี้จะถูก parse ผิด receiver | เปลี่ยนเป็น `*__logapp__*` / `*__loginuser__*` |
| `api.py` `/api/problems` ไม่กรองตามเครื่อง | เลือก web-02 แต่เห็น log ของทุกเครื่อง (ติดป้ายเป็น web-02) | กรองด้วยชื่อ target และใช้การจับคู่ `__<web>__` ทุกจุด |
| `puller.py` `stat` กับ `tail` แยกกัน + ตัด chunk ที่ byte | log ที่กำลังโตทำให้ `partial pull` วน retry; บรรทัดที่เขียนไม่จบถูกแบ่งเป็น 2 record | `tail \| head -c` และตัดที่ newline สุดท้าย |
| ไม่มีตัวเช็คก่อนติดตั้ง | ต้องรอ error ตอน `up` | เพิ่ม `scripts/preflight.sh` |
