"""Read-only launch gate: hybrid MBR and MBR Windows are unsupported.

Inspect physical media, including attached disks, not virtual disk images.
An ordinary GPT protective MBR is allowed. An MBR data disk is not by itself
a Windows installation. Unreadable checks are reported as inconclusive.
"""
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import tempfile
import sys
from install_windows7 import (Installer, require, raw_info, plist_command, media_size,
                              write_receipt, Refusal)
from deployment_lock import DeploymentLock
from partition_detection import registry_index

BLOCK = ('THIS CONFIGURATION IS NOT SUPPORTED.\n\n'
         'GOAWAYBCA CANNOT BE USED WITH A HYBRID MBR DISK OR AN MBR-BASED WINDOWS '
         'INSTALLATION. IT SUPPORTS PURE GPT/EFI ONLY AND WILL NEVER SUPPORT HYBRID MBR '
         'OR MBR WINDOWS.\n\nTHIS BLOCK PROTECTS YOUR EXISTING INSTALLATIONS: CHANGING '
         'THEIR BOOT CONFIGURATION WITH THIS TOOL COULD MAKE THEM UNBOOTABLE. '
         'BACK UP YOUR DATA AND RESTORE THE MAC TO A PURE GPT/EFI CONFIGURATION BEFORE '
         'USING GOAWAYBCA. THIS APP DOES NOT CONVERT OR REPARTITION DISKS.')


def classify_mbr(sector, gpt_signature):
    if len(sector)!=512:raise ValueError('Could not read the complete MBR sector')
    if sector[510:]!=b'\x55\xaa':
        if gpt_signature:raise ValueError('GPT disk has an invalid protective MBR')
        return 'other'
    rows=[]
    for index in range(4):
        entry=sector[446+16*index:462+16*index]
        kind=entry[4];start,size=struct.unpack_from('<II',entry,8)
        if kind or start or size:
            if not kind or not size:raise ValueError('Inconsistent MBR partition entry')
            rows.append((kind,start,size))
    protective=[r for r in rows if r[0]==0xee]
    ordinary=[r for r in rows if r[0]!=0xee]
    if gpt_signature and ordinary:return 'hybrid'
    if protective and ordinary:return 'hybrid'
    if protective:
        if not gpt_signature or len(protective)!=1 or protective[0][1]!=1:
            raise ValueError('Invalid protective MBR/GPT combination')
        return 'gpt'
    if gpt_signature:raise ValueError('GPT disk is missing its protective MBR')
    return 'mbr' if rows else 'empty'


def inspect_layout(device, block_size):
    require(re.fullmatch(r'disk[0-9]+',device),'Invalid physical disk identifier')
    require(block_size in (512,4096),'Unknown physical disk block size')
    path='/dev/'+device
    require(stat.S_ISBLK(os.stat(path).st_mode),'Expected physical disk block device')
    with open(path,'rb',buffering=0) as stream:
        mbr=stream.read(512);stream.seek(block_size);gpt=stream.read(8)==b'EFI PART'
    return classify_mbr(mbr,gpt)


