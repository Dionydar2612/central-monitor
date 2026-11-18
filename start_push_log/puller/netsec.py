"""
netsec.py - Network & Security ของเครื่อง Linux ผ่าน SSH (คำสั่งอ่านอย่างเดียว 1 ครั้งต่อรอบสถานะ)
=============================================================================================
เก็บใน snapshot ของ system_info (data/state/system/<เครื่อง>.json -> "netsec") หน้าเว็บ /network อ่านไปแสดง

    addrs        ip -4 -o addr                        IPv4 ของเครื่อง (ใช้จับคู่ว่าใครต่อหาใคร)
    routes       ip -4 route                          ตาราง route
    listening    ss -tuln(p)                          พอร์ตที่เปิดรับ (ขาเข้าได้ที่ไหน)
    connections  ss -tun(p) state established         การเชื่อมต่อที่ค้างอยู่ รวมตาม ขาเข้า/ขาออก + ปลายทาง + พอร์ต + โปรแกรม
                   in  = local port อยู่ในพอร์ตที่เปิดรับ (มีคนต่อเข้ามา)   out = เครื่องนี้ต่อออกไป
    firewall     ufw status verbose (ต้อง root: sudo -n ถ้าตั้ง NOPASSWD ไว้) ไม่ได้ -> /etc/ufw/ufw.conf + /etc/default/ufw
                 ไม่มี ufw -> firewalld (firewall-cmd) / iptables -S (sudo -n)
    logins       journalctl sshd 24 ชม. (หรือ /var/log/auth.log, /var/log/secure) -> สำเร็จ / ไม่สำเร็จ / ต้นทาง
ส่วนไหนอ่านไม่ได้ (สิทธิ์ไม่พอ / ไม่มีคำสั่ง) = ว่าง + note บอกเหตุผล ไม่ทำให้ส่วนอื่นล้ม
"""

import re
from collections import Counter

MAX_ROWS = 400

COMMAND = r"""
echo @@ADDR; ip -4 -o addr show 2>/dev/null
echo @@ROUTE; ip -4 route show 2>/dev/null
echo @@LISTEN; ss -H -tulnp 2>/dev/null || ss -H -tuln 2>/dev/null
echo @@CONN; ss -H -tunp state established 2>/dev/null || ss -H -tun state established 2>/dev/null
echo @@UFW; if command -v ufw >/dev/null 2>&1 || [ -x /usr/sbin/ufw ]; then (sudo -n ufw status verbose 2>/dev/null || /usr/sbin/ufw status verbose 2>&1) | head -n 400; else echo NOUFW; fi
echo @@UFWCONF; cat /etc/ufw/ufw.conf /etc/default/ufw 2>/dev/null | grep -E '^(ENABLED|DEFAULT_[A-Z]+_POLICY)='
echo @@FWD; if command -v firewall-cmd >/dev/null 2>&1; then (sudo -n firewall-cmd --state 2>&1 || firewall-cmd --state 2>&1); (sudo -n firewall-cmd --list-all 2>/dev/null || firewall-cmd --list-all 2>/dev/null) | head -n 80; fi
echo @@IPT; (sudo -n iptables -S 2>/dev/null) | head -n 300
echo @@AUTH; (journalctl -q --no-pager --since '24 hours ago' -o short-iso -t sshd -t sshd-session 2>/dev/null || true) | grep -E 'Failed |Accepted |Invalid user' | tail -n 3000
echo @@AUTHFILE; [ -r /var/log/auth.log ] && tail -n 5000 /var/log/auth.log | grep -E 'sshd.*(Failed |Accepted |Invalid user)' | tail -n 3000; [ -r /var/log/secure ] && tail -n 5000 /var/log/secure | grep -E 'sshd.*(Failed |Accepted |Invalid user)' | tail -n 3000
true
"""


