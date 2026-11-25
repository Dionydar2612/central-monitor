# =====================================================================
# setup-cm-monitor.ps1 - Central Monitor: agentless Windows monitoring (setup / update)
# Run it through setup-cm-monitor.bat (asks for administrator rights itself).
#
# What it creates (all recorded in %ProgramData%\CentralMonitor\manifest.json
# so remove-cm-monitor.bat removes exactly these and nothing else):
#   - local user  cm-monitor  (NOT an administrator; Performance Monitor Users + Event Log Readers;
#                              optional: denied local / RDP logon)
#   - module      %ProgramFiles%\WindowsPowerShell\Modules\CentralMonitor  (read-only Get-CM* functions)
#   - JEA endpoint "CentralMonitor" (NoLanguage, only the Get-CM* functions are visible)
#                 + limits: 150 MB RAM per session, 2 sessions, idle timeout 5 min
#   - WinRM HTTPS listener on 5986 with a self-signed certificate (only if no HTTPS listener exists)
#   - Windows Firewall rule: TCP 5986 from the central IP only
#   - allowed-paths.txt (log files central may read) + read permission on those folders
#
# What it does NOT do: no service, no scheduled task, no agent, no reboot, no change to
# the existing WinRM HTTP listener / TrustedHosts / other endpoints / other software.
# It restarts the WinRM service once (a few seconds) to apply the endpoint.
#
# Arguments (from the .bat):  resetpw = give cm-monitor a new password
# ASCII only (Windows PowerShell 5.1 reads files without a BOM as ANSI).
# =====================================================================
param([string[]]$Options = @())

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Here      = Split-Path -Parent $MyInvocation.MyCommand.Path
$EpName    = 'CentralMonitor'
$UserName  = 'cm-monitor'
$UserDesc  = 'Central Monitor read-only (JEA)'   # New-LocalUser: Description <= 48 chars
$DataDir   = Join-Path $env:ProgramData 'CentralMonitor'
$ModDir    = Join-Path $env:ProgramFiles 'WindowsPowerShell\Modules\CentralMonitor'
$RuleName  = 'CentralMonitor-WinRM-HTTPS'
$ManiPath  = Join-Path $DataDir 'manifest.json'
$CredPath  = Join-Path $DataDir 'cm-monitor-credential.txt'
$Version   = '1.1.0'
$SID_PERF  = 'S-1-5-32-558'   # Performance Monitor Users
$SID_EVT   = 'S-1-5-32-573'   # Event Log Readers
$SID_ADM   = 'S-1-5-32-544'   # Administrators
$SID_SYS   = 'S-1-5-18'       # SYSTEM

$script:Warnings = 0
$LogDir  = Join-Path $Here 'logs'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogFile = Join-Path $LogDir ('setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
# keep the 5 newest setup logs only
Get-ChildItem -Path $LogDir -Filter 'setup-*.log' | Sort-Object Name -Descending | Select-Object -Skip 4 | Remove-Item -Force -ErrorAction SilentlyContinue

function Log([string]$m)  { Add-Content -Path $LogFile -Value ((Get-Date -Format 'HH:mm:ss') + ' ' + $m) -Encoding UTF8 }
function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c; Log $m }
function Ok([string]$m)   { Say "[ OK ] $m" 'Green' }
function Info([string]$m) { Say "[INFO] $m" 'Gray' }
function Warn([string]$m) { Say "[WARN] $m" 'Yellow'; $script:Warnings++ }
function Step([string]$m) { Say ''; Say "--- $m" 'Cyan' }

function Read-Config {
    $cfg = @{ CentralIP = ''; Port = '5986'; LogPaths = ''; DenyLogon = 'yes' }
    $path = Join-Path $Here 'cm-monitor-config.txt'
    if (-not (Test-Path -LiteralPath $path)) { throw "cm-monitor-config.txt not found next to the .bat ($path)" }
    foreach ($line in (Get-Content -LiteralPath $path)) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
        $k = $t.Substring(0, $t.IndexOf('=')).Trim()
        $v = $t.Substring($t.IndexOf('=') + 1).Trim()
        if ($cfg.ContainsKey($k)) { $cfg[$k] = $v }
    }
    return $cfg
}

