# คู่มือติดตั้งและตั้งค่า central-monitor (ฉบับย่อ)

> อ่านเร็ว ๆ แล้วทำตามได้เลย รายละเอียดเชิงลึกอยู่ใน [INSTALL-TH.md](INSTALL-TH.md) และ [USAGE-TH.md](USAGE-TH.md)

---

## 1. ติดตั้งคำสั่งเดียว

บน Ubuntu/Debian server (ต้องมี sudo และต่ออินเทอร์เน็ตได้):

```bash
unzip central-monitor-V5.zip -d /opt && cd /opt/central-monitor && bash install.sh
```

`install.sh` จะทำให้ทั้งหมดนี้ (รันซ้ำได้ ไม่ทับของเดิม)

| ขั้น | ทำอะไร |
|---|---|
| 1 | ติดตั้ง Docker ถ้ายังไม่มี |
| 2 | ตั้ง `vm.max_map_count=262144` แบบถาวร (OpenSearch ต้องใช้) |
| 3 | สร้าง `docker/.env` — login เว็บ **admin / admin**, สุ่มรหัส OpenSearch และ secret ให้ |
| 4 | สร้าง SSH key `keys/central_ed25519` + ไฟล์ `keys/known_hosts` |
| 5 | `docker compose up -d --build` |
| 6 | รอ OpenSearch พร้อม แล้วตั้ง ISM policy ลบ log อัตโนมัติ |

เสร็จแล้วเปิด **http://127.0.0.1:8090** → login `admin` / `admin`

> เปิดจากเครื่องอื่น: `ssh -L 8090:127.0.0.1:8090 user@server` แล้วเปิด http://localhost:8090
> (พอร์ตผูกกับ 127.0.0.1 ตั้งใจให้ไม่เปิดตรงสู่ภายนอก ถ้าจะเปิดจริงให้ใช้ nginx + HTTPS ดู INSTALL-TH.md ข้อ 9)

ถ้าอยากเตรียมอย่างเดียวยังไม่สตาร์ท: `bash install.sh --no-start`

---

## 2. เปลี่ยน username / password เข้าเว็บ

ค่าเริ่มต้นคือ `admin` / `admin` อยากเปลี่ยนค่อยมาทำตอนนี้:

```bash
cd /opt/central-monitor
nano docker/.env
```

แก้สองบรรทัดนี้

```env
CENTRAL_AUTH_USERNAME=admin
CENTRAL_AUTH_PASSWORD=admin
```

แล้วสั่งให้ API โหลดค่าใหม่

```bash
cd docker && docker compose up -d --force-recreate central-api
```

> ใช้ `restart` ไม่ได้ ต้องเป็น `up -d --force-recreate` เพราะ `.env` อ่านตอนสร้าง container
> ปล่อยค่าว่าง = กลับไปใช้ `admin`/`admin`
> ถ้าเข้าเว็บผ่านอินเทอร์เน็ต **ต้องเปลี่ยนรหัสผ่านก่อนเสมอ**

ตั้งตั้งแต่ติดตั้งเลย (ก่อนรัน install.sh):

```bash
UI_USER=somchai UI_PASS='รหัสผ่านใหม่' bash scripts/init-env.sh && bash install.sh
```

---

## 3. เพิ่มเครื่องที่จะดึง log

### 3.1 ผ่านหน้าเว็บ (แนะนำ)

เข้าเว็บ → กด **Settings** (มุมขวาบน) หรือไปที่ `/settings`