def mounted_names(mount, device_id):
    import ntfs_read
    if os.listdir in os.supports_fd:
        return ntfs_read._mounted_names(mount,device_id)
    result=subprocess.run([sys.executable,'-I','-B',str(Path(ntfs_read.__file__).resolve()),
        '--root-names-child',mount,str(device_id)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
    require(result.returncode==0 and len(result.stdout)<=4*1024**2,'Cannot inspect mounted MBR partition')
    return json.loads(result.stdout.decode('utf-8'))


def mbr_windows(partition, ntfscat):
    """Return true for a Windows directory plus SYSTEM hive on an MBR volume.

    A winload.exe file alone proves nothing: EFI Windows has that file too.
    Never mount or write an MBR volume just to inspect it.
    """
    from ntfs_read import mounted_read,raw_read
    device=partition['DeviceIdentifier']
    require(re.fullmatch(r'disk[0-9]+s[1-9][0-9]*',device),'Invalid MBR partition identifier')
    path='/dev/'+device;node=os.stat(path)
    require(stat.S_ISBLK(node.st_mode),'Expected MBR partition device')
    mount=partition.get('MountPoint') or ''
    fs=str(partition.get('FilesystemType',partition.get('FileSystemPersonality',''))).lower()
    if mount:
        names=mounted_names(mount,node.st_rdev)
    elif fs=='ntfs' or partition.get('Content')=='Windows_NTFS':
        result=subprocess.run([str(Path(ntfscat).with_name('ntfsls')),'-a','-p','/',path],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60,env=dict(os.environ,LC_ALL='C'))
        require(result.returncode==0 and len(result.stdout)<=4*1024**2,'Cannot inspect unmounted MBR NTFS partition')
        names=result.stdout.decode('utf-8').splitlines()
    else:
        # Linux/HFS partitions cannot host supported Windows installations.
        # An unmounted Windows-capable volume is inconclusive, never a pass.
        require(not (fs in ('msdos','fat','fat32','exfat') or
            'FAT' in str(partition.get('Content','')).upper()),
            'Mount the MBR FAT/exFAT data volume read-only so Windows presence can be checked')
        return False
    matches=[n for n in names if n.casefold() in ('windows','winnt')]
    if not matches:return False
    for name in matches:
        target='/'+name+'/System32/config/SYSTEM'
        try:
            if mount:data=mounted_read(mount,target,node.st_rdev,4096,prefix=True)
            else:data=raw_read(ntfscat,path,target,256*1024**2)
        except (OSError,Refusal) as error:
            raise ValueError('A Windows-like directory exists on '+device+' but its identity could not be checked: '+str(error))
        if len(data)>=4096 and data[:4]==b'regf':return True
    raise ValueError('A Windows-like directory exists on '+device+' but its SYSTEM hive is not identifiable')


def inspect(ntfscat, listing=None, registry=None, get_info=raw_info,
            layout=inspect_layout, windows=mbr_windows):
    listing=listing if listing is not None else plist_command('/usr/sbin/diskutil','list','-plist')
    registry=registry if registry is not None else registry_index(plist_command(
        '/usr/sbin/ioreg','-a','-l','-r','-c','IOBlockStorageDevice'))
    physical={name for name,row in registry.items() if row['node'].get('Whole') is True}
    listed={r['DeviceIdentifier']:r for r in listing.get('AllDisksAndPartitions',[])}
    require(physical and physical<=set(listed),'Physical disk inventory is incomplete')
    result=dict(schema=1,physical_writes=False,disks=[],blocked=[],errors=[])
    for device in sorted(physical):
        try:
            disk=get_info(device);before=(disk.get('DeviceIdentifier'),media_size(disk))
            mode=layout(device,disk.get('DeviceBlockSize'))
            scheme=disk.get('Content')
            require(scheme!='GUID_partition_scheme' or mode in ('gpt','hybrid'),
                    'GPT metadata and the disk sector disagree: '+device)
            require(scheme!='FDisk_partition_scheme' or mode in ('mbr','hybrid'),
                    'MBR metadata and the disk sector disagree: '+device)
            result['disks'].append(dict(device=device,layout=mode))
            if mode=='hybrid':result['blocked'].append(device+': hybrid MBR')
            elif mode=='mbr':
                parts=listed[device].get('Partitions',[])
                require(parts,'MBR partition inventory is incomplete: '+device)
                for item in parts:
                    name=item['DeviceIdentifier'];part=get_info(name)
                    if windows(part,ntfscat):result['blocked'].append(name+': MBR Windows installation')
                    after=get_info(name)
                    fields=('DeviceIdentifier','ParentWholeDisk','PartitionMapPartitionOffset',
                            'DiskUUID','MountPoint','ReadOnlyVolume','FilesystemType','Content')
                    require(all(part.get(k)==after.get(k) for k in fields) and
                        media_size(part)==media_size(after),'MBR partition identity changed while being inspected')
            after=get_info(device)
            require(before==(after.get('DeviceIdentifier'),media_size(after)),'Disk identity changed during launch check')
        except (OSError,ValueError,Refusal,KeyError) as error:
            result['errors'].append(device+': '+str(error))
    result['allowed']=not result['blocked'] and not result['errors']
    return result


def enforce(result):
    if result['blocked']:
        raise ValueError(BLOCK+'\n\nDETECTED: '+ '; '.join(result['blocked']).upper())
    if result['errors']:
        raise ValueError('Disk compatibility could not be checked. GoAwayBCA will not open '
            'until the check completes. No partition or boot file was changed.\n\n'+
            '\n'.join(result['errors'])+'\n\nSave this message when reporting the problem.')


SIP_GUIDE = ('To use the automatic disk-layout check, quit GoAwayBCA, restart into macOS Recovery '
    '(hold Command-R), open Utilities > Terminal, and run: csrutil disable\n'
    'Restart into macOS and open GoAwayBCA again. The app will automatically check the layout. '
    'Disabling SIP temporarily reduces macOS system protection; use this only if you understand '
    'that change. A complete backup is strongly recommended.\n\n'
    'REENABLE SIP after finishing the Windows installations: restart into Recovery, run '
    'csrutil enable in Terminal, and restart. GoAwayBCA never changes SIP itself. '
    'You can also reenable SIP before installation and use the manual confirmations; '
    'SIP can remain enabled when using the manual confirmation route.')


def sip_status():
    import platform
    try:
        version=tuple(int(x) for x in platform.mac_ver()[0].split('.')[:2])
        if version and version < (10,11):
            return dict(status='unavailable-on-this-macos',automatic=True)
        p=subprocess.run(['/usr/bin/csrutil','status'],stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,timeout=15,env=dict(os.environ,LC_ALL='C'))
        text=p.stdout.decode('utf-8','replace').strip()
        if p.returncode==0 and re.search(r'status:\s*disabled\.',text,re.I):
            return dict(status='disabled',automatic=True,detail=text)
        if p.returncode==0 and re.search(r'status:\s*enabled\.',text,re.I):
            return dict(status='enabled',automatic=False,detail=text)
        return dict(status='partial-or-unknown',automatic=False,detail=text)
    except (OSError,ValueError,subprocess.TimeoutExpired) as error:
        return dict(status='unknown',automatic=False,detail=str(error))


class StartupCheck(Installer):
    def execute(self):
        self.tools()
        self.deployment_lock=DeploymentLock().acquire()
        base=Path(self.args.work_dir);base.mkdir(parents=True,exist_ok=True)
        self.session=Path(tempfile.mkdtemp(prefix='startup-check-',dir=str(base)))
        os.chmod(self.session,0o700)
        from drive_geometry import collect
        self.drive_geometry = collect(self.note)
        write_receipt(self.session/'drive-geometry.json',self.drive_geometry)
        sip=sip_status()
        write_receipt(self.session/'sip-status.json',sip)
        self.note('System Integrity Protection: '+sip['status'])
        if not sip['automatic']:
            require(self.interaction is not None,'Use the app for the launch confirmations.')
            action=self.interaction.request('sip_choice',
                'SIP is enabled, partially enabled, or could not be conclusively identified. '
                'macOS may deny the whole-disk read used by the automatic hybrid/MBR check. '
                'Choose Keep SIP enabled to confirm the layout yourself, or Disable SIP instructions '
                'to learn about temporarily disabling SIP. If SIP is already disabled, or you '
                'want to try the automatic check without changing your settings, choose '
                'Continue as is / SIP is disabled. That option attempts the same read-only '
                'check and stops if the disk cannot be read or its layout is incompatible. '
                'GoAwayBCA does not change SIP.',status=sip['status'])
            if action=='guide':
                raise ValueError(SIP_GUIDE)
            require(action in ('manual','automatic'),'Disk-layout check cancelled. No disk or boot files were changed.')
            write_receipt(self.session/'sip-choice.json',dict(choice=action,detected_status=sip['status'],sip_changed=False))
            if action=='manual':
                from mac_compatibility import load
                model=subprocess.check_output(['/usr/sbin/sysctl','-n','hw.model']).decode().strip()
                modes=load()['models'].get(model,{}).get('boot_camp_installation_mode',{})
                reply=self.interaction.request('layout_declaration',
                    'Confirm your current disk layout.',model=model,boot_modes=modes)
                require(isinstance(reply,dict) and reply.get('confirmed') is True,
                        'Disk-layout confirmation cancelled. No disk or boot files were changed.')
                write_receipt(self.session/'startup-layout-consent.json',dict(schema=1,
                    automatic_layout_check=False,basis='user declaration',declaration=reply,sip=sip))
                self.note('User confirmed no hybrid MBR or MBR Windows. The app has not inspected MBR '
                          'contents in this mode. Administrator session ready.')
                return
            self.note('Trying the automatic read-only disk-layout check with current permissions. SIP settings are unchanged.')
        self.note('Checking physical disks for hybrid MBR and MBR Windows installations (read-only).')
        result=inspect(self.ntfscat)
        write_receipt(self.session/'startup-disk-check.json',result)
        enforce(result)
        self.note('Disk compatibility check completed: no hybrid MBR or MBR Windows installation detected.')
