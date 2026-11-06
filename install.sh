#!/usr/bin/env bash
# =====================================================================
# install.sh - ติดตั้ง central-monitor ด้วยคำสั่งเดียว
# =====================================================================
#   bash install.sh                 ติดตั้ง + สตาร์ททั้งระบบ
#   bash install.sh --no-start      เตรียมทุกอย่างแต่ยังไม่สตาร์ท container
#   bash install.sh --keep          อัปเกรดโค้ด: คง docker/.env และ SSH key เดิมไว้ (ไม่ต้อง Deploy key ใหม่ทุกเครื่อง)
#   (รันด้วย user ปกติ หรือ sudo bash install.sh ก็ได้ — ไฟล์ทั้งหมดจะเป็นของ user จริงเสมอ ไม่ใช่ root)
#
# ทำให้อัตโนมัติ
#   1) ติดตั้ง Docker ถ้ายังไม่มี + เพิ่ม user เข้ากลุ่ม docker
#   2) ตั้ง vm.max_map_count=262144 (OpenSearch ต้องการ) แบบถาวร
#   3) ปรับเจ้าของไฟล์ทั้งโปรเจกต์เป็น user จริง (กัน permission denied)
#   4) ลบ docker/.env เดิม แล้วสร้างใหม่ (login admin/admin + สุ่มรหัส/secret)  [--keep = ไม่ลบ]
#        - ถ้ามีข้อมูล OpenSearch เดิมอยู่ จะคงรหัส OpenSearch เดิมไว้ (ไม่งั้นต่อกับข้อมูลเดิมไม่ได้)
#        - ตั้ง login เองได้:  UI_USER=xxx UI_PASS=yyy bash install.sh
#   5) ลบ SSH key เดิม แล้วสร้าง keys/central_ed25519 ใหม่ (known_hosts เก็บของเดิมไว้)  [--keep = ไม่ลบ]
#        ถ้าไม่ใช้ --keep ทุกเครื่อง target ต้อง Deploy key ใหม่ ไม่งั้นดึง log ไม่ได้
#   6) docker compose up -d --build   (container รันด้วย uid/gid ของ user จริง ไม่ใช่ root)
#   7) รอจน OpenSearch พร้อม แล้วตั้ง/อัปเดต ISM policy ลบ log อัตโนมัติ
#        อายุ log ตั้งที่ OPENSEARCH_LOG_RETENTION ใน docker/.env (ปริยาย 7d) รันซ้ำเพื่อเปลี่ยนค่าได้
#   + สร้างคำสั่ง cm-update (/usr/local/bin/cm-update -> scripts/update.sh)
#        อัปเดตโค้ดจาก GitHub ได้จากที่ไหนก็ได้โดยไม่ต้องจำ path: cm-update
#
# config/puller.yaml ไม่อยู่ใน git (เป็นค่าเฉพาะเครื่อง) ถ้ายังไม่มีจะสร้างจาก config/puller.example.yaml
#
# ที่ต้องทำต่อหลังเปิดเว็บได้: หน้า Settings -> + Add target แล้วกรอก Admin user/Password ของเครื่องปลายทาง
#   ระบบจะวาง public key ให้เอง (ไม่เก็บรหัสผ่าน) — ขั้นวาง key ไม่ได้ทำใน install.sh
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")"

START=1
KEEP=0
for a in "$@"; do
  case "$a" in
    --no-start)      START=0 ;;
    --keep)          KEEP=1 ;;
    -h|--help)       sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "ไม่รู้จักตัวเลือก: $a (ดู bash install.sh --help)" >&2; exit 1 ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
if [ -n "$SUDO" ] && ! command -v sudo >/dev/null; then
  echo "ต้องรันด้วย root หรือมี sudo" >&2; exit 1
fi

# --- เจ้าของจริงของโปรเจกต์ = user ที่ล็อกอิน (แม้รันด้วย sudo ก็ไม่ใช่ root) ----------
AS_OWNER=()
if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  OWNER="$SUDO_USER"; AS_OWNER=(sudo -u "$SUDO_USER")
else
  OWNER="$(id -un)"
fi
OWNER_GRP="$(id -gn "$OWNER")"
HOST_UID="$(id -u "$OWNER")"; HOST_GID="$(id -g "$OWNER")"
run_o() { if [ "${#AS_OWNER[@]}" -gt 0 ]; then "${AS_OWNER[@]}" "$@"; else "$@"; fi; }

