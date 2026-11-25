#!/usr/bin/env python3
"""
win-poc.py - Central Monitor: PoC tester for agentless Windows monitoring (phase 1)

Connects from central to the JEA endpoint "CentralMonitor" on a Windows machine
(WinRM HTTPS, user cm-monitor) and calls the read-only Get-CM* functions on the
same schedule the real puller will use:

    Get-CMMetrics   every 30 s       Get-CMServices  every 60 s
    Get-CMEvents    every 120 s      Get-CMLogTail   every 120 s (only with --log)
    Get-CMInventory every 3600 s     session re-opened every 6 h

It measures what the PoC must prove (no averages - maximum values only):
    - CPU of the Windows session process (wsmprovhost), % of one core, per interval (max)
    - RAM of that process (max)
    - time per call (max) and errors
    - commands outside the JEA list are refused

Nothing is stored except a small summary JSON (--out, default ./win-poc-<host>.json,
rewritten every cycle) and, with --raw, the raw replies capped at --raw-max-mb.

Run with scripts/win-poc.sh (creates a venv with pypsrp). Password: env CM_WIN_PASSWORD
or typed at the prompt (never on the command line).

Examples
  scripts/win-poc.sh --host 192.168.181.101 --thumbprint AB12... --once
  scripts/win-poc.sh --host 192.168.181.101 --thumbprint AB12... --hours 24 --services W3SVC,MSSQLSERVER
  scripts/win-poc.sh --host 192.168.181.101 --thumbprint AB12... --log 'D:\\THMux\\logs\\app.log'
"""
import argparse
import getpass
import hashlib
import json
import os
import signal
import socket
import ssl
import sys
import time

try:
    from pypsrp.powershell import PowerShell, RunspacePool
    from pypsrp.wsman import WSMan
except ImportError:  # pragma: no cover
    sys.exit("pypsrp is not installed - run scripts/win-poc.sh (it creates a venv with pypsrp)")

ENDPOINT = "CentralMonitor"
SESSION_MAX_AGE = 6 * 3600
SCHEDULE = {"metrics": 30, "services": 60, "events": 120, "logtail": 120, "inventory": 3600}
GATE_CPU_PCT = 2.0      # PoC pass: wsmprovhost CPU max < 2% of one core
GATE_RAM_MB = 150.0     # PoC pass: wsmprovhost RAM max < 150 MB


