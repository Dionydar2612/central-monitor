# Central Monitor - ติดตั้ง agent บนเครื่อง Windows (สร้างจากหน้า Settings · ใช้ได้ภายใน 24 ชม.)
# รันผ่านคำสั่งบรรทัดเดียวใน PowerShell แบบ Run as Administrator
& {
$ErrorActionPreference = 'Stop'
$Central = __CENTRAL__
$EnrollPort = '__ENROLL_PORT__'
$AgentPort = '__AGENT_PORT__'
$Code = __CODE__
$Thumb = __THUMB__
$Web = __WEB__
$FbVersion = __FB_VERSION__
$ExpVersion = __EXP_VERSION__
$Base = "https://${Central}:$EnrollPort"

function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
          [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'ต้องรันใน PowerShell แบบ Run as Administrator' }

# ตรวจใบรับรองของ central ด้วย thumbprint (ไม่ต้องติดตั้ง CA ลงเครื่อง)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
[Net.ServicePointManager]::ServerCertificateValidationCallback = [ScriptBlock]::Create(
    "param(`$s,`$c) `$c.GetCertHashString() -eq '$Thumb'")
$wc = New-Object Net.WebClient
$wc.Proxy = $null                 # central อยู่ในวง network ไม่ผ่าน proxy
$wc.Encoding = [Text.Encoding]::UTF8

Say "ลงทะเบียนเครื่อง $Web กับ central $Central"
$wc.Headers['Content-Type'] = 'application/json'
try {
    $resp = $wc.UploadString("$Base/agent/register", (@{ code = $Code; host = $env:COMPUTERNAME } | ConvertTo-Json))
} catch [Net.WebException] {
    $msg = $_.Exception.Message
    if ($_.Exception.Response) {
        $sr = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream(), [Text.Encoding]::UTF8)
        $msg = $sr.ReadToEnd()
    }
    throw "ลงทะเบียนไม่สำเร็จ: $msg"
}
$r = $resp | ConvertFrom-Json

$Setup = Join-Path $env:ProgramFiles 'central-monitor-agent\setup'
New-Item -ItemType Directory -Force -Path $Setup | Out-Null
foreach ($f in 'install-fluentbit.ps1', 'uninstall-fluentbit.ps1', 'cm-agent-sync.ps1') {
    $wc.DownloadFile("$Base/agent/files/$f", (Join-Path $Setup $f))
}
[IO.File]::WriteAllText((Join-Path $Setup 'central-ca.pem'), $r.ca_pem)

# ไฟล์ใหญ่: โหลดจาก central (เครื่องไม่ต้องออกอินเทอร์เน็ต) ไม่ได้ -> ให้สคริปต์ติดตั้งโหลดจากอินเทอร์เน็ตเอง
$zip = Join-Path $Setup "fluent-bit-$FbVersion-win64.zip"
$exe = Join-Path $Setup "windows_exporter-$ExpVersion-amd64.exe"
foreach ($p in @($zip, $exe)) {
    if ((Test-Path $p) -and (Get-Item $p).Length -gt 3MB) { continue }
    Say "ดาวน์โหลด $(Split-Path $p -Leaf) จาก central"
    try { $wc.DownloadFile("$Base/agent/files/$(Split-Path $p -Leaf)", $p) }
    catch { Write-Host "[WARN] central ไม่มีไฟล์นี้ ($($_.Exception.Message)) -> โหลดจากอินเทอร์เน็ตแทน" -ForegroundColor Yellow
            Remove-Item $p -ErrorAction SilentlyContinue }
}

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$a = @{
    Central = $Central; Port = [int]$AgentPort; Token = $r.token; Name = $r.name
    Logs = ($r.logs -join ';'); Channels = ($r.channels -join ',')
    Version = $FbVersion; ExporterVersion = $ExpVersion
    EnrollPort = [int]$EnrollPort; Thumbprint = $Thumb; ConfigHash = $r.hash
}
if (Test-Path $zip) { $a.ZipPath = $zip }
if (Test-Path $exe) { $a.ExporterExe = $exe }
& (Join-Path $Setup 'install-fluentbit.ps1') @a
}
