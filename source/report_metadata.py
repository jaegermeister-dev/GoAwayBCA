"""Bounded provenance for reports; observations never imply earlier boot history."""
import datetime
import json
from pathlib import Path
import plistlib
import re
import subprocess

MULTI_WINDOWS = "If you plan to install multiple Windows versions, we recommend completing one installation before starting the next. Boot into Windows, finish setup, install its drivers and complete required restarts. Windows normally updates its boot files and settings during setup, so waiting lets that installation's boot configuration settle before another version is added. This may not be necessary in every case, but it is the recommended approach."
ISO_GUIDANCE = 'We recommend an official, unmodified Windows ISO and checking its hash yourself against a trusted reference. GoAwayBCA does not enforce this recommendation.'
LINUX_GUIDANCE = "Linux coexistence has not been tested, whether Linux was installed before, between or after Windows installations. GRUB may ignore our Windows menu, offer the whole menu, or detect individual Windows entries; any of these routes may or may not boot. Installing Linux or changing its bootloader later can also change boot files or firmware entries. OpenCore and the Mac's built-in Option/Alt picker may behave differently: either, both or neither OS may appear or boot. Try both pickers where available. If an entry is missing, OpenShell can be used to try launching the appropriate existing EFI loader directly; this is a possible workaround, not a guaranteed repair. Our menu lists Windows only and leaves other OS entries alone. Back up before changing a multi-OS boot configuration. Please report successes and failures with your Linux distribution/version, bootloader/version if known, installation method and order, disk layout, Mac model, Windows versions, GoAwayBCA version, OpenCore version if used, entries visible in each picker, and what actually booted. Include a GoAwayBCA diagnostic report. Linux testing is deferred beyond Beta 1."
BOOT_HELP = ('For a boot problem, try both OpenCore and the Mac\'s Option-key boot picker where available. '
    'You do not need to install OpenCore just to submit a report. Please report both outcomes, even if '
    'one works. Use Collect Hardware Report and include the exact failure stage, relevant peripherals '
    'and any installation history not recorded by the app. OpenCore and OCLP are different versions. '
    'Unknown details are fine. Never include passwords or product keys.')

def small_text(value):
    if isinstance(value,bytes):
        try:value=value.decode('utf-8')
        except UnicodeError:return None
    if isinstance(value,str):
        value=value.strip('\0 \r\n')
        if re.fullmatch(r'[A-Za-z0-9_.,+ ()/;-]{1,160}',value):return value
    return None

def plist_file(path):
    try:
        path=Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size>1024**2:return {}
        data=plistlib.loads(path.read_bytes())
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError,plistlib.InvalidFileException):return {}

def host(resources,system='/System/Library/CoreServices/SystemVersion.plist'):
    info=plist_file(Path(resources).parent/'Info.plist');os_info=plist_file(system)
    return dict(observed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        app=dict(name='GoAwayBCA',version=small_text(info.get('CFBundleShortVersionString')) or 'unknown',
                 build=small_text(info.get('CFBundleVersion')) or 'unknown'),
        macos={key:small_text(os_info.get(key)) or 'unknown' for key in ('ProductName','ProductVersion','ProductBuildVersion')},
        source='Current app Info.plist and currently booted macOS SystemVersion.plist')

def deployment(resources,edition,model,disk_guid,partition_guid,keyboard_preparation=None):
    import partition_detection
    return dict(schema=1,host=host(resources),keyboard_preparation=keyboard_preparation,partition_detection=partition_detection.report(),confirmed_model=model,disk_guid=disk_guid,partition_guid=partition_guid,
        image={key:edition[key] for key in ('family','name','architecture','arch','version','build','index') if key in edition},
        scope='Recorded before this deployment; no claim about subsequent boot routes or working drivers')

def original_deployment(raw,disk_guid,partition_guid):
    if raw is None:return dict(status='unavailable',reason='This installation has no saved deployment metadata; current macOS is not its original deployment host.')
    try:
        if len(raw)>32768:raise ValueError('oversized')
        data=json.loads(raw.decode('utf-8'))
        if data.get('schema')!=1 or data.get('disk_guid')!=disk_guid or data.get('partition_guid')!=partition_guid:raise ValueError('identity mismatch')
        saved_host=data.get('host',{})
        if not isinstance(saved_host,dict):raise ValueError('invalid host')
        saved={}
        for section,keys in (('app',('name','version','build')),('macos',('ProductName','ProductVersion','ProductBuildVersion'))):
            row=saved_host.get(section,{})
            if not isinstance(row,dict):raise ValueError('invalid host section')
            saved[section]={key:small_text(row.get(key)) or 'unknown' for key in keys}
        image=data.get('image',{})
        if not isinstance(image,dict):raise ValueError('invalid image')
        image={key:(value if type(value) is int else small_text(value)) for key,value in image.items()
            if key in ('family','name','architecture','arch','build','index')}
        return dict(status='recorded',source='Windows partition /WinDeploy/deployment.json; informational, not boot ownership proof',
            host=saved,confirmed_model=small_text(data.get('confirmed_model')),
            image=image,keyboard_preparation=(data.get('keyboard_preparation')
                if type(data.get('keyboard_preparation')) is bool else None),
            scope='Original saved deployment metadata; not current boot evidence')
    except (ValueError,TypeError,AttributeError,UnicodeError):return dict(status='unavailable',reason='Saved deployment metadata is invalid or targets another partition.')

def nvram_version():
    try:
        p=subprocess.run(['/usr/sbin/nvram','-x','opencore-version'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
        if p.returncode or len(p.stdout)>16384:return dict(status='unavailable')
        data=plistlib.loads(p.stdout)
        if not isinstance(data,dict):return dict(status='unavailable')
        return dict(status='observed',data={k:small_text(v) for k,v in data.items() if k.casefold().endswith('opencore-version')})
    except (OSError,ValueError,subprocess.SubprocessError):return dict(status='unavailable')

def boot_environment(mount,version_probe):
    """Only exact version fields; no complete NVRAM/config export or serials."""
    observed=version_probe
    version=None
    if isinstance(observed,dict) and observed.get('status')=='observed':
        data=observed.get('data')
        if isinstance(data,dict):
            for key,value in data.items():
                if key.casefold().endswith('opencore-version'):version=small_text(value)
    config=plist_file(Path(mount)/'EFI/OC/config.plist')
    revision=config.get('#Revision',{})
    if not isinstance(revision,dict):revision={}
    oclp=small_text(revision.get('OpenCore-Legacy-Patcher-Version')) or small_text(revision.get('OCLP-Version'))
    return dict(opencore=dict(version=version or 'unknown',source='Current boot NVRAM opencore-version, if exposed',
        limitation='NVRAM may retain earlier values; does not establish a previous failed boot version'),
        oclp=dict(version=oclp or 'unknown',source='Selected EFI config.plist revision metadata, if recorded',
        limitation='Selected EFI is not necessarily the currently booted OpenCore'),
        previous_boot_route='unknown; user must describe the route and outcomes')
