"""
host_details.py - รายละเอียดเครื่อง 1 เครื่อง สำหรับหน้า Machine details (แท็บ Performance / Hardware / System)
==========================================================================================================

แหล่งข้อมูล: Prometheus อย่างเดียว (PromQL)  ไม่มีค่าสมมติ
    Linux    node_exporter (Prometheus scrape :9100)                                                      label job/instance
    Ping     blackbox_exporter (icmp) job "ping"                                                          label cm_target="<เครื่อง>"

ค่าที่เครื่องไม่มี / exporter ไม่ส่ง -> None และมีรายการ "unsupported" บอกเหตุผล (หน้าเว็บแสดง Unsupported / Unavailable)

ฟังก์ชัน
    performance(web)   CPU / Memory / Disk (ต่อไดรฟ์) / Network (ต่อการ์ด) / TCP / Ping
    hardware(web)      CPU รุ่น / RAM / ดิสก์ / การ์ดเครือข่าย / GPU / อุณหภูมิ / พัดลม / SMART
    system(web)        ชื่อเครื่อง / OS / build / boot / uptime / timezone / เวลาเพี้ยน / IP / agent
"""

import json
import os
import re
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from clients import prom_value, prom_vector
from metrics_service import _finite, _labels, get_web_prometheus_config
from system_state import SYSTEM_DIR

_LINUX_FS = ('fstype!~"tmpfs|devtmpfs|overlay|squashfs|ramfs|nsfs|autofs|efivarfs|fuse.lxcfs",'
             'mountpoint!~"/(run|snap|var/lib/docker|var/lib/kubelet|var/lib/containerd)(/.*)?"')
_LINUX_DEV = 'device!~"(lo|veth.*|docker.*|br-.*|virbr.*|cni.*|flannel.*|cali.*)"'
_LINUX_DISK = 'device!~"(loop|ram|fd|sr|dm-).*"'

MB = 1048576


def _r(v, nd=2):
    return round(v, nd) if _finite(v) else None


def _run(queries):
    """{key: query} -> {key: ผลของ prom_value หรือ prom_vector} ยิงพร้อมกัน (key ขึ้นต้น '*' = vector)"""
    def one(item):
        key, q = item
        try:
            return key, (prom_vector(q) if key.startswith("*") else prom_value(q))
        except Exception as e:                      # Prometheus ล่ม/ช้า -> ค่านั้นเป็น None ไม่ล้มทั้งหน้า
            print(f"[HOST] query {key}: {e!r}")
            return key, ([] if key.startswith("*") else None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(one, queries.items()))


def _by(rows, label, scale=1.0, nd=2):
    out = {}
    for m, v in rows or []:
        k = m.get(label)
        if k is not None and _finite(v):
            out[k] = round(v * scale, nd)
    return out


def _context(web):
    config = get_web_prometheus_config(web)
    if not config:
        return None
    return _labels(config)


def _ping(web):
    """latency (ms) / packet loss (%) จาก blackbox_exporter (job ping)  ไม่มี host / ไม่ได้เปิด -> None"""
    sel = f'cm_target="{web}"'
    res = _run({
        # เฉลี่ยเฉพาะ ping ที่ตอบ (ping ที่หายมี rtt = 0)
        "rtt": (f'sum(sum_over_time(probe_icmp_duration_seconds{{{sel},phase="rtt"}}[5m])) / '
                f'sum(sum_over_time(probe_success{{{sel}}}[5m])) * 1000'),
        "loss": f"(1 - avg(avg_over_time(probe_success{{{sel}}}[5m]))) * 100",
        "up": f"max(probe_success{{{sel}}})",
        "samples": f"sum(count_over_time(probe_success{{{sel}}}[5m]))",
    })
    if res["samples"] is None:
        return {"available": False, "reason": "ไม่มีผล ping (เครื่องไม่มี host หรือ blackbox_exporter ไม่ได้เปิด)"}
    return {"available": True, "latency_ms": _r(res["rtt"]), "packet_loss": _r(res["loss"]),
            "reachable": res["up"] == 1, "samples_5m": int(res["samples"] or 0)}


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

def _core_key(core):
    try:
        return int(str(core).split(",")[-1])
    except ValueError:
        return 0


