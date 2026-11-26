# =====================================================================
# cm-logs.ps1 (Central Monitor cm-agent, pull mode) - keep the latest ERROR / WARN of this machine
# for central to READ (central -> windows_exporter :9182, this machine cannot reach central).
# Scheduled task "CentralMonitorLogs" (SYSTEM, low priority) every EventInterval (min 1 minute):
#   - Event Log channels in cm-logs.json: Critical / Error / Warning newer than last time
#   - log files in cm-logs.json (wildcards): new lines with error / fatal / critical / exception / warn
# -> textfile of windows_exporter: cm_log_entry{seq,type,body} = time of the entry
# central (puller agent_bridge.py) keeps only seq it has not seen. Keeps KeepHours / MaxEntries.
# ASCII only.
# =====================================================================
$ErrorActionPreference = "SilentlyContinue"
$KeepHours  = 3
$MaxEntries = 1000
$MaxBody    = 600
$Dir      = Join-Path $env:ProgramData "central-monitor-agent"
$TextDir  = Join-Path $Dir "textfile"
$StateF   = Join-Path $Dir "cm-logs-state.json"
$ConfF    = Join-Path $Dir "cm-logs.json"
New-Item -ItemType Directory -Force -Path $TextDir | Out-Null

$conf = @{ channels = @("System", "Application"); logs = @() }
if (Test-Path $ConfF) { try { $c = Get-Content $ConfF -Raw | ConvertFrom-Json; if ($c.channels) { $conf.channels = @($c.channels) }; if ($c.logs) { $conf.logs = @($c.logs) } } catch { } }

$state = @{ seq = 0; events = @{}; files = @{}; entries = @() }
if (Test-Path $StateF) {
    try {
        $s = Get-Content $StateF -Raw | ConvertFrom-Json
        $state.seq = [int64]$s.seq
        if ($s.events) { $s.events.PSObject.Properties | ForEach-Object { $state.events[$_.Name] = [int64]$_.Value } }
        if ($s.files)  { $s.files.PSObject.Properties  | ForEach-Object { $state.files[$_.Name]  = [int64]$_.Value } }
        if ($s.entries) { $state.entries = @($s.entries) }
    } catch { }
}

function One($s) { return ([string]$s -replace "[\r\n\t]+", " ").Trim() }
function Add-Entry([datetime] $t, [string] $type, [string] $body) {
    $state.seq++
    $b = One $body
    if ($b.Length -gt $MaxBody) { $b = $b.Substring(0, $MaxBody) }
    $script:state.entries += [pscustomobject]@{ seq = $state.seq; ts = [int64]([DateTimeOffset]$t).ToUnixTimeSeconds(); type = $type; body = $b }
}
$now = Get-Date

# ---------------- Event Log: Critical (1) / Error (2) / Warning (3) newer than last time
foreach ($ch in $conf.channels) {
    $last = if ($state.events.ContainsKey($ch)) { $state.events[$ch] } else { -1 }
    $xp = if ($last -ge 0) { "*[System[(Level=1 or Level=2 or Level=3) and EventRecordID > $last]]" }
          else { "*[System[(Level=1 or Level=2 or Level=3) and TimeCreated[timediff(@SystemTime) <= 3600000]]]" }
    $ev = @(Get-WinEvent -LogName $ch -FilterXPath $xp -MaxEvents 300 -ErrorAction SilentlyContinue | Sort-Object RecordId)
    if ($last -lt 0) {
        # first run: remember the newest record of the channel (even without errors) and continue from there
        $top = Get-WinEvent -LogName $ch -MaxEvents 1 -ErrorAction SilentlyContinue
        if ($top) { $state.events[$ch] = [int64]$top.RecordId }
    }
    foreach ($e in $ev) {
        $lv = if ($e.Level -le 2) { "ERROR" } else { "WARN" }
        $msg = $e.Message; if (-not $msg) { $msg = "(no message)" }
        $iso = $e.TimeCreated.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        Add-Entry $e.TimeCreated "eventlog" "$iso [$lv] [$ch] $($e.ProviderName) (Event $($e.Id)): $msg"
        if ([int64]$e.RecordId -gt [int64]$state.events[$ch]) { $state.events[$ch] = [int64]$e.RecordId }
    }
}

