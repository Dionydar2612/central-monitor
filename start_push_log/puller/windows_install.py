"""
windows_install.py - ติดตั้ง Central Monitor บนเครื่อง Windows จากหน้าเว็บ (ไม่ต้องไปที่เครื่อง)
==============================================================================================

ปุ่ม "ติดตั้งให้เครื่องนี้" / "ถอนการติดตั้ง" ใน Settings > การเชื่อมต่อ  (central-api -> ondemand.py POST /winrm-install)
body.action = install (setup-cm-monitor.ps1) | uninstall (remove-cm-monitor.ps1 -> remove-result.json)

ขั้นตอน (ทำใน thread แยก หน้าเว็บถามสถานะทุก ~3 วินาที: GET /winrm-install-status?name=)
    1. เชื่อมต่อ WinRM ของเครื่อง (5985 HTTP / 5986 HTTPS) ด้วยบัญชี admin ที่กรอก (NTLM / Kerberos, ข้อความเข้ารหัส)
       รหัส admin ใช้ครั้งนี้ครั้งเดียว: ไม่เขียนลงดิสก์ ไม่ลง log ไม่ส่งกลับหน้าเว็บ
    2. เขียนไฟล์ชุดติดตั้งลง C:\\ProgramData\\CentralMonitor-setup (admin / SYSTEM เท่านั้น)
       CentralIP = auto -> ใช้ IP ของ central ที่เครื่อง Windows เห็นจริง (จากการเชื่อมต่อ WinRM ครั้งนี้)
    3. เริ่ม setup-cm-monitor.ps1 เป็น process แยก (Win32_Process.Create) เพราะ setup restart WinRM
       ซึ่งจะตัด session นี้ (process ลูกของ session จะถูกปิดด้วย)
    4. ต่อใหม่ทุก 5 วินาทีจนเจอ setup-result.json (สูงสุด 5 นาที) อ่านผล + log แล้วลบโฟลเดอร์ทิ้ง
       (ในนั้นมีรหัสของ cm-monitor)
    ผล: {state: done|failed, ok, thumbprint, port, warnings, log, error}  central-api เก็บรหัส + thumbprint เอง
"""

import base64
import threading
import time

import winrm_client

SETUP_DIR = r"C:\ProgramData\CentralMonitor-setup"
MAX_WAIT = 300
JOB_KEEP = 1800

_jobs = {}
_lock = threading.Lock()


def _b64(text_or_bytes):
    data = text_or_bytes.encode("utf-8") if isinstance(text_or_bytes, str) else text_or_bytes
    return base64.b64encode(data).decode("ascii")


def _ps_str(value):
    return "'" + str(value).replace("'", "''") + "'"


def _pool(host, port, user, password):
    from pypsrp.powershell import RunspacePool
    from pypsrp.wsman import WSMan
    https = int(port) == 5986
    wsman = WSMan(host, port=int(port), ssl=https, auth="negotiate", encryption="auto",
                  username=user, password=password, cert_validation=False,
                  connection_timeout=15, operation_timeout=60, read_timeout=90)
    return RunspacePool(wsman)          # endpoint ปกติ Microsoft.PowerShell (ต้องเป็น admin)


def _run(pool, script):
    from pypsrp.powershell import PowerShell
    ps = PowerShell(pool)
    ps.add_script(script)
    out = ps.invoke()
    if ps.had_errors:
        raise winrm_client.WinRMError("; ".join(str(e) for e in ps.streams.error)[:400])
    return "\n".join(str(o) for o in out if o is not None)


