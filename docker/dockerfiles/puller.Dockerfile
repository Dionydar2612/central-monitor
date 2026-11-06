# ------------------------------------------------------------
# start_push_log image  (python + ssh client + yaml)
#
# Used by TWO services in docker-compose.yml (same image, different command):
#   central-puller : python3 /app/puller.py                       (SSH -> data/buffer)
#   buffer-cleaner : python3 /app/cleanup/cleanup-buffer.py --loop (delete expired buffer files)
#
# Build context = repo root (see docker-compose.yml)
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

# openssh-client: ให้ puller ใช้ `ssh` ไปเครื่องปลายทาง | python3-yaml: อ่าน puller.yaml
# python3-prometheus-client: เปิด /metrics ของ puller
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y \
       openssh-client \
       ca-certificates \
       python3 \
       python3-yaml \
       python3-prometheus-client \
    && rm -rf /var/lib/apt/lists/*

# โฟลเดอร์ที่ถูก mount ทับตอนรัน (docker-compose.yml): /data <- ./data, /app/config <- ./config
RUN mkdir -p /root/.ssh /data/buffer /data/state /app/config

WORKDIR /app

# puller package (puller.py, scheduler.py, ssh_client.py, log_puller.py, ...) -> /app
COPY start_push_log/puller/ /app/

# buffer cleanup script -> /app/cleanup
COPY start_push_log/cleanup/ /app/cleanup/

# คำสั่งปริยาย = puller (service buffer-cleaner ใน compose ใส่คำสั่งของตัวเองทับ)
# print() ออก docker logs ทันที (ไม่พักไว้ใน buffer -> docker logs ว่าง ตอนหาปัญหา)
ENV PYTHONUNBUFFERED=1

CMD ["python3", "/app/puller.py"]
