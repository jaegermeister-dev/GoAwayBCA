"""Add the tested private 11,5 GPU helper to a NEW staged EFI tree only.

Never operates on a mounted ESP, never modifies BCD, never changes startup.
The helper remains firmware/OpenCore-pinned engineering code, not general
11,4 compatibility. Callers must confirm the applicable profile separately.
"""
import hashlib
from pathlib import Path

HELPER_SHA='e883cafc83e127c09ccec1c6cef96d4cf0b2f39242ba66e68adaa0279c2d7c07'
ORIGINAL_SHA='6470d503fc884f23b515457a03018ece8bf6f6c5bd358f3b5518711ebadb5161'
MARKER_SHA='784bd1fbe529be3c24c82daf84e92ce972792d46ca87a7fb48be899fc1dc10ed'
PUBLIC=('Microsoft/Boot/bootmgfw.efi','Boot/BOOTX64.EFI')
SAVED=('Microsoft/Boot/bootmgfw-w7dep-original.efi','Boot/BOOTX64-W7DEP-ORIGINAL.EFI')
ESP_TYPE='c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
BASIC_TYPE='ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'


def require(ok,message):
    if not ok:raise ValueError(message)
def sha(data):return hashlib.sha256(data).hexdigest()
def regular(path):
    path=Path(path)
    require(path.is_absolute() and path.resolve()==path and path.is_file() and not path.is_symlink(),
            'Expected a canonical regular staging file')
    return path.read_bytes()


def selected_binding(gpt,win,esp,bcd):
    """Bind to current, already validated GPT observations, never old lab IDs.

    The unchanged helper reads its saved loader from the ESP on which it was
    loaded, using the canonical Microsoft path. The selector validates the GPT
    and activates the selected installation's private BCD before calling it.
    That BCD is what identifies Windows; no diskXsY or partition GUID is baked
    into WIN7AUTO.efi. Keep this check structural, not a byte-search/patch.
    """
    from boot_catalog import guid
    from bcd_semantics import identity
    disk_id,win_id,esp_id=(guid(gpt['guid']),guid(win['guid']),guid(esp['guid']))
    require(len({disk_id,win_id,esp_id})==3,'Windows, ESP and disk identities must be distinct')
    rows=list(gpt['partitions'].values())
    seen=[guid(p['guid']) for p in rows]
    require(len(seen)==len(set(seen)) and disk_id not in seen,'Ambiguous current GPT identities')
    for row,identifier,kind in ((win,win_id,BASIC_TYPE),(esp,esp_id,ESP_TYPE)):
        matching=[p for p in rows if guid(p['guid'])==identifier]
        require(len(matching)==1 and matching[0]==row,'Selected partition is not in the current GPT')
        require(guid(row['type'])==kind,'Incorrect Windows or ESP partition type')
        require(type(row['first']) is int and type(row['last']) is int and
                0<row['first']<=row['last'],'Invalid selected partition geometry')
    require(win['last']<esp['first'] or esp['last']<win['first'],'Windows partition overlaps the ESP')
    identity(bcd,disk_id,esp_id,disk_id,win_id)
    return dict(disk_guid=disk_id,partition_guid=win_id,esp_guid=esp_id,
        partition_offset=win['first']*gpt.get('sector_size',512),partition_bytes=(win['last']-win['first']+1)*gpt.get('sector_size',512),
        bcd_sha256=sha(bcd),boot_partition_guid=esp_id,
        loader_path='EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi',bcd_path='EFI/Microsoft/Boot/BCD',
        binding_source='current-selected-GPT-and-structurally-verified-BCD',
        selector_activates_independent_bcd=True,helper_binary_personalized=False)


def stage_for_target(session,efi_root,helper,marker,model,family,gpt,win,esp):
    """Prepare the tested helper and verify the selected installation binding."""
    binding=selected_binding(gpt,win,esp,regular(Path(efi_root)/'Microsoft/Boot/BCD'))
    result=stage(session,efi_root,helper,marker,model,family)
    require(sha(regular(Path(efi_root)/'Microsoft/Boot/BCD'))==binding['bcd_sha256'],
            'Selected Windows boot store changed during helper staging')
    result['target_binding']=binding
    return result


def stage(session,efi_root,helper,marker,model,family):
    session=Path(session);root=Path(efi_root)
    require(model=='MacBookPro11,5' and family=='Windows 7','This accepted helper is private 11,5 Windows 7 only')
    require(session.is_absolute() and session.resolve()==session and session.is_dir() and not session.is_symlink(),
            'Invalid private staging session')
    require(root.is_absolute() and root.resolve()==root and root.parent==session
            and root.name=='staged-efi' and root.is_dir() and not root.is_symlink(),
            'EFI helper requires this session\'s fresh staged-efi directory')
    # A mounted volume cannot masquerade as an ordinary subdirectory here.
    require(root.stat().st_dev==session.stat().st_dev,'EFI staging directory is on another filesystem')
    helper_bytes=regular(helper);marker_bytes=regular(marker)
    require(sha(helper_bytes)==HELPER_SHA and helper_bytes[:2]==b'MZ','Unverified GPU helper')
    require(sha(marker_bytes)==MARKER_SHA,'Unverified GPU helper marker')
    before={}
    for file in root.rglob('*'):
        require(not file.is_symlink() and file.resolve()==file and file.stat().st_dev==root.stat().st_dev,
                'Linked or foreign staged EFI entry')
        if file.is_file():before[file.relative_to(root).as_posix()]=sha(file.read_bytes())
    require(before.get('Microsoft/Boot/BCD') is not None,'Missing staged Windows boot store')
    originals=[regular(root/path) for path in PUBLIC]
    require(all(sha(data)==ORIGINAL_SHA for data in originals),'Prepared Windows 7 loader does not match tested helper')
    auxiliary=root/'W7DEP-WIN7-AUTO'
    require(not auxiliary.exists() and all(not (root/path).exists() for path in SAVED),
            'Existing helper files must not be overwritten')
    # Save both originals before replacing this unpublished staging copy.
    for relative,data in zip(SAVED,originals):
        with (root/relative).open('xb') as out:out.write(data)
        require(regular(root/relative)==data,'Original loader staging readback failed')
    auxiliary.mkdir()
    for name,data in (('WIN7AUTO.efi',helper_bytes),('ALLOW-AUTO.txt',marker_bytes)):
        with (auxiliary/name).open('xb') as out:out.write(data)
        require(regular(auxiliary/name)==data,'Helper staging readback failed')
    for path in PUBLIC:
        require(sha(regular(root/path))==ORIGINAL_SHA,'Staged loader changed before replacement')
        (root/path).write_bytes(helper_bytes)
        require(regular(root/path)==helper_bytes,'Helper alias staging readback failed')
    for path,expected in before.items():
        if path not in PUBLIC:require(sha(regular(root/path))==expected,'Unrelated staged EFI file changed')
    return dict(profile='private-tested-11,5-win7-M370X',mandatory=True,
        firmware_and_opencore_pinned=True,helper_sha256=HELPER_SHA,
        original_loader_sha256=ORIGINAL_SHA,bcd_unchanged=True,
        physical_efi_written=False,default_boot_changed=False,
        added_paths=list(SAVED)+['W7DEP-WIN7-AUTO/WIN7AUTO.efi','W7DEP-WIN7-AUTO/ALLOW-AUTO.txt'])
