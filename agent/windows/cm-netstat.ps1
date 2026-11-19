<#
  cm-netstat.ps1 (Central Monitor) - Netstat Monitor: TCP / UDP ของเครื่อง Windows แบบ real-time -> textfile ของ windows_exporter
  ---------------------------------------------------------------------------------------------
  รันค้างไว้ 1 process (scheduled task "CentralMonitorNetstat" ตอนเปิดเครื่อง + ทุก 5 นาทีเผื่อหลุด, SYSTEM)
  ไม่สร้าง process ใหม่ทุกวินาที: วนลูปเก็บทุก -Interval วินาที (1 / 2 / 5, ปริยาย 1) ด้วย .NET IPGlobalProperties
  เขียน cm_netstat.prom ใหม่ทุกรอบ (เขียนไฟล์ชั่วคราวแล้วย้ายทับ) -> central อ่านจาก windows_exporter
    cm_netstat_info{interval,os,version} 1                     ข้อมูล agent / Windows
    cm_netstat_heartbeat_seconds                              เวลาที่เก็บล่าสุด (unix) -> central รู้ว่า agent ยังส่งอยู่
    cm_netstat_collect_ms                                     ใช้เวลาเก็บกี่ ms
    cm_netstat_listen{proto,port,addr,pid,proc} 1             พอร์ตที่เปิดรับ (TCP LISTEN / UDP endpoint)
    cm_netstat_conn{proto,dir,port,state}                     จำนวนการเชื่อมต่อตามสถานะ (เฉพาะที่ไม่ใช่ 0)
                                                              dir=in: ต่อเข้าพอร์ตที่เครื่องนี้เปิดรับ (port = พอร์ตของเครื่องนี้)
                                                              dir=out: เครื่องนี้ต่อออก (port = พอร์ตปลายทาง)
    cm_netstat_conn_total{proto,dir,port}                     รวมทุกสถานะ
    cm_netstat_conn_peak{proto,dir,port}                      สูงสุดใน 15 วินาทีล่าสุด (central scrape ทุก 15 วิ ไม่พลาดค่าพุ่ง)
    cm_netstat_peer{dir,port,remote}                          ปลายทาง 3 อันดับแรกต่อพอร์ต
    cm_netstat_proc{dir,port,pid,proc} 1                      โปรแกรมของการเชื่อมต่อขาออก (อัปเดตทุก 15 วิ)
    cm_netstat_accept_queue{proto,port,source}                คิวรอรับของพอร์ตที่ HTTP.sys (IIS) เปิดรับ = CurrentQueueSize
    cm_netstat_accept_queue_peak / cm_netstat_accept_queue_limit (queueLength ของ app pool)
    cm_netstat_queue_supported{kind} 0/1                      recv / send / accept อ่านได้ไหม
  Windows ไม่มี Recv-Q / Send-Q ต่อ socket ใน API ปกติ -> ส่ง supported=0 (central แสดง Unsupported ไม่ใช่ 0)
  ทุกส่วนอ่านอย่างเดียว · ส่วนไหนอ่านไม่ได้ก็ข้าม · ติดตั้งโดย install-fluentbit.ps1 · ถอน: uninstall-fluentbit.ps1
#>
param([ValidateSet(1, 2, 5)] [int] $Interval = 0)
$ErrorActionPreference = "SilentlyContinue"
$Version = "1"
$Dir     = Join-Path $env:ProgramData "central-monitor-agent"
$TextDir = Join-Path $Dir "textfile"
$ConfF   = Join-Path $Dir "cm-netstat.json"
New-Item -ItemType Directory -Force -Path $TextDir | Out-Null

# ทำงานได้ทีละ 1 process (task ทุก 5 นาทีแค่ตรวจว่ายังรันอยู่)
$mutex = New-Object System.Threading.Mutex($false, "Global\CentralMonitorNetstat")
try { if (-not $mutex.WaitOne(0)) { exit 0 } } catch [System.Threading.AbandonedMutexException] { }

