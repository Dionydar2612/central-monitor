#!/usr/bin/env bash
# =====================================================================
# run-before-windows.sh - เปิดหน้าเว็บ "เวอร์ชันก่อนเพิ่ม Windows" (branch before-windows = commit 1bbd8ae)
#                         แยกพอร์ต ใช้ข้อมูลชุดเดียวกับระบบหลัก (OpenSearch / Prometheus / data / config)
# =====================================================================
# ใช้:
#   bash scripts/run-before-windows.sh            # เปิดที่พอร์ต 8091 (http://localhost:8091)
#   bash scripts/run-before-windows.sh 8092       # เลือกพอร์ตเอง
#   bash scripts/run-before-windows.sh stop       # ปิด
#
# ทำอะไร
#   1) git worktree ของ branch before-windows ไว้ที่ ~/central-monitor-before-windows (ไม่แตะโฟลเดอร์หลัก)
#   2) build image central-monitor-api:before-windows จากโค้ดชุดนั้น
#   3) รัน container central-api-before-windows ใน network เดียวกับระบบหลัก
#      - ใช้ docker/.env ของระบบหลัก (รหัสผ่าน / ผู้ใช้เดียวกัน)
#      - config/puller.yaml mount แบบอ่านอย่างเดียว -> หน้า Settings ของเวอร์ชันเก่าแก้เครื่องไม่ได้
#        (กันเวอร์ชันเก่าเขียนทับการตั้งค่าใหม่ เช่น เครื่อง Windows / เกณฑ์ / Teams)
#   ไม่มี puller / otel / OpenSearch / Prometheus ชุดที่สอง -> ไม่ดึงข้อมูลซ้ำ
#   เครื่อง Windows (agent) จะขึ้นเป็น Down / ไม่มีค่า เพราะเวอร์ชันนี้ยังไม่รู้จัก Windows
# =====================================================================
set -euo pipefail

MAIN="$(cd "$(dirname "$0")/.." && pwd)"          # โฟลเดอร์ระบบหลัก (ที่รัน docker compose อยู่)
WT="${CM_OLD_DIR:-$HOME/central-monitor-before-windows}"
BRANCH=before-windows
BASE=1bbd8ae
NAME=central-api-before-windows
IMAGE=central-monitor-api:before-windows
NET=central-monitor_central-net

if [ "${1:-}" = "stop" ]; then
  docker rm -f "$NAME" >/dev/null 2>&1 && echo "ปิด $NAME แล้ว" || echo "ไม่ได้เปิดอยู่"
  exit 0
fi
PORT="${1:-8091}"

cd "$MAIN"
[ -f docker/.env ] || { echo "ไม่พบ $MAIN/docker/.env (ต้องรันในโฟลเดอร์ที่ติดตั้งระบบหลักแล้ว)"; exit 1; }
docker network inspect "$NET" >/dev/null 2>&1 || { echo "ไม่พบ network $NET (ระบบหลักยังไม่ได้เริ่ม: cd docker && docker compose up -d)"; exit 1; }

# --- 1) branch + worktree ---
git fetch -q origin "$BRANCH" 2>/dev/null || true
if ! git rev-parse -q --verify "refs/heads/$BRANCH" >/dev/null; then
  if git rev-parse -q --verify "refs/remotes/origin/$BRANCH" >/dev/null; then
    git branch "$BRANCH" "origin/$BRANCH"
  else
    git branch "$BRANCH" "$BASE"
  fi
fi
if [ ! -e "$WT/.git" ]; then
  git worktree add "$WT" "$BRANCH"
else
  echo "ใช้ worktree เดิม: $WT ($(git -C "$WT" log -1 --format='%h %s'))"
fi

# --- 2) image ---
set -a; . docker/.env; set +a
HOST_UID="${HOST_UID:-$(id -u)}"; HOST_GID="${HOST_GID:-$(id -g)}"
docker build -q -f "$WT/docker/dockerfiles/web.Dockerfile" \
  --build-arg HOST_UID="$HOST_UID" --build-arg HOST_GID="$HOST_GID" -t "$IMAGE" "$WT" >/dev/null
echo "build $IMAGE แล้ว"

# --- 3) container ---
docker rm -f "$NAME" >/dev/null 2>&1 || true
TLS=()
[ -d "$MAIN/keys/tls" ] && TLS=(-v "$MAIN/keys/tls:/run/tls:ro")
docker run -d --name "$NAME" --restart unless-stopped \
  --network "$NET" --add-host host.docker.internal:host-gateway \
  --user "$HOST_UID:$HOST_GID" --env-file docker/.env \
  -e TZ=Asia/Bangkok \
  -e PROMETHEUS_URL="${PROMETHEUS_URL:-http://prometheus:9090}" \
  -e CENTRAL_NODE_JOB="${CENTRAL_NODE_JOB:-central}" \
  -e CENTRAL_NODE_INSTANCE="${CENTRAL_NODE_INSTANCE:-node-exporter:9100}" \
  -e CENTRAL_COOKIE_SECURE="${CENTRAL_COOKIE_SECURE:-false}" \
  -e PULLER_URL="${PULLER_URL:-http://central-puller:8001}" \
  -e CENTRAL_PULLER_TOKEN="${CENTRAL_SECRET_KEY:-}" \
  -p "${CENTRAL_WEB_BIND:-127.0.0.1}:$PORT:8000" \
  -v "$MAIN/data:/data" \
  -v "$MAIN/config:/app/config:ro" \
  -v "$MAIN/keys/known_hosts:/run/keys/known_hosts:ro" \
  -v "$MAIN/keys/central_ed25519.pub:/run/keys/central_ed25519.pub:ro" \
  "${TLS[@]}" \
  "$IMAGE" gunicorn --chdir /app --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 60 api:app >/dev/null

sleep 3
if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
  SCHEME=http; [ -f "$MAIN/keys/tls/cert.pem" ] && SCHEME=https
  echo "เปิดแล้ว: $SCHEME://localhost:$PORT  (เวอร์ชันก่อนเพิ่ม Windows · ล็อกอินด้วยผู้ใช้เดิม)"
  echo "ปิด: bash scripts/run-before-windows.sh stop"
else
  echo "เริ่มไม่สำเร็จ ดู: docker logs $NAME"; exit 1
fi
