"""
metrics.py - ตัวชี้วัด (Prometheus metrics) ของตัว puller เอง
===========================================================

หน้าที่
    ประกาศ metric ที่ puller เปิดให้ที่ http://central-puller:8000/metrics
    ใช้ตอบคำถามว่า "การดึง log จากแต่ละเครื่องสำเร็จไหม / ล่าสุดเมื่อไหร่ /
    ดึงมาเท่าไหร่" เพื่อให้ตรวจสุขภาพของระบบดึง log ได้

เชื่อมกับอะไร
    - เขียนค่าโดย log_puller.py และ scheduler.py
    - Prometheus (metrics/prometheus/prometheus.yml, job "central-puller") มา scrape
    - healthcheck ของ container central-puller ยิง /metrics เพื่อเช็คว่าโปรเซสยังมีชีวิต
    - web_dashboard ดูสถานะ "online/offline" จาก Prometheus ไม่ได้อ่าน metric ในไฟล์นี้
      โดยตรง (ใช้ตรวจ ssh_pull_success ด้วยมือตามคู่มือ docs/USAGE-TH.md)

หมายเหตุ
    ชื่อ metric เป็น "สัญญา" กับ dashboard/คู่มือ ห้ามเปลี่ยนชื่อโดยไม่แก้เอกสาร
"""

from prometheus_client import Counter, Gauge

# --- ระดับเครื่อง (1 ค่าต่อ 1 เครื่อง) --------------------------------------

ssh_pull_success = Gauge(
    "ssh_pull_success",
    "SSH pull status: 1=success, 0=failed",
    ["target"],
)

ssh_pull_last_success_timestamp = Gauge(
    "ssh_pull_last_success_timestamp",
    "Unix timestamp of last successful SSH pull",
    ["target"],
)

ssh_pull_duration_seconds = Gauge(
    "ssh_pull_duration_seconds",
    "Duration of the last SSH pull cycle",
    ["target"],
)

ssh_pull_errors_total = Counter(
    "ssh_pull_errors_total",
    "Total SSH pull errors",
    ["target"],
)

ssh_pull_sources_total = Gauge(
    "ssh_pull_sources_total",
    "Number of configured log sources",
    ["target"],
)

# --- ระดับ source (1 ค่าต่อ 1 ชนิด log ของ 1 เครื่อง) ----------------------

ssh_pull_source_success = Gauge(
    "ssh_pull_source_success",
    "Log source pull status: 1=success, 0=failed",
    ["target", "source"],
)

ssh_pull_bytes_total = Counter(
    "ssh_pull_bytes_total",
    "Total bytes pulled",
    ["target", "source"],
)

ssh_pull_records_total = Counter(
    "ssh_pull_records_total",
    "Total log records pulled",
    ["target", "source"],
)

# --- ระดับรอบการทำงาน -------------------------------------------------------

pull_cycle_timestamp = Gauge(
    "pull_cycle_timestamp",
    "Unix timestamp of the last completed pull cycle",
)
