"""
ondemand.py - ดึง log ระดับ INFO "สด" จากเครื่องปลายทาง เฉพาะตอนที่หน้าเว็บสั่ง
===============================================================================

ทำไมต้องมี
    puller ไม่เก็บ INFO (log_filter.py) แต่บางครั้งยังอยากดู -> หน้าเว็บมีปุ่ม "Load INFO log"
    ปุ่มนี้เรียก central-api -> central-api เรียก HTTP ภายในของ puller (ไฟล์นี้)
    -> puller SSH ไปอ่าน N บรรทัดท้ายของทุก log ของเครื่องนั้น แล้วคืนเฉพาะ INFO
    ผลลัพธ์ส่งกลับไปแสดงอย่างเดียว: ไม่เขียนลง data/buffer, ไม่เข้า OpenSearch, ไม่เก็บไฟล์ใด ๆ

ทำไมต้องผ่าน puller
    private key ของ SSH อยู่ที่ puller เท่านั้น (central-api ไม่ได้ mount key โดยตั้งใจ)

ความปลอดภัย
    - เปิดเฉพาะใน docker network ภายใน (ไม่ publish port ออก host)
    - ต้องส่ง header X-Central-Token ตรงกับ CENTRAL_PULLER_TOKEN (ถ้าตั้งไว้)
    - รับเฉพาะ "ชื่อเครื่อง" และ "ชื่อ log" ที่มีอยู่ใน puller.yaml + ตัวเลข
      ไม่มีทางส่ง path/คำสั่งเองได้

    GET /info-logs?target=web-01&lines=500[&source=system][&start=<unix>][&end=<unix>]
        -> {"entries":[{timestamp, ts, level, type, message, source_file}], "errors":[...]}

    GET /processes?target=web-01
        -> {"time", "cpu_cores": {count, per_core:[{core, usage, active, processes:[...]}]},
            "memory_top": [{pid, name, memory, rss_mb}]}
        วัดสด ณ ตอนที่ขอ (CPU ต่อ core 1 วินาที + 10 process ที่ใช้ RAM สูงสุด) แสดงแล้วไม่เก็บ
        หน้าเว็บเรียกทุก ~60 วินาทีขณะเปิดหน้า (central-api cache ไว้ 20 วินาที)

    GET /winrm-cert?host=&port=  ·  POST /winrm-test  ·  POST /winrm-install + GET /winrm-install-status?name=
    GET /winrm-status  ·  GET /win-services|/win-tasks|/win-programs?target=  (Windows: ดึงสด ไม่เก็บ)
    (ติดตั้งจากหน้าเว็บ: windows_install.py)  ·  เครื่อง type: winrm -> windows_ondemand.py
    GET /log-check?target=web-01
        -> {"logs":[{name, path, ok, problem, files:[{path, size, mtime, readable}]}], "error"}
        ตรวจ log ที่ตั้งไว้ใน puller.yaml ของเครื่องนั้น (ปุ่ม "ตรวจ" ใน Settings > Log)
        ว่าโฟลเดอร์มีจริง / มีไฟล์ตรง pattern / อ่านได้ (SSH ครั้งเดียว อ่านอย่างเดียว ไม่เปิดไฟล์)
"""

import hmac
import json
import os
import re
import shlex
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import journal
import windows_install
import windows_ondemand
from config import load_config
from log_filter import only_info
from system_info import CPU_CORE_COMMAND, parse_cpu_cores
from ssh_client import list_remote_files, run_ssh, run_ssh_bytes

PORT = int(os.environ.get("CENTRAL_ONDEMAND_PORT", "8001"))
TOKEN = os.environ.get("CENTRAL_PULLER_TOKEN", "")

DEFAULT_LINES = 500
MAX_LINES = 5000          # ต่อไฟล์ (ก่อนกรอง)
MAX_ENTRIES = 3000        # รวมที่ส่งกลับ
WILDCARD_CHARS = ("*", "?", "[")

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))")


def _line_ts(text):
    m = _TS.match(text)
    if not m:
        return None, None
    value = m.group(1)
    try:
        iso = value.replace("Z", "+00:00")
        if re.search(r"[+-]\d{4}$", iso):
            iso = iso[:-2] + ":" + iso[-2:]
        return value, datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return value, None


def _in_range(ts, start, end):
    if ts is None:
        return start is None and end is None
    return (start is None or ts >= start) and (end is None or ts <= end)