def _nics(r, label):
    rx, tx, bw = _by(r["*rx"], label), _by(r["*tx"], label), _by(r["*bw"], label, nd=0)
    err, disc, oq = _by(r["*err"], label, nd=3), _by(r["*disc"], label, nd=3), _by(r["*oq"], label, nd=0)
    st = {m.get(label): m.get("status") or m.get("operstate") for m, _ in r.get("*nicst") or []}
    out = []
    for n in sorted(set(rx) | set(tx)):
        speed = bw.get(n) or None
        use = ((rx.get(n) or 0) + (tx.get(n) or 0))
        out.append({
            "nic": n, "rx_mbps": _r((rx.get(n) or 0) * 8 / 1e6, 3), "tx_mbps": _r((tx.get(n) or 0) * 8 / 1e6, 3),
            "speed_mbps": _r(speed * 8 / 1e6, 0) if speed else None,
            # full duplex: ใช้ทิศที่มากกว่าเทียบความเร็วการ์ด
            "bandwidth_pct": _r(max(rx.get(n) or 0, tx.get(n) or 0) / speed * 100) if speed else None,
            "errors_per_sec": err.get(n), "discards_per_sec": disc.get(n), "output_queue": oq.get(n),
            "status": st.get(n), "bytes_per_sec": _r(use, 0),
        })
    return out


