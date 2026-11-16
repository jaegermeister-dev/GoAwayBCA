"""Offline model/Windows evidence; actual CPU and firmware checks are separate.

Unknown, variant-dependent and explicitly unsupported are different results.
Only narrow, evidenced warnings are enforced here. No matrix entry can bypass
the disk-layout, firmware, image or actual-CPU checks elsewhere.
"""
import json
from pathlib import Path
import re

DATABASE=Path(__file__).with_name('mac_windows_compatibility.json')
FAMILIES=('Windows 7','Windows 8','Windows 8.1','Windows 10','Windows 11')
MODEL=re.compile(r'(?:MacBookPro|MacBookAir|MacBook|Macmini|MacPro|iMacPro|iMac|Xserve)\d+,\d+$')
OLDER_VERSION_POLICY=('Support is offered for Apple-supported Windows versions and newer versions, '
    'plus community Windows 7 support on MacBookPro11,4 and 11,5. Getting an older-than-Apple-supported '
    'Windows version working on other Macs is outside this project\'s support scope.')

def load(path=DATABASE):
    raw=Path(path).read_bytes()
    if len(raw)>4*1024*1024:raise ValueError('Compatibility database is too large')
    data=json.loads(raw.decode('utf-8'))
    if data.get('schema')!=1 or not isinstance(data.get('models'),dict) or not isinstance(data.get('sources'),dict):
        raise ValueError('Compatibility database is not valid')
    return data

def confirm_model(interaction,detected):
    while True:
        model=interaction.request('driver_model',
            'Confirm the Mac model identifier for this installation. Make sure the identifier is accurate '
            'for the Mac that will run Windows. The detected identifier is filled in; correct it if '
            'firmware spoofing has changed it. CPU and EFI architecture are checked from the actual hardware.',model=detected)
        if isinstance(model,str) and MODEL.fullmatch(model.strip()):return model.strip()
        if interaction.choose('Enter a full Mac model identifier, for example MacBookPro11,5.',
                              ['Enter the identifier again','Cancel'])!=0:
            raise ValueError('Cancelled before installation preparation')

