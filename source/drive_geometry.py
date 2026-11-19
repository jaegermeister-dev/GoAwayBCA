"""One privileged-session geometry snapshot, derived from internal drive metadata.

No device opens, raw reads, model guesses or bus-dependent sector assumptions.
Later operations compare fresh identities/geometry with this snapshot before
using its sector size. Partition contents may change; disk identity must not.
"""
import copy
import uuid

_session = None
_external = {}


def install_session(value):
    global _session, _external
    _external = {}  # Each operation must observe connected USB media afresh.
    if value is None:
        _session = None
        return
    if type(value) is not dict or value.get('schema') != 1 or type(value.get('drives')) is not dict:
        raise ValueError('Invalid administrator-session drive geometry')
    for key, row in value['drives'].items():
        if (str(uuid.UUID(key)) != key or not uuid.UUID(key).int or type(row) is not dict
                or row.get('disk_guid') != key or type(row.get('sector_size')) is not int
                or row['sector_size'] not in (512, 4096) or type(row.get('bytes')) is not int
                or row['bytes'] <= 0 or row['bytes'] % row['sector_size']):
            raise ValueError('Invalid drive identity or sector size in session')
    _session = copy.deepcopy(value)


def report():
    return copy.deepcopy(_session)


def confirm(layout):
    """Fail on changed/unknown drive; a fresh launch creates a fresh snapshot."""
    if layout.get('connection') == 'USB':
        binding = {key: layout.get(key) for key in
                   ('guid', 'sector_size', 'bytes', 'device', 'device_path', 'connection')}
        if not binding['device'] or not binding['device_path']:
            raise ValueError('USB drive identity is incomplete; reconnect it and retry.')
        previous = _external.setdefault(layout['guid'], binding)
        if binding != previous:
            raise ValueError('The USB drive or its connection/sector size changed during this operation. '
                             'No further writes are allowed. Reconnect it and start the operation again.')
        return layout['sector_size']
    if _session is None:
        return layout['sector_size']  # Standalone read-only helpers/tests.
    row = _session['drives'].get(layout['guid'])
    if row is None:
        raise ValueError('This internal drive was not identified at launch. Close and reopen GoAwayBCA to detect it before continuing.')
    if row['sector_size'] != layout['sector_size'] or row['bytes'] != layout['bytes']:
        raise ValueError('Drive geometry changed since launch. Close and reopen GoAwayBCA. No further writes are allowed in this session.')
    return row['sector_size']


def external_report():
    return copy.deepcopy(_external)


def collect(note=lambda message: None, tree=None, read_info=None, include_external=False):
    from install_windows7 import plist_command, raw_info
    from partition_detection import registry_index
    from disk_layout import layout
    if tree is None:
        tree = plist_command('/usr/sbin/ioreg', '-a', '-l', '-r', '-c', 'IOBlockStorageDevice')
    read_info = read_info or raw_info
    index = registry_index(tree)
    result = dict(schema=1, source='diskutil and IORegistry metadata', drives={})
    for name, entry in sorted(index.items()):
        node = entry['node']
        location=entry['protocol'].get('Physical Interconnect Location')
        usb=location=='External' and entry['protocol'].get('Physical Interconnect')=='USB'
        if node.get('Whole') is not True or (location!='Internal' and not (include_external and usb)):
            continue
        # MBR disks still go through the separate launch compatibility flow.
        # They cannot supply a valid GPT installation destination here.
        if not any(n.get('IOObjectClass', n.get('IOClass')) == 'IOGUIDPartitionScheme'
                   for n in node.get('IORegistryEntryChildren', [])):
            continue
        current = layout(read_info(name), tree)
        key = current['guid']
        if key in result['drives']:
            raise ValueError('Duplicate internal drive identity at launch')
        result['drives'][key] = dict(disk_guid=key, device_at_launch=name,
            sector_size=current['sector_size'], bytes=current['bytes'])
        note('%s drive %s: %d-byte logical sectors; shared by installation, management and boot-menu operations.' %
             ('USB' if usb else 'Internal',name, current['sector_size']))
    return result
