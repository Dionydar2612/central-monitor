<#
.SYNOPSIS  ตรวจเครื่อง Windows ก่อนติดตั้ง Central Monitor Agent - อ่านอย่างเดียว ไม่ติดตั้ง / ไม่หยุด / ไม่แก้อะไร
.EXAMPLE   powershell -ExecutionPolicy Bypass -File .\precheck.ps1 -Central 172.27.2.22
           powershell -ExecutionPolicy Bypass -File .\precheck.ps1 -Central 172.27.2.22 -Pull
           powershell -ExecutionPolicy Bypass -File .\precheck.ps1 -Central 172.27.2.22 -Logs "C:\app\logs\*.log" -Channels "System,Application"
  ผล: [ OK ] ผ่าน · [WARN] ติดตั้งได้แต่มีผลกับของเดิม / ควรรู้ก่อน · [FAIL] ติดตั้งไม่ได้หรือจะชนของเดิม
  exit code: 0 = ผ่านหมด · 1 = มี WARN · 2 = มี FAIL
  สิ่งที่ตัวติดตั้งจะทำกับเครื่อง (เพื่อเทียบกับผลตรวจ):
    service ใหม่ cm-exporter (windows_exporter :9182) + cm-agent (Fluent Bit, ไม่มีในโหมด pull)
    scheduled task CentralMonitorInventory (15 นาที) / Probe (1 นาที) / NetSec (5 นาที) / Logs (1 นาที, เฉพาะ pull)
    โฟลเดอร์ C:\Program Files\central-monitor-agent + C:\ProgramData\central-monitor-agent (buffer ไม่เกิน 512 MB)
    โหมด pull: กฎ Windows Firewall เปิดรับ TCP 9182 เฉพาะ IP ของ central
    ถ้ามี windows_exporter ตัวเดิม: ถอน (MSI) / ปิด service เดิม เพราะใช้พอร์ต 9182 เดียวกัน
#>
param(
    [Parameter(Mandatory = $true)] [string] $Central,
    [int]    $Port = 4318,
    [switch] $Pull,
    [string] $Logs = "",
    [string] $Channels = "System,Application",
    [int]    $ExporterPort = 9182
)
$ErrorActionPreference = "SilentlyContinue"
$script:R = New-Object System.Collections.Generic.List[object]
function Res([string] $lv, [string] $what, [string] $detail) {
    $script:R.Add([pscustomobject]@{ Level = $lv; What = $what; Detail = $detail })
    $c = @{ OK = "Green"; WARN = "Yellow"; FAIL = "Red"; INFO = "Gray" }[$lv]
    Write-Host ("[{0,-4}] {1}" -f $lv, $what) -ForegroundColor $c -NoNewline
    if ($detail) { Write-Host " - $detail" } else { Write-Host "" }
}
function Proc($id) { $p = Get-Process -Id $id; if ($p) { "$($p.ProcessName) (PID $id)" } else { "PID $id" } }

Write-Host ""
Write-Host "Central Monitor - ตรวจก่อนติดตั้ง ($(if ($Pull) { 'โหมด pull' } else { 'โหมด push' })) · ไม่มีการแก้ไขเครื่อง" -ForegroundColor Cyan
Write-Host "เครื่อง: $env:COMPUTERNAME · central: $Central" -ForegroundColor Cyan
Write-Host ""

# ---------------- สิทธิ์ / ระบบ
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($admin) { Res OK "สิทธิ์ Administrator" "" } else { Res FAIL "สิทธิ์ Administrator" "ต้องเปิด PowerShell แบบ Run as Administrator ตอนติดตั้ง (ตรวจบางข้อได้ไม่ครบ)" }
$os = Get-CimInstance Win32_OperatingSystem
Res INFO "Windows" "$($os.Caption) build $($os.BuildNumber) · PowerShell $($PSVersionTable.PSVersion)"
if ($PSVersionTable.PSVersion.Major -lt 5) { Res FAIL "PowerShell" "ต้องเป็น 5.1 ขึ้นไป" }
if (-not [Environment]::Is64BitOperatingSystem) { Res FAIL "Windows 64-bit" "Fluent Bit / windows_exporter มีเฉพาะ 64-bit" }

# ---------------- พอร์ต 9182 / windows_exporter เดิม
$l = Get-NetTCPConnection -LocalPort $ExporterPort -State Listen | Select-Object -First 1
$ours = Get-Service cm-exporter
if ($l) {
    $who = Proc $l.OwningProcess
    if ($ours -and (Get-Process -Id $l.OwningProcess).ProcessName -like "windows_exporter*" -and $ours.Status -eq "Running") {
        Res OK "พอร์ต $ExporterPort" "ใช้โดย cm-exporter ของ Central Monitor เอง (ติดตั้งไว้แล้ว -> ติดตั้งซ้ำได้)"
    } else {
        Res FAIL "พอร์ต $ExporterPort ถูกใช้อยู่" "$who · ตัวติดตั้งจะหยุดตัวนี้ถ้าเป็น windows_exporter ไม่งั้นติดตั้ง cm-exporter ไม่ได้"
    }
} else { Res OK "พอร์ต $ExporterPort ว่าง" "" }
$msi = Get-ChildItem "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall" |
       Get-ItemProperty | Where-Object { $_.DisplayName -like "windows_exporter*" }
