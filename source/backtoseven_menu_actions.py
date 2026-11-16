"""Privileged, user-requested experimental boot-menu operations; never formats."""
import json
from pathlib import Path
import shutil
import uuid
from install_windows7 import (ROOT, require, info, gpt_for, match_gpt, tree_manifest,
    require_efi_unchanged, write_receipt, run, Refusal)
from manage_installations import InstallationManager
from ntfs_read import Probe
from windows_discovery import loader_version, version_label, validate_efi_application
from installation_records import make_marker
from efi_transaction import EFITransaction
from operation_progress import phase
from boot_catalog import identity
import menu_lifecycle as m
import backtoseven_menu as menu
from backtoseven_experimental_policy import WARNING, NO_EXISTING, RESTORE
import bcd_isolation
import win7_accepted_efi as gpu

BACKUPS = Path('/Library/Application Support/BackToSeven/BootBackups')


def read_efi(root, manifest):
    result = {}
    for path, digest in manifest.items():
        if not path.casefold().startswith('efi/'):
            continue
        file = root/path
        require(not file.is_symlink() and file.stat().st_size <= 16*1024**2, 'Unsupported EFI file: '+path)
        data = file.read_bytes()
        require(m.sha(data) == digest, 'EFI changed during read: '+path)
        result[path] = data
    m.indexed(result)
    return result


