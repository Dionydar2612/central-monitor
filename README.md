# central-monitor

Central log + metrics monitoring for web servers (SSH pull -> otel -> OpenSearch, Prometheus metrics, web dashboard).

- **ติดตั้งคำสั่งเดียว:** `bash install.sh`  ·  login เริ่มต้น `admin` / `admin`  ·  คู่มือย่อ: [`docs/CONFIG-TH.md`](docs/CONFIG-TH.md)
- Start here: [`docs/README-TH.md`](docs/README-TH.md) — flow, folder map, "where do I edit X"
- Install: [`docs/INSTALL-TH.md`](docs/INSTALL-TH.md)  ·  Daily use / add machines: [`docs/USAGE-TH.md`](docs/USAGE-TH.md)

```
config/          puller.yaml (targets, log paths, schedule, retention)
start_push_log/  core pipeline: puller -> data/buffer -> otel -> OpenSearch (+ cleanup)
web_dashboard/   backend (Flask API) + frontend (HTML/CSS/JS) — หน้า /settings เพิ่ม/แก้/ลบเครื่องใน puller.yaml
metrics/         prometheus.yml (bundled mode)
docker/          docker-compose.yml, .env, dockerfiles/, systemd/
install.sh      one-command install
scripts/         init-env.sh, preflight.sh
data/ keys/      runtime data and SSH keys (git-ignored)
```
