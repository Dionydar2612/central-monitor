"""
log_puller.py - ดึง log จากเครื่อง web มาวางเป็นไฟล์ใน data/buffer
==================================================================

ภาพรวมของ "1 รอบการดึง log ของ 1 ไฟล์" (pull_log)
    1. อ่าน checkpoint  -> เคยดึงถึง byte ที่เท่าไหร่
    2. SSH `stat`       -> ตอนนี้ไฟล์ปลายทางขนาดเท่าไหร่
         ขนาด < checkpoint  = ไฟล์ถูก rotate  -> เริ่มใหม่จาก 0
         ขนาด = checkpoint  = ไม่มี log ใหม่   -> จบ
    3. SSH `tail -c +N | head -c M` -> ดึงเฉพาะส่วนที่โตขึ้น
    4. ตัดให้เหลือเฉพาะ "บรรทัดที่จบสมบูรณ์" (ไม่ส่งบรรทัดที่เขียนค้างอยู่)
    5. ตัด log ระดับ INFO ทิ้ง (log_filter.drop_info) -> INFO ไม่ถูกเก็บ/ไม่เข้า OpenSearch
       (ถ้าเปิด storage.collapse_repeats: ยุบรายการซ้ำเกิน keep เป็นบรรทัดสรุป log_filter.collapse_repeats)
       (ดู INFO ได้จากปุ่ม "Load INFO log" ที่หน้าเว็บ ซึ่งดึงสดเฉพาะตอนสั่ง -> ondemand.py)
    6. เขียนไฟล์ใน data/buffer แบบ atomic (.tmp -> rename)  (ถ้าเหลือ 0 รายการ ไม่เขียนไฟล์)
    7. อัปเดต checkpoint (หลังเขียนไฟล์สำเร็จเท่านั้น -> ไม่มี log หาย)

journal (logs[].path = journal หรือ journal:<unit>)
    ใช้ pull_journal แทน: จำตำแหน่งด้วย cursor ของ journal แทน byte offset (ดู journal.py)

ชื่อไฟล์ใน buffer (otel เลือกอ่านตามชื่อนี้ ห้ามเปลี่ยนรูปแบบ)
    <site>__<เครื่อง>__<source>__<เวลา UTC>__<offsetเริ่ม>-<offsetจบ>__<pull_id>.log
    ตัวอย่าง: production__web-01__logapp__20261002T013000Z__0-5120__ab12cd...log
    ส่วน <source> คือ logs[].name ใน puller.yaml -> otel/otelcol.yaml ใช้ glob
    `*__<source>__*.log` เพื่อเลือก parser

หน้าที่รองในไฟล์นี้
    - retry เมื่อดึงไม่สำเร็จ (retry.max_attempts / delay_seconds)
    - ขยาย wildcard (/logs/logapp/*.log) เป็นรายไฟล์ แต่ละไฟล์มี checkpoint ของตัวเอง
    - บันทึก metric สำเร็จ/ล้มเหลว (metrics.py)

เชื่อมกับอะไร
    - ssh_client.py : คุยกับเครื่องปลายทาง
    - checkpoint.py : จำตำแหน่งที่ดึงแล้ว
    - metrics.py    : รายงานผลให้ Prometheus
    - ผลลัพธ์ (ไฟล์ใน data/buffer) -> otel (start_push_log/otel/otelcol.yaml)
    - ไฟล์ที่ครบอายุถูกลบโดย start_push_log/cleanup/cleanup-buffer.py
"""

import hashlib
import os
import time
from datetime import datetime, timezone

import journal
from checkpoint import save_state
from config import BUFFER_DIR, parse_interval
from events import append_events
from log_filter import collapse_config, collapse_repeats, drop_info, store_info_enabled
from metrics import (
    ssh_pull_bytes_total,
    ssh_pull_duration_seconds,
    ssh_pull_errors_total,
    ssh_pull_last_success_timestamp,
    ssh_pull_records_total,
    ssh_pull_source_success,
    ssh_pull_sources_total,
    ssh_pull_success,
)
from ssh_client import list_remote_files, remote_stat, run_ssh, run_ssh_bytes

WILDCARD_CHARS = ("*", "?", "[")


# ---------------------------------------------------------------------------
# ตัวช่วยตั้งชื่อ
# ---------------------------------------------------------------------------

def make_source_id(target, log_source):
    """คีย์ของ checkpoint: <เครื่อง>::<source>::<path จริงของไฟล์>"""
    return f"{target['name']}::{log_source['name']}::{log_source['path']}"


