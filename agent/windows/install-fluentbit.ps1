<#
.SYNOPSIS
  ติดตั้ง Central Monitor Agent (Fluent Bit) บนเครื่อง Windows
  ส่ง ERROR/WARN จาก Event Log + ไฟล์ log และสัญญาณ "ยังทำงานอยู่" ไปที่ central (OTLP/HTTPS + token)

.EXAMPLE
  # รันใน PowerShell แบบ Administrator (คำสั่งเต็มคัดลอกได้จากหน้า Settings ของ central)
  powershell -ExecutionPolicy Bypass -File .\install-fluentbit.ps1 `
      -Central 10.0.0.5 -Token <token> -Name win-app-01 `
      -Logs "C:\inetpub\logs\LogFiles\*\*.log;D:\app\logs\*.log" -Channels "System,Application"

.NOTES
  - ไม่มีอินเทอร์เน็ต: วางไฟล์ fluent-bit-<version>-win64.zip ไว้ข้างสคริปต์ หรือระบุ -ZipPath
  - รันซ้ำได้ (อัปเดต config / เวอร์ชัน) ข้อมูลตำแหน่งที่อ่านแล้วถูกเก็บไว้ ไม่ส่งซ้ำ
  - ถอนการติดตั้ง: uninstall-fluentbit.ps1
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Central,          # IP หรือชื่อของเครื่อง central (ที่เครื่องนี้มองเห็น)
    [Parameter(Mandatory = $true)] [string] $Token,            # CENTRAL_AGENT_TOKEN ใน docker/.env ของ central
    [string] $Name = "",                                        # ชื่อเครื่องใน central (ต้องตรงกับที่เพิ่มในหน้า Settings)
    [int]    $Port = 4318,
    [string] $Logs = "",                                        # path ไฟล์ log คั่นด้วย ; (wildcard ได้)
    [string] $Channels = "System,Application",                 # Event Log channel คั่นด้วย ,
    [string] $Version = "5.1.5",                                # เวอร์ชัน Fluent Bit
    [string] $ZipPath = "",                                     # ไฟล์ zip ของ Fluent Bit (ใช้แทนการดาวน์โหลด)
    [string] $CaFile = "",                                      # ใบรับรองของ central (ปริยาย: central-ca.pem ข้างสคริปต์)
    [switch] $SkipCertCheck                                     # ไม่ตรวจใบรับรอง (ยังเข้ารหัส) ใช้เมื่อชื่อ/IP ไม่ตรงกับใบรับรอง
)

$ErrorActionPreference = "Stop"
$ServiceName = "cm-agent"
$InstallDir  = Join-Path $env:ProgramFiles "central-monitor-agent"
$DataDir     = Join-Path $env:ProgramData  "central-monitor-agent"
$Here        = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 0) ตรวจเบื้องต้น
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
          [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "ต้องรันใน PowerShell แบบ Run as Administrator" }

if (-not $Name) { $Name = $env:COMPUTERNAME.ToLower() }
if ($Name -notmatch '^[a-z0-9][a-z0-9-]{0,39}$') {
    throw "ชื่อเครื่อง '$Name' ใช้ไม่ได้ (a-z 0-9 - เท่านั้น ยาวไม่เกิน 40) ระบุ -Name ให้ตรงกับที่เพิ่มในหน้า Settings"
}
$logPaths = @($Logs -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$channelList = (@($Channels -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })) -join ','

Say "เครื่อง: $Name  ->  central: ${Central}:$Port"

# ---------------------------------------------------------------- 1) ทดสอบว่าต่อ central ได้
Say "1/6 ทดสอบการเชื่อมต่อ ${Central}:$Port"
$tcp = Test-NetConnection -ComputerName $Central -Port $Port -WarningAction SilentlyContinue
if (-not $tcp.TcpTestSucceeded) {
    Warn "ต่อ ${Central}:$Port ไม่ได้ (firewall / central ยังไม่เปิดพอร์ต) - ติดตั้งต่อได้ agent จะพักข้อมูลไว้แล้วส่งเมื่อต่อได้"
}

# ---------------------------------------------------------------- 2) Fluent Bit
Say "2/6 เตรียม Fluent Bit $Version"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if (-not $ZipPath) {
    $local = Join-Path $Here "fluent-bit-$Version-win64.zip"
    if (Test-Path $local) { $ZipPath = $local }
}
if (-not $ZipPath) {
    $ZipPath = Join-Path $env:TEMP "fluent-bit-$Version-win64.zip"
    $url = "https://packages.fluentbit.io/windows/fluent-bit-$Version-win64.zip"
    Say "   ดาวน์โหลด $url"
    Invoke-WebRequest -Uri $url -OutFile $ZipPath -UseBasicParsing
}
if (-not (Test-Path $ZipPath)) { throw "ไม่พบไฟล์ $ZipPath" }

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -ne 'Stopped') { Say "   หยุด service เดิม"; Stop-Service $ServiceName -Force; Start-Sleep 2 }

