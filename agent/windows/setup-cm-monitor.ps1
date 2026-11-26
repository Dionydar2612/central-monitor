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
#                 + limits: 150 MB RAM per session, 5 sessions, idle timeout 5 min
#   - WinRM HTTPS listener on 5986 with a self-signed certificate (only if no HTTPS listener exists)
#   - Windows Firewall rule: TCP 5986 from the central IP only
#   - allowed-paths.txt (log files central may read) + read permission on those folders
#
# What it does NOT do: no service, no scheduled task, no agent, no reboot, no change to
# the existing WinRM HTTP listener / TrustedHosts / other endpoints / other software.
# It restarts the WinRM service once (a few seconds) to apply the endpoint.
#
# Account= in cm-monitor-config.txt (optional): use an EXISTING account (local or DOMAIN\name) instead of creating
#   cm-monitor. Setup never changes its password, does not deny its logon, and remove-cm-monitor keeps the account
#   (only the group memberships / permissions setup added are taken away again).
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
$Version   = '1.1.5'

# Source of CentralMonitor.Native.<version>.dll, compiled ON THIS MACHINE by step 2 (no binary is shipped).
# Read-only Win32 calls usable by a non-admin user (no process handle is opened):
#   Processes()    NtQuerySystemInformation(SystemProcessInformation)  - CPU time / RAM of every process (= Task Manager)
#   ImagePath(pid) NtQuerySystemInformation(SystemProcessIdInformation) - full exe path of any process
#   ServicePids()  EnumServicesStatusEx                                 - process id of every service
#   CpuTimes()     NtQuerySystemInformation(SystemProcessorPerformanceInformation) - CPU time per logical processor
$NativeSource = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

namespace CentralMonitorNative
{
    public class ProcInfo
    {
        public int Pid;
        public int ParentPid;
        public string Name;
        public long Cpu100ns;
        public long WorkingSet;
        public long Private;
        public int Threads;
        public int Handles;
        public int Session;
        public long Created;
    }

    public static class Win
    {
        [StructLayout(LayoutKind.Sequential)]
        struct UNICODE_STRING { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }

        [StructLayout(LayoutKind.Sequential)]
        struct SYSTEM_PROCESS_ID_INFORMATION { public IntPtr ProcessId; public UNICODE_STRING ImageName; }

        [StructLayout(LayoutKind.Sequential)]
        struct SERVICE_STATUS_PROCESS
        {
            public int ServiceType, CurrentState, ControlsAccepted, Win32ExitCode, ServiceSpecificExitCode,
                       CheckPoint, WaitHint, ProcessId, ServiceFlags;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct ENUM_SERVICE_STATUS_PROCESS { public IntPtr ServiceName; public IntPtr DisplayName; public SERVICE_STATUS_PROCESS Status; }

        [DllImport("ntdll.dll")]
        static extern int NtQuerySystemInformation(int infoClass, IntPtr info, int length, out int returnLength);

        [DllImport("ntdll.dll")]
        static extern int NtQuerySystemInformation(int infoClass, ref SYSTEM_PROCESS_ID_INFORMATION info, int length, out int returnLength);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern int QueryDosDeviceW(string deviceName, StringBuilder targetPath, int max);

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr OpenSCManagerW(string machine, string database, int access);

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool EnumServicesStatusExW(IntPtr scm, int infoLevel, int serviceType, int serviceState, IntPtr services,
                                                 int bufSize, out int needed, out int returned, ref int resume, string group);

        [DllImport("advapi32.dll")]
        static extern bool CloseServiceHandle(IntPtr h);

        const int STATUS_INFO_LENGTH_MISMATCH = unchecked((int)0xC0000004);
        const int SystemProcessInformation = 5;
        const int SystemProcessIdInformation = 88;
        const int SystemProcessorPerformanceInformation = 8;