if (-not $Interval) {
    $Interval = 1
    if (Test-Path $ConfF) { try { $c = Get-Content $ConfF -Raw | ConvertFrom-Json; if (@(1, 2, 5) -contains [int]$c.interval) { $Interval = [int]$c.interval } } catch { } }
}
$PeakWindow = 15
$MaxPorts   = 300
$MaxOut     = 60

function Esc($s) { return ([string]$s).Replace('\', '\\').Replace('"', '\"').Replace("`r", " ").Replace("`n", " ") }
function Ip($a) { return (([string]$a) -replace '^::ffff:', '') }
$IsLoop = { param($ip) $ip -like "127.*" -or $ip -eq "::1" }

$OsName = ""
try { $OsName = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion").ProductName + " (" + [Environment]::OSVersion.Version.ToString() + ")" } catch { }

$StateName = @{
    "Established" = "established"; "TimeWait" = "time_wait"; "CloseWait" = "close_wait"; "SynSent" = "syn_sent"
    "SynReceived" = "syn_received"; "FinWait1" = "fin_wait1"; "FinWait2" = "fin_wait2"; "LastAck" = "last_ack"
    "Closing" = "closing"; "Closed" = "closed"; "DeleteTcb" = "delete_tcb"
}

# ---------------- PID / โปรแกรม (อัปเดตทุก 15 วิ ใช้ CIM ซึ่งหนักกว่า .NET) ----------------
$PidMap = @{}; $UdpMap = @{}; $OutProc = @{}; $ProcName = @{}; $PidAt = [datetime]::MinValue
function Update-Pids {
    $script:ProcName = @{}
    Get-Process | ForEach-Object { $script:ProcName[[int]$_.Id] = $_.ProcessName }
    $script:ProcName[4] = "System (HTTP.sys)"
    $script:PidMap = @{}
    Get-NetTCPConnection -State Listen | ForEach-Object { $script:PidMap["$($_.LocalPort)"] = [int]$_.OwningProcess }
    $script:UdpMap = @{}
    Get-NetUDPEndpoint | ForEach-Object { $script:UdpMap["$($_.LocalPort)"] = [int]$_.OwningProcess }
    $script:OutProc = @{}
    $lp = $script:PidMap
    Get-NetTCPConnection -State Established | Where-Object { -not $lp.ContainsKey("$($_.LocalPort)") } |
        Group-Object RemotePort, OwningProcess | Sort-Object Count -Descending | ForEach-Object {
            $p = $_.Group[0]
            if (-not $script:OutProc.ContainsKey("$($p.RemotePort)")) { $script:OutProc["$($p.RemotePort)"] = [int]$p.OwningProcess }
        }
    $script:PidAt = Get-Date
}

# ---------------- คิวของ HTTP.sys (IIS): perf counter ต่อ app pool + พอร์ตของ site ----------------
$HttpPorts = @{}; $HttpLimit = @{}; $HttpCounters = @{}; $HttpAt = [datetime]::MinValue
function Update-Http {
    $script:HttpPorts = @{}; $script:HttpLimit = @{}; $script:HttpCounters = @{}
    $cfg = Join-Path $env:windir "System32\inetsrv\config\applicationHost.config"
    if (Test-Path $cfg) {
        try {
            $x = [xml](Get-Content $cfg -Raw)
            $ah = $x.configuration.'system.applicationHost'
            $poolLimit = @{}
            foreach ($p in @($ah.applicationPools.add)) { if ($p.name) { $poolLimit[$p.name] = if ($p.queueLength) { [int]$p.queueLength } else { 1000 } } }
            $defPool = if ($ah.sites.applicationDefaults.applicationPool) { $ah.sites.applicationDefaults.applicationPool } else { "DefaultAppPool" }
            foreach ($s in @($ah.sites.site)) {
                $root = @($s.application) | Where-Object { $_.path -eq "/" } | Select-Object -First 1
                $pool = if ($root -and $root.applicationPool) { $root.applicationPool } else { $defPool }
                foreach ($b in @($s.bindings.binding)) {
                    if ($b.protocol -notin "http", "https") { continue }
                    $port = ([string]$b.bindingInformation -split ":")[-2]
                    if (-not $port) { continue }
                    if (-not $script:HttpPorts.ContainsKey($port)) { $script:HttpPorts[$port] = New-Object System.Collections.Generic.HashSet[string] }
                    [void]$script:HttpPorts[$port].Add($pool)
                    if ($poolLimit.ContainsKey($pool)) { $script:HttpLimit[$pool] = $poolLimit[$pool] } else { $script:HttpLimit[$pool] = 1000 }
                }
            }
        } catch { }
    }
    try {
        if ([System.Diagnostics.PerformanceCounterCategory]::Exists("HTTP Service Request Queues")) {
            $cat = New-Object System.Diagnostics.PerformanceCounterCategory("HTTP Service Request Queues")
            foreach ($inst in $cat.GetInstanceNames()) {
                $script:HttpCounters[$inst.ToLower()] = New-Object System.Diagnostics.PerformanceCounter("HTTP Service Request Queues", "CurrentQueueSize", $inst, $true)
            }
        }
    } catch { }
    $script:HttpAt = Get-Date
}

# ---------------- ค่าสูงสุดใน 15 วิ ----------------
$Hist = @{}        # key -> Queue of @(time, value)
function Peak($key, $value, $now) {
    if (-not $Hist.ContainsKey($key)) { $Hist[$key] = New-Object System.Collections.Generic.Queue[object] }
    $q = $Hist[$key]; $q.Enqueue(@($now, $value))
    while ($q.Count -gt 0 -and ($now - $q.Peek()[0]) -gt $PeakWindow) { [void]$q.Dequeue() }
    $m = 0; foreach ($e in $q) { if ($e[1] -gt $m) { $m = $e[1] } }
    return $m
}

$out = Join-Path $TextDir "cm_netstat.prom"
$tmp = "$out.tmp"
$enc = New-Object Text.UTF8Encoding($false)
$ipp = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties()
$loop = 0

while ($true) {
    $t0 = [Diagnostics.Stopwatch]::StartNew()
    $nowDt = Get-Date
    $now = [double]([DateTimeOffset]$nowDt).ToUnixTimeMilliseconds() / 1000
    if (($nowDt - $PidAt).TotalSeconds -ge 15) { Update-Pids }
    if (($nowDt - $HttpAt).TotalSeconds -ge 300) { Update-Http }

    $L = New-Object System.Collections.Generic.List[string]
    $listenAddr = @{}
    foreach ($ep in $ipp.GetActiveTcpListeners()) {
        $k = "$($ep.Port)"
        if (-not $listenAddr.ContainsKey($k)) { $listenAddr[$k] = New-Object System.Collections.Generic.List[string] }
        $a = Ip $ep.Address; if (-not $listenAddr[$k].Contains($a)) { $listenAddr[$k].Add($a) }
    }
    $counts = @{}; $totals = @{}; $peers = @{}
    foreach ($c in $ipp.GetActiveTcpConnections()) {
        $rip = Ip $c.RemoteEndPoint.Address
        $lport = "$($c.LocalEndPoint.Port)"
        if ($listenAddr.ContainsKey($lport)) { $dir = "in"; $port = $lport } else {
            if (& $IsLoop $rip) { continue }
            $dir = "out"; $port = "$($c.RemoteEndPoint.Port)"
        }
        $st = $StateName["$($c.State)"]; if (-not $st) { $st = "other" }
        $pk = "$dir|$port"
        $sk = "$pk|$st"
        $counts[$sk] = 1 + [int]$counts[$sk]
        $totals[$pk] = 1 + [int]$totals[$pk]
        if ($st -eq "established") {
            if (-not $peers.ContainsKey($pk)) { $peers[$pk] = @{} }
            $peers[$pk][$rip] = 1 + [int]$peers[$pk][$rip]
        }
    }

    $L.Add("# HELP cm_netstat_info Netstat collector of Central Monitor")
    $L.Add("# TYPE cm_netstat_info gauge")
    $L.Add("cm_netstat_info{interval=`"$Interval`",os=`"$(Esc $OsName)`",version=`"$Version`"} 1")
    $L.Add("# TYPE cm_netstat_heartbeat_seconds gauge")
    $L.Add(("cm_netstat_heartbeat_seconds {0}" -f $now.ToString([Globalization.CultureInfo]::InvariantCulture)))

    # พอร์ตที่เปิดรับ
    $L.Add("# HELP cm_netstat_listen Listening TCP ports / UDP endpoints")
    $L.Add("# TYPE cm_netstat_listen gauge")
    $n = 0
    foreach ($k in ($listenAddr.Keys | Sort-Object { [int]$_ })) {
        if (++$n -gt $MaxPorts) { break }
        $pid0 = $PidMap[$k]
        $L.Add("cm_netstat_listen{proto=`"tcp`",port=`"$k`",addr=`"$(Esc (($listenAddr[$k]) -join ','))`",pid=`"$pid0`",proc=`"$(Esc $ProcName[[int]$pid0])`"} 1")
    }
    $udp = @{}
    foreach ($ep in $ipp.GetActiveUdpListeners()) {
        $k = "$($ep.Port)"; $a = Ip $ep.Address
        if (-not $udp.ContainsKey($k)) { $udp[$k] = New-Object System.Collections.Generic.List[string] }
        if (-not $udp[$k].Contains($a)) { $udp[$k].Add($a) }
    }
    $n = 0
    foreach ($k in ($udp.Keys | Sort-Object { [int]$_ })) {
        if (++$n -gt $MaxPorts) { break }
        $pid0 = $UdpMap[$k]
        $L.Add("cm_netstat_listen{proto=`"udp`",port=`"$k`",addr=`"$(Esc (($udp[$k]) -join ','))`",pid=`"$pid0`",proc=`"$(Esc $ProcName[[int]$pid0])`"} 1")
    }

    # การเชื่อมต่อ (ขาเข้าทุกพอร์ตที่เปิดรับ + ขาออก $MaxOut พอร์ตที่มากสุด)
    $keys = @($totals.Keys | Where-Object { $_ -like "in|*" }) +
            @($totals.Keys | Where-Object { $_ -like "out|*" } | Sort-Object { -$totals[$_] } | Select-Object -First $MaxOut)
    foreach ($k in $listenAddr.Keys) { if (-not $totals.ContainsKey("in|$k")) { $keys += "in|$k" } }
    $L.Add("# HELP cm_netstat_conn TCP connections by state (non-zero only)")
    $L.Add("# TYPE cm_netstat_conn gauge")
    foreach ($sk in $counts.Keys) {
        $f = $sk -split '\|'
        if ($keys -notcontains "$($f[0])|$($f[1])") { continue }
        $L.Add("cm_netstat_conn{proto=`"tcp`",dir=`"$($f[0])`",port=`"$($f[1])`",state=`"$($f[2])`"} $($counts[$sk])")
    }
    $L.Add("# TYPE cm_netstat_conn_total gauge")
    $T = New-Object System.Collections.Generic.List[string]
    $P = New-Object System.Collections.Generic.List[string]
    foreach ($pk in $keys) {
        $f = $pk -split '\|'; $v = [int]$totals[$pk]
        $T.Add("cm_netstat_conn_total{proto=`"tcp`",dir=`"$($f[0])`",port=`"$($f[1])`"} $v")
        $P.Add("cm_netstat_conn_peak{proto=`"tcp`",dir=`"$($f[0])`",port=`"$($f[1])`"} $(Peak "c|$pk" $v $now)")
    }
    $L.AddRange($T); $L.Add("# TYPE cm_netstat_conn_peak gauge"); $L.AddRange($P)
    $L.Add("# TYPE cm_netstat_peer gauge")
    foreach ($pk in $peers.Keys) {
        if ($keys -notcontains $pk) { continue }
        $f = $pk -split '\|'
        $peers[$pk].GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 3 | ForEach-Object {
            $L.Add("cm_netstat_peer{dir=`"$($f[0])`",port=`"$($f[1])`",remote=`"$(Esc $_.Key)`"} $($_.Value)")
        }
    }
    $L.Add("# TYPE cm_netstat_proc gauge")
    foreach ($pk in $keys) {
        $f = $pk -split '\|'
        if ($f[0] -ne "out" -or -not $OutProc.ContainsKey($f[1])) { continue }
        $pid0 = $OutProc[$f[1]]
        $L.Add("cm_netstat_proc{dir=`"out`",port=`"$($f[1])`",pid=`"$pid0`",proc=`"$(Esc $ProcName[[int]$pid0])`"} 1")
    }

    # คิว: HTTP.sys เท่านั้นที่อ่านได้ (พอร์ตของ site IIS ที่ System/PID 4 เปิดรับ)
    $acceptOk = $HttpCounters.Count -gt 0
    $L.Add("# HELP cm_netstat_queue_supported 1 = this queue can be read on this machine")
    $L.Add("# TYPE cm_netstat_queue_supported gauge")
    $L.Add("cm_netstat_queue_supported{kind=`"recv`"} 0")
    $L.Add("cm_netstat_queue_supported{kind=`"send`"} 0")
    $L.Add("cm_netstat_queue_supported{kind=`"accept`"} $(if ($acceptOk) { 1 } else { 0 })")
    if ($acceptOk) {
        $Q = New-Object System.Collections.Generic.List[string]; $QP = New-Object System.Collections.Generic.List[string]; $QL = New-Object System.Collections.Generic.List[string]
        foreach ($port in $HttpPorts.Keys) {
            if (-not $listenAddr.ContainsKey($port) -or [int]$PidMap[$port] -ne 4) { continue }
            $sum = 0; $lim = 0; $any = $false
            foreach ($pool in $HttpPorts[$port]) {
                $pc = $HttpCounters[$pool.ToLower()]
                if ($pc) { try { $sum += [int64]$pc.RawValue; $any = $true } catch { } }
                $lim += [int]$HttpLimit[$pool]
            }
            if (-not $any) { continue }
            $Q.Add("cm_netstat_accept_queue{proto=`"tcp`",port=`"$port`",source=`"http.sys`"} $sum")
            $QP.Add("cm_netstat_accept_queue_peak{proto=`"tcp`",port=`"$port`",source=`"http.sys`"} $(Peak "q|$port" $sum $now)")
            if ($lim -gt 0) { $QL.Add("cm_netstat_accept_queue_limit{proto=`"tcp`",port=`"$port`",source=`"http.sys`"} $lim") }
        }
        $L.Add("# TYPE cm_netstat_accept_queue gauge"); $L.AddRange($Q)
        $L.Add("# TYPE cm_netstat_accept_queue_peak gauge"); $L.AddRange($QP)
        $L.Add("# TYPE cm_netstat_accept_queue_limit gauge"); $L.AddRange($QL)
    }

    $t0.Stop()
    $L.Add("# TYPE cm_netstat_collect_ms gauge")
    $L.Add("cm_netstat_collect_ms $($t0.ElapsedMilliseconds)")
    try {
        [IO.File]::WriteAllText($tmp, (($L -join "`n") + "`n"), $enc)
        Move-Item $tmp $out -Force
    } catch { }

    # คืนหน่วยความจำเป็นระยะ (ลูปยาว)
    if ((++$loop % 300) -eq 0) { [GC]::Collect() }
    $wait = [int]($Interval * 1000 - $t0.ElapsedMilliseconds)
    if ($wait -lt 200) { $wait = 200 }
    Start-Sleep -Milliseconds $wait
}