def _sections(output):
    out, cur = {}, None
    for line in str(output or "").splitlines():
        if line.startswith("@@") and line[2:].strip().isupper():
            cur = line[2:].strip()
            out[cur] = []
        elif cur:
            out[cur].append(line.rstrip())
    return out


def _hostport(s):
    """'10.0.0.1:22' / '[::ffff:10.0.0.1]:22' / '*:80' / '0.0.0.0%eth0:68' -> (ip, port)"""
    s = s.strip()
    if ":" not in s:
        return s, ""
    host, port = s.rsplit(":", 1)
    host = host.strip("[]").split("%")[0]
    host = re.sub(r"^::ffff:", "", host)
    return host, port


def _proc(rest):
    m = re.search(r'users:\(\("([^"]+)"', rest or "")
    return m.group(1) if m else ""


def _loop(ip):
    return ip.startswith("127.") or ip in ("::1", "*", "0.0.0.0", "::", "")


def parse_addrs(lines):
    out = []
    for l in lines:
        m = re.search(r"^\d+:\s+(\S+)\s+inet\s+([\d.]+)/(\d+)", l)
        if m and not m.group(2).startswith("127."):
            out.append({"ip": m.group(2), "prefix": int(m.group(3)), "if": m.group(1)})
    return out


def parse_routes(lines):
    out = []
    for l in lines[:MAX_ROWS]:
        p = l.split()
        if not p:
            continue
        r = {"dest": "0.0.0.0/0" if p[0] == "default" else p[0], "gw": "", "if": "", "metric": ""}
        for k, key in (("via", "gw"), ("dev", "if"), ("metric", "metric"), ("src", "src")):
            if k in p and p.index(k) + 1 < len(p):
                r[key] = p[p.index(k) + 1]
        out.append(r)
    return out


def parse_listen(lines):
    out, seen = [], set()
    for l in lines:
        p = l.split()
        if len(p) < 5:
            continue
        proto = p[0]
        local = p[4] if p[1] in ("LISTEN", "UNCONN") else p[3]
        ip, port = _hostport(local)
        key = (proto, ip, port)
        if not port.isdigit() or key in seen:
            continue
        seen.add(key)
        out.append({"proto": proto, "address": ip or "*", "port": int(port), "process": _proc(l), "local_only": _loop(ip) and ip not in ("*", "0.0.0.0", "::", "")})
    out.sort(key=lambda x: (x["port"], x["proto"]))
    return out[:MAX_ROWS]


def parse_connections(lines, listening):
    """ss state established (ไม่มีคอลัมน์ State): Netid Recv-Q Send-Q Local Peer [Process]"""
    lports = {(x["proto"], x["port"]) for x in listening}
    agg = Counter()
    for l in lines:
        p = l.split()
        if len(p) < 5:
            continue
        proto = p[0]
        lip, lport = _hostport(p[3])
        rip, rport = _hostport(p[4])
        if _loop(rip) or not lport.isdigit() or not rport.isdigit():
            continue
        if (proto, int(lport)) in lports:
            agg[("in", rip, int(lport), _proc(l))] += 1
        else:
            agg[("out", rip, int(rport), _proc(l))] += 1
    return [{"dir": d, "peer": ip, "port": port, "proc": pr, "count": n}
            for (d, ip, port, pr), n in agg.most_common(MAX_ROWS)]


UFW_ACTION = re.compile(r"^(ALLOW|DENY|REJECT|LIMIT)( (IN|OUT|FWD))?$")


