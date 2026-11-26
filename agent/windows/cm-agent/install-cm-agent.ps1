# =====================================================================
# install-cm-agent.ps1 - Central Monitor cm-agent for Windows (install / update)
# Run it through install-cm-agent.bat (asks for administrator rights itself),
# or let central install it from the web page (Settings > machine > Install cm-agent).
#
# What it installs (no new user account - both services run as LocalSystem):
#   - service cm-exporter : windows_exporter, listens on 127.0.0.1:9182 only (no firewall rule)
#   - service cm-agent    : Fluent Bit, reads windows_exporter + Event Log + log files
#                           and sends them to central over HTTPS (port 4318) with a token
#   - task CentralMonitorInventory (only when Software=1): installed programs + listening ports
# Mode=pull (this machine cannot reach central, but central can reach it):
#   no Fluent Bit / token; cm-exporter listens on 0.0.0.0:ExporterPort with a firewall rule for the
#   central IP only, central reads it; logs = task CentralMonitorLogs (cm-logs.ps1 -> textfile)
# Folders: %ProgramFiles%\central-monitor-agent (programs)  %ProgramData%\central-monitor-agent (data)
#
# Every interval / feature comes from cm-agent-config.txt next to this file, so the load on
# the machine is decided BEFORE the install (see the comments in that file).
# Argument: unattended  = called by central (writes setup-result.json, no prompts)
# ASCII only (Windows PowerShell 5.1 reads files without a BOM as ANSI).
# =====================================================================
param([string[]]$Options = @())

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Here        = Split-Path -Parent $MyInvocation.MyCommand.Path
$Version     = '2.0.0'
$Svc         = 'cm-agent'
$ExpSvc      = 'cm-exporter'
$InvTask     = 'CentralMonitorInventory'
$InstallDir  = Join-Path $env:ProgramFiles 'central-monitor-agent'
$DataDir     = Join-Path $env:ProgramData 'central-monitor-agent'
$TextDir     = Join-Path $DataDir 'textfile'
$ExpPort     = 9182
$script:Warnings = 0

$LogDir  = Join-Path $Here 'logs'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogFile = Join-Path $LogDir ('setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
Get-ChildItem -Path $LogDir -Filter 'setup-*.log' | Sort-Object Name -Descending | Select-Object -Skip 4 | Remove-Item -Force -ErrorAction SilentlyContinue

function Log([string]$m)  { Add-Content -Path $LogFile -Value ((Get-Date -Format 'HH:mm:ss') + ' ' + $m) -Encoding UTF8 }
function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c; Log $m }
function Ok([string]$m)   { Say "[ OK ] $m" 'Green' }
function Info([string]$m) { Say "[INFO] $m" 'Gray' }
function Warn([string]$m) { Say "[WARN] $m" 'Yellow'; $script:Warnings++ }
function Step([string]$m) { Say ''; Say "--- $m" 'Cyan' }

# ---------------------------------------------------------------- config
function Read-Config {
    $cfg = [ordered]@{
        Name = ''; CentralIP = ''; Port = '4318'; Token = ''; SkipCertCheck = '0'; Mode = 'push'; ExporterPort = '9182'
        Metrics = '1'; Logs = '1'; Software = '1'; Processes = '0'
        Channels = 'System,Application'; LogPaths = ''
        MetricsInterval = '60'; HeartbeatInterval = '60'; EventInterval = '30'; LogRefresh = '60'
        Flush = '15'; InventoryMinutes = '60'
    }
    $path = Join-Path $Here 'cm-agent-config.txt'
    if (-not (Test-Path -LiteralPath $path)) { throw "cm-agent-config.txt not found next to this script ($path)" }
    foreach ($line in (Get-Content -LiteralPath $path)) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
        $k = $t.Substring(0, $t.IndexOf('=')).Trim()
        $v = $t.Substring($t.IndexOf('=') + 1).Trim()
        if ($cfg.Contains($k)) { $cfg[$k] = $v }
    }
    return $cfg
}