def assemble(owner, root, files, disk, esp, rows, *, addition=None, preserve_addition_paths=False,confirmed_absent=()):
    """Read actual installed loaders; isolate copies of verified existing BCDs."""
    old = menu.previous(files, disk, esp)
    index = m.indexed(files)
    candidates = []
    if m.CANON.casefold() in index:
        candidates.append(index[m.CANON.casefold()][1])
    candidates.extend(old['stores'].values())
    # A standalone installation keeps its own clean initial BCD so that a
    # later Windows installer cannot destroy the only known boot-store copy.
    for path, data in files.items():
        if path.casefold().startswith(('efi/backtoseven/children/','efi/windeploy/installs/')) and path.casefold().endswith('/bcd'):
            candidates.append(data)
    children, stores = {}, {}
    sources=[]
    attempt_id=uuid.uuid4().hex
    for number, row in enumerate(rows):
        if row['observed']['disk_guid'] != disk or row['esp_guid'] != esp:
            continue
        key = identity(row['observed'])
        win = row['observed']['partition_guid']
        with Probe(row['target']['partition'], owner.ntfscat) as probe:
            windows_loader = probe.read('/Windows/Boot/EFI/bootmgfw.efi', 8*1024**2)
            validate_efi_application(windows_loader)
            # bootmgfw file versions are not OS versions (our accepted Win7
            # manager reports 11.1). They are diagnostic hints only. Identify
            # Windows separately using its already-discovered OS loader.
            os_loader = probe.read('/Windows/System32/winload.efi', 8*1024**2)
            version = loader_version(os_loader)
            local_candidates = list(candidates)
            local_bcd=None
            try:
                local_bcd=probe.read('/EFI/Microsoft/Boot/BCD', 2*1024**2)
                local_candidates.append(local_bcd)
            except (OSError, Refusal, ValueError):
                pass
        sources.append(dict(partition=row['target']['partition'],loader_sha256=m.sha(windows_loader),
                            os_loader_sha256=m.sha(os_loader),
                            boot_manager_version=loader_version(windows_loader),windows_version=version,
                            local_bcd_sha256=m.sha(local_bcd) if local_bcd is not None else None))
        failures = []
        for attempt, candidate in enumerate(local_candidates):
            try:
                store = bcd_isolation.isolate(candidate, disk, esp, disk, win,
                    owner.session/('isolated-%s-%d-%d'%(attempt_id,number,attempt)), ROOT/'lib/libhivex.0.dylib')
                break
            except (OSError, ValueError) as error:
                failures.append(str(error))
        else:
            raise ValueError('Cannot safely recover a separate boot store for '+row['title']+
                             '. Existing boot files are retained. '+ '; '.join(failures))
        previous = next((e for e in old['catalog']['entries'] if identity(e)==key), None)
        prefix = 'EFI/BackToSeven/Children/'+win+'/'
        path = prefix+'bootmgfw.efi'
        record = row.get('record') or {}
        paired = False
        try:
            paired = row.get('marker') == make_marker(record) and identity(record) == key
        except (ValueError, KeyError, TypeError):
            pass
        # Older owned installations may predate the confirmed_model field.
        # A paired record explicitly owning the pinned helper is equivalent
        # evidence of that already-selected route; do not silently lose it.
        recorded_helper=any(a.get('path')=='EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi'
            and a.get('partition_guid')==esp and a.get('sha256')==gpu.HELPER_SHA
            and a.get('exclusive') is True for a in record.get('efi_assets',[]))
        specific = paired and (recorded_helper or (
            record.get('confirmed_model') == 'MacBookPro11,5' and
            (record.get('family') == 'Windows 7' or (version and version[:2] == (6,1)))))
        child_files = {prefix+'BCD': store}
        public_hashes = [m.sha(windows_loader)]
        sector_route=paired and bool(record.get('sector_compatibility'))
        if sector_route:
            import win7_sector
            path,sector_files=win7_sector.recorded_route(record,files)
            digest=win7_sector.HELPER_SHA
            public_hashes.append(digest)
            if specific:
                require(record['sector_compatibility']['next_sha256']==gpu.HELPER_SHA and
                        m.sha(windows_loader)==gpu.ORIGINAL_SHA,
                        '11,5 sector/graphics boot route differs from its recorded helper')
                public_hashes.append(gpu.HELPER_SHA)
            # These exact paired-record assets already exist. Preserve them;
            # they are installation-owned, not newly imported menu assets.
        elif specific:
            helper = (ROOT/'efi/accepted-win7/WIN7AUTO.efi').read_bytes()
            marker = (ROOT/'efi/accepted-win7/ALLOW-AUTO.txt').read_bytes()
            require(m.sha(helper)==gpu.HELPER_SHA and m.sha(marker)==gpu.MARKER_SHA and
                    m.sha(windows_loader)==gpu.ORIGINAL_SHA, '11,5 graphics helper/original loader mismatch')
            path = 'EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi'
            child_files.update({path:helper,'EFI/W7DEP-WIN7-AUTO/ALLOW-AUTO.txt':marker,
                'EFI/Microsoft/Boot/bootmgfw-w7dep-original.efi':windows_loader})
            public_hashes.append(gpu.HELPER_SHA)
            digest = gpu.HELPER_SHA
        elif previous:
            # Preserve previously chosen special loaders, rather than guessing
            # that the installed Windows copy is equivalent to that route.
            path = previous['loader_path']
            if previous['boot_partition_guid'] == esp:
                require(path.casefold() in index, 'Existing child EFI loader is missing')
                loader = index[path.casefold()][1]
                if previous['hash_locked']:
                    require(m.sha(loader)==previous['loader_sha256'], 'Existing child loader changed')
                digest = m.sha(loader)
            else:
                with Probe(row['target']['partition'], owner.ntfscat) as probe:
                    loader = probe.read('/'+path,8*1024**2)
                require(not previous['hash_locked'] or m.sha(loader)==previous['loader_sha256'],
                        'Existing child loader changed')
                digest = m.sha(loader)
                sources[-1].update(route_loader_path='/'+path,route_loader_sha256=digest)
        else:
            child_files[path] = windows_loader
            digest = m.sha(windows_loader)
        title=previous['title'] if previous else (version_label(version).split(' (loader')[0]
            if version else (record.get('family') if paired else None) or 'Windows')
        if previous is None:
            from entry_names import imported_name
            title=imported_name(owner,row,title)
        child = dict(disk_guid=disk,partition_guid=win,esp_guid=esp,boot_partition_guid=esp,
            title=title,
            loader_path=path,bcd_path=prefix+'BCD',single_os_bcd_verified=True,available=True,
            requires_ntfs=False,hash_locked=True,loader_sha256=digest,ownership='boot-menu-imported',
            _files=child_files,public_loader_hashes=public_hashes)
        if previous and not specific and not sector_route:
            child.update({k:previous[k] for k in ('boot_partition_guid','requires_ntfs','hash_locked','bcd_path')})
        children[key], stores[key] = child, store
    if addition:
        child = dict(addition['entry'])
        key = identity(child)
        require(key not in children, 'New Windows partition is already installed')
        prefix = 'EFI/BackToSeven/Children/'+child['partition_guid']+'/'
        if preserve_addition_paths:
            # Broader installs already stage their loader either on their NTFS
            # partition or at an exclusive ESP path. Keep that chosen route and
            # its BCD; importing an older standalone install must not convert it.
            if child['requires_ntfs']:
                original=(addition['root']/child['loader_path']).read_bytes()
            else:
                original=addition['esp_files'][child['loader_path']]
            require(m.sha(original)==child['loader_sha256'],'New child loader changed during menu preparation')
            child['_files']={}
            child['public_loader_hashes']=[m.sha(original)]
            children[key],stores[key]=child,addition['canonical_bcd']
            rows=list(rows)+[dict(observed=child)]
            result=menu.reconcile(files,disk,esp,rows,children,stores,
                (ROOT/'efi/Windows.efi').read_bytes(),(ROOT/'efi/OpenNtfsDxe.efi').read_bytes(),ntfs_4kn_driver=(ROOT/'efi/Ntfs4Kn.efi').read_bytes(),active=key,confirmed_absent=confirmed_absent)
            result['windows_sources']=sources
            return result
        original = (owner.session/'staged-efi/Microsoft/Boot/bootmgfw.efi').read_bytes()
        child['_files'] = dict(addition['esp_files'])
        # Filter shared fonts/support files; caller uses normal dependency
        # planning to add missing shared files without replacing existing ones.
        child['_files'] = {p:v for p,v in child['_files'].items()
            if p.startswith('EFI/W7DEP-WIN7-AUTO/') or p=='EFI/Microsoft/Boot/bootmgfw-w7dep-original.efi'}
        if child['loader_path'] != 'EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi':
            original = addition['esp_files'][child['loader_path']]
            child['loader_path'] = prefix+'bootmgfw.efi'
            child['_files'][child['loader_path']] = original
        child['bcd_path'] = prefix+'BCD'
        child['_files'][prefix+'BCD'] = addition['canonical_bcd']
        child['public_loader_hashes'] = [m.sha(original), gpu.ORIGINAL_SHA] if owner.confirmed_model=='MacBookPro11,5' else [m.sha(original)]
        children[key], stores[key] = child, addition['canonical_bcd']
        rows = list(rows)+[dict(observed=child)]
    result=menu.reconcile(files,disk,esp,rows,children,stores,
        (ROOT/'efi/Windows.efi').read_bytes(),(ROOT/'efi/OpenNtfsDxe.efi').read_bytes(),ntfs_4kn_driver=(ROOT/'efi/Ntfs4Kn.efi').read_bytes(),
        active=identity(addition['entry']) if addition else None,confirmed_absent=confirmed_absent)
    result['windows_sources']=sources
    return result