# ---------------- log files: read the new part, ERROR / WARN lines only
$errRx  = '(?i)\b(error|fatal|critical|crit|exception|failed|failure|panic)\b'
$warnRx = '(?i)\b(warn|warning)\b'
$recent = (Get-Date).AddDays(-2)
foreach ($pat in $conf.logs) {
    # only files written in the last 2 days (folders with hundreds of old files are not re-read)
    foreach ($f in @(Get-ChildItem -Path $pat -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt $recent })) {
        $key = $f.FullName
        $off = if ($state.files.ContainsKey($key)) { $state.files[$key] } else { [math]::Max(0, $f.Length - 65536) }
        if ($f.Length -lt $off) { $off = 0 }                       # rotated / truncated -> read again
        if ($f.Length -eq $off) { continue }
        try {
            $fs = [IO.File]::Open($f.FullName, 'Open', 'Read', 'ReadWrite, Delete')
            $fs.Seek($off, 'Begin') | Out-Null
            $max = [math]::Min($f.Length - $off, 4MB)
            $buf = New-Object byte[] $max
            $n = $fs.Read($buf, 0, $max); $fs.Close()
            $text = [Text.Encoding]::UTF8.GetString($buf, 0, $n)
            $cut = $text.LastIndexOf("`n")
            if ($cut -lt 0) { continue }                            # no complete line yet
            $text = $text.Substring(0, $cut + 1)
            $state.files[$key] = $off + [Text.Encoding]::UTF8.GetByteCount($text)
            $count = 0
            foreach ($line in ($text -split "`r?`n")) {
                if (-not $line.Trim()) { continue }
                $lv = if ($line -match $errRx) { "ERROR" } elseif ($line -match $warnRx) { "WARN" } else { $null }
                if (-not $lv) { continue }
                if (++$count -gt 300) { break }
                $iso = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
                Add-Entry (Get-Date) "application" "$iso [$lv] [$($f.Name)] $line"
            }
        } catch { }
    }
}

foreach ($k in @($state.files.Keys)) { if (-not (Test-Path -LiteralPath $k) -or (Get-Item -LiteralPath $k).LastWriteTime -lt $recent.AddDays(-5)) { $state.files.Remove($k) } }

# ---------------- drop old entries + write the textfile (each metric must be contiguous)
$cutoff = [int64]([DateTimeOffset]$now.AddHours(-$KeepHours)).ToUnixTimeSeconds()
$keep = @($state.entries | Where-Object { [int64]$_.ts -ge $cutoff -or [int64]$_.seq -gt ($state.seq - 50) })
if ($keep.Count -gt $MaxEntries) { $keep = @($keep | Select-Object -Last $MaxEntries) }
$state.entries = $keep

function Esc($s) { return ([string]$s).Replace('\', '\\').Replace('"', '\"') }
$L = New-Object System.Collections.Generic.List[string]
$L.Add("# HELP cm_log_entry Recent ERROR/WARN log entries (pull mode, read by central puller)")
$L.Add("# TYPE cm_log_entry gauge")
foreach ($e in $keep) { $L.Add("cm_log_entry{seq=`"$($e.seq)`",type=`"$($e.type)`",body=`"$(Esc $e.body)`"} $($e.ts)") }
$L.Add("# HELP cm_log_seq Last cm_log_entry sequence number")
$L.Add("# TYPE cm_log_seq gauge")
$L.Add("cm_log_seq $($state.seq)")
$out = Join-Path $TextDir "cm_logs.prom"
$tmp = "$out.tmp"
[IO.File]::WriteAllText($tmp, (($L -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))
Move-Item $tmp $out -Force

$save = [pscustomobject]@{ seq = $state.seq; events = [pscustomobject]$state.events; files = [pscustomobject]$state.files; entries = $state.entries }
[IO.File]::WriteAllText($StateF, ($save | ConvertTo-Json -Depth 4 -Compress), (New-Object Text.UTF8Encoding($false)))
