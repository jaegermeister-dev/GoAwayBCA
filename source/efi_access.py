"""User-requested EFI mounting; never formats or edits boot files."""
import os
from pathlib import Path
import pwd
import uuid
from manage_installations import InstallationManager
from install_windows7 import raw_info,plist_command,require,run,write_receipt,media_size


def info(device):
    """EFI browsing metadata, independent of internal-only Windows destinations.

    Older diskutil schemas need IORegistry fields too, but must not send USB
    EFI partitions through the installer's internal-drive session guard.
    No raw whole-disk read or change to the installation guard is involved.
    """
    from partition_detection import registry_index
    value=raw_info(device)
    registry=registry_index(plist_command('/usr/sbin/ioreg','-a','-l','-r','-c','IOBlockStorageDevice'))
    row=registry.get(device)
    require(row is not None,'Selected EFI drive is no longer connected')
    node=row['node'];location=row['protocol'].get('Physical Interconnect Location')
    require(type(node.get('Whole')) is bool and location in ('Internal','External'),
            'Selected EFI drive identity is unavailable')
    require(media_size(value)==node.get('Size'),'Selected EFI media size disagrees')
    expected=dict(DeviceIdentifier=device,Whole=node['Whole'],Internal=location=='Internal')
    if not node['Whole']:
        expected.update(ParentWholeDisk=row['parent'],DiskUUID=str(uuid.UUID(node.get('UUID',''))))
        require(uuid.UUID(expected['DiskUUID']).int!=0,'Selected EFI partition identity is unavailable')
    for name,current in expected.items():
        previous=value.get(name)
        if name=='DiskUUID' and previous is not None:previous=str(previous).lower()
        require(name not in value or (type(previous) is type(current) and previous==current),
                'Selected EFI metadata disagrees: '+name)
        value[name]=current
    return value

def candidates():
    listing=plist_command('/usr/sbin/diskutil','list','-plist');rows=[]
    from partition_detection import registry_index
    registry=registry_index(plist_command('/usr/sbin/ioreg','-a','-l','-r','-c','IOBlockStorageDevice'))
    physical={name for name,row in registry.items() if row['node'].get('Whole') is True}
    for disk in listing.get('AllDisksAndPartitions',[]):
        if disk.get('Content')!='GUID_partition_scheme':continue
        if disk['DeviceIdentifier'] not in physical:continue
        parent=info(disk['DeviceIdentifier'])
        if parent.get('Whole') is not True or type(parent.get('Internal')) is not bool:continue
        for part in disk.get('Partitions',[]):
            if part.get('Content')!='EFI':continue
            value=info(part['DeviceIdentifier'])
            if (value.get('DiskUUID') and value.get('Content')=='EFI' and value.get('Whole') is False
                and value.get('ParentWholeDisk')==parent['DeviceIdentifier']
                and value.get('Internal') is parent['Internal']):
                location='Internal' if parent['Internal'] else 'External'
                bus=parent.get('BusProtocol') or registry[parent['DeviceIdentifier']]['protocol'].get('Physical Interconnect') or 'connection unknown'
                value['parent_description']=parent['DeviceIdentifier']+' / '+str(parent.get('MediaName') or parent.get('DeviceModel') or 'drive')+' / '+location+' / '+str(bus)
                rows.append(value)
    return rows

def same(before,after):
    require(before['DeviceIdentifier']==after['DeviceIdentifier'] and before['DiskUUID']==after.get('DiskUUID')
        and media_size(before)==media_size(after) and after.get('Content')=='EFI'
        and before.get('ParentWholeDisk')==after.get('ParentWholeDisk')
        and after.get('Whole') is False and type(before.get('Internal')) is bool
        and after.get('Internal') is before['Internal'],'Selected EFI partition changed')