def _linux_performance(l):
    fs = f"{l},{_LINUX_FS}"
    dev = f"{l},{_LINUX_DEV}"
    dk = f"{l},{_LINUX_DISK}"
    q = {
        "cpu": f'100 - avg(rate(node_cpu_seconds_total{{{l},mode="idle"}}[5m])) * 100',
        "cpu_user": f'avg(rate(node_cpu_seconds_total{{{l},mode="user"}}[5m])) * 100',
        "cpu_privileged": f'avg(rate(node_cpu_seconds_total{{{l},mode="system"}}[5m])) * 100',
        "cpu_iowait": f'avg(rate(node_cpu_seconds_total{{{l},mode="iowait"}}[5m])) * 100',
        "cores": f'count(node_cpu_seconds_total{{{l},mode="idle"}})',
        "load1": f"max(node_load1{{{l}}})",
        "ctx": f"sum(rate(node_context_switches_total{{{l}}}[5m]))",
        "procs": f"max(node_procs_running{{{l}}})",
        "*core": f'100 - avg by(cpu) (rate(node_cpu_seconds_total{{{l},mode="idle"}}[5m])) * 100',
        "mem_total": f"max(node_memory_MemTotal_bytes{{{l}}})",
        "mem_avail": f"max(node_memory_MemAvailable_bytes{{{l}}})",
        "cache": f"max(node_memory_Cached_bytes{{{l}}})",
        "committed": f"max(node_memory_Committed_AS_bytes{{{l}}})",
        "commit_limit": f"max(node_memory_CommitLimit_bytes{{{l}}})",
        "swap_total": f"max(node_memory_SwapTotal_bytes{{{l}}})",
        "swap_free": f"max(node_memory_SwapFree_bytes{{{l}}})",
        "pages": f"sum(rate(node_vmstat_pswpin{{{l}}}[5m])) + sum(rate(node_vmstat_pswpout{{{l}}}[5m]))",
        "faults": f"sum(rate(node_vmstat_pgmajfault{{{l}}}[5m]))",
        "*size": f"node_filesystem_size_bytes{{{fs}}}",
        "*free": f"node_filesystem_avail_bytes{{{fs}}}",
        "*ro": f"node_filesystem_readonly{{{fs}}}",
        "*rd_b": f"rate(node_disk_read_bytes_total{{{dk}}}[5m])",
        "*wr_b": f"rate(node_disk_written_bytes_total{{{dk}}}[5m])",
        "*rd_ops": f"rate(node_disk_reads_completed_total{{{dk}}}[5m])",
        "*wr_ops": f"rate(node_disk_writes_completed_total{{{dk}}}[5m])",
        "*rd_lat": (f"rate(node_disk_read_time_seconds_total{{{dk}}}[5m]) / "
                    f"rate(node_disk_reads_completed_total{{{dk}}}[5m]) * 1000"),
        "*wr_lat": (f"rate(node_disk_write_time_seconds_total{{{dk}}}[5m]) / "
                    f"rate(node_disk_writes_completed_total{{{dk}}}[5m]) * 1000"),
        "*queue_d": f"rate(node_disk_io_time_weighted_seconds_total{{{dk}}}[5m])",
        "*busy": f"rate(node_disk_io_time_seconds_total{{{dk}}}[5m]) * 100",
        "*rx": f"rate(node_network_receive_bytes_total{{{dev}}}[5m])",
        "*tx": f"rate(node_network_transmit_bytes_total{{{dev}}}[5m])",
        "*bw": f"node_network_speed_bytes{{{dev}}} > 0",
        "*err": (f"rate(node_network_receive_errs_total{{{dev}}}[5m]) + "
                 f"rate(node_network_transmit_errs_total{{{dev}}}[5m])"),
        "*disc": (f"rate(node_network_receive_drop_total{{{dev}}}[5m]) + "
                  f"rate(node_network_transmit_drop_total{{{dev}}}[5m])"),
        "*oq": f"node_network_transmit_queue_length{{{dev}}}",
        "*nicst": f"node_network_info{{{dev}}}",
        "tcp_est": f"max(node_netstat_Tcp_CurrEstab{{{l}}})",
        "tcp_fail": f"sum(rate(node_netstat_Tcp_AttemptFails{{{l}}}[5m]))",
        "tcp_reset": f"sum(rate(node_netstat_Tcp_EstabResets{{{l}}}[5m]))",
        "tcp_retx": (f"sum(rate(node_netstat_Tcp_RetransSegs{{{l}}}[5m])) / "
                     f"sum(rate(node_netstat_Tcp_OutSegs{{{l}}}[5m])) * 100"),
    }
    r = _run(q)
    total, avail = r["mem_total"], r["mem_avail"]
    size, free = _by(r["*size"], "mountpoint", nd=0), _by(r["*free"], "mountpoint", nd=0)
    ro = _by(r["*ro"], "mountpoint")
    dev_of = {m.get("mountpoint"): (m.get("device") or "").split("/")[-1] for m, _ in r["*size"] or []}
    maps = {k: _by(r[k], "device") for k in ("*rd_b", "*wr_b", "*rd_ops", "*wr_ops", "*rd_lat", "*wr_lat",
                                              "*queue_d", "*busy")}
    disks = []
    for mnt in sorted(size):
        s, f = size.get(mnt), free.get(mnt)
        d = dev_of.get(mnt)
        if d not in maps["*rd_ops"]:
            # node_exporter ไม่เก็บสถิติของ partition (sda1, nvme0n1p1) -> ใช้ของดิสก์แม่ (sda, nvme0n1)
            parent = re.sub(r"p?\d+$", "", d or "") if re.match(r"^(nvme\d+n\d+p\d+|[a-z]+\d+)$", d or "") else None
            if parent in maps["*rd_ops"]:
                d = parent
        # mount ที่อยู่บน LVM/partition: ใช้ค่าของ device ตรงชื่อ (ถ้ามี) ไม่เดา
        disks.append({
            "volume": mnt, "device": d, "size_bytes": s, "free_bytes": f,
            "used_pct": _r((1 - f / s) * 100) if s and f is not None else None,
            "read_mbps": _r(maps["*rd_b"][d] / MB) if d in maps["*rd_b"] else None,
            "write_mbps": _r(maps["*wr_b"][d] / MB) if d in maps["*wr_b"] else None,
            "read_iops": maps["*rd_ops"].get(d), "write_iops": maps["*wr_ops"].get(d),
            "read_latency_ms": maps["*rd_lat"].get(d), "write_latency_ms": maps["*wr_lat"].get(d),
            "queue": maps["*queue_d"].get(d), "busy_pct": maps["*busy"].get(d),
            "status": None if mnt not in ro else ("Read-only" if ro[mnt] == 1 else "OK"),
        })
    # อุปกรณ์ดิสก์ (sda, nvme0n1) แยกต่างหาก: latency / busy ของดิสก์จริง
    devices = [{"device": d, "read_iops": maps["*rd_ops"].get(d), "write_iops": maps["*wr_ops"].get(d),
                "read_latency_ms": maps["*rd_lat"].get(d), "write_latency_ms": maps["*wr_lat"].get(d),
                "queue": maps["*queue_d"].get(d), "busy_pct": maps["*busy"].get(d)}
               for d in sorted(maps["*rd_ops"])]
    return {
        "os": "linux",
        "cpu": {"usage": _r(r["cpu"]), "user": _r(r["cpu_user"]), "privileged": _r(r["cpu_privileged"]),
                "iowait": _r(r["cpu_iowait"]), "cores": int(r["cores"]) if r["cores"] else None,
                "load1": _r(r["load1"]), "context_switches_per_sec": _r(r["ctx"], 0), "processes": _r(r["procs"], 0),
                "per_core": [{"core": k, "usage": v} for k, v in sorted(
                    _by(r["*core"], "cpu").items(), key=lambda x: _core_key(x[0]))]},
        "memory": {
            "total_bytes": total, "available_bytes": avail,
            "used_bytes": total - avail if total and avail is not None else None,
            "used_pct": _r((1 - avail / total) * 100) if total and avail is not None else None,
            "available_pct": _r(avail / total * 100) if total and avail is not None else None,
            "committed_bytes": r["committed"], "commit_limit_bytes": r["commit_limit"],
            "committed_pct": _r(r["committed"] / r["commit_limit"] * 100) if r["committed"] and r["commit_limit"] else None,
            "cache_bytes": r["cache"], "pages_per_sec": _r(r["pages"], 1), "page_faults_per_sec": _r(r["faults"], 1),
            "swap_total_bytes": r["swap_total"],
            "swap_used_pct": _r((1 - r["swap_free"] / r["swap_total"]) * 100) if r["swap_total"] and r["swap_free"] is not None else None,
        },
        "disks": disks,
        "disk_devices": devices,
        "network": _nics(r, "device"),
        "tcp": {"established": _r(r["tcp_est"], 0), "failures_per_sec": _r(r["tcp_fail"], 3),
                "resets_per_sec": _r(r["tcp_reset"], 3), "retransmit_pct": _r(r["tcp_retx"], 3)},
    }


