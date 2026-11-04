#!/usr/bin/env bash
# =====================================================================
# update.sh - อัปเดตโค้ด central-monitor จาก GitHub ด้วยคำสั่งเดียว
# =====================================================================
#   cm-update              ดึงโค้ดใหม่ + build + restart (รันจากที่ไหนก็ได้ ไม่ต้องจำ path)
#   cm-update --check      ดูอย่างเดียวว่ามีอัปเดตอะไรบ้าง ยังไม่เปลี่ยนอะไร
#   cm-update --rebuild    build + restart ใหม่ แม้ไม่มีโค้ดใหม่
#   cm-update --where      บอกว่าโปรเจกต์ติดตั้งอยู่ที่ไหน
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

# --- หาโฟลเดอร์โปรเจกต์ (ตามลิงก์ cm-update -> scripts/update.sh) ----------------
SELF="${BASH_SOURCE[0]:-}"
if [ -n "$SELF" ] && [ -f "$SELF" ]; then
  ROOT="$(cd "$(dirname "$(readlink -f "$SELF")")/.." && pwd)"
else
  ROOT="$(git rev-parse --show-toplevel)"        # รันแบบ git show ... | bash จากในโปรเจกต์
fi
cd "$ROOT"

CHECK=0; REBUILD=0
for a in "$@"; do
  case "$a" in
    --check)   CHECK=1 ;;
    --rebuild) REBUILD=1 ;;
    --where)   echo "$ROOT"; exit 0 ;;
    -h|--help) sed -n '2,22p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "ไม่รู้จักตัวเลือก: $a (ดู cm-update --help)" >&2; exit 1 ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }
[ -d .git ] || { echo "$ROOT ไม่ใช่ git clone - อัปเดตด้วย git ไม่ได้ (ดู docs/INSTALL-TH.md)" >&2; exit 1; }

# รันด้วย sudo -> ใช้ git ในนามเจ้าของโฟลเดอร์ (ไฟล์ใหม่จะได้ไม่เป็นของ root)
OWNER="$(stat -c %U "$ROOT")"
GIT=(git)
if [ "$(id -u)" -eq 0 ] && [ "$OWNER" != "root" ]; then GIT=(sudo -u "$OWNER" git); fi
g() { "${GIT[@]}" "$@"; }

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
