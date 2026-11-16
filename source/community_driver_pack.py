"""Private driver-pack delivery. Never executes Windows code or installs drivers."""
import json
from pathlib import Path
from community_payload import stage as stage_payload, require, digest

INSTALLER_SHA256='d7633862c3dc64df4fdc7c0f52c0df07bf878c506e243734c4cb53f17c2343c5'
INSTALLER_BYTES=2447538
INSTALLER_NAME='BackToSeven Drivers Installer.exe'
GUIDE_NAME='READ-ME-FIRST.txt'
REPAIR_NAME='Repair graphics boot.cmd'
REPAIR_COMMAND=('@echo off\r\ncd /d "%~dp0"\r\n'
    'start "" /wait "%~dp0BackToSeven Drivers Installer.exe" /REPAIRGRAPHICS=1\r\n')

def recovery_matches(folder,model):
    path=Path(folder)/REPAIR_NAME
    if model!='MacBookPro11,5':return not path.exists()
    return path.is_file() and not path.is_symlink() and path.read_bytes()==REPAIR_COMMAND.encode('ascii')



def stage(manifest,source,installer,destination,model,family,opt_in):
    if not opt_in:return dict(status='skipped',files=[],drivers_executed=False)
    installer=Path(installer);destination=Path(destination)
    require(installer.is_absolute() and installer.resolve()==installer and installer.is_file()
        and not installer.is_symlink(),'Installer source is not a canonical regular file')
    require(installer.stat().st_size==INSTALLER_BYTES and digest(installer)==INSTALLER_SHA256,
        'Driver installer failed pinned integrity check')
    result=stage_payload(manifest,source,destination,model,family,True)
    # stage_payload performs model/OS/path checks and refuses destination reuse.
    data=installer.read_bytes()
    import hashlib
    require(len(data)==INSTALLER_BYTES and hashlib.sha256(data).hexdigest()==INSTALLER_SHA256,
        'Driver installer source changed during staging')
    with (destination/INSTALLER_NAME).open('xb') as out:out.write(data)
    require(digest(destination/INSTALLER_NAME)==INSTALLER_SHA256,'Installer copy verification failed')
    guide=(
        'BackToSeven Windows 7 drivers\r\n\r\n'
        'For Windows 7 SP1 x64 on '+model+'.\r\n'
        'Once normal Windows setup finishes, open '+INSTALLER_NAME+'.\r\n'
        'Approve the normal Windows administrator/UAC prompt, then click Install in the setup wizard.\r\n'
        'Setup automatically handles all applicable components; there is no driver-selection checklist.\r\n'
        'No Internet connection, Python download or PowerShell upgrade is required.\r\n'
        'Driver files are checked before installation. Windows signature enforcement stays on.\r\n'
        'No remote-access agent is installed; UAC/security settings are not disabled.\r\n'
        'Use Restart now whenever it is offered. A 4096-byte-sector drive may require an extra restart\r\n'
        'to prepare Windows signing services before its signing-support update. Setup resumes after sign-in\r\n'
        'with normal UAC approval; click Install to continue. If it does not reopen, run this EXE again.\r\n'
        'Already installed matching drivers are verified, not forcibly downgraded. Results and failures\r\n'
        'are saved per component. An AMD restart request does not skip the remaining driver stages.\r\n'
        'The Boot Camp control panel/utility starts normally after your next Windows sign-in.\r\n'
        'Two-finger scrolling is enabled by the installed preferences/startup helper. Use the\r\n'
        'Boot Camp Trackpad tab to choose tapping, dragging and secondary-click settings.\r\n'
        'This EXE and a fresh full driver installation still require hardware acceptance testing.\r\n'
        'Deferred on both models: internal camera, sleep/resume repair and dock Ethernet.\r\n'
        'Sleep can leave input/display or other devices unresponsive; a restart may be needed.\r\n'
        'Connect a Thunderbolt dock before booting Windows; reconnecting may require a restart.\r\n'
        'Bluetooth device discovery was tested; pairing and Bluetooth audio were not tested.\r\n'
        'Normal UAC and driver-signature enforcement remain enabled. No Test Mode is used.\r\n'
        + ('The 11,4 receives its accepted Intel Iris and Intel HDMI-audio drivers, not AMD/M370X\r\n'
           'or the 11,5 graphics EFI helper.\r\n' if model=='MacBookPro11,4' else
           'The 11,5 receives AMD/M370X and AMD HDMI audio. Its installer checks its recorded\r\n'
           'graphics boot route without replacing OpenCore or another installation.\r\n'
           'If acceleration later fails with a warning on the AMD device, run Repair graphics boot.cmd\r\n'
           'from this folder to check the known boot-route issue without reinstalling drivers.\r\n')
        + '\r\nThis is not a public redistribution bundle or a claim of release readiness.\r\n')
    with (destination/GUIDE_NAME).open('x',newline='') as out:out.write(guide)
    result['files'].extend([dict(path=INSTALLER_NAME,bytes=INSTALLER_BYTES,sha256=INSTALLER_SHA256),
        dict(path=GUIDE_NAME,bytes=(destination/GUIDE_NAME).stat().st_size,sha256=digest(destination/GUIDE_NAME))])
    if model=='MacBookPro11,5':
        with (destination/REPAIR_NAME).open('xb') as out:out.write(REPAIR_COMMAND.encode('ascii'))
        result['files'].append(dict(path=REPAIR_NAME,bytes=(destination/REPAIR_NAME).stat().st_size,sha256=digest(destination/REPAIR_NAME)))
    result.update(status='private-driver-pack-staged',installer_complete=True,
        installer_hardware_validated=False,ready_for_release=False)
    (destination/'payload.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
