"""
ntp_monitor.py - ตรวจ NTP server (หน้า NTP Monitor)
====================================================

ทำงานเป็น thread แยกใน central-puller
    ทุก ntp.interval วินาที (ปริยาย 30) ส่งคำขอ NTP (SNTP v4, UDP 123) จาก central ไปที่แต่ละ server ใน puller.yaml
    วัด: ตอบไหม · offset (นาฬิกา server - นาฬิกา central) · เวลาไป-กลับ (RTT) · stratum · leap · refid

puller.yaml
    ntp:
      interval: 30                  # วินาที (15-600)
      offset_warning_ms: 500        # |offset| ตั้งแต่ค่านี้ = Warning
      offset_error_ms: 2000         # |offset| ตั้งแต่ค่านี้ = Error
      rtt_warning_ms: 200           # RTT ตั้งแต่ค่านี้ = Warning
      servers:
        - {name: ntp-dc1, host: 10.0.0.1, port: 123, group: TQ/IT, paused: false, notify: true}

สถานะของแต่ละ server
    down     ไม่ตอบ (timeout / ตอบผิดรูปแบบ / Kiss-o'-Death)
    error    server เองไม่ sync (stratum 16 / leap = 3) หรือ |offset| >= offset_error_ms
    warning  |offset| >= offset_warning_ms หรือ RTT >= rtt_warning_ms
    ok       ปกติ
    เปลี่ยนสถานะเมื่อเห็นซ้ำติดกัน sensors.confirm_checks รอบ (เหมือน sensor ของเครื่อง) แล้วแจ้ง Teams (การ์ดหัวข้อ Alert)

ผลลัพธ์
    data/state/ntp.json            ผลล่าสุดของทุก server (หน้าเว็บอ่านผ่าน web_dashboard/backend/ntp_service.py)
    Prometheus (job central-puller) cm_ntp_up / cm_ntp_offset_seconds / cm_ntp_rtt_seconds / cm_ntp_stratum  -> กราฟย้อนหลัง
"""

import json
import os
import socket
import struct
import tempfile
import threading
import time

from config import STATE_DIR, load_config
import metrics
import notify

STATE_FILE = os.path.join(STATE_DIR, "ntp.json")
NTP_EPOCH = 2208988800            # วินาทีจาก 1900-01-01 ถึง 1970-01-01
TIMEOUT = 3
DEFAULTS = {"interval": 30, "offset_warning_ms": 500, "offset_error_ms": 2000, "rtt_warning_ms": 200}
RANK = {"ok": 0, "warning": 1, "error": 2, "down": 3}
LEVEL_TEXT = {"ok": "OK", "warning": "Warning", "error": "Error", "down": "Down"}


# ------------------------------------------------------------------
# SNTP client
# ------------------------------------------------------------------

def _ts(raw):
    """NTP timestamp 64 บิต -> unix (float)"""
    sec, frac = struct.unpack("!II", raw)
    return sec - NTP_EPOCH + frac / 2 ** 32