function Num($cfg, [string]$key, [int]$min, [int]$max) {
    $n = 0
    if (-not [int]::TryParse([string]$cfg[$key], [ref]$n)) { throw "$key must be a number ($min-$max)" }
    if ($n -lt $min) { Warn "$key=$n is too small - using $min"; $n = $min }
    if ($n -gt $max) { Warn "$key=$n is too large - using $max"; $n = $max }
    return $n
}
function Flag($cfg, [string]$key) { return @('1', 'yes', 'true', 'on') -contains ([string]$cfg[$key]).ToLower() }

function Set-ServiceBin([string]$name, [string]$bin, [string]$display, [string]$desc) {
    if (Get-Service -Name $name -ErrorAction SilentlyContinue) {
        Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\$name" -Name ImagePath -Value $bin
        Set-Service -Name $name -StartupType Automatic
    } else {
        New-Service -Name $name -BinaryPathName $bin -DisplayName $display -Description $desc -StartupType Automatic | Out-Null
    }
    & sc.exe failure $name reset= 86400 actions= restart/10000/restart/30000/restart/60000 | Out-Null
}

function Stop-Svc([string]$name) {
    $s = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($s -and $s.Status -ne 'Stopped') { Stop-Service -Name $name -Force -ErrorAction SilentlyContinue; Start-Sleep 2 }
}

# find a file: next to this script > Downloads > already installed copy
function Find-File([string[]]$patterns, [string]$installed) {
    foreach ($dir in @($Here, (Join-Path $env:USERPROFILE 'Downloads'))) {
        foreach ($p in $patterns) {
            $f = Get-ChildItem -Path $dir -Filter $p -File -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
            if ($f) { return $f.FullName }
        }
    }
    if ($installed -and (Test-Path -LiteralPath $installed)) { return $installed }
    return $null
}

function Try-Download([string]$url, [string]$dest) {
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Info "downloading $url"
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 300
        return $dest
    } catch { Warn "download failed: $($_.Exception.Message)"; return $null }
}

