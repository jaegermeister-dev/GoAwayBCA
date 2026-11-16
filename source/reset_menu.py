"""Explicit debug reset of our picker, without touching any OS partition.

Discovery needs only the ESP, so erased Windows installations do not prevent a
reset. Public loader paths are removable ONLY when their hashes identify our
selector. Shared BCD data needs independent, ESP-bound ownership evidence.
Unknown files, per-installation loaders/receipts and diagnostic logs stay put.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile

from deployment_lock import DeploymentLock
from efi_transaction import EFITransaction, node
from install_windows7 import (Installer, Refusal, require, info, gpt_for, PART_RE, ESP_TYPE,
    media_size, tree_manifest, require_efi_unchanged, write_receipt, run)
import menu_lifecycle as lifecycle
from operation_progress import phase, Message
from store_configuration import decode as decode_config
from manage_installations import InstallationManager


def candidates():
    from efi_access import candidates as discover
    return discover()


def same(selected, current):
    from efi_access import same as check
    return check(selected, current)


def reset_path(name):
    """Exact reserved picker files, never recursive directory deletion."""
    name = name.casefold()
    if name in {p.casefold() for p in (lifecycle.CATALOG, lifecycle.MENU,
                                      lifecycle.CONFIG, lifecycle.STATE)}:
        return True
    return bool(re.fullmatch(re.escape(lifecycle.PREFIX.casefold()) +
                            r'private-([0-9]|1[0-5])\.bcd', name))


def snapshot(root, manifest):
    files = {}
    for name, expected in manifest.items():
        if reset_path(name) or name.casefold() == lifecycle.CANON.casefold():
            path = node(root, name)
            require(path.stat().st_size <= 16*1024**2, 'Unexpectedly large picker file: '+name)
            data = path.read_bytes()
            require(lifecycle.sha(data) == expected, 'Picker changed during review: '+name)
            files[name] = data
    return files


def canonical_owned(files, disk_guid, esp_guid):
    """Do not infer ownership from a Microsoft filename or Windows absence."""
    values = {k.casefold(): v for k, v in files.items()}
    try:
        configuration = values[lifecycle.CONFIG.casefold()]
        menu = values.get(lifecycle.MENU.casefold())
        # The catalog can recover the redundant menu, not a missing identity.
        if lifecycle.CATALOG.casefold() in values:
            menu = lifecycle.menu_encode(json.loads(values[lifecycle.CATALOG.casefold()])['entries'])
        config = decode_config(configuration, menu)
        require(config['esp_disk_guid'] == disk_guid.lower() and
                config['esp_partition_guid'] == esp_guid.lower(), 'Different ESP identity')
        state = values[lifecycle.STATE.casefold()]
        require(len(state) == 88 and state[:8] == b'W7DEPS01' and state[20:24] == bytes(4)
                and hashlib.sha256(state[:-32]).digest() == state[-32:]
                and state[24:56] == hashlib.sha256(configuration).digest(), 'Invalid active state')
        _, active = struct.unpack_from('<QI', state, 8)
        expected = config['identities'][active]
        canonical = values[lifecycle.CANON.casefold()]
        private = values[(lifecycle.PREFIX+'Private-%d.BCD' % active).casefold()]
        if canonical == private:
            # Exact snapshot equality also works with an unfamiliar recovery
            # path. No Windows partition or recovery image is modified here.
            return True
        # Windows may have serviced its clean BCD after taking the snapshot.
        lifecycle.verify_bcd(canonical, disk_guid, esp_guid,
                             expected['disk_guid'], expected['partition_guid'], for_removal=True)
        return True
    except (Refusal, ValueError, KeyError, TypeError, IndexError, struct.error):
        return False


def plan(files, manifest, disk_guid, esp_guid):
    original = lifecycle.indexed(manifest)
    lifecycle.indexed(files)
    require(all(original.get(k.casefold(), (None, None))[1] == lifecycle.sha(v)
                for k, v in files.items()), 'Reset snapshot/manifest mismatch')
    selected = {name for name in manifest if reset_path(name)}
    preserved = []
    for path in lifecycle.ALIASES:
        actual, digest = original.get(path.casefold(), (path, None))
        if digest in lifecycle.PRIOR_CHOOSER_SHAS | {lifecycle.CHOOSER_SHA}:
            selected.add(actual)
        elif digest is not None:
            preserved.append(actual+' (not a recognised WinDeploy selector)')
    for name,expected in (('OpenNtfsDxe.efi',lifecycle.NTFS_SHA),('Ntfs4Kn.efi',lifecycle.NTFS_4KN_SHA)):
        driver = lifecycle.BASE+name
        actual, digest = original.get(driver.casefold(), (driver, None))
        if digest == expected or (name=='Ntfs4Kn.efi' and digest in lifecycle.PRIOR_NTFS_4KN_SHAS):
            selected.add(actual)
        elif digest is not None:
            preserved.append(actual+' (unrecognised contents)')
    canonical = original.get(lifecycle.CANON.casefold())
    canonical_removed = bool(canonical and canonical_owned(files, disk_guid, esp_guid))
    if canonical_removed:
        selected.add(canonical[0])
        for suffix in ('.LOG', '.LOG1', '.LOG2'):
            existing = original.get((lifecycle.CANON+suffix).casefold())
            if existing:
                selected.add(existing[0])
    elif canonical:
        preserved.append(canonical[0]+' (ownership not proven; a fresh installation may require separate cleanup)')
    known = {name.casefold() for name in selected}
    # Keep recovery journals and unfamiliar files. Explain them instead of
    # reporting a complete clean slate when they may prevent republishing.
    leftovers = [name for name in manifest if name.casefold().startswith(lifecycle.PREFIX.casefold())
                 and name.casefold() not in known]
    preserved += [name+' (recovery/diagnostic evidence retained)' for name in leftovers]
    blocking_leftovers = [name for name in leftovers if not re.fullmatch(
        re.escape(lifecycle.PREFIX)+r'Log-[0-9a-f]{16}-[0-9]+-[012]\.bin', name, re.I)]
    changes = [dict(path=name, expected=manifest[name], data=None) for name in selected]
    aliases = {p.casefold() for p in lifecycle.ALIASES}
    changes.sort(key=lambda c: (c['path'].casefold() not in aliases, c['path'].casefold()))
    return dict(changes=changes, preserved=preserved, canonical_removed=canonical_removed,
                needs_review=bool(canonical and not canonical_removed) or bool(blocking_leftovers))


class ResetMenu(InstallationManager):
    def empty_scan(self):
        """No Windows, including unregistered installations, may lose its menu."""
        self.tools()
        scan=self.scan()
        require(scan.get('scan_complete') is True,
                'Windows discovery is incomplete. The boot menu cannot be deleted.')
        require(not scan['installations'],
                'Windows installations still exist. Remove them through Manage Installations before deleting the boot menu.')
        # Discovery must account for every internal physical NTFS partition.
        # Zero returned rows is not enough if a metadata adapter missed one.
        from partition_detection import registry_index
        from install_windows7 import plist_command,raw_info,filesystem
        index=registry_index(plist_command('/usr/sbin/ioreg','-a','-l','-r','-c','IOBlockStorageDevice'))
        expected={name for name,row in index.items() if row['node'].get('Whole') is False and
            row['protocol'].get('Physical Interconnect Location')=='Internal' and
            filesystem(raw_info(name))=='ntfs'}
        covered={row['partition'] for row in scan.get('non_windows',[])}
        require(expected<=covered,'Not every NTFS partition was inspected. The boot menu cannot be deleted.')
        return scan

    def begin(self):
        require(os.geteuid() == 0 and self.interaction is not None, 'Use the authorised GoAwayBCA app')
        self.deployment_lock = DeploymentLock().acquire()
        base = Path(self.args.work_dir)
        base.mkdir(parents=True, exist_ok=True)
        self.session = Path(tempfile.mkdtemp(prefix='reset-menu-', dir=str(base)))
        os.chmod(self.session, 0o700)
        self.note('Delete Windows Boot Menu: checking that no Windows installations remain. No partition will be formatted.')
        self.note('Reset backup and report: '+str(self.session))

    def execute(self):
        self.begin()
        self.empty_scan()
        phase(self.note, 'Inspecting EFI partitions for the debug reset')
        choices = candidates()
        require(choices, 'No internal physical GPT EFI partition found')
        selected = choices[self.choose('Select the disk whose Windows boot menu you want to reset. '
            'This does not format the disk or reinstall macOS.',
            [r['DeviceIdentifier']+' | '+r['DiskUUID']+' | '+r.get('parent_description', '') for r in choices])]
        device = selected['DeviceIdentifier']
        same(selected, info(device))
        match = PART_RE.fullmatch(device)
        require(match is not None, 'Not a physical EFI partition')
        parent = info(match[1])
        require(parent.get('Internal') is True and parent.get('Whole') is True, 'Not an internal physical disk')
        target = dict(disk=parent)
        gpt = gpt_for(target)
        esp = gpt['partitions'].get(int(match[2]))
        require(esp and esp['type'] == ESP_TYPE and esp['guid'].lower() == selected['DiskUUID'].lower()
                and (esp['last']-esp['first']+1)*gpt.get('sector_size',512) == media_size(selected)
                and esp['first']*gpt.get('sector_size',512) == selected.get('PartitionMapPartitionOffset'), 'EFI/GPT identity mismatch')
        mount = self.mount_esp(device, readonly=True)
        before = tree_manifest(mount)
        proposed = plan(snapshot(mount, before), before, gpt['guid'], esp['guid'])
        receipt = self.session/'reset-receipt.json'
        report = dict(status='planned', device=device, disk_guid=gpt['guid'], esp_guid=esp['guid'],
            changes=proposed['changes'], preserved=proposed['preserved'], needs_review=proposed['needs_review'],
            partition_formatted=False, windows_files_changed=False, macos_files_changed=False,
            opencore_changed=False, default_changed=False)
        write_receipt(receipt, report)
        for message in proposed['preserved']:
            self.note('Preserving: '+message)
        if not proposed['changes']:
            report['status'] = 'no-menu-files-removed'
            write_receipt(receipt, report)
            self.note('No identified Windows boot-menu files belonging to this app were found. No EFI files changed.')
            return
        for change in proposed['changes']:
            self.note('Reset will remove: '+change['path'])
        text = ('Reset ONLY our Windows boot menu on '+device+' ('+esp['guid']+').\n\n'
            'This does not erase the disk, format Windows partitions, reinstall macOS, remove OpenCore '
            'or change the default boot selection. Existing Windows data, per-installation loaders, '
            'installation receipts and diagnostics are preserved.\n\n'
            'Existing Windows installations will no longer be accessible through this picker until '
            'their menu is restored or recreated. Rebuild Boot Menu can recover supported installations '
            'when their matching loaders and boot-store copies remain. If those records are missing or '
            'inconsistent, recovery needs review; do not discard the reset backup. '
            'A verified backup and exact-file rollback journal will be saved before removal.\n\n'
            'Files to remove ('+str(len(proposed['changes']))+'); the complete list is in the log:\n'+
            '\n'.join(c['path'] for c in proposed['changes'][:6])+
            ('\n...and '+str(len(proposed['changes'])-6)+' further listed files.' if len(proposed['changes'])>6 else '')+
            '\n\nBackup/report: '+str(self.session))
        if proposed['needs_review']:
            text += '\n\nSome shared or recovery files cannot be safely removed. They will remain; '
            text += 'this is not a fully clean slate. See the preserved-file list in the log.'
        if self.args.dry_run:
            self.note(text)
            self.note('Reset preview complete; no EFI files changed.')
            return
        risk=('WARNING: DELETING EFI BOOT FILES CAN REMOVE BOOT OPTIONS OR DISRUPT AN EXISTING BOOT CHAIN. '
              'LINUX/GRUB INTERACTIONS HAVE NOT BEEN TESTED. BACK UP YOUR EFI CONFIGURATION AND DATA FIRST. '
              'ONLY PROCEED IF YOU UNDERSTAND THESE RISKS.\n\n')
        require(self.approve('reset_review','Confirmation 1 of 3.\n\n'+risk+text),'Boot menu deletion cancelled')
        require(self.approve('reset_review','Confirmation 2 of 3.\n\n'+risk+
            'Confirm you have reviewed the exact file list and backup location above. '
            'Unknown, shared, OpenCore, Apple and Linux files will be retained.'),'Boot menu deletion cancelled')
        require(self.confirm_erase('Confirmation 3 of 3.\n\n'+risk+
            'Remove only the listed Windows boot-menu files on '+device+
            '? No operating-system partition will be erased. Type confirm to continue.','confirm'),'Boot menu deletion cancelled')
        self.empty_scan()
        phase(self.note, 'Backing up EFI before the debug reset')
        backup = self.session/'efi-before'
        shutil.copytree(mount, backup)
        require_efi_unchanged(tree_manifest(backup, self.note, 'Verifying reset backup'), before,
                              'Reset backup verification failed; no EFI files removed')
        same(selected, info(device))
        require(gpt_for(target)['digest'] == gpt['digest'], 'Disk layout changed; start again')
        self.empty_scan()
        mount = self.mount_esp(device, readonly=False)
        def guard():
            current = info(device)
            same(selected, current)
            require(current.get('MountPoint') == str(mount) and current.get('ReadOnlyVolume') is False,
                    'EFI access changed before reset')
            require(gpt_for(target)['digest'] == gpt['digest'], 'Disk layout changed before reset')
        guard()
        require_efi_unchanged(tree_manifest(mount), before, 'EFI changed since reset review; start again')
        tx = EFITransaction(mount, self.session/'efi-transaction', guard)
        tx.prepare(proposed['changes'])
        tx.commit(after_each=lambda index: self.note(Message(
            'Resetting Windows boot menu: %d of %d files' % (index+1, len(proposed['changes'])),
            'Resetting Windows boot menu', 100*(index+1)/len(proposed['changes']))))
        run(['/bin/sync'])
        report.update(status='reset-complete-with-retained-files' if proposed['needs_review'] else 'reset-complete',
                      efi_after=tree_manifest(mount), backup=str(backup))
        write_receipt(receipt, report)
        self.note('Windows boot menu reset. No Windows/macOS partition, OpenCore or startup default changed.')
        self.note('Removed files are recoverable from the verified backup: '+str(backup))
        if proposed['needs_review']:
            self.note('Retained shared/recovery files need review before declaring a completely clean slate.')
