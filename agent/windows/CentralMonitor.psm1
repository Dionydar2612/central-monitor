# =====================================================================
# CentralMonitor.psm1 - Central Monitor (agentless Windows monitoring)
#
# Read-only functions exposed through the JEA endpoint "CentralMonitor".
# Central (puller) connects over WinRM HTTPS as the local user cm-monitor
# (not an administrator) and may ONLY call the Get-CM* functions below.
#
# Rules this module follows (do not break them when editing):
#   - never writes anything to disk, never starts a process, never changes settings
#   - no WMI/CIM except the hotfix list (a non-admin JEA session is often refused by WMI):
#     RAM from .NET ComputerInfo (GlobalMemoryStatusEx), OS / hardware from the registry
#   - fast functions (metrics / services) do not use WMI/CIM
#     (.NET PerformanceCounter created once per session + Service Control Manager)
#   - log files are opened with FileShare ReadWrite + Delete (the owner can
#     rotate / delete them while we read) and closed right away
#   - log files are readable only when they match allowed-paths.txt
#     (editable by local administrators only)
#   - the session lowers its own priority to BelowNormal
#   - every result is ONE compact JSON string
#   - JEA (RestrictedRemoteServer) replaces Select-Object / Measure-Object / Get-Command / Get-Help /
#     Out-Default / Get-FormatData with limited proxies (no -First ...): call the real cmdlet by its
#     module-qualified name, e.g. Microsoft.PowerShell.Utility\Select-Object
#
# ASCII only: Windows PowerShell 5.1 reads files without a BOM as ANSI.
# =====================================================================
Set-StrictMode -Version 2.0

$script:CMVersion   = '1.1.2'
$script:DataDir     = Join-Path $env:ProgramData 'CentralMonitor'
$script:AllowFile   = Join-Path $script:DataDir 'allowed-paths.txt'
$script:MaxText     = 2048          # max characters per event message
$script:Counters    = $null         # list of counter entries (created once, refreshed every 10 min)
$script:CountersAt  = [datetime]::MinValue
$script:TotalRamMB  = 0
$script:RamTriedAt  = [datetime]::MinValue
$script:Self        = $null
$script:StartedAt   = [datetime]::UtcNow
$script:ProcPrev    = $null         # previous process CPU sample (Get-CMProcesses)

# ---------------------------------------------------------------- helpers (not visible to the caller)
function Initialize-CMSession {
    if ($null -ne $script:Self) { return }
    $script:Self = [System.Diagnostics.Process]::GetCurrentProcess()
    try { $script:Self.PriorityClass = [System.Diagnostics.ProcessPriorityClass]::BelowNormal } catch { }
}

function Get-CMRegValue([string]$Key, [string]$Name) {
    # HKLM read with .NET (no registry provider needed inside the JEA session)
    try {
        $k = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($Key)
        if ($null -eq $k) { return $null }
        try { return $k.GetValue($Name) } finally { $k.Close() }
    } catch { return $null }
}

function Get-CMTotalRamMB {
    # .NET ComputerInfo = GlobalMemoryStatusEx (no WMI). CIM only as a fallback. Retried every 10 min if both fail.
    if ($script:TotalRamMB -gt 0) { return $script:TotalRamMB }
    if (([datetime]::UtcNow - $script:RamTriedAt).TotalMinutes -lt 10) { return $script:TotalRamMB }
    $script:RamTriedAt = [datetime]::UtcNow
    try {
        [void][System.Reflection.Assembly]::Load('Microsoft.VisualBasic, Version=10.0.0.0, Culture=neutral, PublicKeyToken=b03f5f7f11d50a3a')
        $ci = New-Object Microsoft.VisualBasic.Devices.ComputerInfo
        $script:TotalRamMB = [Math]::Round([double]$ci.TotalPhysicalMemory / 1MB, 0)
    } catch {
        try {
            $cs = Get-CimInstance -ClassName Win32_ComputerSystem -Property TotalPhysicalMemory -ErrorAction Stop
            $script:TotalRamMB = [Math]::Round($cs.TotalPhysicalMemory / 1MB, 0)
        } catch { $script:TotalRamMB = -1 }
    }
    return $script:TotalRamMB
}

