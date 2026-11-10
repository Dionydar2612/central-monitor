"""
software_watch.py - ประวัติติดตั้ง / ถอน / อัปเดตโปรแกรมบนเครื่อง Windows
=========================================================================

ข้อมูล: cm_software_installed{cm_web, name, version, publisher, arch}  (cm-inventory.ps1 บนเครื่อง ทุก 15 นาที
        -> windows_exporter textfile -> Fluent Bit -> Prometheus)
ทุก 5 นาที (เรียกจาก sensor_monitor) เมื่อเครื่องส่งรายการรอบใหม่ (cm_inventory_timestamp_seconds เปลี่ยน):
    เทียบกับรายการที่จำไว้ -> ติดตั้งใหม่ / ถอน / อัปเดต (ชื่อ + arch เดิม เวอร์ชันเปลี่ยน)
    -> System log (source "software") + การ์ด Teams (ระดับ "software" ในการ์ด Notifications) + ประวัติในแท็บ Software
ครั้งแรกที่เห็นเครื่อง = จำรายการไว้เฉยๆ ไม่แจ้ง
รายการว่าง / ลดลงเกินครึ่งในรอบเดียว = น่าจะอ่านไม่ครบ -> ข้าม (ไม่แจ้งถอนทั้งเครื่อง)
"""

import json
import os
import time

INTERVAL = 300
FRESH = 60              # series ที่ไม่มีค่าใหม่เกินนี้ (เทียบกับตัวล่าสุดของเครื่อง) = โปรแกรมถูกถอนแล้ว
HISTORY = 200


def fresh_rows(rows):
    """[(labels, timestamp ของค่าล่าสุด)] -> labels ที่ยังส่งมาอยู่ (Prometheus ยังเห็น series ที่หายไปอีก ~5 นาที)"""
    rows = [(lb, ts) for lb, ts in rows or []]
    if not rows:
        return []
    newest = max(ts for _, ts in rows)
    return [lb for lb, ts in rows if ts >= newest - FRESH]


def diff(old, new):
    """old / new = {"name|arch": version} -> [(action, name, arch, old_version, new_version)]"""
    out = []
    for k in sorted(set(old) | set(new)):
        name, _, arch = k.rpartition("|")
        if k not in old:
            out.append(("installed", name, arch, "", new[k]))
        elif k not in new:
            out.append(("removed", name, arch, old[k], ""))
        elif old[k] != new[k]:
            out.append(("updated", name, arch, old[k], new[k]))
    return out


TEXT = {"installed": "ติดตั้ง", "removed": "ถอน", "updated": "อัปเดต"}


def describe(c):
    action, name, arch, ov, nv = c
    if action == "updated":
        return f"อัปเดต {name} {ov} → {nv}"
    return f"{TEXT[action]} {name} {nv or ov}".strip()


def run(targets, query, state_file, now=None, on_changes=None):
    """targets = [(ชื่อเครื่อง, labels PromQL)]  query(q) -> [(labels, value)]
    on_changes(web, changes) เรียกเมื่อมีการเปลี่ยน  -> {web: changes}"""
    now = now or time.time()
    try:
        with open(state_file, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    if now - state.get("_checked", 0) < INTERVAL:
        return {}
    state["_checked"] = now
    result = {}
    for web, labels in targets:
        inv = query(f"max(cm_inventory_timestamp_seconds{{{labels}}})")
        if not inv:
            continue
        inv_ts = inv[0][1]
        st = state.setdefault(web, {})
        if st.get("inventory") == inv_ts:
            continue
        rows = query(f"timestamp(cm_software_installed{{{labels}}})")
        if rows is None:
            continue
        cur = {}
        for lb in fresh_rows(rows):
            if lb.get("name"):
                cur[f"{lb['name']}|{lb.get('arch') or ''}"] = lb.get("version") or ""
        old = st.get("snapshot")
        if not cur or (old and len(cur) < len(old) / 2):
            continue                                     # อ่านไม่ครบ -> รอรอบหน้า
        st["inventory"] = inv_ts
        st["snapshot"] = cur
        if old is None:
            continue                                     # เห็นครั้งแรก: จำไว้เฉยๆ
        changes = diff(old, cur)
        if not changes:
            continue
        hist = st.setdefault("history", [])
        for a, name, arch, ov, nv in changes:
            hist.append({"time": now, "action": a, "name": name, "arch": arch, "old": ov, "new": nv})
        st["history"] = hist[-HISTORY:]
        result[web] = changes
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    tmp = state_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, state_file)
    if on_changes:
        for web, changes in result.items():
            try:
                on_changes(web, changes)
            except Exception as e:
                print(f"[SOFTWARE ERROR] {web}: {e!r}")
    return result