def assess(model,edition,database=None,variant=None):
    db=database if database is not None else load()
    family=edition.get('family');arch=edition.get('architecture')
    if family not in FAMILIES:raise ValueError('Unsupported Windows family for compatibility lookup')
    bits={0:32,9:64}.get(arch) if type(arch) is int else None
    record=db['models'].get(model)
    result=dict(model=model,family=family,image_bits=bits,database_revision=db['revision'],
        official_status='unknown',official_architectures=[],variant_details=[],source_urls=[],
        apple_boot_camp_mode='unknown',project_test=None,restore_policy=None,known_supported_families=[],
        definitive_official_minimum=None,definitive_official_maximum=None,warnings=[])
    if record is None:
        result['warnings'].append(dict(code='unknown-model-evidence',text=
            'The local compatibility records do not contain '+model+'. Check the confirmed model identifier. '
            'Apple driver support and the historical Windows boot method could not be established for '
            'this selection. You may try pure EFI, but Windows or some hardware may not work. '+OLDER_VERSION_POLICY))
        return result
    os_record=record['windows'][family]
    variants=os_record['variants']
    if variant is not None:
        variants=[v for v in variants if v['name']==variant]
        if not variants:raise ValueError('Marketing variant is not in this model record')
    observations=[e for v in variants for e in v['evidence']]
    result.update(official_architectures=sorted({b for e in observations for b in e['architectures']}),
        variant_details=variants,project_test=record['tested'].get(family),
        restore_policy=record['winclone_restore_policy'],
        known_supported_families=[f for f in FAMILIES if any(
            e['status']=='supported' for v in record['windows'][f]['variants'] for e in v['evidence'])])
    statuses=[]
    for evidence in observations:
        status=evidence['status']
        if status=='supported' and bits is not None and bits not in evidence['architectures']:
            status='architecture-not-supported'
        statuses.append(status)
    unique=set(statuses)
    if len(unique)==1:result['official_status']=next(iter(unique))
    elif unique:result['official_status']='variant-dependent'
    ids=set(os_record['sources'])
    tested=result['project_test'] is not None
    # Missing table coverage does not prove that Apple explicitly refused a
    # combination. Still inform users before proceeding without that evidence.
    # The tested 2015 Windows 7 exception has its own community-driver dialog.
    if not tested and result['official_status'] in ('not-listed','not-supported'):
        if result['official_status']=='not-supported':
            reason='Apple\'s saved compatibility table explicitly marks '+family+' as unsupported on '+model+'.'
        else:
            reason='Apple\'s saved '+family+' compatibility table does not list a matching variant for '+model+'. This is not proof that it cannot run.'
        result['warnings'].append(dict(code='apple-support-unconfirmed',text=reason+
            ' Matching official drivers may be unavailable or may refuse to install. You may continue, '
            'but Windows or some hardware may not work. '+OLDER_VERSION_POLICY))
    if result['official_status']=='architecture-not-supported':
        available=', '.join(str(b)+'-bit' for b in result['official_architectures'])
        result['warnings'].append(dict(code='official-driver-architecture',text=
            'Apple lists '+family+' support for '+model+' in '+available+' form, not the selected '+str(bits)+
            '-bit form. A 64-bit CPU alone does not establish matching Apple drivers. Driver installers '
            'may refuse this Windows architecture or leave hardware without drivers. You may try anyway '
            'if the separate CPU and EFI checks pass. You may need to find compatible drivers yourself '
            'or choose another Windows version. An Apple-supported legacy installation may require a '
            'different installation method; this app never uses BIOS boot or hybrid MBR.'))
    # The selected image architecture can resolve the shared-ID distinction:
    # 10,1 x64 Win7 is supported in both years; x86 differs between them.
    if result['official_status']=='variant-dependent':
        result['warnings'].append(dict(code='variant-dependent',text=
            'Apple support for '+family+' on '+model+' differs between marketing variants sharing this '
            'identifier, or our records do not cover every variant. The identifier alone cannot confirm '
            'driver support for the selected architecture. You may continue, but review the model details '
            'and be prepared for missing drivers.'))
    policy=record['winclone_restore_policy']
    tested_win7 = family=='Windows 7' and model in ('MacBookPro11,3','MacBookPro11,4','MacBookPro11,5')
    if not tested and not tested_win7 and (policy['listed_legacy'] or family=='Windows 7'):
        if policy['listed_legacy']:
            reason='The Winclone developer documents legacy Windows restoration for this Mac model.'
            ids.add(policy['source'])
        else:
            reason='The Winclone developer recommends legacy boot for Windows 7; that is not a model-specific Apple installation-mode guarantee.'
            ids.add(policy['windows7_source'])
        if family=='Windows 7':
            text=('Windows 7 EFI compatibility\n\n'
                'GoAwayBCA installs Windows 7 using pure EFI on a GPT disk. This configuration has not been tested by us on your Mac model.\n\n'
                'Boot behaviour and driver compatibility may differ from a traditional Boot Camp installation. Official Apple drivers being available does not guarantee that every device will work under pure EFI.\n\n'
                'You may continue. If you encounter problems, please submit a GitHub issue with your GoAwayBCA diagnostic report.')
        else:text=(reason+
            ' This app uses pure EFI. Apple driver availability does not prove those drivers will behave '
            'the same under EFI, so boot or hardware problems are possible. You may try pure EFI, but '
            'this app will not create a hybrid MBR or alter a hybrid-MBR installation.')
        result['warnings'].append(dict(code='historical-legacy-path',text=text))
    supported=result['known_supported_families']
    if supported:
        maximum=max(supported,key=FAMILIES.index)
        if FAMILIES.index(family)>FAMILIES.index(maximum):
            text=('The latest Windows version listed as officially supported by Apple for '+model+
                ' in our compatibility records is '+maximum+'. You selected '+family+'.\n\n'
                'Use the Boot Camp drivers cautiously. The main Boot Camp Setup.exe installs the whole '
                'package and may replace working drivers with older drivers that are unsuitable for '
                'this Windows version, causing instability or startup failures.')
            if FAMILIES.index(family)>=FAMILIES.index('Windows 8'):
                text+=('\n\nConnect Windows to the internet and let Windows Update find and install '
                    'drivers first. Then check Device Manager for devices still missing drivers. '
                    'Install only those missing drivers, one at a time, from the downloaded driver '
                    'folder using each driver\'s own installer, rather than the main Boot Camp Setup.exe. '
                    'If Wi-Fi is not working, install only its driver first or use a working Ethernet connection.')
            result['warnings'].append(dict(code='newer-windows-driver-guidance',text=text))
    result['source_urls']=sorted({db['sources'][key]['url'] for key in ids})
    return result

def configure(model,edition,interaction,database=None):
    result=assess(model,edition,database)
    from older_windows_policy import configure as configure_older
    result['older_windows_driver_choice']=configure_older(model,edition,interaction,database if database is not None else load())
    if result['warnings']:
        message='\n\n'.join(w['text'] for w in result['warnings'])
        answer=interaction.approve('compatibility_review',message)
        if answer is not True:
            raise ValueError('Cancelled after Mac/Windows compatibility guidance; no partition was formatted')
    result['acknowledged_warning_codes']=[w['code'] for w in result['warnings']]
    return result