def fetch_info_logs(target, lines=DEFAULT_LINES, source=None, start=None, end=None):
    lines = max(1, min(int(lines), MAX_LINES))
    entries, errors = [], []

    for log_source in target.get("logs") or []:
        name, path = log_source.get("name"), log_source.get("path", "")
        if source and name != source:
            continue

        try:
            if journal.is_journal(path):
                cmd = journal.build_command(
                    path, since=start, until=end, priority=journal.INFO_PRIORITY, lines=lines
                )
                for e in journal.parse_output(run_ssh(target, cmd)):
                    if e["level"] != "INFO":
                        continue
                    entries.append({
                        "timestamp": e["line"].split(" ", 1)[0],
                        "ts": e["ts"],
                        "level": "INFO",
                        "type": name,
                        "message": e["line"],
                        "source_file": path,
                    })
                continue

            files = (
                list_remote_files(target, path)
                if any(ch in path for ch in WILDCARD_CHARS) else [path]
            )
            for file_path in files:
                data = run_ssh_bytes(target, f"tail -n {lines} -- '{file_path}'")
                for text in only_info(data):
                    stamp, ts = _line_ts(text)
                    if not _in_range(ts, start, end):
                        continue
                    entries.append({
                        "timestamp": stamp,
                        "ts": ts,
                        "level": "INFO",
                        "type": name,
                        "message": text,
                        "source_file": file_path,
                    })

        except Exception as e:      # ไฟล์หนึ่งพัง ไฟล์อื่นยังได้
            errors.append(f"{name}: {e}")

    entries.sort(key=lambda x: x["ts"] or 0, reverse=True)
    return {"entries": entries[:MAX_ENTRIES], "errors": errors, "lines": lines}


MEMORY_COMMAND = "ps -eo pid,pmem,rss,comm --sort=-rss --no-headers | head -n 10"


def parse_memory_top(output):
    """ผล MEMORY_COMMAND -> [{pid, name, memory(%), rss_mb}]"""
    out = []
    for line in output.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            out.append({
                "pid": int(parts[0]),
                "memory": float(parts[1]),
                "rss_mb": round(int(parts[2]) / 1024, 1),
                "name": parts[3].strip(),
            })
        except ValueError:
            continue
    return out


def parse_details(output):
    """ผล `ps -o pid=,user:32=,pmem=,rss=,nlwp=,etime=,stat=,args=` -> {pid: {...}}"""
    out = {}
    for line in output.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 7:
            continue
        try:
            out[int(parts[0])] = {
                "user": parts[1],
                "memory": float(parts[2]),
                "rss_mb": round(int(parts[3]) / 1024, 1),
                "threads_total": int(parts[4]),
                "elapsed": parts[5],          # [[dd-]hh:]mm:ss ตั้งแต่ process เริ่ม
                "state": parts[6],
                "command": parts[7].strip() if len(parts) > 7 else "",
            }
        except ValueError:
            continue
    return out


def fetch_processes(target):
    """CPU ต่อ core + process บนแต่ละ core + top memory (SSH ครั้งแรก)
    แล้วขอรายละเอียดของ process ที่กิน CPU สูงสุด: user, RAM, thread, เวลาทำงาน, คำสั่งเต็ม (SSH ครั้งที่ 2)"""
    output = run_ssh(target, f"{CPU_CORE_COMMAND}; echo @@MEM; {MEMORY_COMMAND}")
    cpu_part, _, mem_part = output.partition("@@MEM")
    cores = parse_cpu_cores(cpu_part)

    pids = [p["pid"] for p in cores.get("processes", [])]
    if pids:
        try:
            details = parse_details(run_ssh(
                target,
                "ps -o pid=,user:32=,pmem=,rss=,nlwp=,etime=,stat=,args= -p "
                + ",".join(str(int(p)) for p in pids) + " 2>/dev/null; true",
            ))
            for p in cores["processes"]:
                p.update(details.get(p["pid"], {}))
        except Exception as e:
            print(f"[ONDEMAND] process details failed target={target.get('name')} error={e}")

    return {
        "time": datetime.now().astimezone().isoformat(),
        "cpu_cores": cores,
        "memory_top": parse_memory_top(mem_part),
    }


LOG_CHECK_MAX_FILES = 50