# ให้ $OWNER เป็นเจ้าของทุกไฟล์ในโปรเจกต์ ยกเว้น data/buffer/.otel-storage (เป็นของ uid 10001 ของ otel)
fix_owner() {
  [ "$OWNER" = "root" ] && return 0
  local bad
  bad="$(find . -path ./data/buffer/.otel-storage -prune -o \( ! -user "$OWNER" -o ! -group "$OWNER_GRP" \) -print -quit 2>/dev/null || true)"
  [ -n "$bad" ] || return 0
  $SUDO find . -path ./data/buffer/.otel-storage -prune -o \( ! -user "$OWNER" -o ! -group "$OWNER_GRP" \) \
    -exec chown -h "$OWNER:$OWNER_GRP" {} + 2>/dev/null || true
}

# --- 1) Docker -------------------------------------------------------
say "1/7 ตรวจ Docker"
if ! command -v docker >/dev/null; then
  command -v curl >/dev/null || { echo "ต้องมี curl" >&2; exit 1; }
  echo "ไม่พบ Docker - กำลังติดตั้ง (get.docker.com)"
  curl -fsSL https://get.docker.com | $SUDO sh
  $SUDO systemctl enable --now docker 2>/dev/null || true
fi
# ให้ user ใช้ docker ได้โดยไม่ต้อง sudo (มีผลหลัง logout/login ใหม่)
if [ "$OWNER" != "root" ] && getent group docker >/dev/null; then
  case " $(id -nG "$OWNER") " in
    *" docker "*) ;;
    *) $SUDO usermod -aG docker "$OWNER" \
         && echo "เพิ่ม $OWNER เข้ากลุ่ม docker แล้ว (ต้อง logout/login ใหม่ถึงจะใช้ docker โดยไม่ใส่ sudo)" ;;
  esac
fi
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if $SUDO docker info >/dev/null 2>&1; then DOCKER="$SUDO docker"
  else echo "docker daemon ไม่ทำงาน หรือไม่มีสิทธิ์ใช้งาน" >&2; exit 1; fi
fi
$DOCKER compose version >/dev/null 2>&1 || { echo "ต้องมี docker compose v2 plugin" >&2; exit 1; }
echo "docker compose $($DOCKER compose version --short)"

# --- 2) sysctl -------------------------------------------------------
say "2/7 ตั้ง vm.max_map_count"
if [ "$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)" -lt 262144 ]; then
  echo 'vm.max_map_count=262144' | $SUDO tee /etc/sysctl.d/99-opensearch.conf >/dev/null
  $SUDO sysctl -w vm.max_map_count=262144 >/dev/null
fi
echo "vm.max_map_count=$(sysctl -n vm.max_map_count)"

# --- 3) สิทธิ์ไฟล์ -----------------------------------------------------
say "3/7 ปรับเจ้าของไฟล์เป็น $OWNER:$OWNER_GRP (กัน permission denied)"
run_o mkdir -p keys keys/tls config data/buffer data/state/system data/prometheus
if [ ! -f config/puller.yaml ] && [ -f config/puller.example.yaml ]; then
  run_o cp config/puller.example.yaml config/puller.yaml
  echo "สร้าง config/puller.yaml จาก config/puller.example.yaml"
fi
[ -d keys/known_hosts ] && rm -rf keys/known_hosts       # Docker เคยสร้างเป็นโฟลเดอร์ (ลบหลัง fix_owner ด้านล่างถ้าเป็นของ root)
fix_owner
[ -d keys/known_hosts ] && $SUDO rm -rf keys/known_hosts
if [ "$OWNER" = "root" ]; then
  echo "[WARN] รันเป็น root โดยตรง (ไม่ใช่ sudo) - container จะรันเป็น root ถ้าอยากให้เป็น user ปกติ ให้รันด้วย user นั้น: bash install.sh"
else
  echo "ไฟล์ทั้งหมดเป็นของ $OWNER แล้ว (container จะรันด้วย uid=$HOST_UID gid=$HOST_GID)"
fi

# --- 4) .env (ลบแล้วสร้างใหม่ ยกเว้นใช้ --keep) -------------------------
if [ "$KEEP" -eq 1 ] && [ -f docker/.env ]; then
  say "4/7 คง docker/.env เดิมไว้ (--keep)"
  # .env เก่าที่ยังไม่มีค่าอายุ log -> เติมให้ (ไม่แตะค่าอื่น)
  grep -q '^OPENSEARCH_LOG_RETENTION=' docker/.env || echo 'OPENSEARCH_LOG_RETENTION=7d' >> docker/.env
