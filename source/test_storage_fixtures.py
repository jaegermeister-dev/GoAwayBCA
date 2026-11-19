import uuid
import install_windows7 as app

def fixtures():
    disk = app.normalize_info({'DeviceIdentifier': 'disk0', 'WholeDisk': True, 'Internal': True,
          'VirtualOrPhysical': 'Physical', 'WritableMedia': True, 'DeviceTreePath': 'IODeviceTree:test',
          'Content': 'GUID_partition_scheme', 'TotalSize': 128 * app.GIB,
          'IOKitSize': 128 * app.GIB, 'Size': 128 * app.GIB, 'MediaName': 'SSD'})
    part = app.normalize_info({'DeviceIdentifier': 'disk0s3', 'ParentWholeDisk': 'disk0', 'WholeDisk': False,
          'Internal': True, 'WritableMedia': True, 'WritableVolume': True, 'FilesystemType': 'exfat',
          'Content': 'Microsoft Basic Data', 'DiskUUID': str(uuid.uuid4()), 'VolumeUUID': str(uuid.uuid4()),
          'TotalSize': 40 * app.GIB - 4096, 'IOKitSize': 40 * app.GIB,
          'Size': 40 * app.GIB, 'PartitionMapPartitionOffset': 400 * 1024**2,
          'MountPoint': '/Volumes/WIN7', 'VolumeName': 'WIN7', 'FreeSpace': 30 * app.GIB})
    listing = {'AllDisksAndPartitions': [{'DeviceIdentifier': 'disk0', 'Partitions': [
        {'DeviceIdentifier': 'disk0s1', 'Content': 'EFI'},
        {'DeviceIdentifier': 'disk0s3', 'Content': 'Microsoft Basic Data'}]}]}
    return disk, part, listing

