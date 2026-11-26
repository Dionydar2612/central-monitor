"""
windows_ondemand.py - ปุ่มบนหน้าเว็บสำหรับเครื่อง Windows (type: winrm)  เรียกผ่าน ondemand.py (พอร์ต 8001 ภายใน)
==================================================================================================================

    POST /winrm-test   {host, port, user, password?, thumbprint, name?}   ทดสอบการเชื่อมต่อจากฟอร์ม Settings
                       password ว่าง + มี name -> ใช้รหัสที่เก็บไว้ของเครื่องนั้น
    GET  /winrm-cert?host=&port=                                          อ่าน thumbprint ของใบรับรอง (Scan certificate)
    GET  /processes?target=      (winrm)   CPU ต่อ core + process ที่ใช้ CPU / RAM สูงสุด (วัดสด ไม่เก็บ)
    GET  /info-logs?target=      (winrm)   Event log ระดับ Information + บรรทัด INFO ท้ายไฟล์ log (แสดงอย่างเดียว ไม่เก็บ)
    GET  /log-check?target=      (winrm)   ไฟล์ log ที่ตั้งไว้มีจริง / อยู่ใน allowed-paths.txt ไหม
    GET  /win-services?target=   (winrm)   Service ทั้งหมด + exe / บัญชีที่รัน / PID / คำอธิบาย          (module 1.1.4+)
    GET  /win-tasks?target=&ms=1 (winrm)   Task Scheduler (ms=1 รวม task ของ Microsoft)                  (module 1.1.4+)
    GET  /win-programs?target=   (winrm)   process ทั้งหมด + path ของ exe / คำอธิบาย / service ที่อยู่ในนั้น   (module 1.1.4+)
    (ทั้งสามอย่างดึงสดเมื่อเปิดแท็บ ไม่เก็บลงดิสก์)

ทุกงานใช้ session เดิมของเครื่อง (windows_pull.Worker.call) ไม่เปิด PowerShell ใหม่บนเครื่องปลายทาง
ยกเว้น /winrm-test ที่เปิด-ปิด 1 ครั้ง (ยังไม่ได้บันทึกเครื่อง)
"""

import fnmatch
from datetime import datetime

import cm_secrets
import windows_pull
import winrm_client
from log_filter import only_info

INFO_TAIL_BYTES = 262144


def _worker(target):
    w = windows_pull.get_worker(target.get("name"))
    if w is None:
        raise winrm_client.WinRMError("ยังไม่ได้เริ่มเก็บข้อมูลเครื่องนี้ (รอ ~15 วินาทีหลังบันทึก)")
    return w


def test(body):
    host = str(body.get("host") or "").strip()
    try:
        port = int(body.get("port") or 5986)
    except (TypeError, ValueError):
        port = 5986
    user = str(body.get("user") or "cm-monitor").strip()
    password = body.get("password") or ""
    if not password and body.get("name"):
        password = cm_secrets.get_password(str(body["name"])) or ""
    if not host:
        raise winrm_client.WinRMError("ใส่ Host ก่อน")
    if not password:
        raise winrm_client.WinRMError("ใส่รหัสผ่านของ cm-monitor (ที่ setup-cm-monitor.bat แสดงตอนจบ)")
    return winrm_client.test_connection(host, port, user, password, body.get("thumbprint") or "")


def cert(host, port):
    if not host:
        raise winrm_client.WinRMError("ใส่ Host ก่อน")
    return {"thumbprint": winrm_client.server_thumbprint(host, int(port or 5986))}


def processes(target):
    w = _worker(target)
    p = w.call("Get-CMProcesses", {"Top": 10})
    vals = (w.last_metrics or {}).get("values") or []
    cores = sorted([(r.get("i"), r.get("v")) for r in vals if r.get("k") == "core_pct" and r.get("i") not in ("", "_Total")],
                   key=lambda x: (len(x[0]), x[0]))
    ram_mb = ((w.last_metrics or {}).get("mem_total_mb") or 0) or 0
    top = [{"pid": x.get("pid"), "name": x.get("name"), "cpu": x.get("cpu"), "threads": x.get("threads"), "cores": [],
            "rss_mb": x.get("ws_mb"), "memory": round(x.get("ws_mb", 0) / ram_mb * 100, 2) if ram_mb else None,
            "self": x.get("pid") == p.get("self_pid")} for x in p.get("top_cpu") or []]
    return {
        "time": datetime.now().astimezone().isoformat(),
        "os": "windows",
        "cpu_cores": {"count": p.get("cores") or len(cores), "interval": p.get("interval"),
                      "per_core": [{"core": int(c) if str(c).isdigit() else i, "usage": round(v or 0, 1), "active": [],
                                    "processes": {}} for i, (c, v) in enumerate(cores)],
                      "processes": top},
        "memory_top": [{"pid": x.get("pid"), "name": x.get("name"), "rss_mb": x.get("ws_mb"),
                        "memory": round(x.get("ws_mb", 0) / ram_mb * 100, 2) if ram_mb else None}
                       for x in p.get("top_ram") or []],
    }


