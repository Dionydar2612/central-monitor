#!/usr/bin/env bash
# =====================================================================
# update.sh - อัปเดตโค้ด central-monitor จาก GitHub ด้วยคำสั่งเดียว
# =====================================================================
#   cm-update              ดึงโค้ดใหม่ + build + restart (รันจากที่ไหนก็ได้ ไม่ต้องจำ path)
#   cm-update --check      ดูอย่างเดียวว่ามีอัปเดตอะไรบ้าง ยังไม่เปลี่ยนอะไร
#   cm-update --rebuild    build + restart ใหม่ แม้ไม่มีโค้ดใหม่
#   cm-update --where      บอกว่าโปรเจกต์ติดตั้งอยู่ที่ไหน
#
#   เครื่องอื่นที่ยังไม่มีคำสั่งนี้ (ติดตั้งจาก zip / ไม่รู้ path) ใช้คำสั่งเดียว:
#     curl -fsSL https://raw.githubusercontent.com/Dionydar2612/central-monitor/main/scripts/update.sh | bash
#   (หาโฟลเดอร์จาก container ที่รันอยู่, แปลงเป็น git clone ในที่เดิม, คง puller.yaml/.env/keys/data)
#
#   คำสั่ง cm-update ถูกสร้างโดย install.sh (ลิงก์ /usr/local/bin/cm-update -> ไฟล์นี้)
#   จึงรู้ตำแหน่งโปรเจกต์เองเสมอ  ถ้ายังไม่มีคำสั่งนี้ (เครื่องที่ติดตั้งก่อนมีไฟล์นี้) ดู docs/INSTALL-TH.md
#
# ทำอะไรบ้าง
#   1) git fetch แล้วแสดงรายการ commit ใหม่
#   2) สำรอง config/puller.yaml และ docker/.env ไว้ที่ data/state/update-backup/<เวลา>/
#   3) git merge --ff-only (ไม่ทับงานที่ commit ไว้บนเครื่องนี้ ถ้ามีจะหยุดและบอก)
#      ไฟล์ที่ถูกแก้บนเครื่องนี้ (นอกจาก puller.yaml) ถูกเก็บด้วย git stash ไม่หาย
#   4) คืน config/puller.yaml ของเครื่องนี้ (รายชื่อเครื่องที่เพิ่มจากหน้า Settings ไม่หาย)
#   5) bash install.sh --keep  (คง .env + SSH key เดิม, build, restart, อัปเดต ISM policy)
# =====================================================================
set -euo pipefail

REPO_URL="${CM_REPO:-https://github.com/Dionydar2612/central-monitor.git}"

CHECK=0; REBUILD=0; DIR_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check)   CHECK=1 ;;
    --rebuild) REBUILD=1 ;;
    --where)   WHERE=1 ;;
    --dir)     shift; DIR_ARG="${1:-}" ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null || true; exit 0 ;;
    *) echo "ไม่รู้จักตัวเลือก: $1 (ดู cm-update --help)" >&2; exit 1 ;;
  esac
  shift
done

# --- หาโฟลเดอร์โปรเจกต์ --------------------------------------------------
#   1) --dir <path>   2) ตามลิงก์ cm-update -> scripts/update.sh
#   3) รันอยู่ใน git clone ของโปรเจกต์   4) หาจาก container central-api ที่รันอยู่ (ไม่ต้องรู้ path)
SELF="${BASH_SOURCE[0]:-}"
if [ -n "$DIR_ARG" ]; then
  ROOT="$(cd "$DIR_ARG" && pwd)"
elif [ -n "$SELF" ] && [ -f "$SELF" ] && [ -f "$(dirname "$(readlink -f "$SELF")")/../install.sh" ]; then
  ROOT="$(cd "$(dirname "$(readlink -f "$SELF")")/.." && pwd)"
elif TOP="$(git rev-parse --show-toplevel 2>/dev/null)" && [ -f "$TOP/install.sh" ]; then
  ROOT="$TOP"
elif WD="$(docker inspect central-api -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null \
          || sudo docker inspect central-api -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null)" && [ -n "$WD" ]; then
  ROOT="$(cd "$WD/.." && pwd)"
else
  echo "หาโฟลเดอร์ที่ติดตั้ง central-monitor ไม่เจอ (container central-api ไม่ได้รันอยู่)" >&2
  echo "ระบุเองได้:  ... | bash -s -- --dir /path/to/central-monitor" >&2
  echo "ถ้ายังไม่เคยติดตั้งเลย:  git clone $REPO_URL && cd central-monitor && bash install.sh" >&2
  exit 1
fi
cd "$ROOT"
[ "${WHERE:-0}" -eq 1 ] && { echo "$ROOT"; exit 0; }

say() { printf '\n==> %s\n' "$*"; }
command -v git >/dev/null || { echo "ต้องมี git ก่อน:  sudo apt install -y git" >&2; exit 1; }

# รันด้วย sudo -> ใช้ git ในนามเจ้าของโฟลเดอร์ (ไฟล์ใหม่จะได้ไม่เป็นของ root)
OWNER="$(stat -c %U "$ROOT")"
GIT=(git -c "safe.directory=$ROOT")
if [ "$(id -u)" -eq 0 ] && [ "$OWNER" != "root" ]; then GIT=(sudo -u "$OWNER" git -c "safe.directory=$ROOT"); fi
g() { "${GIT[@]}" "$@"; }

