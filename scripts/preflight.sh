#!/usr/bin/env bash
# =====================================================================
# preflight.sh - ตรวจความพร้อมก่อนสตาร์ท (เจอปัญหาก่อน ดีกว่าให้ container ล้ม)
# =====================================================================
#   ./scripts/preflight.sh          ตรวจแบบไม่ใช้เครือข่าย (เร็ว)
#   ./scripts/preflight.sh --ssh    ตรวจเพิ่ม: SSH เข้าทุกเครื่องได้ไหม + อ่านไฟล์ log ได้ไหม
#
# ตรวจอะไรบ้าง (ผลแสดงเป็น [ OK ] / [WARN] / [FAIL]; มี FAIL = ออกด้วย exit code 1)
#   Host     : docker compose v2, vm.max_map_count >= 262144 (OpenSearch ต้องการ)
#   .env     : docker/.env มีอยู่ ไม่เหลือ CHANGE_ME รหัสผ่าน OpenSearch 2 ตัวตรงกัน สิทธิ์ 600
#   SSH keys : keys/central_ed25519 เป็นไฟล์ สิทธิ์ถูกต้อง, keys/known_hosts เป็นไฟล์
#              (ถ้า Docker สร้างเป็น "โฟลเดอร์" เพราะไฟล์ไม่มีตอน mount จะฟ้องและบอกวิธีแก้)
#   Targets  : ทุกเครื่องใน config/puller.yaml มี host key ใน known_hosts
#   (--ssh)  : ssh เข้าเครื่องได้ และ path ของ log ที่ตั้งไว้ใน puller.yaml มีไฟล์ที่อ่านได้จริง
#
# อ่าน config/puller.yaml แบบ awk ง่าย ๆ จึงต้องคงรูปแบบย่อหน้าตามไฟล์ตัวอย่าง
# =====================================================================
set -uo pipefail
cd "$(dirname "$0")/.."      # ย้ายไปโฟลเดอร์รากของโปรเจกต์ ไม่ว่าจะเรียกจากที่ไหน

