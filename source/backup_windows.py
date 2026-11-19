"""Explicit user-started backup of an existing Windows NTFS partition.

Includes foreign installations without granting ownership or uninstall rights.
No installed Windows/EFI file is changed by this operation.
"""
import json
import hashlib
import os
from pathlib import Path
import re
import stat
import struct

import backup_capture
import backup_drive
import backup_metadata
from install_windows7 import (ROOT, require, info, assert_same_target,
    ensure_target_unmounted, require_sources_off_target, write_receipt)
from installation_records import can_uninstall
from manage_installations import InstallationManager
from ntfs_read import Probe
from product_scope import INSTALL_FAMILIES
from windows_discovery import loader_version, display_name

HISTORY_PATH = '/GoAwayBCA-Backup-History.json'


def open_backup(uid, path, create=False):
    # POSIX descriptor passing is only needed in the privileged Mac operation.
    from backup_file_access import open_backup as user_file
    return user_file(uid, path, create=create)


def windows_identity(loader):
    """Identify the installed OS, never the version of a shared boot manager."""
    require(len(loader) >= 64 and loader[:2] == b'MZ', 'The installed Windows loader is unreadable.')
    offset = struct.unpack_from('<I', loader, 60)[0]
    require(64 <= offset <= len(loader)-24 and loader[offset:offset+4] == b'PE\0\0',
            'The installed Windows loader header is invalid.')
    machine = struct.unpack_from('<H', loader, offset+4)[0]
    require(machine in (0x14c, 0x8664), 'The Windows architecture could not be identified.')
    version = loader_version(loader)
    require(version is not None, 'The installed Windows version could not be identified for backup.')
    major, minor, build, revision = version
    family = {(6, 1): 'Windows 7', (6, 2): 'Windows 8', (6, 3): 'Windows 8.1'}.get((major, minor))
    if (major, minor) == (10, 0):
        family = 'Windows 11' if build >= 22000 else 'Windows 10'
    require(family is not None, 'This Windows version is not supported by the backup workflow.')
    require(family in INSTALL_FAMILIES, 'GoAwayBCA backs up Windows 8.1, 10 and 11 only. '
            'This partition contains '+family+'. No backup file was created and no source files were changed.')
    require(family != 'Windows 11' or machine == 0x8664, 'This Windows 11 architecture is not supported.')
    return dict(family=family, architecture=0 if machine == 0x14c else 9, version=list(version))


def previous_history(probe):
    """Missing history is normal; corrupt existing history must not reset origin."""
    roots = probe.names()
    matches = [name for name in roots if name.casefold() == HISTORY_PATH[1:].casefold()]
    if not matches:
        return None
    require(len(matches) == 1, 'Ambiguous backup history file.')
    raw = probe.read('/'+matches[0], 16*1024**2)
    import backup_archive
    value = json.loads(raw.decode('utf8'), object_pairs_hook=backup_archive.unique_object)
    backup_metadata.validate(value)
    return value


def review_text(selected, location, owned):
    text = ('WINDOWS PARTITION BACKUP IS EXPERIMENTAL.\n\n' + selected['title'] + '\n' +
            selected['summary'] + '\n\nThe backup contains this Windows partition, including its files, '
            'applications, settings and filesystem metadata. It is not a whole-disk or EFI-partition backup. '
            'The Windows partition will be temporarily unmounted for a consistent read. '
            'No Windows or boot files will be edited.\n\n'
            'We recommend restoring to the exact same physical drive. Even then, restoration and booting '
            'are experimental. Keep another backup and the original installation until you have tested the restore.')
    if not owned:
        text += ('\n\nThis installation was not verified as created by GoAwayBCA. Backup and restore are '
                 'especially experimental for it. Restoring it will not enable uninstalling it through GoAwayBCA.')
    details = json.dumps(location, indent=2, sort_keys=True)
    return text, 'Detected source drive and partition:\n'+details


