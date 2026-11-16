"""Refuse installing over any root Windows entry, including a partial install."""
import os
from pathlib import Path
import stat


def check(target, ntfsls):
    import install_windows7 as app
    from ntfs_read import root_names
    device = target['partition']['DeviceIdentifier']
    before = app.info(device)
    app.assert_same_target(target, before, app.info(target['disk']['DeviceIdentifier']))
    owned = False
    mounted = None
    try:
        if app.filesystem(before) == 'ntfs':
            names = root_names(before, ntfsls)
        else:
            app.require(app.filesystem(before) == 'exfat', 'Unexpected destination filesystem')
            if before.get('MountPoint') == '':
                app.run(['/usr/sbin/diskutil', 'mount', 'readOnly', device])
                mounted = app.info(device)
                owned = True
                app.require(app.partition_identity(mounted) == app.partition_identity(before),
                            'Destination identity changed during temporary read-only mount')
                app.require(mounted.get('ReadOnlyVolume') is True, 'Destination was not mounted read-only')
            else:
                mounted = before
            mount = mounted.get('MountPoint')
            app.require(type(mount) is str and mount and mount != '/', 'Unknown destination mount state')
            node = os.stat('/dev/'+device)
            app.require(stat.S_ISBLK(node.st_mode), 'Destination is not a partition block device')
            app.require(os.stat(mount).st_dev == node.st_rdev, 'Destination mount identity changed')
            names = os.listdir(mount)
            app.require(os.stat(mount).st_dev == node.st_rdev, 'Destination mount changed during inspection')
        app.require(not any(name.casefold() == 'windows' for name in names),
                    'This partition contains a Windows folder. Use Manage Installations to remove '
                    'the existing installation before installing here. Nothing was formatted.')
        after = app.info(device)
        if owned:
            app.require(app.partition_identity(before) == app.partition_identity(after) and
                        after.get('ReadOnlyVolume') is True, 'Destination identity changed during inspection')
        else:
            app.assert_same_target(target, after, app.info(target['disk']['DeviceIdentifier']))
    finally:
        if owned:
            current = app.info(device)
            app.require(mounted and current.get('MountPoint') == mounted.get('MountPoint') and
                        app.partition_identity(current) == app.partition_identity(mounted),
                        'Destination changed; its temporary mount was left untouched')
            app.run(['/usr/sbin/diskutil', 'unmount', device])
