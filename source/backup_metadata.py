"""Experimental backup provenance and explicit source/destination comparison.

Original installation location is history, never a substitute for the latest
backup source. Metadata alone never authorizes uninstalling an installation.
"""
import copy
import datetime
import hashlib
import json
import uuid

SCHEMA=1
FIELDS=(
    ('serial','Drive serial number'),('disk_guid','GPT disk identifier'),('model','Drive model'),
    ('logical_sector_size','Logical sector size'),('physical_sector_size','Physical sector size'),
    ('connection','Connection'),('controller','Storage connection/controller'),('adapter','USB adapter'),
    ('connection_path','Connection path'),
    ('bytes','Drive capacity'),('partition_bytes','Partition capacity'),
    ('partition_guid','Partition identifier'),('partition_offset','Partition offset'),
    ('layout','Partition layout'))


def require(condition,message):
    if not condition:raise ValueError(message)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')


def validate_location(location):
    require(type(location) is dict,'Missing backup drive metadata')
    for key in ('disk_guid','partition_guid'):
        require(str(uuid.UUID(location.get(key,'')))==location[key] and uuid.UUID(location[key]).int,
                'Invalid backup '+key)
    require(type(location.get('logical_sector_size')) is int and location['logical_sector_size'] in (512,4096),
            'Unknown backup logical sector size')
    require(location.get('physical_sector_size') is None or
            (type(location['physical_sector_size']) is int and location['physical_sector_size'] in (512,4096)),
            'Unknown physical sector size must be recorded as unavailable')
    require(location.get('connection') in ('Internal','USB'),'Unknown backup connection')
    for key in ('bytes','partition_bytes','partition_offset'):
        require(type(location.get(key)) is int and location[key]>0 and location[key]%location['logical_sector_size']==0,
                'Invalid backup geometry: '+key)
    require(location['partition_offset']+location['partition_bytes']<=location['bytes'],
            'Backup partition extends outside its source drive')
    for key in ('serial','model','controller','adapter','connection_path'):
        require(location.get(key) is None or (type(location[key]) is str and
            len(location[key])<=1024 and not any(ord(c)<32 for c in location[key])), 'Invalid drive metadata: '+key)
    require(type(location.get('layout')) is list and len(location['layout'])<=4096,'Missing backup partition layout')
    return location


def validate(metadata):
    require(type(metadata) is dict and metadata.get('schema')==SCHEMA,'Unsupported backup metadata version')
    require(str(uuid.UUID(metadata.get('backup_id','')))==metadata['backup_id'],'Invalid backup identifier')
    validate_location(metadata['source'])
    origin=metadata['origin']
    require(type(origin) is dict and origin.get('kind') in ('app','external-or-unverified'), 'Invalid installation origin')
    validate_location(origin['location'])
    require(type(metadata.get('uninstall_eligible')) is bool,'Missing uninstall provenance')
    require(not metadata['uninstall_eligible'] or origin['kind']=='app','External installation cannot acquire uninstall permission')
    require(type(metadata.get('history')) is list and len(metadata['history'])<=10000,'Invalid backup history')
    require(type(metadata.get('windows')) is dict and metadata['windows'].get('family') in
        ('Windows 7','Windows 8','Windows 8.1','Windows 10','Windows 11'),'Unsupported backup Windows version')
    require(metadata['windows'].get('architecture') in (0,9),'Unknown backup architecture')
    return metadata


