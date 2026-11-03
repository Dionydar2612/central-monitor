#!/usr/bin/env python3
"""
cleanup-buffer.py - ลบไฟล์ log พักใน data/buffer ที่ครบอายุ (ขั้นตอนสุดท้ายของสายส่ง log)
=========================================================================================

ตำแหน่งในสายส่ง
    puller -> data/buffer/*.log -> otel -> OpenSearch -> [cleanup-buffer ลบไฟล์พัก]

    puller/otel จบหน้าที่เมื่อ log เข้า OpenSearch สำเร็จ ไฟล์ใน buffer ที่ส่งแล้ว
    ไม่มีประโยชน์อีก จึงให้สคริปต์นี้ลบแยกต่างหาก (ไม่ลบในตัว puller เพื่อให้
    ขั้นตอนดึง/ส่ง กับขั้นตอนลบ ไม่ผูกกัน)

กติกาการลบ
    - ลบไฟล์ *.log (และ *.log.tmp ที่ค้างจากการเขียนไม่เสร็จ) ที่เวลาแก้ไขเก่ากว่า
      storage.buffer_retention ใน config/puller.yaml  (ปริยาย 30m)
    - ค่า retention มีที่เดียวคือ puller.yaml (เดิมมีซ้ำใน docker-compose ด้วย -> แก้แล้ว)
    - ไม่แตะโฟลเดอร์ย่อย เช่น .otel-storage (ที่ otel เก็บตำแหน่งที่อ่านไฟล์)

ข้อควรระวัง
    เป็นการลบ "ตามเวลา" ไม่ได้เช็คว่า otel ส่งไฟล์นั้นเข้า OpenSearch แล้วจริง
    ถ้า otel/OpenSearch ล่มนานกว่า buffer_retention ไฟล์ที่ยังไม่ถูกส่งอาจถูกลบก่อน
    ถ้ากังวลเรื่องนี้ ให้เพิ่ม buffer_retention

วิธีรัน (เลือกอย่างใดอย่างหนึ่ง)
    1) Docker (ปริยาย) : service "buffer-cleaner" ใน docker/docker-compose.yml
                         รัน `cleanup-buffer.py --loop` ต่อเนื่อง ทุก storage.cleanup_interval
    2) systemd (ทางเลือก, ไม่ใช้ Docker) : docker/systemd/central-buffer-cleanup.timer
                         เรียกแบบรันครั้งเดียว (ไม่ใส่ --loop) ทุก 10 นาที
    ห้ามเปิดทั้ง 2 แบบพร้อมกัน

เชื่อมกับอะไร
    - อ่าน  : config/puller.yaml  (storage.buffer_retention, storage.cleanup_interval)
    - ลบจาก : data/buffer  (ที่ puller เขียน และ otel อ่าน)
"""

import os
import sys
import time

import yaml

CONFIG_FILE = os.environ.get("CENTRAL_CONFIG_FILE", "/app/config/puller.yaml")
BUFFER_DIR = os.environ.get("CENTRAL_BUFFER_DIR", "/data/buffer")

DEFAULT_RETENTION = "30m"
DEFAULT_CLEANUP_INTERVAL = "10m"

# นามสกุลไฟล์ที่ลบ: ไฟล์ log ที่เสร็จแล้ว และไฟล์ชั่วคราวที่ค้าง
DELETABLE_SUFFIXES = (".log", ".log.tmp")


def parse_interval(value):
    """แปลง "30s" "5m" "1h" "7d" (หรือตัวเลขล้วน) เป็นวินาที"""
    if isinstance(value, int):
        return value

    value = str(value).strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}

    if value and value[-1] in units:
        return int(value[:-1]) * units[value[-1]]

    return int(value)


def load_storage_config():
    """อ่านส่วน storage: ของ puller.yaml (อ่านใหม่ทุกรอบ -> แก้ค่าแล้วมีผลเลย)"""
    with open(CONFIG_FILE, "r") as f:
        config = yaml.safe_load(f) or {}

    return config.get("storage", {})


def cleanup_once():
    """ลบไฟล์ที่ครบอายุหนึ่งรอบ คืนจำนวนที่ลบ"""
    retention_raw = load_storage_config().get("buffer_retention", DEFAULT_RETENTION)
    cutoff = time.time() - parse_interval(retention_raw)

    if not os.path.isdir(BUFFER_DIR):
        print(f"[CLEANUP] buffer dir not found: {BUFFER_DIR}")
        return 0

    deleted = 0

    for name in os.listdir(BUFFER_DIR):
        if not name.endswith(DELETABLE_SUFFIXES):
            continue

        path = os.path.join(BUFFER_DIR, name)

        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                deleted += 1
        except FileNotFoundError:
            continue    # ถูกลบ/ย้ายไปแล้วระหว่างวนอยู่

    print(f"[CLEANUP] retention={retention_raw} deleted={deleted}")
    return deleted


def main():
    # ไม่ใส่ --loop = ลบครั้งเดียวแล้วจบ (ใช้กับ systemd timer / ทดสอบมือ)
    if "--loop" not in sys.argv:
        cleanup_once()
        return 0

    # --loop = ทำงานต่อเนื่อง (ใช้ใน container buffer-cleaner)
    while True:
        try:
            cleanup_once()
            interval_raw = load_storage_config().get(
                "cleanup_interval", DEFAULT_CLEANUP_INTERVAL
            )
        except Exception as e:
            # config อ่านไม่ได้ชั่วคราว ฯลฯ -> ไม่ให้ตัวลบตาย
            print(f"[CLEANUP ERROR] {e}")
            interval_raw = DEFAULT_CLEANUP_INTERVAL

        time.sleep(parse_interval(interval_raw))


if __name__ == "__main__":
    sys.exit(main())
