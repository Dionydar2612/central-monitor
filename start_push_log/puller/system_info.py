"""
system_info.py - เก็บ "สถานะของเครื่อง" ผ่าน SSH (นอกเหนือจาก log)
===================================================================

หน้าที่
    ทุกรอบการดึง จะ SSH เข้าไปอ่านสถานะเครื่อง web แล้วเก็บเป็นไฟล์ JSON
    เพื่อให้หน้า dashboard โชว์ข้อมูลที่ Prometheus/node_exporter ไม่มี เช่น
    รายการ service, process ที่กิน CPU สูงสุด, system event จาก journalctl,
    load, uptime, swap, inode, network error

สิ่งที่เก็บ (ทั้งหมดอ่านด้วยคำสั่ง read-only)
    resource      <- /proc/loadavg, /proc/meminfo, df -P /, df -Pi /,
                     /proc/uptime, /sys/class/net/*/statistics/{rx,tx}_errors
    processes     <- ps (10 อันดับ CPU สูงสุด)
    cpu_cores     <- /proc/stat + /proc/<pid>/task/<tid>/stat อ่าน 2 ครั้งห่างกัน 1 วินาที
                     -> จำนวน core, % การใช้งานของแต่ละ core, และ process ที่รันบนแต่ละ core
                        กิน CPU ของ core นั้นเท่าไหร่ (ณ วินาทีที่เก็บ ไม่ใช่ค่าเฉลี่ยตลอดอายุแบบ ps)
    services      <- systemctl list-units --type=service --all
    system_events <- journalctl 15 นาทีล่าสุด ระดับ warning ขึ้นไป
                     (แต่ละรายการมี type จาก classify_event, timestamp จากต้นบรรทัด, message)

ไฟล์ที่เขียน (ใต้ data/state/system/)
    <เครื่อง>.json                        snapshot ล่าสุด (ถูกเขียนทับทุกรอบ)
    history/<เครื่อง>/<YYYYmmddTHHMMSSZ>.json   สำเนาทุกรอบ ไว้ดูย้อนหลัง
                                          เก็บตาม storage.system_history_retention
                                          (puller.yaml, ปริยาย 7d) ที่เกินจะถูกลบ

เชื่อมกับอะไร
    - ssh_client.run_ssh : ช่องทางเดียวที่ใช้คุยกับเครื่องปลายทาง
    - web_dashboard/backend/system_state.py : อ่านไฟล์ที่นี่ไปแสดง
      (รูปแบบชื่อไฟล์ history ต้องตรงกัน -> HISTORY_TS_FORMAT)
    - ถ้าบางคำสั่งล้มเหลว (เช่น user ไม่มีสิทธิ์ journalctl) ส่วนนั้นจะว่าง
      ส่วนอื่นยังเก็บต่อ ไม่ทำให้ทั้งรอบล้ม
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

from config import SYSTEM_DIR, parse_interval
from ssh_client import run_ssh

# รูปแบบเวลาในชื่อไฟล์ history -- web_dashboard/backend/system_state.py ใช้รูปแบบเดียวกัน
HISTORY_TS_FORMAT = "%Y%m%dT%H%M%SZ"

DEFAULT_HISTORY_RETENTION = "7d"


# ---------------------------------------------------------------------------
# ตัวช่วยทั่วไป
# ---------------------------------------------------------------------------

def _write_json_atomic(path, data):
    """เขียน JSON แบบ atomic (.tmp แล้ว rename) กัน dashboard อ่านไฟล์ที่เขียนค้าง"""
    os.makedirs(os.path.dirname(path), exist_ok=True)

    tmp_file = path + ".tmp"

    with open(tmp_file, "w") as f:
        json.dump(data, f, indent=2)

    os.replace(tmp_file, path)


def _percent_column(df_output):
    """ดึงตัวเลข "Use%" จากผล `df` (บรรทัดสุดท้าย, คอลัมน์ที่ 5) คืน (parts, ตัวเลข)"""
    lines = df_output.splitlines()

    if len(lines) < 2:
        return None, None

    parts = lines[-1].split()

    if len(parts) < 5:
        return None, None

    return parts, float(parts[4].rstrip("%"))


# ---------------------------------------------------------------------------
# เก็บทีละหมวด (แต่ละหมวด try/except แยก: หมวดหนึ่งพัง หมวดอื่นยังได้ข้อมูล)
# ---------------------------------------------------------------------------

def _collect_resource(target):
    """load, memory, swap, disk, inode, uptime, network error -> dict"""
    name = target["name"]
    resource = {}

    # --- load average : "0.52 0.41 0.30 1/234 5678" ---
    try:
        parts = run_ssh(target, "cat /proc/loadavg").strip().split()

        if len(parts) >= 3:
            resource["load_1m"] = float(parts[0])
            resource["load_5m"] = float(parts[1])
            resource["load_15m"] = float(parts[2])
            resource["load"] = float(parts[0])

    except Exception as e:
        print(f"[SYSTEM] load failed target={name} error={e}")
        resource = {}

    # --- memory / swap : /proc/meminfo (หน่วย kB) ---
    try:
        mem = {}

        for line in run_ssh(target, "cat /proc/meminfo").splitlines():
            parts = line.split()

            if len(parts) >= 2:
                try:
                    mem[parts[0].rstrip(":")] = int(parts[1])
                except ValueError:
                    pass

        total = mem.get("MemTotal")
        available = mem.get("MemAvailable")

        if total and available is not None:
            used = total - available
            resource["memory"] = round(used / total * 100, 2)          # % ที่ใช้
            resource["memory_used"] = round(used / 1024 / 1024, 2)      # GB
            resource["memory_available"] = round(available / 1024 / 1024, 2)  # GB

        swap_total = mem.get("SwapTotal")
        swap_free = mem.get("SwapFree", 0)

        if swap_total:
            resource["swap"] = round((swap_total - swap_free) / swap_total * 100, 2)

    except Exception as e:
        print(f"[SYSTEM] memory failed target={name} error={e}")

    # --- disk ของ / : df -P (บล็อกละ 1K) ---
    try:
        parts, percent = _percent_column(run_ssh(target, "df -P /"))

        if parts:
            resource["disk"] = percent
            resource["disk_available"] = round(int(parts[3]) / 1024 / 1024, 2)  # GB

    except Exception as e:
        print(f"[SYSTEM] disk failed target={name} error={e}")

    # --- inode ของ / : df -Pi ---
    try:
        parts, percent = _percent_column(run_ssh(target, "df -Pi /"))

        if parts:
            resource["inode"] = percent

    except Exception as e:
        print(f"[SYSTEM] inode failed target={name} error={e}")

    # --- uptime : /proc/uptime (วินาที) -> "Xd Yh Zm" ---
    try:
        seconds = float(run_ssh(target, "cat /proc/uptime").strip().split()[0])

        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)

        resource["uptime"] = f"{days}d {hours}h {minutes}m"

    except Exception as e:
        print(f"[SYSTEM] uptime failed target={name} error={e}")

    # --- network error รวมทุก interface ---
    # คำสั่งพิมพ์ rx_errors ของทุก NIC ก่อน แล้วตามด้วย tx_errors ของทุก NIC
    # จำนวนเท่ากัน จึงแบ่งครึ่งเพื่อแยก rx / tx
    try:
        command = (
            "cat /sys/class/net/*/statistics/rx_errors 2>/dev/null; "
            "cat /sys/class/net/*/statistics/tx_errors 2>/dev/null"
        )

        values = []

        for line in run_ssh(target, command).splitlines():
            try:
                values.append(int(line.strip()))
            except ValueError:
                pass

        if values:
            half = len(values) // 2
            resource["net_rx_errors"] = sum(values[:half])
            resource["net_tx_errors"] = sum(values[half:])

    except Exception as e:
        print(f"[SYSTEM] network errors failed target={name} error={e}")

    return resource


def _collect_processes(target):
    """10 process ที่กิน CPU สูงสุด -> [{pid, name, cpu, memory}]"""
    processes = []

    try:
        # comm ไว้ท้ายสุด (ชื่อ process อาจมีช่องว่าง)
        output = run_ssh(target, "ps -eo pid,psr,pcpu,pmem,comm --sort=-pcpu | head -11")

        for line in output.splitlines()[1:]:      # ข้ามบรรทัดหัวตาราง
            parts = line.split(None, 4)

            if len(parts) < 5:
                continue

            try:
                processes.append({
                    "pid": int(parts[0]),
                    "core": int(parts[1]),
                    "name": parts[4].strip(),
                    "cpu": float(parts[2]),
                    "memory": float(parts[3]),
                })
            except ValueError:
                continue

    except Exception as e:
        print(f"[SYSTEM] processes failed target={target['name']} error={e}")

    return processes


# ---------------------------------------------------------------------------
# CPU แยกตาม core
# ---------------------------------------------------------------------------
# อ่าน 2 รอบห่างกัน 1 วินาที แต่ละรอบ:
#   uptime, บรรทัด cpuN ของ /proc/stat, และทุก thread: "<pid>/task/<tid>/stat <utime+stime> <core> <ชื่อ>"
#   (awk ตัดเอาเฉพาะ field ที่ใช้ ข้อมูลที่ส่งกลับจึงเล็ก; ชื่อ process อาจมีช่องว่าง -> อยู่ท้ายสุด)
#   field ของ stat หลังวงเล็บชื่อ: 1=state ... 12=utime 13=stime ... 37=processor (core ที่รันล่าสุด)
_THREAD_AWK = (
    "awk '{p=index($0,\":\"); f=substr($0,1,p-1); s=substr($0,p+1); "
    "c=s; sub(/^[^(]*\\(/,\"\",c); sub(/\\) [^)]*$/,\"\",c); "
    "r=s; sub(/.*\\) /,\"\",r); split(r,a,\" \"); print f, a[12]+a[13], a[37], c}'"
)
_SAMPLE = (
    "cat /proc/uptime; grep '^cpu[0-9]' /proc/stat; "
    "grep -H '' [0-9]*/task/[0-9]*/stat 2>/dev/null | " + _THREAD_AWK
)
CPU_CORE_COMMAND = (
    "getconf CLK_TCK; cd /proc || exit 1; "
    f"echo @@S1; {_SAMPLE}; sleep 1; echo @@S2; {_SAMPLE}; true"
)

TOP_PER_CORE = 8
TOP_PROCESSES = 15


def _parse_core_sample(lines):
    """1 รอบ -> (uptime, {core: (busy, total)}, {(pid, tid): (ticks, core, name)})"""
    uptime, cores, threads = None, {}, {}

    for line in lines:
        if uptime is None:
            try:
                uptime = float(line.split()[0])
            except (ValueError, IndexError):
                pass
            continue

        if line.startswith("cpu"):
            parts = line.split()
            try:
                values = [int(v) for v in parts[1:]]
                core = int(parts[0][3:])
            except ValueError:
                continue
            total = sum(values[:8])          # user nice system idle iowait irq softirq steal
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            cores[core] = (total - idle, total)
            continue

        parts = line.split(" ", 3)
        if len(parts) < 3:
            continue
        path = parts[0].split("/")
        try:
            pid, tid = int(path[0]), int(path[2])
            threads[(pid, tid)] = (int(parts[1]), int(parts[2]), parts[3] if len(parts) > 3 else "?")
        except (ValueError, IndexError):
            continue

    return uptime, cores, threads


def parse_cpu_cores(output):
    """ผลของ CPU_CORE_COMMAND -> {"count", "interval", "per_core": [...]} หรือ {} ถ้าอ่านไม่ได้"""
    text = output.split("@@S1", 1)
    if len(text) < 2 or "@@S2" not in text[1]:
        return {}

    try:
        clk = int(text[0].strip().splitlines()[0])
    except (ValueError, IndexError):
        clk = 100

    first, second = text[1].split("@@S2", 1)
    up1, cores1, th1 = _parse_core_sample([l for l in first.splitlines() if l.strip()])
    up2, cores2, th2 = _parse_core_sample([l for l in second.splitlines() if l.strip()])

    if not cores2:
        return {}

    interval = (up2 - up1) if (up1 is not None and up2 is not None and up2 > up1) else 1.0
    ticks = interval * clk

    per_core = {}
    for core in sorted(cores2):
        busy2, total2 = cores2[core]
        busy1, total1 = cores1.get(core, (busy2, total2))
        span = total2 - total1
        per_core[core] = {
            "core": core,
            "usage": round((busy2 - busy1) / span * 100, 1) if span > 0 else None,
            "processes": {},
        }

    names = {pid: name for (pid, tid), (_, _, name) in th2.items() if pid == tid}

    for (pid, tid), (ticks2, core, name) in th2.items():
        ticks1 = th1.get((pid, tid), (None,))[0]
        if ticks1 is None or ticks2 <= ticks1 or core not in per_core:
            continue
        cpu = (ticks2 - ticks1) / ticks * 100
        procs = per_core[core]["processes"]
        item = procs.setdefault(pid, {"pid": pid, "name": names.get(pid, name), "cpu": 0.0, "threads": 0})
        item["cpu"] += cpu
        item["threads"] += 1

    # รวมต่อ process (ทุก core): ใช้กับตาราง "CPU process" + รายละเอียดว่ารันบน core ไหนเท่าไหร่
    by_pid = {}
    for core, entry in per_core.items():
        for pid, p in entry["processes"].items():
            item = by_pid.setdefault(pid, {"pid": pid, "name": p["name"], "cpu": 0.0, "threads": 0, "cores": []})
            item["cpu"] += p["cpu"]
            item["threads"] += p["threads"]
            item["cores"].append({"core": core, "cpu": round(min(p["cpu"], 100.0), 1)})

    top = sorted(by_pid.values(), key=lambda p: p["cpu"], reverse=True)[:TOP_PROCESSES]
    for p in top:
        p["cpu"] = round(p["cpu"], 1)                     # % ของ 1 core (หลาย thread อาจเกิน 100)
        p["cores"].sort(key=lambda c: c["cpu"], reverse=True)

    result = []
    for core in sorted(per_core):
        entry = per_core[core]
        procs = sorted(entry["processes"].values(), key=lambda p: p["cpu"], reverse=True)
        for p in procs:
            p["cpu"] = round(min(p["cpu"], 100.0), 1)
        entry["active"] = len(procs)
        entry["processes"] = procs[:TOP_PER_CORE]
        result.append(entry)

    return {"count": len(result), "interval": round(interval, 2), "per_core": result, "processes": top}


def _collect_cpu_cores(target):
    try:
        return parse_cpu_cores(run_ssh(target, CPU_CORE_COMMAND))
    except Exception as e:
        print(f"[SYSTEM] cpu cores failed target={target['name']} error={e}")
        return {}


def _parse_services(output):
    """แปลงผล `systemctl list-units --type=service --all` เป็น [{name, status}]

    คอลัมน์: [●] UNIT LOAD ACTIVE SUB DESCRIPTION
    - unit ที่ไม่ปกติ (not-found / masked / failed) systemd จะใส่สัญลักษณ์ ● (หรือ ○ × *) นำหน้าเป็นคอลัมน์แยก
      ถ้าไม่ตัดทิ้ง ชื่อ service จะกลายเป็น "●" และสถานะเลื่อนผิดคอลัมน์ (อาการที่เห็นในหน้าเว็บ: จุด ● + not-found)
    - status = ACTIVE (active/inactive/failed) แต่ถ้า LOAD ผิดปกติ (not-found/masked/error) ใช้ค่า LOAD แทน
      เพื่อให้หน้าเว็บแสดงจุดสีเหลืองเหมือนเดิม
    """
    services = []
    for line in output.splitlines():
        parts = line.split()
        # ตัดสัญลักษณ์นำหน้า: โทเคนแรกที่ไม่ใช่ชื่อ unit (ชื่อ unit มี "." เสมอ เช่น ssh.service)
        if parts and "." not in parts[0]:
            parts = parts[1:]
        if len(parts) < 3:
            continue
        name, load, active = parts[0], parts[1], parts[2]
        status = load if load in ("not-found", "masked", "error", "bad-setting") else active
        services.append({"name": name, "status": status})
    return services


def _collect_services(target):
    """systemd service ทั้งหมด -> [{name, status}]  (status = active/inactive/failed หรือ not-found/masked)"""
    try:
        output = run_ssh(
            target,
            # --plain: ไม่ใส่สัญลักษณ์ ● นำหน้า (ถ้า systemd รุ่นเก่าไม่รองรับ ตัวแปลงด้านบนตัดสัญลักษณ์ให้อยู่แล้ว)
            "systemctl list-units --type=service --all --no-legend --no-pager --plain 2>/dev/null",
        )
        return _parse_services(output)
    except Exception as e:
        print(f"[SYSTEM] services failed target={target['name']} error={e}")
        return []


_OOM = re.compile(r"(?<![a-z0-9])oom(?![a-z0-9])")


def classify_event(line):
    """จัดชนิดของบรรทัด journal ด้วยคำสำคัญ (ลำดับสำคัญ: ตรงอันแรกก่อนได้ก่อน)"""
    lower = line.lower()

    # "oom" ต้องไม่ติดตัวอักษรหน้า/หลัง (กัน room / zoom) แต่ oom_reaper / oom-killer ยังจับได้
    if "out of memory" in lower or _OOM.search(lower) or "killed process" in lower:
        return "oom"
    if "no space left" in lower or "disk full" in lower:
        return "disk_full"
    if "failed" in lower:
        return "service_failed"
    if "restart" in lower:
        return "service_restarted"
    if "network" in lower or "link is down" in lower:
        return "network_error"
    if "filesystem" in lower or "mount" in lower:
        return "filesystem_error"
    if "kernel" in lower:
        return "kernel_error"

    return "system"


# journalctl -o short-iso ขึ้นต้นบรรทัดด้วยเวลา เช่น 2026-10-02T01:00:00+0700
_EVENT_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{2}):?(\d{2})")


def _event_timestamp(line):
    """ดึงเวลาจากต้นบรรทัด journal เป็น ISO 8601 (เช่น 2026-10-02T01:00:00+07:00)
    ไม่พบ -> None  (หน้าเว็บใช้แสดงคอลัมน์เวลาของ System events)"""
    match = _EVENT_TS.match(line)

    if not match:
        return None

    return f"{match.group(1)}{match.group(2)}:{match.group(3)}"


def _collect_events(target):
    """เหตุการณ์ระดับ warning ขึ้นไปใน 15 นาทีล่าสุด -> [{type, message}] (สูงสุด 100)"""
    events = []

    try:
        output = run_ssh(
            target,
            "journalctl --since '15 minutes ago' -p warning..alert "
            "--no-pager -o short-iso 2>/dev/null | tail -100",
        )

        for line in output.splitlines():
            line = line.strip()

            if line:
                events.append({
                    "type": classify_event(line),
                    "timestamp": _event_timestamp(line),
                    "message": line,
                })

    except Exception as e:
        print(f"[SYSTEM] events failed target={target['name']} error={e}")

    return events


# ---------------------------------------------------------------------------
# history: เก็บสำเนาทุกรอบ + ลบที่เก่าเกิน retention
# ---------------------------------------------------------------------------

def history_dir(web):
    """โฟลเดอร์ประวัติของเครื่อง: data/state/system/history/<เครื่อง>"""
    return os.path.join(SYSTEM_DIR, "history", web)


def _history_file_time(name, path):
    """เวลาของไฟล์ history จากชื่อไฟล์ (สำรองด้วยเวลาแก้ไขไฟล์ถ้าชื่อผิดรูปแบบ)"""
    try:
        stem = name[: -len(".json")]
        return datetime.strptime(stem, HISTORY_TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)


def prune_history(web, retention_seconds, now=None):
    """ลบไฟล์ history ของเครื่องที่เก่ากว่า retention คืนจำนวนที่ลบ"""
    directory = history_dir(web)

    if not os.path.isdir(directory):
        return 0

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=retention_seconds)
    deleted = 0

    for name in os.listdir(directory):
        if not name.endswith(".json"):
            continue

        path = os.path.join(directory, name)

        try:
            if _history_file_time(name, path) < cutoff:
                os.remove(path)
                deleted += 1
        except FileNotFoundError:
            continue

    return deleted


# ---------------------------------------------------------------------------
# ฟังก์ชันหลัก: เรียกจาก scheduler ทุกรอบ ต่อเครื่อง
# ---------------------------------------------------------------------------

def collect_web_system_info(config, target):
    """เก็บสถานะ 1 เครื่อง แล้วเขียน snapshot ล่าสุด + history + ลบ history เก่า"""
    name = target["name"]
    now = datetime.now(timezone.utc)

    result = {
        "web": name,
        "timestamp": now.isoformat(),
        "resource": _collect_resource(target),
        "services": _collect_services(target),
        "processes": _collect_processes(target),
        "cpu_cores": _collect_cpu_cores(target),
        "system_events": _collect_events(target),
    }

    # เข้าเครื่องไม่ได้ (ทุกหมวดว่าง) -> ไม่เขียนทับ snapshot เดิมและไม่ลง history
    # เดิมจะได้ไฟล์ว่างทับข้อมูลล่าสุดที่ดีอยู่ ทำให้หน้าเว็บว่างทั้งที่ยังมีข้อมูลเก่าให้ดู
    if not (result["resource"] or result["services"] or result["processes"]):
        print(f"[SYSTEM SKIP] {name} unreachable or no data -> keep previous snapshot")
        return

    # 1) snapshot ล่าสุด (dashboard อ่านตอนเปิดหน้าปัจจุบัน)
    _write_json_atomic(os.path.join(SYSTEM_DIR, f"{name}.json"), result)

    # 2) สำเนาลง history (dashboard อ่านตอนคลิก log ย้อนหลัง)
    _write_json_atomic(
        os.path.join(history_dir(name), now.strftime(HISTORY_TS_FORMAT) + ".json"),
        result,
    )

    # 3) ลบ history ที่เก่าเกิน storage.system_history_retention
    retention = config.get("storage", {}).get(
        "system_history_retention", DEFAULT_HISTORY_RETENTION
    )
    pruned = prune_history(name, parse_interval(retention), now)

    print(
        f"[SYSTEM OK] {name} "
        f"services={len(result['services'])} "
        f"processes={len(result['processes'])} "
        f"cores={result['cpu_cores'].get('count', 0)} "
        f"events={len(result['system_events'])} "
        f"history_pruned={pruned}"
    )


def prune_orphan_systems(valid_names):
    """ลบ snapshot + history ของเครื่องที่ไม่อยู่ใน puller.yaml แล้ว (ถูกลบจากหน้า Settings) คืนรายชื่อที่ลบ"""
    import shutil

    valid = set(valid_names)
    removed = []

    if not os.path.isdir(SYSTEM_DIR):
        return removed

    for name in os.listdir(SYSTEM_DIR):
        path = os.path.join(SYSTEM_DIR, name)

        if name == "history" and os.path.isdir(path):
            for web in os.listdir(path):
                if web not in valid:
                    shutil.rmtree(os.path.join(path, web), ignore_errors=True)
                    removed.append(f"history/{web}")
            continue

        if name.endswith(".json") and name[: -len(".json")] not in valid:
            try:
                os.remove(path)
                removed.append(name)
            except FileNotFoundError:
                pass

    return removed