else
say "4/7 สร้าง docker/.env ใหม่"
KEEP_OS_PASS=""
if $DOCKER volume inspect central-monitor_opensearch-data >/dev/null 2>&1; then
  if [ -f docker/.env ]; then
    KEEP_OS_PASS="$(grep -E '^OPENSEARCH_ADMIN_PASSWORD=' docker/.env | cut -d= -f2- || true)"
  fi
  if [ -n "$KEEP_OS_PASS" ]; then
    echo "พบข้อมูล OpenSearch เดิม -> คงรหัส OpenSearch เดิมไว้ (รหัสผ่านถูกผูกกับ volume ตอนสร้างครั้งแรก)"
  else
    echo "[WARN] พบข้อมูล OpenSearch เดิมแต่ไม่มี .env เดิมให้อ่านรหัส -> รหัสใหม่จะต่อกับข้อมูลเดิมไม่ได้"
    echo "       ถ้าไม่ต้องการ log เดิม ให้ลบ volume ก่อน:  cd docker && docker compose down -v"
  fi
fi
rm -rf docker/.env
ENV_ARGS=(HOST_UID="$HOST_UID" HOST_GID="$HOST_GID")
[ -z "$KEEP_OS_PASS" ] || ENV_ARGS+=(OS_PASS="$KEEP_OS_PASS")
[ -z "${UI_USER:-}" ]  || ENV_ARGS+=(UI_USER="$UI_USER")
[ -z "${UI_PASS:-}" ]  || ENV_ARGS+=(UI_PASS="$UI_PASS")
run_o env "${ENV_ARGS[@]}" bash scripts/init-env.sh
fi

# --- 5) SSH key (สร้างใหม่ ยกเว้นใช้ --keep และมี key เดิมอยู่) -----------
if [ "$KEEP" -eq 1 ] && [ -f keys/central_ed25519 ] && [ -f keys/central_ed25519.pub ]; then
  say "5/7 คง SSH key เดิมไว้ (--keep)"
else
say "5/7 สร้าง SSH key ใหม่"
command -v ssh-keygen >/dev/null || { echo "ต้องมี ssh-keygen (apt install openssh-client)" >&2; exit 1; }
rm -rf keys/central_ed25519 keys/central_ed25519.pub
KEY_COMMENT="central-monitor@$(hostname -s 2>/dev/null | tr -c 'A-Za-z0-9._\n-' '-')"
run_o ssh-keygen -q -t ed25519 -N "" -C "$KEY_COMMENT" -f keys/central_ed25519
chmod 600 keys/central_ed25519
echo "สร้าง key ใหม่แล้ว ($KEY_COMMENT)"
echo "เครื่อง target ที่เคยวาง key เก่าไว้ ต้องวาง key ใหม่ที่หน้า Settings (ปุ่ม Deploy key) หลังติดตั้งเสร็จ"
fi
[ -f keys/known_hosts ] || run_o touch keys/known_hosts

# --- agent Windows (Fluent Bit): token + ใบรับรองของ central-agent-gw (สร้างครั้งเดียว คงไว้ทุกครั้งที่อัปเดต) ---
if ! grep -q '^CENTRAL_AGENT_TOKEN=.\+' docker/.env; then
  sed -i '/^CENTRAL_AGENT_TOKEN=/d' docker/.env
  echo "CENTRAL_AGENT_TOKEN=$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')" >> docker/.env
  echo "สร้าง CENTRAL_AGENT_TOKEN ใน docker/.env (agent Windows ใช้ส่งข้อมูลเข้า :4318)"
fi
if [ ! -f keys/agent-tls/cert.pem ] || [ ! -f keys/agent-tls/key.pem ]; then
  run_o bash scripts/make-cert.sh agent >/dev/null && echo "สร้างใบรับรอง keys/agent-tls/ (agent Windows ใช้ตรวจ central)"
fi
[ -f data/prometheus/web-nodes.json ] || printf '[]\n' | run_o tee data/prometheus/web-nodes.json >/dev/null

if [ "$START" -eq 0 ]; then
  fix_owner
  say "เตรียมเสร็จ (--no-start) สั่งเริ่มเองด้วย: cd docker && docker compose up -d --build"
  exit 0
fi

# --- 6) สตาร์ท --------------------------------------------------------
say "6/7 สตาร์ท container (ครั้งแรกใช้เวลา build/ดึง image หลายนาที)"
WAS_RUNNING="$( cd docker && $DOCKER compose ps -q central-api 2>/dev/null || true )"
( cd docker && $DOCKER compose up -d --build )
if [ -n "$WAS_RUNNING" ]; then
  # central-api mount ไฟล์ .pub เดี่ยว ๆ - สร้างไฟล์ใหม่แล้ว container เก่ายังเห็นไฟล์เดิม ต้องสร้าง container ใหม่
  ( cd docker && $DOCKER compose up -d --force-recreate --no-deps central-api central-puller )
  # ใบรับรองของ agent อาจเปลี่ยน -> คัดลอกใหม่แล้วเริ่ม gateway ใหม่
  ( cd docker && $DOCKER compose up -d --force-recreate buffer-init central-agent-gw ) || true
