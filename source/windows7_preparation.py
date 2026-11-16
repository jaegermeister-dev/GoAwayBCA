"""Stage first-boot inbox keyboard discovery in new session files only.

This is not a Setup/OOBE hook, driver-signature bypass or per-keyboard binding.
The original selected image supplies every Microsoft file. Installation of the
prepared hive is a separate guarded NTFS operation, never a live-registry edit.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import offline_keyboard
from windows_images import inspect_editions

PACKAGE='/Windows/System32/DriverStore/FileRepository/keyboard.inf_amd64_neutral_0684fdc43059f486'
FILES=('keyboard.inf','kbdhid.sys','kbdclass.sys','i8042prt.sys')
HIVE='/Windows/System32/config/SOFTWARE'


def stage(tool,wim,index,session,library):
    require=offline_keyboard.require
    session=Path(session);wim=Path(wim)
    require(session.is_absolute() and session.resolve()==session and session.is_dir()
            and not session.is_symlink(),'Use a canonical private session directory')
    require(wim.is_absolute() and wim.resolve()==wim and wim.is_file()
            and not wim.is_symlink(),'Use a regular WIM source')
    require(type(index) is int and 1<=index<=100,'Invalid WIM image index')
    metadata=subprocess.check_output([str(tool),'info',str(wim),'--xml'])
    require(len(metadata)<=8*1024**2,'Oversized WIM metadata')
    editions=[e for e in inspect_editions(metadata) if e['index']==index]
    require(len(editions)==1 and editions[0]['family']=='Windows 7'
            and editions[0]['version']==(6,1,7601),'Inbox preparation currently requires Windows 7 SP1 x64')
    out=session/'keyboard-preparation';out.mkdir(mode=0o700)
    def extract(member,limit):
        data=subprocess.check_output([str(tool),'extract',str(wim),str(index),member,'--to-stdout'])
        require(0<len(data)<=limit,'Unexpected image member size: '+member)
        return data
    source=out/'SOFTWARE.original'
    with source.open('xb') as stream:stream.write(extract(HIVE,256*1024**2))
    prepared=out/'SOFTWARE.prepared'
    report=offline_keyboard.prepare(library,source,prepared)
    overlay=out/'overlay';folder=overlay/'WinDeploy-InboxKeyboard';folder.mkdir(parents=True)
    hashes={}
    for name in FILES:
        data=extract(PACKAGE+'/'+name,8*1024**2)
        original='/Windows/inf/'+name if name.endswith('.inf') else '/Windows/System32/drivers/'+name
        require(data==extract(original,8*1024**2),'Image inbox package differs from original Windows file: '+name)
        with (folder/name).open('xb') as stream:stream.write(data)
        hashes['/WinDeploy-InboxKeyboard/'+name]=hashlib.sha256(data).hexdigest()
    # Keep the original hive outside the OS; the installer session already has
    # root-only backup permissions. No diagnostic scripts are deployed.
    overlay_wim=out/'inbox-keyboard.wim'
    capture=subprocess.run([str(tool),'capture',str(overlay),str(overlay_wim),
        'WinDeploy original inbox keyboard discovery','--compress=none'],
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (out/'capture.log').write_bytes(capture.stdout);capture.check_returncode()
    report.update(files=hashes,overlay_wim_sha256=hashlib.sha256(overlay_wim.read_bytes()).hexdigest(),
                  per_device_binding=False,driver_binaries_modified=False)
    (out/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    return dict(hive=prepared,original_hive=source,wim=overlay_wim,root=overlay,receipt=report)
