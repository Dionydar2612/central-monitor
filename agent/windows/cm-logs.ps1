<#
  cm-logs.ps1 (Central Monitor) - เก็บ ERROR / WARN ล่าสุดของเครื่องนี้ไว้ให้ central "ดึง" ไป (โหมด pull)
  ---------------------------------------------------------------------------------------------
  ใช้เมื่อเครื่อง Windows ต่อไปหา central ไม่ได้ (firewall ระหว่างวง) แต่ central ต่อเข้ามาที่พอร์ต 9182 ได้
    scheduled task "CentralMonitorLogs" รันทุก 1 นาที (SYSTEM) -> อ่าน log ใหม่ตั้งแต่ครั้งก่อน
      - Event Log (channels ใน cm-logs.json) เฉพาะ Critical / Error / Warning
      - ไฟล์ log (paths ใน cm-logs.json, wildcard ได้) เฉพาะบรรทัดที่มีคำว่า error / fatal / critical / exception / warn
    -> เขียน textfile ของ windows_exporter: cm_log_entry{seq,type,body} = เวลาของ log
    -> central (puller) อ่านจาก http://<เครื่องนี้>:9182/metrics เอาเฉพาะ seq ที่ยังไม่เคยเห็น แล้วเก็บเข้า OpenSearch
       (Prometheus ทิ้ง cm_log_entry ไม่เก็บเป็น metric)
  เก็บย้อนหลังไม่เกิน KeepHours ชั่วโมง / MaxEntries รายการ (puller ดึงทุกรอบ ~1 ชม.)
  ติดตั้งโดย install-fluentbit.ps1 -Pull · ถอน: uninstall-fluentbit.ps1
#>
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

# ---------------- Event Log: Critical (1) / Error (2) / Warning (3) ที่ใหม่กว่าครั้งก่อน
foreach ($ch in $conf.channels) {
    $last = if ($state.events.ContainsKey($ch)) { $state.events[$ch] } else { -1 }
    $xp = if ($last -ge 0) { "*[System[(Level=1 or Level=2 or Level=3) and EventRecordID > $last]]" }
          else { "*[System[(Level=1 or Level=2 or Level=3) and TimeCreated[timediff(@SystemTime) <= 3600000]]]" }
    $ev = @(Get-WinEvent -LogName $ch -FilterXPath $xp -MaxEvents 300 -ErrorAction SilentlyContinue | Sort-Object RecordId)
    if ($last -lt 0) {
        # ครั้งแรก: จำตำแหน่งล่าสุดของ channel ไว้ (แม้ไม่มี error) ครั้งต่อไปอ่านต่อจากตรงนี้
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

# ---------------- ไฟล์ log: อ่านส่วนที่เพิ่มขึ้น เฉพาะบรรทัด ERROR / WARN
$errRx  = '(?i)\b(error|fatal|critical|crit|exception|failed|failure|panic)\b'
$warnRx = '(?i)\b(warn|warning)\b'
foreach ($pat in $conf.logs) {
    foreach ($f in @(Get-ChildItem -Path $pat -File -ErrorAction SilentlyContinue)) {
        $key = $f.FullName
        $off = if ($state.files.ContainsKey($key)) { $state.files[$key] } else { [math]::Max(0, $f.Length - 65536) }
        if ($f.Length -lt $off) { $off = 0 }                       # ไฟล์ถูกหมุน / ตัด -> อ่านใหม่
        if ($f.Length -eq $off) { continue }
        try {
            $fs = [IO.File]::Open($f.FullName, 'Open', 'Read', 'ReadWrite')
            $fs.Seek($off, 'Begin') | Out-Null
            $max = [math]::Min($f.Length - $off, 4MB)
            $buf = New-Object byte[] $max
            $n = $fs.Read($buf, 0, $max); $fs.Close()
            $text = [Text.Encoding]::UTF8.GetString($buf, 0, $n)
            $cut = $text.LastIndexOf("`n")
            if ($cut -lt 0) { continue }                            # ยังไม่มีบรรทัดที่จบ
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

# ---------------- ตัดของเก่า + เขียน textfile (แต่ละ metric ต้องอยู่ติดกัน)
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