foreach ($m in $msi) { Res WARN "windows_exporter ติดตั้งจาก MSI อยู่แล้ว ($($m.DisplayVersion))" "ตัวติดตั้งจะถอนออก -> ระบบ monitor อื่นที่ดึงพอร์ต $ExporterPort จะดึงไม่ได้" }
$we = Get-Service windows_exporter
if ($we) { Res WARN "มี service windows_exporter ($($we.Status))" "ตัวติดตั้งจะหยุดและตั้งเป็น Disabled (ใช้พอร์ตเดียวกัน)" }
elseif (-not $msi) { Res OK "ไม่มี windows_exporter ตัวอื่น" "" }

# ---------------- ของ Central Monitor ที่มีอยู่แล้ว
$agent = Get-Service cm-agent
if ($agent) { Res INFO "มี cm-agent อยู่แล้ว ($($agent.Status))" $(if ($Pull) { "โหมด pull จะหยุดและปิด cm-agent" } else { "ติดตั้งซ้ำ = หยุดแล้วเริ่มใหม่ (ข้อมูลที่พักไว้ไม่หาย)" }) }
if ($ours) { Res INFO "มี cm-exporter อยู่แล้ว ($($ours.Status))" "ติดตั้งซ้ำ = แก้ค่าแล้วเริ่มใหม่" }
$tasks = Get-ScheduledTask -TaskName "CentralMonitor*"
if ($tasks) { Res INFO "scheduled task เดิม" (($tasks | ForEach-Object TaskName) -join ", ") }
$fb = Get-Service | Where-Object { $_.Name -ne "cm-agent" -and ($_.Name -match "fluent" -or $_.DisplayName -match "fluent") }
foreach ($s in $fb) { Res INFO "มี Fluent Bit ตัวอื่น: $($s.Name) ($($s.Status))" "ไม่ยุ่งกัน (คนละ service / คนละ config)" }

