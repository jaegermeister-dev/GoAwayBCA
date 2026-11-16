# Exact 11,5 helper repair if its public alias was replaced by the known loader.
# Reads current GPT identities; never modifies OpenCore, BCD or firmware boot order.
function AssertGraphicsMenuRoute($record,$entry,[string]$letter) {
 $helper='e883cafc83e127c09ccec1c6cef96d4cf0b2f39242ba66e68adaa0279c2d7c07'
 if([string]$record.SectorLoader){
  $sector='9561d32c599637021d19f766dbe5d91ee699454fe3bb845712a2e2a5ac14a21b'
  $part=([Guid]([string]$record.WindowsGuid)).ToString()
  $folder='EFI/WinDeploy/Installs/'+$part+'/'
  if($record.SectorLoader-ne($folder+'SectorBoot.efi')-or$record.SectorHelperSha256-ne$sector-or$record.SectorNextSha256-ne$helper){throw 'Unexpected recorded sector/graphics route'}
  if($entry['loader_path']-ne$record.SectorLoader-or$entry['loader_sha256']-ne$sector-or$entry['boot_partition_guid']-ne$record.EspGuid-or$entry['requires_ntfs']-ne$false-or$entry['hash_locked']-ne$true){throw 'Shared menu differs from the recorded sector/graphics route'}
  $local=$letter+'\'+$folder.Replace('/','\')
  if((Hash ($local+'SectorBoot.efi'))-ne$sector-or(Hash ($local+'SectorNext.efi'))-ne$helper){throw 'Sector/graphics component is missing or changed'}
  if((Hash ($letter+'\EFI\Microsoft\Boot\bootmgfw-w7dep-original.efi'))-ne'6470d503fc884f23b515457a03018ece8bf6f6c5bd358f3b5518711ebadb5161'-or(Hash ($letter+'\EFI\W7DEP-WIN7-AUTO\ALLOW-AUTO.txt'))-ne'784bd1fbe529be3c24c82daf84e92ce972792d46ca87a7fb48be899fc1dc10ed'){throw 'Sector/graphics child dependencies differ'}
 }else{
  if($entry['loader_path']-ne'EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi'-or(Hash ($letter+'\EFI\W7DEP-WIN7-AUTO\WIN7AUTO.efi'))-ne$helper){throw 'Shared menu retained; this installation needs a menu-aware repair from the macOS app'}
 }
}