def verify_sources(owner,sources):
    """Recheck read-only installed boot files before publishing a recovery plan."""
    for source in sources:
        with Probe(source['partition'],owner.ntfscat) as probe:
            loader=probe.read('/Windows/Boot/EFI/bootmgfw.efi',8*1024**2)
            require(m.sha(loader)==source['loader_sha256'],'Existing Windows loader changed during review')
            if source.get('os_loader_sha256'):
                os_loader=probe.read('/Windows/System32/winload.efi',8*1024**2)
                require(m.sha(os_loader)==source['os_loader_sha256'],'Existing Windows OS loader changed during review')
            bcd=None
            try:bcd=probe.read('/EFI/Microsoft/Boot/BCD',2*1024**2)
            except (OSError,Refusal,ValueError):pass
            require((m.sha(bcd) if bcd is not None else None)==source['local_bcd_sha256'],
                    'Existing Windows local boot store changed during review')
            if source.get('route_loader_path'):
                route=probe.read(source['route_loader_path'],8*1024**2)
                require(m.sha(route)==source['route_loader_sha256'],
                        'Existing Windows menu loader changed during review')


def save_backup(root, files, disk, esp, changes, *, kind, windows=None):
    """Off-ESP immutable snapshot and a small authenticated-by-hash index."""
    folder = BACKUPS/esp/str(uuid.uuid4())
    folder.mkdir(parents=True,mode=0o700)
    before = tree_manifest(root)
    shutil.copytree(root,folder/'efi-before')
    require(tree_manifest(folder/'efi-before')==before, 'Experimental EFI backup failed verification')
    receipt = dict(schema=1,kind=kind,disk_guid=disk,esp_guid=esp,windows_guid=windows,
        folder=str(folder),before=before,
        changes=[dict(path=c['path'],before=c['expected'],after=m.sha(c['data']) if c['data'] is not None else None)
                 for c in changes])
    write_receipt(folder/'receipt.json',receipt)
    data = (folder/'receipt.json').read_bytes()
    pointer = json.dumps(dict(schema=1,folder=str(folder),sha256=m.sha(data)),sort_keys=True).encode()+b'\n'
    indexed = m.indexed(files)
    output = [dict(path=menu.LATEST,expected=m.sha(indexed[menu.LATEST.casefold()][1]) if menu.LATEST.casefold() in indexed else None,data=pointer)]
    if kind=='pre-install' and menu.BASELINE.casefold() not in indexed:
        output.append(dict(path=menu.BASELINE,expected=None,data=pointer))
    return output,receipt