class EFIAccess(InstallationManager):
    def execute(self):
        self.begin();rows=candidates();require(rows,'No EFI partitions found on connected physical GPT drives')
        selected=rows[self.choose('Select which EFI partition to mount or unmount. Internal and external drives, including USB, are listed. Opening it does not repair or change boot files.',
            [r['DeviceIdentifier']+' | '+r['DiskUUID']+' | '+str(media_size(r)//1024**2)+' MiB'+
                ' | parent '+r.get('parent_description',str(r.get('ParentWholeDisk','unavailable')))+
                (' | mounted at '+r['MountPoint'] if r.get('MountPoint') else ' | not mounted') for r in rows])]
        action=self.choose('EFI files can make your operating systems unbootable if altered incorrectly. '
            'Only edit files yourself if you know what you are doing. GoAwayBCA will not edit anything here.',
            ['Mount/open in Finder (read/write)','Mount/open in Finder (read-only)',
             'Unmount this EFI partition (no force)','Cancel'])
        if action==3:return
        device=selected['DeviceIdentifier'];current=info(device);same(selected,current)
        if action==2:
            if not current.get('MountPoint'):self.note('Selected EFI partition is already unmounted.');return
            require(self.choose('Unmount '+device+' at '+current['MountPoint']+'? Close files using it first. '
                'This will never force an unmount or stop another process.', ['Unmount','Cancel'])==0,'Cancelled by user')
            fresh=info(device);same(selected,fresh)
            require(fresh.get('MountPoint')==current['MountPoint'],'EFI mount changed')
            run(['/usr/sbin/diskutil','unmount',device])
            require(not info(device).get('MountPoint'),'EFI is still mounted')
            self.note('EFI unmounted. No files changed.');return
        readonly=action==1
        if current.get('MountPoint') and not readonly and current.get('ReadOnlyVolume') is True:
            from efi_mounts import remount_for_write
            current=remount_for_write(device,current,info,run,self.choose)
        existing=bool(current.get('MountPoint'))
        if existing:
            mount=Path(current['MountPoint'])
            if readonly and current.get('ReadOnlyVolume') is not True:
                require(self.choose('This EFI is already mounted READ/WRITE by another operation. '
                    'GoAwayBCA will not change or unmount that existing mount. Open it with its current access mode?',
                    ['Open existing read/write mount','Cancel'])==0,'Cancelled by user')
            self.note('Using the existing mount without changing its access mode or taking ownership.')
        else:
            user=pwd.getpwuid(self.interaction.user_uid)
            mount=Path('/Volumes')/('WinDeploy-EFI-'+device+'-'+uuid.uuid4().hex[:12])
            require(not mount.exists() and not mount.is_symlink(),'EFI mountpoint already exists; inspect it first')
            mount.mkdir(mode=0o755)
            try:
                run(['/sbin/fsck_msdos','-n','/dev/r'+device])
                same(selected,info(device));require(not info(device).get('MountPoint'),'EFI was mounted elsewhere')
                run(['/sbin/mount_msdos','-u',str(user.pw_uid),'-g',str(user.pw_gid),'-m','700',
                    '-o','rdonly,nobrowse' if readonly else 'nobrowse','/dev/'+device,str(mount)])
                fresh=info(device);same(selected,fresh)
                require(fresh.get('MountPoint')==str(mount) and fresh.get('ReadOnlyVolume') is readonly,
                    'EFI mount/access verification failed')
            except BaseException:
                # Only our new, exactly identified mount can be rolled back.
                # Never force-unmount or mask the original failure.
                try:
                    fresh=info(device);same(selected,fresh)
                    if fresh.get('MountPoint')==str(mount):
                        run(['/usr/sbin/diskutil','unmount',device])
                    if not info(device).get('MountPoint') and not mount.is_symlink() and mount.is_dir() and not any(mount.iterdir()):mount.rmdir()
                except Exception as cleanup:
                    self.note('Mount cleanup needs inspection: '+str(mount)+' — '+str(cleanup))
                raise
        write_receipt(self.session/'efi-access.json',dict(device=device,guid=selected['DiskUUID'],path=str(mount),
            preexisting_mount=existing,read_only=info(device).get('ReadOnlyVolume'),left_mounted_by_request=True,boot_files_changed=False))
        self.interaction.request('open_folder','EFI is mounted. Leave it mounted for inspection, then use this button to unmount it.',path=str(mount))
        self.note('EFI left mounted as requested: '+str(mount)+'. No boot files changed.')