function ConvertTo-CMJson($Object) {
    return (ConvertTo-Json -InputObject $Object -Depth 6 -Compress)
}

function Get-CMUnixTime {
    return [long][Math]::Floor(([datetime]::UtcNow - [datetime]'1970-01-01').TotalSeconds)
}

function Get-CMSelf {
    Initialize-CMSession
    $script:Self.Refresh()
    return [ordered]@{
        pid      = $script:Self.Id
        ws_mb    = [Math]::Round($script:Self.WorkingSet64 / 1MB, 1)
        cpu_s    = [Math]::Round($script:Self.TotalProcessorTime.TotalSeconds, 3)
        age_s    = [long]([datetime]::UtcNow - $script:StartedAt).TotalSeconds
        priority = [string]$script:Self.PriorityClass
    }
}

function New-CMCounter([string]$Category, [string]$Name, [string]$Instance) {
    try {
        $c = New-Object System.Diagnostics.PerformanceCounter($Category, $Name, $Instance, $true)
        [void]$c.NextValue()   # first read primes rate counters
        return $c
    } catch {
        return $null
    }
}

function Get-CMInstances([string]$Category) {
    try {
        $cat = New-Object System.Diagnostics.PerformanceCounterCategory($Category)
        return @($cat.GetInstanceNames() | Where-Object { $_ -ne '_Total' } | Sort-Object)
    } catch {
        return @()
    }
}

function Get-CMCounterSet {
    $now = [datetime]::UtcNow
    if ($null -ne $script:Counters -and ($now - $script:CountersAt).TotalMinutes -lt 10) {
        return $script:Counters
    }
    if ($null -ne $script:Counters) {
        foreach ($e in $script:Counters) { try { $e.C.Dispose() } catch { } }
    }
    $list = New-Object System.Collections.ArrayList

    # key, category, counter, instance
    $global = @(
        @('cpu_pct',        'Processor', '% Processor Time',            '_Total'),
        @('cpu_user_pct',   'Processor', '% User Time',                 '_Total'),
        @('cpu_priv_pct',   'Processor', '% Privileged Time',           '_Total'),
        @('ctx_switch_s',   'System',    'Context Switches/sec',        ''),
        @('processes',      'System',    'Processes',                   ''),
        @('mem_committed_b','Memory',    'Committed Bytes',             ''),
        @('mem_limit_b',    'Memory',    'Commit Limit',                ''),
        @('mem_cache_b',    'Memory',    'Cache Bytes',                 ''),
        @('mem_pagein_s',   'Memory',    'Pages Input/sec',             ''),
        @('mem_pageout_s',  'Memory',    'Pages Output/sec',            ''),
        @('mem_fault_s',    'Memory',    'Page Reads/sec',              ''),
        @('tcp_estab',      'TCPv4',     'Connections Established',     ''),
        @('tcp_fail',       'TCPv4',     'Connection Failures',         ''),
        @('tcp_reset',      'TCPv4',     'Connections Reset',           ''),
        @('cpu_queue',      'System',    'Processor Queue Length',      ''),
        @('uptime_s',       'System',    'System Up Time',              ''),
        @('mem_avail_mb',   'Memory',    'Available MBytes',            ''),
        @('mem_commit_pct', 'Memory',    '% Committed Bytes In Use',    ''),
        @('mem_pages_s',    'Memory',    'Pages/sec',                   ''),
        @('tcp_retrans_s',  'TCPv4',     'Segments Retransmitted/sec',  ''),
        @('tcp_sent_s',     'TCPv4',     'Segments Sent/sec',           '')
    )
    foreach ($d in $global) {
        $c = New-CMCounter $d[1] $d[2] $d[3]
        if ($null -ne $c) { [void]$list.Add([pscustomobject]@{ K = $d[0]; I = ''; C = $c }) }
    }

    foreach ($inst in (Get-CMInstances 'Processor')) {
        $c = New-CMCounter 'Processor' '% Processor Time' $inst
        if ($null -ne $c) { [void]$list.Add([pscustomobject]@{ K = 'core_pct'; I = $inst; C = $c }) }
    }

    $disk = @(
        @('disk_read_lat_s',  'Avg. Disk sec/Read'),
        @('disk_write_lat_s', 'Avg. Disk sec/Write'),
        @('disk_queue',       'Current Disk Queue Length'),
        @('disk_idle_pct',    '% Idle Time'),
        @('disk_reads_s',     'Disk Reads/sec'),
        @('disk_writes_s',    'Disk Writes/sec'),
        @('disk_read_bps',    'Disk Read Bytes/sec'),
        @('disk_write_bps',   'Disk Write Bytes/sec'),
        @('disk_avg_queue',   'Avg. Disk Queue Length')
    )
    foreach ($inst in (Get-CMInstances 'PhysicalDisk')) {
        foreach ($d in $disk) {
            $c = New-CMCounter 'PhysicalDisk' $d[1] $inst
            if ($null -ne $c) { [void]$list.Add([pscustomobject]@{ K = $d[0]; I = $inst; C = $c }) }
        }
    }

    # error / discard counters are running totals (central computes the rate)
    $net = @(
        @('net_rx_bps',       'Bytes Received/sec'),
        @('net_tx_bps',       'Bytes Sent/sec'),
        @('net_bw_bps',       'Current Bandwidth'),
        @('net_rx_err',       'Packets Received Errors'),
        @('net_tx_err',       'Packets Outbound Errors'),
        @('net_rx_discard',   'Packets Received Discarded'),
        @('net_tx_discard',   'Packets Outbound Discarded')
    )
    foreach ($inst in (Get-CMInstances 'Network Interface')) {
        foreach ($d in $net) {
            $c = New-CMCounter 'Network Interface' $d[1] $inst
            if ($null -ne $c) { [void]$list.Add([pscustomobject]@{ K = $d[0]; I = $inst; C = $c }) }
        }
    }

    $script:Counters = $list
    $script:CountersAt = $now
    return $list
}

