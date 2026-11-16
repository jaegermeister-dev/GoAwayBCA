"""Stage the user-selected RAM-only ROM-table experiment, never live EFI.

The AMD ROM table is identical to the 11,5 payload. This is NOT a proven Intel
or Nvidia fix, and contains no 11,3-specific adaptation. The tested 11,5 Win7
path uses its unchanged accepted helper instead of this experimental wrapper.
"""
from pathlib import Path
import hashlib
from install_windows7 import require,require_x64_pe,run,tree_manifest,sha256
from installation_records import canonical,make_marker

HELPER_SHA='e3f0584dca446d4926749087de19b24ae2bfc74727ef3eb1e6342fffd1f41d5f'

def files(resources,original):
    helper=Path(resources)/'efi/ExperimentalFix.efi'
    require(sha256(helper)==HELPER_SHA,'Experimental EFI helper fingerprint mismatch')
    require_x64_pe(helper)
    require(original[:2]==b'MZ','Missing original Windows loader')
    return {'Fix.efi':helper.read_bytes(),'Original.efi':original,
        'Entry.bin':b'NMBFIX01'+hashlib.sha256(original).digest()}

def wrap_stock(overlay,resources,tool,session):
    """Add a separate helper; retain the original unmodified stock bootmgfw."""
    root=overlay['root'];boot=root/'EFI/Microsoft/Boot'
    additions=files(resources,(boot/'bootmgfw.efi').read_bytes())
    for name,data in additions.items():
        with (boot/name).open('xb') as f:f.write(data)
    record=overlay['record'];partition=record['partition_guid']
    record['efi_assets'] += [dict(partition_guid=partition,path='EFI/Microsoft/Boot/'+name,
        sha256=hashlib.sha256(data).hexdigest(),exclusive=True) for name,data in additions.items()]
    record['experimental_rom_table']=True
    (root/'WinDeploy/installation.json').write_bytes(canonical(make_marker(record))+b'\n')
    wim=Path(session)/'ntfs-overlay-experimental.wim'
    run([tool,'capture',root,wim,'WinDeploy selected experimental boot path','--compress=none'])
    overlay.update(wim=wim,wim_sha256=sha256(wim),files=tree_manifest(root))
    overlay['entry'].update(loader_path='EFI/Microsoft/Boot/Fix.efi',hash_locked=True,
                            loader_sha256=HELPER_SHA)