def _listing(w):
    return w.call("Get-CMLogTail", {"List": True, "Days": 30}).get("files") or []


def log_check(target):
    w = _worker(target)
    logs = [s for s in target.get("logs") or [] if s.get("path")]
    if not logs:
        return {"logs": []}
    files = _listing(w)
    out = []
    for s in logs:
        pat = str(s["path"])
        hit = [f for f in files if fnmatch.fnmatch(f["path"].lower(), pat.lower())]
        row = {"name": s.get("name"), "path": pat, "ok": bool(hit), "problem": "",
               "files": [{"readable": True, "size": f.get("size"), "mtime": _ts(f.get("mtime")), "path": f["path"]} for f in hit[:50]]}
        if not hit:
            row["problem"] = ("ไม่พบไฟล์ (ต้องเพิ่มโฟลเดอร์นี้ใน LogPaths ของ cm-monitor-config.txt แล้วรัน setup-cm-monitor.bat "
                              "อีกครั้ง หรือไม่มีไฟล์ที่แก้ไขใน 30 วัน)")
        out.append(row)
    return {"logs": out}


def info_logs(target, lines=500, source=None, start=None, end=None):
    w = _worker(target)
    lines = max(1, min(int(lines), 2000))
    entries, errors = [], []
    computer = (w.inventory or {}).get("host") or target.get("name")
    if not source or source in ("system", "application"):
        for log_name in windows_pull.winrm_settings(target)["event_logs"]:
            if source and windows_pull.event_source(log_name) != source:
                continue
            try:
                newest = int(w.call("Get-CMEvents", {"LogName": log_name, "AfterRecordId": -1}).get("newest") or 0)
                r = w.call("Get-CMEvents", {"LogName": log_name, "AfterRecordId": max(0, newest - lines),
                                            "MaxEvents": 500, "MaxLevel": 5})
                for e in r.get("events") or []:
                    if int(e.get("level") or 4) < 4:
                        continue
                    ts = _ts(e.get("time"))
                    if (start and ts and ts < start) or (end and ts and ts > end):
                        continue
                    entries.append({"timestamp": e.get("time"), "ts": ts, "level": "INFO",
                                    "type": windows_pull.event_source(log_name),
                                    "message": windows_pull.event_line(e, computer), "source_file": f"EventLog:{log_name}"})
            except Exception as ex:  # noqa: BLE001
                errors.append(f"{log_name}: {ex}")
    srcs = [s for s in target.get("logs") or [] if s.get("path") and (not source or s.get("name") == source)]
    files = None
    for s in srcs:
        try:
            pat = str(s["path"])
            if any(ch in pat for ch in "*?"):
                files = files if files is not None else _listing(w)
                paths = [f["path"] for f in files if fnmatch.fnmatch(f["path"].lower(), pat.lower())][:10]
            else:
                paths = [pat]
            for path in paths:
                size = int(w.call("Get-CMLogTail", {"Path": path, "Offset": -1}).get("size") or 0)
                r = w.call("Get-CMLogTail", {"Path": path, "Offset": max(0, size - INFO_TAIL_BYTES),
                                             "MaxBytes": INFO_TAIL_BYTES, "Encoding": s.get("encoding") or "utf8"})
                for text in only_info((r.get("text") or "").encode("utf-8"))[-lines:]:
                    entries.append({"timestamp": text[:30].split(" ")[0], "ts": None, "level": "INFO",
                                    "type": s.get("name"), "message": text, "source_file": path})
        except Exception as ex:  # noqa: BLE001
            errors.append(f"{s.get('name')}: {ex}")
    entries.sort(key=lambda x: x["ts"] or 0, reverse=True)
    return {"entries": entries[:3000], "errors": errors, "lines": lines}