function Load-Manifest {
    $m = @{ version = $Version; created = @{} }
    if (Test-Path -LiteralPath $ManiPath) {
        try {
            $j = Get-Content -LiteralPath $ManiPath -Raw | ConvertFrom-Json
            foreach ($p in $j.created.PSObject.Properties) { $m.created[$p.Name] = $p.Value }
        } catch { Warn "manifest.json unreadable - starting a new one ($($_.Exception.Message))" }
    }
    return $m
}

function Save-Manifest($m) {
    $m.version = $Version
    $m.updated = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    $m | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ManiPath -Encoding UTF8
}

function New-RandomPassword {
    $sets = @('ABCDEFGHJKLMNPQRSTUVWXYZ', 'abcdefghijkmnopqrstuvwxyz', '23456789', '-_.#+=')
    $all = -join $sets
    $rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
    $b = New-Object byte[] 4
    $pick = {
        param([string]$s)
        $rng.GetBytes($b)
        return $s[[int]([BitConverter]::ToUInt32($b, 0) % [uint32]$s.Length)]
    }
    $chars = New-Object System.Collections.ArrayList
    foreach ($s in $sets) { [void]$chars.Add((& $pick $s)) }      # at least one of each class
    while ($chars.Count -lt 24) { [void]$chars.Add((& $pick $all)) }
    # shuffle
    for ($i = $chars.Count - 1; $i -gt 0; $i--) {
        $rng.GetBytes($b); $j = [int]([BitConverter]::ToUInt32($b, 0) % [uint32]($i + 1))
        $tmp = $chars[$i]; $chars[$i] = $chars[$j]; $chars[$j] = $tmp
    }
    $rng.Dispose()
    return (-join $chars)
}

function Set-DenyLogonRights([string]$Sid, [bool]$Add) {
    # edits only SeDenyInteractiveLogonRight + SeDenyRemoteInteractiveLogonRight
    # (network logon - used by WinRM - is NOT denied)
    $tmp = Join-Path $env:TEMP ('cm-secpol-' + [guid]::NewGuid().ToString('N'))
    try {
        & secedit.exe /export /cfg "$tmp.inf" /areas USER_RIGHTS | Out-Null
        if (-not (Test-Path "$tmp.inf")) { throw 'secedit export failed' }
        $current = Get-Content -LiteralPath "$tmp.inf" -Encoding Unicode
        $out = New-Object System.Collections.ArrayList
        foreach ($right in @('SeDenyInteractiveLogonRight', 'SeDenyRemoteInteractiveLogonRight')) {
            $vals = @()
            foreach ($l in $current) {
                if ($l -match ('^\s*' + $right + '\s*=\s*(.*)$')) { $vals = @($Matches[1].Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }) }
            }
            $entry = '*' + $Sid
            if ($Add) { if ($vals -notcontains $entry) { $vals += $entry } }
            else      { $vals = @($vals | Where-Object { $_ -ne $entry }) }
            [void]$out.Add($right + ' = ' + ($vals -join ','))
        }
        $inf = @('[Unicode]', 'Unicode=yes', '[Version]', 'signature="$CHICAGO$"', 'Revision=1', '[Privilege Rights]') + $out
        Set-Content -LiteralPath "$tmp.inf" -Value $inf -Encoding Unicode
        & secedit.exe /configure /db "$tmp.sdb" /cfg "$tmp.inf" /areas USER_RIGHTS /quiet | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "secedit /configure exit code $LASTEXITCODE" }
    } finally {
        Remove-Item -LiteralPath "$tmp.inf", "$tmp.sdb", "$tmp.jfm" -Force -ErrorAction SilentlyContinue
    }
}

