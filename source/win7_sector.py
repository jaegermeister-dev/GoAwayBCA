"""Private, exact-image Windows 7 4Kn boot preparation; no disk writes.

The installation engine must verify this plan before formatting and bind the
route to its paired installation records. 512-byte destinations use no wrapper.
The packaged helper was boot-tested through the production menu in a 4Kn VM.
"""
import hashlib
from pathlib import Path
import subprocess
from boot_catalog import guid

KERNEL_SHA='7fba129db114e8808a2ee5ae597f66176b7eb3c4077e0b5e9a3be3f74aa2e6a6'
HELPER_SHA='6eb2afdf99e2b7f4d045ac585e7417d260dd93198ce7f8eee365cde13a7e052b'
PROFILE='win7-sp1-x64-7601-hive-sector-sha2-v2'
LEGACY_PROFILE=dict(schema=1,profile='win7-sp1-x64-7601-4ce7951a-hive-sector-v1',
    logical_sector_bytes=4096,kernel_sha256=KERNEL_SHA,
    helper_sha256='9561d32c599637021d19f766dbe5d91ee699454fe3bb845712a2e2a5ac14a21b')

def sha(data):return hashlib.sha256(data).hexdigest()

def require(ok,message):
    if not ok:raise ValueError(message)

def validate_profile(value):
    current=dict(schema=1,profile=PROFILE,logical_sector_bytes=4096,
        kernel_sha256=KERNEL_SHA,helper_sha256=HELPER_SHA)
    require(type(value) is dict and value in (current,LEGACY_PROFILE),
        'Windows 7 4Kn preparation is missing or differs from the tested profile.')
    return value

def prepare(tool,wim,index,resources):
    require(type(index) is int and 1<=index<=100,'Invalid selected Windows image index')
    kernel=subprocess.check_output([str(tool),'extract',str(wim),str(index),
        '/Windows/System32/ntoskrnl.exe','--to-stdout'])
    require(0<len(kernel)<=16*1024**2 and sha(kernel)==KERNEL_SHA,
        'This Windows 7 kernel is not covered by the current 4096-byte-sector compatibility profile. '
        'Nothing has been formatted. Use the supported original Windows 7 SP1 image for this test.')
    helper=(Path(resources)/'efi/Win7SectorBoot.efi').read_bytes()
    require(sha(helper)==HELPER_SHA,'Windows 7 sector helper failed verification')
    return validate_profile(dict(schema=1,profile=PROFILE,logical_sector_bytes=4096,
        kernel_sha256=sha(kernel),helper_sha256=sha(helper)))

def prepare_target(owner,target,edition,wim,resources,model):
    from disk_layout import sector_bytes
    if edition['family']!='Windows 7' or sector_bytes(target['disk'])!=4096:return None
    if model not in ('MacBookPro11,4','MacBookPro11,5'):
        choice=owner.choose(
            'The selected drive reports 4096-byte logical sectors (4Kn). Windows 7 normally '
            'cannot boot from this sector size. This experimental compatibility helper adjusts '
            'a specific Windows 7 boot-time sector check. It has been tested in an isolated VM, '
            'but is not confirmed on this Mac. This is separate from NVMe drivers and graphics fixes. '
            'It does not disable driver-signature enforcement. A different or updated Windows '
            'kernel may need an updated helper. Sleep and hibernation are not validated.',
            ['Cancel and use a 512-byte-sector drive or a newer Windows version',
             'Try the experimental Windows 7 4Kn compatibility helper'])
        require(choice==1,'Windows 7 4Kn compatibility was not selected. Nothing was formatted.')
    owner.note('Preparing Windows 7 compatibility for the selected drive\'s 4096-byte logical sectors. '
        'Normal driver-signature enforcement is retained. Kernel updates and hibernation are not validated.')
    return prepare(owner.wim,wim,edition['index'],resources)

def paths(partition):
    folder='EFI/WinDeploy/Installs/'+guid(partition)+'/'
    return folder+'SectorBoot.efi',folder+'SectorNext.efi'

def wrap(files,partition,loader,profile,helper):
    validate_profile(profile)
    require(sha(helper)==HELPER_SHA,'Unexpected sector helper')
    wrapper,next_path=paths(partition)
    require(wrapper not in files and next_path not in files and loader in files,
        'Sector route conflicts with existing staged boot files')
    # Preserve relative sidecars for the generic EFI option by keeping the
    # wrapper and its renamed next component in that same private directory.
    prefix=wrapper.rsplit('/',1)[0]+'/'
    require(loader.startswith(prefix) or loader=='EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi',
        'Unexpected Windows 7 graphics route')
    next_data=files[loader]
    output=dict(files,**{wrapper:helper,next_path:next_data})
    route=dict(profile,loader_path=wrapper,next_path=next_path,
        next_sha256=sha(next_data),original_loader_path=loader)
    return output,wrapper,route

def recorded_route(record,files):
    """Caller first proves the Windows marker/EFI receipt pair and GPT identity."""
    route=record.get('sector_compatibility')
    require(type(route) is dict,'Missing sector compatibility record')
    profile=validate_profile({k:route.get(k) for k in ('schema','profile','logical_sector_bytes','kernel_sha256','helper_sha256')})
    wrapper,next_path=paths(record['partition_guid'])
    require(route.get('loader_path')==wrapper and route.get('next_path')==next_path,
        'Sector helper route belongs to another partition')
    owned={a['path']:a for a in record['efi_assets']}
    result={}
    for name,expected in ((wrapper,profile['helper_sha256']),(next_path,route.get('next_sha256'))):
        asset=owned.get(name)
        require(asset is not None and asset.get('partition_guid')==record['esp_guid']
            and asset.get('exclusive') is True and asset.get('sha256')==expected,
            'Sector route is not owned by its paired installation record')
        require(name in files and sha(files[name])==expected,'Recorded sector boot component is missing or changed: '+name)
        result[name]=files[name]
    return wrapper,result