def build_start_script(files, config_text, port, run_file="setup-cm-monitor.ps1"):
    """สคริปต์ที่รันบนเครื่อง Windows: เขียนไฟล์ (base64 อย่างเดียว ไม่มีข้อความจากผู้ใช้ปนในคำสั่ง) แล้วเริ่ม run_file
    config_text = None (ถอนการติดตั้ง) -> ไม่เขียน cm-monitor-config.txt"""
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$d = {_ps_str(SETUP_DIR)}",
        "if (Test-Path -LiteralPath $d) { Remove-Item -LiteralPath $d -Recurse -Force }",
        "New-Item -ItemType Directory -Path $d -Force | Out-Null",
        "& icacls.exe $d /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' | Out-Null",
        # IP ของ central ที่เครื่องนี้เห็น = ปลายทางของการเชื่อมต่อ WinRM ที่ใช้อยู่ตอนนี้
        f"$c = @(Get-NetTCPConnection -State Established -LocalPort {int(port)} -ErrorAction SilentlyContinue |"
        " Select-Object -ExpandProperty RemoteAddress -Unique)",
        "$central = if ($c.Count -eq 1) { [string]$c[0] } else { '' }",
    ]
    for name, data in files.items():
        if not all(ch.isalnum() or ch in "-_." for ch in name):
            raise ValueError(f"bad file name {name!r}")
        lines.append(f"[IO.File]::WriteAllBytes((Join-Path $d '{name}'), [Convert]::FromBase64String('{_b64(data)}'))")
    if config_text is not None:
        lines += [
            f"$cfg = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{_b64(config_text)}'))",
            "if ($cfg -match 'CentralIP=auto') { if (-not $central) { throw 'cannot detect the central IP - type it in the form' }; $cfg = $cfg.Replace('CentralIP=auto', 'CentralIP=' + $central) }",
            "[IO.File]::WriteAllText((Join-Path $d 'cm-monitor-config.txt'), $cfg, (New-Object Text.UTF8Encoding($true)))",
        ]
    if run_file not in files:
        raise ValueError(f"{run_file} not in files")
    lines += [
        "Get-ChildItem -LiteralPath $d -File | Unblock-File",
        f"$cmd = 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"' + $d + '\\{run_file}\" unattended'",
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $d }",
        "if ($r.ReturnValue -ne 0) { throw ('cannot start setup (Win32_Process.Create ' + $r.ReturnValue + ')') }",
        "'STARTED ' + $r.ProcessId + ' ' + $central",
    ]
    return "\n".join(lines)


def poll_script(kind):
    prefix = "setup" if kind == "install" else "remove"
    return POLL_SCRIPT.replace("setup-result.json", f"{prefix}-result.json").replace("'setup-*.log'", f"'{prefix}-*.log'")


POLL_SCRIPT = "\n".join([
    f"$d = {_ps_str(SETUP_DIR)}",
    "$f = Join-Path $d 'setup-result.json'",
    "$l = Get-ChildItem -LiteralPath (Join-Path $d 'logs') -Filter 'setup-*.log' -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1",
    "$last = if ($l) { (Get-Content -LiteralPath $l.FullName -Tail 1) } else { '' }",
    "$tail = if ($l) { (Get-Content -LiteralPath $l.FullName -Tail 80) -join [char]10 } else { '' }",
    "if (Test-Path -LiteralPath $f) { 'DONE' + [char]10 + (Get-Content -LiteralPath $f -Raw) + [char]10 + '@@LOG' + [char]10 + $tail }",
    "else { 'RUNNING' + [char]10 + $last }",
])

CLEANUP_SCRIPT = f"$d = {_ps_str(SETUP_DIR)}; if (Test-Path -LiteralPath $d) {{ Remove-Item -LiteralPath $d -Recurse -Force }}; 'CLEAN'"


def _set(name, **kw):
    with _lock:
        job = _jobs.setdefault(name, {})
        job.update(kw)
        job["updated"] = time.time()


