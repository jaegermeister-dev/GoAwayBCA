"""Plan a genuine Microsoft ESP boot path; do not execute any disk writes here."""
from pathlib import Path
from boot_catalog import identity
from windows_discovery import validate_efi_application
import menu_lifecycle as m


def choose(owner, rows, target):
    existing=[r for r in rows if r['target']['disk']['DeviceIdentifier']==target['disk']['DeviceIdentifier']]
    if existing:
        recommended=True
        reason='Another Windows installation exists on this drive. The custom menu is recommended for choosing between installations.'
    else:
        recommended=owner.choose('Do you intend to keep more than one Windows installation on this drive?',
            ['No — one Windows installation','Yes — multiple Windows installations'])==1
        reason=('The custom menu is recommended when you intend to keep multiple Windows installations.' if recommended else
                'For one Windows installation, the standard Microsoft boot path is recommended. A custom menu is optional.')
    options=['Use the custom Windows boot menu','Use the standard Microsoft boot path (no custom menu)']
    if not recommended:options.reverse()
    answer=owner.choose(reason+'\n\nThe standard path boots Windows directly from OpenCore or the Mac’s Option/Alt picker. '
        'It does not change your default startup disk. Rebuild Boot Menu can create or reactivate the custom menu later.',options)
    enabled=options[answer].startswith('Use the custom')
    if not enabled and existing:
        from install_windows7 import require
        require(owner.approve('review','Use the standard Microsoft boot path for the new Windows installation?\n\n'
            'The shared Windows entry will start this new installation. Other Windows partitions will be kept, '
            'but they may not be selectable from that entry until you use Rebuild Boot Menu. Existing menu files '
            'and a backup of the current EFI files will be retained. OpenCore and Apple files are preserved.'),
            'Standard boot selection cancelled.')
    return enabled


def plan(overlay, files, manifest, previous, rows, menu_plan=None, public_hashes=()):
    """Retain an existing menu privately; publish stock Microsoft entry points.

    No fresh chooser is installed when there was no menu. A caller-supplied
    existing-menu plan already reconciles its private BCDs and active identity.
    """
    before=m.indexed(manifest)
    boot=Path(overlay['root'])/'EFI/Microsoft/Boot'
    loader=(boot/'bootmgfw.efi').read_bytes();validate_efi_application(loader)
    known={m.CHOOSER_SHA,*m.PRIOR_CHOOSER_SHAS,m.sha(loader),*public_hashes}
    current=before.get('efi/microsoft/boot/bootmgfw.efi',(None,None))[1]
    m.require(current is None or current in known,
        'The existing Microsoft boot entry could not be identified. It was preserved; no partition was formatted.')
    desired={}
    # Stock resources come from this selected ISO. Other vendors' paths stay out.
    for p in sorted(boot.rglob('*')):
        if p.is_file():desired['EFI/Microsoft/Boot/'+p.relative_to(boot).as_posix()]=p.read_bytes()
    desired[m.CANON]=overlay['canonical_bcd']
    desired['EFI/Microsoft/Boot/bootmgfw.efi']=loader
    fallback='EFI/Boot/BOOTX64.EFI'
    old=before.get(fallback.casefold(),(None,None))[1]
    if old is None or old in known:desired[fallback]=loader
    # Retain stores independently so a later Rebuild can recover old Windows
    # even when its installer never placed a BCD on its NTFS partition.
    file_index=m.indexed(files)
    canonical=file_index.get(m.CANON.casefold(),(None,None))[1]
    for row in rows:
        if row['observed']['disk_guid']!=overlay['record']['disk_guid']:continue
        path='EFI/WinDeploy/Installs/'+row['observed']['partition_guid']+'/BCD'
        data=previous['stores'].get(identity(row['observed']),canonical)
        if data is not None and path.casefold() not in before:desired[path]=data
    own='EFI/WinDeploy/Installs/'+overlay['record']['partition_guid']+'/BCD'
    if own.casefold() not in before:desired[own]=overlay['canonical_bcd']
    for suffix in ('.LOG','.LOG1','.LOG2'):
        path=m.CANON+suffix
        if path.casefold() in before:desired[path]=None
    changes={c['path'].casefold():c for c in (menu_plan or {}).get('changes',[])}
    # Do not re-add a fallback owned by OpenCore/another loader.
    if fallback not in desired:changes.pop(fallback.casefold(),None)
    for path,data in desired.items():
        actual,digest=before.get(path.casefold(),(path,None))
        changes.pop(path.casefold(),None)
        if (m.sha(data) if data is not None else None)!=digest:
            changes[path.casefold()]=dict(path=actual,expected=digest,data=data)
    ordered=list(changes.values())
    ordered.sort(key=lambda c:c['path'].casefold() in ('efi/microsoft/boot/bootmgfw.efi','efi/boot/bootx64.efi'))
    return dict(changes=ordered,catalog=(menu_plan or {}).get('catalog',previous['catalog']),
        windows_sources=(menu_plan or {}).get('windows_sources',[]),boot_mode='standard')
