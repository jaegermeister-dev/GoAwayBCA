"""Partition identities from macOS metadata; never opens a whole-disk device.

This is NOT an MBR/hybrid detector or a raw GPT checksum validator. The launch
check or manual declaration handles layout compatibility. Metadata binds every later operation
to the intended disk, partition UUID and byte range, including 4K-sector disks.
"""
import hashlib
import json
import uuid


def sector_bytes(disk):
    from install_windows7 import require
    value = disk.get('DeviceBlockSize')
    require(type(value) is int and value in (512, 4096),
            'The destination logical-sector size is unavailable or unsupported.')
    return value


def layout(disk, tree=None):
    from install_windows7 import require, plist_command, media_size, DISK_RE
    from partition_detection import registry_index
    name = disk.get('DeviceIdentifier', '')
    require(DISK_RE.fullmatch(name), 'Invalid physical disk identifier')
    if tree is None:
        tree = plist_command('/usr/sbin/ioreg', '-a', '-l', '-r', '-c', 'IOBlockStorageDevice')
    index = registry_index(tree)
    require(name in index and index[name]['node'].get('Whole') is True,
            'Physical disk is absent from macOS partition metadata: '+name)
    whole = index[name]['node']
    size = media_size(disk)
    sector = sector_bytes(disk)
    require(whole.get('Size') == size and whole.get('Preferred Block Size') == sector,
            'macOS disk size or logical-sector metadata disagrees: '+name)
    require(size % sector == 0, 'Disk size is not a whole number of logical sectors')
    schemes = [n for n in whole.get('IORegistryEntryChildren', [])
               if n.get('IOObjectClass', n.get('IOClass')) == 'IOGUIDPartitionScheme']
    require(len(schemes) == 1, 'GPT partition identities are unavailable for '+name)
    def guid(value):
        try:
            result = uuid.UUID(value)
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Invalid UUID in macOS partition metadata')
        require(result.int != 0, 'Missing UUID in macOS partition metadata')
        return str(result)
    disk_guid = guid(schemes[0].get('UUID'))
    partitions, occupied, identities = {}, [], {disk_guid}
    devices, complete = {}, True
    for node in schemes[0].get('IORegistryEntryChildren', []):
        device = node.get('BSD Name')
        if not device:
            # A partially enumerated partition must never be evidence that a
            # previously recorded Windows partition has been deleted.
            complete = False
            continue
        require(device in index and index[device]['parent'] == name
                and node.get('Whole') is False, 'Partition parent identity disagrees')
        number, base, length = (node.get(k) for k in ('Partition ID','Base','Size'))
        flags = node.get('GPT Attributes', 0)
        require(type(number) is int and 0 < number <= 4096 and number not in partitions,
                'Duplicate or invalid partition identifier')
        require(type(base) is int and type(length) is int and base > 0 and length > 0
                and base % sector == 0 and length % sector == 0 and base+length <= size,
                'Invalid partition byte range')
        require(type(flags) is int and 0 <= flags < 2**64, 'Invalid partition attributes')
        require(not any(base < end and base+length > start for start,end in occupied),
                'Overlapping partition metadata')
        part_guid = guid(node.get('UUID'))
        require(part_guid not in identities, 'Duplicate partition UUID')
        identities.add(part_guid); occupied.append((base,base+length))
        devices[part_guid] = device
        partitions[number] = dict(type=guid(node.get('Content Hint') or node.get('Content')),
            guid=part_guid, first=base//sector, last=(base+length)//sector-1, flags=flags)
    require(partitions, 'No GPT partition identities available')
    identity = dict(guid=disk_guid, partitions=partitions, sector_size=sector, bytes=size)
    digest = hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    result = dict(identity, digest=digest, source='macOS IORegistry partition metadata',
                  hybrid_mbr_checked=False, partition_devices=devices,
                  partition_scan_complete=complete)
    protocol = index[name]['protocol']
    if protocol.get('Physical Interconnect Location') == 'External':
        require(protocol.get('Physical Interconnect') == 'USB',
                'Only internal and USB Windows destinations are supported.')
        result.update(connection='USB', device=name, device_path=disk.get('DeviceTreePath'))
    from drive_geometry import confirm
    result['sector_size'] = confirm(result)
    return result


def require_windows_sector_support(target, family, prepared_4kn=None):
    from install_windows7 import require
    sector = sector_bytes(target['disk'])
    if family=='Windows 7' and sector==4096 and prepared_4kn is not None:
        from win7_sector import validate_profile
        validate_profile(prepared_4kn)
        return
    require(family != 'Windows 7' or sector == 512,
        'This selected drive reports 4096-byte logical sectors (4Kn). Windows 7 does not '
        'support installing to a 4Kn disk. Use a drive exposing 512-byte logical sectors '
        '(including 512e), or choose Windows 8 or newer. Nothing has been formatted. '
        'This is a destination/Windows limitation, not a macOS launch requirement.')
