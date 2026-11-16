"""Prepare/read back accepted Windows 7 additions; no Setup/OOBE scripts.

Preparation writes new private session files. Application requires an explicit
target-identity guard from the installer and does not format, mount, repartition,
publish EFI, run a Windows program or change security/UAC policy.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import windows7_preparation as keyboard
import community_driver_pack as pack
from community_payload import require,digest


def validate_plan(prepared):
    require(isinstance(prepared,dict) and prepared.get('schema')==1
            and isinstance(prepared.get('model'),str)
            and re.fullmatch(r'[A-Za-z][A-Za-z0-9]*[0-9]+,[0-9]+',prepared['model']),
            'Unrecognised Windows 7 preparation plan')
    k=prepared['keyboard'];r=k['receipt'];drivers=prepared['drivers']
    expected={'/WinDeploy-InboxKeyboard/'+name for name in keyboard.FILES}
    require(set(r['files'])==expected,'Unexpected inbox keyboard destinations')
    hashes=list(r['files'].values())+[r['source_sha256'],r['output_sha256'],r['overlay_wim_sha256']]
    sources=[(k['wim'],r['overlay_wim_sha256']),
             (k['original_hive'],r['source_sha256']),(k['hive'],r['output_sha256'])]
    if drivers is not None:
        require(prepared['model'] in ('MacBookPro11,4','MacBookPro11,5'),
                'Community drivers are restricted to their supported models')
        require(isinstance(drivers,dict) and drivers.get('files'),'Invalid driver delivery plan')
        seen=set()
        for member in drivers['files']:
            require(isinstance(member,str) and member.startswith('/BackToSevenSupport/')
                    and '\\' not in member and ':' not in member
                    and all(part not in ('','.','..') for part in member.split('/')[1:])
                    and member.lower() not in seen,'Unsafe/duplicate driver destination')
            seen.add(member.lower())
        require('/BackToSevenSupport/payload.json' in drivers['files']
                and '/BackToSevenSupport/'+pack.INSTALLER_NAME in drivers['files'],
                'Incomplete driver delivery plan')
        hashes+=list(drivers['files'].values())+[drivers['wim_sha256']]
        sources.append((drivers['wim'],drivers['wim_sha256']))
    require(all(isinstance(h,str) and re.fullmatch('[0-9a-f]{64}',h) for h in hashes),
            'Invalid preparation digest')
    require(len({str(Path(name)) for name,_ in sources})==len(sources),'Aliased prepared sources')
    for name,expected_hash in sources:
        path=Path(name)
        require(path.is_absolute() and path.resolve()==path and path.is_file() and not path.is_symlink()
                and digest(path)==expected_hash,'Prepared source changed: '+str(path))


def prepare(tool,wim,index,session,library,resources,model,copy_drivers):
    require(isinstance(model,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9]*[0-9]+,[0-9]+',model),
            'Invalid keyboard-preparation model')
    require(not copy_drivers or model in ('MacBookPro11,4','MacBookPro11,5'),
            'Community drivers are restricted to their supported models')
    require(type(copy_drivers) is bool,'Community driver choice must be explicit')
    resources=Path(resources);session=Path(session)
    require(resources.is_absolute() and resources.resolve()==resources and resources.is_dir()
            and not resources.is_symlink(),'Invalid verified app resources')
    k=keyboard.stage(tool,wim,index,session,library)
    result=dict(schema=1,model=model,keyboard=k,drivers=None)
    if copy_drivers:
        manifest=json.loads((resources/'community-driver-inventory.json').read_text())
        root=session/'driver-overlay';root.mkdir(mode=0o700)
        folder=root/'BackToSevenSupport'
        staged=pack.stage(manifest,resources/'community-drivers',
            resources/'community-drivers'/pack.INSTALLER_NAME,folder,model,'Windows 7',True)
        wim_path=session/'driver-overlay.wim'
        subprocess.run([str(tool),'capture',str(root),str(wim_path),
            'WinDeploy optional Windows 7 drivers','--compress=none'],
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=True)
        files={'/BackToSevenSupport/'+row['path']:row['sha256'] for row in staged['files']}
        files['/BackToSevenSupport/payload.json']=digest(folder/'payload.json')
        result['drivers']=dict(wim=wim_path,wim_sha256=digest(wim_path),files=files)
    validate_plan(result)
    return result


def apply(prepared,tool,ntfscp,ntfscat,target,assert_target,note):
    require(callable(assert_target),'A confirmed target identity guard is mandatory')
    validate_plan(prepared)
    k=prepared['keyboard'];r=k['receipt'];drivers=prepared['drivers']
    def read(member):
        assert_target()
        return subprocess.check_output([str(ntfscat),str(target),member])
    def write(argv):
        # Session files are private, but recheck them at each write boundary.
        validate_plan(prepared)
        assert_target();subprocess.run([str(x) for x in argv],check=True)
    require(hashlib.sha256(read(keyboard.HIVE)).hexdigest()==r['source_sha256'],
            'Installed SOFTWARE is not the original selected image; refusing replacement')
    system_before=hashlib.sha256(read('/Windows/System32/config/SYSTEM')).hexdigest()
    note('Adding original inbox keyboard discovery files; normal Windows setup is unchanged.')
    write([tool,'apply',k['wim'],'1',target,'--check'])
    for member,expected in r['files'].items():
        require(hashlib.sha256(read(member)).hexdigest()==expected,'Inbox keyboard readback failed: '+member)
    # Recheck baseline after additive copy; ntfscp replaces only the unnamed
    # data stream, retaining the original file's Windows security descriptor.
    require(hashlib.sha256(read(keyboard.HIVE)).hexdigest()==r['source_sha256'],
            'Original hive changed during additive copy')
    write([ntfscp,target,k['hive'],keyboard.HIVE])
    require(hashlib.sha256(read(keyboard.HIVE)).hexdigest()==r['output_sha256'],'Prepared SOFTWARE readback failed')
    if drivers:
        note('Copying the optional community driver installer to the Windows partition root.')
        write([tool,'apply',drivers['wim'],'1',target,'--check'])
        for member,expected in drivers['files'].items():
            require(hashlib.sha256(read(member)).hexdigest()==expected,'Community driver readback failed: '+member)
    require(hashlib.sha256(read('/Windows/System32/config/SYSTEM')).hexdigest()==system_before,
            'SYSTEM hive unexpectedly changed during extras delivery')
    return dict(status='verified',keyboard_preparation=True,community_drivers=bool(drivers),
        system_hive_unchanged=True,setup_hooks_added=False,windows_executables_run=False,
        uac_changed=False,security_enforcement_changed=False,hardware_acceptance_pending=True)