def create(source,windows,app_origin_verified,previous=None,now=None):
    validate_location(source)
    require(type(app_origin_verified) is bool,'Fresh ownership verification is required')
    moment=now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    if previous is not None:
        validate(previous)
        origin=copy.deepcopy(previous['origin'])
        history=copy.deepcopy(previous['history'])
        # Repeated capture/restore cannot promote a foreign or unverified origin.
        eligible=app_origin_verified and previous['uninstall_eligible'] and origin['kind']=='app'
        parent=previous['backup_id']
    else:
        origin=dict(kind='app' if app_origin_verified else 'external-or-unverified',location=copy.deepcopy(source))
        history=[];eligible=app_origin_verified;parent=None
    identifier=str(uuid.uuid4())
    history.append(dict(event='backup',id=identifier,time=moment,source=copy.deepcopy(source),parent_backup=parent))
    result=dict(schema=SCHEMA,backup_id=identifier,created_at=moment,source=copy.deepcopy(source),
        windows=copy.deepcopy(windows),origin=origin,uninstall_eligible=eligible,history=history,
        experimental=True)
    validate(result)
    return result


def restored_history(metadata,destination,now=None):
    validate(metadata);validate_location(destination)
    result=copy.deepcopy(metadata)
    result['history'].append(dict(event='restore',from_backup=metadata['backup_id'],
        time=now or datetime.datetime.now(datetime.timezone.utc).isoformat(),destination=copy.deepcopy(destination)))
    # source remains the drive actually captured in this backup. A NEW backup
    # calls create with freshly observed destination metadata, replacing source.
    return result


def compare(metadata,destination):
    validate(metadata);validate_location(destination)
    source=metadata['source'];changes=[]
    for key,label in FIELDS:
        old,new=source.get(key),destination.get(key)
        status='unknown' if old is None or new is None else 'same' if old==new else 'different'
        changes.append(dict(field=key,label=label,source=old,destination=new,status=status))
    # GPT identifiers can be cloned. Without an actual serial do not claim to
    # have proved that two snapshots are the exact same physical device.
    if (source.get('serial') and destination.get('serial') and
            source.get('serial_kind')=='physical-drive' and destination.get('serial_kind')=='physical-drive'):
        same_drive=(source['serial']==destination['serial'] and source.get('model')==destination.get('model'))
    elif source.get('serial') and destination.get('serial') and source['serial']!=destination['serial']:
        same_drive=False
    elif source['disk_guid']!=destination['disk_guid']:
        same_drive=False
    else:same_drive=None
    return dict(same_physical_drive=same_drive,fields=changes,
        sector_size_changed=source['logical_sector_size']!=destination['logical_sector_size'],
        connection_changed=source['connection']!=destination['connection'])


def warning(metadata,destination):
    result=compare(metadata,destination)
    lines=['WINDOWS BACKUP AND RESTORE IS EXPERIMENTAL.',
        'We recommend restoring to the exact same physical drive the backup was made from. '
        'Even a same-drive restore is not guaranteed to boot. Keep another backup until you have tested the restored system.']
    if result['same_physical_drive'] is False:
        lines+=['','THIS IS A DIFFERENT DRIVE. Boot, storage drivers, activation and hardware behaviour may differ.']
    elif result['same_physical_drive'] is None:
        lines+=['','The exact physical drive could not be confirmed. Matching GPT identifiers or a USB adapter\'s serial number alone are not proof.']
    if metadata['origin']['kind']!='app':
        lines+=['','This Windows installation was not verified as created by GoAwayBCA. Restoring it does not '
                'enable removal through this app. If you later remove it, back it up, format its partition '
                'yourself and rebuild the boot menu.']
    lines+=['','Latest backup source → selected destination:']
    for row in result['fields']:
        def show(value):
            if value is None:return 'unavailable'
            if isinstance(value,(list,dict)):return json.dumps(value,ensure_ascii=True,sort_keys=True)
            return str(value)
        lines.append('%s: %s → %s (%s)'%(row['label'],show(row['source']),show(row['destination']),row['status']))
    if result['sector_size_changed']:
        lines+=['','Logical sector size differs. A file restore does not by itself convert Windows storage/boot '
                'support. The app must prepare the destination-specific boot files and any required compatibility '
                'components before restoration can proceed.']
    return '\n'.join(lines)


def metadata_hash(metadata):
    validate(metadata)
    return hashlib.sha256(canonical(metadata)).hexdigest()