def performance(web):
    labels = _context(web)
    if labels is None:
        return None
    data = _linux_performance(labels)
    data["web"] = web
    data["ping"] = _ping(web)
    data["generated_at"] = time.time()
    return data


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------

def _unsupported(item, reason):
    return {"item": item, "reason": reason}


def _first(rows):
    return (rows or [({}, 0)])[0][0]


def _val(rows):
    vals = [v for _, v in rows or [] if _finite(v)]
    return vals[0] if vals else None


def _clean(v):
    """ค่าจาก SMBIOS ที่เป็น placeholder -> None"""
    v = (v or "").strip()
    return None if not v or v.lower() in ("not specified", "default string", "to be filled by o.e.m.", "none",
                                          "system serial number", "0", "n/a") else v


def _linux_hardware(l):
    r = _run({
        "*cpu": f"node_cpu_info{{{l}}}",
        "cores": f'count(node_cpu_seconds_total{{{l},mode="idle"}})',
        "*freq": f"node_cpu_scaling_frequency_hertz{{{l}}}",
        "mem": f"max(node_memory_MemTotal_bytes{{{l}}})",
        "*disk": f"node_disk_info{{{l},{_LINUX_DISK}}}",
        "*fs": f"node_filesystem_size_bytes{{{l},{_LINUX_FS}}}",
        "*nic": f"node_network_info{{{l},{_LINUX_DEV}}}",
        "*speed": f"node_network_speed_bytes{{{l},{_LINUX_DEV}}} > 0",
        "*temp": f'node_hwmon_temp_celsius{{{l}}}',
        "*fan": f"node_hwmon_fan_rpm{{{l}}}",
        "*thermal": f"node_thermal_zone_temp{{{l}}}",
    })
    models = sorted({m.get("model_name") for m, _ in r["*cpu"] if m.get("model_name")})
    freq = [v for _, v in r["*freq"] if _finite(v)]
    speed = _by(r["*speed"], "device", nd=0)
    temps = [{"zone": f"{m.get('chip', '')}/{m.get('sensor', '')}", "temperature_c": _r(v, 1)} for m, v in r["*temp"]]
    temps += [{"zone": m.get("type") or m.get("zone"), "temperature_c": _r(v, 1)} for m, v in r["*thermal"]]
    fans = [{"fan": f"{m.get('chip', '')}/{m.get('sensor', '')}", "rpm": _r(v, 0)} for m, v in r["*fan"]]
    unsupported = []
    if not temps:
        unsupported.append(_unsupported("CPU temperature", "node_exporter ไม่พบ hwmon/thermal zone (VM มักไม่มี)"))
    if not fans:
        unsupported.append(_unsupported("Fan speed", "node_exporter ไม่พบ hwmon fan (VM / server ที่ต้องอ่านผ่าน IPMI)"))
    unsupported.append(_unsupported("SMART", "node_exporter ไม่อ่าน SMART (ต้องติดตั้ง smartctl_exporter เพิ่ม)"))
    return {
        "os": "linux",
        "cpu": {"models": [{"name": m} for m in models], "cores": int(r["cores"]) if r["cores"] else None,
                "logical_processors": int(r["cores"]) if r["cores"] else None,
                "frequency_mhz": _r(sum(freq) / len(freq) / 1e6, 0) if freq else None},
        "memory": {"total_bytes": r["mem"]},
        "disk_drives": [{"device": m.get("device"), "model": m.get("model") or None, "serial": m.get("serial") or None}
                        for m, _ in r["*disk"]],
        "volumes": [{"volume": m.get("mountpoint"), "device": m.get("device"), "filesystem": m.get("fstype"),
                     "size_bytes": _r(v, 0)} for m, v in r["*fs"]],
        "network_adapters": [{"nic": m.get("device"), "mac": m.get("address"), "status": m.get("operstate"),
                              "speed_mbps": _r(speed[m.get("device")] * 8 / 1e6, 0) if speed.get(m.get("device")) else None}
                             for m, _ in r["*nic"]],
        "gpus": [], "temperatures": temps, "fans": fans, "unsupported": unsupported,
    }