def _job(name, host, port, user, password, files, config_text, kind="install"):
    import json
    run_file = "setup-cm-monitor.ps1" if kind == "install" else "remove-cm-monitor.ps1"
    what = "setup" if kind == "install" else "ตัวถอน"
    try:
        _set(name, state="running", kind=kind, step=f"เชื่อมต่อ {host}:{port} ด้วย {user}")
        with _pool(host, port, user, password) as pool:
            _set(name, step=f"คัดลอกไฟล์และเริ่ม{what}")
            out = _run(pool, build_start_script(files, config_text, port, run_file))
        if not out.startswith("STARTED"):
            raise winrm_client.WinRMError(f"{run_file} did not start: {out[:200]}")
        central = out.split()[2] if len(out.split()) > 2 else ""
        _set(name, step=f"{what}กำลังทำงาน (WinRM จะ restart 1 ครั้ง)", central=central)
        started = time.time()
        result, log = None, ""
        time.sleep(8)
        while time.time() - started < MAX_WAIT:
            try:
                with _pool(host, port, user, password) as pool:
                    text = _run(pool, poll_script(kind))
                if text.startswith("DONE"):
                    body, _, log = text[5:].partition("@@LOG")
                    result = json.loads(body.strip().lstrip("\ufeff"))
                    break
                last = text[8:].strip()
                if last:
                    _set(name, step=f"{what}: " + last[9:120] if len(last) > 9 else last[:120])
            except Exception as e:  # noqa: BLE001  WinRM กำลัง restart -> ลองใหม่
                _set(name, step=f"รอ WinRM กลับมา ({str(e)[:80]})")
            time.sleep(5)
        if result is None:
            raise winrm_client.WinRMError(f"{what}ไม่จบภายใน 5 นาที - ดู log บนเครื่อง: " + SETUP_DIR + "\\logs")
        try:
            with _pool(host, port, user, password) as pool:
                _run(pool, CLEANUP_SCRIPT)
        except Exception as e:  # noqa: BLE001
            print(f"[WINRM INSTALL] {name} cleanup failed: {e}")
        ok = bool(result.get("ok"))
        _set(name, state="done" if ok else "failed", ok=ok, thumbprint=str(result.get("thumbprint") or "").upper(),
             port=result.get("port"), warnings=result.get("warnings") or 0, error=result.get("error") or "",
             leftovers=result.get("leftovers") or [], computer=result.get("computer"), version=result.get("version"),
             log=log.strip()[-6000:],
             step="เสร็จ" if ok else f"{what}ล้ม")
        print(f"[WINRM {kind.upper()}] {name} {'OK' if ok else 'FAILED'} {result.get('error') or ''}")
        if ok and kind == "install":
            try:
                import windows_pull
                windows_pull.reconnect(name)          # session ใหม่ = โหลด module รุ่นที่เพิ่งติดตั้ง
            except Exception as e:  # noqa: BLE001
                print(f"[WINRM INSTALL] {name} reconnect: {e}")
    except Exception as e:  # noqa: BLE001
        msg = winrm_client._friendly(e) if not isinstance(e, winrm_client.WinRMError) else str(e)
        low = msg.lower()
        if "รหัสผ่าน" in msg or "access is denied" in low or "401" in msg:
            msg += (" · ใช้บัญชี admin ที่ล็อกอินทาง network ได้ (Administrator ของเครื่อง หรือ domain admin;"
                    " local admin อื่นถูก UAC remote restriction บล็อก) หรือใช้วิธีดาวน์โหลดชุดติดตั้ง")
        _set(name, state="failed", ok=False, error=msg[:600], step="ล้ม")
        print(f"[WINRM {kind.upper()}] {name} FAILED {msg[:200]}")


def start(body):
    name = str(body.get("name") or "")
    with _lock:
        for k in [k for k, v in _jobs.items() if time.time() - v.get("updated", 0) > JOB_KEEP]:
            _jobs.pop(k, None)
        if _jobs.get(name, {}).get("state") == "running":
            raise winrm_client.WinRMError("กำลังติดตั้ง / ถอนเครื่องนี้อยู่")
        kind = "uninstall" if body.get("action") == "uninstall" else "install"
        _jobs[name] = {"state": "running", "kind": kind, "step": "เริ่ม", "updated": time.time(),
                       "delete_after": bool(body.get("delete_after")), "mode": str(body.get("mode") or kind)}
    if not winrm_client.HAVE_PYPSRP:
        _set(name, state="failed", error="puller image ไม่มี pypsrp - รัน cm-update --rebuild")
        return status(name)
    files = {k: base64.b64decode(v) for k, v in dict(body.get("files_b64") or {}).items()}
    config = body.get("config")
    args = (name, str(body["host"]), int(body.get("port") or 5985), str(body["user"]), str(body["password"]),
            files, None if config is None else str(config), kind)
    threading.Thread(target=_job, args=args, daemon=True, name=f"winrm-install-{name}").start()
    return status(name)


def status(name):
    with _lock:
        job = dict(_jobs.get(name) or {})
    return job or {"state": "none"}