def log(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


def server_thumbprint(host, port, timeout=10):
    """SHA1 of the server certificate (= Windows 'Thumbprint'), uppercase hex."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    return hashlib.sha1(der).hexdigest().upper()


def norm_thumb(t):
    return "".join(ch for ch in (t or "") if ch.isalnum()).upper()


class Stats:
    def __init__(self, host):
        self.host = host
        self.started = time.time()
        self.calls = {}           # name -> {"n", "err", "max_s", "max_kb"}
        self.cpu_max = 0.0        # % of one core, per interval
        self.ram_max = 0.0
        self.sessions = 0
        self.jea_blocked = None
        self.last_errors = []
        self._prev_cpu = None     # (pid, cpu_s, wall)

    def call(self, name, secs, nbytes, error=None):
        c = self.calls.setdefault(name, {"n": 0, "err": 0, "max_s": 0.0, "max_kb": 0.0})
        c["n"] += 1
        c["max_s"] = max(c["max_s"], round(secs, 3))
        c["max_kb"] = max(c["max_kb"], round(nbytes / 1024, 1))
        if error:
            c["err"] += 1
            self.last_errors = (self.last_errors + [f"{time.strftime('%Y-%m-%d %H:%M:%S')} {name}: {error}"])[-20:]

    def self_sample(self, s):
        """s = 'self' block from a Get-CM* reply: pid, ws_mb, cpu_s"""
        if not s:
            return None
        now = time.time()
        self.ram_max = max(self.ram_max, float(s.get("ws_mb", 0)))
        pct = None
        if self._prev_cpu and self._prev_cpu[0] == s.get("pid"):
            dt = now - self._prev_cpu[2]
            if dt > 1:
                pct = max(0.0, (float(s["cpu_s"]) - self._prev_cpu[1]) / dt * 100)
                self.cpu_max = max(self.cpu_max, pct)
        self._prev_cpu = (s.get("pid"), float(s.get("cpu_s", 0)), now)
        return pct

    def summary(self):
        return {
            "host": self.host,
            "endpoint": ENDPOINT,
            "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started)),
            "hours": round((time.time() - self.started) / 3600, 2),
            "sessions_opened": self.sessions,
            "wsmprovhost_cpu_pct_max": round(self.cpu_max, 3),
            "wsmprovhost_ram_mb_max": round(self.ram_max, 1),
            "jea_blocks_other_commands": self.jea_blocked,
            "calls": self.calls,
            "gate": {
                "cpu_ok": self.cpu_max < GATE_CPU_PCT,
                "ram_ok": self.ram_max < GATE_RAM_MB,
                "jea_ok": self.jea_blocked is True,
                "errors": sum(c["err"] for c in self.calls.values()),
            },
            "last_errors": self.last_errors,
        }


class Client:
    def __init__(self, args, password):
        self.a = args
        self.password = password
        self.pool = None
        self.opened = 0.0

    def open(self):
        self.close()
        wsman = WSMan(
            self.a.host, port=self.a.port, ssl=True, auth="ntlm", encryption="auto",
            username=self.a.user, password=self.password,
            cert_validation=False,            # self-signed: pinned by thumbprint before every open
            connection_timeout=10, operation_timeout=20, read_timeout=30,
        )
        self.pool = RunspacePool(wsman, configuration_name=ENDPOINT)
        self.pool.open()
        self.opened = time.time()

    def close(self):
        if self.pool is not None:
            try:
                self.pool.close()
            except Exception:
                pass
        self.pool = None

    def run(self, cmd, params=None):
        ps = PowerShell(self.pool)
        ps.add_cmdlet(cmd)
        for k, v in (params or {}).items():
            if v is True:
                ps.add_parameter(k)
            else:
                ps.add_parameter(k, v)
        out = ps.invoke()
        if ps.had_errors:
            msgs = "; ".join(str(e) for e in ps.streams.error) or "error"
            raise RuntimeError(msgs)
        text = "".join(str(o) for o in out if o is not None)
        return text

    def run_script(self, script):
        ps = PowerShell(self.pool)
        ps.add_script(script)
        ps.invoke()
        return ps.had_errors


def main():
    ap = argparse.ArgumentParser(description="PoC tester for Central Monitor agentless Windows monitoring")
    ap.add_argument("--host", required=True, help="IP / name of the Windows machine")
    ap.add_argument("--port", type=int, default=5986)
    ap.add_argument("--user", default="cm-monitor")
    ap.add_argument("--thumbprint", default="", help="certificate thumbprint shown by setup-cm-monitor.bat")
    ap.add_argument("--insecure", action="store_true", help="do not check the thumbprint (test only)")
    ap.add_argument("--once", action="store_true", help="call every function once, show results, exit")
    ap.add_argument("--hours", type=float, default=24.0, help="how long to run (default 24)")
    ap.add_argument("--services", default="", help="comma separated service names (empty = all Automatic services)")
    ap.add_argument("--eventlogs", default="System,Application")
    ap.add_argument("--log", action="append", default=[], help="log file to tail (must be in allowed-paths.txt); repeatable")
    ap.add_argument("--out", default="", help="summary JSON path (default ./win-poc-<host>.json)")
    ap.add_argument("--raw", action="store_true", help="also save raw replies (JSON lines)")
    ap.add_argument("--raw-max-mb", type=float, default=20.0)
    a = ap.parse_args()

    if not a.thumbprint and not a.insecure:
        ap.error("--thumbprint is required (or --insecure for a quick test)")
    password = os.environ.get("CM_WIN_PASSWORD") or getpass.getpass(f"password of {a.user}@{a.host}: ")
    out_path = a.out or f"win-poc-{a.host.replace(':', '_')}.json"
    raw_path = out_path[:-5] + ".raw.jsonl" if out_path.endswith(".json") else out_path + ".raw.jsonl"
    raw_cap = int(a.raw_max_mb * 1024 * 1024)

    st = Stats(a.host)
    cl = Client(a, password)
    services = [s.strip() for s in a.services.split(",") if s.strip()]
    evlogs = [s.strip() for s in a.eventlogs.split(",") if s.strip()]
    bookmarks = {}        # event log -> last record id
    tails = {p: {"offset": -1, "fp": ""} for p in a.log}
    last = {k: 0.0 for k in SCHEDULE}
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    backoff = [30, 60, 120, 300]
    fails = 0

    def save():
        tmp = out_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st.summary(), f, indent=2, ensure_ascii=False)
        os.replace(tmp, out_path)

    def raw(name, text):
        if not a.raw:
            return
        try:
            if os.path.exists(raw_path) and os.path.getsize(raw_path) > raw_cap:
                return
            with open(raw_path, "a") as f:
                f.write(json.dumps({"t": int(time.time()), "fn": name, "reply": text}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def call(name, cmd, params=None):
        t0 = time.time()
        try:
            text = cl.run(cmd, params)
        except Exception as e:  # noqa: BLE001
            st.call(name, time.time() - t0, 0, error=str(e)[:300])
            raise
        st.call(name, time.time() - t0, len(text.encode()))
        raw(name, text)
        return json.loads(text) if text else {}

    def open_session():
        if not a.insecure:
            got = server_thumbprint(a.host, a.port)
            if got != norm_thumb(a.thumbprint):
                raise RuntimeError(f"certificate thumbprint mismatch: server {got}, expected {norm_thumb(a.thumbprint)} - NOT connecting")
        cl.open()
        st.sessions += 1
        log(f"session opened ({ENDPOINT} on {a.host}:{a.port})")
        if st.jea_blocked is None:
            # JEA must refuse anything that is not a Get-CM* function
            refused = 0
            for test in ("Get-ChildItem C:\\", "Get-Process"):
                try:
                    if cl.run_script(test):
                        refused += 1
                except Exception:  # noqa: BLE001
                    refused += 1
            try:
                cl.run("Get-ChildItem", {"Path": "C:\\"})
            except Exception:  # noqa: BLE001
                refused += 1
            st.jea_blocked = refused == 3
            log("JEA check: other commands refused" if st.jea_blocked else "JEA check: WARNING - a command outside the list ran!")

    end = time.time() + a.hours * 3600
    while not stop["flag"]:
        now = time.time()
        try:
            if cl.pool is None or now - cl.opened > SESSION_MAX_AGE:
                open_session()
                last = {k: 0.0 for k in SCHEDULE}
            busy = False
            if now - last["metrics"] >= SCHEDULE["metrics"] or a.once:
                m = call("metrics", "Get-CMMetrics")
                last["metrics"] = now
                pct = st.self_sample(m.get("self"))
                vals = {(v["k"], v["i"]): v["v"] for v in m.get("values", [])}
                cpu = vals.get(("cpu_pct", ""), -1)
                busy = cpu >= 90
                drives = " ".join(f"{d['drive']}{100 - d['free_gb'] / d['size_gb'] * 100:.0f}%" for d in m.get("drives", []) if d.get("size_gb"))
                log(f"metrics: cpu {cpu:.1f}%  avail {vals.get(('mem_avail_mb', ''), -1):.0f}/{m.get('mem_total_mb')} MB  "
                    f"disk {drives}  | session {m['self']['ws_mb']} MB, cpu {'-' if pct is None else f'{pct:.3f}%'}  "
                    f"| {len(vals)} values")
            if now - last["services"] >= SCHEDULE["services"] or a.once:
                s = call("services", "Get-CMServices", {"Name": services} if services else None)
                last["services"] = now
                stopped = [x["name"] for x in s.get("services", []) if x["status"] != "Running"]
                log(f"services: {len(s.get('services', []))} checked, not running: {', '.join(stopped) or '-'}")
            if (now - last["events"] >= SCHEDULE["events"] or a.once) and not busy:
                for ln in evlogs:
                    e = call("events", "Get-CMEvents", {"LogName": ln, "AfterRecordId": bookmarks.get(ln, -1), "MaxEvents": 200})
                    prev = bookmarks.get(ln, -1)
                    if prev >= 0 and e.get("newest", 0) < prev:
                        bookmarks[ln] = e.get("newest", 0)          # log cleared
                    elif prev < 0:
                        bookmarks[ln] = e.get("newest", 0)          # first call: start from now
                    elif e.get("events"):
                        bookmarks[ln] = e["events"][-1]["rid"]
                    for ev in e.get("events", [])[:3]:
                        log(f"  {ln} L{ev['level']} {ev['provider']} {ev['id']}: {ev['msg'][:120]!r}")
                    log(f"events {ln}: {len(e.get('events', []))} new (bookmark {bookmarks[ln]})")
                last["events"] = now
            if tails and (now - last["logtail"] >= SCHEDULE["logtail"] or a.once) and not busy:
                for p, t in tails.items():
                    r = call("logtail", "Get-CMLogTail", {"Path": p, "Offset": t["offset"], "Fingerprint": t["fp"]})
                    t["offset"], t["fp"] = r["next_offset"], r["fingerprint"]
                    lines = r["text"].count("\n")
                    log(f"log {p}: {lines} new lines, size {r['size']}{' ROTATED' if r['rotated'] else ''}")
                last["logtail"] = now
            if (now - last["inventory"] >= SCHEDULE["inventory"] or a.once) and not busy:
                inv = call("inventory", "Get-CMInventory")
                last["inventory"] = now
                os_ = inv.get("os") or {}
                log(f"inventory: {os_.get('caption')} build {os_.get('build')}, boot {os_.get('boot_time')}, PS {inv.get('ps')}")
            fails = 0
        except Exception as e:  # noqa: BLE001
            wait = backoff[min(fails, len(backoff) - 1)]
            fails += 1
            st.last_errors = (st.last_errors + [f"{time.strftime('%Y-%m-%d %H:%M:%S')} {e}"])[-20:]
            log(f"ERROR: {e}  -> reconnect in {wait} s")
            cl.close()
            save()
            if a.once:
                break
            for _ in range(wait):
                if stop["flag"]:
                    break
                time.sleep(1)
            continue
        save()
        if a.once or time.time() >= end:
            break
        time.sleep(1)

    cl.close()
    save()
    s = st.summary()
    g = s["gate"]
    print()
    print(f"=== PoC summary {a.host}: {s['hours']} h, {s['sessions_opened']} session(s) ===")
    print(f"wsmprovhost CPU max : {s['wsmprovhost_cpu_pct_max']}% of one core  (gate < {GATE_CPU_PCT}%)  {'PASS' if g['cpu_ok'] else 'FAIL'}")
    print(f"wsmprovhost RAM max : {s['wsmprovhost_ram_mb_max']} MB  (gate < {GATE_RAM_MB:.0f} MB)  {'PASS' if g['ram_ok'] else 'FAIL'}")
    print(f"JEA blocks others   : {s['jea_blocks_other_commands']}  {'PASS' if g['jea_ok'] else 'FAIL'}")
    for name, c in s["calls"].items():
        print(f"  {name:10s} calls {c['n']:5d}  errors {c['err']:3d}  max {c['max_s']:6.2f} s  max {c['max_kb']:8.1f} KB")
    print(f"summary: {out_path}")
    if a.once:
        print("(--once: CPU needs two samples 30 s apart - run without --once for the real measurement)")
    return 0 if (g["ram_ok"] and g["jea_ok"] and (a.once or g["cpu_ok"])) else 1


if __name__ == "__main__":
    sys.exit(main())