1. กด **+ Add target** กรอก ชื่อเครื่อง, Host (IP/DNS), SSH port/user และเพิ่ม log file ที่ต้องการ (ชื่อ `application` / `logapp` / `loginuser` + path)
2. ช่อง Prometheus job / node_exporter เว้นว่างได้ ระบบตั้งให้เอง (`<ชื่อ>-node`, `<host>:9100`)
3. กด **Scan host key** → ตรวจ fingerprint ให้ตรงกับเครื่องจริง (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` บนเครื่องปลายทาง) → ติ๊กยืนยัน
4. ในหน้าเดียวกันมีคำสั่งสำหรับเครื่องปลายทาง (สร้าง user + วาง public key + node_exporter) กด **Copy commands** แล้วไปรันบนเครื่องนั้น
5. กด **Save**

หลังบันทึก: ไม่ต้อง restart อะไร — puller ดึงเครื่องนั้นทันทีภายในไม่กี่วินาที และ Prometheus เห็นเครื่องใหม่เอง ปุ่ม **Edit** แก้ได้ทุกอย่างยกเว้นชื่อ (อยากเปลี่ยนชื่อ = ลบแล้วเพิ่มใหม่) ปุ่ม **Delete** ลบออกจาก `puller.yaml` (log ที่เก็บไว้แล้วไม่ถูกลบ)

ข้อควรรู้
- หน้านี้แก้ `config/puller.yaml` ให้ตรง ๆ โดยคงคอมเมนต์เดิมไว้ และสำรองเวอร์ชันก่อนหน้าเป็น `config/puller.yaml.bak` ทุกครั้งที่บันทึก
- ค่า path/user/host ถูกตรวจเข้ม (ห้ามช่องว่าง เครื่องหมายคำพูด `;` `$` ฯลฯ) เพราะถูกส่งต่อไปเป็นคำสั่ง SSH
- ส่วนอื่นของไฟล์ (`scheduler`, `storage`, `retry`) ยังแก้ด้วยมือตามข้อ 3.2
- ถ้าไม่ต้องการให้แก้ผ่านเว็บ: เปลี่ยน `../config:/app/config` ใน `docker/docker-compose.yml` (service `central-api`) เป็น `:ro`

### 3.2 แก้ไฟล์ด้วยมือ — `config/puller.yaml`

เปิดไฟล์ `config/puller.yaml` แล้วแก้ส่วน `targets:` ต่อเครื่อง 1 block:

```yaml
targets:
  - name: web-01                  # ชื่อใน UI (a-z 0-9 - เท่านั้น)
    host: 10.0.10.11              # IP/DNS ต้องตรงกับใน known_hosts ทุกตัวอักษร
    prometheus:
      job: web-01-node            # ตั้งชื่ออะไรก็ได้ (โหมด bundled)
      instance: 10.0.10.11:9100   # host:port ของ node_exporter
    ssh:
      port: 22
      user: central-pull          # user read-only บนเครื่องปลายทาง
    logs:
      - name: application         # application | logapp | loginuser (เลือก parser)
        path: /var/log/myapp/app.log
        type: application
      - name: logapp
        path: /var/log/myapp/logapp/*.log   # ใช้ wildcard ได้
        type: application
```

| ค่า | ความหมาย |
|---|---|
| `scheduler.interval` | ดึงทุกกี่เวลา (`30s` `5m` `1h` `1d`) |
| `storage.buffer_retention` | เก็บไฟล์พักบน server กลางนานเท่าไหร่ (ปริยาย 2h) |
| `targets[].aliases` | (ไม่บังคับ) hostname จริงของเครื่องที่ต่างจากชื่อ target เช่น `[30K3PH4]` ใช้จับคู่ log/metrics |
| `storage.system_history_retention` | เก็บสถานะเครื่องย้อนหลัง |
| `storage.collapse_repeats` | `{enabled: false, keep: 20}` ยุบ log ซ้ำตอนดึง: ต่อรอบดึง เก็บชนิดละ (fingerprint เดียวกัน) ไม่เกิน `keep` รายการ ที่เหลือยุบเป็นบรรทัดสรุป `... [ERROR] (collapsed) ซ้ำอีก N ครั้ง: ...` · **ปริยายปิด** เพราะบรรทัดดิบส่วนเกินจะไม่ถูกเก็บในระบบกลาง · จำนวนที่ยุบดูได้ใน System log · มีผลรอบดึงถัดไป ไม่ต้องรีสตาร์ต |

แก้เสร็จ: `cd docker && docker compose restart central-puller`

---

## 4. เตรียมเครื่องปลายทาง (ทำครั้งเดียวต่อเครื่อง)

> หน้า Settings สร้างคำสั่งชุดนี้ให้ตามค่าที่กรอก (พร้อม public key) ข้างล่างคือแบบทำมือ

**บนเครื่องปลายทาง**

```bash
# (ก) node_exporter สำหรับกราฟ CPU/RAM/Disk
# ตรวจสอบ Package/Service
systemctl list-units --type=service --all --no-pager
dpkg -l | grep prometheus-node-exporter
# ตรวจสอบ Service
sudo systemctl status prometheus-node-exporter --no-pager
# ตรวจสอบ Port 9100
sudo ss -lntp | grep ':9100'
# ถ้าไม่มี
sudo apt install -y prometheus-node-exporter
sudo systemctl enable --now prometheus-node-exporter
sudo ufw allow from <IP_server_กลาง> to any port 9100 proto tcp

# (ข) user อ่าน log อย่างเดียว + วาง public key
sudo adduser --disabled-password --gecos "" central-pull
sudo -u central-pull mkdir -p /home/central-pull/.ssh
echo 'from="<IP_server_กลาง>" <เนื้อ public key>' | sudo -u central-pull tee -a /home/central-pull/.ssh/authorized_keys
sudo chmod 700 /home/central-pull/.ssh && sudo chmod 600 /home/central-pull/.ssh/authorized_keys

# (ค) ให้ user อ่านไฟล์ log ได้ (ปรับ path ตามจริง)
sudo usermod -aG adm central-pull
```

**บน server กลาง** — เอา public key ไปวาง และเก็บ host key:

```bash
cd /opt/central-monitor
cat keys/central_ed25519.pub                                 # คัดลอกไปวางที่ (ข)
ssh-keyscan -t ed25519 -p 22 10.0.10.11 >> keys/known_hosts   # host ต้องตรงกับ puller.yaml
ssh-keygen -lf keys/known_hosts                              # เทียบลายนิ้วมือกับเครื่องจริง
```

---

## 5. ตรวจว่าใช้งานได้

```bash
./scripts/preflight.sh --ssh           # ต้องไม่มี [FAIL]
cd docker
docker compose ps                      # central-puller / central-api = healthy
docker compose logs --tail=50 central-puller   # หา "cycle completed"
```

เปิดเว็บ → login → เห็นเครื่องในรายการ และมี log เข้าภายในรอบ `scheduler.interval`

---

## 6. ตัวแปรใน `docker/.env`

| ตัวแปร | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `CENTRAL_AUTH_USERNAME` | `admin` | ชื่อผู้ใช้เข้าเว็บ |
| `CENTRAL_AUTH_PASSWORD` | `admin` | รหัสผ่านเข้าเว็บ |
| `CENTRAL_SECRET_KEY` | สุ่มตอนติดตั้ง | เซ็น session cookie (ห้ามแจก) |
| `CENTRAL_COOKIE_SECURE` | `false` | ตั้ง `true` เมื่อเข้าผ่าน HTTPS |
| `OPENSEARCH_ADMIN_PASSWORD` / `OPENSEARCH_INITIAL_ADMIN_PASSWORD` | สุ่มตอนติดตั้ง | ต้องเหมือนกัน ผูกกับ volume ตอนสร้างครั้งแรก **อย่าแก้เล่น** |
| `COMPOSE_PROFILES` | `bundled-prometheus` | ว่าง = ใช้ Prometheus ที่มีอยู่เอง |
| `PROMETHEUS_URL` ฯลฯ | bundled | ใช้เฉพาะโหมด external |

หลังแก้ `.env` ทุกครั้ง: `cd docker && docker compose up -d --force-recreate`

---

## 7. คำสั่งที่ใช้บ่อย

```bash
cd /opt/central-monitor/docker
docker compose ps                         # สถานะ
docker compose logs -f --tail=100 central-api
docker compose restart central-puller     # หลังแก้ puller.yaml
docker compose up -d --build central-api  # หลังแก้โค้ดเว็บ
docker compose down                       # หยุด (ข้อมูลยังอยู่)
```

## 8. ปัญหาที่เจอบ่อย

| อาการ | วิธีแก้ |
|---|---|
| OpenSearch ล้ม exit code 78 | `sudo sysctl -w vm.max_map_count=262144` แล้ว `docker compose up -d` |
| login ไม่ได้หลังแก้ `.env` | ต้อง `up -d --force-recreate central-api` ไม่ใช่ `restart` |
| ไม่มี log เข้า | `docker compose logs central-puller` — มักเป็น host ไม่อยู่ใน `known_hosts` หรือ user อ่าน log ไม่ได้ |
| Settings: บันทึกแล้วขึ้น 'permission denied' / เขียนไฟล์ไม่ได้ | เช็คว่า `config/` ไม่ได้ mount เป็น `:ro` ใน compose และสั่ง `docker compose up -d --build central-api` หลังอัปเกรด |
| `keys/known_hosts` เป็นโฟลเดอร์ | `rm -rf keys/known_hosts && touch keys/known_hosts` (install.sh แก้ให้อัตโนมัติ) |