function Get-CMAllowList {
    if (-not (Test-Path -LiteralPath $script:AllowFile)) { return @() }
    $out = New-Object System.Collections.ArrayList
    foreach ($line in [System.IO.File]::ReadAllLines($script:AllowFile)) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        [void]$out.Add($t)
    }
    return @($out)
}

function Test-CMNoReparse([string]$FullPath) {
    # reject symlinks / junctions anywhere on the path (no escaping the allow list)
    $p = $FullPath
    while ($p -and $p.Length -gt 3) {
        try {
            $attr = [System.IO.File]::GetAttributes($p)
            if (($attr -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return $false }
        } catch {
            return $false
        }
        $p = [System.IO.Path]::GetDirectoryName($p)
    }
    return $true
}

function Resolve-CMAllowedPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'Path is empty.' }
    if ($Path.Contains('..') -or $Path.StartsWith('\\') -or $Path.Contains('*') -or $Path.Contains('?')) {
        throw 'Path is not allowed.'
    }
    if ($Path -notmatch '^[A-Za-z]:\\[^:]+$') { throw 'Path is not allowed.' }   # local drive, no alternate data streams
    $full = [System.IO.Path]::GetFullPath($Path)
    $ok = $false
    foreach ($rule in (Get-CMAllowList)) {
        if ($rule.EndsWith('\')) {
            if ($full.StartsWith($rule, [System.StringComparison]::OrdinalIgnoreCase)) { $ok = $true; break }
        } elseif ($full -like $rule) {
            $ok = $true; break
        }
    }
    if (-not $ok) { throw 'Path is not in allowed-paths.txt on this machine.' }
    if (-not (Test-CMNoReparse $full)) { throw 'Path goes through a link / junction.' }
    return $full
}

function Get-CMFingerprint([System.IO.FileStream]$Stream) {
    # SHA1 of the first 1 KB: changes when the file is replaced (rotation) even if the new file is bigger
    $n = [int][Math]::Min(1024, $Stream.Length)
    if ($n -le 0) { return '' }
    $buf = New-Object byte[] $n
    [void]$Stream.Seek(0, [System.IO.SeekOrigin]::Begin)
    $read = 0
    while ($read -lt $n) {
        $r = $Stream.Read($buf, $read, $n - $read)
        if ($r -le 0) { break }
        $read += $r
    }
    $sha = [System.Security.Cryptography.SHA1]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($buf, 0, $read))).Replace('-', '') }
    finally { $sha.Dispose() }
}

