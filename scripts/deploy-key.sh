#!/usr/bin/env bash
# =====================================================================
# deploy-key.sh - วาง public key ของ server กลาง ลงเครื่อง target ทุกเครื่องใน puller.yaml
# =====================================================================
#   ./scripts/deploy-key.sh                 ทุกเครื่องใน config/puller.yaml
#   ./scripts/deploy-key.sh --only NAME     เฉพาะ target ชื่อ NAME
#
# ทำอะไร (ต่อ 1 เครื่อง)
#   1) เก็บ host key เข้า keys/known_hosts ถ้ายังไม่มี (ssh-keyscan + แสดง fingerprint ให้ตรวจ)
#   2) ถ้า key ปัจจุบันเข้าเครื่องนั้นได้อยู่แล้ว -> ข้าม (รันซ้ำได้)
#   3) ไม่งั้นวาง key ใหม่ลง ~/.ssh/authorized_keys ของ user ใน puller.yaml
#      และ "ลบบรรทัด key เก่า" ของ server นี้ออกให้ (จับจาก comment central-monitor@<hostname>
#      และจาก key เก่าที่ install.sh ส่งมาทาง OLD_KEY)
#   4) ทดสอบ ssh ด้วย key ใหม่
#
# เข้าเครื่องปลายทางด้วยอะไร (ลองตามลำดับ)
#   a) key เก่า (OLD_KEY=ไฟล์ private key เก่า — install.sh ส่งให้เองตอนสร้าง key ใหม่)
#   b) รหัสผ่าน/สิทธิ์ของคุณเอง (ต้องรันในเทอร์มินัลที่พิมพ์ได้)
#        user ปลายทางที่ปิด password (เช่น central-pull) -> ใช้ admin ของเครื่องนั้น:
#        TARGET_ADMIN_USER=ubuntu ./scripts/deploy-key.sh   (ต้อง sudo -u <user> ได้)
#   ถ้าไม่ได้ทั้งสองทาง สคริปต์พิมพ์คำสั่งให้ไปวางเองและไม่ทำให้การติดตั้งล้ม
#
# ตัวเลือกผ่าน environment
#   KEY_FROM=10.0.0.5   ใส่ from="10.0.0.5" นำหน้า key (จำกัด IP ที่ใช้ key นี้ได้)
#                       หมายเหตุ: puller อยู่หลัง Docker NAT ต้องใส่ IP ของ server กลางที่ออกไปหาเครื่อง target
# =====================================================================
set -uo pipefail
cd "$(dirname "$0")/.."

ONLY=""
[ "${1:-}" = "--only" ] && ONLY="${2:-}"

KEY=keys/central_ed25519; PUB=keys/central_ed25519.pub; KH=keys/known_hosts
[ -f "$KEY" ] && [ -f "$PUB" ] || { echo "ไม่พบ $KEY / $PUB - รัน bash install.sh ก่อน" >&2; exit 1; }
[ -f "$KH" ] || : > "$KH"

NEW_BLOB="$(awk '{print $2}' "$PUB")"
MARK="$(awk '{print $3}' "$PUB")"
OLD_KEY="${OLD_KEY:-}"; OLD_BLOB="${OLD_BLOB:-}"

PREFIX=""
[ -n "${KEY_FROM:-}" ] && PREFIX="from=\"$KEY_FROM\" "
LINE="${PREFIX}$(cat "$PUB")"

# รูปแบบที่ใช้ลบบรรทัดเก่าออกจาก authorized_keys (grep -F -e ...)
PATS="-e '$NEW_BLOB'"
[ -n "$MARK" ] && PATS="$PATS -e '$MARK'"
[ -n "$OLD_BLOB" ] && PATS="$PATS -e '$OLD_BLOB'"