function Get-HttpsListener {
    foreach ($l in @(Get-ChildItem -Path WSMan:\localhost\Listener)) {
        if ($l.Keys -contains 'Transport=HTTPS') {
            $o = @{ Name = $l.Name; Port = 5986; Thumbprint = '' }
            foreach ($i in @(Get-ChildItem -Path ("WSMan:\localhost\Listener\" + $l.Name))) {
                if ($i.Name -eq 'Port') { $o.Port = [int]$i.Value }
                if ($i.Name -eq 'CertificateThumbprint') { $o.Thumbprint = ([string]$i.Value).Replace(' ', '').ToUpper() }
            }
            return $o
        }
    }
    return $null
}

# =====================================================================
$exitCode = 0
$newPassword = $null
try {
    Say '=== Central Monitor - Windows agentless setup (v1.1.0) ===' 'Cyan'
    Info "log: $LogFile"

    # ----------------------------------------------------------- checks
    Step '1/8 Checks'
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run setup-cm-monitor.bat as administrator.'
    }
    $psv = $PSVersionTable.PSVersion
    if ($psv.Major -lt 5 -or ($psv.Major -eq 5 -and $psv.Minor -lt 1)) {
        throw "PowerShell $psv found - 5.1 is required (JEA). Install WMF 5.1 first (needs a reboot) - nothing was changed."
    }
    Ok "PowerShell $psv"
    # A GPO forcing AllSigned blocks the unsigned module inside JEA (the .bat Bypass only covers setup itself)
    $gpoPolicy = [string](Get-ExecutionPolicy -Scope MachinePolicy)
    Info ("ExecutionPolicy: machine=" + (Get-ExecutionPolicy -Scope LocalMachine) + " gpo=" + $gpoPolicy + " user-gpo=" + (Get-ExecutionPolicy -Scope UserPolicy) + " (module is signed by setup, works with AllSigned too)")
    $cfg = Read-Config
    $ip = $null
    if (-not [System.Net.IPAddress]::TryParse($cfg.CentralIP, [ref]$ip)) { throw "CentralIP in cm-monitor-config.txt is not an IP address: '$($cfg.CentralIP)'" }
    $port = 0
    if (-not [int]::TryParse($cfg.Port, [ref]$port) -or $port -lt 1 -or $port -gt 65535) { throw "Port in cm-monitor-config.txt is not valid: '$($cfg.Port)'" }
    Ok "central = $($cfg.CentralIP), port = $port"

    $winrm = Get-Service -Name WinRM -ErrorAction SilentlyContinue
    if ($null -eq $winrm) { throw 'WinRM service not found on this machine.' }
    $startMode = (Get-CimInstance -ClassName Win32_Service -Filter "Name='WinRM'").StartMode
    if ($startMode -eq 'Disabled') { throw 'The WinRM service is Disabled (probably by policy). Nothing was changed - ask the server owner first.' }
    $m = Load-Manifest
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
    if ($winrm.Status -ne 'Running') {
        Info 'WinRM is not running - starting it (automatic start).'
        Set-Service -Name WinRM -StartupType Automatic
        Start-Service -Name WinRM
        $m.created['winrm_started'] = $true
    }
    Ok 'WinRM service running'

    # ----------------------------------------------------------- module + role capability
    Step '2/8 Read-only module (Program Files - writable by administrators only)'
    $src = Join-Path $Here 'CentralMonitor.psm1'
    if (-not (Test-Path -LiteralPath $src)) { throw "CentralMonitor.psm1 not found next to the .bat" }
    New-Item -ItemType Directory -Path (Join-Path $ModDir 'RoleCapabilities') -Force | Out-Null
    Copy-Item -LiteralPath $src -Destination (Join-Path $ModDir 'CentralMonitor.psm1') -Force
    # A zip downloaded with a browser carries Mark of the Web (Zone.Identifier); RemoteSigned then refuses
    # to load the module in the session ("... is not digitally signed"). Remove that mark from our copied files.
    Get-ChildItem -LiteralPath $ModDir -Recurse -File | Unblock-File
    foreach ($f in @('CentralMonitor.psd1', 'RoleCapabilities\CentralMonitor.psrc')) { Remove-Item -LiteralPath (Join-Path $ModDir $f) -Force -ErrorAction SilentlyContinue }
    $funcs = @('Get-CMVersion', 'Get-CMMetrics', 'Get-CMServices', 'Get-CMEvents', 'Get-CMLogTail', 'Get-CMInventory', 'Get-CMProcesses')
    New-ModuleManifest -Path (Join-Path $ModDir 'CentralMonitor.psd1') -RootModule 'CentralMonitor.psm1' -ModuleVersion $Version `
        -Author 'Central Monitor' -Description 'Central Monitor read-only functions for the JEA endpoint' `
        -FunctionsToExport $funcs -CmdletsToExport @() -VariablesToExport @() -AliasesToExport @() `
        -RequiredModules @('Microsoft.PowerShell.Management', 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Diagnostics', 'CimCmdlets') `
        -PowerShellVersion '5.1'
    New-PSRoleCapabilityFile -Path (Join-Path $ModDir 'RoleCapabilities\CentralMonitor.psrc') -ModulesToImport 'CentralMonitor' `
        -VisibleFunctions $funcs -Description 'Central Monitor: read-only Get-CM* functions only'
    $m.created['module_dir'] = $ModDir
    Ok "module installed: $ModDir"

    # Sign the module with a code-signing certificate trusted on THIS machine only, so the JEA session loads it
    # under any execution policy (RemoteSigned / AllSigned) and any change to the file breaks the signature.
    # Private key: LocalMachine\My, not exportable. Public cert: LocalMachine\Root + TrustedPublisher.
    $sign = $null
    if ($m.created.ContainsKey('sign_thumbprint') -and $m.created['sign_thumbprint']) {
        $sign = Get-Item -LiteralPath ('Cert:\LocalMachine\My\' + [string]$m.created['sign_thumbprint']) -ErrorAction SilentlyContinue
    }
    if ($null -eq $sign) {
        $sign = New-SelfSignedCertificate -Type CodeSigningCert -Subject 'CN=Central Monitor Module Signing' -CertStoreLocation 'Cert:\LocalMachine\My' `
            -NotAfter (Get-Date).AddYears(10) -KeyAlgorithm RSA -KeyLength 2048 -KeyExportPolicy NonExportable -FriendlyName 'Central Monitor module signing'
        $m.created['sign_thumbprint'] = $sign.Thumbprint
        Save-Manifest $m
    }
    $pub = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList (,[byte[]]$sign.RawData)
    foreach ($storeName in @('Root', 'TrustedPublisher')) {
        $store = New-Object System.Security.Cryptography.X509Certificates.X509Store($storeName, 'LocalMachine')
        $store.Open('ReadWrite')
        try { $store.Add($pub) } finally { $store.Close() }
    }
    foreach ($f in @('CentralMonitor.psm1', 'CentralMonitor.psd1')) {
        $r = Set-AuthenticodeSignature -FilePath (Join-Path $ModDir $f) -Certificate $sign -HashAlgorithm SHA256
        if ($r.Status -ne 'Valid') { throw "signing $f failed: $($r.Status) $($r.StatusMessage)" }
    }
    Ok "module signed (certificate $($sign.Thumbprint), trusted on this machine only)"

    # ----------------------------------------------------------- user
    Step '3/8 Local user cm-monitor (not an administrator)'
    $u = Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue
    if ($null -eq $u) {
        $newPassword = New-RandomPassword
        $sec = ConvertTo-SecureString $newPassword -AsPlainText -Force
        $u = New-LocalUser -Name $UserName -Password $sec -Description $UserDesc -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires
        $m.created['user'] = $true
        Ok 'user created (random 24-character password)'
    } else {
        if (-not $m.created.ContainsKey('user') -and $u.Description -ne $UserDesc) {
            throw "A user named '$UserName' already exists and was not created by this setup. Nothing was changed."
        }
        if ($Options -contains 'resetpw') {
            $newPassword = New-RandomPassword
            Set-LocalUser -Name $UserName -Password (ConvertTo-SecureString $newPassword -AsPlainText -Force)
            Ok 'existing user - NEW password set (update it in central Settings)'
        } else {
            Ok 'existing user kept - password unchanged (run "setup-cm-monitor.bat resetpw" for a new one)'
        }
        if (-not $u.Enabled) { Enable-LocalUser -Name $UserName; Info 'user was disabled - enabled' }
    }
    $m.created['user'] = $true
    $userSid = (Get-LocalUser -Name $UserName).SID.Value
    foreach ($gs in @($SID_PERF, $SID_EVT)) {
        $g = Get-LocalGroup -SID $gs
        $isMember = @(Get-LocalGroupMember -Group $g -ErrorAction SilentlyContinue | Where-Object { $_.SID.Value -eq $userSid }).Count -gt 0
        if (-not $isMember) {
            try { Add-LocalGroupMember -Group $g -Member $UserName -ErrorAction Stop }
            catch { if ($_.FullyQualifiedErrorId -notlike 'MemberExists*') { throw } }
        }
        Ok "member of $($g.Name)"
    }
    # Service Control Manager: by default a NETWORK logon (WinRM) may only connect, not list services
    # ("Cannot open Service Control Manager"). Allow cm-monitor read-only access: CC connect, LC enumerate,
    # RP query lock status, RC read permissions. No start / stop / change rights. Undone by remove-cm-monitor.
    try {
        $scm = @(& sc.exe sdshow scmanager | Where-Object { $_ -match '^D:' })[0]
        if (-not $scm) { throw 'sc.exe sdshow scmanager returned nothing' }
        $scm = $scm.Trim()
        $ace = '(A;;CCLCRPRC;;;' + $userSid + ')'
        if ($scm.Contains($ace)) {
            Ok 'Service Control Manager: cm-monitor may list services (read-only) - already set'
        } else {
            $i = $scm.IndexOf('S:')
            if ($i -ge 0) { $newSd = $scm.Substring(0, $i) + $ace + $scm.Substring($i) } else { $newSd = $scm + $ace }
            $out = & sc.exe sdset scmanager $newSd
            if ($LASTEXITCODE -ne 0) { throw "sc.exe sdset scmanager exit $LASTEXITCODE $out" }
            $m.created['scm_ace'] = $ace
            Save-Manifest $m
            Ok 'Service Control Manager: cm-monitor may list services (read-only)'
        }
    } catch { Warn "could not allow cm-monitor to list services: $($_.Exception.Message)" }

    if ($cfg.DenyLogon -match '^(yes|y|true|1)$') {
        try {
            Set-DenyLogonRights -Sid $userSid -Add $true
            $m.created['deny_logon_sid'] = $userSid
            Ok 'local logon and Remote Desktop denied for cm-monitor (network logon for WinRM stays allowed)'
        } catch { Warn "could not set deny-logon rights: $($_.Exception.Message)" }
    } else {
        Info 'DenyLogon=no - logon rights not changed'
    }

    # ----------------------------------------------------------- data dir + allow list
    Step '4/8 Allowed log paths'
    # ProgramData\CentralMonitor: Administrators + SYSTEM full, cm-monitor read
    & icacls.exe $DataDir /inheritance:r /grant:r "*${SID_ADM}:(OI)(CI)F" "*${SID_SYS}:(OI)(CI)F" "*${userSid}:(OI)(CI)RX" | Out-Null
    $rules = New-Object System.Collections.ArrayList
    $grantDirs = New-Object System.Collections.ArrayList
    foreach ($raw in ($cfg.LogPaths -split ';')) {
        $p = $raw.Trim().Trim('"')
        if ($p -eq '') { continue }
        if ($p -notmatch '^[A-Za-z]:\\' -or $p.Contains('..') -or $p.Substring(2).Contains(':')) { Warn "skipped (not a local path): $p"; continue }
        $leaf = [System.IO.Path]::GetFileName($p)
        $dir  = [System.IO.Path]::GetDirectoryName($p)
        if ($p.EndsWith('\') -or ([System.IO.Directory]::Exists($p) -and -not ($leaf.Contains('*') -or $leaf.Contains('?')))) {
            $dir = $p.TrimEnd('\'); $rule = $dir + '\'
        } else {
            if ($dir.Contains('*') -or $dir.Contains('?')) { Warn "skipped (wildcards only allowed in the file name): $p"; continue }
            $rule = $p
        }
        if (-not [System.IO.Directory]::Exists($dir)) { Warn "folder not found (rule kept, permission not set): $dir" }
        elseif ($grantDirs -notcontains $dir) { [void]$grantDirs.Add($dir) }
        [void]$rules.Add($rule)
    }
    $allowText = @('# Central Monitor: log files the cm-monitor user may read through the JEA endpoint',
                   '# folder\  = every file directly in that folder    D:\App\logs\*.log = matching files only',
                   '# Edit LogPaths in cm-monitor-config.txt and run setup-cm-monitor.bat again') + $rules
    [System.IO.File]::WriteAllLines((Join-Path $DataDir 'allowed-paths.txt'), [string[]]$allowText, (New-Object System.Text.UTF8Encoding($true)))
    $prevAcl = @()
    if ($m.created.ContainsKey('acl_dirs') -and $null -ne $m.created['acl_dirs']) { $prevAcl = @($m.created['acl_dirs']) }
    foreach ($d in $prevAcl) {
        if ($grantDirs -notcontains $d -and [System.IO.Directory]::Exists($d)) {
            & icacls.exe $d /remove:g "*$userSid" | Out-Null; Info "read permission removed (no longer listed): $d"
        }
    }
    foreach ($d in $grantDirs) {
        & icacls.exe $d /grant "*${userSid}:(OI)(CI)RX" | Out-Null
        if ($LASTEXITCODE -eq 0) { Ok "read permission: $d" } else { Warn "icacls failed on $d (exit $LASTEXITCODE)" }
    }
    $m.created['acl_dirs'] = @($grantDirs)
    if ($rules.Count -eq 0) { Info 'LogPaths is empty - no log files can be read (Event Log still works)' }
    Save-Manifest $m

    # ----------------------------------------------------------- certificate + HTTPS listener
    Step '5/8 WinRM HTTPS listener'
    $lis = Get-HttpsListener
    if ($null -ne $lis) {
        $thumb = $lis.Thumbprint
        if ($lis.Port -ne $port) { Warn "an HTTPS listener already exists on port $($lis.Port) - using it (config said $port)"; $port = $lis.Port }
        Ok "existing HTTPS listener kept (port $port, certificate $thumb)"
    } else {
        $busy = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -ne 4 })
        if ($busy.Count -gt 0) { throw "port $port is already used by process id $($busy[0].OwningProcess). Choose another Port in cm-monitor-config.txt." }
        $cert = New-SelfSignedCertificate -DnsName $env:COMPUTERNAME -CertStoreLocation 'Cert:\LocalMachine\My' `
            -NotAfter (Get-Date).AddYears(10) -KeyAlgorithm RSA -KeyLength 2048 -KeyExportPolicy NonExportable `
            -FriendlyName 'Central Monitor WinRM HTTPS' -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.1')
        $thumb = $cert.Thumbprint.ToUpper()
        $m.created['cert_thumbprint'] = $thumb
        Save-Manifest $m
        New-WSManInstance -ResourceURI 'winrm/config/Listener' -SelectorSet @{ Address = '*'; Transport = 'HTTPS' } `
            -ValueSet @{ Hostname = $env:COMPUTERNAME; CertificateThumbprint = $thumb; Port = [string]$port } | Out-Null
        $m.created['listener_https'] = $true
        $m.created['listener_port'] = $port
        Save-Manifest $m
        Ok "HTTPS listener created on port $port (self-signed certificate, 10 years)"
    }

    # ----------------------------------------------------------- firewall
    Step '6/8 Windows Firewall'
    $fw = Get-Service -Name mpssvc -ErrorAction SilentlyContinue
    if ($null -eq $fw -or $fw.Status -ne 'Running') {
        Warn 'Windows Firewall service is not running - rule not created (port is open to everyone the network allows)'
    } else {
        $r = Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue
        if ($null -eq $r) {
            New-NetFirewallRule -Name $RuleName -DisplayName 'Central Monitor WinRM HTTPS (central only)' -Direction Inbound -Action Allow `
                -Protocol TCP -LocalPort $port -RemoteAddress $cfg.CentralIP -Profile Any | Out-Null
        } else {
            Set-NetFirewallRule -Name $RuleName -Enabled True -Action Allow -Protocol TCP -LocalPort $port -RemoteAddress $cfg.CentralIP -Profile Any | Out-Null
        }
        $m.created['firewall_rule'] = $RuleName
        Save-Manifest $m
        Ok "TCP $port allowed from $($cfg.CentralIP) only"
    }

    # ----------------------------------------------------------- JEA endpoint
    Step '7/8 JEA endpoint CentralMonitor'
    $pssc = Join-Path $DataDir 'CentralMonitor.pssc'
    Remove-Item -LiteralPath $pssc -Force -ErrorAction SilentlyContinue
    New-PSSessionConfigurationFile -Path $pssc -SessionType RestrictedRemoteServer -LanguageMode NoLanguage -ExecutionPolicy RemoteSigned `
        -RoleDefinitions @{ ($env:COMPUTERNAME + '\' + $UserName) = @{ RoleCapabilities = 'CentralMonitor' } } `
        -Description 'Central Monitor read-only endpoint'
    if (-not (Test-PSSessionConfigurationFile -Path $pssc)) { throw 'the generated .pssc file is not valid' }
    if ($null -ne (Get-PSSessionConfiguration -Name $EpName -ErrorAction SilentlyContinue)) {
        Unregister-PSSessionConfiguration -Name $EpName -NoServiceRestart -Force
    }
    Register-PSSessionConfiguration -Name $EpName -Path $pssc -NoServiceRestart -Force | Out-Null
    $m.created['endpoint'] = $EpName
    Save-Manifest $m
    $quota = 'WSMan:\localhost\Plugin\' + $EpName + '\Quotas\'
    foreach ($q in @(@('MaxMemoryPerShellMB', 150), @('MaxShellsPerUser', 2), @('MaxShells', 2), @('MaxConcurrentUsers', 1), @('IdleTimeoutms', 300000))) {
        try { Set-Item -Path ($quota + $q[0]) -Value $q[1] -Force -WarningAction SilentlyContinue }
        catch { Warn "could not set $($q[0]) = $($q[1]): $($_.Exception.Message)" }
    }
    Info 'restarting WinRM once to apply (other remote PowerShell sessions on this machine reconnect)'
    Restart-Service -Name WinRM -Force
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Service WinRM).Status -ne 'Running' -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
    Start-Sleep -Seconds 2
    Ok 'endpoint registered: 150 MB per session, max 2 sessions, idle timeout 5 min'

    # ----------------------------------------------------------- self test
    Step '8/8 Self test'
    if ($null -ne $newPassword) {
        try {
            $cred = New-Object System.Management.Automation.PSCredential(($env:COMPUTERNAME + '\' + $UserName), (ConvertTo-SecureString $newPassword -AsPlainText -Force))
            $so = New-PSSessionOption -SkipCACheck -SkipCNCheck -OperationTimeout 30000
            $s = New-PSSession -ComputerName $env:COMPUTERNAME -Port $port -UseSSL -ConfigurationName $EpName -Credential $cred -SessionOption $so -Authentication Negotiate
            try {
                $v = Invoke-Command -Session $s -ScriptBlock { Get-CMVersion } | ConvertFrom-Json
                Ok "endpoint answers: module $($v.v), session process $($v.self.ws_mb) MB, priority $($v.self.priority)"
                $met = Invoke-Command -Session $s -ScriptBlock { Get-CMMetrics } | ConvertFrom-Json
                Ok "metrics: $(@($met.values).Count) counter values, $(@($met.drives).Count) drives"
                $blocked = $false
                try { Invoke-Command -Session $s -ScriptBlock { Get-ChildItem C:\ } -ErrorAction Stop | Out-Null } catch { $blocked = $true }
                if ($blocked) { Ok 'commands outside the allowed list are refused (Get-ChildItem C:\ blocked)' }
                else { Warn 'Get-ChildItem was NOT blocked - check the endpoint' }
            } finally { Remove-PSSession $s }
        } catch {
            Warn "self test failed: $($_.Exception.Message)"
        }
    } else {
        Info 'password unchanged - self test skipped (test from central: scripts/win-poc.sh --once)'
    }

    Save-Manifest $m
} catch {
    Say ''
    Say "[FAIL] $($_.Exception.Message)" 'Red'
    Log ($_ | Out-String)
    $exitCode = 1
}

# ----------------------------------------------------------- summary (password is NOT written to the log)
Say ''
if ($exitCode -eq 0) {
    $ips = @([System.Net.Dns]::GetHostAddresses($env:COMPUTERNAME) | Where-Object { $_.AddressFamily -eq 'InterNetwork' } | ForEach-Object { $_.ToString() }) -join ', '
    Say '================ Put these in central (Settings > Windows target) ================' 'Cyan'
    Say "  Host / IP    : $ips"
    Say "  Port         : $port"
    Say "  User         : $UserName"
    Say "  Thumbprint   : $thumb"
    if ($null -ne $newPassword) {
        Write-Host "  Password     : $newPassword" -ForegroundColor Yellow
        Set-Content -LiteralPath $CredPath -Value @("user=$UserName", "password=$newPassword", "thumbprint=$thumb", "port=$port") -Encoding ASCII
        & icacls.exe $CredPath /inheritance:r /grant:r "*${SID_ADM}:F" "*${SID_SYS}:F" | Out-Null
        Write-Host "  (also saved to $CredPath - administrators only. DELETE it after copying.)" -ForegroundColor Yellow
        Log 'new password shown on screen and saved to the credential file (not logged)'
    } else {
        Say '  Password     : (unchanged)'
    }
    if ($script:Warnings -gt 0) { Say "RESULT: DONE WITH $($script:Warnings) WARNING(S) - see above" 'Yellow'; $exitCode = 2 }
    else { Say 'RESULT: SUCCESS' 'Green' }
} else {
    Say 'RESULT: FAILED - see the message above and the log file' 'Red'
}
Say "log: $LogFile"
exit $exitCode