def hardware(web, target=None):
    labels = _context(web)
    if labels is None:
        return None
    data = _linux_hardware(labels)
    data["web"] = web
    if _is_winrm(target):
        _win_hardware(data, _win_inventory(web))
    return data


# ---------------------------------------------------------------------------
# Windows (WinRM): ข้อมูลเครื่องจาก inventory ใน snapshot ของ puller (data/state/system/<เครื่อง>.json อัปเดตทุก 1 ชม.)
# ---------------------------------------------------------------------------

def _is_winrm(target):
    """เครื่อง Windows (WinRM / cm-agent): ข้อมูลเครื่องอยู่ใน inventory ของ snapshot"""
    return bool(target) and str(target.get("type") or "ssh") in ("winrm", "agent")


def software(web):
    """แท็บ Software: โปรแกรมที่ติดตั้ง + พอร์ตที่เปิดรับ (cm-agent: cm-inventory.ps1)  ไม่มีข้อมูล -> ว่าง"""
    inv = _win_inventory(web)
    return {"web": web, "software": inv.get("software") or [], "ports": inv.get("ports") or [],
            "inventory_time": inv.get("inventory_ts")}


def _win_inventory(web):
    try:
        with open(os.path.join(SYSTEM_DIR, f"{web}.json"), encoding="utf-8") as f:
            inv = (json.load(f) or {}).get("inventory")
        return inv if isinstance(inv, dict) else {}
    except (OSError, ValueError):
        return {}