class ExperimentalMenu(InstallationManager):
    def execute(self):
        self.begin()
        choice = self.choose(WARNING+'\n\n'+RESTORE,
            ['Rescan Windows installations and rebuild boot menu','Restore pre-install boot configuration',
             'Undo the most recent experimental menu change','Cancel'])
        if choice==3:
            self.note('Experimental settings cancelled. No boot files changed.');return
        scan=self.scan()
        require(scan['scan_complete'],'Windows scan incomplete; current boot configuration preserved')
        if choice in (1,2):
            return self.restore(scan, menu.BASELINE if choice==1 else menu.LATEST)
        require(scan['installations'],'No Windows installation found. '+NO_EXISTING)
        unique={r['target']['esp']:r for r in scan['installations']}
        rows=list(unique.values())
        row=rows[0] if len(rows)==1 else rows[self.choose('Choose the Windows disk to review:',[r['summary'] for r in rows])]
        target=row['target'];gpt=gpt_for(target);_,esp=match_gpt(target,gpt)
        root=self.mount_esp(target['esp'],readonly=True);before=tree_manifest(root)
        files=read_efi(root,before)
        selected=[r for r in scan['installations'] if r['target']['esp']==target['esp']]
        if len(selected)==1:
            require(self.choose(NO_EXISTING.replace('No existing Windows installation was found.',
                'Only one Windows installation was found.'),['Cancel','I understand; review experimental menu'])==1,
                'Experimental single-installation menu cancelled')
        import menu_presence
        old=menu.previous(files,gpt['guid'],esp['guid'])
        removed=menu_presence.collect(self,old['catalog']['entries'],scan['installations'],target)
        plan=assemble(self,root,files,gpt['guid'],esp['guid'],selected,confirmed_absent=removed)
        require(self.approve('rebuild_review',WARNING+'\n\nThe proposed list:\n'+
                '\n'.join(e['title']+(' (unavailable)' if not e['available'] else '') for e in plan['catalog']['entries'])+
                '\n\nProceed with a verified backup and the displayed menu changes?'),'Experimental menu cancelled')
        fresh=self.scan();require(fresh==scan,'Windows discovery changed; start again')
        menu_presence.recheck(self,old['catalog']['entries'],fresh['installations'],target,removed)
        verify_sources(self,plan['windows_sources'])
        root=self.mount_esp(target['esp'],readonly=False)
        def guard():
            current=info(target['esp'])
            require(current.get('DiskUUID','').lower()==esp['guid'] and current.get('MountPoint')==str(root)
                and current.get('ReadOnlyVolume') is False and gpt_for(target)['digest']==gpt['digest'],'EFI identity changed')
        guard();require_efi_unchanged(tree_manifest(root),before,'EFI changed during confirmation')
        links,receipt=save_backup(root,files,gpt['guid'],esp['guid'],plan['changes'],kind='menu-rebuild')
        tx=EFITransaction(root,self.session/'experimental-menu-transaction',guard)
        tx.prepare(plan['changes']+links);tx.commit();run(['/bin/sync'])
        self.note('Experimental Windows menu rebuilt. EFI backup: '+receipt['folder'])

    def restore(self,scan,pointer_path):
        # Locate snapshots even after the owner manually erases Windows 7.
        from install_windows7 import inventory
        candidates={t['esp']:t for t in inventory()}
        found=[]
        for device,target in candidates.items():
            if self.esp_owned and self.esp_device!=device:
                require(not self.close_esp(),'Could not detach prior EFI mount')
            root=self.mount_esp(device,readonly=True)
            file=root/pointer_path
            if file.is_file() and not file.is_symlink():
                require(file.stat().st_size<4096,'Invalid backup pointer')
                found.append((target,file.read_bytes()))
        require(found,'No saved configuration of this type was found')
        target,pointer=found[0] if len(found)==1 else found[self.choose('Choose the EFI backup:',[x[0]['esp'] for x in found])]
        meta=json.loads(pointer);folder=Path(meta['folder'])
        require(folder.is_absolute() and folder.resolve()==folder and BACKUPS in folder.parents and
                not folder.is_symlink(),'Invalid backup location')
        raw=(folder/'receipt.json').read_bytes();require(m.sha(raw)==meta['sha256'],'Backup receipt changed')
        receipt=json.loads(raw);gpt=gpt_for(target);_,esp=match_gpt(target,gpt)
        require(receipt['disk_guid']==gpt['guid'] and receipt['esp_guid']==esp['guid'],'Backup belongs to another disk')
        if pointer_path==menu.BASELINE and receipt.get('windows_guid'):
            require(not any(r['observed']['partition_guid']==receipt['windows_guid'] for r in scan['installations']),
                    'Back up Windows 7, then erase only its partition yourself before restoring the pre-install configuration.')
        require(tree_manifest(folder/'efi-before')==receipt['before'],'Saved EFI files failed verification')
        if self.esp_owned and self.esp_device!=target['esp']:require(not self.close_esp(),'Could not detach prior EFI mount')
        root=self.mount_esp(target['esp'],readonly=True);before=tree_manifest(root);indexed=m.indexed(before)
        changes=[]
        for item in receipt['changes']:
            path=item['path'];actual,current=indexed.get(path.casefold(),(path,None))
            if current==item['before']:continue
            require(current==item['after'],'Boot files have changed since this snapshot. Preserve them and seek recovery help: '+path)
            data=(folder/'efi-before'/path).read_bytes() if item['before'] else None
            changes.append(dict(path=actual,expected=current,data=data))
        require(changes,'The saved configuration is already present; no changes needed')
        require(self.approve('rebuild_review','Restore the saved EFI file configuration?\n\n'+WARNING+
                '\n\nSnapshot: '+str(folder)+'\nOnly recorded files with matching current hashes will change.'),'Restore cancelled')
        root=self.mount_esp(target['esp'],readonly=False)
        def guard():
            current=info(target['esp'])
            require(current.get('DiskUUID','').lower()==esp['guid'] and current.get('MountPoint')==str(root)
                and current.get('ReadOnlyVolume') is False and gpt_for(target)['digest']==gpt['digest'],'EFI identity changed')
        guard();require_efi_unchanged(tree_manifest(root),before,'EFI changed before restore')
        files=read_efi(root,before)
        links,_=save_backup(root,files,gpt['guid'],esp['guid'],changes,kind='menu-restore')
        tx=EFITransaction(root,self.session/'restore-menu-transaction',guard)
        tx.prepare(changes+links);tx.commit();run(['/bin/sync'])
        self.note('Recorded EFI configuration restored. Windows files, OpenCore and startup defaults were not changed.')
