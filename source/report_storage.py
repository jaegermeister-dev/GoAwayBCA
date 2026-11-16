"""Storage identity for the running macOS, not arbitrary volume contents."""
import plistlib,re,subprocess
from install_windows7 import info,gpt_for,Refusal

KEYS=frozenset(('DeviceIdentifier','DeviceNode','ParentWholeDisk','DiskUUID','VolumeUUID',
    'APFSContainerReference','ContainerReference','APFSContainerUUID','APFSVolumeUUID',
    'APFSVolumeGroupID','APFSVolumeGroupUUID','APFSSnapshotUUID','APFSSnapshot',
    'PhysicalStoreUUID','PhysicalStores','APFSPhysicalStores','APFSPhysicalStore',
    'Volumes','Roles','VolumeGroupUUID','Content','FilesystemType','FilesystemName',
    'MountPoint','DeviceBlockSize','IOKitSize','TotalSize','Size','CapacityCeiling',
    'Internal','VirtualOrPhysical','WholeDisk','Partitions','VolumeName','Name'))

def identity(value):
    if isinstance(value,dict):return {k:identity(v) for k,v in value.items() if k in KEYS}
    if isinstance(value,list):return [identity(v) for v in value[:256]]
    if value is None or type(value) in (str,int,bool):return value
    return 'unavailable'

def plist_read(argv):
    value=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=25)
    if value.returncode or len(value.stdout)>8*1024**2:raise ValueError('Storage query unavailable')
    return plistlib.loads(value.stdout)

def collect(get_info=info,read=plist_read,get_gpt=gpt_for):
    output=dict(source='Read-only diskutil metadata for the currently running root volume',failures=[])
    try:root=get_info('/');output['root_volume']=identity(root)
    except (OSError,ValueError,Refusal,subprocess.SubprocessError) as error:
        output['failures'].append(str(error));output['status']='unavailable';return output
    stores=root.get('APFSPhysicalStores',[])
    refs=[s.get('APFSPhysicalStore') for s in stores] if stores else [root.get('DeviceIdentifier')]
    if root.get('FilesystemType')=='apfs':
        try:
            containers=read(['/usr/sbin/diskutil','apfs','list','-plist']).get('Containers',[])
            selected=[c for c in containers if c.get('ContainerReference')==root.get('APFSContainerReference')]
            if len(selected)!=1:raise ValueError('Running APFS container mapping is ambiguous/unavailable')
            output['apfs_container']=identity(selected[0])
        except (OSError,ValueError,Refusal,subprocess.SubprocessError) as error:output['failures'].append(str(error))
    output['physical_stores']=[];output['physical_disk_gpt']={}
    for reference in refs:
        if not isinstance(reference,str) or not re.fullmatch(r'disk\d+s\d+',reference):
            output['failures'].append('Physical store mapping unavailable');continue
        try:
            part=get_info(reference);output['physical_stores'].append(identity(part))
            parent=part.get('ParentWholeDisk')
            if not isinstance(parent,str) or not re.fullmatch(r'disk\d+',parent):raise ValueError('Backing whole disk unavailable')
            if parent in output['physical_disk_gpt']:continue
            disk=get_info(parent)
            if disk.get('VirtualOrPhysical')!='Physical':raise ValueError('Backing disk is not identified as physical')
            output['physical_disk_gpt'][parent]=dict(identity=identity(disk),gpt=get_gpt(dict(disk=disk)))
        except (OSError,ValueError,Refusal,subprocess.SubprocessError) as error:output['failures'].append(str(error))
    output['status']='partial' if output['failures'] else 'observed'
    return output
