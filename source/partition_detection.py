"""Read-only, version-preferred discovery with reversible fallback order.

diskutil remains the filesystem authority. The alternative supplements its
missing physical/GPT identity fields from IORegistry; it does not read MBR contents.
The OS version chooses order only; it never disables either method.
"""
import platform
import re
import uuid

METHODS = ('diskutil', 'registry-gpt')
_version = None
_reverse = False
_selected = None
_note = lambda message: None
_report = {'attempts': []}


def configure(version=None, reverse=False, note=None):
    global _version, _reverse, _selected, _note, _report
    if type(reverse) is not bool:
        raise ValueError('Invalid partition-detection preference')
    _version, _reverse, _selected = version, reverse, None
    _note = note or (lambda message: None)
    _report = {'macos': version, 'reverse_order': reverse, 'attempts': []}


def order(version, reverse=False):
    try:
        parts = tuple(int(p) for p in version.split('.')[:2])
    except (ValueError, AttributeError):
        parts = ()
    # A preference, NOT a statement of the schema on untested host releases.
    preferred = ('registry-gpt', 'diskutil') if parts and parts <= (10, 9) else METHODS
    return tuple(reversed(preferred)) if reverse else preferred


def report():
    import copy
    return copy.deepcopy(_report)


def registry_index(tree):
    from install_windows7 import require
    require(isinstance(tree, list) and all(isinstance(root, dict) for root in tree),
            'Unexpected IORegistry storage report format')
    found = {}
    physical_buses = {'ATA', 'ATAPI', 'SATA', 'SAS', 'SCSI', 'PCI', 'PCI-Express',
                      'PCIe', 'NVMe', 'USB', 'FireWire', 'Thunderbolt'}

    def visit(node, protocol, whole=None):
        if not isinstance(node, dict):
            return
        name = node.get('BSD Name')
        if name:
            if whole is not None and node.get('Whole') is True:
                return  # APFS/CoreStorage/disk-image layers are not physical GPT slices.
            if node.get('Whole') is True and re.fullmatch(r'disk[0-9]+', name):
                whole = name
            elif not (whole and node.get('Whole') is False and
                      re.fullmatch(re.escape(whole)+r's[1-9][0-9]*', name)):
                return
            if protocol.get('Physical Interconnect') not in physical_buses:
                return
            require(name not in found, 'Duplicate IORegistry disk identity: '+name)
            found[name] = dict(node=node, parent=whole, protocol=protocol)
        for child in node.get('IORegistryEntryChildren', []):
            visit(child, protocol, whole)

    for root in tree:
        visit(root, root.get('Protocol Characteristics', {}))
    return found


class Reader:
    """Caches one discovery snapshot only; later write guards get fresh readers."""
    def __init__(self, method, raw_info=None, read_registry=None, read_gpt=None):
        import install_windows7 as app
        self.method = method
        self.raw_info = raw_info or app.raw_info
        self.read_registry = read_registry or (lambda: app.plist_command(
            '/usr/sbin/ioreg', '-a', '-l', '-r', '-c', 'IOBlockStorageDevice'))
        self.read_gpt = read_gpt or self._read_gpt
        self.rows, self.registry, self.tables = {}, None, {}

    def _read_gpt(self, disk):
        import install_windows7 as app
        from disk_layout import layout
        return layout(disk, self.read_registry())

    def info(self, device):
        import install_windows7 as app
        if device in self.rows:
            return dict(self.rows[device])
        d = self.raw_info(device)
        if self.method == 'diskutil':
            self.rows[device] = dict(d)
            return d
        if self.registry is None:
            self.registry = registry_index(self.read_registry())
        entry = self.registry.get(d.get('DeviceIdentifier'))
        if entry is None:
            # Unsupported/virtual objects remain ineligible, not reclassified.
            self.rows[device] = dict(d)
            return d
        node, parent, protocol = entry['node'], entry['parent'], entry['protocol']

        def merge(key, value, guid=False):
            if key in d:
                left, right = d[key], value
                if guid:
                    left, right = str(left).lower(), str(right).lower()
                app.require(type(left) is type(right) and left == right,
                            'Partition detection disagreement for '+device+': '+key)
            d[key] = value

        app.require(type(node.get('Size')) is int and node['Size'] > 0,
                    'Missing IORegistry physical media size: '+device)
        app.require(app.media_size(d) == node['Size'], 'Physical media size disagreement: '+device)
        merge('Whole', node['Whole'])
        location = protocol.get('Physical Interconnect Location')
        app.require(location in ('Internal', 'External'), 'Unknown storage connection location: '+device)
        merge('Internal', location == 'Internal')
        if d['Whole']:
            if d.get('VirtualOrPhysical') == 'Unknown':
                d.pop('VirtualOrPhysical')
            merge('VirtualOrPhysical', 'Physical')
        else:
            merge('ParentWholeDisk', parent)
            guid = str(uuid.UUID(node.get('UUID', '')))
            app.require(uuid.UUID(guid).int != 0, 'Missing GPT partition identity: '+device)
            merge('DiskUUID', guid, guid=True)
            # Compare kernel-published identities/geometry with diskutil.
            # This does not detect hybrid MBR or repeat the launch scan.
            if parent not in self.tables:
                self.tables[parent] = self.read_gpt(self.info(parent))
            rows = [r for r in self.tables[parent]['partitions'].values()
                    if r['guid'].lower() == guid]
            app.require(len(rows) == 1, 'IORegistry GUID not unique in GPT: '+device)
            row = rows[0]
            app.require(row['type'].lower() == str(node.get('Content', '')).lower() and
                        (row['last']-row['first']+1)*self.tables[parent].get('sector_size',512) == node['Size'],
                        'IORegistry and GPT partition geometry/type disagree: '+device)
            offset = row['first']*self.tables[parent].get('sector_size',512)
            if 'Base' in node:
                app.require(type(node['Base']) is int and node['Base'] == offset,
                            'IORegistry and GPT partition offset disagree: '+device)
            merge('PartitionMapPartitionOffset', offset)
        self.rows[device] = dict(d)
        return d


def inventory(listing=None, reader_factory=Reader):
    global _selected
    import install_windows7 as app
    if listing is None:
        listing = app.plist_command('/usr/sbin/diskutil', 'list', '-plist')
    version = _version or platform.mac_ver()[0]
    methods = order(version, _reverse)
    _report.update(macos=version, reverse_order=_reverse, order=list(methods))
    for method in methods:
        row = {'method': method, 'destinations': [], 'errors': []}
        try:
            result = app.find_targets(listing, reader_factory(method).info)
            row['destinations'] = [dict(device=t['partition']['DeviceIdentifier'],
                guid=t['partition']['DiskUUID'], label=t['partition'].get('VolumeName'),
                bytes=app.media_size(t['partition'])) for t in result]
        except (OSError, ValueError, app.Refusal) as error:
            row['errors'].append(str(error))
            result = []
        _report['attempts'].append(row)
        _note('Partition detection (%s): %d eligible partition(s)%s' %
              (method, len(result), '; '+ '; '.join(row['errors']) if row['errors'] else ''))
        if result:
            _selected = method
            _report['selected_method'] = method
            return result
        if method != methods[-1]:
            _note('No eligible destinations from this method. Trying the other partition-detection method.')
    _selected = None
    _report['selected_method'] = None
    return []


def info(device):
    # Keep post-selection guards consistent with the method that identified the
    # destination, and refresh metadata rather than reusing an earlier snapshot.
    method = _selected or order(_version or platform.mac_ver()[0], _reverse)[0]
    return Reader(method).info(device)
