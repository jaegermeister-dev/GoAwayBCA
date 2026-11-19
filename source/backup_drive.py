"""Capture comparable drive metadata from the selected physical connection."""
import backup_metadata


def describe(target, tree=None):
    from install_windows7 import require, info, match_gpt, assert_same_target, plist_command
    from disk_layout import layout as read_layout
    from partition_detection import registry_index
    disk = target['disk']; part = target['partition']
    assert_same_target(target, info(part['DeviceIdentifier']), info(disk['DeviceIdentifier']))
    if tree is None:
        tree = plist_command('/usr/sbin/ioreg', '-a', '-l', '-r', '-c', 'IOBlockStorageDevice')
    layout = read_layout(disk, tree)
    win, esp = match_gpt(target, layout)
    index = registry_index(tree)
    device = disk['DeviceIdentifier']
    require(device in index, 'The selected backup drive is no longer present.')
    location = index[device]['protocol'].get('Physical Interconnect Location')
    bus = index[device]['protocol'].get('Physical Interconnect')
    require(location == 'Internal' or (location == 'External' and bus == 'USB'),
            'Back up Windows from an internal or USB drive.')
    parents = []
    def contains(node):
        if not isinstance(node, dict): return False
        if node.get('BSD Name') == device and node.get('Whole') is True: return True
        return any(contains(child) for child in node.get('IORegistryEntryChildren', []))
    for root in tree:
        if contains(root): parents.append(root)
    require(len(parents) == 1, 'Backup drive identity is ambiguous.')
    characteristics = parents[0].get('Device Characteristics', {})
    require(type(characteristics) is dict, 'Invalid backup drive characteristics.')
    def text(value):
        if isinstance(value, bytes):
            value = value.rstrip(b'\0').decode('utf-8', 'replace')
        return value.strip() if isinstance(value, str) and value.strip() else None
    serial = text(characteristics.get('Serial Number'))
    model = text(characteristics.get('Product Name')) or text(disk.get('MediaName'))
    physical = disk.get('PhysicalBlockSize')
    # A preferred/logical block size is not evidence of physical-sector size.
    if type(physical) is not int or physical not in (512, 4096): physical = None
    sector = layout['sector_size']
    result = dict(disk_guid=layout['guid'], partition_guid=win['guid'],
        logical_sector_size=sector, physical_sector_size=physical,
        serial=serial, serial_kind='physical-drive' if location == 'Internal' and serial else 'connection-reported',
        model=model, connection='Internal' if location == 'Internal' else 'USB',
        controller=text(bus), adapter=text(parents[0].get('USB Product Name')),
        connection_path=text(disk.get('DeviceTreePath')),
        bytes=layout['bytes'], partition_bytes=(win['last']-win['first']+1)*sector,
        partition_offset=win['first']*sector,
        layout=[dict(number=number, type=row['type'], guid=row['guid'],
                     offset=row['first']*sector, bytes=(row['last']-row['first']+1)*sector)
                for number,row in sorted(layout['partitions'].items())])
    backup_metadata.validate_location(result)
    assert_same_target(target, info(part['DeviceIdentifier']), info(disk['DeviceIdentifier']))
    return result
