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
  คิวของโปรแกรมจาก log (เช่น THMux): อ่านไฟล์ log ที่ตั้งไว้แล้ว (cm-logs.json / fluent-bit.conf) ต่อจากเดิมทุกรอบ
    บรรทัดรูปแบบ "yyyyMMdd HH:mm:ss| LEVEL|Output[n] ข้อความ"
    queue exceed (size=, CRITICAL SIZE=) = เริ่มเกิน · queue decrease (size=) = กลับปกติ · port change --> = พอร์ตปัจจุบัน
    cm_netstat_app_state / _size / _critical / _port / _peak / _since{log,ch}   สถานะปัจจุบันของแต่ละ Output
    cm_netstat_app_ep{log,ch,id,start,end,peak,critical} 1                        ช่วงที่คิวเกิน 10 ครั้งล่าสุด (end=0 = ยังเกินอยู่)
    cm_netstat_app_count{log,ch,kind}                                              นับ exceed / disconnect / unknown_socket / drop / error
    cm_netstat_app_event{seq,log,ch,level,msg} = เวลา                             60 บรรทัดล่าสุด (ไม่รวม INFO ยกเว้น "กลับปกติ")
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
# ทำงานเบื้องหลังด้วยความสำคัญต่ำ ไม่แย่ง CPU กับโปรแกรมหลักของเครื่อง
try { [Diagnostics.Process]::GetCurrentProcess().PriorityClass = 'BelowNormal' } catch { }
$Started = Get-Date
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

