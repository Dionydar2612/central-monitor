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
        "queue": f"max(windows_system_processor_queue_length{{{l}}})",
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


def _win_hardware(l):
    r = _run({
        "*cpu": f"windows_cpu_info{{{l}}}",
        "*cores": f"windows_cpu_info_core{{{l}}}",
        "*logical": f"windows_cpu_info_logical_processor{{{l}}}",
        "*freq": f"avg by(core) (windows_cpu_core_frequency_hertz{{{l}}})",
        "mem": win_mem_total(l),
        "*drive": f"windows_diskdrive_info{{{l}}}",
        "*drive_size": f"windows_diskdrive_size_bytes{{{l}}}",
        "*drive_status": f"windows_diskdrive_status{{{l}}} == 1",
        "*vol": f"windows_logical_disk_info{{{l},{_WIN_VOL}}}",
        "*vol_size": f"windows_logical_disk_size_bytes{{{l},{_WIN_VOL}}}",
        "*nic": f"windows_net_nic_info{{{l}}}",
        "*nic_bw": f"windows_net_current_bandwidth_bytes{{{l},{_WIN_NIC}}}",
        "*nic_st": f"windows_net_nic_operation_status{{{l}}} == 1",
        "*gpu_temp": f"windows_gpu_temperature_celsius{{{l}}}",
        "*gpu_fan": f"windows_gpu_fan_speed_rpm{{{l}}}",
        "*gpu_info": f"windows_gpu_info{{{l}}}",
        "*thermal": f"windows_thermalzone_temperature_celsius{{{l}}}",
        "exporter": f"count(windows_exporter_build_info{{{l}}})",
    })
    cpus = [{"name": m.get("name"), "description": m.get("description"), "socket": m.get("device_id")}
            for m, _ in r["*cpu"]]
    cores = sum(v for _, v in r["*cores"]) or None
    logical = sum(v for _, v in r["*logical"]) or None
    freq = [v for _, v in r["*freq"] if _finite(v)]
    dsize = _by(r["*drive_size"], "device_id", nd=0)
    dstat = {m.get("device_id"): m.get("status") for m, _ in r["*drive_status"]}
    drives = [{"device": m.get("device_id"), "model": m.get("model") or m.get("caption"),
               "size_bytes": dsize.get(m.get("device_id")), "status": dstat.get(m.get("device_id"))}
              for m, _ in r["*drive"]]
    vsize = _by(r["*vol_size"], "volume", nd=0)
    vols = [{"volume": m.get("volume"), "label": m.get("volume_name"), "filesystem": m.get("filesystem"),
             "type": m.get("type"), "size_bytes": vsize.get(m.get("volume"))} for m, _ in r["*vol"]]
    bw = _by(r["*nic_bw"], "nic", nd=0)
    nst = {m.get("nic"): m.get("status") for m, _ in r["*nic_st"]}
    nics = [{"nic": m.get("nic"), "name": m.get("friendly_name"), "mac": m.get("mac"),
             "speed_mbps": _r(bw[m.get("nic")] * 8 / 1e6, 0) if bw.get(m.get("nic")) else None,
             "status": nst.get(m.get("nic"))} for m, _ in r["*nic"]]
    gpus = [{"device": m.get("phys") or m.get("luid"), "temperature_c": _r(v, 1)} for m, v in r["*gpu_temp"]]
    for m, v in r["*gpu_fan"]:
        gpus.append({"device": m.get("phys") or m.get("luid"), "fan_rpm": _r(v, 0)})
    temps = [{"zone": m.get("zone") or m.get("instance"), "temperature_c": _r(v, 1)} for m, v in r["*thermal"]]
    unsupported = []
    if not temps:
        unsupported.append(_unsupported("CPU temperature", "Windows ไม่เปิดเซนเซอร์อุณหภูมิ CPU ให้อ่านแบบมาตรฐาน "
                                        "(ACPI thermal zone มักไม่มี/ค่าไม่จริงบน server และ VM)"))
    if not r["*gpu_fan"]:
        unsupported.append(_unsupported("Fan speed", "windows_exporter ไม่มีข้อมูลพัดลมของเครื่อง "
                                        "(มีเฉพาะพัดลม GPU ถ้า driver รองรับ) ต้องใช้เครื่องมือของผู้ผลิต เช่น iLO / iDRAC"))
    unsupported.append(_unsupported("SMART", "windows_exporter ไม่อ่านค่า SMART ใช้ได้แค่สถานะดิสก์ (diskdrive status)"))
    if not r["exporter"]:
        unsupported.append(_unsupported("Hardware details", "เครื่องนี้ใช้ตัวเก็บในตัว Fluent Bit (ไม่มี windows_exporter) "
                                        "ติดตั้ง agent ใหม่ด้วยสคริปต์ล่าสุดเพื่อให้ได้ข้อมูลครบ"))
    return {
        "os": "windows",
        "cpu": {"models": cpus, "cores": int(cores) if cores else None, "logical_processors": int(logical) if logical else None,
                "frequency_mhz": _r(sum(freq) / len(freq) / 1e6, 0) if freq else None},
        "memory": {"total_bytes": r["mem"]},
        "disk_drives": drives, "volumes": vols, "network_adapters": nics,
        "gpus": gpus, "temperatures": temps, "unsupported": unsupported,
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
    r = _run({
        "*host": f"windows_os_hostname{{{l}}}",
        "*os": f"windows_os_info{{{l}}}",
        "install": f"max(windows_os_install_time_timestamp_seconds{{{l}}})",
        "boot": f"max(windows_system_boot_time_timestamp{{{l}}}) or max(windows_system_boot_time_timestamp_seconds{{{l}}})",
        "now": f"max(windows_time_current_timestamp_seconds{{{l}}})",
        "offset": f"max(windows_time_computed_time_offset_seconds{{{l}}})",
        "*tz": f"windows_time_timezone{{{l}}}",
        "*sync": f"windows_time_clock_sync_source{{{l}}} == 1",
        "*addr": f"windows_net_nic_address_info{{{l},{_WIN_NIC}}}",
        "*exp": f"windows_exporter_build_info{{{l}}}",
        "*coll_fail": f"windows_exporter_collector_success{{{l}}} == 0",
        "*fb": f'{{__name__=~"fluentbit_build_info|fluentbit_uptime.*",{l}}}',
    })
    host = (r["*host"] or [({}, 0)])[0][0]
    osi = (r["*os"] or [({}, 0)])[0][0]
    exp = (r["*exp"] or [({}, 0)])[0][0]
    fb_version = next((m.get("version") for m, _ in r["*fb"] if m.get("version")), None)
    addresses = sorted({(m.get("nic"), m.get("address"), m.get("family")) for m, _ in r["*addr"]
                        if m.get("address") and not str(m.get("address")).startswith(("fe80", "169.254"))})
    boot = r["boot"]
    return {
        "os": "windows",
        "hostname": host.get("hostname"), "domain": host.get("domain"), "fqdn": host.get("fqdn"),
        "os_name": osi.get("product"), "os_version": osi.get("version"),
        "build": f"{osi.get('build_number')}.{osi.get('revision')}" if osi.get("build_number") else None,
        "installation_type": osi.get("installation_type"),
        "install_time": r["install"], "boot_time": boot, "uptime_seconds": time.time() - boot if boot else None,
        "timezone": next((m.get("timezone") for m, _ in r["*tz"]), None),
        "clock_offset_seconds": _r(r["offset"], 3),
        "clock_source": next((m.get("type") for m, _ in r["*sync"]), None),
        "ip_addresses": [{"nic": n, "address": a, "family": f} for n, a, f in addresses],
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