def make_pull_id(source_id, start_offset, end_offset):
    """id ประจำก้อน (hash ของ source + ช่วง offset) ใช้ปิดท้ายชื่อไฟล์ใน buffer

    ก้อนเดียวกัน (ช่วง offset เดียวกัน) จะได้ id เดิมเสมอ ช่วยตรวจซ้ำย้อนหลังได้
    """
    raw = f"{source_id}|{start_offset}|{end_offset}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _write_buffer(config, target_name, source_name, source_id, start_offset, end_offset, data):
    """เขียนก้อน log ลง data/buffer แบบ atomic คืน (path, pull_id)"""
    site = config["site"]["name"]
    pull_id = make_pull_id(source_id, start_offset, end_offset)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    filename = (
        f"{site}__"
        f"{target_name.replace('/', '_')}__"
        f"{source_name.replace('/', '_')}__"
        f"{timestamp}__"
        f"{start_offset}-{end_offset}__"
        f"{pull_id}.log"
    )

    output_file = os.path.join(BUFFER_DIR, filename)
    tmp_file = output_file + ".tmp"

    # เขียน .tmp -> fsync -> rename : otel จะไม่เห็นไฟล์ที่เขียนไม่เสร็จ
    # (otel อ่านเฉพาะ *.log ไม่อ่าน *.tmp)
    with open(tmp_file, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_file, output_file)
    return output_file, pull_id


# ---------------------------------------------------------------------------
# ดึง journal (systemd) 1 รายการ
# ---------------------------------------------------------------------------

def _collapse(config, data, target_name, source_name):
    """ยุบรายการซ้ำ (ปริยายปิด: storage.collapse_repeats.enabled) บันทึกจำนวนลง System log ให้ตรวจได้
    คืน (data, จำนวนที่ถูกยุบ)"""
    enabled, keep = collapse_config(config)
    if not enabled or not data:
        return data, 0
    data, collapsed, kinds = collapse_repeats(data, keep)
    if collapsed:
        print(f"[COLLAPSE] {target_name}/{source_name} collapsed={collapsed} kinds={len(kinds)} keep={keep}")
        try:
            append_events([{"source": "puller", "level": "info", "web": target_name,
                            "message": f"ยุบ log ซ้ำ {collapsed} รายการ ({len(kinds)} ชนิด) ของ {source_name} "
                                       f"เก็บชนิดละไม่เกิน {keep} รายการ + บรรทัดสรุป (storage.collapse_repeats)"}])
        except Exception as e:
            print(f"[COLLAPSE] write event failed: {e!r}")
    return data, collapsed