# ---------------- พื้นที่ / ทรัพยากร
$drv = Get-PSDrive -Name ($env:SystemDrive.TrimEnd(':'))
$freeGB = [math]::Round($drv.Free / 1GB, 1)
if ($freeGB -lt 1) { Res FAIL "พื้นที่ $env:SystemDrive" "ว่าง $freeGB GB (ต้องการ ~1 GB: โปรแกรม ~150 MB + buffer ไม่เกิน 512 MB)" }
elseif ($freeGB -lt 5) { Res WARN "พื้นที่ $env:SystemDrive" "ว่าง $freeGB GB (ใช้ได้ แต่ค่อนข้างน้อย)" }
else { Res OK "พื้นที่ $env:SystemDrive" "ว่าง $freeGB GB" }
$memPct = [math]::Round((1 - $os.FreePhysicalMemory / $os.TotalVisibleMemorySize) * 100)
$freeMB = [math]::Round($os.FreePhysicalMemory / 1024)
if ($memPct -ge 95 -or $freeMB -lt 300) { Res FAIL "RAM" "ใช้ไป $memPct% (ว่าง $freeMB MB) · agent ใช้ประมาณ 60-150 MB อาจทำให้โปรแกรมอื่นขาด RAM" }
elseif ($memPct -ge 85) { Res WARN "RAM" "ใช้ไป $memPct% (ว่าง $freeMB MB) · agent ใช้ประมาณ 60-150 MB" }
else { Res OK "RAM" "ใช้ไป $memPct% (ว่าง $freeMB MB)" }
$cpu = (Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average
if ($cpu -ge 90) { Res WARN "CPU" "ตอนนี้ $cpu% · agent ใช้ไม่ถึง 1-2% แต่ช่วงสคริปต์ทำงาน (ทุก 1 / 5 / 15 นาที) จะมีกระตุกสั้น ๆ" }
else { Res OK "CPU" "ตอนนี้ $cpu%" }
$np = @(Get-Process).Count
if ($np -gt 1500) { Res WARN "จำนวน process" "$np process · ตัวเก็บ process ของ windows_exporter จะใช้ CPU มากขึ้น" }
else { Res OK "จำนวน process" "$np" }

# ---------------- service ที่ตัวเก็บใช้
$wu = Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\wuauserv" -Name Start
if ($wu.Start -eq 4) { Res INFO "Windows Update ถูกปิด" "ตัวติดตั้งจะไม่ใช้ตัวเก็บ update (ไม่แตะ service นี้)" }
$fw = Get-Service MpsSvc
if ($Pull) {
    if ($fw -and $fw.Status -eq "Running") { Res OK "Windows Firewall ทำงาน" "จะเพิ่มกฎ 'Central Monitor Exporter (pull)' เปิดรับ TCP $ExporterPort เฉพาะจาก $Central" }
    else { Res WARN "Windows Firewall ไม่ได้ทำงาน" "สร้างกฎไม่ได้ -> ทุกเครื่องที่ network ปล่อยให้ถึง ต่อพอร์ต $ExporterPort ได้" }
}
$wmi = Get-Service Winmgmt
if (-not $wmi -or $wmi.Status -ne "Running") { Res FAIL "WMI (Winmgmt)" "ไม่ได้ทำงาน -> windows_exporter เก็บค่าบางส่วนไม่ได้" }

# ---------------- การเชื่อมต่อ
if (-not $Pull) {
    $t = Test-NetConnection -ComputerName $Central -Port $Port -WarningAction SilentlyContinue
    if ($t.TcpTestSucceeded) { Res OK "ต่อ central ${Central}:$Port" "" }
    else { Res FAIL "ต่อ central ${Central}:$Port ไม่ได้" "เครื่องนี้ส่งข้อมูลไม่ออก -> แก้ firewall ระหว่างวง หรือใช้โหมด pull (-Pull)" }
} else {
    $ips = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" }).IPAddress -join ", "
    Res INFO "โหมด pull" "central ต้องต่อเข้ามาที่ <IP เครื่องนี้>:$ExporterPort ได้ ($ips) · ทดสอบจาก central หลังติดตั้ง: curl http://<IP>:$ExporterPort/metrics"
}
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$haveFiles = (Get-ChildItem $dir -Filter "windows_exporter-*-amd64.exe") -and ($Pull -or (Get-ChildItem $dir -Filter "fluent-bit-*-win64.zip"))
if ($haveFiles) { Res OK "ไฟล์ติดตั้ง" "มีไฟล์วางข้างสคริปต์แล้ว ไม่ต้องใช้อินเทอร์เน็ต" }
else {
    $gh = Test-NetConnection github.com -Port 443 -WarningAction SilentlyContinue
    $fbn = if ($Pull) { $true } else { (Test-NetConnection packages.fluentbit.io -Port 443 -WarningAction SilentlyContinue).TcpTestSucceeded }
    if ($gh.TcpTestSucceeded -and $fbn) { Res OK "อินเทอร์เน็ต (ดาวน์โหลดตัวติดตั้ง)" "github.com$(if (-not $Pull) { ' / packages.fluentbit.io' })" }
    else { Res FAIL "ดาวน์โหลดตัวติดตั้งไม่ได้" "วาง windows_exporter-0.32.1-amd64.exe$(if (-not $Pull) { ' และ fluent-bit-5.1.5-win64.zip' }) ไว้ข้างสคริปต์" }
}

# ---------------- log ที่จะอ่าน
foreach ($ch in @($Channels -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
    $lg = Get-WinEvent -ListLog $ch
    if ($lg) { Res OK "Event Log '$ch'" "มีอยู่ ($([math]::Round($lg.FileSize / 1MB)) MB)" } else { Res WARN "Event Log '$ch'" "ไม่มี channel นี้ (ข้ามไป)" }
}
foreach ($p in @($Logs -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
    $f = @(Get-ChildItem -Path $p -File)
    if (-not $f) { Res WARN "ไฟล์ log $p" "ยังไม่พบไฟล์ (ใช้ได้ ถ้าไฟล์จะเกิดทีหลัง)"; continue }
    Res WARN "ไฟล์ log $p ($($f.Count) ไฟล์)" "Fluent Bit จะเปิดอ่านค้างไว้ ถ้าโปรแกรมหมุนไฟล์ด้วยการ rename / ลบ อาจ error ว่าไฟล์ถูกใช้งาน -> ทดสอบหมุน log หลังติดตั้ง"
}
$sec = Get-WinEvent -ListLog Security
if ($sec -and $sec.FileSize -gt 1GB) { Res WARN "Security log ใหญ่ ($([math]::Round($sec.FileSize / 1GB, 1)) GB)" "cm-netsec (ทุก 5 นาที) อ่าน logon ไม่สำเร็จ 24 ชม. อาจใช้ CPU ช่วงสั้น ๆ" }

# ---------------- โปรแกรมป้องกันไวรัส
$av = Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct
if ($av) { Res INFO "Antivirus" "$((($av | ForEach-Object displayName) | Select-Object -Unique) -join ', ') · ถ้าบล็อก fluent-bit.exe / windows_exporter.exe / สคริปต์ PowerShell ให้ยกเว้นโฟลเดอร์ central-monitor-agent" }

# ---------------- สรุป
$fail = @($R | Where-Object Level -eq "FAIL").Count
$warn = @($R | Where-Object Level -eq "WARN").Count
Write-Host ""
if ($fail) { Write-Host "สรุป: FAIL $fail ข้อ · WARN $warn ข้อ -> แก้ข้อ FAIL ก่อนติดตั้ง" -ForegroundColor Red; exit 2 }
if ($warn) { Write-Host "สรุป: ติดตั้งได้ · WARN $warn ข้อ (อ่านก่อนว่ายอมรับได้ไหม)" -ForegroundColor Yellow; exit 1 }
Write-Host "สรุป: ผ่านทุกข้อ ติดตั้งได้ ไม่ชนกับของเดิม" -ForegroundColor Green
exit 0
