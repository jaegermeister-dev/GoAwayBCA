# Read-only local collection. Compatible with Windows PowerShell 2 syntax.
# No installation, elevation, network access, device changes or automatic upload.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$context = Join-Path $scriptDir 'context.txt'
if (!(Test-Path -LiteralPath $context -PathType Leaf)) { throw 'Keep context.txt beside this script.' }
$contextInfo = Get-Item -LiteralPath $context
if ($contextInfo.Length -gt 2048) { throw 'Invalid context length.' }
$contextText = [IO.File]::ReadAllText($context)
$match = [regex]::Match($contextText, '(?m)^session_id=([0-9a-f]{32})\r?$')
if (!$match.Success) { throw 'Invalid WinDeploy report context.' }
$sessionId = $match.Groups[1].Value
$desktop = [Environment]::GetFolderPath('Desktop')
$output = Join-Path $desktop ('WinDeploy-Windows-Report-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($output)
[IO.File]::WriteAllText((Join-Path $output 'context.txt'), $contextText)
$status = New-Object 'System.Collections.Generic.List[string]'
$status.Add('UTC: ' + [DateTime]::UtcNow.ToString('o'))
$status.Add('Session: ' + $sessionId)
$status.Add('Target context is inherited from macOS. This running Windows installation has NOT been matched to its GPT GUIDs. Compare OS/build and system-volume geometry before drawing conclusions.')
$status.Add('Bounded read-only evidence only. A detected device is not proof of functionality. No Windows settings were changed.')
$status.Add('Limitations: no memory/crash dumps, camera/audio capture, fan or sleep tests; no setup-log or private-file copy. Relevant System events are bounded to 512 recent events within 7 days, then filtered by hardware provider.')

function Save-Wmi([string]$name, [string]$class, [string[]]$properties) {
    try {
        $options = New-Object System.Management.EnumerationOptions
        $options.Timeout = [TimeSpan]::FromSeconds(20)
        $options.ReturnImmediately = $false
        $search = New-Object System.Management.ManagementObjectSearcher
        $search.Scope = New-Object System.Management.ManagementScope('root\cimv2')
        $search.Query = New-Object System.Management.ObjectQuery('SELECT * FROM ' + $class)
        $search.Options = $options
        try {
            $rows = @($search.Get() | Select-Object -First 4096 -Property $properties)
            [IO.File]::WriteAllText((Join-Path $output ($name + '.xml')), [string]($rows | ConvertTo-Xml -Depth 4 -As String))
            $status.Add($name + ': observed ' + $rows.Count + ' rows (limit 4096).')
        } finally { $search.Dispose() }
    } catch { $status.Add($name + ': unavailable/partial: ' + $_.Exception.Message) }
}

Save-Wmi 'os' 'Win32_OperatingSystem' @('Caption','Version','BuildNumber','OSArchitecture','SystemDrive','ServicePackMajorVersion','LastBootUpTime')
Save-Wmi 'model' 'Win32_ComputerSystem' @('Manufacturer','Model','SystemType','TotalPhysicalMemory')
Save-Wmi 'firmware' 'Win32_BIOS' @('Manufacturer','SMBIOSBIOSVersion','Version','ReleaseDate')
Save-Wmi 'cpu' 'Win32_Processor' @('Name','Manufacturer','Architecture','NumberOfCores','NumberOfLogicalProcessors','MaxClockSpeed','CurrentClockSpeed')
Save-Wmi 'devices' 'Win32_PnPEntity' @('Name','PNPDeviceID','HardwareID','CompatibleID','ClassGuid','Service','Status','ConfigManagerErrorCode')
Save-Wmi 'drivers' 'Win32_PnPSignedDriver' @('DeviceName','DeviceID','DriverVersion','DriverDate','DriverProviderName','InfName','IsSigned','Signer','DeviceClass')
Save-Wmi 'graphics' 'Win32_VideoController' @('Name','PNPDeviceID','DriverVersion','VideoModeDescription','CurrentHorizontalResolution','CurrentVerticalResolution','ConfigManagerErrorCode')
Save-Wmi 'audio' 'Win32_SoundDevice' @('Name','PNPDeviceID','Status','ConfigManagerErrorCode')
Save-Wmi 'network-devices' 'Win32_NetworkAdapter' @('Name','PNPDeviceID','AdapterType','PhysicalAdapter','NetEnabled','ConfigManagerErrorCode')
Save-Wmi 'battery' 'Win32_Battery' @('Name','BatteryStatus','EstimatedChargeRemaining','EstimatedRunTime','Status')
Save-Wmi 'disk-geometry' 'Win32_DiskDrive' @('Index','Model','InterfaceType','Size','BytesPerSector','Partitions','PNPDeviceID')
Save-Wmi 'partitions' 'Win32_DiskPartition' @('DeviceID','DiskIndex','Index','StartingOffset','Size','Type','BootPartition','PrimaryPartition')
Save-Wmi 'logical-disks' 'Win32_LogicalDisk' @('DeviceID','FileSystem','VolumeName','Size','FreeSpace','DriveType')
Save-Wmi 'volume-associations' 'Win32_LogicalDiskToPartition' @('Antecedent','Dependent','StartingAddress','EndingAddress')
Save-Wmi 'device-resources' 'Win32_PNPAllocatedResource' @('Antecedent','Dependent')
Save-Wmi 'memory-resources' 'Win32_DeviceMemoryAddress' @('Name','StartingAddress','EndingAddress')
Save-Wmi 'irq-resources' 'Win32_IRQResource' @('Name','IRQNumber','Availability','Shareable')
Save-Wmi 'port-resources' 'Win32_PortResource' @('Name','StartingAddress','EndingAddress')
try {
    $events = @(Get-WinEvent -FilterHashtable @{LogName='System';StartTime=(Get-Date).AddDays(-7)} -MaxEvents 512 |
        Where-Object { $_.ProviderName -match '(?i)(Kernel-PnP|UserPnp|WHEA|ACPI|Display|amdkmd|igfx|Disk|Ntfs|stor|USB|NDIS|Netwtw|Broadcom|BugCheck)' } |
        Select-Object TimeCreated,Id,LevelDisplayName,ProviderName,@{Name='Message';Expression={
            $text=[string]$_.Message; if ($text.Length -gt 8192) { $text.Substring(0,8192) } else { $text }
        }})
    [IO.File]::WriteAllText((Join-Path $output 'hardware-events.xml'), [string]($events | ConvertTo-Xml -Depth 4 -As String))
    $status.Add('Hardware events: observed (bounded); no match is not proof that no errors occurred.')
} catch { $status.Add('Hardware events: unavailable/partial: ' + $_.Exception.Message) }
try {
    $crashes = @(Get-ChildItem -LiteralPath (Join-Path $env:WINDIR 'Minidump') |
        Where-Object { !$_.PSIsContainer -and $_.Extension -eq '.dmp' } |
        Select-Object -First 32 Name,Length,LastWriteTimeUtc)
    [IO.File]::WriteAllText((Join-Path $output 'crash-file-presence.xml'), [string]($crashes | ConvertTo-Xml -Depth 4 -As String))
    $status.Add('Crash files: names/dates/sizes only; dump contents were not read.')
} catch { $status.Add('Crash-file presence: unavailable (folder may not exist).') }
[IO.File]::WriteAllLines((Join-Path $output 'COLLECTION-STATUS.txt'), $status.ToArray())
$readMe = @'
Review every file before sharing. Device IDs, resource-association paths, event messages and volume labels can identify your machine or attached peripherals. No anonymization guarantee is made.
Include these files with the matching macOS/EFI report. Write down which Windows install you actually booted, which boot-menu entry/fix mode you used, and what hardware fails or works. The inherited target is NOT automatically verified as the current Windows installation.
No settings, drivers, services, boot files or firewall rules were changed. Nothing was uploaded. This collector writes only this new report folder.
'@
[IO.File]::WriteAllText((Join-Path $output 'READ-ME-FIRST.txt'), $readMe)
$hashes = @()
foreach ($file in (Get-ChildItem -LiteralPath $output | Where-Object { !$_.PSIsContainer })) {
    $stream = [IO.File]::OpenRead($file.FullName)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hashes += ([BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLower() + '  ' + $file.Name) }
    finally { $stream.Dispose(); $sha.Dispose() }
}
[IO.File]::WriteAllLines((Join-Path $output 'SHA256SUMS.txt'), [string[]]$hashes)
Write-Host ('Report saved: ' + $output)
Write-Host 'Review COLLECTION-STATUS.txt for missing or partial evidence. No hardware fixes were applied.'
