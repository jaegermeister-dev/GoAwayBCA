"""Installation discovery and management review for the privileged GUI worker.

Discovery uses read-only NTFS reads and a read-only ESP mount. Unrecognised
origins may be imported into the menu, but cannot be uninstalled. Never infer absence from an unreadable
partition or remove another installation's EFI files because its disk is away.
"""
import json
import os
from pathlib import Path
import tempfile

from install_windows7 import (Installer, Refusal, require, run, inventory, gpt_for,
    match_gpt, partition_summary, filesystem, media_size, tree_manifest, write_receipt,
    find_targets,plist_command,info)
from installation_records import ownership
from deployment_lock import DeploymentLock
from ntfs_read import read_file,root_names
from ntfs_read import Probe
from windows_discovery import loader_version,version_label,recorded_origin,display_name

MARKER = '/WinDeploy/installation.json'


def discovery_inventory():
    # No per-volume queries for APFS, HFS, recovery or unrelated disk images.
    # Read enough disk metadata to find NTFS, then inspect NTFS contents only.
    listing=plist_command('/usr/sbin/diskutil','list','-plist')
    candidates=[d for d in listing.get('AllDisksAndPartitions',[]) if
        any(p.get('Content')=='Microsoft Basic Data' for p in d.get('Partitions',[]))]
    return inventory(dict(AllDisksAndPartitions=candidates))


def bounded_json(data):
    require(len(data) <= 1024*1024, 'Installation record is too large')
    result = json.loads(data.decode('utf-8'))
    require(type(result) is dict, 'Installation record is not an object')
    return result


def preserve_report(manifest):
    # All actual files under public Windows/unknown paths, not invented names.
    # Known protected OC/Apple paths remain protected even if absent here.
    return sorted(path for path in manifest if path.casefold().startswith('efi/')
                  and not path.casefold().startswith(('efi/oc/', 'efi/apple/')))


