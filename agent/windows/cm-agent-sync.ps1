<#
.SYNOPSIS
  Central Monitor: ถามค่าตั้งล่าสุดของเครื่องนี้จาก central ทุก 5 นาที (scheduled task CentralMonitorAgentSync รันเป็น SYSTEM)
  ค่าเปลี่ยน (แก้ไฟล์ log / Event channel ในหน้า Settings) -> เขียน config ของ Fluent Bit ใหม่แล้วเริ่ม agent ใหม่
  token ถูกยกเลิก (Revoke ในหน้า Settings) -> บันทึกไว้ใน sync.log ไม่ทำอะไรต่อ
  log: C:\ProgramData\central-monitor-agent\sync.log
#>
$ErrorActionPreference = "Stop"
$DataDir = Join-Path $env:ProgramData "central-monitor-agent"
$StateFile = Join-Path $DataDir "agent.json"
$Log = Join-Path $DataDir "sync.log"

function Log($m) {
    $line = "$((Get-Date).ToString('s')) $m"
    if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 512KB) { Move-Item $Log "$Log.old" -Force }
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

try {
    $st = Get-Content $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $st.enroll_port -or -not $st.thumbprint) { exit 0 }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    [Net.ServicePointManager]::ServerCertificateValidationCallback = [ScriptBlock]::Create(
        "param(`$s,`$c) `$c.GetCertHashString() -eq '$($st.thumbprint)'")
    $wc = New-Object Net.WebClient
    $wc.Proxy = $null
    $wc.Encoding = [Text.Encoding]::UTF8
    $wc.Headers["Authorization"] = "Bearer $($st.token)"
    try {
        $cfg = $wc.DownloadString("https://$($st.central):$($st.enroll_port)/agent/config") | ConvertFrom-Json
    } catch [Net.WebException] {
        $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
        if ($code -eq 401) { Log "token ถูกยกเลิกที่ central (Revoke) - ติดตั้งใหม่ด้วยคำสั่งจากหน้า Settings"; exit 0 }
        Log "ติดต่อ central ไม่ได้: $($_.Exception.Message)"
        exit 0
    }
    if ($cfg.hash -and $cfg.hash -ne $st.config_hash) {
        Log "ค่าตั้งเปลี่ยน ($($st.config_hash) -> $($cfg.hash)) logs=$($cfg.logs -join ';') channels=$($cfg.channels -join ',')"
        $install = Join-Path $PSScriptRoot "install-fluentbit.ps1"
        $out = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $install -ConfigOnly `
            -Logs ($cfg.logs -join ";") -Channels ($cfg.channels -join ",") -ConfigHash $cfg.hash 2>&1
        $out | Select-Object -Last 5 | ForEach-Object { Log "   $_" }
        if ($LASTEXITCODE -ne 0) { Log "อัปเดตค่าตั้งไม่สำเร็จ (exit $LASTEXITCODE)" }
    }
} catch {
    Log "ERROR $($_.Exception.Message)"
}
