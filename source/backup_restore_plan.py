"""Read-only restore planning: geometry, provenance and explicit warnings.

No destination is opened here. The executor must still prepare the selected
route, verify capacity, obtain consent and revalidate the chosen physical drive.
"""
import copy

import backup_metadata
from installation_records import can_uninstall
from product_scope import INSTALL_FAMILIES


def require(value, message):
    if not value:
        raise ValueError(message)


def provenance(metadata, source_marker):
    """Match the creation pair against the marker READ FROM the verified image.

    The archive's eligible flag and its copied marker are not sufficient. A
    foreign origin never becomes ours because our tool restored its files.
    """
    if not metadata['uninstall_eligible'] or metadata['origin']['kind'] != 'app':
        return dict(eligible=False, status='external-or-unverified', record=None)
    evidence = metadata.get('creation_evidence')
    if type(evidence) is not dict or evidence.get('marker') != source_marker:
        return dict(eligible=False, status='creation-pair-not-verified', record=None)
    location = metadata['source']
    observed = dict(disk_guid=location['disk_guid'], partition_guid=location['partition_guid'],
        partition_bytes=location['partition_bytes'], partition_offset=location['partition_offset'],
        filesystem='ntfs', windows_verified=True)
    if not can_uninstall(observed, source_marker, evidence.get('record')):
        return dict(eligible=False, status='creation-pair-not-verified', record=None)
    return dict(eligible=True, status='verified-application-origin', record=copy.deepcopy(evidence['record']))


def plan(metadata, geometry, destination, source_marker, verified_windows):
    backup_metadata.validate(metadata)
    backup_metadata.validate_location(destination)
    family = metadata['windows']['family']
    require(type(verified_windows) is dict and
            (verified_windows.get('family'),verified_windows.get('architecture')) ==
            (family,metadata['windows']['architecture']),
            'The restored Windows version and architecture must be verified from its filesystem image.')
    require(family in INSTALL_FAMILIES, 'GoAwayBCA restores Windows 8.1, 10 and 11 backups only. '
            +family+' restoration is not enabled. No destination was changed.')
    require(family != 'Windows 11' or verified_windows['architecture']==9,
            'This Windows 11 architecture is not supported.')
    source = metadata['source']
    require(type(geometry) is dict and geometry.get('bytes') == source['partition_bytes'] and
            geometry.get('logical_sector_size') == source['logical_sector_size'],
            'The verified filesystem geometry does not match the backup metadata.')
    allocated = geometry.get('allocated_bytes')
    require(type(allocated) is int and 0 < allocated <= geometry['bytes'], 'Missing verified filesystem allocation.')
    require(destination['partition_bytes'] > allocated,
            'The selected destination cannot contain the backup\'s allocated filesystem data. '
            'Choose a larger partition. No destination was changed.')
    comparison = backup_metadata.compare(metadata, destination)
    changed_size = source['partition_bytes'] != destination['partition_bytes']
    if comparison['sector_size_changed']:
        route = 'file-conversion'
        actions = ['Inspect NTFS metadata for features the conversion cannot preserve',
                   'Prepare a file image and verify it fits the selected destination',
                   'Create a destination filesystem using its detected logical sector size']
    else:
        route = 'ntfs-image'
        actions = ['Use the verified NTFS filesystem image without file-level conversion']
        if changed_size:
            actions += ['Check and prepare the NTFS filesystem for the selected partition capacity']
    actions += ['Update restored volume identity and Windows drive mappings for the destination',
                'Prepare boot files that refer to the restored partition',
                'Preserve existing Windows installations and unrelated EFI files',
                'Refresh only this destination drive\'s menu, including Windows added or removed outside the app']
    if destination['connection'] == 'USB':
        actions.append('Review optional portable Windows preparation for this USB connection')
    origin = provenance(metadata, source_marker)
    history = backup_metadata.restored_history(metadata, destination)
    history['uninstall_eligible'] = origin['eligible']
    # A claim with no matching creation pair remains unverified on future
    # backups as well. Do not manufacture replacement evidence here.
    history['restore_provenance'] = origin['status']
    title = metadata.get('menu_title') or family
    require(type(title) is str and 0 < len(title) <= 120 and not any(ord(c)<32 or ord(c)==127 for c in title),
            'The saved boot-menu name is invalid. Select a valid name before restoring.')
    result = dict(schema=1, experimental=True, family=family, architecture=metadata['windows']['architecture'],
        title=title, source=copy.deepcopy(source), destination=copy.deepcopy(destination),
        comparison=comparison, route=route, partition_capacity_changed=changed_size,
        capacity_preparation_required=True, allocated_bytes=allocated,
        provenance=origin, restored_history=history, actions=actions, destination_writes=False)
    return result


def review(result):
    """A short decision dialog; complete comparisons belong in Details."""
    source = result['source']; destination = result['destination']; comparison = result['comparison']
    text = ('WINDOWS RESTORE IS EXPERIMENTAL.\n\nRestore “'+result['title']+'” ('+result['family']+').\n\n'
        'We recommend the exact same physical drive the backup was made from. A different drive, USB adapter '
        'or connection can affect booting and storage drivers. Even restoring to the same drive is experimental.\n\n'
        'Logical sector size: %d → %d bytes\nConnection: %s → %s\nPartition capacity: %.1f → %.1f GiB' %
        (source['logical_sector_size'], destination['logical_sector_size'], source['connection'], destination['connection'],
         source['partition_bytes']/1024**3, destination['partition_bytes']/1024**3))
    if comparison['same_physical_drive'] is False:
        text += '\n\nTHIS IS A DIFFERENT PHYSICAL DRIVE.'
    elif comparison['same_physical_drive'] is None:
        text += '\n\nThe exact physical drive could not be confirmed.'
    if not result['provenance']['eligible']:
        text += ('\n\nApplication ownership is unverified. Restoring this backup will not enable uninstalling '
                 'it through GoAwayBCA. Its backup history will retain this restriction.')
    text += ('\n\nAll existing files on the selected destination partition will be erased only after further '
             'confirmations. Keep the original backup and another copy until you have tested the restored Windows installation.')
    details = backup_metadata.warning(result['restored_history'], destination)
    details += '\n\nPreparation required before destination changes:\n'+'\n'.join('- '+item for item in result['actions'])
    return text, details
