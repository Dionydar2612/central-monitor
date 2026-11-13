<#
.SYNOPSIS  ถอน Central Monitor Agent (Fluent Bit) ออกจากเครื่อง Windows
.EXAMPLE   powershell -ExecutionPolicy Bypass -File .\uninstall-fluentbit.ps1            # เก็บข้อมูล/ตำแหน่งที่อ่านไว้
           powershell -ExecutionPolicy Bypass -File .\uninstall-fluentbit.ps1 -Purge     # ลบทั้งหมด
           powershell -ExecutionPolicy Bypass -File .\uninstall-fluentbit.ps1 -KeepExporter  # ไม่ถอน windows_exporter
#>
param([switch] $Purge, [switch] $KeepExporter)
$ErrorActionPreference = "Stop"
$ServiceName = "cm-agent"
# รายการโปรแกรม / พอร์ต
if (Get-ScheduledTask -TaskName "CentralMonitorProbe" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "CentralMonitorProbe" -Confirm:$false
    Write-Host "ลบ scheduled task CentralMonitorProbe แล้ว"
}
# log ของโหมด pull
if (Get-ScheduledTask -TaskName "CentralMonitorLogs" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "CentralMonitorLogs" -Confirm:$false
    Write-Host "ลบ scheduled task CentralMonitorLogs แล้ว"
}
if (Get-ScheduledTask -TaskName "CentralMonitorInventory" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "CentralMonitorInventory" -Confirm:$false
    Write-Host "ลบ scheduled task CentralMonitorInventory แล้ว"
}
# firewall ของโหมด pull (เปิดรับ 9182 จาก central)
Get-NetFirewallRule -DisplayName "Central Monitor Exporter (pull)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
# windows_exporter ที่ติดตั้งโดยสคริปต์นี้ (service cm-exporter) ถูกลบพร้อม agent เสมอ
if (Get-Service -Name "cm-exporter" -ErrorAction SilentlyContinue) {
    Stop-Service "cm-exporter" -Force -ErrorAction SilentlyContinue
    sc.exe delete "cm-exporter" | Out-Null
    Write-Host "ลบ service cm-exporter แล้ว"
}
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue
    sc.exe delete $ServiceName | Out-Null
    Write-Host "ลบ service $ServiceName แล้ว"
}
Remove-Item (Join-Path $env:ProgramFiles "central-monitor-agent") -Recurse -Force -ErrorAction SilentlyContinue
if ($Purge) {
    Remove-Item (Join-Path $env:ProgramData "central-monitor-agent") -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "ลบข้อมูลใน ProgramData แล้ว"
} else {
    Write-Host "เก็บ $env:ProgramData\central-monitor-agent ไว้ (ติดตั้งใหม่จะอ่านต่อจากเดิม) ใช้ -Purge เพื่อลบ"
}
if (-not $KeepExporter) {
    $keys = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
            "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    foreach ($p in @(Get-ItemProperty $keys -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like "windows_exporter*" })) {
        if ($p.PSChildName -match '^\{[0-9A-Fa-f-]+\}$') {
            Start-Process msiexec.exe -ArgumentList @("/x", $p.PSChildName, "/qn", "/norestart") -Wait | Out-Null
            Write-Host "ถอน $($p.DisplayName) $($p.DisplayVersion) แล้ว"
        }
    }
}