def parse_ufw(lines, conf_lines):
    """ufw status verbose -> {tool, status, defaults, rules, note}  อ่านไม่ได้ -> ใช้ค่าจากไฟล์ตั้งค่า"""
    text = "\n".join(lines)
    if "NOUFW" in text:
        return None
    fw = {"tool": "ufw", "status": "unknown", "defaults": {}, "rules": [], "note": ""}
    m = re.search(r"^Status:\s*(\w+)", text, re.M)
    if m:
        fw["status"] = m.group(1).lower()
        d = re.search(r"^Default:\s*(\w+) \(incoming\),\s*(\w+) \(outgoing\)(?:,\s*(\w+) \(routed\))?", text, re.M)
        if d:
            fw["defaults"] = {"in": d.group(1), "out": d.group(2), "routed": d.group(3) or ""}
        started = False
        for l in lines:
            if l.startswith("--"):
                started = True
                continue
            if not started or not l.strip():
                continue
            cols = [c.strip() for c in re.split(r"\s{2,}", l.strip()) if c.strip()]
            if len(cols) < 3:
                continue
            to, action, frm = cols[0], cols[1], cols[2]
            am = UFW_ACTION.match(action)
            if not am:
                continue
            fw["rules"].append({"to": to, "action": am.group(1), "dir": (am.group(3) or "IN").lower(), "from": frm,
                                "comment": cols[3].lstrip("# ") if len(cols) > 3 else ""})
        fw["rules"] = fw["rules"][:MAX_ROWS]
        return fw
    # อ่าน ufw status ไม่ได้ (ต้อง root) -> สถานะ / ค่าเริ่มต้นจากไฟล์ตั้งค่า (อ่านได้ทุก user)
    conf = dict(l.split("=", 1) for l in conf_lines if "=" in l)
    clean = {k: v.strip().strip('"') for k, v in conf.items()}
    if clean.get("ENABLED"):
        fw["status"] = "active" if clean["ENABLED"].lower() == "yes" else "inactive"
    pol = {"DROP": "deny", "REJECT": "reject", "ACCEPT": "allow"}
    fw["defaults"] = {k: pol.get(clean.get(v, "").upper(), clean.get(v, "").lower())
                      for k, v in (("in", "DEFAULT_INPUT_POLICY"), ("out", "DEFAULT_OUTPUT_POLICY"), ("routed", "DEFAULT_FORWARD_POLICY"))
                      if clean.get(v)}
    fw["note"] = ("อ่านรายการกฎไม่ได้ (ufw status ต้องใช้ root) · ให้ user ของ puller รัน 'sudo -n ufw status' ได้ "
                  "(sudoers: <user> ALL=(root) NOPASSWD: /usr/sbin/ufw status *)")
    return fw


def parse_firewalld(lines):
    if not lines:
        return None
    state = lines[0].strip().lower()
    if state not in ("running", "not running"):
        return None
    fw = {"tool": "firewalld", "status": "active" if state == "running" else "inactive", "defaults": {}, "rules": [], "note": ""}
    for l in lines[1:]:
        m = re.match(r"^\s+(services|ports|rich rules|sources|target):\s*(.*)$", l)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if key == "target":
            fw["defaults"] = {"in": {"default": "deny", "ACCEPT": "allow", "DROP": "deny", "REJECT": "reject"}.get(val, val.lower()), "out": "allow"}
        elif val:
            for item in val.split() if key != "rich rules" else [val]:
                fw["rules"].append({"to": item, "action": "ALLOW", "dir": "in", "from": "Anywhere", "comment": key})
    if len(lines) == 1 and fw["status"] == "active":
        fw["note"] = "อ่านรายการกฎไม่ได้ (firewall-cmd --list-all ต้องใช้สิทธิ์เพิ่ม)"
    return fw