class WindowsBackup(InstallationManager):
    def execute(self):
        self.begin()
        tool = Path(self.args.tools_dir)/'ntfsclone'
        require(tool.is_file() and os.access(str(tool), os.X_OK), 'The bundled Windows backup helper is missing.')
        scan = self.discover()
        require(scan['scan_complete'], 'Some Windows partitions could not be read. See the saved log before backing up.')
        write_receipt(self.session/'backup-discovery.json', scan)
        if not scan['installations']:
            self.interaction.request('notice', 'No Windows installations found. No backup file was created.')
            return
        rows = scan['installations']
        selected = rows[self.choose('Choose the Windows partition to back up. Backups can include installations '
            'made outside this app. Only Windows 8.1, 10 and 11 backups are supported in GoAwayBCA.',
            [row['title']+'\n'+row['summary'] for row in rows])]
        target = selected['target']; device = target['partition']['DeviceIdentifier']
        assert_same_target(target, info(device), info(target['disk']['DeviceIdentifier']))
        location = backup_drive.describe(target)
        with Probe(target['partition'], self.ntfscat) as probe:
            loader = probe.read('/Windows/System32/winload.efi', 8*1024**2)
            windows = windows_identity(loader)
            previous = previous_history(probe)
        owned = can_uninstall(selected['observed'], selected['marker'], selected['record'])
        metadata = backup_metadata.create(location, windows, owned, previous)
        # Store the actual menu title separately from the partition label.
        mount = self.mount_esp(target['esp'], readonly=True)
        catalog = self.discovery_json(mount/'EFI/WinDeploy/catalog.json')
        metadata['menu_title'] = display_name(catalog, location['disk_guid'], location['partition_guid'], windows['family'])
        metadata['creation_evidence'] = dict(marker=selected['marker'], record=selected['record']) if owned else None
        text, details = review_text(selected, location, metadata['uninstall_eligible'])
        require(self.approve('backup_review', text, details=details), 'Backup cancelled. No source files changed.')
        filename = re.sub(r'[^A-Za-z0-9._-]', '-', metadata['menu_title']).strip('-')[:80] or 'Windows'
        path = self.interaction.request('backup_save_file', 'Choose where to save the Windows partition backup.',
                                        suggested_name=filename+'-'+metadata['backup_id'][:8]+'.gabackup')
        require(type(path) is str and path.startswith('/'), 'Choose an absolute backup filename.')
        require_sources_off_target([ROOT, self.session, Path(path).parent], device)
        require(not self.close_esp(), 'Could not close the temporary EFI mount before backup.')
        with open_backup(self.interaction.user_uid, path, create=True) as output:
            self.note('Created backup file: '+path+'. It is incomplete until capture and verification finish.')
            node = os.stat('/dev/'+device)
            require(stat.S_ISBLK(node.st_mode) and os.fstat(output.fileno()).st_dev != node.st_rdev,
                    'Save the backup on a different partition from the Windows installation.')
            self.remount_backup_source = None
            if info(device).get('MountPoint'):
                self.remount_backup_source = target
            ensure_target_unmounted(target, self.note)
            # Do not carry stale provenance through a dialog while the volume
            # could still be mounted. Recheck it once the source is unmounted.
            with Probe(target['partition'], self.ntfscat) as probe:
                require(hashlib.sha256(probe.read('/Windows/System32/winload.efi', 8*1024**2)).digest() ==
                        hashlib.sha256(loader).digest() and previous_history(probe) == previous,
                        'The Windows installation or its backup history changed during review. Start again.')
                if owned:
                    from manage_installations import bounded_json, MARKER
                    require(bounded_json(probe.read(MARKER, 1024**2)) == selected['marker'],
                            'The installation creation record changed during review. Start again.')
            def guard():
                current = info(device)
                assert_same_target(target, current, info(target['disk']['DeviceIdentifier']))
                require(current.get('MountPoint') == '', 'The Windows partition was mounted during backup; capture stopped.')
                require(backup_drive.describe(target) == location, 'The backup source drive or its geometry changed.')
            self.note('Saving Windows backup to '+path)
            try:
                result = backup_capture.capture(tool, '/dev/'+device, output, metadata,
                    self.session/'ntfsclone-backup.log', guard, self.note)
            except Exception:
                self.note('The new backup could not be completed and verified. Its file was retained for review; '
                          'do not rely on it. The original Windows installation was not edited.')
                raise
        write_receipt(self.session/'backup-complete.json', dict(path=path, metadata=metadata, result=result))
        self.interaction.request('notice', 'Windows partition backup completed and its saved data was verified.\n\n'+path+
            '\n\nKeep the original Windows installation and another backup until you have tested restoring it. '
            'Verification does not prove that a restored installation will boot. No Windows or EFI files were changed.',
            button_title='Done')

    def close(self):
        target = getattr(self, 'remount_backup_source', None)
        extra = []
        if target is not None:
            try:
                from install_windows7 import run
                device = target['partition']['DeviceIdentifier']
                current = info(device)
                assert_same_target(target, current, info(target['disk']['DeviceIdentifier']))
                if not current.get('MountPoint'):
                    # Read-only remount, never force or grant write access.
                    run(['/usr/sbin/diskutil', 'mount', 'readOnly', device])
                    require(bool(info(device).get('MountPoint')), 'The Windows partition did not remount.')
            except Exception as error:
                extra.append('Windows backup finished or stopped, but its partition could not be remounted: '+str(error))
        return super().close()+extra
