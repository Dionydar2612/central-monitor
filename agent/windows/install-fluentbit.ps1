<#
.SYNOPSIS
  ติดตั้ง Central Monitor Agent (Fluent Bit) บนเครื่อง Windows
  ส่ง ERROR/WARN จาก Event Log + ไฟล์ log, metrics ของเครื่อง และสัญญาณ "ยังทำงานอยู่" ไปที่ central (OTLP/HTTPS + token)
  metrics เก็บด้วย windows_exporter ตัวจริง (ฟังเฉพาะ 127.0.0.1:9182 ไม่เปิดพอร์ตออกนอกเครื่อง)
  Fluent Bit อ่านจาก 127.0.0.1 แล้วส่งต่อแบบเข้ารหัส -> ไม่มีใครในวง network ดึง/ดักข้อมูลจากเครื่องนี้ได้

.EXAMPLE
  # รันใน PowerShell แบบ Administrator (คำสั่งเต็มคัดลอกได้จากหน้า Settings ของ central)
  powershell -ExecutionPolicy Bypass -File .\install-fluentbit.ps1 `
      -Central 10.0.0.5 -Token <token> -Name win-app-01 `
      -Logs "C:\inetpub\logs\LogFiles\*\*.log;D:\app\logs\*.log" -Channels "System,Application"

.NOTES
  - ไม่มีอินเทอร์เน็ต: วางไฟล์ fluent-bit-<version>-win64.zip และ windows_exporter-<version>-amd64.exe
    ไว้ข้างสคริปต์ หรือระบุ -ZipPath / -ExporterExe
  - windows_exporter ถูกติดตั้งเป็น service "cm-exporter" (ไม่ใช้ MSI) log: C:\ProgramData\central-monitor-agent\windows_exporter.log
  - ติดตั้ง windows_exporter ไม่ได้ -> ใช้ตัวเก็บ metrics ในตัว Fluent Bit แทนอัตโนมัติ (ข้อมูลน้อยกว่า)
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
    [switch] $SkipCertCheck,                                    # ไม่ตรวจใบรับรอง (ยังเข้ารหัส) ใช้เมื่อชื่อ/IP ไม่ตรงกับใบรับรอง
    [string] $ExporterVersion = "0.32.1",                       # เวอร์ชัน windows_exporter
    [string] $ExporterExe = "",                                 # ไฟล์ windows_exporter-<version>-amd64.exe (ใช้แทนการดาวน์โหลด)
    [switch] $NoExporter                                        # ไม่ติดตั้ง windows_exporter (ใช้ตัวเก็บในตัว Fluent Bit)
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
Say "1/7 ทดสอบการเชื่อมต่อ ${Central}:$Port"
$tcp = Test-NetConnection -ComputerName $Central -Port $Port -WarningAction SilentlyContinue
if (-not $tcp.TcpTestSucceeded) {
    Warn "ต่อ ${Central}:$Port ไม่ได้ (firewall / central ยังไม่เปิดพอร์ต) - ติดตั้งต่อได้ agent จะพักข้อมูลไว้แล้วส่งเมื่อต่อได้"
}

# ---------------------------------------------------------------- 2) Fluent Bit
Say "2/7 เตรียม Fluent Bit $Version"
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

# ---------------------------------------------------------------- 3) windows_exporter (metrics)
# ใช้ไฟล์ .exe ของ windows_exporter แล้วสร้าง Windows service "cm-exporter" เอง (ไม่ใช้ MSI)
#   - ฟังเฉพาะ 127.0.0.1:9182 ไม่เปิด firewall -> มีแค่ Fluent Bit บนเครื่องนี้ที่อ่านได้
#   - ก่อนสร้าง service ทดลองเก็บ 1 ครั้ง (--scrape.stdout) เพื่อเลือกชุด collector ที่ใช้ได้กับเครื่องนี้
#   - log ของ exporter: C:\ProgramData\central-monitor-agent\windows_exporter.log
#   ใช้ไม่ได้เลย -> ใช้ตัวเก็บในตัว Fluent Bit แทน (ข้อมูลน้อยกว่า)
$ExporterPort = 9182
$ExporterUrl  = "http://127.0.0.1:$ExporterPort/metrics"
$ExpService   = "cm-exporter"
$ExpExe       = Join-Path $InstallDir "windows_exporter.exe"
$ExpLog       = Join-Path $DataDir "windows_exporter.log"
$ExpCollectorSets = @(
    "cpu,cpu_info,os,memory,logical_disk,physical_disk,net,tcp,system,service,process,diskdrive,time,gpu,dmi,license,pagefile,update",
    "cpu,cpu_info,os,memory,logical_disk,physical_disk,net,tcp,system,service,process,time,pagefile",
    "cpu,os,memory,logical_disk,net,system,service"
)
$UseExporter = $false
$script:ExporterLastError = $null