def log_check_command(logs):
    """คำสั่งเดียวตรวจทุก log (path มาจาก puller.yaml เท่านั้น + shlex.quote ทุกค่า)"""
    parts = []
    for i, src in enumerate(logs):
        path = str(src.get("path") or "")
        parts.append(f"echo '@@L {i}'")
        if journal.is_journal(path):
            try:
                cmd = journal.build_command(path, lines=1)
            except ValueError:
                parts.append("echo '@@BADUNIT'")
                continue
            parts.append("if command -v journalctl >/dev/null 2>&1; then "
                         f"n=$( ({cmd}) 2>/dev/null | wc -l); echo \"@@J $n\"; else echo '@@NOJOURNAL'; fi")
            continue
        d, pat = os.path.dirname(path) or ".", os.path.basename(path)
        qd, qp = shlex.quote(d), shlex.quote(pat)
        parts.append(f"if [ ! -d {qd} ]; then echo '@@NODIR'; elif [ ! -r {qd} ] || [ ! -x {qd} ]; then echo '@@NOACCESS'; "
                     f"else find {qd} -maxdepth 1 -type f -name {qp} \\( -readable -printf 'R %s %T@ %p\\n' -o -printf 'N %s %T@ %p\\n' \\) "
                     f"2>/dev/null | head -n {LOG_CHECK_MAX_FILES}; fi")
    return "; ".join(parts) + "; true"


_PROBLEM = {
    "@@NODIR": "ไม่พบโฟลเดอร์",
    "@@NOACCESS": "ไม่มีสิทธิ์เข้าโฟลเดอร์ (เพิ่ม user ที่ดึง log เข้ากลุ่มที่อ่านได้ เช่น adm)",
    "@@NOJOURNAL": "เครื่องนี้ไม่มี journalctl",
    "@@BADUNIT": "ชื่อ unit ไม่ถูกต้อง",
}


def parse_log_check(logs, output):
    """ผลของ log_check_command -> [{name, path, ok, problem, files}]"""
    out = [{"name": s.get("name"), "path": s.get("path"), "ok": False, "problem": "", "files": []} for s in logs]
    cur = None
    for line in output.splitlines():
        line = line.rstrip("\r")
        if line.startswith("@@L "):
            try:
                cur = out[int(line[4:])]
            except (ValueError, IndexError):
                cur = None
            continue
        if cur is None or not line.strip():
            continue
        if line in _PROBLEM:
            cur["problem"] = _PROBLEM[line]
        elif line.startswith("@@J "):
            n = int(line[4:].strip() or 0) if line[4:].strip().isdigit() else 0
            cur["journal_entries"] = n
            if not n:
                cur["problem"] = "อ่าน journal ไม่ได้หรือยังไม่มี log (เพิ่ม user เข้ากลุ่ม systemd-journal / adm)"
        else:
            bits = line.split(" ", 3)
            if len(bits) == 4:
                try:
                    cur["files"].append({"readable": bits[0] == "R", "size": int(bits[1]),
                                         "mtime": int(float(bits[2])), "path": bits[3]})
                except ValueError:
                    pass
    for r in out:
        if r["problem"]:
            continue
        if "journal_entries" in r:
            r["ok"] = True
        elif not r["files"]:
            r["problem"] = "ไม่พบไฟล์ที่ตรงกับ path"
        elif not any(f["readable"] for f in r["files"]):
            r["problem"] = "พบไฟล์แต่อ่านไม่ได้ (สิทธิ์ไฟล์)"
        else:
            r["ok"] = True
            if not all(f["readable"] for f in r["files"]):
                r["problem"] = "บางไฟล์อ่านไม่ได้"
    return out


def check_logs(target):
    logs = [s for s in target.get("logs") or [] if s.get("path")]
    if not logs:
        return {"logs": []}
    return {"logs": parse_log_check(logs, run_ssh(target, log_check_command(logs)))}


def _num(params, key):
    try:
        value = params.get(key, [""])[0]
        return float(value) if value not in ("", None) else None
    except ValueError:
        return None


