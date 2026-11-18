<#
  cm-netsec.ps1 (Central Monitor) - Network & Security ของเครื่อง Windows -> textfile ของ windows_exporter
  ---------------------------------------------------------------------------------------------
  scheduled task "CentralMonitorNetSec" ทุก 5 นาที (SYSTEM) เขียน cm_netsec.prom:
    cm_net_conn{dir,peer,port,proc}            การเชื่อมต่อ TCP ที่ค้างอยู่ (Established) รวมตาม ขาเข้า/ขาออก + ปลายทาง + พอร์ต + โปรแกรม
                                               in  = เครื่องอื่นต่อเข้าพอร์ตที่เครื่องนี้เปิดรับ (port = พอร์ตของเครื่องนี้)
                                               out = เครื่องนี้ต่อออกไปหาเครื่องอื่น (port = พอร์ตปลายทาง)
    cm_net_addr{ip,if}                         IPv4 ของเครื่อง (ใช้จับคู่ว่าใครต่อหาใคร)
    cm_net_route{dest,gw,if,metric}            ตาราง route (IPv4 ไม่รวม host route / multicast)
    cm_fw_service{state}                       service Windows Firewall (MpsSvc)
    cm_fw_profile{profile,enabled,inbound,outbound}
    cm_fw_rule{name,dir,action,proto,port,remote,program}   กฎที่เปิดใช้ (Block ทั้งหมด + Allow ขาเข้า) ไม่เกิน 400
    cm_login_failed_24h                        จำนวน logon ไม่สำเร็จ (Security 4625) 24 ชม.
    cm_login_failed_by{ip,user}                แยกตามต้นทาง (20 อันดับ)
    cm_login_ok{n,user,ip,type}                logon สำเร็จแบบ console / RDP ล่าสุด (Security 4624 type 2 / 10) ค่า = เวลา (unix)
  ทุกส่วนอ่านอย่างเดียว · ส่วนไหนอ่านไม่ได้ก็ข้าม
  ติดตั้งโดย install-fluentbit.ps1 (ทั้ง push / pull) · ถอน: uninstall-fluentbit.ps1
