"""
puller.py - จุดเริ่มต้นของ central-puller (ระบบดึงข้อมูลจากเครื่องอื่น)
=======================================================================

รันด้วย:  python3 /app/puller.py   (docker/docker-compose.yml service "central-puller")

ไฟล์นี้บางโดยตั้งใจ ทำแค่ 3 อย่าง:
    1. เปิด HTTP server พอร์ต 8000 ให้ Prometheus มาอ่าน /metrics ของ puller
    2. เปิด HTTP ภายในพอร์ต 8001 ให้ปุ่ม "Load INFO log" ของหน้าเว็บ (ondemand.py)
    3. เริ่ม scheduler (วนดึงข้อมูลเป็นรอบ ๆ)

โครงของ puller (แต่ละไฟล์ทำหน้าที่เดียว แก้เรื่องไหนไปไฟล์นั้น)

    puller.py              จุดเริ่มต้น (ไฟล์นี้)
    scheduler.py           ควบคุมรอบ: interval, stagger, ลำดับขั้นตอน
    config.py              path ต่าง ๆ + อ่าน puller.yaml + แปลง "5m"/"1h" เป็นวินาที
    ssh_client.py          ช่องทาง SSH ไปเครื่องปลายทาง (stat, tail, find, cat)
    log_puller.py          ดึง log -> data/buffer  (ส่งต่อให้ otel) ตัด INFO ทิ้งก่อนเก็บ
    log_filter.py          แยก log เป็นรายการ + คัดระดับ (log_levels.py)
    journal.py             อ่าน systemd journal (logs[].path = journal / journal:<unit>)
    ondemand.py            ดึง INFO สดเฉพาะตอนกดปุ่มบนหน้าเว็บ (ไม่เก็บ)
    checkpoint.py          จำ byte offset ที่ดึงแล้ว (data/state/checkpoints.json)
    system_info.py         เก็บ load/memory/service/process/event + history
    prometheus_targets.py  สร้าง data/prometheus/web-nodes.json ให้ Prometheus
    metrics.py             ตัวชี้วัดสุขภาพของ puller เอง (/metrics)

ข้อมูลไหลไปไหน
    เครื่อง web --SSH--> log_puller --> data/buffer/*.log --> otel --> OpenSearch
    เครื่อง web --SSH--> system_info --> data/state/system/*.json --> web_dashboard
    puller.yaml --> prometheus_targets --> data/prometheus/web-nodes.json --> Prometheus
"""

from prometheus_client import start_http_server

from config import METRICS_PORT
from ondemand import start_server as start_ondemand_server
from scheduler import run_forever


def main():
    print("[START] central-puller")

    # เปิด /metrics ก่อนเริ่มดึง เพื่อให้ healthcheck ผ่านแม้รอบแรกยังไม่จบ
    start_http_server(METRICS_PORT)
    print(f"[METRICS] http://0.0.0.0:{METRICS_PORT}/metrics")

    try:
        start_ondemand_server()
    except Exception as e:      # เปิดพอร์ตไม่ได้ -> ปุ่ม Load INFO log ใช้ไม่ได้ แต่การดึงปกติยังทำงาน
        print(f"[ONDEMAND ERROR] {e}")

    run_forever()


if __name__ == "__main__":
    main()