# --- เครื่องที่ติดตั้งจาก zip (ไม่มี .git): แปลงเป็น git clone ในที่เดิม คงค่าทั้งหมด -----------
#   ไฟล์โค้ดถูกแทนด้วยเวอร์ชันบน GitHub ; config/puller.yaml, docker/.env, keys/, data/ ไม่ถูกแตะ
#   (puller.yaml + .env สำรองไว้ก่อนอีกชั้น) แล้วรัน install.sh --keep -> ได้คำสั่ง cm-update ไว้ใช้ครั้งต่อไป
if [ ! -d .git ]; then
  [ "$CHECK" -eq 1 ] && { echo "$ROOT ยังไม่ใช่ git clone -> รันโดยไม่ใส่ --check เพื่อแปลงและอัปเดต"; exit 0; }
  BK="data/state/update-backup/$(date +%Y%m%d-%H%M%S)-convert"
  say "แปลง $ROOT เป็น git clone ของ $REPO_URL (คงค่าเดิม สำรองไว้ที่ $BK/)"
  mkdir -p "$BK"
  [ -f config/puller.yaml ] && cp -p config/puller.yaml "$BK/puller.yaml"
  [ -f docker/.env ] && cp -p docker/.env "$BK/.env"
  g init -q
  g remote add origin "$REPO_URL"
  g fetch --quiet origin main
  g reset -q --hard origin/main
  g branch -q -M main
  g branch -q --set-upstream-to=origin/main main
  [ -f "$BK/puller.yaml" ] && cp -p "$BK/puller.yaml" config/puller.yaml
  [ -f config/puller.yaml ] || cp -p config/puller.example.yaml config/puller.yaml
  echo "ตอนนี้: $(g log -1 --format='%h %ad %s' --date=short)"
  say "build + restart (install.sh --keep)"
  bash install.sh --keep
  echo; echo "อัปเดตเสร็จ ครั้งต่อไปพิมพ์แค่: cm-update"
  exit 0
fi

echo "โปรเจกต์: $ROOT"

# --- 1) มีอะไรใหม่ ---------------------------------------------------
say "1/5 ตรวจโค้ดใหม่ที่ GitHub"
BRANCH="$(g rev-parse --abbrev-ref HEAD)"
UPSTREAM="$(g rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo "origin/$BRANCH")"
g fetch --quiet origin
NEW="$(g rev-list --count "HEAD..$UPSTREAM")"
echo "ตอนนี้: $(g log -1 --format='%h %ad %s' --date=short)"
if [ "$NEW" -eq 0 ]; then
  echo "เป็นเวอร์ชันล่าสุดแล้ว ($UPSTREAM)"
  [ "$CHECK" -eq 1 ] && exit 0
  if [ "$REBUILD" -eq 0 ]; then echo "(ถ้าต้องการ build/restart ใหม่อยู่ดี: cm-update --rebuild)"; exit 0; fi
else
  echo "มี $NEW commit ใหม่:"
  g log --format='  %h %ad %s' --date=short "HEAD..$UPSTREAM" | head -30
  [ "$CHECK" -eq 1 ] && { echo; echo "อัปเดตจริงด้วย: cm-update"; exit 0; }
fi

# --- 2) สำรองค่าของเครื่องนี้ --------------------------------------------
BK="data/state/update-backup/$(date +%Y%m%d-%H%M%S)"
say "2/5 สำรอง config/puller.yaml + docker/.env -> $BK/"
mkdir -p "$BK"
[ -f config/puller.yaml ] && cp -p config/puller.yaml "$BK/puller.yaml"
[ -f docker/.env ] && cp -p docker/.env "$BK/.env"
# เก็บแค่ 10 ชุดล่าสุด
ls -1d data/state/update-backup/*/ 2>/dev/null | sort | head -n -10 | xargs -r rm -rf

# --- 3) ดึงโค้ด -------------------------------------------------------
if [ "$NEW" -gt 0 ]; then
  say "3/5 ดึงโค้ดใหม่"
  # รุ่นเก่า puller.yaml ยังอยู่ใน git -> ปล่อยให้ git จัดการไฟล์นี้ได้ (มีสำรองแล้ว คืนในขั้นที่ 4)
  if g ls-files --error-unmatch config/puller.yaml >/dev/null 2>&1; then
    g checkout -- config/puller.yaml
  fi
  if ! g diff --quiet || ! g diff --cached --quiet; then
    g stash push -m "cm-update $(date '+%F %T')" >/dev/null
    echo "[WARN] มีไฟล์ที่ถูกแก้บนเครื่องนี้ -> เก็บไว้ใน git stash แล้ว (ดู: git -C $ROOT stash list)"
  fi
  if ! g merge --ff-only "$UPSTREAM"; then
    echo "[ERROR] เครื่องนี้มี commit ของตัวเองที่ไม่อยู่บน GitHub -> ไม่ได้อัปเดต (โค้ดเดิมยังทำงานอยู่)" >&2
    [ -f "$BK/puller.yaml" ] && cp -p "$BK/puller.yaml" config/puller.yaml
    exit 1
  fi
  echo "ตอนนี้: $(g log -1 --format='%h %ad %s' --date=short)"
else
  say "3/5 ไม่มีโค้ดใหม่ (--rebuild)"
fi

# --- 4) คืนค่าของเครื่องนี้ ---------------------------------------------
say "4/5 คืน config/puller.yaml ของเครื่องนี้"
if [ -f "$BK/puller.yaml" ]; then
  cp -p "$BK/puller.yaml" config/puller.yaml
elif [ ! -f config/puller.yaml ]; then
  cp -p config/puller.example.yaml config/puller.yaml
  echo "ยังไม่มี puller.yaml -> สร้างจาก config/puller.example.yaml"
fi
echo "รายชื่อเครื่อง: $(grep -cE '^  - name:' config/puller.yaml || true) เครื่อง"

# --- 5) build + restart --------------------------------------------------
say "5/5 build + restart (install.sh --keep)"
bash install.sh --keep

echo
echo "อัปเดตเสร็จ  สำรองค่าเดิมไว้ที่ $ROOT/$BK"
