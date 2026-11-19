"""Prepare destination-specific restore boot files in a private directory.

Uses the existing stock Windows boot-file staging, without changing normal
installation or menu code. A restored foreign installation receives an explicit
unverified marker, never a new application-ownership receipt.
"""
import hashlib
import json
from pathlib import Path

from backup_restore_identity import regular, require
from backup_restore_filesystem import command


BOOT_CONFIG = '[ExclusionList]\n*\n[ExclusionException]\n/Windows/Boot/EFI\n/Windows/Boot/Fonts\n'


def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        for data in iter(lambda:source.read(1024**2),b''):digest.update(data)
    return digest.hexdigest()


def boot_image(image, wim, work, note=lambda text: None):
    """Capture only the saved Windows EFI boot directory and fonts.

    Absolute exclusion exceptions include descendants even while all other
    paths are excluded (wimlib capture config). User files, EFS and registry
    hives are not needed for this small boot-file image.
    """
    regular(image); work=Path(work)
    config=work/'restore-boot-files.ini'
    with config.open('x',encoding='ascii') as output:output.write(BOOT_CONFIG)
    output=work/'restore-boot-files.wim'
    require(not output.exists(),'Use a new restore boot-image filename.')
    command([wim,'capture',image,output,'Restored Windows boot files','--compress=none',
             '--check','--strict-acls','--norpfix','--config='+str(config)],work/'restore-boot-capture.log',note)
    return output


def provenance_payload(overlay, plan):
    """Remove the new creation pair for an external/unverified source."""
    from installation_records import make_marker
    record=overlay['record']
    if plan['provenance']['eligible']:
        record['restore_origin']=plan['restored_history']['origin']
        record['restored_from_backup']=plan['restored_history']['backup_id']
        marker=make_marker(record)
    else:
        record=None
        marker=dict(schema=1,creator='GoAwayBCA-restored-unverified',
                    source_backup=plan['restored_history']['backup_id'],uninstall_eligible=False,
                    disk_guid=plan['destination']['disk_guid'],
                    partition_guid=plan['destination']['partition_guid'])
    return record,marker


def prepare(image, plan, session, resources, wim, gpt, win, esp, note=lambda text: None):
    from product_scope import require_family
    from install_multiboot import stock_entry
    from install_windows7 import tree_manifest
    from installation_records import canonical
    from backup_windows import HISTORY_PATH
    import windows10_boot
    require_family(plan['family'])
    # The separately planned IA32 boot path must supply its matching loader
    # staging; do not mislabel an x64 boot overlay as an x86 restore.
    require(plan['architecture']==9,'The selected restore requires the matching 32-bit EFI boot-file path.')
    work=Path(session)/'restore-boot';work.mkdir(mode=0o700)
    boot=boot_image(image,wim,work,note)
    edition=dict(index=1,family=plan['family'],name=plan['family']+' restored backup',
                 architecture=plan['architecture'],version=None)
    template=Path(resources)/('BCD81.template' if plan['family']=='Windows 8.1' else 'BCD10.template')
    overlay=windows10_boot.stage(wim,boot,edition,work,gpt,win,esp,sha(boot),template,note)
    entry=stock_entry(overlay['record']);entry['title']=plan['title']
    record,marker=provenance_payload(overlay,plan)
    if record is None:
        entry['ownership']='boot-menu-imported'
    root=overlay['root']
    deployment=root/'WinDeploy/deployment.json'
    observed=json.loads(deployment.read_text())
    observed.update(operation='restore',source_backup=plan['restored_history']['backup_id'],
                    scope='Observed during restoration. This is not evidence of the original installation host.')
    deployment.write_text(json.dumps(observed,indent=2)+'\n')
    (root/'WinDeploy/installation.json').write_bytes(canonical(marker)+b'\n')
    (root/HISTORY_PATH.lstrip('/')).write_text(json.dumps(plan['restored_history'],indent=2)+'\n',encoding='utf8')
    final=work/'restore-overlay.wim'
    command([wim,'capture',root,final,'Restored Windows boot and history','--compress=none','--check'],
            work/'restore-overlay-capture.log',note)
    return dict(overlay,record=record,marker=marker,entry=entry,esp_files={},wim=final,
                wim_sha256=sha(final),files=tree_manifest(root),
                restore_provenance=plan['provenance']['status'])