def pull_journal(config, target, log_source, state):
    """ดึง journal ต่อจาก cursor เดิม -> data/buffer (ไม่เก็บ INFO ถ้าไม่ได้เปิด store_info_logs)"""
    target_name = target["name"]
    source_name = log_source["name"]
    path = log_source["path"]
    source_id = make_source_id(target, log_source)

    previous = state.get(source_id, {})
    cursor = previous.get("cursor")
    store_info = store_info_enabled(config)

    since = None
    if not cursor:
        interval = config.get("scheduler", {}).get("interval", "1h")
        since = time.time() - parse_interval(interval)

    print(f"[PULL] {target_name}/{source_name} journal cursor={'yes' if cursor else 'none'}")

    command = journal.build_command(
        path,
        cursor=cursor,
        since=since,
        priority=None if store_info else journal.STORE_PRIORITY,
        limit=journal.MAX_ENTRIES_PER_PULL,
    )
    entries = journal.parse_output(run_ssh(target, command))
    entries = [e for e in entries if e.get("cursor")]

    if not entries:
        print(f"[NO CHANGE] {source_id}")
        return

    start_offset = int(previous.get("offset", 0))
    end_offset = start_offset + len(entries)

    lines = [e["line"] for e in entries if store_info or e["level"] != "INFO"]
    data = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    data, _ = _collapse(config, data, target_name, source_name)

    output_file, pull_id = None, previous.get("last_pull_id")
    if data:
        output_file, pull_id = _write_buffer(
            config, target_name, source_name, source_id, start_offset, end_offset, data
        )

    state[source_id] = {
        "target": target_name,
        "source": source_name,
        "path": path,
        "cursor": entries[-1]["cursor"],
        "offset": end_offset,
        "last_pull_id": pull_id,
        "last_file": output_file or previous.get("last_file"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)

    ssh_pull_bytes_total.labels(target=target_name, source=source_name).inc(len(data))
    ssh_pull_records_total.labels(target=target_name, source=source_name).inc(len(lines))

    print(f"[OK] journal {target_name}/{source_name} entries={len(entries)} stored={len(lines)}")


# ---------------------------------------------------------------------------
# ดึง 1 ไฟล์
# ---------------------------------------------------------------------------

def pull_log(config, target, log_source, state):
    """ดึง log ส่วนที่โตขึ้นของไฟล์เดียว แล้วเขียนลง data/buffer

    config     : puller.yaml ทั้งก้อน (ใช้ชื่อ site)
    target     : dict ของเครื่องจาก puller.yaml
    log_source : dict ของ log 1 ชนิด (name, path) -- path ต้องเป็นไฟล์จริง ไม่ใช่ wildcard
    state      : dict checkpoint (ถูกแก้ไขและบันทึกลงดิสก์ในฟังก์ชันนี้)

    raise Exception เมื่อ SSH ล้มเหลว/ดึงได้ไม่ครบ -> ตัวเรียก (pull_target) จะ retry
    """
    if journal.is_journal(log_source["path"]):
        return pull_journal(config, target, log_source, state)

    target_name = target["name"]
    source_name = log_source["name"]
    path = log_source["path"]

    source_id = make_source_id(target, log_source)
    checkpoint = int(state.get(source_id, {}).get("offset", 0))

    print(f"[PULL] {target_name}/{source_name} path={path} checkpoint={checkpoint}")

    remote_size = remote_stat(target, path)

    # ไฟล์เล็กลงกว่าที่เคยอ่าน = ถูก rotate/ตัดทิ้ง -> เริ่มอ่านจากต้นไฟล์ใหม่
    if remote_size < checkpoint:
        print(
            f"[ROTATION] {source_id} size={remote_size} "
            f"checkpoint={checkpoint} -> reset to 0"
        )
        checkpoint = 0

    if remote_size == checkpoint:
        print(f"[NO CHANGE] {source_id}")
        return

    start_offset = checkpoint
    expected_bytes = remote_size - start_offset

    print(f"[INCREMENTAL] {start_offset} -> {remote_size}")

    # stat กับ tail เป็น SSH คนละครั้ง ไฟล์อาจโตระหว่างนั้น
    # `head -c` จำกัดให้อ่านไม่เกินขนาดที่วัดไว้ ส่วนที่เขียนเพิ่มจะถูกดึงในรอบหน้า
    data = run_ssh_bytes(
        target,
        f"tail -c +{start_offset + 1} -- '{path}' | head -c {expected_bytes}",
    )

    if len(data) != expected_bytes:
        raise RuntimeError(
            f"partial pull: expected={expected_bytes} actual={len(data)}"
        )

    # ส่งเฉพาะบรรทัดที่จบสมบูรณ์ ไม่งั้นบรรทัดที่เพิ่งเขียนค้างอยู่จะถูกผ่าครึ่ง
    # เป็น 2 ไฟล์ -> otel เห็นเป็น 2 record พัง
    last_newline = data.rfind(b"\n")

    if last_newline == -1:
        print(
            f"[INCOMPLETE LINE] {source_id} no newline in "
            f"{len(data)} bytes -> retry next cycle"
        )
        return

    data = data[: last_newline + 1]
    end_offset = start_offset + len(data)

    # ไม่เก็บ INFO (ตั้ง storage.store_info_logs: true ถ้าต้องการเก็บ)
    dropped = 0
    if not store_info_enabled(config):
        data, _, dropped = drop_info(data)

    data, collapsed = _collapse(config, data, target_name, source_name)

    output_file, pull_id = None, state.get(source_id, {}).get("last_pull_id")
    if data:
        output_file, pull_id = _write_buffer(
            config, target_name, source_name, source_id, start_offset, end_offset, data
        )

    # อัปเดต checkpoint "หลัง" ไฟล์อยู่ใน buffer แล้วเท่านั้น
    # ถ้าดับก่อนบรรทัดนี้ จะดึงซ้ำรอบหน้า (ซ้ำดีกว่าหาย)
    state[source_id] = {
        "target": target_name,
        "source": source_name,
        "path": path,
        "offset": end_offset,
        "last_size": remote_size,
        "last_pull_id": pull_id,
        "last_file": output_file or state.get(source_id, {}).get("last_file"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)

    lines = data.count(b"\n")

    ssh_pull_bytes_total.labels(target=target_name, source=source_name).inc(len(data))
    ssh_pull_records_total.labels(target=target_name, source=source_name).inc(lines)

    print(f"[OK] {output_file or '(no file: all INFO)'} bytes={len(data)} lines={lines} info_dropped={dropped}"
          + (f" collapsed={collapsed}" if collapsed else ""))
    print(f"[CHECKPOINT] {source_id} = {end_offset}")


# ---------------------------------------------------------------------------
# ดึงทุก log ของ 1 เครื่อง
# ---------------------------------------------------------------------------

def _expand_sources(target, log_source):
    """แปลง log_source 1 รายการเป็นรายการไฟล์จริง

    path ปกติ     -> [log_source]
    path มี wildcard -> 1 รายการต่อไฟล์ที่เจอบนเครื่องปลายทาง
    คืน None ถ้าหาไฟล์ไม่ได้เพราะ SSH error (ตัวเรียกจะนับเป็นความล้มเหลว)
    """
    path = log_source["path"]

    if journal.is_journal(path) or not any(ch in path for ch in WILDCARD_CHARS):
        return [log_source]

    remote_files = list_remote_files(target, path)

    expanded = []
    for remote_file in remote_files:
        actual = dict(log_source)
        actual["path"] = remote_file
        expanded.append(actual)

    return expanded


def _pull_with_retry(config, target, actual_source, state):
    """เรียก pull_log ซ้ำได้สูงสุด retry.max_attempts ครั้ง คืน True ถ้าสำเร็จ"""
    retry_cfg = config.get("retry", {})
    max_retries = int(retry_cfg.get("max_attempts", 3))
    retry_delay = int(retry_cfg.get("delay_seconds", 10))

    target_name = target["name"]
    source_name = actual_source["name"]
    actual_path = actual_source["path"]

    for attempt in range(1, max_retries + 1):
        try:
            print(
                f"[ATTEMPT] {target_name}/{source_name} "
                f"path={actual_path} {attempt}/{max_retries}"
            )

            started = time.time()
            pull_log(config, target, actual_source, state)

            ssh_pull_source_success.labels(target=target_name, source=source_name).set(1)

            print(
                f"[METRIC] {target_name}/{source_name} path={actual_path} "
                f"success {time.time() - started:.2f}s"
            )
            return True

        except Exception as e:
            print(
                f"[ERROR] {target_name}/{source_name} path={actual_path} "
                f"attempt={attempt} error={e}"
            )

            if attempt < max_retries:
                print(f"[RETRY] waiting {retry_delay}s")
                time.sleep(retry_delay)

    return False


def pull_target(config, target, state):
    """ดึง log ทุกชนิดของเครื่องเดียว และรายงานผลเป็น metric

    ผลรวมของเครื่อง = สำเร็จก็ต่อเมื่อ "ทุกไฟล์" สำเร็จ
    (ssh_pull_success{target=...} = 1)
    """
    target_name = target["name"]

    print(f"\n[TARGET] {target_name} {target['host']}")

    sources = target.get("logs", [])
    ssh_pull_sources_total.labels(target=target_name).set(len(sources))

    target_started = time.time()
    target_success = True

    for log_source in sources:
        configured_name = log_source["name"]

        # --- ขยาย wildcard ---
        try:
            source_files = _expand_sources(target, log_source)
        except Exception as e:
            print(
                f"[ERROR] {target_name}/{configured_name} "
                f"wildcard discovery failed error={e}"
            )
            target_success = False
            ssh_pull_source_success.labels(target=target_name, source=configured_name).set(0)
            ssh_pull_errors_total.labels(target=target_name).inc()
            continue

        if not source_files:
            print(
                f"[NO FILES] {target_name}/{configured_name} "
                f"pattern={log_source['path']}"
            )
            ssh_pull_source_success.labels(target=target_name, source=configured_name).set(1)
            continue

        # --- ดึงทีละไฟล์ (มี retry) ---
        group_ok = True

        for actual_source in source_files:
            if not _pull_with_retry(config, target, actual_source, state):
                group_ok = False
                ssh_pull_source_success.labels(
                    target=target_name, source=actual_source["name"]
                ).set(0)
                ssh_pull_errors_total.labels(target=target_name).inc()

        if not group_ok:
            target_success = False

    duration = time.time() - target_started
    ssh_pull_duration_seconds.labels(target=target_name).set(duration)

    if target_success:
        ssh_pull_success.labels(target=target_name).set(1)
        ssh_pull_last_success_timestamp.labels(target=target_name).set(time.time())
        print(f"[TARGET OK] {target_name} duration={duration:.2f}s")
    else:
        ssh_pull_success.labels(target=target_name).set(0)
        print(f"[TARGET FAILED] {target_name}")
