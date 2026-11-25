"""
windows_ondemand.py - ปุ่มบนหน้าเว็บสำหรับเครื่อง Windows (type: winrm)  เรียกผ่าน ondemand.py (พอร์ต 8001 ภายใน)
==================================================================================================================

    POST /winrm-test   {host, port, user, password?, thumbprint, name?}   ทดสอบการเชื่อมต่อจากฟอร์ม Settings
                       password ว่าง + มี name -> ใช้รหัสที่เก็บไว้ของเครื่องนั้น
    GET  /winrm-cert?host=&port=                                          อ่าน thumbprint ของใบรับรอง (Scan certificate)
    GET  /processes?target=      (winrm)   CPU ต่อ core + process ที่ใช้ CPU / RAM สูงสุด (วัดสด ไม่เก็บ)
    GET  /info-logs?target=      (winrm)   Event log ระดับ Information + บรรทัด INFO ท้ายไฟล์ log (แสดงอย่างเดียว ไม่เก็บ)
    GET  /log-check?target=      (winrm)   ไฟล์ log ที่ตั้งไว้มีจริง / อยู่ใน allowed-paths.txt ไหม

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