$tmp = Join-Path $env:TEMP ("cm-agent-" + [guid]::NewGuid())
Expand-Archive -Path $ZipPath -DestinationPath $tmp -Force
$exe = Get-ChildItem -Path $tmp -Recurse -Filter "fluent-bit.exe" | Select-Object -First 1
if (-not $exe) { throw "ไม่พบ fluent-bit.exe ใน $ZipPath" }
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Path (Join-Path $exe.Directory.Parent.FullName "*") -Destination $InstallDir -Recurse -Force
Remove-Item $tmp -Recurse -Force
$FluentExe = Join-Path $InstallDir "bin\fluent-bit.exe"

# ---------------------------------------------------------------- 3) ใบรับรองของ central
Say "3/6 ใบรับรองของ central"
New-Item -ItemType Directory -Force -Path $DataDir, (Join-Path $DataDir "buffer") | Out-Null
if (-not $CaFile) { $CaFile = Join-Path $Here "central-ca.pem" }
$CaDest = Join-Path $DataDir "central-ca.pem"
if (Test-Path $CaFile) {
    Copy-Item $CaFile $CaDest -Force
} elseif (-not $SkipCertCheck) {
    throw "ไม่พบ central-ca.pem (ดาวน์โหลดชุดติดตั้งจากหน้า Settings) หรือใช้ -SkipCertCheck"
}

# ---------------------------------------------------------------- 4) Lua: เลือกเฉพาะ ERROR/WARN + จัดรูปแบบ
Say "4/6 เขียน config"
$lua = @'
-- cm-agent.lua : ตัด INFO ทิ้ง แล้วจัดรูปแบบให้ central  {cm_web, source, file, level, message}
-- message = "<เวลา UTC> [LEVEL] ..." (central อ่านระดับจาก [LEVEL] หลังเวลา เหมือน log ของเครื่อง Linux)
local WEB = "__CM_WEB__"

local function days_from_civil(y, m, d)
  y = (m <= 2) and (y - 1) or y
  local era = math.floor(y / 400)
  local yoe = y - era * 400
  local mp = (m + 9) % 12
  local doy = math.floor((153 * mp + 2) / 5) + d - 1
  local doe = yoe * 365 + math.floor(yoe / 4) - math.floor(yoe / 100) + doy
  return era * 146097 + doe - 719468
end

local function utc_epoch(y, mo, d, h, mi, s)
  return days_from_civil(tonumber(y), tonumber(mo), tonumber(d)) * 86400 + tonumber(h) * 3600 + tonumber(mi) * 60 + tonumber(s)
end

local function iso(sec) return os.date("!%Y-%m-%dT%H:%M:%SZ", math.floor(sec)) end
local function oneline(s) return (tostring(s or ""):gsub("[\r\n\t]+", " ")) end
local function basename(p) return (tostring(p or ""):match("([^\\/]+)$") or "") end

local WORDS = { error="ERROR", err="ERROR", critical="ERROR", crit="ERROR", fatal="ERROR", alert="ERROR",
  emerg="ERROR", emergency="ERROR", severe="ERROR", panic="ERROR", warn="WARN", warning="WARN",
  info="INFO", information="INFO", notice="INFO", debug="INFO", trace="INFO", verbose="INFO" }

-- ระดับที่ log บอกเอง: level=xxx / severity: xxx / [XXX] / คำพิมพ์ใหญ่ช่วงต้นบรรทัด
local function explicit_level(line)
  local low = line:lower()
  local w = low:match("level[\"']?%s*[=:]%s*[\"']?(%a+)") or low:match("severity[\"']?%s*[=:]%s*[\"']?(%a+)")
         or low:match("%[%s*(%a+)%s*%]")
  if w and WORDS[w] then return WORDS[w] end
  for word in line:sub(1, 160):gmatch("%f[%w]%u%u%u+%f[%W]") do
    if WORDS[word:lower()] then return WORDS[word:lower()] end
  end
  return nil
end

local BENIGN = { "no errors?", "0 errors?", "zero errors?", "without errors?", "no failures?", "0 failures?",
  "errors?%s*[=:]%s*0", "failures?%s*[=:]%s*0", "failed%s*[=:]%s*0", "error[_%.%-]?log", "error[_%.%-]?page" }