#>
$ErrorActionPreference = "SilentlyContinue"
$TextDir = Join-Path $env:ProgramData "central-monitor-agent\textfile"
New-Item -ItemType Directory -Force -Path $TextDir | Out-Null
function Esc($s) { return ([string]$s).Replace('\', '\\').Replace('"', '\"').Replace("`r", " ").Replace("`n", " ") }
$L = New-Object System.Collections.Generic.List[string]
function Head($name, $help) { $L.Add("# HELP $name $help"); $L.Add("# TYPE $name gauge") }
# ชื่อ + label ซ้ำกันใน 1 ไฟล์ = windows_exporter ไม่อ่านทั้งไฟล์ (เช่นกฎ firewall ชื่อเดียวกันหลาย profile) -> เก็บตัวแรก
$Seen = New-Object 'System.Collections.Generic.HashSet[string]'
function Sample([string] $line) { $k = $line.Substring(0, $line.LastIndexOf(' ')); if ($Seen.Add($k)) { $L.Add($line) } }
$loop = { param($ip) $ip -like "127.*" -or $ip -eq "::1" -or $ip -like "0.0.0.0" -or $ip -eq "::" }

# ---------------- connections
$procs = @{}
Get-Process | ForEach-Object { $procs[[int]$_.Id] = $_.ProcessName }
$listen = @{}
Get-NetTCPConnection -State Listen | ForEach-Object { $listen[[int]$_.LocalPort] = $true }
$agg = @{}
foreach ($c in @(Get-NetTCPConnection -State Established)) {
    $peer = ([string]$c.RemoteAddress) -replace '^::ffff:', ''
    if (& $loop $peer) { continue }
    if ($listen.ContainsKey([int]$c.LocalPort)) { $dir = "in"; $port = $c.LocalPort } else { $dir = "out"; $port = $c.RemotePort }
    $p = $procs[[int]$c.OwningProcess]
    $k = "$dir|$peer|$port|$p"
    $agg[$k] = 1 + [int]$agg[$k]
}
Head "cm_net_conn" "Established TCP connections grouped by direction, peer, port, process"
$agg.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 400 | ForEach-Object {
    $f = $_.Key -split '\|', 4
    Sample("cm_net_conn{dir=`"$($f[0])`",peer=`"$(Esc $f[1])`",port=`"$($f[2])`",proc=`"$(Esc $f[3])`"} $($_.Value)")
}

# ---------------- addresses / routes
Head "cm_net_addr" "IPv4 addresses of this machine"
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } | ForEach-Object {
    Sample("cm_net_addr{ip=`"$($_.IPAddress)`",if=`"$(Esc $_.InterfaceAlias)`"} $($_.PrefixLength)")
}
Head "cm_net_route" "IPv4 routes (value = route metric)"
$seen = @{}
Get-NetRoute -AddressFamily IPv4 | Where-Object {
    $_.DestinationPrefix -notlike "*/32" -and $_.DestinationPrefix -notlike "224.*" -and $_.DestinationPrefix -notlike "127.*" -and $_.DestinationPrefix -ne "255.255.255.255/32"
} | Sort-Object DestinationPrefix | Select-Object -First 200 | ForEach-Object {
    $k = "$($_.DestinationPrefix)|$($_.NextHop)|$($_.InterfaceAlias)"
    if ($seen[$k]) { return }; $seen[$k] = 1
    Sample("cm_net_route{dest=`"$($_.DestinationPrefix)`",gw=`"$($_.NextHop)`",if=`"$(Esc $_.InterfaceAlias)`",metric=`"$($_.RouteMetric)`"} $([int]$_.RouteMetric + [int]$_.InterfaceMetric)")
}

# ---------------- firewall
$svc = Get-Service MpsSvc
Head "cm_fw_service" "Windows Firewall service (MpsSvc) 1 = running"
Sample("cm_fw_service{state=`"$(if ($svc) { $svc.Status } else { 'missing' })`"} $(if ($svc -and $svc.Status -eq 'Running') { 1 } else { 0 })")
Head "cm_fw_profile" "Windows Firewall profiles"
foreach ($p in @(Get-NetFirewallProfile)) {
    $in = if ("$($p.DefaultInboundAction)" -eq "NotConfigured") { "Block" } else { "$($p.DefaultInboundAction)" }
    $out = if ("$($p.DefaultOutboundAction)" -eq "NotConfigured") { "Allow" } else { "$($p.DefaultOutboundAction)" }
    $on = if ("$($p.Enabled)" -eq "True" -or "$($p.Enabled)" -eq "1") { 1 } else { 0 }
    Sample("cm_fw_profile{profile=`"$($p.Name)`",enabled=`"$on`",inbound=`"$in`",outbound=`"$out`"} $on")
}
Head "cm_fw_rule" "Enabled firewall rules (all Block + inbound Allow), 1 = custom rule (no group)"
try {
    $ports = @{}; Get-NetFirewallPortFilter -All | ForEach-Object { $ports[$_.InstanceID] = $_ }
    $addrs = @{}; Get-NetFirewallAddressFilter -All | ForEach-Object { $addrs[$_.InstanceID] = $_ }
    $apps = @{}; Get-NetFirewallApplicationFilter -All | ForEach-Object { $apps[$_.InstanceID] = $_ }
    $rules = @(Get-NetFirewallRule -Enabled True | Where-Object { "$($_.Action)" -eq "Block" -or "$($_.Direction)" -eq "Inbound" } |
               Sort-Object @{ e = { if ("$($_.Action)" -eq "Block") { 0 } elseif (-not $_.DisplayGroup) { 1 } else { 2 } } }, DisplayName |
               Select-Object -First 400)
    foreach ($r in $rules) {
        $pf = $ports[$r.InstanceID]; $af = $addrs[$r.InstanceID]; $ap = $apps[$r.InstanceID]
        $proto = if ($pf) { "$($pf.Protocol)" } else { "Any" }
        $port = if ($pf) { (@($pf.LocalPort) -join ",") } else { "Any" }
        if ("$($r.Direction)" -eq "Outbound" -and $pf) { $port = (@($pf.RemotePort) -join ",") }
        $remote = if ($af) { (@($af.RemoteAddress) -join ",") } else { "Any" }
        $prog = if ($ap -and $ap.Program -and $ap.Program -ne "Any") { Split-Path $ap.Program -Leaf } else { "" }
        $dir = if ("$($r.Direction)" -eq "Inbound") { "in" } else { "out" }
        Sample("cm_fw_rule{name=`"$(Esc $r.DisplayName)`",dir=`"$dir`",action=`"$($r.Action)`",proto=`"$(Esc $proto)`",port=`"$(Esc $port)`",remote=`"$(Esc $remote)`",program=`"$(Esc $prog)`"} $(if ($r.DisplayGroup) { 0 } else { 1 })")
    }
} catch { }

# ---------------- logon (Security log: ต้องเปิด Audit Logon ซึ่ง Windows Server เปิดไว้เป็นปกติ)
$fail = @(Get-WinEvent -LogName Security -FilterXPath "*[System[EventID=4625 and TimeCreated[timediff(@SystemTime) <= 86400000]]]" -MaxEvents 5000)
Head "cm_login_failed_24h" "Failed logons (Security 4625) in the last 24 hours"
Sample("cm_login_failed_24h $($fail.Count)")
Head "cm_login_failed_by" "Failed logons in 24h by source IP and user (top 20)"
$by = @{}
foreach ($e in $fail) {
    $x = [xml]$e.ToXml(); $d = @{}; $x.Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }
    $k = "$($d['IpAddress'])|$($d['TargetUserName'])"
    $by[$k] = 1 + [int]$by[$k]
}
$by.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 20 | ForEach-Object {
    $f = $_.Key -split '\|', 2
    Sample("cm_login_failed_by{ip=`"$(Esc $f[0])`",user=`"$(Esc $f[1])`"} $($_.Value)")
}
Head "cm_login_ok" "Recent console / RDP logons (value = unix time)"
$ok = @(Get-WinEvent -LogName Security -FilterXPath "*[System[EventID=4624 and TimeCreated[timediff(@SystemTime) <= 604800000]] and EventData[Data[@Name='LogonType']='10' or Data[@Name='LogonType']='2']]" -MaxEvents 20)
$n = 0
foreach ($e in $ok) {
    $x = [xml]$e.ToXml(); $d = @{}; $x.Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }
    $type = if ($d['LogonType'] -eq '10') { "RDP" } else { "Console" }
    Sample("cm_login_ok{n=`"$n`",user=`"$(Esc "$($d['TargetDomainName'])\$($d['TargetUserName'])")`",ip=`"$(Esc $d['IpAddress'])`",type=`"$type`"} $([int64]([DateTimeOffset]$e.TimeCreated).ToUnixTimeSeconds())")
    $n++
}

$out = Join-Path $TextDir "cm_netsec.prom"
$tmp = "$out.tmp"
[IO.File]::WriteAllText($tmp, (($L -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))
Move-Item $tmp $out -Force