        // ---- CPU time of every logical processor since boot, 100 ns units (= what Task Manager uses) ----
        // 5 values per processor: idle, kernel (includes idle), user, dpc, interrupt
        public static long[] CpuTimes()
        {
            const int each = 48;
            int n = Math.Max(1, Environment.ProcessorCount);
            IntPtr buf = Marshal.AllocHGlobal(each * n);
            try
            {
                int ret;
                int st = NtQuerySystemInformation(SystemProcessorPerformanceInformation, buf, each * n, out ret);
                if (st != 0) return new long[0];
                int count = Math.Min(n, ret / each);
                var r = new long[count * 5];
                for (int i = 0; i < count; i++)
                {
                    IntPtr p = new IntPtr(buf.ToInt64() + (long)i * each);
                    r[i * 5 + 0] = Marshal.ReadInt64(p, 0);
                    r[i * 5 + 1] = Marshal.ReadInt64(p, 8);
                    r[i * 5 + 2] = Marshal.ReadInt64(p, 16);
                    r[i * 5 + 3] = Marshal.ReadInt64(p, 24);
                    r[i * 5 + 4] = Marshal.ReadInt64(p, 32);
                }
                return r;
            }
            finally { Marshal.FreeHGlobal(buf); }
        }

        // ---- all processes (what Task Manager uses; no process handle is opened) ----
        // SYSTEM_PROCESS_INFORMATION offsets, 64-bit Windows
        public static ProcInfo[] Processes()
        {
            if (IntPtr.Size != 8) return new ProcInfo[0];
            int size = 1024 * 1024;
            for (int attempt = 0; attempt < 6; attempt++)
            {
                IntPtr buf = Marshal.AllocHGlobal(size);
                try
                {
                    int needed;
                    int st = NtQuerySystemInformation(SystemProcessInformation, buf, size, out needed);
                    if (st == STATUS_INFO_LENGTH_MISMATCH)
                    {
                        size = Math.Max(size * 2, needed + 65536);
                        if (size > 64 * 1024 * 1024) break;
                        continue;
                    }
                    if (st != 0) break;
                    var list = new List<ProcInfo>(512);
                    long off = 0;
                    while (true)
                    {
                        IntPtr p = new IntPtr(buf.ToInt64() + off);
                        int next = Marshal.ReadInt32(p, 0);
                        var pi = new ProcInfo();
                        pi.Threads = Marshal.ReadInt32(p, 4);
                        pi.Created = Marshal.ReadInt64(p, 32);
                        pi.Cpu100ns = Marshal.ReadInt64(p, 40) + Marshal.ReadInt64(p, 48);
                        ushort nameLen = (ushort)Marshal.ReadInt16(p, 56);
                        IntPtr nameBuf = Marshal.ReadIntPtr(p, 64);
                        pi.Name = (nameBuf != IntPtr.Zero && nameLen > 0) ? Marshal.PtrToStringUni(nameBuf, nameLen / 2) : null;
                        pi.Pid = (int)Marshal.ReadInt64(p, 80);
                        pi.ParentPid = (int)Marshal.ReadInt64(p, 88);
                        pi.Handles = Marshal.ReadInt32(p, 96);
                        pi.Session = Marshal.ReadInt32(p, 100);
                        pi.WorkingSet = Marshal.ReadInt64(p, 144);
                        pi.Private = Marshal.ReadInt64(p, 200);
                        if (pi.Name == null) pi.Name = pi.Pid == 0 ? "Idle" : (pi.Pid == 4 ? "System" : "");
                        list.Add(pi);
                        if (next == 0) break;
                        off += (uint)next;
                        if (off >= size) break;
                    }
                    return list.ToArray();
                }
                finally { Marshal.FreeHGlobal(buf); }
            }
            return new ProcInfo[0];
        }

        // ---- full exe path of any process id (no process handle needed) ----
        static Dictionary<string, string> devices;

        public static string ImagePath(int pid)
        {
            if (pid <= 4) return null;
            int size = 1024;
            for (int attempt = 0; attempt < 3; attempt++)
            {
                IntPtr buf = Marshal.AllocHGlobal(size);
                try
                {
                    var info = new SYSTEM_PROCESS_ID_INFORMATION();
                    info.ProcessId = new IntPtr(pid);
                    info.ImageName.Length = 0;
                    info.ImageName.MaximumLength = (ushort)size;
                    info.ImageName.Buffer = buf;
                    int ret;
                    int st = NtQuerySystemInformation(SystemProcessIdInformation, ref info, Marshal.SizeOf(typeof(SYSTEM_PROCESS_ID_INFORMATION)), out ret);
                    if (st == STATUS_INFO_LENGTH_MISMATCH)
                    {
                        int want = info.ImageName.MaximumLength;
                        if (want <= size || want > 65534) return null;
                        size = want;
                        continue;
                    }
                    if (st != 0 || info.ImageName.Length == 0) return null;
                    return ToDosPath(Marshal.PtrToStringUni(buf, info.ImageName.Length / 2));
                }
                finally { Marshal.FreeHGlobal(buf); }
            }
            return null;
        }

