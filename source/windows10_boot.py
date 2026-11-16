"""Stage independent Windows 10 boot files from the selected WIM edition.

Only new session directories are written. Application to an NTFS partition and
EFI publication are separate confirmed transactions. No host BCD is opened.
"""
import hashlib
from pathlib import Path
import shutil
import uuid
import json
from bcd import personalise_colocated, personalise
from bcd_semantics import identity as verify_bcd_identity
from install_windows7 import ROOT, run, require, ci_child, require_x64_pe, tree_manifest, sha256
from installation_records import make_marker, canonical

TEMPLATE_SHA='78305a0512fcb202e84d4862da7ed66d34b93a3bb92e08aed3fbfb90fef27e99'
TEMPLATE81_SHA = 'f796708a6a52be6278460bf5f4bedf5c9b1685da696f882c73ce4ccd5dec2abb'


def stage(tool, wim, edition, session, gpt, win, esp, wim_sha, template, note=None):
    require(edition['family'] in ('Windows 8','Windows 8.1','Windows 10','Windows 11'), 'Stock EFI staging supports Windows 8, 8.1, 10 and 11')
    template_sha=TEMPLATE81_SHA if edition['family'] in ('Windows 8','Windows 8.1') else TEMPLATE_SHA
    require(template_sha,'Windows 8.1 BCD template has not been verified')
    session=Path(session)
    extracted=session/'extracted-win10-boot';extracted.mkdir()
    from operation_progress import run_phase,hash_file
    def command(argv,label):
        return run_phase(argv,note,label) if note else run(argv)
    for folder in ('EFI','Fonts'):
        command([tool,'extract',wim,str(edition['index']),'/Windows/Boot/'+folder,
             '--dest-dir='+str(extracted),'--no-acls','--no-attributes'],'Extracting Windows boot '+folder)
    overlay=session/'ntfs-overlay';overlay.mkdir()
    boot=overlay/'EFI/Microsoft/Boot';boot.parent.mkdir(parents=True)
    shutil.copytree(ci_child(extracted,'EFI'),boot)
    fonts=boot/'Fonts'
    require(not fonts.exists(), 'Unexpected pre-existing fonts path')
    shutil.copytree(ci_child(extracted,'Fonts'),fonts)
    loader=ci_child(boot,'bootmgfw.efi');require_x64_pe(loader)
    require(not (boot/'BCD').exists(), 'WIM supplied an unexpected boot store')
    (boot/'BCD').write_bytes(personalise_colocated(Path(template).read_bytes(),template_sha,
                                                gpt['guid'],win['guid']))
    # Windows specializes/servicing also accesses the actual ESP system BCD.
    # Stage its separate normal single-OS store in memory; do not publish it or
    # substitute it into the NTFS loader's colocated store. A future validated
    # activation transaction must preserve the other installed OS's BCD.
    canonical_bcd=personalise(Path(template).read_bytes(),template_sha,
                             gpt['guid'],esp['guid'],win['guid'])
    verify_bcd_identity(canonical_bcd,gpt['guid'],esp['guid'],gpt['guid'],win['guid'])
    # No NTFS fallback path: only the shared ESP chooser advertises Windows.
    assets=[dict(partition_guid=win['guid'],path=path,sha256=digest,exclusive=True)
            for path,digest in tree_manifest(overlay,note,'Verifying prepared Windows boot files').items()]
    record=dict(schema=1,creator='WinDeploy',state='committed',
        installation_uuid=str(uuid.uuid4()),disk_guid=gpt['guid'],partition_guid=win['guid'],
        esp_guid=esp['guid'],partition_bytes=(win['last']-win['first']+1)*gpt.get('sector_size',512),
        partition_offset=win['first']*gpt.get('sector_size',512),family=edition['family'],edition=edition['name'],
        image_index=edition['index'],wim_sha256=wim_sha,efi_assets=assets)
    marker=make_marker(record)
    folder=overlay/'WinDeploy';folder.mkdir()
    import report_metadata
    (folder/'deployment.json').write_text(json.dumps(report_metadata.deployment(ROOT,edition,'unknown',gpt['guid'],win['guid']),indent=2)+'\n')
    (folder/'installation.json').write_bytes(canonical(marker)+b'\n')
    overlay_wim=session/'ntfs-overlay.wim'
    command([tool,'capture',overlay,overlay_wim,'WinDeploy independent boot files','--compress=none','--check'],
            'Preparing Windows boot-file image')
    # This is a small additive tree, not a second operating-system image.
    return dict(root=overlay,wim=overlay_wim,record=record,marker=marker,
                files=tree_manifest(overlay),wim_sha256=(hash_file(overlay_wim,note,'Hashing boot-file image') if note else sha256(overlay_wim)),
                canonical_bcd=canonical_bcd,
                canonical_bcd_sha256=hashlib.sha256(canonical_bcd).hexdigest())