# ---------------------------------------------------------------- main
$exitCode = 0; $errMsg = ''; $features = @{}; $HbInt = 60; $MetricsInt = 60; $Pull = $false
try {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
              [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { throw 'run as administrator (use install-cm-agent.bat)' }

    Step '1/7 Read cm-agent-config.txt'
    $cfg = Read-Config
    $Name = ([string]$cfg.Name).Trim()
    if ($Name -notmatch '^[a-z0-9][a-z0-9-]{0,39}$') { throw "Name '$Name' is invalid (a-z 0-9 - , same name as in central)" }
    $Central = ([string]$cfg.CentralIP).Trim()
    if (-not $Central -or $Central -eq 'auto') { throw 'CentralIP is empty - type the IP / name of central' }
    $Port = Num $cfg 'Port' 1 65535
    $Pull = ([string]$cfg.Mode).Trim().ToLower() -eq 'pull'
    $Token = ([string]$cfg.Token).Trim()
    if (-not $Token) {
        # run again from the zip: Token was cleared after the first install -> reuse the one in the installed config
        $old = Join-Path $DataDir 'fluent-bit.conf'
        if (Test-Path -LiteralPath $old) {
            $m = Select-String -LiteralPath $old -Pattern 'Authorization Bearer (\S+)' | Select-Object -First 1
            if ($m) { $Token = $m.Matches[0].Groups[1].Value; Info 'Token: reusing the one of the installed agent' }
        }
    }
    if (-not $Pull -and $Token.Length -lt 16) { throw 'Token is missing (copy it from central or install from the web page)' }
    $UseMetrics  = Flag $cfg 'Metrics'
    if ($Pull) {
        $ExpPort = Num $cfg 'ExporterPort' 1024 65535
        if (-not $UseMetrics) { Warn 'Mode=pull: central reads everything from windows_exporter - Metrics turned on'; $UseMetrics = $true }
        try { $CentralIps = @([System.Net.Dns]::GetHostAddresses($Central) | Where-Object { $_.AddressFamily -eq 'InterNetwork' } | ForEach-Object { $_.IPAddressToString }) }
        catch { $CentralIps = @() }
        if (-not $CentralIps) { throw "cannot resolve '$Central' - use the IP of central" }
    }
    $UseLogs     = Flag $cfg 'Logs'
    $UseSoftware = (Flag $cfg 'Software')
    $UseProc     = (Flag $cfg 'Processes')
    if ($UseSoftware -and -not $UseMetrics) { Warn 'Software=1 needs Metrics=1 (sent through windows_exporter) - Software turned off'; $UseSoftware = $false }
    if ($UseProc -and -not $UseMetrics) { $UseProc = $false }
    $MetricsInt = Num $cfg 'MetricsInterval' 15 600
    $HbInt      = Num $cfg 'HeartbeatInterval' 15 600
    $EvInt      = Num $cfg 'EventInterval' 5 600
    $LogRefresh = Num $cfg 'LogRefresh' 10 600
    $Flush      = Num $cfg 'Flush' 1 120
    $InvMin     = Num $cfg 'InventoryMinutes' 15 1440
    $Channels   = (@(([string]$cfg.Channels) -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })) -join ','
    $LogPaths   = @(([string]$cfg.LogPaths) -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $features = [ordered]@{ mode = $(if ($Pull) { 'pull' } else { 'push' }); metrics = $UseMetrics; logs = $UseLogs; software = $UseSoftware; processes = $UseProc
                            metrics_interval = $MetricsInt; heartbeat_interval = $HbInt; event_interval = $EvInt
                            log_refresh = $LogRefresh; flush = $Flush; inventory_minutes = $InvMin }
    if ($Pull) { Ok "machine '$Name' <- central $Central reads :$ExpPort (pull mode)" } else { Ok "machine '$Name' -> ${Central}:$Port" }
    Info ("metrics=$UseMetrics (every ${MetricsInt}s, processes=$UseProc)  logs=$UseLogs (events every ${EvInt}s, files every ${LogRefresh}s)" +
          "  software=$UseSoftware (every $InvMin min)  heartbeat every ${HbInt}s  flush ${Flush}s")

    Step '2/7 Test the connection to central'
    if ($Pull) { Info 'pull mode - central connects to this machine, nothing to test here' } else {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $iar = $c.BeginConnect($Central, $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(5000) -and $c.Connected) { Ok "${Central}:$Port reachable" }
        else { Warn "${Central}:$Port not reachable now (firewall?) - installing anyway, data is kept on disk and sent later" }
        $c.Close()
    } catch { Warn "${Central}:$Port not reachable: $($_.Exception.Message)" }
    }

    Step '3/7 Programs (Fluent Bit / windows_exporter)'
    New-Item -ItemType Directory -Force -Path $InstallDir, $DataDir, (Join-Path $DataDir 'buffer'), $TextDir | Out-Null
    $FluentExe = Join-Path $InstallDir 'bin\fluent-bit.exe'
    $zip = $null
    if ($Pull) {
        if (Get-Service $Svc -ErrorAction SilentlyContinue) { Stop-Svc $Svc; & sc.exe delete $Svc | Out-Null; Info 'pull mode - removed service cm-agent (push)' }
    } else { $zip = Find-File @('fluent-bit-*-win64.zip') $null }
    if (-not $Pull -and -not $zip -and -not (Test-Path -LiteralPath $FluentExe)) {
        $zip = Try-Download 'https://packages.fluentbit.io/windows/fluent-bit-5.1.5-win64.zip' (Join-Path $env:TEMP 'fluent-bit-5.1.5-win64.zip')
    }
    Stop-Svc $Svc
    if ($zip) {
        $tmp = Join-Path $env:TEMP ('cm-agent-' + [guid]::NewGuid())
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $exe = Get-ChildItem -Path $tmp -Recurse -Filter 'fluent-bit.exe' | Select-Object -First 1
        if (-not $exe) { throw "fluent-bit.exe not found in $zip" }
        Copy-Item -Path (Join-Path $exe.Directory.Parent.FullName '*') -Destination $InstallDir -Recurse -Force
        Remove-Item $tmp -Recurse -Force
        Ok "Fluent Bit from $(Split-Path -Leaf $zip)"
    } elseif ($Pull) {
        Info 'pull mode - Fluent Bit not needed'
    } elseif (Test-Path -LiteralPath $FluentExe) {
        Ok 'Fluent Bit: keep the installed copy'
    } else {
        throw 'Fluent Bit not found: put fluent-bit-<version>-win64.zip next to this script (or in data/agent-bin on central)'
    }

    $ExpExe = Join-Path $InstallDir 'windows_exporter.exe'
    if ($UseMetrics) {
        $src = Find-File @('windows_exporter-*-amd64.exe') $null
        if (-not $src -and -not (Test-Path -LiteralPath $ExpExe)) {
            $src = Try-Download 'https://github.com/prometheus-community/windows_exporter/releases/download/v0.32.1/windows_exporter-0.32.1-amd64.exe' (Join-Path $env:TEMP 'windows_exporter-0.32.1-amd64.exe')
        }
        Stop-Svc $ExpSvc
        if ($src) {
            if ((Get-Item $src).Length -lt 5MB) { throw "$src is too small (incomplete download?)" }
            Copy-Item $src $ExpExe -Force
            Ok "windows_exporter from $(Split-Path -Leaf $src)"
        } elseif (Test-Path -LiteralPath $ExpExe) {
            Ok 'windows_exporter: keep the installed copy'
        } else {
            throw 'windows_exporter not found: put windows_exporter-<version>-amd64.exe next to this script (or in data/agent-bin on central)'
        }
        Get-ChildItem -LiteralPath $InstallDir -File | Unblock-File -ErrorAction SilentlyContinue
    }

    $Listen = if ($Pull) { '0.0.0.0' } else { '127.0.0.1' }
    $UseText = $UseSoftware -or ($Pull -and $UseLogs)
    Step "4/7 windows_exporter (service cm-exporter, ${Listen}:$ExpPort)"
    if ($UseMetrics) {
        $busy = Get-NetTCPConnection -LocalPort $ExpPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($busy) {
            $pn = (Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue).ProcessName
            throw "port $ExpPort is used by $pn (pid $($busy.OwningProcess)) - stop it first"
        }
        # light collector set first; textfile only for Software, process only for Processes
        $base = 'cpu,cpu_info,os,memory,logical_disk,physical_disk,net,tcp,system,service'
        $sets = @($base, 'cpu,os,memory,logical_disk,net,tcp,system,service', 'cpu,os,memory,logical_disk,net,system')
        $chosen = $null; $why = ''
        foreach ($s in $sets) {
            $set = $s
            if ($UseProc) { $set += ',process' }
            if ($UseText) { $set += ',textfile' }
            $eargs = @("--collectors.enabled=$set")
            if ($UseText) { $eargs += "--collector.textfile.directories=`"$TextDir`"" }
            $out = Join-Path $env:TEMP 'cm-exporter-test.out'; $err = Join-Path $env:TEMP 'cm-exporter-test.err'
            Remove-Item $out, $err -ErrorAction SilentlyContinue
            $p = Start-Process -FilePath $ExpExe -ArgumentList ($eargs + '--scrape.stdout') -NoNewWindow -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
            $null = $p.Handle
            if (-not $p.WaitForExit(120000)) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; $why = 'timeout'; continue }
            $text = if (Test-Path $out) { Get-Content $out -Raw -ErrorAction SilentlyContinue } else { '' }
            if ($p.ExitCode -eq 0 -and $text -match 'windows_exporter_build_info') { $chosen = $eargs; break }
            $why = 'exit ' + $p.ExitCode + ' ' + ((Get-Content $err -Tail 3 -ErrorAction SilentlyContinue) -join ' | ')
            Warn "collector set not usable: $why"
        }
        if (-not $chosen) { throw "windows_exporter cannot collect on this machine ($why)" }
        $expLog = Join-Path $DataDir 'windows_exporter.log'
        $bin = "`"$ExpExe`" --web.listen-address=${Listen}:$ExpPort " + ($chosen -join ' ') +
               " --log.file=`"$expLog`" --log.level=warn"
        Set-ServiceBin $ExpSvc $bin 'Central Monitor Exporter (windows_exporter)' 'Machine metrics for Central Monitor (127.0.0.1 only)'
        Start-Service $ExpSvc
        Ok "collectors: $(($chosen[0]) -replace '--collectors.enabled=', '')"
        $Rule = 'Central Monitor Exporter (pull)'
        Get-NetFirewallRule -DisplayName $Rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
        if ($Pull) {
            try {
                New-NetFirewallRule -DisplayName $Rule -Direction Inbound -Protocol TCP -LocalPort $ExpPort -RemoteAddress $CentralIps `
                    -Action Allow -Profile Any -ErrorAction Stop | Out-Null
                Ok "firewall: TCP $ExpPort only from $($CentralIps -join ', ')"
            } catch { Warn "firewall rule not created ($($_.Exception.Message)) - allow TCP $ExpPort from $($CentralIps -join ', ') yourself" }
        }
    } else {
        if (Get-Service $ExpSvc -ErrorAction SilentlyContinue) {
            Stop-Svc $ExpSvc; & sc.exe delete $ExpSvc | Out-Null; Info 'Metrics=0 - removed service cm-exporter'
        } else { Info 'Metrics=0 - skipped' }
    }

    Step '5/7 Software inventory task'
    $InvScript = Join-Path $InstallDir 'cm-inventory.ps1'
    if ($UseSoftware) {
        Copy-Item (Join-Path $Here 'cm-inventory.ps1') $InvScript -Force
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $InvScript
        $act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$InvScript`""
        $trg = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes $InvMin) -RepetitionDuration (New-TimeSpan -Days 3650)
        $pr  = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        $set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -StartWhenAvailable -MultipleInstances IgnoreNew -Priority 7
        Register-ScheduledTask -TaskName $InvTask -Action $act -Trigger $trg -Principal $pr -Settings $set -Force | Out-Null
        Ok "task $InvTask every $InvMin min (low priority)"
    } else {
        if (Get-ScheduledTask -TaskName $InvTask -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $InvTask -Confirm:$false }
        Remove-Item (Join-Path $TextDir 'cm_inventory.prom') -Force -ErrorAction SilentlyContinue
        Info 'Software=0 - skipped'
    }

    $LogsTask = 'CentralMonitorLogs'
    if ($Pull -and $UseLogs) {
        $LogsScript = Join-Path $InstallDir 'cm-logs.ps1'
        Copy-Item (Join-Path $Here 'cm-logs.ps1') $LogsScript -Force
        $lc = [ordered]@{ channels = @($Channels -split ',' | Where-Object { $_ }); logs = @($LogPaths) }
        [IO.File]::WriteAllText((Join-Path $DataDir 'cm-logs.json'), ($lc | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $LogsScript
        $everyMin = [Math]::Max(1, [int][Math]::Ceiling($EvInt / 60))
        $act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LogsScript`""
        $trg = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $everyMin) -RepetitionDuration (New-TimeSpan -Days 3650)
        $pr  = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        $set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -StartWhenAvailable -MultipleInstances IgnoreNew -Priority 7
        Register-ScheduledTask -TaskName $LogsTask -Action $act -Trigger $trg -Principal $pr -Settings $set -Force | Out-Null
        Ok "logs: task $LogsTask every $everyMin min -> central reads them with the metrics"
    } elseif (Get-ScheduledTask -TaskName $LogsTask -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $LogsTask -Confirm:$false
        Remove-Item (Join-Path $TextDir 'cm_logs.prom') -Force -ErrorAction SilentlyContinue
    }

    if ($Pull) {
        & icacls.exe $DataDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
        Step '6/7 Fluent Bit - not used in pull mode'
        Step '7/7 Check'
        $ok = $false
        foreach ($i in 1..10) {
            try { $wc = New-Object Net.WebClient; $wc.Proxy = $null; if ($wc.DownloadString("http://127.0.0.1:$ExpPort/metrics") -match 'windows_exporter_build_info') { $ok = $true; break } } catch { }
            Start-Sleep 2
        }
        if (-not $ok) { throw "cm-exporter does not answer on port $ExpPort" }
        $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -ExpandProperty IPAddress)
        Ok "cm-exporter answers on ${Listen}:$ExpPort (this machine: $($ips -join ', '))"
    } else {
    Step '6/7 Fluent Bit config'
    $CaDest = Join-Path $DataDir 'central-ca.pem'
    $skip = Flag $cfg 'SkipCertCheck'
    $tls = "    Tls.verify       Off"
    if (-not $skip) {
        $ca = Join-Path $Here 'central-ca.pem'
        if (-not (Test-Path -LiteralPath $ca)) { throw 'central-ca.pem not found next to this script (or set SkipCertCheck=1)' }
        Copy-Item $ca $CaDest -Force
        $tls = "    Tls.verify       On`r`n    Tls.ca_file      $CaDest"
        # connect by IP but verify the name in the certificate when the IP is not in it
        $cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2($CaDest)
        $san = ($cert.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.17' } | ForEach-Object { $_.Format($false) }) -join ', '
        if ($san -notmatch [regex]::Escape($Central)) {
            $dns = [regex]::Match($san, 'DNS Name=([^,\s]+)').Groups[1].Value
            if ($dns) { $tls += "`r`n    Tls.vhost        $dns"; Info "certificate does not list $Central - verifying the name $dns instead" }
            else { Warn "certificate does not list $Central - run scripts/make-cert.sh agent <IP> on central" }
        }
    }
    $lua = Get-Content -LiteralPath (Join-Path $Here 'cm-agent.lua') -Raw
    [IO.File]::WriteAllText((Join-Path $DataDir 'cm-agent.lua'), $lua.Replace('__CM_WEB__', $Name), (New-Object Text.UTF8Encoding($false)))
    $LuaFile = Join-Path $DataDir 'cm-agent.lua'
    function Output([string]$match, [string]$source, [string]$extra) {
        return "[OUTPUT]`r`n    Name             opentelemetry`r`n    Match            $match`r`n    Host             $Central`r`n" +
               "    Port             $Port`r`n    Logs_uri         /v1/logs`r`n    Metrics_uri      /v1/metrics`r`n" +
               "    Header           Authorization Bearer $Token`r`n    Header           X-CM-Web $Name`r`n" +
               "    Header           X-CM-Source $source`r`n    Tls              On`r`n$tls`r`n" +
               "    Retry_Limit      no_limits`r`n    storage.total_limit_size 256M`r`n$extra`r`n"
    }
    $conf = "# written by install-cm-agent.ps1 (Central Monitor) - run the installer again to change it`r`n" +
            "[SERVICE]`r`n    Flush            $Flush`r`n    Log_Level        warn`r`n    Log_File         $DataDir\fluent-bit.log`r`n" +
            "    storage.path     $DataDir\buffer`r`n    storage.sync     normal`r`n    storage.backlog.mem_limit 8M`r`n    storage.max_chunks_up 32`r`n`r`n" +
            "[INPUT]`r`n    Name             fluentbit_metrics`r`n    Tag              cm.hb`r`n    Scrape_Interval  $HbInt`r`n`r`n"
    if ($UseLogs -and $Channels) {
        $conf += "[INPUT]`r`n    Name             winevtlog`r`n    Tag              cm.event`r`n    Channels         $Channels`r`n" +
                 "    Interval_Sec     $EvInt`r`n    Read_Existing_Events Off`r`n    DB               $DataDir\winevtlog.sqlite`r`n" +
                 "    Mem_Buf_Limit    4MB`r`n    storage.type     filesystem`r`n`r`n" +
                 "[FILTER]`r`n    Name             lua`r`n    Match            cm.event`r`n    script           $LuaFile`r`n    call             cm_event`r`n`r`n"
    }
    if ($UseLogs -and $LogPaths.Count -gt 0) {
        $conf += "[INPUT]`r`n    Name             tail`r`n    Tag              cm.file`r`n    Path             $($LogPaths -join ',')`r`n" +
                 "    Path_Key         cm_path`r`n    DB               $DataDir\tail.sqlite`r`n    Read_from_Head   Off`r`n" +
                 "    Skip_Long_Lines  On`r`n    Buffer_Max_Size  256k`r`n    Mem_Buf_Limit    8MB`r`n    Refresh_Interval $LogRefresh`r`n" +
                 "    storage.type     filesystem`r`n`r`n" +
                 "[FILTER]`r`n    Name             lua`r`n    Match            cm.file`r`n    script           $LuaFile`r`n    call             cm_file`r`n`r`n"
    }
    if ($UseMetrics) {
        $conf += "[INPUT]`r`n    Name             prometheus_scrape`r`n    Tag              cm.win`r`n    Host             127.0.0.1`r`n" +
                 "    Port             $ExpPort`r`n    Metrics_Path     /metrics`r`n    Scrape_Interval  ${MetricsInt}s`r`n`r`n"
    }
    if ($UseLogs -and $Channels) { $conf += (Output 'cm.event' 'eventlog' '') }
    if ($UseLogs -and $LogPaths.Count -gt 0) { $conf += (Output 'cm.file' 'application' '') }
    $conf += (Output 'cm.hb' 'agent' "    add_label        cm_web $Name")
    if ($UseMetrics) { $conf += (Output 'cm.win' 'metrics' "    add_label        cm_web $Name") }
    $ConfFile = Join-Path $DataDir 'fluent-bit.conf'
    [IO.File]::WriteAllText($ConfFile, $conf, (New-Object Text.UTF8Encoding($false)))
    # the config holds the token -> SYSTEM + Administrators only
    & icacls.exe $DataDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    $ErrorActionPreference = 'Continue'
    $dry = & $FluentExe -c $ConfFile --dry-run 2>&1 | ForEach-Object { "$_" }
    $rc = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($rc -ne 0) { throw ('Fluent Bit config check failed: ' + (($dry | Select-Object -Last 3) -join ' | ')) }
    Ok 'config checked'

    Step '7/7 Service cm-agent (LocalSystem)'
    $agentLog = Join-Path $DataDir 'fluent-bit.log'
    if (Test-Path $agentLog) { Move-Item $agentLog "$agentLog.old" -Force }
    Set-ServiceBin $Svc "`"$FluentExe`" -c `"$ConfFile`"" 'Central Monitor Agent (Fluent Bit)' 'Sends machine status and ERROR/WARN logs to Central Monitor'
    Start-Service $Svc
    Start-Sleep 6
    $s = Get-Service $Svc
    if ($s.Status -ne 'Running') { throw "service cm-agent is $($s.Status) - see $agentLog" }
    Ok 'service cm-agent running'
    if (Test-Path $agentLog) {
        Select-String -Path $agentLog -Pattern '\[error\]' -ErrorAction SilentlyContinue | Select-Object -Last 3 | ForEach-Object { Warn $_.Line }
    }
    }
} catch {
    $exitCode = 1
    $errMsg = $_.Exception.Message
    Say "[FAIL] $errMsg" 'Red'
}

