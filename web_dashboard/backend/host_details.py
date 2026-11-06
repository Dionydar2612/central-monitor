"""
host_details.py - รายละเอียดเครื่อง 1 เครื่อง สำหรับหน้า Machine details (แท็บ Performance / Hardware / System)
==========================================================================================================

แหล่งข้อมูล: Prometheus อย่างเดียว (PromQL)  ไม่มีค่าสมมติ
    Windows  windows_exporter (127.0.0.1:9182 บนเครื่อง -> Fluent Bit -> central-agent-gw -> remote write)  label cm_web="<เครื่อง>"
    Linux    node_exporter (Prometheus scrape :9100)                                                      label job/instance
    Ping     blackbox_exporter (icmp) job "ping"                                                          label cm_target="<เครื่อง>"

ค่าที่เครื่องไม่มี / exporter ไม่ส่ง -> None และมีรายการ "unsupported" บอกเหตุผล (หน้าเว็บแสดง Unsupported / Unavailable)

ฟังก์ชัน
    performance(web)   CPU / Memory / Disk (ต่อไดรฟ์) / Network (ต่อการ์ด) / TCP / Ping
    hardware(web)      CPU รุ่น / RAM / ดิสก์ / การ์ดเครือข่าย / GPU / อุณหภูมิ / พัดลม / SMART
    system(web)        ชื่อเครื่อง / OS / build / boot / uptime / timezone / เวลาเพี้ยน / IP / agent
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor

from clients import prom_value, prom_vector
from metrics_service import (
    _WIN_NIC,
    _WIN_VOL,
    _finite,
    _is_windows,
    _labels,
    get_web_prometheus_config,
    win_cpu,
    win_mem_available,
    win_mem_total,
)

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
        return None, None
    labels = _labels(config)
    return labels, _is_windows(labels)


def _ping(web):
    """latency (ms) / packet loss (%) จาก blackbox_exporter (job ping)  ไม่มี host / ไม่ได้เปิด -> None"""
    sel = f'cm_target="{web}"'
    res = _run({
        "rtt": f'avg(avg_over_time(probe_icmp_duration_seconds{{{sel},phase="rtt"}}[5m])) * 1000',
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

def _win_performance(l):
    vol = f"{l},{_WIN_VOL}"
    nic = f"{l},{_WIN_NIC}"
    q = {
        "cpu": f'100 - avg(rate({win_cpu(l, "idle")}[5m])) * 100',
        "cpu_user": f'avg(rate({win_cpu(l, "user")}[5m])) * 100',
        "cpu_privileged": f'avg(rate({win_cpu(l, "privileged")}[5m])) * 100',
        "cpu_interrupt": f'sum(avg by(mode) (rate({win_cpu(l, "interrupt|dpc")}[5m]))) * 100',
        "cores": f'count(count by(core) ({win_cpu(l, "idle")}))',
        "queue": f'max({{__name__=~"windows_system_processor_queue(_length)?",{l}}})',
        "ctx": f"sum(rate(windows_system_context_switches_total{{{l}}}[5m]))",
        "procs": f"max(windows_system_processes{{{l}}})",
        "threads": f"max(windows_system_threads{{{l}}})",
        "*core": f'100 - avg by(core) (rate({win_cpu(l, "idle")}[5m])) * 100',
        "mem_total": win_mem_total(l),
        "mem_avail": win_mem_available(l),
        "committed": f"max(windows_memory_committed_bytes{{{l}}})",
        "commit_limit": f"max(windows_memory_commit_limit_bytes{{{l}}})",
        "cache": f"max(windows_memory_cache_bytes{{{l}}})",
        "pages": f"sum(rate(windows_memory_swap_page_operations_total{{{l}}}[5m]))",
        "pages_in": f"sum(rate(windows_memory_swap_pages_read_total{{{l}}}[5m]))",
        "faults": f"sum(rate(windows_memory_page_faults_total{{{l}}}[5m]))",
        "pool_paged": f"max(windows_memory_pool_paged_bytes{{{l}}})",
        "pool_nonpaged": f"max(windows_memory_pool_nonpaged_bytes{{{l}}})",
        "*size": f"windows_logical_disk_size_bytes{{{vol}}}",
        "*free": f"windows_logical_disk_free_bytes{{{vol}}}",
        "*rd_b": f"rate(windows_logical_disk_read_bytes_total{{{vol}}}[5m])",
        "*wr_b": f"rate(windows_logical_disk_write_bytes_total{{{vol}}}[5m])",
        "*rd_ops": f"rate(windows_logical_disk_reads_total{{{vol}}}[5m])",
        "*wr_ops": f"rate(windows_logical_disk_writes_total{{{vol}}}[5m])",
        "*rd_lat": (f"rate(windows_logical_disk_read_seconds_total{{{vol}}}[5m]) / "
                    f"rate(windows_logical_disk_reads_total{{{vol}}}[5m]) * 1000"),
        "*wr_lat": (f"rate(windows_logical_disk_write_seconds_total{{{vol}}}[5m]) / "
                    f"rate(windows_logical_disk_writes_total{{{vol}}}[5m]) * 1000"),
        "*queue_d": f"windows_logical_disk_requests_queued{{{vol}}}",
        "*busy": f"100 - clamp_max(rate(windows_logical_disk_idle_seconds_total{{{vol}}}[5m]), 1) * 100",
        "*ro": f"windows_logical_disk_readonly{{{vol}}}",
        "*fs": f"windows_logical_disk_info{{{vol}}}",
        "*rx": f"rate(windows_net_bytes_received_total{{{nic}}}[5m])",
        "*tx": f"rate(windows_net_bytes_sent_total{{{nic}}}[5m])",
        "*bw": f"windows_net_current_bandwidth_bytes{{{nic}}}",
        "*err": (f"rate(windows_net_packets_received_errors_total{{{nic}}}[5m]) + "
                 f"rate(windows_net_packets_outbound_errors_total{{{nic}}}[5m])"),
        "*disc": (f"rate(windows_net_packets_received_discarded_total{{{nic}}}[5m]) + "
                  f"rate(windows_net_packets_outbound_discarded_total{{{nic}}}[5m])"),
        "*oq": f"windows_net_output_queue_length_packets{{{nic}}}",
        "*nicst": f"windows_net_nic_operation_status{{{nic}}} == 1",
        "tcp_est": f"sum(windows_tcp_connections_established{{{l}}})",
        "tcp_fail": f"sum(rate(windows_tcp_connection_failures_total{{{l}}}[5m]))",
        "tcp_reset": f"sum(rate(windows_tcp_connections_reset_total{{{l}}}[5m]))",
        "tcp_retx": (f"sum(rate(windows_tcp_segments_retransmitted_total{{{l}}}[5m])) / "
                     f"sum(rate(windows_tcp_segments_sent_total{{{l}}}[5m])) * 100"),
    }
    r = _run(q)
    total, avail = r["mem_total"], r["mem_avail"]
    memory = {
        "total_bytes": total, "available_bytes": avail,
        "used_bytes": total - avail if total and avail is not None else None,
        "used_pct": _r((1 - avail / total) * 100) if total and avail is not None else None,
        "available_pct": _r(avail / total * 100) if total and avail is not None else None,
        "committed_bytes": r["committed"], "commit_limit_bytes": r["commit_limit"],
        "committed_pct": _r(r["committed"] / r["commit_limit"] * 100) if r["committed"] and r["commit_limit"] else None,
        "cache_bytes": r["cache"], "pages_per_sec": _r(r["pages"], 1), "pages_input_per_sec": _r(r["pages_in"], 1),
        "page_faults_per_sec": _r(r["faults"], 1),
        "pool_paged_bytes": r["pool_paged"], "pool_nonpaged_bytes": r["pool_nonpaged"],
    }
    size, free = _by(r["*size"], "volume", nd=0), _by(r["*free"], "volume", nd=0)
    maps = {k: _by(r[k], "volume") for k in ("*rd_b", "*wr_b", "*rd_ops", "*wr_ops", "*rd_lat", "*wr_lat",
                                              "*queue_d", "*busy", "*ro")}
    fs = {m.get("volume"): m for m, _ in r["*fs"] or []}
    disks = []
    for v in sorted(size):
        s, f = size.get(v), free.get(v)
        disks.append({
            "volume": v, "label": (fs.get(v) or {}).get("volume_name") or None,
            "filesystem": (fs.get(v) or {}).get("filesystem") or None,
            "size_bytes": s, "free_bytes": f,
            "used_pct": _r((1 - f / s) * 100) if s and f is not None else None,
            "read_mbps": _r((maps["*rd_b"].get(v) or 0) / MB) if v in maps["*rd_b"] else None,
            "write_mbps": _r((maps["*wr_b"].get(v) or 0) / MB) if v in maps["*wr_b"] else None,
            "read_iops": maps["*rd_ops"].get(v), "write_iops": maps["*wr_ops"].get(v),
            "read_latency_ms": maps["*rd_lat"].get(v), "write_latency_ms": maps["*wr_lat"].get(v),
            "queue": maps["*queue_d"].get(v), "busy_pct": maps["*busy"].get(v),
            "status": None if v not in maps["*ro"] else ("Read-only" if maps["*ro"][v] == 1 else "OK"),
        })
    nics = _nics(r, "nic")
    return {
        "os": "windows",
        "cpu": {"usage": _r(r["cpu"]), "user": _r(r["cpu_user"]), "privileged": _r(r["cpu_privileged"]),
                "interrupt_dpc": _r(r["cpu_interrupt"]), "cores": int(r["cores"]) if r["cores"] else None,
                "queue_length": _r(r["queue"], 0), "context_switches_per_sec": _r(r["ctx"], 0),
                "processes": _r(r["procs"], 0), "threads": _r(r["threads"], 0),
                "per_core": [{"core": k, "usage": v} for k, v in sorted(
                    _by(r["*core"], "core").items(), key=lambda x: _core_key(x[0]))]},
        "memory": memory,
        "disks": disks,
        "network": nics,
        "tcp": {"established": _r(r["tcp_est"], 0), "failures_per_sec": _r(r["tcp_fail"], 3),
                "resets_per_sec": _r(r["tcp_reset"], 3), "retransmit_pct": _r(r["tcp_retx"], 3)},
    }


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
    labels, win = _context(web)
    if labels is None:
        return None
    data = _win_performance(labels) if win else _linux_performance(labels)
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


def _win_hardware(l):
    # ชื่อ metric: windows_exporter (ใหม่) และตัวเก็บในตัว Fluent Bit (เก่า: windows_cs_*, windows_paging_file_*)
    r = _run({
        "*dmi": f"windows_dmi_info{{{l}}}",
        "*cpu": f"windows_cpu_info{{{l}}}",
        "*cores": f"windows_cpu_info_core{{{l}}}",
        "*logical": f"windows_cpu_info_logical_processor{{{l}}}",
        "logical_old": f"max(windows_cs_logical_processors{{{l}}})",
        "*l2": f"windows_cpu_info_l2_cache_size_bytes{{{l}}}",
        "*l3": f"windows_cpu_info_l3_cache_size_bytes{{{l}}}",
        "freq": f"avg(windows_cpu_core_frequency_hertz{{{l}}})",
        "cores_rate": f'count(count by(core) ({win_cpu(l, "idle")}))',
        "mem": win_mem_total(l),
        "page_limit": f"sum(windows_pagefile_limit_bytes{{{l}}}) or sum(windows_paging_file_limit_megabytes{{{l}}}) * 1048576",
        "page_free": f"sum(windows_pagefile_free_bytes{{{l}}}) or sum(windows_paging_file_free_megabytes{{{l}}}) * 1048576",
        "*drive": f"windows_diskdrive_info{{{l}}}",
        "*drive_size": f"windows_diskdrive_size_bytes{{{l}}}",
        "*drive_status": f"windows_diskdrive_status{{{l}}} == 1",
        "*drive_parts": f"windows_diskdrive_partitions{{{l}}}",
        "*vol_info": f"windows_logical_disk_info{{{l},{_WIN_VOL}}}",
        "*vol_size": f"windows_logical_disk_size_bytes{{{l},{_WIN_VOL}}}",
        "*vol_free": f"windows_logical_disk_free_bytes{{{l},{_WIN_VOL}}}",
        "*nic": f"windows_net_nic_info{{{l}}}",
        "*nic_bw": f"windows_net_current_bandwidth_bytes{{{l},{_WIN_NIC}}}",
        "*nic_st": f"windows_net_nic_operation_status{{{l}}} == 1",
        "*gpu_temp": f"windows_gpu_temperature_celsius{{{l}}}",
        "*gpu_fan": f"windows_gpu_fan_speed_rpm{{{l}}}",
        "*thermal": f"windows_thermalzone_temperature_celsius{{{l}}}",
        "exporter": f"count(windows_exporter_build_info{{{l}}})",
    })
    dmi = _first(r["*dmi"])
    system = {
        "manufacturer": _clean(dmi.get("system_vendor")),
        "model": _clean(dmi.get("product_name")),
        "family": _clean(dmi.get("product_family")),
        "serial": _clean(dmi.get("product_serial")),
        "uuid": _clean(dmi.get("product_uuid")),
        "board": " ".join(x for x in (_clean(dmi.get("board_vendor")), _clean(dmi.get("board_name"))) if x) or None,
        "bios": " ".join(x for x in (_clean(dmi.get("bios_vendor")), _clean(dmi.get("bios_version"))) if x) or None,
        "bios_date": _clean(dmi.get("bios_date")),
    }
    l2, l3 = _by(r["*l2"], "device_id", nd=0), _by(r["*l3"], "device_id", nd=0)
    cpus = []
    for m, _ in r["*cpu"]:
        dev = m.get("device_id") or m.get("deviceid")
        # Fluent Bit ในตัว: l2_cache_size / l3_cache_size เป็น KB ใน label
        l2b = l2.get(dev) or (float(m["l2_cache_size"]) * 1024 if str(m.get("l2_cache_size") or "").isdigit() else None)
        l3b = l3.get(dev) or (float(m["l3_cache_size"]) * 1024 if str(m.get("l3_cache_size") or "").isdigit() else None)
        cpus.append({"socket": dev, "name": (m.get("name") or "").strip() or None,
                     "description": m.get("description"), "l2_bytes": l2b or None, "l3_bytes": l3b or None})
    cores = sum(v for _, v in r["*cores"]) or None
    logical = sum(v for _, v in r["*logical"]) or r["logical_old"] or r["cores_rate"]
    dsize = _by(r["*drive_size"], "device_id", nd=0)
    dstat = {m.get("device_id"): m.get("status") for m, _ in r["*drive_status"]}
    dparts = _by(r["*drive_parts"], "device_id", nd=0)
    drives = [{"device": str(m.get("device_id") or "").replace("\\\\.\\", ""), "model": m.get("model") or m.get("caption"),
               "size_bytes": dsize.get(m.get("device_id")), "partitions": dparts.get(m.get("device_id")),
               "status": dstat.get(m.get("device_id"))} for m, _ in r["*drive"]]
    vinfo = {m.get("volume"): m for m, _ in r["*vol_info"]}
    vsize, vfree = _by(r["*vol_size"], "volume", nd=0), _by(r["*vol_free"], "volume", nd=0)
    vols = [{"volume": v, "label": (vinfo.get(v) or {}).get("volume_name") or None,
             "filesystem": (vinfo.get(v) or {}).get("filesystem") or None, "type": (vinfo.get(v) or {}).get("type") or None,
             "size_bytes": vsize.get(v), "free_bytes": vfree.get(v)} for v in sorted(vsize)]
    bw = _by(r["*nic_bw"], "nic", nd=0)
    nst = {m.get("nic"): m.get("status") for m, _ in r["*nic_st"]}
    info = {m.get("nic"): m for m, _ in r["*nic"]}
    names = sorted(set(info) | set(bw))
    nics = [{"nic": n, "name": (info.get(n) or {}).get("friendly_name"), "mac": (info.get(n) or {}).get("mac"),
             "speed_mbps": _r(bw[n] * 8 / 1e6, 0) if bw.get(n) else None, "status": nst.get(n)} for n in names]
    sensors = [{"sensor": f"GPU {m.get('phys') or m.get('luid')}", "type": "Temperature", "value": _r(v, 1), "unit": "°C"}
               for m, v in r["*gpu_temp"]]
    sensors += [{"sensor": f"GPU {m.get('phys') or m.get('luid')}", "type": "Fan", "value": _r(v, 0), "unit": "rpm"}
                for m, v in r["*gpu_fan"]]
    sensors += [{"sensor": m.get("name") or m.get("zone") or "Thermal zone", "type": "Temperature", "value": _r(v, 1),
                 "unit": "°C"} for m, v in r["*thermal"] if _finite(v) and v > 0]
    return {
        "os": "windows",
        "exporter": bool(r["exporter"]),
        "system": system,
        "cpu": {"models": cpus, "sockets": len(cpus) or None, "cores": int(cores) if cores else None,
                "logical_processors": int(logical) if logical else None,
                "frequency_mhz": _r(r["freq"] / 1e6, 0) if r["freq"] else None},
        "memory": {"total_bytes": r["mem"], "pagefile_bytes": r["page_limit"],
                   "pagefile_used_bytes": r["page_limit"] - r["page_free"] if r["page_limit"] and r["page_free"] is not None else None},
        "disk_drives": drives, "volumes": vols, "network_adapters": nics, "sensors": sensors,
    }


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


def hardware(web):
    labels, win = _context(web)
    if labels is None:
        return None
    data = _win_hardware(labels) if win else _linux_hardware(labels)
    data["web"] = web
    return data


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

def _win_system(l):
    # ชื่อ metric: windows_exporter (ใหม่) และตัวเก็บในตัว Fluent Bit (เก่า: windows_cs_hostname, windows_os_timezone,
    # windows_system_system_up_time)
    r = _run({
        "*host": f"windows_os_hostname{{{l}}} or windows_cs_hostname{{{l}}}",
        "*os": f"windows_os_info{{{l}}}",
        "install": f"max(windows_os_install_time_timestamp_seconds{{{l}}})",
        "boot": (f"max(windows_system_boot_time_timestamp{{{l}}}) or max(windows_system_boot_time_timestamp_seconds{{{l}}})"
                 f" or (time() - max(windows_system_system_up_time{{{l}}}))"),
        "offset": f"max(windows_time_computed_time_offset_seconds{{{l}}})",
        "*tz": f"windows_time_timezone{{{l}}} or windows_os_timezone{{{l}}}",
        "*sync": f"windows_time_clock_sync_source{{{l}}} == 1",
        "*ntp": f"windows_time_sync_source_info{{{l}}}",
        "last_sync": f"max(windows_time_sync_last_success_timestamp_seconds{{{l}}})",
        "*lic": f"windows_license_status{{{l}}} == 1",
        "upd_pending": f"count(windows_update_pending_info{{{l}}})",
        "upd_scraped": f"max(windows_update_scrape_timestamp_seconds{{{l}}})",
        "upd_install": f"max(windows_update_last_installation_success_timestamp_seconds{{{l}}})",
        "users": f"max(windows_os_users{{{l}}})",
        "procs": f"max(windows_system_processes{{{l}}}) or max(windows_os_processes{{{l}}})",
        "threads": f"max(windows_system_threads{{{l}}})",
        "*addr": f"windows_net_nic_address_info{{{l},{_WIN_NIC}}}",
        "*mac": f"windows_net_nic_info{{{l}}}",
        "*exp": f"windows_exporter_build_info{{{l}}}",
        "*coll_fail": f"windows_exporter_collector_success{{{l}}} == 0",
        "*fb": f'{{__name__=~"fluentbit_build_info|fluentbit_uptime.*",{l}}}',
    })
    host, osi, exp = _first(r["*host"]), _first(r["*os"]), _first(r["*exp"])
    fb_version = next((m.get("version") for m, _ in r["*fb"] if m.get("version")), None)
    macs = {m.get("nic"): (m.get("friendly_name"), m.get("mac")) for m, _ in r["*mac"]}
    addresses = sorted({(m.get("nic"), m.get("address"), m.get("family")) for m, _ in r["*addr"]
                        if m.get("address") and not str(m.get("address")).startswith(("fe80", "169.254"))},
                       key=lambda x: (x[0] or "", x[2] != "ipv4", x[1]))
    build = osi.get("build_number")
    boot = r["boot"]
    upd_known = r["upd_scraped"] is not None
    return {
        "os": "windows",
        "hostname": host.get("hostname"), "domain": host.get("domain") or None, "fqdn": host.get("fqdn") or None,
        "os_name": osi.get("product"), "os_version": osi.get("version"),
        "build": f"{build}.{osi['revision']}" if build and osi.get("revision") else build,
        "installation_type": osi.get("installation_type"),
        "install_time": r["install"], "boot_time": boot, "uptime_seconds": time.time() - boot if boot else None,
        "timezone": next((m.get("timezone") for m, _ in r["*tz"]), None),
        "clock_offset_seconds": _r(r["offset"], 3),
        "clock_source": next((m.get("source") for m, _ in r["*ntp"]), None) or next((m.get("type") for m, _ in r["*sync"]), None),
        "last_time_sync": r["last_sync"],
        "license": next((m.get("state") for m, _ in r["*lic"]), None),
        "updates_pending": int(r["upd_pending"] or 0) if upd_known else None,
        "updates_last_install": r["upd_install"],
        "users": _r(r["users"], 0), "processes": _r(r["procs"], 0), "threads": _r(r["threads"], 0),
        "ip_addresses": [{"nic": (macs.get(n) or (None,))[0] or n, "address": a, "family": f,
                          "mac": (macs.get(n) or (None, None))[1]} for n, a, f in addresses],
        "agent": {"collector": "windows_exporter " + exp.get("version") if exp.get("version") else "Fluent Bit (built-in)",
                  "fluent_bit": fb_version,
                  "failed_collectors": sorted({m.get("collector") for m, _ in r["*coll_fail"] if m.get("collector")})},
    }


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
    labels, win = _context(web)
    if labels is None:
        return None
    data = _win_system(labels) if win else _linux_system(labels)
    data["web"] = web
    if target:
        data.update({"host": target.get("host"), "group": target.get("group") or "",
                     "type": target.get("type") or "ssh"})
        if not data["ip_addresses"] and target.get("host"):
            data["ip_addresses"] = [{"nic": None, "address": target.get("host"), "family": "configured"}]
    return data