FAIL=0
ok()   { printf '  [ OK ] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; FAIL=1; }

echo "== Host"
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  ok "docker compose v2 ($(docker compose version --short 2>/dev/null))"
else
  bad "docker + compose v2 plugin not found"
fi

mm="$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)"
if [ "${mm:-0}" -ge 262144 ]; then ok "vm.max_map_count=$mm"
else bad "vm.max_map_count=$mm (need >= 262144, else OpenSearch exits with code 78)"; fi

ENV_FILE="docker/.env"
echo "== $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
  bad "$ENV_FILE missing -> run ./scripts/init-env.sh"
else
  if grep -Eq '=(CHANGE_ME|CHANGE-ME|CHANGEME)\s*$' "$ENV_FILE"; then bad "$ENV_FILE still contains CHANGE_ME placeholders"
  else ok "$ENV_FILE has no placeholders"; fi
  a="$(grep -E '^OPENSEARCH_ADMIN_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
  b="$(grep -E '^OPENSEARCH_INITIAL_ADMIN_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
  [ -n "$a" ] && [ "$a" = "$b" ] && ok "OpenSearch passwords match" || bad "OPENSEARCH_ADMIN_PASSWORD and OPENSEARCH_INITIAL_ADMIN_PASSWORD must be identical"
  [ "$(stat -c %a "$ENV_FILE")" = "600" ] && ok "$ENV_FILE mode 600" || warn "$ENV_FILE mode is $(stat -c %a "$ENV_FILE") (recommend chmod 600 $ENV_FILE)"
fi

echo "== SSH keys"
if [ -f keys/central_ed25519 ]; then
  ok "keys/central_ed25519 is a file"
  m="$(stat -c %a keys/central_ed25519)"
  [ "$m" = "600" ] || [ "$m" = "400" ] && ok "private key mode $m" || bad "private key mode $m -> chmod 600 keys/central_ed25519"
elif [ -d keys/central_ed25519 ]; then
  bad "keys/central_ed25519 is a DIRECTORY (Docker created it) -> rm -rf it and run ssh-keygen (docs 7)"
else
  bad "keys/central_ed25519 missing -> ssh-keygen -t ed25519 -N '' -f keys/central_ed25519"
fi
if [ -f keys/central_ed25519.pub ]; then ok "keys/central_ed25519.pub is a file"
elif [ -d keys/central_ed25519.pub ]; then bad "keys/central_ed25519.pub is a DIRECTORY -> rm -rf it && ssh-keygen -y -f keys/central_ed25519 > keys/central_ed25519.pub"
else bad "keys/central_ed25519.pub missing -> ssh-keygen -y -f keys/central_ed25519 > keys/central_ed25519.pub (หน้า Settings ใช้โชว์ public key)"; fi
if [ -f keys/known_hosts ]; then ok "keys/known_hosts is a file"
elif [ -d keys/known_hosts ]; then bad "keys/known_hosts is a DIRECTORY -> rm -rf keys/known_hosts && touch keys/known_hosts"
else bad "keys/known_hosts missing -> touch keys/known_hosts then ssh-keyscan each host"; fi

echo "== Targets in config/puller.yaml"
# prints: name host port user   (one line per target)
TARGETS="$(awk '
  /^  - name:/ { if (n) print n, h, p, u; n=$3; h=""; p=22; u=""; next }
  /^    host:/ { h=$2 }
  /^      port:/ { p=$2 }
  /^      user:/ { u=$2 }
  END { if (n) print n, h, p, u }' config/puller.yaml)"

if [ -z "$TARGETS" ]; then
  warn "no targets defined yet (targets: [] ) - add machines, see docs/USAGE-TH.md"
else
  while read -r name host port user; do
    [ -z "$name" ] && continue
    if [ "$port" = "22" ]; then key="$host"; else key="[$host]:$port"; fi
    if [ -f keys/known_hosts ] && ssh-keygen -F "$key" -f keys/known_hosts >/dev/null 2>&1; then
      ok "$name: host key for '$key' is in known_hosts"
    else
      bad "$name: '$key' NOT in keys/known_hosts -> ssh-keyscan -t ed25519 -p $port $host >> keys/known_hosts"
    fi
    if [ "${1:-}" = "--ssh" ] && [ -f keys/central_ed25519 ]; then
      if out="$(ssh -i keys/central_ed25519 -p "$port" -o UserKnownHostsFile=keys/known_hosts \
            -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=10 \
            "$user@$host" hostname 2>&1)"; then
        ok "$name: ssh $user@$host -> $out"
      else
        bad "$name: ssh failed: $out"
      fi
    fi
  done <<< "$TARGETS"

  if [ "${1:-}" = "--ssh" ]; then
    echo "== Log paths (readable by the SSH user?)"
    awk '
      /^  - name:/ { t=$3 }
      /^    host:/ { h=$2 }
      /^      port:/ { p=$2 }
      /^      user:/ { u=$2 }
      /^        path:/ { print t, h, (p?p:22), u, $2 }
      /^  - name:/ { p=22 }' config/puller.yaml | while read -r t h p u path; do
      dir="$(dirname "$path")"; pat="$(basename "$path")"
      n="$(ssh -i keys/central_ed25519 -p "$p" -o UserKnownHostsFile=keys/known_hosts \
            -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=10 "$u@$h" \
            "find -- '$dir' -maxdepth 1 -type f -name '$pat' 2>/dev/null | wc -l" 2>/dev/null || echo ERR)"
      case "$n" in ERR|0) warn "$t: no readable file matches $path" ;; *) ok "$t: $n file(s) match $path" ;; esac
    done
  fi
fi

echo
if [ "$FAIL" -eq 0 ]; then echo "Preflight passed."; else echo "Preflight FOUND PROBLEMS - fix the [FAIL] lines first."; fi
exit "$FAIL"