Say ''
if ($exitCode -eq 0) {
    if ($script:Warnings) { Say "RESULT: SUCCESS with $($script:Warnings) warning(s)" 'Yellow' } else { Say 'RESULT: SUCCESS' 'Green' }
    Say "the machine shows up in Central Monitor within about $([Math]::Max(1, [Math]::Ceiling(2 * [Math]::Max($HbInt, $MetricsInt) / 60))) minute(s)"
} else { Say 'RESULT: FAILED - see the message above and the log file' 'Red' }
Say "log: $LogFile"

# the token must not stay in the config next to the .bat
try {
    $cfgFile = Join-Path $Here 'cm-agent-config.txt'
    if ((Test-Path -LiteralPath $cfgFile) -and (Select-String -LiteralPath $cfgFile -Pattern '^\s*Token\s*=\s*\S' -Quiet)) {
        $lines = @(Get-Content -LiteralPath $cfgFile | ForEach-Object { if ($_ -match '^\s*Token\s*=') { 'Token=' } else { $_ } })
        [System.IO.File]::WriteAllLines($cfgFile, [string[]]$lines, (New-Object System.Text.UTF8Encoding($true)))
    }
} catch { Log "could not clear Token in cm-agent-config.txt: $($_.Exception.Message)" }

try {
    [ordered]@{ ok = ($exitCode -eq 0); exit = $exitCode; error = $errMsg; warnings = $script:Warnings
                computer = $env:COMPUTERNAME; version = $Version; features = $features } |
        ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $Here 'setup-result.json') -Encoding ASCII
} catch { }
exit $exitCode
