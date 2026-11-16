"""Publisher download or app-owned cache only; never import arbitrary drivers."""
from pathlib import Path, PurePosixPath
import tempfile
import bootcamp_download as apple
from operation_progress import Meter,phase

class ReturnToDriverSelection(ValueError):pass


def file_hashes(root,note):
    paths=sorted(Path(root).rglob('*'));files={};seen=set();regular=[]
    for path in paths:
        apple.require(not path.is_symlink(),'Linked driver source is not accepted')
        apple.require(path.is_file() or path.is_dir(),'Special driver source is not accepted')
        key=path.relative_to(root).as_posix().casefold()
        apple.require(key not in seen,'Case-colliding driver source');seen.add(key)
        if path.is_file():regular.append(path)
    meter=Meter(note,'Recording driver file fingerprints',len(regular),'files recorded')
    for number,path in enumerate(regular,1):
        files[path.relative_to(root).as_posix()]=apple.sha256(path);meter.update(number,path.name)
    apple.require(files,'Driver folder is empty')
    return files


def decision(interaction,kind,text):
    result=interaction.request(kind,text)
    limit=4 if kind=='driver_mismatch' else 2
    apple.require(type(result) is int and 0<=result<limit,'Invalid driver dialog response')
    if kind=='driver_mismatch' and result==3:raise ReturnToDriverSelection('Back to driver selection')
    return result


def safe_delivery(files,baseline):
    """Even a user-approved mismatch cannot become an OS/EFI overwrite."""
    from user_driver_store import relative
    folders={name.split('/')[0].casefold() for name in baseline if '/' in name}
    singles={name.casefold() for name in baseline if '/' not in name}
    forbidden={'windows','efi','boot','bootmgr','bootmgr.efi','windeploy','$recycle.bin','system volume information'}
    apple.require(files,'Saved driver folder is empty')
    for name in files:
        relative(name);parts=PurePosixPath(name).parts
        apple.require(parts[0].casefold() not in forbidden,'Driver files overlap protected Windows or boot paths')
        apple.require((len(parts)>1 and parts[0].casefold() in folders) or
                      (len(parts)==1 and name.casefold() in singles),
                      'Changed drivers contain an unexpected top-level delivery path: '+name)


def source(plan,session,interaction,note,download,category,store=None,registry=None,download_only=False):
    while True:
        try:return _source(plan,session,interaction,note,download,category,store,registry,download_only)
        except ReturnToDriverSelection:
            note('Driver choice cancelled. Existing driver files were left untouched; returning to driver selection.')
            download_only=False

