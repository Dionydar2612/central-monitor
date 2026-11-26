# =====================================================================
# cm-inventory.ps1 (Central Monitor cm-agent) - machine info, installed programs, listening TCP ports
# -> textfile for windows_exporter (%ProgramData%\central-monitor-agent\textfile\cm_inventory.prom)
# Scheduled task "CentralMonitorInventory" (SYSTEM, low priority) - interval = InventoryMinutes.
# Registry + .NET only (no WMI queries) to keep the load low. ASCII only.
# =====================================================================
$ErrorActionPreference = 'Continue'
$Dir = Join-Path $env:ProgramData 'central-monitor-agent\textfile'
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
function Esc($s) { if ($null -eq $s) { return '' }; return ([string]$s -replace '\\', '\\' -replace '"', '\"' -replace "[\r\n\t]+", ' ').Trim() }
function Reg($path, $name) { try { return (Get-ItemProperty -Path "HKLM:\$path" -Name $name -ErrorAction Stop).$name } catch { return $null } }
$L = New-Object System.Collections.Generic.List[string]

# ---- machine ----
$cv = 'SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$build = [string](Reg $cv 'CurrentBuild'); $ubr = Reg $cv 'UBR'
$inst = Reg $cv 'InstallDate'
$install = if ($inst) { ([datetime]'1970-01-01').AddSeconds([double]$inst).ToString('yyyy-MM-ddTHH:mm:ssZ') } else { '' }
$bios = 'HARDWARE\DESCRIPTION\System\BIOS'
$dom = ''
try { $dom = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().DomainName } catch { }
$L.Add('# TYPE cm_host_info gauge')
$L.Add(('cm_host_info{{caption="{0}",edition="{1}",release="{2}",build="{3}",install="{4}",manufacturer="{5}",model="{6}",domain="{7}",tz="{8}"}} 1' -f
    (Esc (Reg $cv 'ProductName')), (Esc (Reg $cv 'EditionID')), (Esc (Reg $cv 'DisplayVersion')),
    (Esc $(if ($null -ne $ubr) { "$build.$ubr" } else { $build })), (Esc $install),
    (Esc (Reg $bios 'SystemManufacturer')), (Esc (Reg $bios 'SystemProductName')), (Esc $dom), (Esc ([TimeZoneInfo]::Local.Id))))

$L.Add('# TYPE cm_nic_ip gauge')
try {
    foreach ($ni in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
        if ($ni.NetworkInterfaceType -eq [System.Net.NetworkInformation.NetworkInterfaceType]::Loopback) { continue }
        foreach ($a in $ni.GetIPProperties().UnicastAddresses) {
            $ip = $a.Address.ToString()
            if ($ip -like 'fe80*' -or $ip -like '169.254.*') { continue }
            $L.Add(('cm_nic_ip{{nic="{0}",ip="{1}"}} 1' -f (Esc $ni.Name), (Esc $ip)))
        }
    }
} catch { }

$L.Add('# TYPE cm_hotfix gauge')
try {
    $hk = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\Packages'
    $seen = @{}
    Get-ChildItem $hk -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.PSChildName -notmatch 'KB(\d{6,8})') { return }
        $kb = 'KB' + $Matches[1]
        if ($seen.ContainsKey($kb)) { return }
        $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
        if ($p -and $p.CurrentState -eq 112 -and $p.InstallTimeHigh) {
            $ft = ([long]$p.InstallTimeHigh -shl 32) -bor ([long]$p.InstallTimeLow -band 0xFFFFFFFFL)
            $seen[$kb] = [DateTime]::FromFileTimeUtc($ft).ToString('yyyy-MM-dd')
        }
    }
    $seen.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 5 | ForEach-Object {
        $L.Add(('cm_hotfix{{id="{0}",date="{1}"}} 1' -f $_.Key, $_.Value))
    }
} catch { }

# ---- installed programs ----
$L.Add('# TYPE cm_software_installed gauge')
$seen = @{}
$keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
foreach ($p in @(Get-ItemProperty $keys -ErrorAction SilentlyContinue)) {
    $pn = $p.PSObject.Properties
    if (-not $pn['DisplayName'] -or -not $p.DisplayName) { continue }
    if (($pn['SystemComponent'] -and $p.SystemComponent -eq 1) -or ($pn['ParentKeyName'] -and $p.ParentKeyName)) { continue }
    if ($pn['ReleaseType'] -and "$($p.ReleaseType)" -match 'Update|Hotfix') { continue }
    $arch = if ($p.PSPath -match 'WOW6432Node') { 'x86' } else { 'x64' }
    $n = Esc $p.DisplayName; $v = if ($pn['DisplayVersion']) { Esc $p.DisplayVersion } else { '' }
    $k = "$n|$v|$arch"
    if ($seen.ContainsKey($k)) { continue }
    $seen[$k] = 1
    $pub = if ($pn['Publisher']) { Esc $p.Publisher } else { '' }
    $idt = if ($pn['InstallDate']) { Esc $p.InstallDate } else { '' }
    $L.Add(('cm_software_installed{{name="{0}",version="{1}",publisher="{2}",installed="{3}",arch="{4}"}} 1' -f $n, $v, $pub, $idt, $arch))
}

# ---- listening TCP ports ----
$L.Add('# TYPE cm_port_listening gauge')
$procs = @{}
Get-Process -ErrorAction SilentlyContinue | ForEach-Object { $procs[[int]$_.Id] = $_.ProcessName }
$seenP = @{}
foreach ($c in @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)) {
    $k = "$($c.LocalPort)|$($c.LocalAddress)"
    if ($seenP.ContainsKey($k)) { continue }
    $seenP[$k] = 1
    $L.Add(('cm_port_listening{{port="{0}",address="{1}",process="{2}"}} 1' -f $c.LocalPort, (Esc $c.LocalAddress), (Esc $procs[[int]$c.OwningProcess])))
}

$L.Add('# TYPE cm_inventory_timestamp_seconds gauge')
$L.Add("cm_inventory_timestamp_seconds $([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())")
$tmp = Join-Path $Dir 'cm_inventory.prom.tmp'
[IO.File]::WriteAllText($tmp, (($L -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))
Move-Item $tmp (Join-Path $Dir 'cm_inventory.prom') -Force
