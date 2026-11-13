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

.EXAMPLE
  # โหมด pull: เครื่อง Windows ต่อไปหา central ไม่ได้ (firewall ระหว่างวง) แต่ central ต่อเข้ามาได้
  #   ติดตั้งเฉพาะ windows_exporter ฟังที่ 0.0.0.0:9182 + firewall เปิดรับเฉพาะ IP ของ central
  #   central (Prometheus) เป็นฝ่ายดึง metrics เอง ไม่ต้องใช้ token / Fluent Bit (ไม่มี log ในโหมดนี้)
  powershell -ExecutionPolicy Bypass -File .\install-fluentbit.ps1 -Pull -Central 172.27.2.22 -Name win-app-01

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
    [string] $Token = "",                                       # CENTRAL_AGENT_TOKEN ใน docker/.env ของ central (โหมด pull ไม่ใช้)
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
    [switch] $NoExporter,                                       # ไม่ติดตั้ง windows_exporter (ใช้ตัวเก็บในตัว Fluent Bit)
    [switch] $Pull                                              # โหมด pull: central ดึง metrics จาก windows_exporter เอง (เครื่องนี้ต่อไปหา central ไม่ได้)
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
if (-not $Pull -and -not $Token) { throw "ต้องระบุ -Token (หรือใช้ -Pull ถ้าเครื่องนี้ต่อไปหา central ไม่ได้)" }
if ($Pull -and $NoExporter) { throw "โหมด -Pull ต้องใช้ windows_exporter (เอา -NoExporter ออก)" }
$logPaths = @($Logs -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$channelList = (@($Channels -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })) -join ','

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if ($Pull) {
    # ---------------------------------------------------------------- โหมด pull: central เป็นฝ่ายดึง
    Say "เครื่อง: $Name  <-  central ($Central) ดึง metrics ที่พอร์ต 9182 (โหมด pull ไม่มี Fluent Bit / log)"
    try {
        $CentralIps = @([System.Net.Dns]::GetHostAddresses($Central) | Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
                        ForEach-Object { $_.IPAddressToString })
    } catch { $CentralIps = @() }
    if (-not $CentralIps) { throw "แปลง '$Central' เป็น IP ไม่ได้ -> ใส่ -Central เป็น IP ของ central" }
    Say "1/3 ข้ามการทดสอบต่อไปหา central (โหมด pull ไม่ต้องใช้)"
} else {
Say "เครื่อง: $Name  ->  central: ${Central}:$Port"

# ---------------------------------------------------------------- 1) ทดสอบว่าต่อ central ได้
Say "1/7 ทดสอบการเชื่อมต่อ ${Central}:$Port"
$tcp = Test-NetConnection -ComputerName $Central -Port $Port -WarningAction SilentlyContinue
if (-not $tcp.TcpTestSucceeded) {
    Warn "ต่อ ${Central}:$Port ไม่ได้ (firewall / central ยังไม่เปิดพอร์ต) - ติดตั้งต่อได้ agent จะพักข้อมูลไว้แล้วส่งเมื่อต่อได้"
    Warn "ถ้าเครื่องนี้ต่อไปหา central ไม่ได้จริง (firewall ระหว่างวง) แต่ central ต่อเข้ามาได้ ใช้โหมด -Pull แทน"
}

# ---------------------------------------------------------------- 2) Fluent Bit
Say "2/7 เตรียม Fluent Bit $Version"
if (-not $ZipPath) {
    foreach ($dir in @($Here, (Join-Path $env:USERPROFILE "Downloads"))) {
        $local = Join-Path $dir "fluent-bit-$Version-win64.zip"
        if (-not $ZipPath -and (Test-Path $local)) { $ZipPath = $local }
    }
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
}   # จบส่วน push (โหมด pull ไม่ติดตั้ง Fluent Bit)

# ---------------------------------------------------------------- 3) windows_exporter (metrics)
# ใช้ไฟล์ .exe ของ windows_exporter แล้วสร้าง Windows service "cm-exporter" เอง (ไม่ใช้ MSI)
#   - ฟังเฉพาะ 127.0.0.1:9182 ไม่เปิด firewall -> มีแค่ Fluent Bit บนเครื่องนี้ที่อ่านได้
#   - ก่อนสร้าง service ทดลองเก็บ 1 ครั้ง (--scrape.stdout) เพื่อเลือกชุด collector ที่ใช้ได้กับเครื่องนี้
#   - log ของ exporter: C:\ProgramData\central-monitor-agent\windows_exporter.log
#   ใช้ไม่ได้เลย -> ใช้ตัวเก็บในตัว Fluent Bit แทน (ข้อมูลน้อยกว่า)
$ExporterPort = 9182
$ListenAddr   = if ($Pull) { "0.0.0.0" } else { "127.0.0.1" }   # pull: central ต้องเข้ามาอ่านได้ (firewall เปิดเฉพาะ IP ของ central)
$ExporterUrl  = "http://127.0.0.1:$ExporterPort/metrics"
$ExpService   = "cm-exporter"
$ExpExe       = Join-Path $InstallDir "windows_exporter.exe"
$ExpLog       = Join-Path $DataDir "windows_exporter.log"
$ExpCollectorSets = @(
    "cpu,cpu_info,os,memory,logical_disk,physical_disk,net,tcp,system,service,process,diskdrive,time,gpu,dmi,license,pagefile,update,textfile",
    "cpu,cpu_info,os,memory,logical_disk,physical_disk,net,tcp,system,service,process,time,pagefile,textfile",
    "cpu,os,memory,logical_disk,net,system,service,process,textfile",
    "cpu,os,memory,logical_disk,net,system,service"
)
# textfile: โปรแกรมที่ติดตั้ง + พอร์ตที่เปิดรับ (cm-inventory.ps1 เขียนทุก 15 นาที)
$TextDir = Join-Path $DataDir "textfile"
$InvScript = Join-Path $InstallDir "cm-inventory.ps1"
$InvTask = "CentralMonitorInventory"
# Docker container + เว็บ localhost (cm-probe.ps1 ทุก 1 นาที)
$ProbeScript = Join-Path $InstallDir "cm-probe.ps1"
$ProbeTask = "CentralMonitorProbe"
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
    if ($set -match "textfile") { $a += "--collector.textfile.directories=$TextDir" }
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
    Say "$(if ($Pull) { '2/3' } else { '3/7' }) windows_exporter $ExporterVersion (service $ExpService ฟังที่ ${ListenAddr}:$ExporterPort)"
    try {
        New-Item -ItemType Directory -Force -Path $DataDir, $InstallDir | Out-Null

        # ไฟล์ .exe: -ExporterExe > วางไว้ข้างสคริปต์ > ดาวน์โหลด
        if (-not $ExporterExe) {
            foreach ($dir in @($Here, (Join-Path $env:USERPROFILE "Downloads"))) {
                $local = Join-Path $dir "windows_exporter-$ExporterVersion-amd64.exe"
                if (-not $ExporterExe -and (Test-Path $local)) { $ExporterExe = $local }
            }
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

        # รายการโปรแกรมที่ติดตั้ง + พอร์ตที่เปิดรับ -> ไฟล์ .prom ให้ windows_exporter (textfile collector) อ่าน
        New-Item -ItemType Directory -Force -Path $TextDir | Out-Null
        $inv = @'
# cm-inventory.ps1 (Central Monitor) - รายการโปรแกรมที่ติดตั้ง + พอร์ต TCP ที่เปิดรับ -> textfile ของ windows_exporter
# scheduled task "CentralMonitorInventory" รันทุก 15 นาที (SYSTEM)
$ErrorActionPreference = "Stop"
$Dir = Join-Path $env:ProgramData "central-monitor-agent\textfile"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
function Esc($s) { if ($null -eq $s) { return "" }; return ([string]$s -replace '\\', '\\' -replace '"', '\"' -replace "[\r\n\t]+", " ").Trim() }
$L = New-Object System.Collections.Generic.List[string]
$L.Add("# HELP cm_software_installed Installed program (registry Uninstall keys)")
$L.Add("# TYPE cm_software_installed gauge")
$seen = @{}
$keys = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
foreach ($p in @(Get-ItemProperty $keys -ErrorAction SilentlyContinue)) {
    if (-not $p.DisplayName -or $p.SystemComponent -eq 1 -or $p.ParentKeyName -or "$($p.ReleaseType)" -match "Update|Hotfix") { continue }
    $arch = if ($p.PSPath -match "WOW6432Node") { "x86" } else { "x64" }
    $n = Esc $p.DisplayName; $v = Esc $p.DisplayVersion
    $k = "$n|$v|$arch"
    if ($seen.ContainsKey($k)) { continue }
    $seen[$k] = 1
    $L.Add("cm_software_installed{name=`"$n`",version=`"$v`",publisher=`"$(Esc $p.Publisher)`",installed=`"$(Esc $p.InstallDate)`",arch=`"$arch`"} 1")
}
$L.Add("# HELP cm_port_listening TCP port listening on this machine")
$L.Add("# TYPE cm_port_listening gauge")
$procs = @{}
Get-Process -ErrorAction SilentlyContinue | ForEach-Object { $procs[[int]$_.Id] = $_.ProcessName }
$seenP = @{}
foreach ($c in @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)) {
    $k = "$($c.LocalPort)|$($c.LocalAddress)"
    if ($seenP.ContainsKey($k)) { continue }
    $seenP[$k] = 1
    $L.Add("cm_port_listening{port=`"$($c.LocalPort)`",address=`"$(Esc $c.LocalAddress)`",process=`"$(Esc $procs[[int]$c.OwningProcess])`"} 1")
}
$L.Add("# HELP cm_inventory_timestamp_seconds Last inventory run")
$L.Add("# TYPE cm_inventory_timestamp_seconds gauge")
$L.Add("cm_inventory_timestamp_seconds $([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())")
$tmp = Join-Path $Dir "cm_inventory.prom.tmp"
[IO.File]::WriteAllText($tmp, (($L -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))
Move-Item $tmp (Join-Path $Dir "cm_inventory.prom") -Force
'@
        $probe = @'
# cm-probe.ps1 (Central Monitor) - Docker container + เว็บที่เปิดบนเครื่อง (http://127.0.0.1:<พอร์ต>/)
# -> textfile ของ windows_exporter  scheduled task "CentralMonitorProbe" รันทุก 1 นาที (SYSTEM)
$ErrorActionPreference = "Continue"
$Dir = Join-Path $env:ProgramData "central-monitor-agent\textfile"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
function Esc($s) { if ($null -eq $s) { return "" }; return ([string]$s -replace '\\', '\\' -replace '"', '\"' -replace "[\r\n\t]+", " ").Trim() }
$L = New-Object System.Collections.Generic.List[string]

# ---- Docker ----
$L.Add("# TYPE cm_docker_container gauge")
$up = 0
$docker = Get-Command docker.exe -ErrorAction SilentlyContinue
if ($docker) {
    $out = & $docker.Source ps -a --format "{{json .}}" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $up = 1
        $seen = @{}
        foreach ($line in @($out)) {
            try { $c = $line | ConvertFrom-Json } catch { continue }
            foreach ($n in ("$($c.Names)" -split ",")) {
                $n = $n.Trim().TrimStart("/")
                if (-not $n -or $seen.ContainsKey($n)) { continue }
                $seen[$n] = 1
                $L.Add("cm_docker_container{name=`"$(Esc $n)`",image=`"$(Esc $c.Image)`",state=`"$(Esc "$($c.State)".ToLower())`",status=`"$(Esc $c.Status)`"} 1")
            }
        }
    }
}
$L.Add("# TYPE cm_docker_up gauge")
$L.Add("cm_docker_up $up")

# ---- เว็บบนเครื่อง: ทุกพอร์ตที่เปิดรับ (ยกเว้นพอร์ตที่ไม่ใช่เว็บแน่ๆ) ----
$skip = @(53, 88, 135, 139, 389, 445, 464, 593, 636, 1433, 1434, 1521, 3268, 3269, 3306, 3389, 5432, 5985, 5986, 6379, 9182, 27017, 47001)
$ports = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty LocalPort -Unique |
          Where-Object { $_ -lt 49152 -and $skip -notcontains $_ } | Sort-Object | Select-Object -First 30)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls
[Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
function Probe($url) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
        $rq = [Net.HttpWebRequest]::Create($url)
        $rq.Timeout = 2500; $rq.ReadWriteTimeout = 2500; $rq.AllowAutoRedirect = $false; $rq.Proxy = $null
        $rs = $rq.GetResponse(); $code = [int]$rs.StatusCode; $rs.Close()
        return @($code, $sw.ElapsedMilliseconds, $null)
    } catch {
        $ex = $_.Exception
        while ($ex -and -not ($ex -is [Net.WebException])) { $ex = $ex.InnerException }
        if ($ex -and $ex.Response) { $code = [int]$ex.Response.StatusCode; $ex.Response.Close(); return @($code, $sw.ElapsedMilliseconds, $null) }
        return @(0, $sw.ElapsedMilliseconds, $(if ($ex) { "$($ex.Status)" } else { "Error" }))
    }
}
$codes = New-Object System.Collections.Generic.List[string]
$times = New-Object System.Collections.Generic.List[string]
foreach ($p in $ports) {
    $r = Probe "http://127.0.0.1:$p/"; $scheme = "http"
    if ($r[0] -eq 0 -and "$($r[2])" -match "ServerProtocolViolation|ConnectionClosed|ReceiveFailure|SecureChannelFailure|KeepAliveFailure") {
        $r2 = Probe "https://127.0.0.1:$p/"
        if ($r2[0] -gt 0) { $r = $r2; $scheme = "https" }
    }
    $codes.Add("cm_http_local_code{port=`"$p`",scheme=`"$scheme`"} $($r[0])")
    if ($r[0] -gt 0) { $times.Add("cm_http_local_ms{port=`"$p`",scheme=`"$scheme`"} $($r[1])") }
}
# แต่ละ metric ต้องอยู่ติดกัน (textfile collector ของ windows_exporter ตรวจรูปแบบเข้ม)
$L.Add("# TYPE cm_http_local_code gauge"); $L.AddRange($codes)
$L.Add("# TYPE cm_http_local_ms gauge"); $L.AddRange($times)
$L.Add("# TYPE cm_probe_timestamp_seconds gauge")
$L.Add("cm_probe_timestamp_seconds $([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())")
$tmp = Join-Path $Dir "cm_probe.prom.tmp"
[IO.File]::WriteAllText($tmp, (($L -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))
Move-Item $tmp (Join-Path $Dir "cm_probe.prom") -Force
'@
        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
        [IO.File]::WriteAllText($InvScript, $inv, (New-Object Text.UTF8Encoding($true)))
        [IO.File]::WriteAllText($ProbeScript, $probe, (New-Object Text.UTF8Encoding($true)))
        try { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ProbeScript }
        catch { Warn "ตรวจ Docker / เว็บบนเครื่องไม่ได้: $($_.Exception.Message)" }
        try { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $InvScript }
        catch { Warn "อ่านรายการโปรแกรม / พอร์ตไม่ได้: $($_.Exception.Message)" }

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
        $svcArgs = @("--web.listen-address=${ListenAddr}:$ExporterPort") + $chosen +
                   @("--log.file=`"$ExpLog`"", "--log.rotation.max-size=10", "--log.rotation.max-backups=2")
        $bin = "`"$ExpExe`" " + ($svcArgs -join " ")
        if (Get-Service $ExpService -ErrorAction SilentlyContinue) {
            sc.exe config $ExpService binPath= $bin start= auto | Out-Null
        } else {
            New-Service -Name $ExpService -BinaryPathName $bin -DisplayName "Central Monitor Exporter (windows_exporter)" `
                -Description "เก็บ metrics ของเครื่อง ($ListenAddr`:$ExporterPort) ให้ Central Monitor" `
                -StartupType Automatic | Out-Null
        }
        sc.exe failure $ExpService reset= 86400 actions= restart/5000/restart/10000/restart/30000 | Out-Null
        if (Test-Path $ExpLog) { Move-Item $ExpLog "$ExpLog.old" -Force -ErrorAction SilentlyContinue }
        Start-Service $ExpService
        if (-not (Test-Exporter 40)) { Show-ExporterDiag; throw "service $ExpService ไม่ตอบที่ $ExporterUrl" }
        $UseExporter = $true
        try {
            $act = New-ScheduledTaskAction -Execute "powershell.exe" `
                -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$InvScript`""
            $trg = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15) `
                -RepetitionDuration (New-TimeSpan -Days 3650)
            $pr = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
            $set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -StartWhenAvailable -MultipleInstances IgnoreNew
            Register-ScheduledTask -TaskName $InvTask -Action $act -Trigger $trg -Principal $pr -Settings $set -Force | Out-Null
            Write-Host "   รายการโปรแกรม / พอร์ต: scheduled task '$InvTask' ทุก 15 นาที"
            $act2 = New-ScheduledTaskAction -Execute "powershell.exe" `
                -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ProbeScript`""
            $trg2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1) `
                -RepetitionDuration (New-TimeSpan -Days 3650)
            $set2 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -StartWhenAvailable -MultipleInstances IgnoreNew
            Register-ScheduledTask -TaskName $ProbeTask -Action $act2 -Trigger $trg2 -Principal $pr -Settings $set2 -Force | Out-Null
            Write-Host "   Docker / เว็บบนเครื่อง: scheduled task '$ProbeTask' ทุก 1 นาที"
        } catch { Warn "สร้าง scheduled task '$InvTask' ไม่ได้: $($_.Exception.Message) (รายการโปรแกรมจะไม่อัปเดต)" }
        $listen = Get-NetTCPConnection -LocalPort $ExporterPort -State Listen -ErrorAction SilentlyContinue |
                  Select-Object -ExpandProperty LocalAddress -Unique
        if (-not $Pull -and ($listen | Where-Object { $_ -notin "127.0.0.1", "::1" })) {
            Warn "windows_exporter ฟังที่ $($listen -join ', ') (ควรเป็น 127.0.0.1 เท่านั้น)"
        }
    } catch {
        if ($Pull) { throw "ใช้ windows_exporter ไม่ได้: $($_.Exception.Message) (โหมด pull ต้องมี windows_exporter)" }
        Warn "ใช้ windows_exporter ไม่ได้: $($_.Exception.Message) -> ใช้ตัวเก็บ metrics ในตัว Fluent Bit แทน (ข้อมูลน้อยกว่า)"
        if (Get-Service $ExpService -ErrorAction SilentlyContinue) {
            Stop-Service $ExpService -Force -ErrorAction SilentlyContinue
            sc.exe config $ExpService start= disabled | Out-Null
        }
        $UseExporter = $false
    }
}

# ---------------------------------------------------------------- โหมด pull: เปิด firewall ให้ central แล้วจบ
if ($Pull) {
    Say "3/3 Windows Firewall: เปิดรับ TCP $ExporterPort เฉพาะจาก $($CentralIps -join ', ')"
    $RuleName = "Central Monitor Exporter (pull)"
    Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol TCP -LocalPort $ExporterPort `
        -RemoteAddress $CentralIps -Action Allow -Profile Any | Out-Null
    # agent แบบ push ที่อาจติดตั้งไว้ก่อน (ส่งไม่ออกอยู่แล้ว) -> หยุดไว้ ไม่ให้พักข้อมูลค้าง
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue
        sc.exe config $ServiceName start= disabled | Out-Null
        Warn "หยุด service $ServiceName (โหมด push) ไว้แล้ว เพราะใช้โหมด pull แทน"
    }
    $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
             Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } | Select-Object -ExpandProperty IPAddress)
    Write-Host ""
    Write-Host "เสร็จแล้ว (โหมด pull) - service $ExpService ฟังที่ ${ListenAddr}:$ExporterPort" -ForegroundColor Green
    Write-Host "IP ของเครื่องนี้: $($ips -join ', ')"
    Write-Host "ที่ central: Settings > เครื่อง '$Name' > วิธีรับข้อมูล = central ดึงจาก Windows (pull)"
    Write-Host "            ช่อง windows_exporter = <IP ของเครื่องนี้>:$ExporterPort  (central ต้องต่อเข้ามาที่พอร์ตนี้ได้)"
    Write-Host "ทดสอบจาก central:  curl -s http://<IP ของเครื่องนี้>:$ExporterPort/metrics | head"
    Write-Host "หมายเหตุ: โหมด pull ได้ CPU / Memory / Disk / Network / Service / Process / Software แต่ไม่มี log (Event Log / ไฟล์ log)"
    exit 0
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