        static string ToDosPath(string nt)
        {
            if (string.IsNullOrEmpty(nt)) return nt;
            for (int round = 0; round < 2; round++)
            {
                if (devices == null || round == 1)
                {
                    var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                    var sb = new StringBuilder(1024);
                    for (char c = 'A'; c <= 'Z'; c++)
                    {
                        string drive = c + ":";
                        sb.Length = 0;
                        if (QueryDosDeviceW(drive, sb, sb.Capacity) > 0)
                        {
                            string dev = sb.ToString();
                            if (!map.ContainsKey(dev)) map[dev] = drive;
                        }
                    }
                    devices = map;
                }
                foreach (var kv in devices)
                {
                    if (nt.StartsWith(kv.Key + "\\", StringComparison.OrdinalIgnoreCase))
                        return kv.Value + nt.Substring(kv.Key.Length);
                }
            }
            if (nt.StartsWith("\\??\\")) return nt.Substring(4);
            return nt;
        }

        // ---- process id of every Win32 service ----
        public static Dictionary<string, int> ServicePids()
        {
            var r = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            IntPtr scm = OpenSCManagerW(null, null, 0x0001 | 0x0004);     // CONNECT | ENUMERATE_SERVICE
            if (scm == IntPtr.Zero) return r;
            try
            {
                int needed, count, resume = 0;
                EnumServicesStatusExW(scm, 0, 0x30, 3, IntPtr.Zero, 0, out needed, out count, ref resume, null);
                if (needed <= 0) return r;
                int size = needed + 16384;
                IntPtr buf = Marshal.AllocHGlobal(size);
                try
                {
                    resume = 0;
                    if (!EnumServicesStatusExW(scm, 0, 0x30, 3, buf, size, out needed, out count, ref resume, null)) return r;
                    int sz = Marshal.SizeOf(typeof(ENUM_SERVICE_STATUS_PROCESS));
                    for (int i = 0; i < count; i++)
                    {
                        var e = (ENUM_SERVICE_STATUS_PROCESS)Marshal.PtrToStructure(new IntPtr(buf.ToInt64() + (long)i * sz), typeof(ENUM_SERVICE_STATUS_PROCESS));
                        string name = Marshal.PtrToStringUni(e.ServiceName);
                        if (name != null) r[name] = e.Status.ProcessId;
                    }
                }
                finally { Marshal.FreeHGlobal(buf); }
            }
            finally { CloseServiceHandle(scm); }
            return r;
        }
    }
}
'@
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
    $cfg = @{ CentralIP = ''; Port = '5986'; LogPaths = ''; DenyLogon = 'yes'; Password = ''; Account = '' }
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
$ExtAcct = ''
$newPassword = $null
$pwFromCentral = $false
$thumb = ''
$port = 0
$errMsg = ''
try {
    Say "=== Central Monitor - Windows agentless setup (v$Version) ===" 'Cyan'
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
        $m.created['winrm_prev_start'] = [string]$startMode
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
    # native helper (see $NativeSource at the top). A DLL still loaded by an old session cannot be replaced:
    # same version = same code, so it is kept. DLLs of other versions are removed when not in use.
    $dllName = "CentralMonitor.Native.$Version.dll"
    $dll = Join-Path $ModDir $dllName
    foreach ($old in @(Get-ChildItem -LiteralPath $ModDir -Filter 'CentralMonitor.Native.*.dll' -File -ErrorAction SilentlyContinue)) {
        if ($old.Name -ne $dllName) { Remove-Item -LiteralPath $old.FullName -Force -ErrorAction SilentlyContinue }
    }
    try {
        $tmp = Join-Path $env:TEMP ('cm-native-' + [guid]::NewGuid().ToString('N') + '.dll')
        Add-Type -TypeDefinition $NativeSource -Language CSharp -OutputAssembly $tmp -OutputType Library -ErrorAction Stop
        try {
            Copy-Item -LiteralPath $tmp -Destination $dll -Force -ErrorAction Stop
            Ok "native helper compiled: $dllName (process list / exe path / service PIDs)"
        } catch {
            if (Test-Path -LiteralPath $dll) { Ok "native helper kept (in use by a session): $dllName" } else { throw }
        } finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    } catch {
        Warn ('native helper not compiled (' + $_.Exception.Message + ') - Programs tab / service PIDs show less detail')
    }
    foreach ($f in @('CentralMonitor.psd1', 'RoleCapabilities\CentralMonitor.psrc')) { Remove-Item -LiteralPath (Join-Path $ModDir $f) -Force -ErrorAction SilentlyContinue }
    $funcs = @('Get-CMVersion', 'Get-CMMetrics', 'Get-CMServices', 'Get-CMEvents', 'Get-CMLogTail', 'Get-CMInventory', 'Get-CMProcesses', 'Get-CMTasks')
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
    $ExtAcct = ([string]$cfg.Account).Trim()
    if ($ExtAcct -and $ExtAcct -ne $UserName) {
        # ------------------------------------------- existing account (not created / not changed by setup)
        Step "3/8 Existing account $ExtAcct (not created, password not changed)"
        if ($ExtAcct.StartsWith('.\')) { $ExtAcct = $env:COMPUTERNAME + $ExtAcct.Substring(1) }
        try {
            $userSid = (New-Object System.Security.Principal.NTAccount($ExtAcct)).Translate([System.Security.Principal.SecurityIdentifier]).Value
            $UserName = (New-Object System.Security.Principal.SecurityIdentifier($userSid)).Translate([System.Security.Principal.NTAccount]).Value
        } catch { throw "Account '$ExtAcct' (Account= in cm-monitor-config.txt) was not found on this machine / domain. Nothing was changed." }
        if ($userSid -match '^S-1-5-(18|19|20)$' -or $userSid -eq $SID_ADM -or $userSid -match '^S-1-5-32-') {
            throw "Account '$UserName' is a built-in system account or group - use a normal user account."
        }
        if ($cfg.Password) { Info 'Password= is ignored for an existing account (setup never changes its password)' }
        if ($m.created.ContainsKey('user') -and $m.created['user']) {
            Warn 'cm-monitor created by an earlier setup still exists - run remove-cm-monitor.bat first if you do not need it'
        }
        $m.created['existing_account'] = $UserName
        $m.created['existing_sid'] = $userSid
        Ok "account $UserName ($userSid)"
    } else {
    $ExtAcct = ''
    Step '3/8 Local user cm-monitor (not an administrator)'
    $u = Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue
    # Password= in cm-monitor-config.txt: central chose it (download from Settings / install from the web page)
    # -> nothing to copy back. It is removed from the config file at the end of this run.
    $cfgPw = [string]$cfg.Password
    if ($cfgPw -and ($cfgPw.Length -lt 12 -or $cfgPw.Length -gt 128)) { throw 'Password in cm-monitor-config.txt must be 12-128 characters' }
    if ($null -eq $u) {
        if ($cfgPw) { $newPassword = $cfgPw; $pwFromCentral = $true } else { $newPassword = New-RandomPassword }
        $sec = ConvertTo-SecureString $newPassword -AsPlainText -Force
        $u = New-LocalUser -Name $UserName -Password $sec -Description $UserDesc -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires
        $m.created['user'] = $true
        Ok $(if ($pwFromCentral) { 'user created (password set by central)' } else { 'user created (random 24-character password)' })
    } else {
        if (-not $m.created.ContainsKey('user') -and $u.Description -ne $UserDesc) {
            throw "A user named '$UserName' already exists and was not created by this setup. Nothing was changed."
        }
        if ($cfgPw) {
            $newPassword = $cfgPw; $pwFromCentral = $true
            Set-LocalUser -Name $UserName -Password (ConvertTo-SecureString $newPassword -AsPlainText -Force)
            Ok 'existing user - password set by central (nothing to copy)'
        } elseif ($Options -contains 'resetpw') {
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
    }
    foreach ($gs in @($SID_PERF, $SID_EVT)) {
        $g = Get-LocalGroup -SID $gs
        # added by SID (works for local and domain accounts) - only groups added here are recorded / removed later
        try {
            Add-LocalGroupMember -Group $g -Member $userSid -ErrorAction Stop
            if ($ExtAcct) {
                $ga = @(); if ($m.created.ContainsKey('groups_added') -and $null -ne $m.created['groups_added']) { $ga = @($m.created['groups_added']) }
                if ($ga -notcontains $gs) { $m.created['groups_added'] = @($ga + $gs) }
            }
        } catch { if ($_.FullyQualifiedErrorId -notlike 'MemberExists*') { throw } }
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

    if ($ExtAcct) {
        Info 'existing account - logon rights not changed (DenyLogon applies to cm-monitor only)'
    } elseif ($cfg.DenyLogon -match '^(yes|y|true|1)$') {
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
    $keepAcl = @()
    if ($m.created.ContainsKey('acl_preexisting') -and $null -ne $m.created['acl_preexisting']) { $keepAcl = @($m.created['acl_preexisting']) }
    $prevAcl = @()
    if ($m.created.ContainsKey('acl_dirs') -and $null -ne $m.created['acl_dirs']) { $prevAcl = @($m.created['acl_dirs']) }
    foreach ($d in $prevAcl) {
        if ($grantDirs -notcontains $d -and [System.IO.Directory]::Exists($d)) {
            if ($keepAcl -notcontains $d) { & icacls.exe $d /remove:g "*$userSid" | Out-Null; Info "read permission removed (no longer listed): $d" }
        }
    }
    foreach ($d in $grantDirs) {
        if ($ExtAcct -and $prevAcl -notcontains $d -and $keepAcl -notcontains $d) {
            # the account already has its own permission here -> do not touch it now or at removal
            $own = @((Get-Acl -LiteralPath $d).Access | Where-Object { -not $_.IsInherited -and $(try { $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -eq $userSid } catch { $false }) })
            if ($own.Count -gt 0) { $keepAcl += $d; Info "existing permission of $UserName kept: $d"; continue }
        }
        if ($keepAcl -contains $d) { Ok "read permission (already had it): $d"; continue }
        & icacls.exe $d /grant "*${userSid}:(OI)(CI)RX" | Out-Null
        if ($LASTEXITCODE -eq 0) { Ok "read permission: $d" } else { Warn "icacls failed on $d (exit $LASTEXITCODE)" }
    }
    $m.created['acl_dirs'] = @($grantDirs | Where-Object { $keepAcl -notcontains $_ })
    if ($keepAcl.Count -gt 0) { $m.created['acl_preexisting'] = @($keepAcl) }
    if ($rules.Count -eq 0) { Info 'LogPaths is empty - no log files can be read (Event Log still works)' }
    Save-Manifest $m

    # ----------------------------------------------------------- certificate + HTTPS listener
    # CPU sockets / physical cores need WMI, which a non-admin JEA session cannot use: read them here (setup runs
    # as administrator) once and keep a small file the module returns in the inventory. Run setup again after a
    # hardware / vCPU change.
    try {
        $procs = @(Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop)
        $hw = [ordered]@{
            updated = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
            sockets = $procs.Count
            cores   = [int](($procs | Measure-Object -Property NumberOfCores -Sum).Sum)
            logical = [int](($procs | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum)
            max_mhz = [int](($procs | Measure-Object -Property MaxClockSpeed -Maximum).Maximum)
            model   = ([string]$procs[0].Name).Trim()
        }
        [System.IO.File]::WriteAllText((Join-Path $DataDir 'hardware.json'), ($hw | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding($false)))
        Ok "CPU: $($hw.sockets) socket(s), $($hw.cores) core(s), $($hw.logical) logical processor(s), $($hw.max_mhz) MHz"
    } catch { Warn "could not read the CPU core count (Win32_Processor): $($_.Exception.Message)" }

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
        -RoleDefinitions @{ $(if ($ExtAcct) { $UserName } else { $env:COMPUTERNAME + '\' + $UserName }) = @{ RoleCapabilities = 'CentralMonitor' } } `
        -Description 'Central Monitor read-only endpoint'
    if (-not (Test-PSSessionConfigurationFile -Path $pssc)) { throw 'the generated .pssc file is not valid' }
    if ($null -ne (Get-PSSessionConfiguration -Name $EpName -ErrorAction SilentlyContinue)) {
        Unregister-PSSessionConfiguration -Name $EpName -NoServiceRestart -Force
    }
    Register-PSSessionConfiguration -Name $EpName -Path $pssc -NoServiceRestart -Force | Out-Null
    $m.created['endpoint'] = $EpName
    Save-Manifest $m
    $quota = 'WSMan:\localhost\Plugin\' + $EpName + '\Quotas\'
    foreach ($q in @(@('MaxMemoryPerShellMB', 150), @('MaxShellsPerUser', 5), @('MaxShells', 5), @('MaxConcurrentUsers', 1), @('IdleTimeoutms', 300000))) {
        try { Set-Item -Path ($quota + $q[0]) -Value $q[1] -Force -WarningAction SilentlyContinue }
        catch { Warn "could not set $($q[0]) = $($q[1]): $($_.Exception.Message)" }
    }
    Info 'restarting WinRM once to apply (other remote PowerShell sessions on this machine reconnect)'
    Restart-Service -Name WinRM -Force
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Service WinRM).Status -ne 'Running' -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
    Start-Sleep -Seconds 2
    Ok 'endpoint registered: 150 MB per session, max 5 sessions, idle timeout 5 min'

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
    $errMsg = $_.Exception.Message
    Say "[FAIL] $errMsg" 'Red'
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
    if ($pwFromCentral) {
        Say '  Password     : (set by central - nothing to copy)'
    } elseif ($null -ne $newPassword) {
        Write-Host "  Password     : $newPassword" -ForegroundColor Yellow
        Set-Content -LiteralPath $CredPath -Value @("user=$UserName", "password=$newPassword", "thumbprint=$thumb", "port=$port") -Encoding ASCII
        & icacls.exe $CredPath /inheritance:r /grant:r "*${SID_ADM}:F" "*${SID_SYS}:F" | Out-Null
        Write-Host "  (also saved to $CredPath - administrators only. DELETE it after copying.)" -ForegroundColor Yellow
        Log 'new password shown on screen and saved to the credential file (not logged)'
    } elseif ($ExtAcct) {
        Say '  Password     : (existing account - its own password, enter it in central)'
    } else {
        Say '  Password     : (unchanged)'
    }
    if ($script:Warnings -gt 0) { Say "RESULT: DONE WITH $($script:Warnings) WARNING(S) - see above" 'Yellow'; $exitCode = 2 }
    else { Say 'RESULT: SUCCESS' 'Green' }
} else {
    Say 'RESULT: FAILED - see the message above and the log file' 'Red'
}
Say "log: $LogFile"

# the password from central must not stay in the config file next to the .bat
try {
    $cfgFile = Join-Path $Here 'cm-monitor-config.txt'
    if ((Test-Path -LiteralPath $cfgFile) -and (Select-String -LiteralPath $cfgFile -Pattern '^\s*Password\s*=\s*\S' -Quiet)) {
        $lines = @(Get-Content -LiteralPath $cfgFile | ForEach-Object { if ($_ -match '^\s*Password\s*=') { 'Password=' } else { $_ } })
        [System.IO.File]::WriteAllLines($cfgFile, [string[]]$lines, (New-Object System.Text.UTF8Encoding($true)))
    }
} catch { Log "could not clear Password in cm-monitor-config.txt: $($_.Exception.Message)" }

# result for the web installer (central reads it, then deletes the folder) - no password inside
try {
    [ordered]@{ ok = ($exitCode -ne 1); exit = $exitCode; error = $errMsg; thumbprint = $thumb; port = $port;
                warnings = $script:Warnings; computer = $env:COMPUTERNAME; version = $Version; account = $UserName } |
        ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $Here 'setup-result.json') -Encoding ASCII
} catch { }
exit $exitCode