local ERRORS = { "%f[%a]error", "%f[%a]exception", "%f[%a]fail", "%f[%a]fatal", "%f[%a]critical", "%f[%a]panic",
  "traceback", "segfault", "refused", "timed out", "denied", "unreachable", "out of memory", "no space left" }

local function guess_level(line)
  local lv = explicit_level(line)
  if lv then return lv end
  local low = line:lower()
  for _, p in ipairs(BENIGN) do low = low:gsub(p, " ") end
  for _, p in ipairs(ERRORS) do if low:find(p) then return "ERROR" end end
  if low:find("%f[%a]warn") or low:find("deprecated") then return "WARN" end
  return "INFO"
end

-- Event Log: Level 1 Critical, 2 Error, 3 Warning (4 Information, 0 LogAlways, 5 Verbose = ทิ้ง)
function cm_event(tag, ts, r)
  local lv = tonumber(r["Level"])
  if not lv then
    local s = tostring(r["Level"] or ""):lower()
    lv = (s == "critical" and 1) or (s == "error" and 2) or (s:find("warn") and 3) or 4
  end
  if lv < 1 or lv > 3 then return -1, 0, 0 end
  local level = (lv <= 2) and "ERROR" or "WARN"

  local t = ts
  local y, mo, d, h, mi, s, sign, oh, om = tostring(r["TimeCreated"] or ""):match(
      "(%d+)-(%d+)-(%d+)[ T](%d+):(%d+):(%d+)[%.%d]*%s*([%+%-])(%d%d):?(%d%d)")
  if y then
    local off = (tonumber(oh) * 3600 + tonumber(om) * 60) * ((sign == "-") and -1 or 1)
    t = utc_epoch(y, mo, d, h, mi, s) - off
  end

  local chan = tostring(r["Channel"] or "Event")
  local msg = string.format("%s [%s] [%s] %s (Event %s): %s", iso(t), level, chan,
      tostring(r["ProviderName"] or "-"), tostring(r["EventID"] or "-"), oneline(r["Message"]))
  return 1, t, { cm_web = WEB, source = "eventlog", file = chan, level = level, message = msg }
end

