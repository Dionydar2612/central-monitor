"""
prometheus_targets.py - สร้างรายชื่อเครื่องให้ Prometheus scrape
================================================================

หน้าที่
    อ่าน config/puller.yaml แล้วสร้างไฟล์ data/prometheus/web-nodes.json
    ในรูปแบบ "file_sd" ของ Prometheus ทำให้เพิ่ม/ลบเครื่องที่ puller.yaml
    ที่เดียว แล้ว Prometheus จะสแกนเครื่องนั้นให้อัตโนมัติ (ไม่ต้องแก้
    prometheus.yml)

ตัวอย่างผลลัพธ์
    [{"targets": ["10.0.10.11:9100"],
      "labels": {"job": "web-01-node", "web": "web-01", "environment": "production"}}]

    label "web" คือชื่อเครื่องใน puller.yaml -> dashboard ใช้ค้นหา `up{web="web-01"}`
    label "job" + target(instance) -> dashboard ใช้ query metric ของเครื่องนั้น

เชื่อมกับอะไร
    - เรียกจาก scheduler.py ก่อนเริ่มดึงทุกรอบ (หรือรันเดี่ยว: python3 prometheus_targets.py)
    - อ่าน  : config/puller.yaml  (targets[].name และ targets[].prometheus.{job,instance})
    - เขียน : data/prometheus/web-nodes.json
              data/prometheus/ping.json   (ทุกเครื่องที่มี host รวม Windows agent)
              -> job "ping" ส่งให้ blackbox_exporter ping (ICMP) วัด latency / packet loss
                 label cm_target="<ชื่อเครื่อง>"
              -> Prometheus (metrics/prometheus/prometheus.yml, job "web-nodes")
                 อ่านไฟล์นี้ผ่าน file_sd_configs และรีโหลดเองเมื่อไฟล์เปลี่ยน
    - ใช้เฉพาะโหมด bundled Prometheus; โหมด external ให้ตั้ง scrape เองตาม
      docs/INSTALL-TH.md ข้อ 5.2
"""

import json
import os

import yaml

from config import CONFIG_FILE

TARGET_FILE = os.environ.get(
    "CENTRAL_PROMETHEUS_TARGET_FILE", "/data/prometheus/web-nodes.json"
)
PING_FILE = os.path.join(os.path.dirname(TARGET_FILE), "ping.json")


def _atomic_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def generate_targets():
    """สร้าง web-nodes.json จาก puller.yaml (เขียนแบบ atomic)"""
    with open(CONFIG_FILE, "r") as f:
        config = yaml.safe_load(f) or {}

    environment = config.get("site", {}).get("environment", "production")
    targets = []
    pings = []

    for web in config.get("targets", []):
        host = str(web.get("host") or "").strip()
        if web.get("name") and host and not web.get("paused"):
            pings.append({"targets": [host], "labels": {"cm_target": web["name"], "environment": environment}})
        if str(web.get("type") or "ssh") == "agent":
            continue          # agent ส่ง metrics เข้ามาเอง (remote write)
        name = web.get("name")
        prometheus = web.get("prometheus", {})

        job = prometheus.get("job")
        instance = prometheus.get("instance")

        # ต้องมีครบทั้ง 3 ค่า ไม่งั้นข้ามเครื่องนั้น (ไม่มี metrics ให้ดู)
        if not name or not job or not instance:
            continue

        targets.append({
            "targets": [instance],
            "labels": {"job": job, "web": name, "environment": environment},
        })

    _atomic_json(TARGET_FILE, targets)
    _atomic_json(PING_FILE, pings)

    print(f"[PROMETHEUS] Generated {len(targets)} targets, {len(pings)} ping targets")


if __name__ == "__main__":
    generate_targets()