def parse_iptables(lines):
    rules = [l for l in lines if l.startswith("-")]
    if not rules:
        return None
    fw = {"tool": "iptables", "status": "active", "defaults": {}, "rules": [], "note": ""}
    pol = {}
    for l in rules:
        m = re.match(r"^-P (INPUT|OUTPUT|FORWARD) (\w+)", l)
        if m:
            pol[m.group(1)] = m.group(2)
            continue
        m = re.match(r"^-A (INPUT|OUTPUT|FORWARD) (.*) -j (\w+)", l)
        if not m:
            continue
        body = m.group(2)
        port = re.search(r"--dports? (\S+)", body)
        src = re.search(r"-s (\S+)", body)
        dst = re.search(r"-d (\S+)", body)
        proto = re.search(r"-p (\w+)", body)
        fw["rules"].append({"to": (port.group(1) if port else "Anywhere") + (f"/{proto.group(1)}" if proto else ""),
                            "action": {"ACCEPT": "ALLOW", "DROP": "DENY", "REJECT": "REJECT"}.get(m.group(3), m.group(3)),
                            "dir": {"INPUT": "in", "OUTPUT": "out", "FORWARD": "fwd"}[m.group(1)],
                            "from": src.group(1) if src else (dst.group(1) if dst else "Anywhere"), "comment": ""})
    pd = {"ACCEPT": "allow", "DROP": "deny", "REJECT": "reject"}
    fw["defaults"] = {"in": pd.get(pol.get("INPUT", ""), ""), "out": pd.get(pol.get("OUTPUT", ""), ""), "routed": pd.get(pol.get("FORWARD", ""), "")}
    fw["rules"] = fw["rules"][:MAX_ROWS]
    return fw


AUTH_FAIL = re.compile(r"Failed (\S+) for (?:invalid user )?(\S*) from (\S+)")
AUTH_OK = re.compile(r"Accepted (\S+) for (\S+) from (\S+)")
AUTH_INVALID = re.compile(r"Invalid user (\S*) from (\S+)")
ISO_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:?\d{2})")


def parse_auth(lines, source):
    fails, by, ok, invalid = 0, Counter(), [], 0
    for l in lines:
        m = AUTH_FAIL.search(l)
        if m:
            fails += 1
            by[(m.group(3), m.group(2))] += 1
            continue
        m = AUTH_OK.search(l)
        if m:
            t = ISO_TS.match(l)
            ok.append({"time": t.group(1) if t else " ".join(l.split()[:3]), "user": m.group(2), "ip": m.group(3), "type": m.group(1)})
            continue
        if AUTH_INVALID.search(l):
            invalid += 1
    return {"source": source, "failed": fails, "invalid_users": invalid, "success": len(ok),
            "failed_by": [{"ip": ip, "user": u, "count": n} for (ip, u), n in by.most_common(20)],
            "recent_ok": ok[-20:][::-1]}


def parse(output):
    sec = _sections(output)
    listening = parse_listen(sec.get("LISTEN", []))
    fw = parse_ufw(sec.get("UFW", []), sec.get("UFWCONF", []))
    if fw is None or (fw["status"] == "unknown" and not fw["defaults"]):
        fw = parse_firewalld(sec.get("FWD", [])) or parse_iptables(sec.get("IPT", [])) or fw or {
            "tool": "", "status": "unknown", "defaults": {}, "rules": [],
            "note": "ไม่พบ ufw / firewalld และอ่าน iptables ไม่ได้ (ต้อง root: sudo -n iptables -S)"}
    if sec.get("AUTH"):
        logins = parse_auth(sec["AUTH"], "journalctl 24h")
    elif sec.get("AUTHFILE"):
        logins = parse_auth(sec["AUTHFILE"], "auth.log (5000 บรรทัดล่าสุด)")
    else:
        logins = {"source": "", "failed": None, "invalid_users": None, "success": None, "failed_by": [], "recent_ok": [],
                  "note": "อ่าน log ของ sshd ไม่ได้ (เพิ่ม user ของ puller เข้ากลุ่ม adm / systemd-journal)"}
    return {"addrs": parse_addrs(sec.get("ADDR", [])), "routes": parse_routes(sec.get("ROUTE", [])),
            "listening": listening, "connections": parse_connections(sec.get("CONN", []), listening),
            "firewall": fw, "logins": logins}


def collect(target, run):
    """run = ssh_client.run_ssh  ล้มเหลว -> {} (ส่วนอื่นของ snapshot ยังเก็บต่อ)"""
    try:
        return parse(run(target, COMMAND))
    except Exception as e:
        print(f"[NETSEC] {target.get('name')} {e}")
        return {}