def _ts(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# แท็บ Services / Task Scheduler / Programs ของหน้ารายละเอียดเครื่อง (ดึงสด ไม่เก็บ)
# ---------------------------------------------------------------------------
NEED_MODULE = "1.1.4"


class NeedUpdate(winrm_client.WinRMError):
    pass


def _ver(v):
    try:
        return tuple(int(x) for x in str(v).split(".")[:3])
    except ValueError:
        return ()


def _need(w, what):
    have = (w.metrics.module or "").strip()
    if have and _ver(have) < _ver(NEED_MODULE):
        raise NeedUpdate(f"{what} ต้องใช้ module {NEED_MODULE} ขึ้นไป (เครื่องนี้ {have}) - "
                         f"อัปเดตได้จาก Settings → แก้ไขเครื่องนี้ → อัปเดตบนเครื่อง (ใช้บัญชี admin ครั้งเดียว)")


def win_services(target):
    w = _worker(target)
    _need(w, "รายการ Service แบบละเอียด")
    r = w.call("Get-CMServices", {"All": True, "Detail": True})
    watch = {str(x).lower() for x in target.get("watch_services") or []}
    rows = []
    for x in r.get("services") or []:
        rows.append({"name": x.get("name"), "display": x.get("display"), "status": x.get("status"),
                     "state": windows_pull._svc_status(x), "start": x.get("start"), "delayed": bool(x.get("delayed")),
                     "path": x.get("path") or "", "account": x.get("account") or "", "desc": x.get("desc") or "",
                     "pid": x.get("pid"), "watched": str(x.get("name") or "").lower() in watch})
    rows.sort(key=lambda x: (str(x["display"] or x["name"] or "").lower()))
    return {"time": datetime.now().astimezone().isoformat(), "os": "windows", "native": bool(r.get("native")),
            "services": rows}


# ผล Last Run Result ที่พบบ่อย (ค่าเดียวกับคอลัมน์ใน Task Scheduler)
TASK_RESULTS = {
    0x0: ("ok", "สำเร็จ (0x0)"),
    0x41300: ("ok", "พร้อมรัน"),
    0x41301: ("run", "กำลังทำงาน"),
    0x41302: ("off", "ถูกปิดอยู่"),
    0x41303: ("off", "ยังไม่เคยรัน"),
    0x41304: ("off", "ไม่มีรอบรันถัดไป"),
    0x41305: ("bad", "ตั้งค่าไม่ครบ"),
    0x41306: ("bad", "ถูกหยุดกลางคัน (terminated)"),
    0x41325: ("run", "อยู่ในคิว"),
    0x8004131F: ("bad", "รอบก่อนยังทำงานอยู่ (ไม่เริ่มรอบใหม่)"),
    0x8004130F: ("bad", "ไม่มีรหัสผ่านของบัญชีที่ใช้รัน"),
    0x800710E0: ("bad", "ถูกปฏิเสธ (operator / administrator refused)"),
    0x80070002: ("bad", "ไม่พบไฟล์ที่สั่งรัน"),
    0x80070005: ("bad", "Access denied"),
    0xC000013A: ("bad", "ถูกยกเลิก (Ctrl+C / ปิดเครื่อง)"),
    0x1: ("bad", "จบด้วย exit code 1"),
    0x2: ("bad", "จบด้วย exit code 2 (มักเป็นไม่พบไฟล์)"),
}


def task_result(hex_text, last_run):
    """'0x00041303' -> (ระดับ ok|run|off|bad, ข้อความ)"""
    try:
        code = int(str(hex_text), 16) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return ("off", "-")
    if code in TASK_RESULTS:
        return TASK_RESULTS[code]
    if not last_run:
        return ("off", f"0x{code:X}")
    return ("bad", f"จบด้วยรหัส 0x{code:X}" + (f" ({code})" if code < 0x10000 else ""))


def win_tasks(target, include_ms=False):
    w = _worker(target)
    _need(w, "รายการ Task Scheduler")
    r = w.call("Get-CMTasks", {"IncludeMicrosoft": bool(include_ms)}, wait=40)
    rows = []
    for t in r.get("tasks") or []:
        lvl, text = task_result(t.get("last_result"), t.get("last_run"))
        rows.append({**t, "result_level": lvl, "result_text": text})
    rows.sort(key=lambda x: str(x.get("path") or "").lower())
    return {"time": datetime.now().astimezone().isoformat(), "os": "windows", "tasks": rows,
            "denied": r.get("denied") or 0, "truncated": bool(r.get("truncated")),
            "microsoft": bool(include_ms)}


def win_programs(target):
    w = _worker(target)
    _need(w, "รายการโปรแกรมที่รันอยู่ (exe)")
    r = w.call("Get-CMProcesses", {"All": True}, wait=40)
    ram_mb = ((w.last_metrics or {}).get("mem_total_mb") or 0) or 0
    rows = []
    for x in r.get("processes") or []:
        rows.append({**x, "memory": round((x.get("ws_mb") or 0) / ram_mb * 100, 2) if ram_mb else None,
                     "self": x.get("pid") == r.get("self_pid")})
    return {"time": datetime.now().astimezone().isoformat(), "os": "windows", "native": bool(r.get("native")),
            "interval": r.get("interval"), "cores": r.get("cores"), "count": len(rows), "processes": rows}