def query(host, port=123, timeout=TIMEOUT, clock=time.time):
    """ถาม NTP server 1 ครั้ง -> dict (ok=False + error เมื่อไม่ตอบ)"""
    pkt = bytearray(48)
    pkt[0] = (0 << 6) | (4 << 3) | 3                 # LI=0, VN=4, Mode=3 (client)
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_DGRAM)
    except OSError as e:
        return {"ok": False, "error": f"resolve {host}: {e.strerror or e}"}
    fam, _, _, _, addr = infos[0]
    s = socket.socket(fam, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        t1 = clock()
        sec = int(t1) + NTP_EPOCH
        pkt[40:48] = struct.pack("!II", sec, int((t1 % 1) * 2 ** 32))   # transmit timestamp (ใช้เทียบ origin)
        s.sendto(bytes(pkt), addr)
        data, _ = s.recvfrom(512)
        t4 = clock()
    except socket.timeout:
        return {"ok": False, "error": f"no reply in {timeout}s (UDP {port} blocked or service stopped)"}
    except OSError as e:
        return {"ok": False, "error": e.strerror or str(e)}
    finally:
        s.close()
    return parse(data, bytes(pkt[40:48]), t1, t4)


def parse(data, origin, t1, t4):
    """แปลงคำตอบ NTP -> dict   offset = ((t2-t1)+(t3-t4))/2 · rtt = (t4-t1)-(t3-t2)"""
    if len(data) < 48:
        return {"ok": False, "error": "short reply"}
    li, mode, stratum = data[0] >> 6, data[0] & 7, data[1]
    if mode not in (4, 5):
        return {"ok": False, "error": f"unexpected mode {mode}"}
    if data[24:32] != origin:
        return {"ok": False, "error": "reply does not match request"}
    refid_raw = data[12:16]
    if stratum == 0:                                  # Kiss-o'-Death เช่น RATE / DENY
        code = refid_raw.decode("ascii", "replace").strip("\x00")
        return {"ok": False, "error": f"kiss-o'-death {code}", "stratum": 0}
    t2, t3 = _ts(data[32:40]), _ts(data[40:48])
    ref = _ts(data[16:24]) if data[16:24] != b"\x00" * 8 else None
    refid = refid_raw.decode("ascii", "replace").strip("\x00") if stratum == 1 else ".".join(str(b) for b in refid_raw)
    return {"ok": True, "offset_ms": round(((t2 - t1) + (t3 - t4)) / 2 * 1000, 3),
            "rtt_ms": round(max(0.0, (t4 - t1) - (t3 - t2)) * 1000, 3), "stratum": stratum, "leap": li,
            "refid": refid, "ref_age_s": round(t3 - ref, 1) if ref else None,
            "root_delay_ms": round(struct.unpack("!i", data[4:8])[0] / 65.536, 3),
            "root_dispersion_ms": round(struct.unpack("!I", data[8:12])[0] / 65.536, 3)}


# ------------------------------------------------------------------
# สถานะ
# ------------------------------------------------------------------

def settings(config):
    n = (config or {}).get("ntp") or {}
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        try:
            out[k] = float(n.get(k, DEFAULTS[k]))
        except (TypeError, ValueError):
            pass
    out["interval"] = max(15, min(600, int(out["interval"])))
    try:
        out["confirm"] = max(1, min(int(((config or {}).get("sensors") or {}).get("confirm_checks", 6)), 120))
    except (TypeError, ValueError):
        out["confirm"] = 6
    out["servers"] = [s for s in n.get("servers") or [] if isinstance(s, dict) and s.get("name") and s.get("host")]
    return out


def grade(r, cfg):
    """ผลตรวจ 1 ครั้ง -> (สถานะ, ข้อความ)"""
    if not r.get("ok"):
        return "down", r.get("error") or "no reply"
    off, rtt = abs(r["offset_ms"]), r["rtt_ms"]
    base = f"offset {r['offset_ms']:+.1f} ms · RTT {rtt:.1f} ms · stratum {r['stratum']}"
    if r["stratum"] >= 16 or r["leap"] == 3:
        return "error", "server itself is not synchronized (stratum 16 / leap alarm) · " + base
    if off >= cfg["offset_error_ms"]:
        return "error", f"offset {r['offset_ms']:+.0f} ms vs central (error ≥ {cfg['offset_error_ms']:g} ms) · RTT {rtt:.1f} ms"
    if off >= cfg["offset_warning_ms"]:
        return "warning", f"offset {r['offset_ms']:+.0f} ms vs central (warning ≥ {cfg['offset_warning_ms']:g} ms) · RTT {rtt:.1f} ms"
    if rtt >= cfg["rtt_warning_ms"]:
        return "warning", f"slow reply RTT {rtt:.0f} ms (warning ≥ {cfg['rtt_warning_ms']:g} ms) · offset {r['offset_ms']:+.1f} ms"
    return "ok", base


def step(prev, raw, cfg, now):
    """รวมผลรอบนี้กับสถานะเดิม (ยืนยัน N รอบ) -> (สถานะใหม่, เหตุการณ์ หรือ None)
    prev = {"status", "since", "pending": [สถานะ, จำนวน]}"""
    st, msg = grade(raw, cfg)
    cur = dict(prev or {})
    confirmed = cur.get("status")
    if confirmed is None:                             # เห็นครั้งแรก: ถือเป็นสถานะเริ่มต้น ไม่แจ้ง
        cur.update(status=st, since=now, pending=None)
        return dict(cur, **raw, message=msg, raw_status=st, checked_at=now), None
    event = None
    if st == confirmed:
        cur["pending"] = None
    else:
        p = cur.get("pending") or [st, 0]
        p = [st, p[1] + 1] if p[0] == st else [st, 1]
        cur["pending"] = p
        if p[1] >= cfg["confirm"]:
            event = {"from": confirmed, "to": st, "duration": now - (cur.get("since") or now)}
            cur.update(status=st, since=now, pending=None)
    return dict(cur, **{k: v for k, v in raw.items()}, message=msg, raw_status=st, checked_at=now), event


# ------------------------------------------------------------------
# แจ้งเตือน
# ------------------------------------------------------------------

def _notify(server, r, event, config):
    if server.get("notify") is False:
        return
    s = notify.settings(config)
    to, frm = event["to"], event["from"]
    k = "recovered" if to == "ok" else to
    if not s["enabled"] or k not in s["levels"] or (k == "recovered" and frm == "ok"):
        return
    name, host = server["name"], str(server.get("host"))
    if k == "recovered":
        text = f"NTP server {name} กลับมาปกติ"
    elif to == "down":
        text = f"NTP server {name} ไม่ตอบ"
    elif r.get("ok") and (r.get("stratum", 0) >= 16 or r.get("leap") == 3):
        text = f"NTP server {name} ไม่ sync เอง (stratum {r.get('stratum')})"
    else:
        text = f"NTP server {name} เวลาคลาด / ตอบช้า"
    val = f"offset {r['offset_ms']:+.1f} ms · RTT {r['rtt_ms']:.1f} ms · stratum {r['stratum']}" if r.get("ok") else ""
    ctx = {"machine": name, "host": host, "group": server.get("group") or "",
           "device": notify._device(name, host, server.get("group") or ""), "sensor": "NTP server",
           "level": notify.LABEL.get(k, k), "status": f"{notify.LABEL.get(k, k)} – {text}", "value": val,
           "threshold": f"offset ≥ {config_ntp(config)['offset_warning_ms']:g} / {config_ntp(config)['offset_error_ms']:g} ms",
           "message": r.get("message") or "", "time": notify._time_full(), "icon": notify.ICON.get(k, ""),
           "duration": notify._dur(event["duration"]) if event.get("duration") and k == "recovered" else ""}
    notify._enqueue(s["url"], notify.card_templates.card("alert", k, ctx, s["cards"], f"{notify.ICON.get(k, '')} {text}"),
                    f"ntp {name} {to}")


def config_ntp(config):
    return settings(config)


# ------------------------------------------------------------------
# loop
# ------------------------------------------------------------------

def _read():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("servers") or {} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(servers, now):
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".ntp.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"updated_at": now, "servers": servers}, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def _export(name, host, r):
    lab = (name, host)
    metrics.ntp_up.labels(*lab).set(1 if r.get("ok") else 0)
    if r.get("ok"):
        metrics.ntp_offset.labels(*lab).set(r["offset_ms"] / 1000)
        metrics.ntp_rtt.labels(*lab).set(r["rtt_ms"] / 1000)
        metrics.ntp_stratum.labels(*lab).set(r["stratum"])
    else:
        for g in (metrics.ntp_offset, metrics.ntp_rtt, metrics.ntp_stratum):
            try:
                g.remove(*lab)
            except KeyError:
                pass


def _drop(name, host):
    for g in (metrics.ntp_up, metrics.ntp_offset, metrics.ntp_rtt, metrics.ntp_stratum):
        try:
            g.remove(name, host)
        except KeyError:
            pass


def run_once(state, config, now=None, ask=query):
    """ตรวจทุก server 1 รอบ -> state ใหม่ (ใช้ใน loop และ tests)"""
    now = now or time.time()
    cfg = settings(config)
    out = {}
    for srv in cfg["servers"]:
        name, host = str(srv["name"]), str(srv["host"])
        try:
            port = int(srv.get("port") or 123)
        except (TypeError, ValueError):
            port = 123
        prev = state.get(name) or {}
        if prev.get("host") not in (None, host):
            prev = {}
        if srv.get("paused"):
            out[name] = {"host": host, "port": port, "group": srv.get("group") or "", "status": "paused",
                         "message": "Paused", "checked_at": now}
            _drop(name, host)
            continue
        raw = ask(host, port)
        cur, event = step({k: prev.get(k) for k in ("status", "since", "pending")} if prev.get("status") != "paused" else {},
                          raw, cfg, now)
        cur.update(host=host, port=port, group=srv.get("group") or "")
        out[name] = cur
        _export(name, host, raw)
        if event:
            try:
                _notify(srv, cur, event, config)
            except Exception as e:  # noqa: BLE001
                print(f"[NTP NOTIFY ERROR] {name}: {e!r}")
    for name, old in state.items():                    # server ที่ถูกลบออกจาก puller.yaml
        if name not in out:
            _drop(name, str(old.get("host")))
    return out


def _loop():
    state = _read()
    while True:
        cfg = None
        try:
            config = load_config()
            cfg = settings(config)
            state = run_once(state, config)
            _write(state, time.time())
        except Exception as e:  # noqa: BLE001
            print(f"[NTP ERROR] {e!r}")
        time.sleep(cfg["interval"] if cfg else 30)


def start():
    threading.Thread(target=_loop, daemon=True, name="ntp-monitor").start()
    print("[NTP] NTP server monitor started")