# ---------------------------------------------------------------- visible functions
function Get-CMVersion {
    [CmdletBinding()] param()
    Initialize-CMSession
    return (ConvertTo-CMJson ([ordered]@{
        v      = $script:CMVersion
        host   = $env:COMPUTERNAME
        ps     = $PSVersionTable.PSVersion.ToString()
        ts     = (Get-CMUnixTime)
        self   = (Get-CMSelf)
    }))
}

function Get-CMMetrics {
    [CmdletBinding()] param()
    Initialize-CMSession
    [void](Get-CMTotalRamMB)

    $values = New-Object System.Collections.ArrayList
    foreach ($e in (Get-CMCounterSet)) {
        try {
            $v = $e.C.NextValue()
            [void]$values.Add([ordered]@{ k = $e.K; i = $e.I; v = [Math]::Round([double]$v, 4) })
        } catch { }
    }

    $drives = New-Object System.Collections.ArrayList
    foreach ($d in [System.IO.DriveInfo]::GetDrives()) {
        try {
            if ($d.DriveType -ne [System.IO.DriveType]::Fixed -or -not $d.IsReady) { continue }
            [void]$drives.Add([ordered]@{
                drive   = $d.Name.TrimEnd('\')
                label   = $d.VolumeLabel
                size_gb = [Math]::Round($d.TotalSize / 1GB, 2)
                free_gb = [Math]::Round($d.AvailableFreeSpace / 1GB, 2)
            })
        } catch { }
    }

    return (ConvertTo-CMJson ([ordered]@{
        v            = $script:CMVersion
        host         = $env:COMPUTERNAME
        ts           = (Get-CMUnixTime)
        mem_total_mb = $script:TotalRamMB
        values       = $values.ToArray()
        drives       = $drives.ToArray()
        self         = (Get-CMSelf)
    }))
}

function Get-CMServices {
    [CmdletBinding()]
    param(
        [ValidateCount(0, 200)]
        [string[]]$Name,
        [switch]$All                      # every service (Settings: choose services to watch)
    )
    Initialize-CMSession
    $out = New-Object System.Collections.ArrayList
    if ($null -eq $Name -or $Name.Count -eq 0) {
        # default: services set to start automatically (a stopped one is a problem)
        foreach ($s in @(Get-Service -ErrorAction SilentlyContinue)) {
            try { $st = [string]$s.StartType } catch { $st = '' }
            if (-not $All -and $st -ne 'Automatic') { continue }
            [void]$out.Add([ordered]@{ name = $s.Name; display = $s.DisplayName; status = [string]$s.Status; start = $st })
        }
    } else {
        foreach ($n in $Name) {
            if ([string]::IsNullOrWhiteSpace($n) -or $n.Length -gt 256 -or $n.Contains('*') -or $n.Contains('?') -or $n.Contains('[')) {
                [void]$out.Add([ordered]@{ name = $n; display = ''; status = 'Invalid'; start = '' })
                continue
            }
            $s = Get-Service -Name $n -ErrorAction SilentlyContinue
            if ($null -eq $s) {
                [void]$out.Add([ordered]@{ name = $n; display = ''; status = 'NotFound'; start = '' })
                continue
            }
            try { $st = [string]$s.StartType } catch { $st = '' }
            [void]$out.Add([ordered]@{ name = $s.Name; display = $s.DisplayName; status = [string]$s.Status; start = $st })
        }
    }
    return (ConvertTo-CMJson ([ordered]@{
        v = $script:CMVersion; host = $env:COMPUTERNAME; ts = (Get-CMUnixTime); services = $out.ToArray()
    }))
}

function Get-CMEvents {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[A-Za-z0-9 ._/\-]{1,200}$')]
        [string]$LogName,
        [long]$AfterRecordId = -1,       # -1 = first call: return only the current newest record id (no history)
        [ValidateRange(1, 500)]
        [int]$MaxEvents = 200,
        [ValidateRange(1, 5)]
        [int]$MaxLevel = 3               # 1 Critical, 2 Error, 3 Warning, 4 Information
    )
    Initialize-CMSession
    $newest = 0
    try {
        $last = Get-WinEvent -LogName $LogName -MaxEvents 1 -ErrorAction Stop
        if ($null -ne $last) { $newest = [long]$last.RecordId }
    } catch {
        if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { throw }
    }

    $events = New-Object System.Collections.ArrayList
    if ($AfterRecordId -ge 0 -and $newest -gt $AfterRecordId) {
        $levels = (1..$MaxLevel | ForEach-Object { "Level=$_" }) -join ' or '
        $xp = "*[System[($levels) and (EventRecordID > $AfterRecordId)]]"
        $found = @()
        try {
            $found = @(Get-WinEvent -LogName $LogName -FilterXPath $xp -Oldest -MaxEvents $MaxEvents -ErrorAction Stop)
        } catch {
            if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { throw }
        }
        foreach ($e in $found) {
            $msg = ''
            try { $msg = [string]$e.Message } catch { }
            if ([string]::IsNullOrEmpty($msg)) { $msg = '(no message text)' }
            if ($msg.Length -gt $script:MaxText) { $msg = $msg.Substring(0, $script:MaxText) + ' ...' }
            [void]$events.Add([ordered]@{
                rid      = [long]$e.RecordId
                time     = $e.TimeCreated.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
                level    = [int]$e.Level
                id       = [int]$e.Id
                provider = [string]$e.ProviderName
                msg      = $msg
            })
        }
    }

    return (ConvertTo-CMJson ([ordered]@{
        v      = $script:CMVersion
        host   = $env:COMPUTERNAME
        ts     = (Get-CMUnixTime)
        log    = $LogName
        newest = $newest              # newest < AfterRecordId = log was cleared -> central restarts from newest
        more   = ($events.Count -ge $MaxEvents)
        events = $events.ToArray()
    }))
}

