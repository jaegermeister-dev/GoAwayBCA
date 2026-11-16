"""Read-only report data. No patches, boot selection or guessed target identity."""
import datetime
import hashlib
import json
import re
import struct
from pathlib import Path
from boot_catalog import identity
from installation_records import validate_record,make_marker
from windows_discovery import loader_version,version_label

SCHEMA=1
PREFIX='EFI/WinDeploy/Diagnostics/'

def require(ok,text):
    if not ok:raise ValueError(text)

def timestamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def eligible(row):
    """Only present Windows with paired creator records, not a menu label."""
    try:
        record=validate_record(row['record']);seen=row['observed']
        return (seen.get('windows_verified') is True and seen.get('filesystem')=='ntfs'
            and row['marker']==make_marker(record) and identity(record)==identity(seen)
            and record['partition_bytes']==seen['partition_bytes']
            and record['partition_offset']==seen['partition_offset'])
    except (ValueError,KeyError,TypeError):return False

def clean(value,key=''):
    """Discard personal/network/unique machine IDs, not selected partition GUIDs.

    Applied to hardware inventory only. Target disk/partition identities are a
    separate explicit part of the report and are necessary for later targeting.
    """
    lowered=key.casefold().replace('_','').replace('-','').replace(' ','')
    if any(k in lowered for k in ('serial','uuid','ssid','bssid','macaddress','ipaddress',
            'email','username','password','credential','productkey','licensekey','hostname',
            'computername','localhostname','networkname','networksignature','networks','networkinformation','locationid',
            'manufacturerdata','bluetoothaddress')):return '[omitted for privacy]'
    if type(value) is dict:return {str(k):clean(v,str(k)) for k,v in value.items()}
    if type(value) in (tuple,list):return [clean(v,key) for v in value[:4096]]
    if type(value) is bytes:return dict(bytes=len(value),sha256=hashlib.sha256(value).hexdigest())
    if type(value) is str:
        value=re.sub(r'/Users/[^/\s]+','/Users/[user]',value)
        value=re.sub(r'(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}','[network address omitted]',value)
        value=re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b','[IPv4 address omitted]',value)
        return value[:8192]
    if value is None or type(value) in (int,float,bool):return value
    return str(type(value).__name__)

def windows_evidence(loader,software=None):
    version=loader_version(loader);architecture='unknown'
    try:
        pe=struct.unpack_from('<I',loader,60)[0]
        require(loader[:2]==b'MZ' and loader[pe:pe+4]==b'PE\0\0','Invalid PE')
        architecture={0x8664:'x86_64',0x14c:'x86',0xaa64:'arm64'}.get(struct.unpack_from('<H',loader,pe+4)[0],'unknown')
    except (ValueError,struct.error):pass
    return dict(loader_path='/Windows/System32/winload.efi',loader_sha256=hashlib.sha256(loader).hexdigest(),
        loader_version=version,family_label=version_label(version),architecture=architecture,
        installed_registry=software or dict(status='unavailable'),
        source='Read-only installed loader; selected offline SOFTWARE values where available. Menu labels are not OS evidence.')

def target_report(row,gpt,entry,windows,app_version,session_id,model):
    require(eligible(row),'Only current WinDeploy installations can be reported')
    require(re.fullmatch('[0-9a-f]{32}',session_id),'Invalid diagnostic session ID')
    record=row['record'];conflicts=[]
    require(record['disk_guid']==gpt['guid'] and record['esp_guid']==row['esp_guid'],
        'Report disk/EFI identity differs from the selected installation')
    if entry is None:conflicts.append('No unique menu entry matches this installation')
    elif identity(entry)!=identity(row['observed']):raise ValueError('Menu targets a different installation')
    label=windows['family_label']
    if windows['loader_version'] is None:conflicts.append('Installed Windows loader version unavailable')
    elif not label.startswith(record['family']+' '):conflicts.append('Installed loader version differs from the creation record')
    installed=windows['installed_registry'];build=installed.get('CurrentBuildNumber')
    if build and windows['loader_version'] and build!=str(windows['loader_version'][2]):
        conflicts.append('Installed registry build differs from loader build')
    return dict(schema=SCHEMA,session_id=session_id,created_utc=timestamp(),collector_version='1',app_version=app_version,
        model=dict(value=model,source='macOS sysctl hw.model',original_hardware_identity='not independently established; OpenCore spoofing may affect this value'),
        selected_installation=dict(row['observed'],installation_uuid=record['installation_uuid']),
        windows=windows,menu_entry=entry,creation_record=record,
        disk_layout=dict(disk_guid=gpt['guid'],digest=gpt['digest'],partitions=gpt['partitions'],logical_sector_bytes=gpt.get('sector_size',512),layout_source=gpt.get('source','offline GPT'),hybrid_mbr_checked=False),
        esp_guid=row['esp_guid'],current_device_names=dict(windows=row['target']['partition']['DeviceIdentifier'],esp=row['target']['esp']),
        boot_mapping_status='incomplete' if conflicts else 'identity-records-match; boot stores must still be checked',
        conflicts=conflicts,hardware_fix_applied=False,automatic_upload=False,
        limitations=['Reports cannot certify driver functionality. EFI cannot show Windows driver bindings.',
          'Future test EFIs must revalidate live GUIDs, Windows version and boot-store identity before any change.',
          'No fans, camera, microphone, network traffic or sleep tests are performed.'])

def context_bytes(report):
    """Bounded printable context carried verbatim into each EFI capture."""
    target=report['selected_installation'];model=report['model']['value']
    values=[('session_id',report['session_id']),('installation_uuid',target['installation_uuid']),
        ('app_version',report['app_version']),('collector_version',report['collector_version']),
        ('windows_disk_guid',target['disk_guid']),('windows_partition_guid',target['partition_guid']),
        ('esp_guid',report['esp_guid']),('macos_model',model),('windows_version',report['windows']['family_label']),
        ('created_utc',report['created_utc'])]
    for key,value in values:require(type(value) is str and re.fullmatch(r'[ -~]{1,180}',value),'Invalid diagnostic context '+key)
    return ('WinDeploy read-only hardware report v1\n'+''.join(k+'='+v+'\n' for k,v in values)+
        'identity_source=Inherited from macOS report; not independently revalidated by EFI\n').encode('ascii')

def summary(report):
    target=report['selected_installation'];windows=report['windows']
    return ('WinDeploy hardware report — review before sharing\n\nSession: '+report['session_id']+
        '\nMac: '+report['model']['value']+' (macOS-reported; spoofing may affect it)'+
        '\nWindows: '+windows['family_label']+' / '+windows['architecture']+
        '\nWindows partition GUID: '+target['partition_guid']+'\nDisk GUID: '+target['disk_guid']+
        '\nEFI partition GUID: '+report['esp_guid']+'\nMapping: '+report['boot_mapping_status']+
        '\n\nConflicts: '+('; '.join(report['conflicts']) or 'none identified in available evidence')+
        '\n\nNo fixes applied. No automatic upload. Personal files, keys and full registry hives are not exported. '+
        'Review all files yourself before sharing. Missing evidence is not a successful hardware test.\n')
