"""
ssh_client.py - ส่วนที่ "คุยกับเครื่องปลายทาง" ผ่าน SSH
=======================================================

หน้าที่
    รันคำสั่งบนเครื่อง web ผ่าน SSH และคืนผลลัพธ์ ทุกการติดต่อกับเครื่อง
    ปลายทางของ puller ผ่านไฟล์นี้ที่เดียว (log_puller.py และ system_info.py
    เรียกใช้ ไม่เรียก ssh เองโดยตรง)

ความปลอดภัยที่ตั้งไว้
    - StrictHostKeyChecking=yes : ต้องมี host key ของเครื่องใน keys/known_hosts
      ก่อน (ssh-keyscan) กัน man-in-the-middle
    - BatchMode=yes             : ห้ามถามรหัสผ่าน ใช้ key เท่านั้น
    - ConnectTimeout=10         : เครื่องปลายทางไม่ตอบ จะไม่ค้างตลอดไป
    - ControlMaster / ControlPersist : ใช้การเชื่อมต่อ SSH เดิมซ้ำ 5 นาที
      ดึง log ถี่ (ทุก 2 นาที) แล้วไม่ต้อง login ใหม่ทุกคำสั่ง -> เครื่องปลายทางไม่โดน SSH ถล่ม,
      auth.log ไม่ท่วม, เร็วขึ้นมาก  (ตั้ง CENTRAL_SSH_MUX=0 เพื่อปิด)
    - ฝั่งปลายทางควรเป็น user read-only (central-pull) ดู docs/INSTALL-TH.md ข้อ 6

เชื่อมกับอะไร
    - keys/central_ed25519 + keys/known_hosts  (mount เข้า container ที่ /run/keys)
    - config/puller.yaml : targets[].host, targets[].ssh.user, targets[].ssh.port
    - ใช้โดย log_puller.py (stat / tail / find) และ system_info.py (cat /proc/..)
"""

import os
import subprocess
import tempfile

from config import SSH_KEY_PATH, SSH_KNOWN_HOSTS

SSH_MUX = os.environ.get("CENTRAL_SSH_MUX", "1") != "0"
# socket ของ ControlMaster (path ต้องสั้น: unix socket จำกัด ~100 ตัวอักษร)
MUX_DIR = os.path.join(tempfile.gettempdir(), "cm-ssh")


def _build_command(target, remote_command):
    """ประกอบคำสั่ง `ssh ... user@host '<remote_command>'`"""
    ssh = target["ssh"]

    cmd = [
        "ssh",
        "-i", SSH_KEY_PATH,
        "-p", str(ssh.get("port", 22)),
        "-o", "StrictHostKeyChecking=yes",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
    ]

    if SSH_KNOWN_HOSTS:
        cmd += ["-o", f"UserKnownHostsFile={SSH_KNOWN_HOSTS}"]

    if SSH_MUX:
        os.makedirs(MUX_DIR, mode=0o700, exist_ok=True)
        cmd += [
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={MUX_DIR}/%C",
            "-o", "ControlPersist=300",
            "-o", "ServerAliveInterval=30",
        ]

    cmd += [f"{ssh['user']}@{target['host']}", remote_command]

    return cmd


def run_ssh_bytes(target, remote_command):
    """รันคำสั่งบนเครื่องปลายทาง คืน stdout เป็น bytes

    ใช้ตอนดึงเนื้อหา log (ต้องคง byte ตรงตัว ห้ามแปลง encoding)
    ถ้า ssh จบด้วย exit code != 0 จะ raise RuntimeError พร้อมข้อความ stderr
    """
    result = subprocess.run(
        _build_command(target, remote_command),
        capture_output=True,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())

    return result.stdout


def run_ssh(target, remote_command):
    """เหมือน run_ssh_bytes แต่คืนเป็นข้อความ (str)

    ใช้กับคำสั่งที่ผลลัพธ์เป็นตัวหนังสือ เช่น stat, find, cat /proc/loadavg
    ถ้ามี byte ที่ decode UTF-8 ไม่ได้ จะแทนด้วย "?" แทนที่จะ error ทั้งก้อน
    """
    return run_ssh_bytes(target, remote_command).decode(errors="replace")


def remote_stat(target, path):
    """ขนาดไฟล์ (bytes) ของ path บนเครื่องปลายทาง

    ใช้เทียบกับ checkpoint: ขนาดมากกว่า = มี log ใหม่, น้อยกว่า = ไฟล์ถูก rotate
    """
    output = run_ssh(target, f"stat -c '%s' -- '{path}'").strip()
    return int(output)


def list_remote_files(target, path_pattern):
    """หาไฟล์ที่ตรงกับ wildcard เช่น /logs/logapp/*.log บนเครื่องปลายทาง

    ค้นเฉพาะชั้นเดียว (maxdepth 1) เฉพาะไฟล์ปกติ คืนรายชื่อที่เรียงแล้ว
    ใช้เมื่อ path ใน puller.yaml มี * ? หรือ [
    """
    directory = os.path.dirname(path_pattern) or "."
    pattern = os.path.basename(path_pattern)

    command = (
        f"find -- '{directory}' "
        f"-maxdepth 1 "
        f"-type f "
        f"-name '{pattern}' "
        f"-print"
    )

    output = run_ssh(target, command)

    files = sorted(line.strip() for line in output.splitlines() if line.strip())

    print(f"[WILDCARD] pattern={path_pattern} files={len(files)}")

    for file_path in files:
        print(f"[WILDCARD FILE] {file_path}")

    return files
