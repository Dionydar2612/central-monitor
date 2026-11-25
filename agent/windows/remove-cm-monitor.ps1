# =====================================================================
# remove-cm-monitor.ps1 - remove Central Monitor agentless monitoring from this machine COMPLETELY
# Run it through remove-cm-monitor.bat (asks for administrator rights itself), or from the web page
# (Settings > Windows target > Uninstall: central runs this file over WinRM with "unattended").
#
# Removes everything setup-cm-monitor created (manifest.json), and also finds our items by their
# own names when the manifest is missing:
#   - JEA endpoint "CentralMonitor" (+ its WinRM plugin limits)          - module folder in Program Files
#   - firewall rule CentralMonitor-WinRM-HTTPS                           - read permission on log folders
#   - WinRM HTTPS listener + certificate "Central Monitor WinRM HTTPS" (only the ones setup created)
#   - module signing certificate "Central Monitor Module Signing" (My + Root + TrustedPublisher)
#   - Service Control Manager read permission for cm-monitor             - deny-logon rights for cm-monitor
#   - user cm-monitor + its profile (C:\Users\cm-monitor and registry)   - %ProgramData%\CentralMonitor
#   - %ProgramData%\CentralMonitor-setup (left by an install from the web page)
#   - WinRM service: back to Stopped / its old start mode IF setup had started it (not in unattended mode:
#     central is connected over WinRM then)
# Never touches: the WinRM HTTP listener (5985), other endpoints / listeners / certificates / users.
# At the end it checks every item again and lists anything still present.
# ASCII only (Windows PowerShell 5.1).
# =====================================================================
param([string[]]$Options = @())

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Here      = Split-Path -Parent $MyInvocation.MyCommand.Path
$EpName    = 'CentralMonitor'
$UserName  = 'cm-monitor'
$UserDesc  = 'Central Monitor read-only (JEA)'
$DataDir   = Join-Path $env:ProgramData 'CentralMonitor'
$SetupDir  = Join-Path $env:ProgramData 'CentralMonitor-setup'
$ModDir    = Join-Path $env:ProgramFiles 'WindowsPowerShell\Modules\CentralMonitor'
$RuleName  = 'CentralMonitor-WinRM-HTTPS'
$ManiPath  = Join-Path $DataDir 'manifest.json'
$SignSubj  = 'CN=Central Monitor Module Signing'
$HttpsName = 'Central Monitor WinRM HTTPS'
$Unattended = $Options -contains 'unattended'
$script:Problems = 0

$LogDir  = Join-Path $Here 'logs'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogFile = Join-Path $LogDir ('remove-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
function Log([string]$m)  { Add-Content -Path $LogFile -Value ((Get-Date -Format 'HH:mm:ss') + ' ' + $m) -Encoding UTF8 }
function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c; Log $m }
function Ok([string]$m)   { Say "[ OK ] $m" 'Green' }
function Info([string]$m) { Say "[INFO] $m" }
function Warn([string]$m) { Say "[WARN] $m" 'Yellow'; $script:Problems++ }
function Try-Do([string]$what, [scriptblock]$action) {
    try { & $action; Ok $what } catch { Warn "$what - failed: $($_.Exception.Message)" }
}

