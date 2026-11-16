"""Positive evidence for externally removed Windows; no writes or raw-disk reads.

An absent scan result is not evidence of deletion. Only a present, identified
disk and complete partition metadata, or a readable former Windows partition,
can confirm removal. Unreadable volumes and unknown filesystems are retained.
"""
from pathlib import Path
from boot_catalog import identity, guid


def inspect_presence(entries, rows, layout, read_partition):
    """Pure policy around an injected read-only partition observer."""
    found = {identity(r['observed']) for r in rows}
    disk = guid(layout['guid'])
    parts = {guid(p['guid']): p for p in layout['partitions'].values()}
    proofs = []
    for entry in entries:
        key = identity(entry)
        if key in found or guid(entry['disk_guid']) != disk:
            continue
        part = guid(entry['partition_guid'])
        reason = None
        if part not in parts:
            if layout.get('partition_scan_complete') is True:
                reason = 'partition-removed'
        else:
            state = read_partition(part, parts[part])
            if state == 'no-windows-directory':
                reason = 'windows-directory-absent'
            elif state == 'non-ntfs-filesystem':
                reason = 'partition-reformatted'
        if reason:
            proofs.append(dict(disk_guid=disk, partition_guid=part,
                status='confirmed-absent', reason=reason, layout_digest=layout['digest']))
    return proofs


def collect(owner, entries, rows, target):
    found = {identity(r['observed']) for r in rows}
    if all(identity(e) in found for e in entries):
        return []
    from install_windows7 import gpt_for, info, filesystem, media_size, require
    from ntfs_read import root_names
    layout = gpt_for(target)
    def read_partition(part, geometry):
        device = layout.get('partition_devices', {}).get(part)
        if not device:
            return 'unknown'
        try:
            current = info(device)
            require(current.get('DiskUUID', '').lower() == part and
                    current.get('ParentWholeDisk') == target['disk']['DeviceIdentifier'] and
                    media_size(current) == (geometry['last']-geometry['first']+1)*layout['sector_size'] and
                    current.get('PartitionMapPartitionOffset') == geometry['first']*layout['sector_size'],
                    'Partition identity changed during removal discovery')
            kind = filesystem(current)
            if kind == 'ntfs':
                names = root_names(current, Path(owner.ntfscat).with_name('ntfsls'))
                return 'windows-present' if any(n.casefold() == 'windows' for n in names) else 'no-windows-directory'
            if kind in ('exfat', 'hfs', 'apfs', 'msdos', 'fat32', 'fat16', 'fat12'):
                return 'non-ntfs-filesystem'
        except (OSError, ValueError, RuntimeError) as error:
            owner.note('Keeping unavailable Windows menu entry: '+device+' could not be checked: '+str(error))
        return 'unknown'
    result = inspect_presence(entries, rows, layout, read_partition)
    require(gpt_for(target)['digest'] == layout['digest'], 'Partition layout changed during removal discovery')
    return result


def recheck(owner, entries, rows, target, expected):
    from install_windows7 import require
    require(collect(owner, entries, rows, target) == expected,
            'Externally removed Windows state changed; refresh the menu again before proceeding')


def empty_menu_targets(owner, excluded_esps=()):
    """Find our existing internal menus even when every Windows volume is gone."""
    import drive_geometry
    import menu_lifecycle as m
    from install_windows7 import info, gpt_for, ESP_TYPE, require, tree_manifest, media_size
    targets = {}
    for drive in drive_geometry.collect()['drives'].values():
        target = dict(disk=info(drive['device_at_launch']))
        layout = gpt_for(target)
        for part in layout['partitions'].values():
            if part['type'] != ESP_TYPE:
                continue
            device = layout.get('partition_devices', {}).get(part['guid'])
            if not device or device in excluded_esps:
                continue
            current = info(device)
            require(current.get('DiskUUID','').lower() == part['guid'] and
                    current.get('ParentWholeDisk') == target['disk']['DeviceIdentifier'] and
                    current.get('PartitionMapPartitionOffset') == part['first']*layout['sector_size'] and
                    media_size(current) == (part['last']-part['first']+1)*layout['sector_size'],
                    'EFI identity changed during menu discovery')
            if owner.esp_owned and owner.esp_device != device:
                require(not owner.close_esp(), 'Could not detach prior scan EFI partition')
            root = owner.mount_esp(device,readonly=True)
            catalog = root/m.CATALOG
            if not catalog.is_file() or catalog.is_symlink():
                continue
            require(catalog.stat().st_size <= 1024**2, 'Oversized menu catalog')
            manifest = tree_manifest(root)
            # Validate the actual catalog/configuration before offering it.
            from backtoseven_menu import previous
            previous(m.snapshot(root,manifest),layout['guid'],part['guid'])
            targets[device] = dict(target=dict(target,esp=device),boot_menu_only=True,
                efi_manifest=manifest,esp_guid=part['guid'],summary='Windows boot menu on '+target['disk']['DeviceIdentifier'])
    return targets