def _iso_unix(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp() if v else None
    except ValueError:
        return None


def _win_nics(inv):
    return [n for n in inv.get("nics") or [] if isinstance(n, dict)]


def _win_hardware(data, inv):
    sy, osi = inv.get("system") or {}, inv.get("os") or {}
    data["os"] = "windows"
    data["system"] = {"manufacturer": sy.get("manufacturer") or None, "model": sy.get("model") or None}
    cpu = data.setdefault("cpu", {})
    if not cpu.get("models") and inv.get("cpu"):
        cpu["models"] = [{"name": n} for n in inv["cpu"] if n]
    if sy.get("logical_cpu"):
        cpu["logical_processors"] = sy["logical_cpu"]
    # "cores" จาก Prometheus = จำนวน logical processor (นับ counter ต่อ CPU) ไม่ใช่ core จริง
    # core จริง / socket / clock มาจาก setup (admin อ่าน WMI แล้วเก็บ hardware.json) -> inventory.cpu_info
    ci = inv.get("cpu_info") if isinstance(inv.get("cpu_info"), dict) else {}
    cpu["cores"] = ci.get("cores") or None
    cpu["sockets"] = ci.get("sockets") or None
    if ci.get("logical"):
        cpu["logical_processors"] = ci["logical"]
    if ci.get("max_mhz"):
        cpu["frequency_mhz"] = ci["max_mhz"]
    if not (data.get("memory") or {}).get("total_bytes") and sy.get("ram_mb"):
        data["memory"] = {"total_bytes": sy["ram_mb"] * MB}
    nics = _win_nics(inv)
    if nics:
        data["network_adapters"] = [{"nic": n.get("name"), "status": str(n.get("status") or "").lower() or None,
                                     "speed_mbps": round(n["speed"] / 1e6) if n.get("speed") and n["speed"] > 0 else None}
                                    for n in nics]
    data["unsupported"] = []
    data["inventory_time"] = inv.get("ts")
    data["os_name"] = osi.get("caption")


def _win_system(data, inv):
    osi, sy = inv.get("os") or {}, inv.get("system") or {}
    data["os"] = "windows"
    if osi.get("caption"):
        data["os_name"] = osi["caption"] + (f" ({osi['edition']})" if osi.get("edition") and osi["edition"] not in osi["caption"] else "")
    data["os_version"] = osi.get("release") or osi.get("version") or data.get("os_version")
    data["build"] = osi.get("build") or data.get("build")
    data["install_time"] = _iso_unix(osi.get("install"))
    if sy.get("domain"):
        data["domain"] = sy["domain"]
    data["timezone"] = inv.get("tz") or data.get("timezone")
    ips = [{"nic": n.get("name"), "address": ip, "family": "IPv6" if ":" in ip else "IPv4"}
           for n in _win_nics(inv) for ip in n.get("ips") or [] if ip and not ip.startswith(("127.", "::1", "fe80:"))]
    if ips:
        data["ip_addresses"] = ips
    hf = [h for h in inv.get("hotfix") or [] if isinstance(h, dict) and h.get("id")]
    data["hotfixes"] = hf
    if hf:
        data["updates_last_install"] = _iso_unix(hf[0].get("date"))
    if inv.get("ps"):
        data["agent"]["powershell"] = inv["ps"]


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

def _linux_system(l):
    r = _run({
        "*uname": f"node_uname_info{{{l}}}",
        "*os": f"node_os_info{{{l}}}",
        "boot": f"max(node_boot_time_seconds{{{l}}})",
        "offset": f"max(node_timex_offset_seconds{{{l}}})",
        "sync": f"max(node_timex_sync_status{{{l}}})",
        "*exp": f"node_exporter_build_info{{{l}}}",
        "*tz": f"node_time_zone_offset_seconds{{{l}}}",
    })
    un = (r["*uname"] or [({}, 0)])[0][0]
    osi = (r["*os"] or [({}, 0)])[0][0]
    exp = (r["*exp"] or [({}, 0)])[0][0]
    boot = r["boot"]
    tz = next((m.get("time_zone") for m, _ in r["*tz"]), None)
    return {
        "os": "linux",
        "hostname": un.get("nodename"), "domain": un.get("domainname") if un.get("domainname") not in ("(none)", "") else None,
        "fqdn": None, "os_name": osi.get("pretty_name") or un.get("sysname"),
        "os_version": osi.get("version") or None, "build": un.get("release"), "architecture": un.get("machine"),
        "boot_time": boot, "uptime_seconds": time.time() - boot if boot else None,
        "timezone": tz, "clock_offset_seconds": _r(r["offset"], 3),
        "clock_source": None if r["sync"] is None else ("synchronised" if r["sync"] == 1 else "not synchronised"),
        "ip_addresses": [],
        "agent": {"collector": "node_exporter " + exp.get("version") if exp.get("version") else "node_exporter"},
    }


def system(web, target=None):
    labels = _context(web)
    if labels is None:
        return None
    data = _linux_system(labels)
    data["web"] = web
    if target:
        data.update({"host": target.get("host"), "group": target.get("group") or "",
                     "type": target.get("type") or "ssh"})
        if _is_winrm(target):                                   # Windows ไม่ลง agent (WinRM -> JEA)
            col = data["agent"]["collector"]
            if str(target.get("type")) == "agent":
                data["agent"] = {"collector": "cm-agent (Fluent Bit + windows_exporter) " + col.replace("node_exporter agent", "").strip()}
            else:
                data["agent"] = {"collector": "WinRM (JEA read-only)" + (" · module " + col.split("winrm-", 1)[1] if "winrm-" in col else "")}
            data["clock_source"] = None
            _win_system(data, _win_inventory(web))
        if not data["ip_addresses"] and target.get("host"):
            data["ip_addresses"] = [{"nic": None, "address": target.get("host"), "family": "configured"}]
    return data
