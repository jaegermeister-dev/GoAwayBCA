"""Partition-only Internal/External selection. All rows are read-only metadata."""
from install_windows7 import (require, plist_command, info, partition_summary,
                             media_size, filesystem, assert_same_target, gpt_for)


def rows(targets, listing, read_info):
    eligible = {t['partition']['DeviceIdentifier']: (i, t) for i, t in enumerate(targets)}
    result = []
    for item in listing.get('AllDisksAndPartitions', []):
        device = item.get('DeviceIdentifier', '')
        try:
            disk = read_info(device)
            if disk.get('Whole') is not True or disk.get('VirtualOrPhysical') != 'Physical':
                continue
            internal = disk.get('Internal')
            if internal is not True and not (internal is False and disk.get('BusProtocol') == 'USB'):
                continue
            location = 'Internal' if internal else 'External'
            gpt = disk.get('Content') == 'GUID_partition_scheme'
            result.append(dict(kind='drive', location=location, enabled=gpt,
                title='%s — %s (%.1f GB)' % (device, disk.get('MediaName') or 'Drive', media_size(disk)/10**9),
                detail='GPT' if gpt else 'MBR is not supported. Only GPT drives can be used.', id=-1))
            for entry in item.get('Partitions', []):
                name = entry.get('DeviceIdentifier', '')
                if name in eligible:
                    index, target = eligible[name]
                    # Do not let a newly substituted listing turn a stale target into consent.
                    require(target['disk']['DeviceIdentifier'] == device, 'Destination parent changed')
                    part = target['partition']
                    detail = partition_summary(part, disk, multiline=True)
                    result.append(dict(kind='partition',location=location,enabled=True,id=index,
                        title='%s — %s (%.1f GB, %s)' % (name,part.get('VolumeName') or 'Unnamed',
                            media_size(part)/10**9,filesystem(part).upper()),detail=detail))
                else:
                    reason = ('MBR is not supported.' if not gpt else
                              'Requires an empty, writable exFAT/NTFS partition and an EFI partition on this GPT drive. '
                              'Existing Windows installations cannot be overwritten.')
                    result.append(dict(kind='partition',location=location,enabled=False,id=-1,
                        title=name+' — '+str(entry.get('VolumeName') or entry.get('Content') or 'Partition'),detail=reason))
        except (OSError, ValueError, RuntimeError) as error:
            # Display a failed enumeration rather than invent a selectable destination.
            result.append(dict(kind='drive',location='External',enabled=False,id=-1,
                title=device+' — details unavailable',detail=str(error)))
    return result


def choose(owner, targets, family):
    listing = plist_command('/usr/sbin/diskutil', 'list', '-plist')
    items = rows(targets, listing, info)
    require(any(row['enabled'] and row['kind']=='partition' for row in items),
            'No eligible destination remains. Refresh the partition list and retry.')
    selected = owner.interaction.request('destination_picker',
        'Choose the destination for '+family+'. Only the selected existing partition is formatted, '
        'after the formatting confirmations. External USB installation is experimental and untested. '
        'MBR drives and incompatible or occupied partitions cannot be selected.', items=items)
    require(type(selected) is int and 0 <= selected < len(targets) and
            any(r['kind']=='partition' and r['enabled'] and r['id']==selected for r in items),
            'Invalid or disabled destination selection')
    target = targets[selected]
    assert_same_target(target, info(target['partition']['DeviceIdentifier']), info(target['disk']['DeviceIdentifier']))
    layout = gpt_for(target)
    if target['disk'].get('Internal') is False:
        owner.interaction.request('notice',
            'Selected USB drive: '+target['disk']['DeviceIdentifier']+'\nLogical sector size: '+
            str(layout['sector_size'])+' bytes.\n\nThis measurement applies to its current USB connection. '
            'Changing the drive, enclosure or adapter can change the result. Keep it connected throughout '
            'installation and while Windows is running. Its Windows boot menu is separate from other drives. '
            'Being readable in macOS does not guarantee it will boot on this Mac.', button_title='Done')
    return target