function Test-Exporter([int] $seconds) {
    # ไม่ผ่าน proxy ของเครื่อง (proxy องค์กรบางที่ไม่ยอมให้เรียก 127.0.0.1 ผ่าน)
    $until = (Get-Date).AddSeconds($seconds)
    do {
        try {
            $wc = New-Object System.Net.WebClient
            $wc.Proxy = New-Object System.Net.WebProxy
            $txt = $wc.DownloadString($ExporterUrl)
            if ($txt -match "windows_exporter_build_info") { return $true }
        } catch { $script:ExporterLastError = $_.Exception.Message }
        Start-Sleep 2
    } while ((Get-Date) -lt $until)
    return $false
}

function Get-ExporterProducts {
    $keys = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
            "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    @(Get-ItemProperty $keys -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like "windows_exporter*" })
}

function Get-ExporterArgs([string] $set, [bool] $withFlags) {
    $a = @("--collectors.enabled=$set")
    if ($withFlags -and $set -match "process") { $a += "--no-collector.process.cmdline" }
    return $a
}

# ทดลองเก็บ 1 ครั้งแล้วออก: ผ่าน = exit 0 และมี windows_exporter_build_info  (กันค้าง: รอไม่เกิน 120 วินาที)
function Test-ExporterOnce([string[]] $expArgs) {
    $out = Join-Path $env:TEMP "cm-exporter-test.out"
    $err = Join-Path $env:TEMP "cm-exporter-test.err"
    Remove-Item $out, $err -ErrorAction SilentlyContinue
    $p = Start-Process -FilePath $ExpExe -ArgumentList ($expArgs + "--scrape.stdout") -NoNewWindow -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    $null = $p.Handle          # PowerShell 5.1: ต้องเปิด handle ไว้ ไม่งั้นอ่าน ExitCode ไม่ได้
    if (-not $p.WaitForExit(120000)) {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        return "หมดเวลา (เก็บนานเกิน 120 วินาที)"
    }
    $text = if (Test-Path $out) { Get-Content $out -Raw -ErrorAction SilentlyContinue } else { "" }
    if ($p.ExitCode -eq 0 -and $text -match "windows_exporter_build_info") { return $null }
    $msg = if (Test-Path $err) { (Get-Content $err -Tail 5 -ErrorAction SilentlyContinue) -join " | " } else { "" }
    return "exit $($p.ExitCode) $msg".Trim()
}