def _source(plan,session,interaction,note,download,category,store=None,registry=None,download_only=False):
    from user_driver_store import Store
    from driver_cache_trust import Registry
    uid=getattr(interaction,'user_uid',None)
    if store is None and uid is not None:store=Store(uid)
    if registry is None:registry=Registry()
    model,family=plan['model'],plan['family'];candidates=[]
    if store is not None:
        try:candidates=[c for c in store.candidates(model,family) if c['receipt'].get('category')==category]
        except (OSError,ValueError) as error:note('Saved drivers could not be listed: '+str(error))
    if plan.get('older_windows_fallback'):
        # Saved packages must carry the same intended driver OS in their protected provenance.
        candidates=[c for c in candidates if c['receipt'].get('driver_family_basis')==plan['driver_family']
                    and c['receipt'].get('older_windows_fallback') is True]
    origin='official Apple drivers' if category=='official' else 'community support from GitHub and its original publishers'
    action=0 if download_only else interaction.choose('Choose drivers for '+model+' / '+family+'. Only app-downloaded drivers '
        'in its default Application Support folder can be reused. Drivers are copied, not installed automatically.',
        ['Download '+origin,'Reuse previously downloaded drivers','Skip this driver preparation'])
    if action==2:return None
    work=Path(tempfile.mkdtemp(prefix='driver-source-',dir=str(session)))
    if action==1:
        if not candidates:
            if interaction.choose('No matching app-downloaded driver package is available in the default folder.',
                    ['Download drivers','Cancel and go back'])==1:raise ValueError('Cancelled by user before deployment')
        else:
            entry=candidates[interaction.choose('Choose a previous app download for this model and Windows version.',
                [str(c['receipt'].get('label','Saved drivers')).replace('\n',' ')[:160]+' — '+c['id'] for c in candidates])]
            try:
                baseline=registry.record(entry['receipt'].get('trust_id'),uid,model,family,category)
            except (OSError,ValueError) as error:
                note('Cannot establish that these files were downloaded by the app: '+str(error))
                if interaction.choose('This cache entry has no valid protected download record. Existing files will be kept.',
                        ['Download a new copy','Cancel and go back'])==1:raise ValueError('Cancelled by user before deployment')
            else:
                if plan.get('older_windows_fallback'):
                    trusted=baseline['receipt']
                    apple.require(trusted.get('driver_family_basis')==plan['driver_family'] and trusted.get('older_windows_fallback') is True,
                        'Saved driver provenance does not match the acknowledged fallback Windows version')
                verify=interaction.choose('Hash-check these saved drivers against the original app download before reuse? '
                    'Skipping leaves their contents unchecked; path and transfer-safety checks still run.',
                    ['Hash-check saved drivers (recommended)','Skip optional hash verification'])==0
                root=work/'overlay'
                try:files=store.restore(entry,root,note)
                except (OSError,ValueError) as error:
                    note('Saved drivers could not be read safely: '+str(error))
                    if interaction.choose('These files cannot be reused. They will not be deleted automatically.',
                            ['Download a new copy','Cancel and go back'])==1:raise ValueError('Cancelled by user before deployment')
                else:
                    mismatch=verify and files!=baseline['files']
                    status='verified' if verify and not mismatch else 'unchecked'
                    if mismatch:
                        choice=decision(interaction,'driver_mismatch','These saved drivers differ from the original download. '
                            'They may be corrupted, incomplete or modified. The app has not changed or deleted them. '
                            'Use them only if you accept that risk. Moving or deleting affects ONLY this selected cache package.')
                        if choice in (1,2):
                            result=store.retire(entry,'move-cache' if choice==1 else 'delete-cache')
                            note('Selected cache package '+result['action']+(': '+result['path'] if result.get('path') else ''))
                            root=None
                        else:status='mismatch-accepted-by-user'
                    if root is not None:
                        safe_delivery(files,baseline['files'])
                        note('Saved-driver content check: '+status+'. No driver download needed.')
                        receipt=dict(baseline['receipt'],reused=True,cache_entry=entry['id'],
                            cache_verification=status,authenticity_verified=(status=='verified'))
                        return dict(root=root,receipt=receipt,files=files)
    apple.require(store is not None,'The signed-in macOS user is required to save downloaded drivers')
    storage=decision(interaction,'driver_storage','Where should GoAwayBCA save this download?\n\n'
        'Default: your Library/Application Support/BackToSeven/Drivers folder. These downloads can be copied to Windows '
        'now and reused later.\n\nCustom location: save only. GoAwayBCA will NOT automatically copy that download to '
        'Windows or reuse it from there; transfer it yourself after installation. This avoids importing user-modified folders.')
    output=None
    if storage==1:
        output=interaction.request('driver_save_folder','Choose a folder for this output-only driver download.')
        apple.require(type(output) is str and Path(output).is_absolute(),'Select an absolute destination folder')
    result=download()
    if result is None or result['receipt'].get('source')=='Apple-download-skipped':return None
    root,receipt=result['root'],result['receipt']
    # Required provenance baseline, not optional independent readback. Never
    # replace this with a user-approved modified package's current hashes.
    files=file_hashes(root,note);safe_delivery(files,files)
    product=receipt.get('product',{})
    label=product.get('version') or product.get('id') or receipt.get('recipe_id') or 'Community Windows 7 support'
    meta=dict(model=model,family=family,category=category,label=str(label))
    if plan.get('older_windows_fallback'):
        meta.update(driver_family_basis=plan['driver_family'],older_windows_fallback=True)
        apple.require(receipt.get('driver_family_basis')==plan['driver_family'] and receipt.get('older_windows_fallback') is True,
            'Driver download does not match the acknowledged fallback Windows version')
    if output is None:
        meta['trust_id']=registry.save(uid,model,family,category,files,receipt)
        saved=store.save(root,meta,note=note)
    else:
        saved=store.export(root,dict(meta,custom_output_only=True),output,note=note)
    note('Driver download complete. Saved at: '+saved['path'])
    verify=interaction.choose('Hash-check the saved download now? Required publisher authentication and safe-copy '
        'checks have already run; this is an additional readback of the saved files.',
        ['Hash-check the saved download (recommended)','Skip optional hash verification'])==0
    status='unchecked'
    if verify:
        phase(note,'Verifying saved driver download')
        check=work/'saved-readback'
        copied=(store.local(str(Path(saved['path'])/'Files'),check,note) if output is not None
                else store.restore(saved,check,note))
        if copied!=files:
            choice=decision(interaction,'driver_mismatch','The saved download does not match its original downloaded files. '
                'No files have been deleted. Choose whether to accept the changed saved copy, move it to your Desktop '
                'and download again, delete only this package and download again, or cancel.')
            if choice in (1,2):
                action='move-cache' if choice==1 else 'delete-cache'
                result=store.retire(saved,action,output=output) if output is not None else store.retire(saved,action)
                note('Selected downloaded package '+result['action']+(': '+result['path'] if result.get('path') else ''))
                return source(plan,session,interaction,note,download,category,store,registry,download_only=True)
            safe_delivery(copied,files)
            root=check;files=copied;status='mismatch-accepted-by-user'
            note('Changed saved download accepted explicitly; it is NOT recorded as verified.')
        else:
            status='verified';note('Saved driver download hash verification complete.')
    else:note('Optional saved-download hash verification skipped by user; recorded as unchecked.')
    if output is not None:
        note('Custom-location download saved only. It will not be copied to the Windows partition or added to the reusable cache.')
        return None
    return dict(root=root,files=files,receipt=dict(receipt,saved_driver_entry=saved['id'],cache_verification=status,
        authenticity_verified=status=='verified'))
