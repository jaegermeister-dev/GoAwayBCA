"""Explicitly consented access-mode changes; never force another mount closed."""

def remount_for_write(device, before, read_info, command, choose):
    from install_windows7 import require
    def same(row):
        require(all(row.get(k)==before.get(k) for k in
                    ('DeviceIdentifier','DiskUUID','ParentWholeDisk','Internal','Content'))
                and row.get('Content')=='EFI' and row.get('Whole') is False,
                'The selected EFI partition changed. Start again.')
    require(before.get('MountPoint') and before.get('ReadOnlyVolume') is True,
            'Expected an existing read-only EFI mount.')
    require(choose is not None,'EFI is mounted read-only. Unmount it and retry.')
    require(choose('The selected EFI partition is already mounted read-only at '+before['MountPoint']+
        '. This operation needs write access. Close any files using it. GoAwayBCA can unmount it normally '
        'and mount it read/write; it will not force an unmount or stop another application.',
        ['Unmount and continue with read/write access','Cancel'])==0,'EFI access change cancelled.')
    fresh=read_info(device);same(fresh)
    require(fresh.get('MountPoint')==before['MountPoint'] and fresh.get('ReadOnlyVolume') is True,
            'The EFI mount changed during confirmation. Start again.')
    command(['/usr/sbin/diskutil','unmount',device])
    after=read_info(device);same(after)
    require(not after.get('MountPoint'),'EFI remains mounted. Close files using it, then retry; no force-unmount was used.')
    return after