fi

# --- 7) ISM policy ---------------------------------------------------
say "7/7 รอ OpenSearch พร้อม แล้วตั้ง/อัปเดต ISM policy ลบ log อัตโนมัติ"
# scripts/apply-ism.py รันใน central-api (มี OPENSEARCH_ADMIN_PASSWORD + OPENSEARCH_LOG_RETENTION จาก .env)
# อ่าน policy JSON จาก stdin ถ้ามี policy อยู่แล้วจะอัปเดตให้ (เดิม PUT ซ้ำแล้วค่าใหม่ไม่ถูกใช้)
POLICY="start_push_log/opensearch/central-log-retention-ism-policy.json"
DONE=0
for i in $(seq 1 36); do
  OUT="$( cd docker && $DOCKER compose exec -T central-api python3 -c "$(cat ../scripts/apply-ism.py)" \
    < "../$POLICY" 2>&1 || true )"
  if echo "$OUT" | grep -q '^ISM-OK'; then DONE=1; echo "$OUT" | grep '^ISM-OK'; break; fi
  sleep 5
done
[ "$DONE" -eq 1 ] || { echo "[WARN] ตั้ง ISM policy ยังไม่สำเร็จ - ทำซ้ำเองได้ตาม docs/INSTALL-TH.md ข้อ 8.4"; echo "$OUT" | tail -3; }

# --- คำสั่ง cm-update (อัปเดตโค้ดจากที่ไหนก็ได้ ไม่ต้องจำ path) -------------------
chmod +x scripts/update.sh 2>/dev/null || true
if $SUDO ln -sfn "$(pwd)/scripts/update.sh" /usr/local/bin/cm-update 2>/dev/null; then
  echo "คำสั่งอัปเดต: cm-update   (ชี้ไปที่ $(pwd))"
else
  echo "[WARN] สร้าง /usr/local/bin/cm-update ไม่ได้ - อัปเดตด้วย: bash $(pwd)/scripts/update.sh"
fi

fix_owner

# --- สรุป ------------------------------------------------------------
USER_NAME="$(grep -E '^CENTRAL_AUTH_USERNAME=' docker/.env | cut -d= -f2-)"
USER_PASS="$(grep -E '^CENTRAL_AUTH_PASSWORD=' docker/.env | cut -d= -f2-)"
if [ "$KEEP" -eq 1 ]; then
  KEY_NOTE="      (--keep: ใช้ SSH key และ .env เดิม เครื่องที่ตั้งไว้แล้วไม่ต้อง Deploy key ใหม่)"
  KEEP_NOTE="หมายเหตุ: รันด้วย --keep -> รหัสเว็บ, รหัส OpenSearch และ SSH key เป็นค่าเดิม"
else
  KEY_NOTE="      เครื่องที่เพิ่มไว้ก่อนหน้านี้: กดปุ่ม Deploy key ที่แถวของเครื่อง (สร้าง key ใหม่แล้ว)"
  KEEP_NOTE="หมายเหตุ: รันโดยไม่ใส่ --keep จะสร้าง .env และ SSH key ใหม่ (อัปเดตโค้ดครั้งหน้าใช้ cm-update)"
fi
cat <<MSG

======================================================================
 ติดตั้งเสร็จแล้ว
   เว็บ        : http://127.0.0.1:8090   (ถ้าอยู่บนเครื่องอื่น: ssh -L 8090:127.0.0.1:8090 user@server)
   Login       : ${USER_NAME} / ${USER_PASS}
   เปลี่ยนรหัส : docs/CONFIG-TH.md ข้อ 2
   เจ้าของไฟล์  : ${OWNER}:${OWNER_GRP}  (container รันด้วย uid ${HOST_UID})

 ขั้นต่อไป
   1) เข้าเว็บ -> Settings -> + Add target : กรอกเครื่อง + Scan host key + (Admin user/Password เพื่อวาง
      public key ให้อัตโนมัติ ไม่เก็บรหัสผ่าน) แล้ว Save
${KEY_NOTE}
   2) ./scripts/preflight.sh --ssh   เช็คว่า SSH เข้าทุกเครื่องได้
   3) อัปเดตโค้ดจาก GitHub ภายหลัง: พิมพ์ cm-update จากที่ไหนก็ได้ (cm-update --check = ดูอย่างเดียว)
 ${KEEP_NOTE}
======================================================================
MSG