ok()   { printf '  [ OK ] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }

TARGETS="$(awk '
  /^  - name:/ { if (n) print n, h, p, u; n=$3; h=""; p=22; u=""; next }
  /^    host:/ { h=$2 }
  /^      port:/ { p=$2 }
  /^      user:/ { u=$2 }
  END { if (n) print n, h, p, u }' config/puller.yaml)"

if [ -z "$TARGETS" ]; then
  echo "ยังไม่มี target ใน config/puller.yaml - เพิ่มเครื่องที่หน้า Settings แล้วรัน ./scripts/deploy-key.sh"
  exit 0
fi

# สคริปต์ที่ไปรันบนเครื่องปลายทาง (เขียน authorized_keys แบบไม่ซ้ำ + ลบ key เก่า)
remote_script() {
cat <<EOS
set -e
umask 077
d="\$HOME/.ssh"; f="\$d/authorized_keys"
mkdir -p "\$d"; chmod 700 "\$d"
touch "\$f"; chmod 600 "\$f"
t="\$(mktemp "\$d/.cm.XXXXXX")"
grep -v -F $PATS "\$f" > "\$t" || true
cat >> "\$t" <<'CM_KEY'
$LINE
CM_KEY
cat "\$t" > "\$f"
rm -f "\$t"
EOS
}
B64="$(remote_script | base64 -w0)"
CMD_PLAIN="echo $B64 | base64 -d | sh"

SSHO=(-o StrictHostKeyChecking=yes -o UserKnownHostsFile="$KH" -o ConnectTimeout=10)
FAILED=()

while read -r name host port user <&3; do
  [ -n "${name:-}" ] && [ -n "${host:-}" ] && [ -n "${user:-}" ] || continue
  [ -z "$ONLY" ] || [ "$ONLY" = "$name" ] || continue
  echo "== $name ($user@$host:$port)"

  # 1) host key
  hk="$host"; [ "$port" = "22" ] || hk="[$host]:$port"
  if ! ssh-keygen -F "$hk" -f "$KH" >/dev/null 2>&1; then
    if out="$(ssh-keyscan -T 10 -t ed25519 -p "$port" "$host" 2>/dev/null)" && [ -n "$out" ]; then
      printf '%s\n' "$out" >> "$KH"
      ok "เก็บ host key ใหม่: $(printf '%s\n' "$out" | ssh-keygen -lf - 2>/dev/null)  <- ตรวจให้ตรงกับเครื่องจริง"
    else
      warn "scan host key ไม่สำเร็จ (เครื่องปิด/พอร์ตผิด/firewall) -> ข้ามเครื่องนี้"; FAILED+=("$name"); continue
    fi
  fi

  # 2) key ปัจจุบันเข้าได้แล้ว?
  if ssh -i "$KEY" -p "$port" "${SSHO[@]}" -o BatchMode=yes -o IdentitiesOnly=yes "$user@$host" true </dev/null 2>/dev/null; then
    ok "key ปัจจุบันเข้าเครื่องนี้ได้อยู่แล้ว - ไม่ต้องวางซ้ำ"; continue
  fi

  # 3) วาง key ใหม่
  done_push=0
  if [ -n "$OLD_KEY" ] && [ -f "$OLD_KEY" ]; then
    if ssh -i "$OLD_KEY" -p "$port" "${SSHO[@]}" -o BatchMode=yes -o IdentitiesOnly=yes "$user@$host" "$CMD_PLAIN" </dev/null 2>/dev/null; then
      ok "วาง key ใหม่ (เข้าด้วย key เก่า) และลบ key เก่าแล้ว"; done_push=1
    fi
  fi
  if [ "$done_push" -eq 0 ] && [ -t 0 ]; then
    admin="${TARGET_ADMIN_USER:-$user}"
    if [ "$admin" = "$user" ]; then CMD="$CMD_PLAIN"; else CMD="echo $B64 | base64 -d | sudo -u $user -H sh"; fi
    echo "  ไม่มี key เก่าที่ใช้ได้ -> เข้าด้วยรหัสผ่านของ $admin@$host (พิมพ์รหัสผ่านถ้าถูกถาม)"
    if ssh -t -p "$port" "${SSHO[@]}" "$admin@$host" "$CMD"; then
      ok "วาง key ใหม่แล้ว"; done_push=1
    fi
  fi

  # 4) ทดสอบ
  if [ "$done_push" -eq 1 ] && ssh -i "$KEY" -p "$port" "${SSHO[@]}" -o BatchMode=yes -o IdentitiesOnly=yes "$user@$host" true </dev/null 2>/dev/null; then
    ok "ทดสอบ ssh ด้วย key ใหม่ผ่าน"
  else
    warn "วาง/ทดสอบ key ไม่สำเร็จ - วางเองบนเครื่อง $host ด้วยคำสั่งนี้:"
    printf '       echo %q | sudo -u %s tee -a ~%s/.ssh/authorized_keys   # (mkdir -p ~/.ssh; chmod 700 ~/.ssh; chmod 600 authorized_keys)\n' "$LINE" "$user" "$user"
    FAILED+=("$name")
  fi
done 3<<< "$TARGETS"

echo
if [ "${#FAILED[@]}" -eq 0 ]; then echo "วาง key ครบทุกเครื่องแล้ว"; exit 0
else echo "ยังไม่สำเร็จ: ${FAILED[*]}  (puller จะ Permission denied (publickey) กับเครื่องเหล่านี้จนกว่าจะวาง key)"; exit 2; fi
