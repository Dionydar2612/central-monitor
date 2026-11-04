"""
scheduler.py - ตัวควบคุมรอบการทำงานของ puller
=============================================

2 จังหวะแยกกัน (ตั้งใน puller.yaml -> scheduler:)
    log_interval: 2m     ดึง log ใหม่ (เกือบ real-time: log เข้าหน้าเว็บช้าไม่เกิน ~log_interval + เวลา otel)
    interval: 1h         เก็บสถานะเครื่อง (service / process / event / load ...) + history
    parallel: 4          ทำพร้อมกันกี่เครื่อง

ทำไมแยก
    สถานะเครื่องเป็นงานหนัก (หลายคำสั่ง + วัด CPU 1 วินาที + เขียนไฟล์ history ทุกครั้ง)
    ถ้าทำถี่เท่า log ไฟล์ history จะท่วม (2 นาที x 7 วัน = 5,000 ไฟล์ต่อเครื่อง)
    log เป็นงานเบา (stat + tail ส่วนที่เพิ่ม) และใช้การเชื่อมต่อ SSH เดิมซ้ำ (ssh_client: ControlMaster)

แต่ละรอบ
    1. อ่าน puller.yaml ใหม่ (แก้ config แล้วมีผลรอบถัดไปโดยไม่ต้อง restart)
    2. สร้าง web-nodes.json ให้ Prometheus      (prometheus_targets.py)
    3. ทุกเครื่องที่ไม่ pause พร้อมกันสูงสุด parallel เครื่อง:
           ดึง log (log_puller.pull_target)  +  ถ้าถึงรอบ: เก็บสถานะเครื่อง (system_info)
    4. บันทึกเวลาจบรอบ (metric pull_cycle_timestamp)
    5. รอจนถึงรอบถัดไป ระหว่างรอ เช็คคำสั่ง "ดึงเครื่องนี้เดี๋ยวนี้" จากหน้า Settings ทุก 2 วินาที

    ถ้ารอบใดพัง (เช่น อ่าน config ไม่ได้) จะแค่ log error แล้วลองใหม่รอบหน้า โปรเซสไม่ตาย
    scheduler.target_stagger (รุ่นเก่า) ไม่ใช้แล้ว: ดึงพร้อมกันแบบจำกัดจำนวนแทน
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor

from checkpoint import load_state, prune_state
from config import BUFFER_DIR, load_config, parse_interval
from log_puller import pull_target
from metrics import pull_cycle_timestamp
from prometheus_targets import generate_targets
from system_info import collect_web_system_info, prune_orphan_systems
from trigger import take_pull_request

DEFAULT_INTERVAL = "1h"          # สถานะเครื่อง
DEFAULT_LOG_INTERVAL = "2m"      # log
DEFAULT_PARALLEL = 4
MIN_LOG_INTERVAL = 30


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


def _settings(config):
    sched = config.get("scheduler", {}) or {}

    def seconds(key, default):
        try:
            return parse_interval(sched.get(key, default))
        except Exception:
            return parse_interval(default)

    try:
        parallel = max(1, min(int(sched.get("parallel", DEFAULT_PARALLEL)), 32))
    except (TypeError, ValueError):
        parallel = DEFAULT_PARALLEL

    return {
        "snapshot": max(seconds("interval", DEFAULT_INTERVAL), 60),
        "log": max(seconds("log_interval", DEFAULT_LOG_INTERVAL), MIN_LOG_INTERVAL),
        "parallel": parallel,
    }


def _work(config, target, state, with_snapshot):
    name = target.get("name")
    try:
        pull_target(config, target, state)
    except Exception as e:
        print(f"[PULL ERROR] {name} {e}")

    if with_snapshot:
        try:
            collect_web_system_info(config, target)
        except Exception as e:
            print(f"[SYSTEM ERROR] {name} {e}")


def run_once(config, state, only=None, snapshot=True):
    """1 รอบ: ดึง log ทุกเครื่อง (+ สถานะเครื่องถ้า snapshot=True)

    only = list ชื่อเครื่อง -> เฉพาะเครื่องเหล่านั้น (ปุ่มบันทึกในหน้า Settings) ทำทั้ง log + สถานะ
    """
    os.makedirs(BUFFER_DIR, exist_ok=True)
    opts = _settings(config)
    targets = [t for t in config.get("targets", []) if isinstance(t, dict) and t.get("name")]

    # รอบสถานะเต็ม: ล้างข้อมูลของเครื่องที่ถูกลบออกจาก puller.yaml (ข้ามเมื่อไม่มีเครื่องเลย กันลบหมดเพราะ config พัง)
    if only is None and snapshot and targets:
        cleanup_removed_targets(state, targets)

    if only is not None:
        targets = [t for t in targets if t.get("name") in only]
        snapshot = True

    paused = [t["name"] for t in targets if t.get("paused")]
    targets = [t for t in targets if not t.get("paused")]

    label = "logs + status" if snapshot else "logs"
    print(f"\n[CYCLE] {label} targets={len(targets)} parallel={opts['parallel']}"
          + (f" paused={','.join(paused)}" if paused else "")
          + (f" on-demand={','.join(only)}" if only else ""))

    started = time.time()
    with ThreadPoolExecutor(max_workers=opts["parallel"]) as pool:
        for t in targets:
            pool.submit(_work, config, t, state, snapshot)

    pull_cycle_timestamp.set(time.time())
    print(f"[CYCLE] done in {time.time() - started:.1f}s")


def run_forever():
    """วนรอบไม่สิ้นสุด (เรียกจาก puller.main)"""
    state = load_state()
    next_log = 0.0
    next_snapshot = 0.0
    only = None

    while True:
        try:
            config = load_config()
            opts = _settings(config)
            now = time.time()

            sync_prometheus_targets()

            if only:
                run_once(config, state, only=only)
            else:
                snapshot = now >= next_snapshot
                run_once(config, state, snapshot=snapshot)
                next_log = time.time() + opts["log"]
                if snapshot:
                    next_snapshot = now + opts["snapshot"]

        except Exception as e:
            print(f"[ERROR] cycle failed: {e}")
            next_log = time.time() + MIN_LOG_INTERVAL

        only = None
        # รอถึงรอบ log ถัดไป ระหว่างรอ เช็คคำสั่งดึงทันทีจากหน้า Settings ทุก 2 วินาที
        while True:
            remaining = next_log - time.time()
            if remaining <= 0:
                break
            requested = take_pull_request()
            if requested:
                only = requested
                break
            time.sleep(min(2, remaining))
