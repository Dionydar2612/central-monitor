"""
scheduler.py - ตัวควบคุมรอบการทำงานของ puller
=============================================

หน้าที่
    วนทำงานเป็นรอบ ๆ ตาม scheduler.interval ใน puller.yaml (ปริยาย 1h)
    แต่ละรอบทำตามลำดับ:

      1. อ่าน puller.yaml ใหม่ (แก้ config แล้วมีผลรอบถัดไปโดยไม่ต้อง restart)
      2. สร้าง web-nodes.json ให้ Prometheus      (prometheus_targets.py)
      3. วนทีละเครื่อง:
           a. ดึง log                              (log_puller.pull_target)
           b. เก็บสถานะเครื่อง + history           (system_info.collect_web_system_info)
           c. พัก scheduler.target_stagger ก่อนเครื่องถัดไป (กระจายโหลดไม่ให้ยิงพร้อมกัน)
      4. บันทึกเวลาจบรอบ (metric pull_cycle_timestamp)
      5. หลับจนครบ interval

    ถ้ารอบใดพัง (เช่น อ่าน config ไม่ได้) จะแค่ log error แล้วลองใหม่รอบหน้า
    โปรเซสไม่ตาย

เชื่อมกับอะไร
    - config.py, checkpoint.py, log_puller.py, system_info.py, prometheus_targets.py
    - metrics.py (pull_cycle_timestamp)
    - ถูกเริ่มจาก puller.py
"""

import os
import time

from checkpoint import load_state, prune_state
from config import BUFFER_DIR, load_config, parse_interval
from log_puller import pull_target
from metrics import pull_cycle_timestamp
from prometheus_targets import generate_targets
from system_info import collect_web_system_info, prune_orphan_systems
from trigger import take_pull_request

DEFAULT_INTERVAL = "1h"


def sync_prometheus_targets():
    """สร้าง web-nodes.json (ล้มเหลวได้ ไม่กระทบการดึง log)"""
    try:
        generate_targets()
    except Exception as e:
        print(f"[PROMETHEUS ERROR] {e}")


def cleanup_removed_targets(state, targets):
    """ลบข้อมูลค้างของเครื่องที่ไม่อยู่ใน puller.yaml แล้ว (ไม่ให้ error จากตรงนี้กระทบการดึง)"""
    names = [t.get("name") for t in targets if t.get("name")]

    try:
        dropped = prune_state(state, names)
        removed = prune_orphan_systems(names)

        if dropped or removed:
            print(f"[CLEANUP] removed targets: checkpoints={dropped} system={removed}")

    except Exception as e:
        print(f"[CLEANUP ERROR] {e}")


def run_once(config, only=None):
    """ดึง log + เก็บสถานะ ครบทุกเครื่องหนึ่งรอบ

    only = list ชื่อเครื่อง -> ดึงเฉพาะเครื่องเหล่านั้นทันที ไม่พัก stagger
           (มาจากปุ่มบันทึกในหน้า Settings ผ่าน trigger.py)
    """
    os.makedirs(BUFFER_DIR, exist_ok=True)

    state = load_state()

    print("\n========================================")
    print(" CENTRAL LOG PULLER")
    print(" INCREMENTAL + CHECKPOINT + RETRY")
    print("========================================")

    targets = config.get("targets", [])
    stagger = parse_interval(config.get("scheduler", {}).get("target_stagger", 0))

    # รอบเต็ม: ล้างข้อมูลของเครื่องที่ถูกลบออกจาก puller.yaml แล้ว (checkpoint + snapshot + history)
    # ข้ามเมื่อไม่มีเครื่องเลย กันลบหมดเพราะ config ที่เพิ่งแก้พัง/ว่างชั่วคราว
    if only is None and targets:
        cleanup_removed_targets(state, targets)

    if only is not None:
        targets = [t for t in targets if t.get("name") in only]
        stagger = 0
        print(f" (on-demand: {', '.join(t.get('name') for t in targets) or 'none'})")

    # เครื่องที่ pause (หน้าเว็บ / puller.yaml -> paused: true) ไม่ดึง log ไม่เก็บสถานะ
    paused = [t.get("name") for t in targets if t.get("paused")]
    if paused:
        print(f"[PAUSED] skip: {', '.join(paused)}")
    targets = [t for t in targets if not t.get("paused")]

    for index, target in enumerate(targets):

        pull_target(config, target, state)

        try:
            collect_web_system_info(config, target)
        except Exception as e:
            print(f"[SYSTEM ERROR] {target.get('name')} {e}")

        is_last = index == len(targets) - 1

        if stagger > 0 and not is_last:
            print(f"[STAGGER] waiting {stagger}s before next target")
            time.sleep(stagger)

    pull_cycle_timestamp.set(time.time())

    print("\n[PULL] cycle completed")


def wait_for_next_cycle(deadline):
    """รอจนถึง deadline  ระหว่างรอ เช็คคำสั่งจากหน้า Settings ทุก 2 วินาที

    คืน list ชื่อเครื่องที่ถูกสั่งให้ดึงทันที หรือ None เมื่อถึงเวลารอบปกติ
    """
    while True:
        remaining = deadline - time.time()

        if remaining <= 0:
            return None

        requested = take_pull_request()

        if requested:
            return requested

        time.sleep(min(2, remaining))


def run_forever():
    """วนรอบไม่สิ้นสุด (เรียกจาก puller.main)"""
    interval_text = DEFAULT_INTERVAL
    only = None          # None = รอบปกติ (ทุกเครื่อง), list = ดึงเฉพาะเครื่องที่ถูกสั่ง
    deadline = 0

    while True:
        try:
            config = load_config()
            interval_text = config.get("scheduler", {}).get("interval", DEFAULT_INTERVAL)

            sync_prometheus_targets()
            run_once(config, only=only)

        except Exception as e:
            print(f"[ERROR] pull cycle failed: {e}")

        if only is None:
            # รอบปกติจบ -> นับ interval ใหม่  (รอบ on-demand ไม่รีเซ็ตเวลารอบปกติ)
            try:
                interval = parse_interval(interval_text)
            except Exception:
                interval = parse_interval(DEFAULT_INTERVAL)

            deadline = time.time() + interval
            print(f"[SLEEP] waiting {interval}s")

        only = wait_for_next_cycle(deadline)
