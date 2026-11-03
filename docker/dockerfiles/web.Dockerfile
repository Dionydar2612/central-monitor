# ------------------------------------------------------------
# web_dashboard image  (Flask API + หน้าเว็บ เสิร์ฟด้วย gunicorn)
#
# ใช้โดย service "central-api" ใน docker/docker-compose.yml
# แพ็กเกจที่ลง: python3-flask + gunicorn (เว็บ), python3-yaml (อ่าน puller.yaml),
#               python3-ruamel.yaml (หน้า Settings แก้ puller.yaml โดยคงคอมเมนต์),
#               openssh-client (ssh-keyscan/ssh-keygen ให้หน้า Settings เก็บ host key),
#               curl (ให้ healthcheck ยิง /health)
# ไม่ต้องมี prometheus-client เพราะฝั่งนี้ไม่ได้ดึงข้อมูลจากเครื่องอื่น
#
# Build context = โฟลเดอร์รากของโปรเจกต์ (ตั้งใน docker-compose.yml)
# ------------------------------------------------------------
FROM ubuntu:24.04

# รันด้วย uid/gid เดียวกับเจ้าของไฟล์บน host (ส่งมาจาก docker/.env ผ่าน build args)
# -> ไฟล์ที่ container สร้างใน data/ และ config/ เป็นของ user จริง ไม่ใช่ root
# ssh ต้องมี passwd entry ของ uid ที่รัน จึงสร้าง user ให้ (ลบ user "ubuntu" uid 1000 ของ image ก่อนกันชน)
ARG HOST_UID=1000
ARG HOST_GID=1000
RUN set -eu; \
    userdel -r ubuntu 2>/dev/null || true; \
    groupdel ubuntu 2>/dev/null || true; \
    if [ "$HOST_UID" != "0" ]; then \
      getent group "$HOST_GID" >/dev/null || groupadd -g "$HOST_GID" cmgroup; \
      getent passwd "$HOST_UID" >/dev/null || useradd -u "$HOST_UID" -g "$HOST_GID" -M -d /tmp -s /usr/sbin/nologin cmuser; \
    fi

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y \
       curl \
       ca-certificates \
       python3 \
       python3-yaml \
       python3-ruamel.yaml \
       openssh-client \
       python3-flask \
       gunicorn \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /data/state /app/config /app/static

WORKDIR /app

# backend: api.py + settings/auth/clients/metrics_service/logs_service/system_state/timeutil -> /app
COPY web_dashboard/backend/ /app/
# frontend: index.html, login.html, dashboard.css, dashboard.js -> /app/static
COPY web_dashboard/frontend/ /app/static/

# คำสั่งจริงถูกกำหนดใน docker-compose.yml (workers/threads/timeout) ค่านี้เป็นค่าสำรอง
CMD ["gunicorn", "--chdir", "/app", "--bind", "0.0.0.0:8000", "api:app"]