function Get-CMLogTail {
    [CmdletBinding(DefaultParameterSetName = 'Tail')]
    param(
        [Parameter(ParameterSetName = 'List', Mandatory = $true)]
        [switch]$List,                   # list files matching allowed-paths.txt changed in the last -Days days

        [Parameter(ParameterSetName = 'List')]
        [ValidateRange(1, 30)]
        [int]$Days = 2,

        [Parameter(ParameterSetName = 'Tail', Mandatory = $true)]
        [ValidateLength(4, 400)]
        [string]$Path,

        [Parameter(ParameterSetName = 'Tail')]
        [long]$Offset = -1,              # -1 = first call: start at the end of the file (no history)

        [Parameter(ParameterSetName = 'Tail')]
        [ValidateRange(1024, 1048576)]
        [int]$MaxBytes = 262144,

        [Parameter(ParameterSetName = 'Tail')]
        [ValidateSet('utf8', 'unicode', 'default')]
        [string]$Encoding = 'utf8',

        [Parameter(ParameterSetName = 'Tail')]
        [string]$Fingerprint = ''        # fingerprint from the previous call (rotation check)
    )
    Initialize-CMSession

    if ($PSCmdlet.ParameterSetName -eq 'List') {
        $since = [datetime]::Now.AddDays(-$Days)
        $files = New-Object System.Collections.ArrayList
        foreach ($rule in (Get-CMAllowList)) {
            if ($files.Count -ge 200) { break }
            if ($rule.EndsWith('\')) { $dir = $rule; $filter = '*' }
            else { $dir = [System.IO.Path]::GetDirectoryName($rule); $filter = [System.IO.Path]::GetFileName($rule) }
            if (-not $dir -or $dir.Contains('*') -or $dir.Contains('?')) { continue }   # wildcards only in the file name
            if (-not [System.IO.Directory]::Exists($dir) -or -not (Test-CMNoReparse $dir)) { continue }
            try {
                foreach ($f in ([System.IO.DirectoryInfo]$dir).EnumerateFiles($filter)) {   # this folder only, not recursive
                    if ($files.Count -ge 200) { break }
                    if ($f.LastWriteTime -lt $since) { continue }
                    [void]$files.Add([ordered]@{
                        path  = $f.FullName
                        size  = [long]$f.Length
                        mtime = $f.LastWriteTimeUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')
                    })
                }
            } catch { }
        }
        return (ConvertTo-CMJson ([ordered]@{
            v = $script:CMVersion; host = $env:COMPUTERNAME; ts = (Get-CMUnixTime); files = $files.ToArray()
        }))
    }

    $full = Resolve-CMAllowedPath $Path
    $share = [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
    $fs = New-Object System.IO.FileStream($full, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, $share, 4096)
    try {
        $len = [long]$fs.Length
        $fp = Get-CMFingerprint $fs
        $rotated = $false
        $start = $Offset
        if ($Offset -lt 0) {
            $start = $len
        } elseif ($Offset -gt $len -or ($Fingerprint -ne '' -and $Offset -ge 1024 -and $Fingerprint -ne $fp)) {
            $start = 0; $rotated = $true
        }
        $n = [int][Math]::Min([long]$MaxBytes, $len - $start)
        $used = 0
        $text = ''
        if ($n -gt 0) {
            $buf = New-Object byte[] $n
            [void]$fs.Seek($start, [System.IO.SeekOrigin]::Begin)
            $read = 0
            while ($read -lt $n) {
                $r = $fs.Read($buf, $read, $n - $read)
                if ($r -le 0) { break }
                $read += $r
            }
            # stop after the last complete line (the rest is read next time)
            $cut = -1
            if ($read -gt 0) { $cut = [Array]::LastIndexOf($buf, [byte]10, $read - 1) }
            if ($cut -ge 0) {
                $used = $cut + 1
                if ($Encoding -eq 'unicode' -and $used -lt $read) { $used += 1 }   # UTF-16LE: 0A 00
            } elseif ($read -ge $MaxBytes) {
                $used = $read                                                      # one huge line: take it
            }
            switch ($Encoding) {
                'unicode' { $enc = [System.Text.Encoding]::Unicode }
                'default' { $enc = [System.Text.Encoding]::Default }
                default   { $enc = New-Object System.Text.UTF8Encoding($false) }
            }
            if ($used -gt 0) { $text = $enc.GetString($buf, 0, $used) }
        }
        return (ConvertTo-CMJson ([ordered]@{
            v           = $script:CMVersion
            host        = $env:COMPUTERNAME
            ts          = (Get-CMUnixTime)
            path        = $full
            size        = $len
            start       = $start
            next_offset = $start + $used
            rotated     = $rotated
            fingerprint = $fp
            more        = (($start + $used) -lt $len)
            text        = $text
        }))
    } finally {
        $fs.Dispose()
    }
}

function Get-CMProcesses {
    # top processes by CPU (over -SampleSeconds) and by RAM. Get-Process only (no WMI).
    [CmdletBinding()]
    param(
        [ValidateRange(1, 50)]
        [int]$Top = 10,
        [ValidateRange(1, 5)]
        [int]$SampleSeconds = 1
    )
    Initialize-CMSession
    $cores = [Environment]::ProcessorCount
    $first = @{}
    foreach ($p in @(Get-Process -ErrorAction SilentlyContinue)) {
        try { $first[$p.Id] = $p.TotalProcessorTime.TotalSeconds } catch { }
    }
    $t0 = [datetime]::UtcNow
    Start-Sleep -Seconds $SampleSeconds
    $span = ([datetime]::UtcNow - $t0).TotalSeconds
    $rows = New-Object System.Collections.ArrayList
    $totalRam = 0
    foreach ($p in @(Get-Process -ErrorAction SilentlyContinue)) {
        $cpu = 0.0
        try {
            if ($first.ContainsKey($p.Id)) { $cpu = ($p.TotalProcessorTime.TotalSeconds - $first[$p.Id]) / $span / $cores * 100 }
        } catch { }
        $ws = [long]$p.WorkingSet64
        $totalRam += $ws
        $th = 0
        try { $th = $p.Threads.Count } catch { }
        [void]$rows.Add([pscustomobject]@{ pid = $p.Id; name = $p.ProcessName; cpu = [Math]::Round([Math]::Max(0, $cpu), 2); ws_mb = [Math]::Round($ws / 1MB, 1); threads = $th })
    }
    $byCpu = @($rows | Sort-Object cpu -Descending | Microsoft.PowerShell.Utility\Select-Object -First $Top)
    $byRam = @($rows | Sort-Object ws_mb -Descending | Microsoft.PowerShell.Utility\Select-Object -First $Top)
    return (ConvertTo-CMJson ([ordered]@{
        v = $script:CMVersion; host = $env:COMPUTERNAME; ts = (Get-CMUnixTime)
        cores = $cores; count = $rows.Count; interval = [Math]::Round($span, 2)
        top_cpu = $byCpu; top_ram = $byRam; self_pid = $script:Self.Id
    }))
}

function Get-CMInventory {
    [CmdletBinding()] param()
    Initialize-CMSession
    $inv = [ordered]@{ v = $script:CMVersion; host = $env:COMPUTERNAME; ts = (Get-CMUnixTime) }
    # registry + .NET (no WMI)
    $cv = 'SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    try {
        $build = [string](Get-CMRegValue $cv 'CurrentBuild')
        $ubr = Get-CMRegValue $cv 'UBR'
        $ver = [Environment]::OSVersion.Version
        $boot = $null
        try {
            $up = New-Object System.Diagnostics.PerformanceCounter('System', 'System Up Time', '', $true)
            [void]$up.NextValue(); $sec = $up.NextValue(); $up.Dispose()
            $boot = [datetime]::UtcNow.AddSeconds(-$sec).ToString('yyyy-MM-ddTHH:mm:ssZ')
        } catch { }
        $inst = Get-CMRegValue $cv 'InstallDate'
        $inv.os = [ordered]@{
            caption   = [string](Get-CMRegValue $cv 'ProductName')
            edition   = [string](Get-CMRegValue $cv 'EditionID')
            release   = [string](Get-CMRegValue $cv 'DisplayVersion')
            version   = ('{0}.{1}.{2}' -f $ver.Major, $ver.Minor, $build)
            build     = $(if ($null -ne $ubr) { $build + '.' + $ubr } else { $build })
            boot_time = $boot
            install   = $(if ($inst) { ([datetime]'1970-01-01').AddSeconds([double]$inst).ToString('yyyy-MM-ddTHH:mm:ssZ') } else { $null })
        }
    } catch { $inv.os = $null }
    try {
        $bios = 'HARDWARE\DESCRIPTION\System\BIOS'
        $dom = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().DomainName
        $inv.system = [ordered]@{
            manufacturer = [string](Get-CMRegValue $bios 'SystemManufacturer')
            model        = [string](Get-CMRegValue $bios 'SystemProductName')
            domain       = [string]$dom
            in_domain    = -not [string]::IsNullOrEmpty($dom)
            ram_mb       = (Get-CMTotalRamMB)
            logical_cpu  = [Environment]::ProcessorCount
        }
    } catch { $inv.system = $null }
    $cpuName = Get-CMRegValue 'HARDWARE\DESCRIPTION\System\CentralProcessor\0' 'ProcessorNameString'
    $inv.cpu = @($(if ($cpuName) { ([string]$cpuName).Trim() }))
    $nics = New-Object System.Collections.ArrayList
    try {
        foreach ($ni in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
            if ($ni.NetworkInterfaceType -eq [System.Net.NetworkInformation.NetworkInterfaceType]::Loopback) { continue }
            $ips = @($ni.GetIPProperties().UnicastAddresses | ForEach-Object { $_.Address.ToString() })
            [void]$nics.Add([ordered]@{ name = $ni.Name; status = [string]$ni.OperationalStatus; speed = [long]$ni.Speed; ips = $ips })
        }
    } catch { }
    $inv.nics = $nics.ToArray()
    try {
        $inv.hotfix = @(Get-HotFix -ErrorAction Stop | Where-Object { $_.InstalledOn } | Sort-Object InstalledOn -Descending |
            Microsoft.PowerShell.Utility\Select-Object -First 5 | ForEach-Object { [ordered]@{ id = $_.HotFixID; date = $_.InstalledOn.ToString('yyyy-MM-dd') } })
    } catch { $inv.hotfix = @() }
    $inv.ps = $PSVersionTable.PSVersion.ToString()
    $inv.tz = [System.TimeZoneInfo]::Local.Id
    $inv.self = (Get-CMSelf)
    return (ConvertTo-CMJson $inv)
}

Export-ModuleMember -Function Get-CMVersion, Get-CMMetrics, Get-CMServices, Get-CMEvents, Get-CMLogTail, Get-CMInventory, Get-CMProcesses