function Set-DenyLogonRights([string]$Sid) {
    $tmp = Join-Path $env:TEMP ('cm-secpol-' + [guid]::NewGuid().ToString('N'))
    try {
        & secedit.exe /export /cfg "$tmp.inf" /areas USER_RIGHTS | Out-Null
        $current = Get-Content -LiteralPath "$tmp.inf" -Encoding Unicode
        $out = New-Object System.Collections.ArrayList
        $changed = $false
        foreach ($right in @('SeDenyInteractiveLogonRight', 'SeDenyRemoteInteractiveLogonRight')) {
            $vals = @()
            foreach ($l in $current) {
                if ($l -match ('^\s*' + $right + '\s*=\s*(.*)$')) { $vals = @($Matches[1].Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }) }
            }
            if ($vals -contains ('*' + $Sid)) { $changed = $true }
            $vals = @($vals | Where-Object { $_ -ne ('*' + $Sid) })
            [void]$out.Add($right + ' = ' + ($vals -join ','))
        }
        if (-not $changed) { return $false }
        $inf = @('[Unicode]', 'Unicode=yes', '[Version]', 'signature="$CHICAGO$"', 'Revision=1', '[Privilege Rights]') + $out
        Set-Content -LiteralPath "$tmp.inf" -Value $inf -Encoding Unicode
        & secedit.exe /configure /db "$tmp.sdb" /cfg "$tmp.inf" /areas USER_RIGHTS /quiet | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "secedit exit code $LASTEXITCODE" }
        return $true
    } finally {
        Remove-Item -LiteralPath "$tmp.inf", "$tmp.sdb", "$tmp.jfm" -Force -ErrorAction SilentlyContinue
    }
}

function Get-ScmSddl { return @(& sc.exe sdshow scmanager | Where-Object { $_ -match '^D:' })[0] }