class _Handler(BaseHTTPRequestHandler):

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)

        if url.path not in ("/info-logs", "/processes", "/rule-preview", "/log-check", "/winrm-cert", "/winrm-install-status",
                            "/winrm-status", "/win-services", "/win-tasks", "/win-programs"):
            return self._send(404, {"error": "not found"})

        if TOKEN and not hmac.compare_digest(self.headers.get("X-Central-Token", ""), TOKEN):
            return self._send(403, {"error": "forbidden"})

        params = parse_qs(url.query)
        if url.path == "/winrm-install-status":
            return self._send(200, windows_install.status(params.get("name", [""])[0]))
        if url.path == "/winrm-status":          # รุ่น module / การเชื่อมต่อของทุกเครื่อง Windows (Settings)
            return self._send(200, {"targets": windows_pull.all_status()})
        if url.path == "/winrm-cert":
            try:
                return self._send(200, windows_ondemand.cert(params.get("host", [""])[0].strip(),
                                                             params.get("port", ["5986"])[0] or 5986))
            except Exception as e:
                return self._send(502, {"error": str(e)})
        if url.path == "/rule-preview":          # ทดลองกฎแจ้งเตือนที่สร้างเอง (Settings > Custom alert rules)
            try:
                import sensor_monitor
                rule = json.loads(params.get("rule", ["{}"])[0])
                return self._send(200, {"rows": sensor_monitor.preview_rule(rule)})
            except Exception as e:
                print(f"[ONDEMAND] rule-preview error={e!r}")
                return self._send(400, {"error": f"ทดลองกฎไม่ได้: {e}"})
        name = params.get("target", [""])[0]
        target = next(
            (t for t in load_config().get("targets") or [] if t.get("name") == name), None
        )
        if not target:
            return self._send(404, {"error": "unknown target"})
        if str(target.get("type") or "ssh") == "agent":
            return self._send(400, {"error": f"{name} เป็นเครื่องแบบ agent (เลิกรองรับแล้ว) ไม่มี SSH"})
        if str(target.get("type") or "ssh") == "winrm":
            return self._windows(url.path, target, params)
        if url.path.startswith("/win-"):
            return self._send(400, {"error": f"{name} ไม่ใช่เครื่อง Windows"})

        if url.path == "/log-check":
            try:
                return self._send(200, check_logs(target))
            except Exception as e:
                print(f"[ONDEMAND] log-check target={name} error={e}")
                return self._send(502, {"error": f"SSH {name}: {e}"})

        if url.path == "/processes":
            try:
                return self._send(200, fetch_processes(target))
            except Exception as e:     # SSH ไม่ได้ -> ส่งสาเหตุให้หน้าเว็บแสดง
                print(f"[ONDEMAND] processes target={name} error={e}")
                return self._send(502, {"error": f"SSH {name}: {e}"})

        try:
            lines = int(params.get("lines", [DEFAULT_LINES])[0])
        except ValueError:
            lines = DEFAULT_LINES

        result = fetch_info_logs(
            target,
            lines=lines,
            source=params.get("source", [""])[0] or None,
            start=_num(params, "start"),
            end=_num(params, "end"),
        )
        print(f"[ONDEMAND] info-logs target={name} entries={len(result['entries'])} errors={len(result['errors'])}")
        return self._send(200, result)

    def _windows(self, path, target, params):
        name = target.get("name")
        try:
            if path == "/log-check":
                return self._send(200, windows_ondemand.log_check(target))
            if path == "/processes":
                return self._send(200, windows_ondemand.processes(target))
            if path == "/win-services":
                return self._send(200, windows_ondemand.win_services(target))
            if path == "/win-tasks":
                return self._send(200, windows_ondemand.win_tasks(target, params.get("ms", [""])[0] in ("1", "true")))
            if path == "/win-programs":
                return self._send(200, windows_ondemand.win_programs(target))
            try:
                lines = int(params.get("lines", [DEFAULT_LINES])[0])
            except ValueError:
                lines = DEFAULT_LINES
            return self._send(200, windows_ondemand.info_logs(target, lines=lines, source=params.get("source", [""])[0] or None,
                                                              start=_num(params, "start"), end=_num(params, "end")))
        except windows_ondemand.NeedUpdate as e:
            return self._send(409, {"error": str(e), "need_update": True})
        except Exception as e:
            print(f"[ONDEMAND] {path} target={name} error={e}")
            return self._send(502, {"error": f"WinRM {name}: {e}"})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path not in ("/winrm-test", "/winrm-install"):
            return self._send(404, {"error": "not found"})
        if TOKEN and not hmac.compare_digest(self.headers.get("X-Central-Token", ""), TOKEN):
            return self._send(403, {"error": "forbidden"})
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 4 * 1048576)
            body = json.loads(self.rfile.read(n) or b"{}")
            body = body if isinstance(body, dict) else {}
            if url.path == "/winrm-install":                 # ติดตั้งจากหน้าเว็บ (thread แยก)
                return self._send(202, windows_install.start(body))
            return self._send(200, windows_ondemand.test(body))
        except Exception as e:
            return self._send(200, {"ok": False, "error": str(e)[:400]})

    def log_message(self, *args):     # ไม่ต้องพิมพ์ access log ทุก request
        pass


def start_server():
    """เปิด HTTP server ในเธรดแยก (ไม่กระทบรอบการดึงปกติ)"""
    server = ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="ondemand").start()
    print(f"[ONDEMAND] http://0.0.0.0:{PORT}/info-logs" + ("" if TOKEN else " (no token set)"))
    return server
