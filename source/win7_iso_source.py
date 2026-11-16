"""Windows 7 ISO preparation, without a bundled complete Windows image.

Only the audited ISO profile is accepted. The compact delta contains matching
compatibility resources and image metadata; missing Windows resources MUST
come from the user's ISO. These derived assets are not cleared for publication.
This creates a new private-session file, never edits input media or a partition.
"""
import json
from pathlib import Path
import shutil
import subprocess
from prepared_media import STOCK_WIM_SHA256, LOADER_SHA256
from operation_progress import hash_file, run_phase
from windows_images import inspect_editions

DELTA_SHA256='01cbfe543c9dc302e069ba7ac8a708792f772ba89977ea8d80993039a3f8e2a5'
DELTA_BYTES=236529840
PROFILE='owner-private-win7-sp1-x64-iso-delta-v1'


def require(ok,message):
    if not ok:raise ValueError(message)


def regular(path):
    path=Path(path)
    require(path.is_absolute() and path.resolve(strict=True)==path and
            path.is_file() and not path.is_symlink(),'Source must be a canonical regular file')
    return path


def inspect(tool,path):
    xml=subprocess.check_output([str(tool),'info',str(path),'--xml'])
    require(len(xml)<8*1024**2,'Oversized source image metadata')
    return inspect_editions(xml)


def signature(editions):
    return [(e['index'],e['name'],e['family'],e['version']) for e in editions]


def prepare(resources,stock_wim,session,tool,note):
    stock=regular(stock_wim);delta=regular(Path(resources)/'private-win7-delta/win7-compatibility.delta.wim')
    session=Path(session)
    require(session.is_absolute() and session.resolve(strict=True)==session and session.is_dir()
            and not session.is_symlink(),'Use a canonical private session directory')
    require(delta.stat().st_size==DELTA_BYTES,'Unexpected private compatibility payload size')
    require(hash_file(stock,note,'Verifying original Windows 7 ISO image')==STOCK_WIM_SHA256,
            'This preparation method requires the expected Windows 7 SP1 x64 ISO. '
            'Other ISO versions/languages are not supported by this preparation profile. No partition was erased.')
    require(hash_file(delta,note,'Verifying Windows 7 compatibility resources')==DELTA_SHA256,
            'Bundled Windows 7 compatibility resources changed; no partition was erased')
    original=inspect(tool,stock);prepared=inspect(tool,delta)
    require(len(original)==4 and signature(original)==signature(prepared)
            and all(e['family']=='Windows 7' and tuple(e['version'])==(6,1,7601) for e in original),
            'Compatibility profile does not match every ISO edition')
    require(shutil.disk_usage(session).free>4*1024**3,
            'Need at least 4 GiB free to prepare Windows 7 from your ISO')
    destination=session/'iso-preparation';destination.mkdir(mode=0o700)
    output=destination/'install.wim'
    note('Building the Windows 7 installation source from your selected ISO. '
         'No complete Windows image is bundled in this app. Normal Windows setup is unchanged.')
    # Export, not apply/recapture: preserve original image metadata, streams,
    # hard links and security descriptors while resolving unchanged resources
    # exclusively from the pinned source WIM. No source or target is overwritten.
    run_phase([tool,'export',delta,'all',output,'--ref='+str(stock),'--compress=LZX',
               '--threads=2','--check'],note,'Preparing Windows 7 from ISO')
    require(output.is_file() and not output.is_symlink(),'Image reconstruction produced no regular file')
    require(signature(inspect(tool,output))==signature(original),'Reconstructed image editions differ')
    run_phase([tool,'verify',output],note,'Validating reconstructed Windows 7 resources')
    # This verification is required to establish that reconstruction succeeded,
    # distinct from the later optional pre-install verification pass.
    output.chmod(0o400)
    receipt=dict(profile=PROFILE,snapshot=str(output),wim_sha256=hash_file(output,note,'Hashing prepared Windows 7 source'),
        stock_wim_sha256=STOCK_WIM_SHA256,delta_sha256=DELTA_SHA256,
        boot_manager_sha256=LOADER_SHA256,boot_tested=False,
        includes_supplied_storage_usb_drivers=True,input_iso_required=True,
        complete_windows_image_bundled=False,private_owner_test_only=True,
        publisher_redistribution_cleared=False)
    (destination/'preparation.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return output,receipt
