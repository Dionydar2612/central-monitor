#!/usr/bin/env bash
# test.sh - รันเทสต์ทั้งหมด (ไม่แตะระบบจริง: ใช้โฟลเดอร์ชั่วคราว)
#   ./scripts/test.sh            บนเครื่อง (ต้องมี python3 + pyyaml; flask/ruamel ไม่มีจะข้ามเทสต์ฝั่งเว็บ)
#   ./scripts/test.sh docker     รันใน image ของ puller + central-api (มีไลบรารีครบ)
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${1:-}" = "docker" ]; then
  docker run --rm -v "$PWD:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 central-monitor-puller:local \
    python3 -m unittest tests.test_puller -v
  docker run --rm -v "$PWD:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python3 central-monitor-central-api \
    -m unittest tests.test_backend -v
else
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
fi