function Show-ExporterDiag {
    Write-Host "   --- ตรวจ windows_exporter ---" -ForegroundColor Yellow
    if ($script:ExporterLastError) { Write-Host "   เรียก ${ExporterUrl}: $script:ExporterLastError" }
    $svc = Get-CimInstance Win32_Service -Filter "Name='$ExpService'" -ErrorAction SilentlyContinue
    if ($svc) { Write-Host "   service: $($svc.State) · $($svc.PathName)" } else { Write-Host "   ไม่พบ service $ExpService" }
    $l = Get-NetTCPConnection -LocalPort $ExporterPort -State Listen -ErrorAction SilentlyContinue
    if ($l) {
        $l | ForEach-Object {
            $pn = (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName
            Write-Host "   พอร์ต ${ExporterPort}: $($_.LocalAddress) ใช้โดย $pn (pid $($_.OwningProcess))"
        }
    } else { Write-Host "   ไม่มีโปรแกรมฟังพอร์ต $ExporterPort" }
    if (Test-Path $ExpLog) { Get-Content $ExpLog -Tail 8 | ForEach-Object { Write-Host "   log: $_" } }
}

if ($NoExporter) {
    Say "3/7 ข้าม windows_exporter (-NoExporter) -> ใช้ตัวเก็บ metrics ในตัว Fluent Bit"
} else {
    Say "3/7 windows_exporter $ExporterVersion (service $ExpService ฟังเฉพาะ 127.0.0.1:$ExporterPort)"
    try {
        New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

        # ไฟล์ .exe: -ExporterExe > วางไว้ข้างสคริปต์ > ดาวน์โหลด
        if (-not $ExporterExe) {
            $local = Join-Path $Here "windows_exporter-$ExporterVersion-amd64.exe"
            if (Test-Path $local) { $ExporterExe = $local }
        }
        if (-not $ExporterExe) {
            $ExporterExe = Join-Path $env:TEMP "windows_exporter-$ExporterVersion-amd64.exe"
            $url = "https://github.com/prometheus-community/windows_exporter/releases/download/v$ExporterVersion/windows_exporter-$ExporterVersion-amd64.exe"
            Say "   ดาวน์โหลด $url"
            Invoke-WebRequest -Uri $url -OutFile $ExporterExe -UseBasicParsing
        }
        if (-not (Test-Path $ExporterExe)) { throw "ไม่พบไฟล์ $ExporterExe" }
        if ((Get-Item $ExporterExe).Length -lt 5MB) { throw "ไฟล์ $ExporterExe เล็กผิดปกติ (ดาวน์โหลดไม่ครบ / ถูก proxy แทนที่) ดาวน์โหลดเองด้วย browser แล้ววางข้างสคริปต์" }
        Unblock-File $ExporterExe -ErrorAction SilentlyContinue

        # หยุดตัวเดิม: service ของเราเอง + windows_exporter ที่ติดตั้งจาก MSI (ใช้พอร์ต 9182 เดียวกัน)
        if (Get-Service $ExpService -ErrorAction SilentlyContinue) { Stop-Service $ExpService -Force -ErrorAction SilentlyContinue; Start-Sleep 2 }
        foreach ($old in Get-ExporterProducts) {
            Warn "พบ $($old.DisplayName) $($old.DisplayVersion) (ติดตั้งจาก MSI) -> ถอนออก (ถ้ามี Prometheus อื่นดึงเครื่องนี้อยู่ จะดึงไม่ได้แล้ว)"
            if ($old.PSChildName -match '^\{[0-9A-Fa-f-]+\}$') {
                Start-Process msiexec.exe -ArgumentList @("/x", $old.PSChildName, "/qn", "/norestart") -Wait | Out-Null
            }
        }
        if (Get-Service windows_exporter -ErrorAction SilentlyContinue) {
            Stop-Service windows_exporter -Force -ErrorAction SilentlyContinue
            sc.exe config windows_exporter start= disabled | Out-Null
            Warn "ปิด service windows_exporter เดิม (ไม่ได้ติดตั้งจาก MSI) เพื่อไม่ให้ชนพอร์ต $ExporterPort"
        }
        $busy = Get-NetTCPConnection -LocalPort $ExporterPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($busy) {
            $pn = (Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue).ProcessName
            throw "พอร์ต $ExporterPort ถูกใช้อยู่โดย $pn (pid $($busy.OwningProcess))"
        }
        Copy-Item $ExporterExe $ExpExe -Force

        # เลือกชุด collector ที่เก็บได้จริงบนเครื่องนี้
        $chosen = $null
        foreach ($set in $ExpCollectorSets) {
            foreach ($flags in @($true, $false)) {
                $expArgs = Get-ExporterArgs $set $flags
                # ตอนทดลองไม่ใส่ update (ถาม Windows Update นานหลายนาที ทำงานเบื้องหลังใน service อยู่แล้ว)
                $testSet = (($set -split ",") | Where-Object { $_ -ne "update" }) -join ","
                $why = Test-ExporterOnce (Get-ExporterArgs $testSet $flags)
                if (-not $why) { $chosen = $expArgs; break }
                $script:ExporterLastError = $why
                Warn "collector ชุดนี้ใช้ไม่ได้: $why"
                if ($set -notmatch "process") { break }
            }
            if ($chosen) { break }
        }
        if (-not $chosen) { throw "windows_exporter เก็บข้อมูลไม่ได้ ($script:ExporterLastError)" }
        Write-Host "   collectors: $(($chosen[0]) -replace '--collectors.enabled=', '')"

        # สร้าง / อัปเดต service
        $svcArgs = @("--web.listen-address=127.0.0.1:$ExporterPort") + $chosen +
                   @("--log.file=`"$ExpLog`"", "--log.rotation.max-size=10", "--log.rotation.max-backups=2")
        $bin = "`"$ExpExe`" " + ($svcArgs -join " ")
        if (Get-Service $ExpService -ErrorAction SilentlyContinue) {
            sc.exe config $ExpService binPath= $bin start= auto | Out-Null
        } else {
            New-Service -Name $ExpService -BinaryPathName $bin -DisplayName "Central Monitor Exporter (windows_exporter)" `
                -Description "เก็บ metrics ของเครื่อง ฟังเฉพาะ 127.0.0.1:$ExporterPort ให้ cm-agent ส่งต่อไปที่ Central Monitor" `
                -StartupType Automatic | Out-Null
        }
        sc.exe failure $ExpService reset= 86400 actions= restart/5000/restart/10000/restart/30000 | Out-Null
        if (Test-Path $ExpLog) { Move-Item $ExpLog "$ExpLog.old" -Force -ErrorAction SilentlyContinue }
        Start-Service $ExpService
        if (-not (Test-Exporter 40)) { Show-ExporterDiag; throw "service $ExpService ไม่ตอบที่ $ExporterUrl" }
        $UseExporter = $true
        $listen = Get-NetTCPConnection -LocalPort $ExporterPort -State Listen -ErrorAction SilentlyContinue |
                  Select-Object -ExpandProperty LocalAddress -Unique
        if ($listen | Where-Object { $_ -notin "127.0.0.1", "::1" }) {
            Warn "windows_exporter ฟังที่ $($listen -join ', ') (ควรเป็น 127.0.0.1 เท่านั้น)"
        }
    } catch {
        Warn "ใช้ windows_exporter ไม่ได้: $($_.Exception.Message) -> ใช้ตัวเก็บ metrics ในตัว Fluent Bit แทน (ข้อมูลน้อยกว่า)"
        if (Get-Service $ExpService -ErrorAction SilentlyContinue) {
            Stop-Service $ExpService -Force -ErrorAction SilentlyContinue
            sc.exe config $ExpService start= disabled | Out-Null
        }
        $UseExporter = $false
    }
}

# ---------------------------------------------------------------- 4) ใบรับรองของ central
Say "4/7 ใบรับรองของ central"
New-Item -ItemType Directory -Force -Path $DataDir, (Join-Path $DataDir "buffer") | Out-Null
if (-not $CaFile) { $CaFile = Join-Path $Here "central-ca.pem" }
$CaDest = Join-Path $DataDir "central-ca.pem"
if (Test-Path $CaFile) {
    Copy-Item $CaFile $CaDest -Force
} elseif (-not $SkipCertCheck) {
    throw "ไม่พบ central-ca.pem (ดาวน์โหลดชุดติดตั้งจากหน้า Settings) หรือใช้ -SkipCertCheck"
}

# ---------------------------------------------------------------- 5) Lua: เลือกเฉพาะ ERROR/WARN + จัดรูปแบบ
Say "5/7 เขียน config"
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

# ---------------------------------------------------------------- 6) fluent-bit.conf
$tls = if ($SkipCertCheck) { "    Tls.verify       Off" } else { "    Tls.verify       On`r`n    Tls.ca_file      $CaDest" }
if (-not $SkipCertCheck -and $Central -notmatch '^[0-9.]+$') { $tls += "`r`n    Tls.vhost        $Central" }

function Output($match, $source, $extra) {
@"
[OUTPUT]
    Name             opentelemetry
    Match            $match
    Host             $Central
    Port             $Port
    Logs_uri         /v1/logs
    Metrics_uri      /v1/metrics
    Header           Authorization Bearer $Token
    Header           X-CM-Web $Name
    Header           X-CM-Source $source
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

__CM_WIN_INPUT__
"@
if ($UseExporter) {
    # windows_exporter ตัวจริง (127.0.0.1 เท่านั้น)
    $winInput = @"
# CPU / Memory / Disk / Network / Service / Process / Hardware ของเครื่อง จาก windows_exporter (127.0.0.1)
[INPUT]
    Name             prometheus_scrape
    Tag              cm.win
    Host             127.0.0.1
    Port             $ExporterPort
    Metrics_Path     /metrics
    Scrape_Interval  15s


"@
} else {
    # ตัวเก็บในตัว Fluent Bit (สำรอง)
    $winInput = @"
# CPU / Memory / Disk / Network / Service / Process ของเครื่อง (ตัวเก็บในตัว Fluent Bit - สำรอง)
[INPUT]
    Name             windows_exporter_metrics
    Tag              cm.win
    Scrape_Interval  15
    metrics          __CM_COLLECTORS__


"@
}
$conf = $conf.Replace("__CM_WIN_INPUT__", $winInput)
$conf += (Output "cm.event" "eventlog" "") + "`r`n"
if ($logPaths.Count -gt 0) { $conf += (Output "cm.file" "application" "") + "`r`n" }
$conf += (Output "cm.hb" "agent" "    add_label        cm_web $Name") + "`r`n"
$conf += (Output "cm.win" "metrics" "    add_label        cm_web $Name") + "`r`n"

# collector ที่อยากได้ (process = รายการ process ที่กิน CPU/RAM) ถ้าเวอร์ชันนี้ไม่รู้จัก จะถอยไปใช้ชุดปริยาย
$Collectors = "cpu,cpu_info,os,net,logical_disk,cs,thermalzone,logon,system,service,memory,paging_file,process"
$confFull = $conf.Replace("__CM_COLLECTORS__", $Collectors)
$confBasic = $conf -replace '(?m)^\s*metrics\s+__CM_COLLECTORS__\r?\n', ''
$conf = $confFull

$ConfFile = Join-Path $DataDir "fluent-bit.conf"
[IO.File]::WriteAllText($ConfFile, $conf, (New-Object Text.UTF8Encoding($false)))

# ตำแหน่งข้อมูลมี token -> ให้เฉพาะ SYSTEM + Administrators อ่านได้
icacls $DataDir /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" | Out-Null

Say "   ตรวจ config"
# fluent-bit เขียนข้อความลง stderr -> PowerShell 5.1 ถือเป็น error เมื่อ ErrorActionPreference=Stop จึงปิดชั่วคราว
$ErrorActionPreference = "Continue"; $dry = & $FluentExe -c $ConfFile --dry-run 2>&1 | ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" } }; $ErrorActionPreference = "Stop"
$dry | Where-Object { $_ -and $_ -notmatch "RemoteException" } | Select-Object -Last 3 | ForEach-Object { Write-Host "   $_" }
if ($LASTEXITCODE -ne 0) {
    Warn "config แบบเต็มไม่ผ่าน -> ลองชุด collector ปริยาย"
    [IO.File]::WriteAllText($ConfFile, $confBasic, (New-Object Text.UTF8Encoding($false)))
    $ErrorActionPreference = "Continue"; $dry = & $FluentExe -c $ConfFile --dry-run 2>&1 | ForEach-Object { "$_" }; $ErrorActionPreference = "Stop"
    $dry | Where-Object { $_ -and $_ -notmatch "RemoteException" } | Select-Object -Last 3 | ForEach-Object { Write-Host "   $_" }
    if ($LASTEXITCODE -ne 0) { throw "config ไม่ผ่าน (ดูข้อความด้านบน)" }
}

# ---------------------------------------------------------------- 7) Windows service
Say "6/7 ติดตั้ง Windows service '$ServiceName'"
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

Say "7/7 ตรวจสถานะ"
Start-Sleep 8
$s = Get-Service $ServiceName
$wemErr = (-not $UseExporter) -and (Test-Path $agentLog) -and (Select-String -Path $agentLog -Pattern "windows_exporter_metrics.*(error|unknown|invalid)|\[error\].*collector" -Quiet)
if ($s.Status -ne "Running" -or $wemErr) {
    Warn "collector บางตัวใช้กับ Fluent Bit $Version ไม่ได้ -> ใช้ชุดปริยาย (อาจไม่มีรายการ process)"
    Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue
    [IO.File]::WriteAllText($ConfFile, $confBasic, (New-Object Text.UTF8Encoding($false)))
    if (Test-Path $agentLog) { Move-Item $agentLog "$agentLog.old" -Force }
    Start-Service $ServiceName
    Start-Sleep 8
    $s = Get-Service $ServiceName
}
Write-Host "   service: $($s.Status)"
if ($UseExporter) {
    Write-Host "   metrics: windows_exporter $ExporterVersion (127.0.0.1:$ExporterPort) -> Fluent Bit -> central"
} else {
    Write-Host "   metrics: ตัวเก็บในตัว Fluent Bit (ไม่มี windows_exporter: บางหน้าจะขึ้น Unsupported)"
}
$logFile = Join-Path $DataDir "fluent-bit.log"
if (Test-Path $logFile) {
    $bad = Select-String -Path $logFile -Pattern "\[error\]|\[ warn\]" | Select-Object -Last 5
    if ($bad) { Warn "พบข้อความใน $logFile :"; $bad | ForEach-Object { Write-Host "   $($_.Line)" } }
}
Write-Host ""
Write-Host "เสร็จแล้ว - เครื่อง '$Name' จะขึ้นสถานะใน Central Monitor ภายใน ~1 นาที" -ForegroundColor Green
Write-Host "log ของ agent: $logFile"
Write-Host "ทดสอบส่ง ERROR:  eventcreate /T ERROR /ID 999 /L APPLICATION /SO CentralMonitorTest /D `"test error from $Name`""