class InstallationManager(Installer):
    def begin(self):
        self.tools()
        require(os.geteuid() == 0 and self.interaction is not None,
                'Use the authorised WinDeploy GUI')
        self.deployment_lock = DeploymentLock().acquire()
        base = Path(self.args.work_dir)
        base.mkdir(parents=True, exist_ok=True)
        self.session = Path(tempfile.mkdtemp(prefix='manage-', dir=str(base)))
        os.chmod(self.session, 0o700)
        self.note('Manage Installations: scanning Windows partitions read-only.')
        self.note('Scan/report location: ' + str(self.session))

    def scan(self):
        rows, failures, non_windows = [], [], []
        for target in inventory():
            p = target['partition']
            if filesystem(p) != 'ntfs':
                continue
            raw = '/dev/' + p['DeviceIdentifier']
            try:
                names=root_names(p,Path(self.ntfscat).with_name('ntfsls'))
                if not any(n.casefold()=='windows' for n in names):
                    non_windows.append(dict(partition=p['DeviceIdentifier'],reason='No Windows directory in verified root listing'))
                    continue
                # A Windows folder/name is not enough. Both loader and system
                # hive must be readable, but this is not a health/boot test.
                loader = read_file(p, '/Windows/System32/winload.efi', self.ntfscat, 8*1024*1024)
                hive = read_file(p, '/Windows/System32/config/SYSTEM', self.ntfscat, 256*1024*1024)
                require(len(loader) > 1024 and loader[:2] == b'MZ' and
                        len(hive) >= 4096 and hive[:4] == b'regf', 'Windows files not verified')
                gpt = gpt_for(target)
                win, esp = match_gpt(target, gpt)
                observed = dict(disk_guid=gpt['guid'], partition_guid=win['guid'],
                    partition_bytes=media_size(p), partition_offset=win['first']*gpt.get('sector_size',512),
                    filesystem='ntfs', windows_verified=True)
                marker, record = None, None
                try:
                    marker = bounded_json(read_file(p, MARKER, self.ntfscat, 1024*1024))
                except (OSError, Refusal, ValueError, UnicodeError):
                    pass
                # One owned ESP mount at a time, retained only for this review.
                if self.esp_owned and self.esp_device != target['esp']:
                    require(not self.close_esp(), 'Could not detach prior scan ESP')
                mount = self.mount_esp(target['esp'], readonly=True)
                manifest = tree_manifest(mount)
                record_path = mount / 'EFI/WinDeploy/Records' / (win['guid'] + '.json')
                if record_path.is_file() and not record_path.is_symlink():
                    try:
                        require(record_path.stat().st_size <= 1024*1024, 'Oversized EFI record')
                        record = bounded_json(record_path.read_bytes())
                    except (Refusal, ValueError, UnicodeError):
                        pass
                assets = {(esp['guid'], path): digest for path,digest in manifest.items()}
                if record and type(record.get('efi_assets')) is list:
                    # Ownership must not cause arbitrary raw-file reads. Only
                    # canonical EFI paths on this exact Windows partition.
                    from boot_catalog import efi_path
                    import hashlib
                    # Same complete per-file hashes, but only one fresh
                    # identity/mount check around this uninterrupted batch.
                    # Hundreds of diskutil calls are not needed per partition.
                    with Probe(p,self.ntfscat) as probe:
                        for asset in record['efi_assets']:
                            try:
                                if asset['partition_guid'] != win['guid']:
                                    continue
                                path = efi_path(asset['path'])
                                data = probe.read('/' + path,16*1024*1024)
                                assets[(win['guid'], path)] = hashlib.sha256(data).hexdigest()
                            except (OSError, Refusal, ValueError, KeyError, TypeError):
                                continue
                observed['unattributed_efi_paths'] = preserve_report(manifest)
                from menu_lifecycle import snapshot
                state = ownership(observed, marker, record, assets, snapshot(mount, manifest))
                title=(record or {}).get('edition') or (record or {}).get('family')
                ledger=mount/'EFI/WinDeploy/catalog.json'
                try:
                    require(ledger.stat().st_size<=1024*1024,'Oversized catalog')
                    catalog=bounded_json(ledger.read_bytes())
                    title=next((e['title'] for e in catalog.get('entries',[]) if
                        e.get('partition_guid')==win['guid'] and e.get('disk_guid')==gpt['guid']),title)
                except (OSError,Refusal,ValueError,KeyError,TypeError):pass
                rows.append(dict(target=target, observed=observed, marker=marker,esp_guid=esp['guid'],
                    record=record, ownership=state, efi_manifest=manifest,
                    # JSON receipts cannot have tuple dictionary keys. Keep the
                    # exact readback list; callers reconstruct only for planning.
                    asset_readbacks=[dict(partition_guid=k[0],path=k[1],sha256=v) for k,v in assets.items()],
                    title=(title or 'Windows installation')+' — '+(p.get('VolumeName') or 'Unlabelled partition'),
                    summary=partition_summary(p, target['disk'], multiline=True)))
            except (OSError, Refusal, ValueError) as error:
                failures.append(dict(partition=p['DeviceIdentifier'], error=str(error)))
        return dict(schema=1, installations=rows, scan_failures=failures,
                    non_windows=non_windows,
                    scan_complete=not failures, scope='internal-physical-GPT-NTFS',
                    physical_writes=False)

    def discover(self,verify_filesystem=False):
        """Quick list only. Full scan() remains mandatory for action planning."""
        rows,failures,non_windows=[],[],[]
        gpts={};metadata={}
        for target in discovery_inventory():
            p=target['partition']
            if filesystem(p) not in (('ntfs','exfat') if verify_filesystem else ('ntfs',)):continue
            device=p['DeviceIdentifier']
            try:
                candidate=(Probe(p,self.ntfscat,verify_filesystem=True) if verify_filesystem else Probe(p,self.ntfscat))
                with candidate as probe:
                    if verify_filesystem and not probe.is_ntfs:
                        non_windows.append(dict(partition=device,reason='Not identified as NTFS; no Windows file inspection attempted'))
                        continue
                    stale_filesystem=verify_filesystem and probe.stale_filesystem
                    if stale_filesystem:
                        self.note(device+': macOS still reports exFAT; the partition header identifies NTFS. Checking Windows files read-only.')
                    if not any(n.casefold()=='windows' for n in probe.names()):
                        non_windows.append(dict(partition=device,reason='No Windows directory in root listing'))
                        continue
                    if verify_filesystem:self.note(device+': Reading Windows EFI loader...')
                    loader=probe.read('/Windows/System32/winload.efi',8*1024**2)
                    if verify_filesystem:self.note(device+': Reading Windows registry header...')
                    header=probe.read('/Windows/System32/config/SYSTEM',4096,prefix=True)
                    require(len(loader)>1024 and loader[:2]==b'MZ' and
                        len(header)==4096 and header[:4]==b'regf','Windows files not identified')
                    marker=None
                    try:marker=bounded_json(probe.read(MARKER,1024**2))
                    except (OSError,Refusal,ValueError,UnicodeError):pass
                if verify_filesystem:self.note(device+': Windows files found. Checking partition identity and EFI records...')
                disk=target['disk']['DeviceIdentifier']
                if disk not in gpts:gpts[disk]=gpt_for(target)
                gpt=gpts[disk];win,esp=match_gpt(target,gpt)
                observed=dict(disk_guid=gpt['guid'],partition_guid=win['guid'],
                    partition_bytes=media_size(p),partition_offset=win['first']*gpt.get('sector_size',512),
                    filesystem='ntfs',windows_verified=True)
                if self.esp_owned and self.esp_device!=target['esp']:
                    require(not self.close_esp(),'Could not detach prior scan ESP')
                mount=self.mount_esp(target['esp'],readonly=True)
                if target['esp'] not in metadata:
                    metadata[target['esp']]=self.discovery_json(mount/'EFI/WinDeploy/catalog.json')
                catalog=metadata[target['esp']]
                record=self.discovery_json(mount/'EFI/WinDeploy/Records'/(win['guid']+'.json'))
                version=loader_version(loader);label=version_label(version)
                name=display_name(catalog,gpt['guid'],win['guid'],label)
                rows.append(dict(target=target,observed=observed,marker=marker,record=record,
                    esp_guid=esp['guid'],ownership=recorded_origin(observed,marker,record),
                    title=name+' — '+(p.get('VolumeName') or 'Unlabelled partition'),
                    windows_version=version,version_label=label,display_only=True,
                    reported_filesystem=filesystem(p),detected_filesystem='ntfs',
                    summary=partition_summary(p,target['disk'],multiline=True)+'\n'+label+
                        ('\nNTFS verified on disk; macOS still reports the previous exFAT format.' if stale_filesystem else '')))
            except (OSError,Refusal,ValueError) as error:
                failures.append(dict(partition=device,error=str(error)))
        return dict(schema=1,installations=rows,scan_failures=failures,non_windows=non_windows,
            scan_complete=not failures,scope='internal-physical-GPT-NTFS',physical_writes=False,display_only=True)

    @staticmethod
    def discovery_json(path):
        try:
            require(not any(p.is_symlink() for p in (path,)+tuple(path.parents)) and
                path.stat().st_size<=1024**2,'Unsafe discovery metadata')
            return bounded_json(path.read_bytes())
        except (OSError,Refusal,ValueError,UnicodeError):return None

    def close_esp(self):
        # Release ONLY this scan's ESP. Installer.close() also detaches its ISO
        # and releases the global deployment lock, neither belongs here.
        from install_windows7 import info
        try:
            if self.esp_owned and self.esp_device:
                current = info(self.esp_device)
                require(current.get('DiskUUID') == self.esp_identity and
                        current.get('MountPoint') == str(self.esp_mount_path),
                        'Scan ESP mount ownership changed')
                run(['/sbin/umount', str(self.esp_mount_path)])
                self.esp_owned = False
            return []
        except Exception as error:
            return [str(error)]

    def execute(self):
        self.begin()
        report = self.scan()
        write_receipt(self.session / 'installation-scan.json', report)
        for failure in report['scan_failures']:
            self.note('Could not inspect %s: %s' % (failure['partition'], failure['error']))
        rows = report['installations']
        if not rows:
            self.note('No verified Windows installations found in this scan scope.')
            return
        options = [row['title'] + ' | ' + row['ownership']['status'] + '\n' + row['summary']
                   for row in rows]
        selected = rows[self.choose('Manage Installations — select an installation to review:', options)]
        self.note(selected['summary'])
        self.note(selected['ownership']['message'])
        self.note('Preserved EFI files will be listed individually in the operation report.')
        # Review only until the format + menu transaction passes its separate
        # fixture and recovery tests. Do not expose a misleading active button.
        self.note('Development scan complete. Format integration is still being tested; no files changed.')