function Get-OurHttpsListeners([string[]]$Thumbs) {
    $out = @()
    foreach ($l in @(Get-ChildItem -Path WSMan:\localhost\Listener -ErrorAction SilentlyContinue)) {
        if ($l.Keys -notcontains 'Transport=HTTPS') { continue }
        $lt = ''
        foreach ($i in @(Get-ChildItem -Path ('WSMan:\localhost\Listener\' + $l.Name))) {
            if ($i.Name -eq 'CertificateThumbprint') { $lt = ([string]$i.Value).Replace(' ', '').ToUpper() }
        }
        if ($lt -and $Thumbs -contains $lt) { $out += $l.Name }
    }
    return $out
}

$exitCode = 0
$errMsg = ''
$leftovers = @()
try {
    Say '=== Central Monitor - remove agentless monitoring ===' 'Cyan'
    Info "log: $LogFile"
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run remove-cm-monitor.bat as administrator.'
    }

    $c = @{}
    if (Test-Path -LiteralPath $ManiPath) {
        try {
            $j = Get-Content -LiteralPath $ManiPath -Raw | ConvertFrom-Json
            foreach ($p in $j.created.PSObject.Properties) { $c[$p.Name] = $p.Value }
            Info 'manifest.json found'
        } catch { Warn 'manifest.json unreadable - removing items by their names' }
    } else {
        Info 'no manifest.json - removing items by their names'
    }

    $u = Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue
    $ours = ($null -ne $u) -and ($c.ContainsKey('user') -or $u.Description -eq $UserDesc)
    $userSid = $null
    if ($ours) { $userSid = $u.SID.Value }
    elseif ($null -ne $u) { Warn "user '$UserName' exists but was not created by setup - it is kept (and its permissions)" }

    # 1. endpoint first: ends every open cm-monitor session (WinRM restarts once)
    if ($null -ne (Get-PSSessionConfiguration -Name $EpName -ErrorAction SilentlyContinue)) {
        Try-Do 'JEA endpoint CentralMonitor removed' { Unregister-PSSessionConfiguration -Name $EpName -NoServiceRestart -Force }
        # this file runs as its own process, so it survives the restart even when started over WinRM
        Try-Do 'WinRM restarted once (open cm-monitor sessions end)' { Restart-Service -Name WinRM -Force }
        Start-Sleep -Seconds 3
    } else { Info 'endpoint not present' }

    # 2. HTTPS listener + its certificate - only the ones setup created (manifest, or our certificate name)
    $ourCerts = @()
    if ($c.ContainsKey('cert_thumbprint') -and $c['cert_thumbprint']) { $ourCerts += ([string]$c['cert_thumbprint']).ToUpper() }
    $ourCerts += @(Get-ChildItem Cert:\LocalMachine\My | Where-Object { $_.FriendlyName -eq $HttpsName } | ForEach-Object { $_.Thumbprint.ToUpper() })
    $ourCerts = @($ourCerts | Select-Object -Unique)
    foreach ($ln in (Get-OurHttpsListeners $ourCerts)) {
        Try-Do 'WinRM HTTPS listener (created by setup) removed' { Remove-Item -Path ('WSMan:\localhost\Listener\' + $ln) -Recurse -Force }
    }
    foreach ($t in $ourCerts) {
        $cp = 'Cert:\LocalMachine\My\' + $t
        if (Test-Path -LiteralPath $cp) { Try-Do "WinRM HTTPS certificate $t removed" { Remove-Item -LiteralPath $cp -Force } }
    }

    # 3. module signing certificate (My + Root + TrustedPublisher)
    $signThumbs = @()
    if ($c.ContainsKey('sign_thumbprint') -and $c['sign_thumbprint']) { $signThumbs += ([string]$c['sign_thumbprint']).ToUpper() }
    foreach ($st in @('My', 'Root', 'TrustedPublisher')) {
        $signThumbs += @(Get-ChildItem ('Cert:\LocalMachine\' + $st) | Where-Object { $_.Subject -eq $SignSubj } | ForEach-Object { $_.Thumbprint.ToUpper() })
    }
    foreach ($t in @($signThumbs | Select-Object -Unique)) {
        foreach ($st in @('My', 'Root', 'TrustedPublisher')) {
            $sp = 'Cert:\LocalMachine\' + $st + '\' + $t
            if (Test-Path -LiteralPath $sp) { Try-Do "module signing certificate removed from $st" { Remove-Item -LiteralPath $sp -Force } }
        }
    }

    # 4. firewall rule
    if ($null -ne (Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue)) {
        Try-Do 'firewall rule removed' { Remove-NetFirewallRule -Name $RuleName }
    }

    if ($null -ne $userSid) {
        # 5. read permission on log folders
        $dirs = @()
        if ($c.ContainsKey('acl_dirs') -and $null -ne $c['acl_dirs']) { $dirs = @($c['acl_dirs']) }
        foreach ($d in $dirs) {
            if ([System.IO.Directory]::Exists($d)) { Try-Do "read permission removed: $d" { & icacls.exe $d /remove:g "*$userSid" | Out-Null } }
        }
        # 6. Service Control Manager permission
        $scm = Get-ScmSddl
        if ($scm -and $scm.Contains($userSid)) {
            $newSd = [regex]::Replace($scm.Trim(), '\(A;;[A-Z]*;;;' + [regex]::Escape($userSid) + '\)', '')
            Try-Do 'Service Control Manager permission for cm-monitor removed' { $o = & sc.exe sdset scmanager $newSd; if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE $o" } }
        }
        # 7. deny-logon rights
        try { if (Set-DenyLogonRights -Sid $userSid) { Ok 'deny-logon rights for cm-monitor removed' } } catch { Warn "deny-logon rights - failed: $($_.Exception.Message)" }
        # 8. user + profile (C:\Users\cm-monitor is created by the first WinRM logon)
        Try-Do 'user cm-monitor removed' { Remove-LocalUser -SID $userSid }
        for ($i = 0; $i -lt 5; $i++) {
            $prof = @(Get-CimInstance -ClassName Win32_UserProfile -Filter ("SID='" + $userSid + "'") -ErrorAction SilentlyContinue)
            if ($prof.Count -eq 0) { break }
            try { $prof | Remove-CimInstance -ErrorAction Stop; Ok 'profile of cm-monitor removed (C:\Users\cm-monitor)'; break }
            catch { if ($i -eq 4) { Warn "profile of cm-monitor - failed (still loaded?): $($_.Exception.Message)" } else { Start-Sleep -Seconds 3 } }
        }
    }

    # 9. module + data folders
    if (Test-Path -LiteralPath $ModDir) { Try-Do 'module folder removed' { Remove-Item -LiteralPath $ModDir -Recurse -Force } }
    if (Test-Path -LiteralPath $DataDir) { Try-Do "$DataDir removed" { Remove-Item -LiteralPath $DataDir -Recurse -Force } }
    if ((Test-Path -LiteralPath $SetupDir) -and ($Here.TrimEnd('\') -ne $SetupDir)) {
        Try-Do "$SetupDir removed" { Remove-Item -LiteralPath $SetupDir -Recurse -Force }
    }

    # 10. WinRM service back to how it was (only if setup had started it)
    if ($c.ContainsKey('winrm_started') -and $c['winrm_started']) {
        $prev = 'Manual'
        if ($c.ContainsKey('winrm_prev_start') -and $c['winrm_prev_start']) { $prev = [string]$c['winrm_prev_start'] }
        if ($Unattended) {
            Info "setup had started WinRM (it was $prev) - left running because this uninstall runs over WinRM; stop it on the machine if nothing else needs it"
        } else {
            if ($prev -eq 'Auto') { $prev = 'Automatic' }
            Try-Do "WinRM service stopped and set back to $prev (setup had started it)" { Stop-Service -Name WinRM -Force; Set-Service -Name WinRM -StartupType $prev }
        }
    }

    # 11. check again: anything left?
    Say ''
    Say '--- check: nothing of Central Monitor should be left' 'Cyan'
    if ($null -ne (Get-PSSessionConfiguration -Name $EpName -ErrorAction SilentlyContinue)) { $leftovers += 'JEA endpoint CentralMonitor' }
    if (Test-Path -LiteralPath $ModDir) { $leftovers += $ModDir }
    if (Test-Path -LiteralPath $DataDir) { $leftovers += $DataDir }
    if ($null -ne (Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue)) { $leftovers += "firewall rule $RuleName" }
    if (@(Get-OurHttpsListeners $ourCerts).Count -gt 0) { $leftovers += 'WinRM HTTPS listener created by setup' }
    foreach ($st in @('My', 'Root', 'TrustedPublisher')) {
        if (@(Get-ChildItem ('Cert:\LocalMachine\' + $st) | Where-Object { $_.Subject -eq $SignSubj -or $_.FriendlyName -eq $HttpsName }).Count -gt 0) { $leftovers += "certificate in LocalMachine\$st" }
    }
    if ($ours -and $null -ne (Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue)) { $leftovers += "user $UserName" }
    if ($null -ne $userSid) {
        if (@(Get-CimInstance -ClassName Win32_UserProfile -Filter ("SID='" + $userSid + "'") -ErrorAction SilentlyContinue).Count -gt 0) { $leftovers += 'profile of cm-monitor' }
        $scm = Get-ScmSddl
        if ($scm -and $scm.Contains($userSid)) { $leftovers += 'Service Control Manager permission' }
    }
    if ($leftovers.Count -eq 0) { Ok 'nothing left' } else { foreach ($x in $leftovers) { Warn "still present: $x" } }
} catch {
    $errMsg = $_.Exception.Message
    Say "[FAIL] $errMsg" 'Red'
    Log ($_ | Out-String)
    $exitCode = 1
}

Say ''
if ($exitCode -eq 1) { Say 'RESULT: FAILED - see the message above' 'Red' }
elseif ($script:Problems -gt 0) { Say "RESULT: DONE WITH $($script:Problems) WARNING(S)" 'Yellow'; $exitCode = 2 }
else { Say 'RESULT: REMOVED COMPLETELY' 'Green' }
if (-not $Unattended) { Say 'Remember to delete the machine in central (Settings) too.' }
Say "log: $LogFile"

# result for the web page uninstall (central reads it, then deletes the folder)
try {
    [ordered]@{ ok = ($exitCode -ne 1); exit = $exitCode; error = $errMsg; warnings = $script:Problems;
                leftovers = @($leftovers); computer = $env:COMPUTERNAME } |
        ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $Here 'remove-result.json') -Encoding ASCII
} catch { }
exit $exitCode