# ---------------- PID / โปรแกรม (อัปเดตทุก $PidEvery วิ) ----------------
# ใช้ netstat.exe -ano (native) แทน Get-NetTCPConnection / Get-NetUDPEndpoint (CIM/WMI)
# เรียก WMI ถี่ ๆ ตลอดเวลาทำให้ WmiPrvSE.exe กิน CPU / หน่วยความจำสะสม (เคยเป็นสาเหตุเครื่องอืด / ค้างบน Windows Server 2016)
$PidEvery = 30
$PidMap = @{}; $UdpMap = @{}; $OutProc = @{}; $ProcName = @{}; $PidAt = [datetime]::MinValue
function PortOf($ep) { $i = $ep.LastIndexOf(":"); if ($i -lt 0) { return "" } return $ep.Substring($i + 1) }
function Update-Pids {
    $script:ProcName = @{}
    foreach ($pr in [Diagnostics.Process]::GetProcesses()) { $script:ProcName[[int]$pr.Id] = $pr.ProcessName; $pr.Dispose() }
    $script:ProcName[4] = "System (HTTP.sys)"
    $pm = @{}; $um = @{}; $est = @{}
    foreach ($line in (& netstat.exe -ano 2>$null)) {
        $f = ([string]$line).Trim() -split '\s+'
        if ($f[0] -eq "TCP" -and $f.Count -ge 5) {
            $lport = PortOf $f[1]; $rport = PortOf $f[2]; $procId = [int]$f[$f.Count - 1]
            if ($rport -eq "0") { if (-not $pm.ContainsKey($lport)) { $pm[$lport] = $procId } }     # remote x:0 = LISTEN
            elseif ($procId -gt 0) { $k = "$lport|$rport|$procId"; $est[$k] = 1 + [int]$est[$k] }
        } elseif ($f[0] -eq "UDP" -and $f.Count -ge 4) {
            $lport = PortOf $f[1]; if (-not $um.ContainsKey($lport)) { $um[$lport] = [int]$f[$f.Count - 1] }
        }
    }
    $script:PidMap = $pm; $script:UdpMap = $um
    $cnt = @{}
    foreach ($k in $est.Keys) {
        $x = $k -split '\|'
        if ($pm.ContainsKey($x[0])) { continue }                                    # ขาเข้า (พอร์ตที่เปิดรับ) ไม่ใช่ขาออก
        $ck = "$($x[1])|$($x[2])"; $cnt[$ck] = $est[$k] + [int]$cnt[$ck]
    }
    $script:OutProc = @{}
    foreach ($e in ($cnt.GetEnumerator() | Sort-Object Value -Descending)) {
        $x = $e.Key -split '\|'
        if (-not $script:OutProc.ContainsKey($x[0])) { $script:OutProc[$x[0]] = [int]$x[1] }
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

# ---------------- คิวของโปรแกรมจาก log ----------------
$Inv = [Globalization.CultureInfo]::InvariantCulture
$AppFiles = @{}; $AppState = @{}; $AppSeq = 0; $AppAt = [datetime]::MinValue
$AppEvery = 30                         # วินาที: อ่านไฟล์ log ของโปรแกรมทุกเท่านี้ (เวลาของแต่ละช่วงมาจาก log จึงไม่คลาดแม้อ่านห่าง)
$AppSources = @{}                      # pattern -> @{ files; newest; lines }  ให้ central บอกได้ว่าทำไมยังไม่มีข้อมูล
$AppEvents = New-Object System.Collections.Generic.List[object]
$AppRx = [regex]'^(\d{8})\s+(\d{2}:\d{2}:\d{2})\|\s*([A-Za-z]+)\s*\|\s*([^\[|]+?)\[(\d+)\]\s*(.*)$'
function Get-AppPatterns {
    $p = @()
    $lc = Join-Path $Dir "cm-logs.json"
    if (Test-Path $lc) { try { $p += @((Get-Content $lc -Raw | ConvertFrom-Json).logs) } catch { } }
    else {
        $fb = Join-Path $Dir "fluent-bit.conf"
        if (Test-Path $fb) { Select-String -Path $fb -Pattern '^\s*Path\s+(.+)$' | ForEach-Object { $p += ($_.Matches[0].Groups[1].Value -split ",") } }
    }
    if (Test-Path $ConfF) { try { $p += @((Get-Content $ConfF -Raw | ConvertFrom-Json).applogs) } catch { } }
    return @($p | ForEach-Object { "$_".Trim() } | Where-Object { $_ -and $_ -notlike "*inetpub*" } | Select-Object -Unique)
}
function Update-AppSources {
    $since = (Get-Date).AddDays(-2)
    $pats = Get-AppPatterns
    $fresh = @{}                       # สร้างตารางใหม่ทุกรอบแล้วแทนที่ทั้งก้อน (กัน PowerShell 5.1 ไม่อัปเดตตารางเดิม)
    foreach ($pat in $pats) {
        $all = @(Get-ChildItem -Path $pat -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
        $recent = @($all | Where-Object { $_.LastWriteTime -gt $since } | Select-Object -First 2)
        $old = 0; if ($script:AppSources.ContainsKey($pat)) { $old = [int64]$script:AppSources[$pat].lines }
        $fresh[$pat] = @{ files = $all.Count; newest = $(if ($all.Count) { $all[0].Name } else { "" }); lines = $old; recent = $recent.Count }
        foreach ($f in $recent) {
            if ($AppFiles.ContainsKey($f.FullName)) { continue }
            $b = New-Object byte[] 2; $enc = [Text.Encoding]::UTF8; $skip = 0
            try { $fs = [IO.File]::Open($f.FullName, 'Open', 'Read', 'ReadWrite, Delete'); $n = $fs.Read($b, 0, 2); $fs.Close()
                  if ($n -eq 2 -and $b[0] -eq 0xFF -and $b[1] -eq 0xFE) { $enc = [Text.Encoding]::Unicode; $skip = 2 } } catch { continue }
            $off = [math]::Max($skip, $f.Length - 524288); if ($enc -eq [Text.Encoding]::Unicode -and ($off % 2)) { $off++ }
            $AppFiles[$f.FullName] = @{ off = [int64]$off; enc = $enc; log = (Split-Path (Split-Path $f.FullName -Parent) -Leaf); pat = $pat }
        }
    }
    $script:AppSources = $fresh
    $script:AppAt = Get-Date
}
function App-Line($log, $line, $pat) {
    $m = $AppRx.Match($line); if (-not $m.Success) { return }
    if ($pat -and $script:AppSources.ContainsKey($pat)) { $script:AppSources[$pat].lines++ }
    try { $ts = [int64]([DateTimeOffset][datetime]::ParseExact($m.Groups[1].Value + " " + $m.Groups[2].Value, "yyyyMMdd HH:mm:ss", $Inv)).ToUnixTimeSeconds() } catch { return }
    $lv = $m.Groups[3].Value.ToUpper(); $ch = $m.Groups[4].Value.Trim() + "[" + $m.Groups[5].Value + "]"; $msg = $m.Groups[6].Value.Trim()
    $k = "$log|$ch"
    if (-not $AppState.ContainsKey($k)) {
        $AppState[$k] = @{ log = $log; ch = $ch; exceed = 0; size = $null; crit = $null; port = $null; since = $ts; peak = 0; n = 0
                           eps = (New-Object System.Collections.Generic.List[object]); last = $ts
                           cnt = @{ exceed = 0; disconnect = 0; unknown_socket = 0; drop = 0; error = 0 } }
    }
    $s = $AppState[$k]; $s.last = $ts; $show = $false
    if ($msg -match 'queue exceed.*?size=(\d+).*?CRITICAL SIZE=(\d+)') {
        $size = [int64]$matches[1]; $s.crit = [int64]$matches[2]; $s.size = $size
        if (-not $s.exceed) {
            $s.exceed = 1; $s.since = $ts; $s.peak = $size; $s.n++; $s.cnt.exceed++
            $s.eps.Add(@{ id = "$ts"; start = $ts; end = 0; peak = $size; crit = $s.crit })
            if ($s.eps.Count -gt 10) { $s.eps.RemoveAt(0) }
        } elseif ($size -gt $s.peak) { $s.peak = $size }
        if ($s.eps.Count) { $s.eps[$s.eps.Count - 1].peak = $s.peak }
        $show = $true
    } elseif ($msg -match 'queue decrease.*?size=(\d+)') {
        $s.size = [int64]$matches[1]
        if ($s.exceed) { $s.exceed = 0; $s.since = $ts; if ($s.eps.Count) { $s.eps[$s.eps.Count - 1].end = $ts }; $lv = "RECOVER"; $show = $true }
    } elseif ($msg -match 'port change\s*-->\s*(\d+)') { $s.port = [int]$matches[1] }
    elseif ($msg -match 'notify disconnect') { $s.cnt.disconnect++ }
    elseif ($msg -match 'Unknown Socket') { $s.cnt.unknown_socket++ }
    elseif ($msg -match 'drop new connection') { $s.cnt.drop++ }
    if ($lv -eq "ERROR") { $s.cnt.error++; $show = $true } elseif ($lv -like "WARN*") { $show = $true }
    if ($show) {
        $script:AppSeq++
        $AppEvents.Add(@{ seq = $AppSeq; ts = $ts; log = $log; ch = $ch; lv = $lv; msg = $msg })
        if ($AppEvents.Count -gt 60) { $AppEvents.RemoveAt(0) }
    }
}
function Read-AppLogs {
    foreach ($path in @($AppFiles.Keys)) {
        $e = $AppFiles[$path]
        try {
            $len = (New-Object IO.FileInfo $path).Length
            if ($len -lt $e.off) { $e.off = if ($e.enc -eq [Text.Encoding]::Unicode) { 2 } else { 0 } }   # ไฟล์ถูกตัด / หมุนใหม่
            if ($len -le $e.off) { continue }
            $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite, Delete')
            $fs.Seek($e.off, 'Begin') | Out-Null
            $buf = New-Object byte[] ([math]::Min($len - $e.off, 1048576))
            $n = $fs.Read($buf, 0, $buf.Length); $fs.Close()
            $text = $e.enc.GetString($buf, 0, $n)
            $cut = $text.LastIndexOf("`n"); if ($cut -lt 0) { continue }
            $text = $text.Substring(0, $cut + 1)
            $e.off += $e.enc.GetByteCount($text)
            foreach ($line in ($text -split "`r?`n")) { if ($line) { App-Line $e.log $line $e.pat } }
        } catch { }
    }
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
    if (($nowDt - $PidAt).TotalSeconds -ge $PidEvery) { Update-Pids }
    if (($nowDt - $HttpAt).TotalSeconds -ge 300) { Update-Http }
    if (($nowDt - $AppAt).TotalSeconds -ge $AppEvery) { Update-AppSources; Read-AppLogs }

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

    # คิวของโปรแกรมจาก log
    $L.Add("# TYPE cm_netstat_app_source gauge")
    $srcRows = @{}
    foreach ($pat in @($script:AppSources.Keys)) { $srcRows[$pat] = $script:AppSources[$pat] }
    if (-not $srcRows.Count) {          # สำรอง: รายงานจากไฟล์ที่กำลังอ่านอยู่จริง
        foreach ($f in @($AppFiles.Values)) {
            $pk = [string]$f.pat; if (-not $pk) { continue }
            if (-not $srcRows.ContainsKey($pk)) { $srcRows[$pk] = @{ files = 0; newest = ""; lines = 0; recent = 0 } }
            $srcRows[$pk].files++; $srcRows[$pk].recent = [math]::Min(2, $srcRows[$pk].files)
        }
    }
    foreach ($pat in @($srcRows.Keys)) {
        $src = $srcRows[$pat]
        $L.Add("cm_netstat_app_source{path=`"$(Esc ($pat -replace '\\', '/'))`",files=`"$($src.files)`",recent=`"$($src.recent)`",newest=`"$(Esc $src.newest)`"} $([int64]$src.lines)")
    }
    $L.Add("# TYPE cm_netstat_app_every_seconds gauge")
    $L.Add("cm_netstat_app_every_seconds $AppEvery")
    if ($AppState.Count) {
        foreach ($name in "state", "size", "critical", "port", "peak", "since") {
            $L.Add("# TYPE cm_netstat_app_$name gauge")
            foreach ($s in $AppState.Values) {
                $v = switch ($name) { "state" { $s.exceed } "size" { $s.size } "critical" { $s.crit } "port" { $s.port } "peak" { $s.peak } "since" { $s.since } }
                if ($null -ne $v) { $L.Add("cm_netstat_app_$name{log=`"$(Esc $s.log)`",ch=`"$(Esc $s.ch)`"} $v") }
            }
        }
        $L.Add("# TYPE cm_netstat_app_ep gauge")
        foreach ($s in $AppState.Values) { foreach ($ep in $s.eps) {
            $L.Add("cm_netstat_app_ep{log=`"$(Esc $s.log)`",ch=`"$(Esc $s.ch)`",id=`"$($ep.id)`",start=`"$($ep.start)`",end=`"$($ep.end)`",peak=`"$($ep.peak)`",critical=`"$($ep.crit)`"} 1")
        } }
        $L.Add("# TYPE cm_netstat_app_count gauge")
        foreach ($s in $AppState.Values) { foreach ($c in $s.cnt.Keys) { $L.Add("cm_netstat_app_count{log=`"$(Esc $s.log)`",ch=`"$(Esc $s.ch)`",kind=`"$c`"} $($s.cnt[$c])") } }
        $L.Add("# TYPE cm_netstat_app_event gauge")
        foreach ($ev in $AppEvents) {
            $m2 = $ev.msg; if ($m2.Length -gt 300) { $m2 = $m2.Substring(0, 300) }
            $L.Add("cm_netstat_app_event{seq=`"$($ev.seq)`",log=`"$(Esc $ev.log)`",ch=`"$(Esc $ev.ch)`",level=`"$($ev.lv)`",msg=`"$(Esc $m2)`"} $($ev.ts)")
        }
    }

    $t0.Stop()
    $L.Add("# TYPE cm_netstat_collect_ms gauge")
    $L.Add("cm_netstat_collect_ms $($t0.ElapsedMilliseconds)")
    try {
        [IO.File]::WriteAllText($tmp, (($L -join "`n") + "`n"), $enc)
        Move-Item $tmp $out -Force
    } catch { }

    # คืนหน่วยความจำเป็นระยะ (ลูปยาว) + ล้างค่าสูงสุดของพอร์ตที่หายไป
    if ((++$loop % 300) -eq 0) {
        foreach ($k in @($Hist.Keys)) { $q = $Hist[$k]; if ($q.Count -eq 0 -or ($now - $q.ToArray()[-1][0]) -gt 60) { $Hist.Remove($k) } }
        [GC]::Collect()
        # กันกินหน่วยความจำสะสม: ใช้เกิน 300 MB หรือรันครบ 6 ชม. -> ออก (task ตรวจทุก 5 นาทีจะเปิดใหม่ให้)
        $me = [Diagnostics.Process]::GetCurrentProcess()
        if ($me.WorkingSet64 -gt 300MB -or ($nowDt - $Started).TotalHours -ge 6) { try { $mutex.ReleaseMutex() } catch { }; exit 0 }
    }
    $wait = [int]($Interval * 1000 - $t0.ElapsedMilliseconds)
    if ($wait -lt 200) { $wait = 200 }
    Start-Sleep -Milliseconds $wait
}