-- ไฟล์ log ทั่วไป + IIS (W3C: เวลาเป็น UTC, ERROR เมื่อ HTTP status 5xx)
function cm_file(tag, ts, r)
  local line = oneline(r["log"])
  if line == "" or line:sub(1, 1) == "#" then return -1, 0, 0 end
  local path = tostring(r["cm_path"] or "")
  local file = basename(path)

  local y, mo, d, h, mi, s, rest = line:match("^(%d%d%d%d)%-(%d%d)%-(%d%d) (%d%d):(%d%d):(%d%d) (.*)$")
  if y and path:lower():find("inetpub") then
    local f = {}
    for x in rest:gmatch("%S+") do f[#f + 1] = x end
    local status = tonumber(f[#f - 3] or "")
    if not status or status < 500 then return -1, 0, 0 end
    local t = utc_epoch(y, mo, d, h, mi, s)
    return 1, t, { cm_web = WEB, source = "iis", file = file, level = "ERROR",
                   message = string.format("%s [ERROR] HTTP %d %s", iso(t), status, rest) }
  end

  local level = guess_level(line)
  if level == "INFO" then return -1, 0, 0 end
  return 2, ts, { cm_web = WEB, source = "application", file = file, level = level,
                  message = string.format("%s [%s] %s", iso(ts), level, line) }
end
'@
$lua = $lua.Replace("__CM_WEB__", $Name)
$LuaFile = Join-Path $DataDir "cm-agent.lua"
[IO.File]::WriteAllText($LuaFile, $lua, (New-Object Text.UTF8Encoding($false)))

# ---------------------------------------------------------------- 5) fluent-bit.conf
$tls = if ($SkipCertCheck) { "    Tls.verify       Off" } else { "    Tls.verify       On`r`n    Tls.ca_file      $CaDest" }
if (-not $SkipCertCheck -and $Central -notmatch '^[0-9.]+$') { $tls += "`r`n    Tls.vhost        $Central" }

function Output($match, $extra) {
@"
[OUTPUT]
    Name             opentelemetry
    Match            $match
    Host             $Central
    Port             $Port
    Logs_uri         /v1/logs
    Metrics_uri      /v1/metrics
    Header           Authorization Bearer $Token
    Tls              On
$tls
    Retry_Limit      no_limits
    storage.total_limit_size 512M
$extra
"@
}

$conf = @"
# สร้างโดย install-fluentbit.ps1 (Central Monitor) - แก้แล้วรันสคริปต์ซ้ำจะถูกเขียนทับ
[SERVICE]
    Flush            5
    Log_Level        info
    Log_File         $DataDir\fluent-bit.log
    storage.path     $DataDir\buffer
    storage.sync     normal
    storage.backlog.mem_limit 16M

[INPUT]
    Name             winevtlog
    Tag              cm.event
    Channels         $channelList
    Interval_Sec     5
    Read_Existing_Events Off
    DB               $DataDir\winevtlog.sqlite
    storage.type     filesystem

[FILTER]
    Name             lua
    Match            cm.event
    script           $LuaFile
    call             cm_event


"@
if ($logPaths.Count -gt 0) {
    $conf += @"
[INPUT]
    Name             tail
    Tag              cm.file
    Path             $($logPaths -join ',')
    Path_Key         cm_path
    DB               $DataDir\tail.sqlite
    Read_from_Head   Off
    Skip_Long_Lines  On
    Refresh_Interval 10
    storage.type     filesystem

[FILTER]
    Name             lua
    Match            cm.file
    script           $LuaFile
    call             cm_file


"@
}
$conf += @"
# สัญญาณ "agent ยังทำงานอยู่" (central ใช้แสดงสถานะ / Last seen)
[INPUT]
    Name             fluentbit_metrics
    Tag              cm.hb
    Scrape_Interval  30


"@
$conf += (Output "cm.event" "") + "`r`n"
if ($logPaths.Count -gt 0) { $conf += (Output "cm.file" "") + "`r`n" }
$conf += (Output "cm.hb" "    add_label        cm_web $Name") + "`r`n"

$ConfFile = Join-Path $DataDir "fluent-bit.conf"
[IO.File]::WriteAllText($ConfFile, $conf, (New-Object Text.UTF8Encoding($false)))

# ตำแหน่งข้อมูลมี token -> ให้เฉพาะ SYSTEM + Administrators อ่านได้
icacls $DataDir /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" | Out-Null

Say "   ตรวจ config"
# fluent-bit เขียนข้อความลง stderr -> PowerShell 5.1 ถือเป็น error เมื่อ ErrorActionPreference=Stop จึงปิดชั่วคราว
$ErrorActionPreference = "Continue"; $dry = & $FluentExe -c $ConfFile --dry-run 2>&1 | ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" } }; $ErrorActionPreference = "Stop"
$dry | Where-Object { $_ -and $_ -notmatch "RemoteException" } | Select-Object -Last 3 | ForEach-Object { Write-Host "   $_" }
if ($LASTEXITCODE -ne 0) { throw "config ไม่ผ่าน (ดูข้อความด้านบน)" }

# ---------------------------------------------------------------- 6) Windows service
Say "5/6 ติดตั้ง Windows service '$ServiceName'"
$bin = "`"$FluentExe`" -c `"$ConfFile`""
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    sc.exe config $ServiceName binPath= $bin start= auto | Out-Null
} else {
    New-Service -Name $ServiceName -BinaryPathName $bin -DisplayName "Central Monitor Agent (Fluent Bit)" `
        -Description "ส่ง ERROR/WARN log และสถานะเครื่องไปที่ Central Monitor" -StartupType Automatic | Out-Null
}
sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/10000/restart/30000 | Out-Null
# log เก่าเก็บเป็น .old -> ข้อความที่ตรวจด้านล่างเป็นของรอบนี้เท่านั้น
$agentLog = Join-Path $DataDir "fluent-bit.log"
if (Test-Path $agentLog) { Move-Item $agentLog "$agentLog.old" -Force }
Start-Service $ServiceName

Say "6/6 ตรวจสถานะ"
Start-Sleep 8
$s = Get-Service $ServiceName
Write-Host "   service: $($s.Status)"
$logFile = Join-Path $DataDir "fluent-bit.log"
if (Test-Path $logFile) {
    $bad = Select-String -Path $logFile -Pattern "\[error\]|\[ warn\]" | Select-Object -Last 5
    if ($bad) { Warn "พบข้อความใน $logFile :"; $bad | ForEach-Object { Write-Host "   $($_.Line)" } }
}
Write-Host ""
Write-Host "เสร็จแล้ว - เครื่อง '$Name' จะขึ้นสถานะใน Central Monitor ภายใน ~1 นาที" -ForegroundColor Green
Write-Host "log ของ agent: $logFile"
Write-Host "ทดสอบส่ง ERROR:  eventcreate /T ERROR /ID 999 /L APPLICATION /SO CentralMonitorTest /D `"test error from $Name`""