function RepairGraphicsBoot {
 if($ExpectedModel-ne'MacBookPro11,5'){return}
 Add-Type -Path (Join-Path $PackRoot 'Boot-Partition-Identity.cs')
 Add-Type -Path @((Join-Path $PackRoot 'Boot-Store-Guard.cs'),(Join-Path $PackRoot 'Menu-Store-Guard.cs'))
 $system=[IO.Path]::GetPathRoot($env:WINDIR).TrimEnd('\').ToUpperInvariant()
 $binding=$system+'\BackToSeven\Boot-Binding.xml'
 if(!(Test-Path -LiteralPath $binding)){throw 'Missing installation boot binding; no EFI repair attempted'}
 $xml=New-Object Xml.XmlDocument;$xml.XmlResolver=$null;$xml.Load($binding)
 $record=$xml.BackToSeven
 if($record.Model-ne'MacBookPro11,5'){throw 'Unexpected boot-binding model'}
 $current=[BootPartitionIdentity]::Read($system)
 if($current[0]-ne$record.DiskGuid-or$current[1]-ne$record.WindowsGuid-or$current[2]-ne'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'){throw 'Windows boot partition differs from the installation record'}
 $original='6470d503fc884f23b515457a03018ece8bf6f6c5bd358f3b5518711ebadb5161'
 $helper='e883cafc83e127c09ccec1c6cef96d4cf0b2f39242ba66e68adaa0279c2d7c07'
 $letter=$null
 foreach($char in @('S','T','U','V','W','X','Y','Z')){if(!([IO.Directory]::GetLogicalDrives()-contains($char+':\'))){$letter=$char+':';break}}
 if(!$letter){throw 'No free drive letter for temporary EFI access'}
 $mounted=$false
 try{
  & "$env:WINDIR\System32\mountvol.exe" $letter /S | Out-Null
  if($LASTEXITCODE-ne0){throw 'Could not mount current system EFI partition'};$mounted=$true
  $esp=[BootPartitionIdentity]::Read($letter)
  if($esp[0]-ne$record.DiskGuid-or$esp[1]-ne$record.EspGuid-or$esp[2]-ne'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'){throw 'EFI identity differs; no repair applied'}
  $catalog=$letter+'\EFI\WinDeploy\catalog.json'
  if(Test-Path -LiteralPath $catalog){
   # A shared picker controls its own per-installation stores. Never replace
   # its public alias with a standalone graphics helper.
   Add-Type -AssemblyName System.Web.Extensions
   $serializer=New-Object Web.Script.Serialization.JavaScriptSerializer
   $text=[IO.File]::ReadAllText($catalog)
   if($text.Length-gt1048576){throw 'Oversize shared-menu catalogue retained'}
   $menu=$serializer.DeserializeObject($text)
   $entry=@($menu['entries']|Where-Object {$_['disk_guid']-eq$record.DiskGuid-and$_['partition_guid']-eq$record.WindowsGuid})
   if($menu['schema']-ne1-or$entry.Count-ne1){throw 'Shared menu does not identify this installation uniquely'}
   AssertGraphicsMenuRoute $record $entry[0] $letter
   $pickerHash=Hash ($letter+'\EFI\Microsoft\Boot\bootmgfw.efi')
   $knownPickers=@('f581308710fbf74d260496f41300581fb4179c289021e0938e3aef276171247b','6e09756631c7cb94914723f84403e902892b5a16fc8784023d96c9e1ab29bee7','f00a1114201cca0e23f5e751789a1671215101d56d6701d3fc37e4dd0c90b21d','3138e9c51d291be0ad25e8e06ae6ed05b85295cb2c83f12b7239c506acaca49c','96c358eed054e5c2a12307e47add7fd5b4f091fdf48be26950bc2cad671f4528','f77bc2f4d73bed564fdeb423c8707aeea383e674f88a9621a482901c40bfdaa9','9b60ae395259a0171f0d78b3b2e8efd4a59e5aac36e1c67be615ea1f48a2485c')
   $picker=Hash ($letter+'\EFI\WinDeploy\Windows.efi')
   if($knownPickers-notcontains$picker){throw 'Unrecognised saved picker retained for explicit macOS recovery'}
   $export=Join-Path $PackRoot ('menu-system-BCD-export-'+[Guid]::NewGuid().ToString('N'))
   & "$env:WINDIR\System32\bcdedit.exe" /export $export | Out-Null
   if($LASTEXITCODE-ne0){throw 'Could not snapshot the current shared boot store'}
   [MenuStoreGuard]::Validate($letter+'\',$record.DiskGuid,$record.EspGuid,$record.WindowsGuid,$export,$entry[0]['loader_path'],$entry[0]['loader_sha256'])
   if($knownPickers-notcontains$pickerHash){
    if($pickerHash-ne$original-or(Hash ($letter+'\EFI\WinDeploy\Windows.efi'))-ne$picker){throw 'Unknown shared boot entry retained for explicit macOS recovery'}
    $backup=Join-Path $PackRoot ('backup-menu-alias-'+[Guid]::NewGuid().ToString('N'));[void][IO.Directory]::CreateDirectory($backup)
    $target=$letter+'\EFI\Microsoft\Boot\bootmgfw.efi';$copy=Join-Path $backup 'bootmgfw.before.efi'
    [IO.File]::Copy($target,$copy,$false);if((Hash $copy)-ne$original){throw 'Menu-alias backup failed'}
    [IO.File]::Copy(($letter+'\EFI\WinDeploy\Windows.efi'),$target,$true)
    if((Hash $target)-ne$picker){[IO.File]::Copy($copy,$target,$true);throw 'Menu-alias repair failed'}
    $script:RebootNeeded=$true;$script:GraphicsBootRepaired=$true
    Note 'Restored the existing verified multi-Windows picker alias. The per-installation stores, other entries and OpenCore were preserved. Restart required.'
    return
   }
   Note 'Existing multi-Windows picker already routes this installation through the graphics helper. All menu entries and private boot stores are preserved.'
   return
  }
  # Other directories may contain independent loaders or old diagnostics.
  # Preserve them byte-for-byte. A proven current BCD/partition binding and
  # the exact known alias/helper bytes authorize this one-file repair.
  if(Test-Path -LiteralPath ($letter+'\EFI\WinDeploy\menu.bin')){throw 'Uncatalogued shared menu retained for explicit recovery'}
  $export=Join-Path $PackRoot ('graphics-system-BCD-export-'+[Guid]::NewGuid().ToString('N'))
  & "$env:WINDIR\System32\bcdedit.exe" /export $export | Out-Null
  if($LASTEXITCODE-ne0){throw 'Could not snapshot the active boot store; no EFI repair'}
  [BootStoreGuard]::Validate($export,$record.DiskGuid,$record.EspGuid,$record.WindowsGuid)
  $target=$letter+'\EFI\Microsoft\Boot\bootmgfw.efi'
  $saved=$letter+'\EFI\Microsoft\Boot\bootmgfw-w7dep-original.efi'
  $source=$letter+'\EFI\W7DEP-WIN7-AUTO\WIN7AUTO.efi'
  if((Hash $saved)-ne$original-or(Hash $source)-ne$helper-or(Hash ($letter+'\EFI\W7DEP-WIN7-AUTO\ALLOW-AUTO.txt'))-ne'784bd1fbe529be3c24c82daf84e92ce972792d46ca87a7fb48be899fc1dc10ed'){throw 'Original loader/helper provenance differs'}
  $before=Hash $target
  if($before-eq$helper){Note '11,5 graphics EFI route already correct.';return}
  if($before-ne$original){throw 'Unknown EFI loader retained without modification'}
  $backup=Join-Path $PackRoot ('backup-graphics-efi-'+[Guid]::NewGuid().ToString('N'));[void][IO.Directory]::CreateDirectory($backup)
  $copy=Join-Path $backup 'bootmgfw.before.efi';[IO.File]::Copy($target,$copy,$false)
  if((Hash $copy)-ne$original){throw 'EFI backup verification failed'}
  $others=@(Get-ChildItem -LiteralPath ($letter+'\EFI') -Recurse | Where-Object {!$_.PSIsContainer-and$_.Name-notlike'BCD*'-and$_.FullName-ne$target} | ForEach-Object {New-Object PSObject -Property @{Path=$_.FullName;SHA256=(Hash $_.FullName)}})
  $others|Export-Clixml (Join-Path $backup 'other-efi-before.xml')
  try{
   [IO.File]::Copy($source,$target,$true)
   if((Hash $target)-ne$helper){throw 'EFI repair readback failed'}
   foreach($row in $others){if((Hash $row.Path)-ne$row.SHA256){throw 'Unrelated EFI file changed'}}
  }catch{if((Hash $target)-eq$helper){[IO.File]::Copy($copy,$target,$true)};throw}
  ('Restored only the known 11,5 graphics-helper alias. The process that replaced it was not observed. Disk='+$record.DiskGuid+' ESP='+$record.EspGuid+'. OpenCore/BCD/firmware untouched. Rollback only if current alias matches '+$helper+': restore the backed-up file with hash '+$original+'.')|Out-File (Join-Path $backup 'receipt.txt')
  $script:RebootNeeded=$true;$script:GraphicsBootRepaired=$true
  Note '11,5 graphics EFI route repaired. A normal restart is required before acceleration can be assessed.'
 }finally{if($mounted){& "$env:WINDIR\System32\mountvol.exe" $letter /D | Out-Null;if($LASTEXITCODE-ne0){throw 'Temporary EFI drive letter could not be removed'}}}
}
