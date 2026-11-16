"""Strict ownership evidence for Manage Installations (pure data, no I/O).

Callers supply the paired WinDeploy EFI receipt and Windows-partition marker,
fresh GPT identity and fresh owned-EFI hashes. A marker alone
is never proof of ownership. Uninstall requires verified paired creation records.
Changed boot assets remain protected independently of origin. No blanket EFI-folder deletion.
"""
import hashlib
import json
import re
from boot_catalog import guid, identity, efi_path
from installation_policy import UNOWNED_WARNING, CLEANUP_WARNING

SCHEMA = 1
HEX = re.compile(r'[0-9a-f]{64}\Z')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def validate_record(record):
    require(type(record) is dict and record.get('schema') == SCHEMA, 'Unknown record schema')
    require(record.get('creator') == 'WinDeploy', 'Unknown record creator')
    require(record.get('state') == 'committed', 'Installation transaction is not complete')
    identity(record)
    guid(record['installation_uuid'])
    guid(record['esp_guid'])
    require(type(record.get('partition_bytes')) is int and record['partition_bytes'] >= 20 * 1024**3,
            'Invalid recorded partition size')
    require(type(record.get('partition_offset')) is int and record['partition_offset'] >= 1024**2
            and record['partition_offset'] % 512 == 0, 'Invalid recorded partition offset')
    require(record.get('family') in ('Windows 7', 'Windows 8', 'Windows 8.1', 'Windows 10', 'Windows 11'), 'Unknown installed OS')
    require(isinstance(record.get('wim_sha256'), str) and HEX.fullmatch(record['wim_sha256']),
            'Invalid image digest')
    require(type(record.get('efi_assets')) is list and record['efi_assets'], 'No owned boot assets')
    seen = set()
    for asset in record['efi_assets']:
        require(type(asset) is dict, 'Invalid owned asset')
        path = efi_path(asset['path'])
        device = guid(asset['partition_guid'])
        require(device in (guid(record['esp_guid']), guid(record['partition_guid'])),
                'EFI asset points outside its recorded boot/Windows partitions')
        require(not path.casefold().startswith(('efi/oc/', 'efi/apple/')),
                'WinDeploy cannot own OpenCore or Apple boot files')
        key = (device, path.casefold())
        require(key not in seen, 'Duplicate EFI asset')
        seen.add(key)
        require(isinstance(asset.get('sha256'), str) and HEX.fullmatch(asset['sha256']),
                'Invalid EFI digest')
        require(asset.get('exclusive') is True, 'Shared EFI assets are not installation-owned')
    return record


def make_marker(record):
    validate_record(record)
    return dict(schema=SCHEMA, creator='WinDeploy', installation_uuid=record['installation_uuid'],
                disk_guid=guid(record['disk_guid']), partition_guid=guid(record['partition_guid']),
                record_sha256=sha(record))


def paired_record(observed, marker, ledger_record):
    """Prove record/partition pairing independently from boot-file ownership."""
    require(ledger_record is not None, 'No paired WinDeploy receipt')
    record = validate_record(ledger_record)
    require(marker == make_marker(record), 'Missing, altered or mismatched partition marker')
    require(identity(observed) == identity(record), 'GPT identity changed')
    require(observed.get('partition_bytes') == record['partition_bytes'] and
            observed.get('partition_offset') == record['partition_offset'], 'Partition geometry changed')
    require(observed.get('filesystem') == 'ntfs' and observed.get('windows_verified') is True,
            'Windows files are not verified')
    return record


def ownership(observed, marker=None, ledger_record=None, asset_hashes=None, bcd_files=None):
    """Never overclaim: missing evidence means unverified, not third-party proof."""
    unverified = dict(status='unverified', managed=False, message=UNOWNED_WARNING)
    try:
        record = paired_record(observed, marker, ledger_record)
    except (ValueError, TypeError, KeyError):
        return unverified
    try:
        require(asset_hashes is not None, 'Boot assets were not read')
        for asset in record['efi_assets']:
            require(asset_hashes.get((guid(asset['partition_guid']), asset['path'])) == asset['sha256'],
                    'Owned boot asset changed or is unavailable')
    except (ValueError, TypeError, KeyError):
        if bcd_files is not None:
            try:
                from standalone_ownership import verified_assets
                verified_assets(record, asset_hashes, bcd_files)
                return dict(status='verified-windows-serviced-bcd', managed=True,
                            installation_uuid=record['installation_uuid'], record_sha256=sha(record),
                            message='Created by GoAwayBCA or BackToSeven. Windows updated its boot database; '
                                    'the installation identity and remaining boot files are verified.')
            except (ValueError, TypeError, KeyError):
                pass
        return dict(status='windeploy-boot-files-changed', managed=False,
                    installation_uuid=record['installation_uuid'], record_sha256=sha(record),
                    message=('Created by WinDeploy; boot files have changed or could not be read. '
                             'Automatic cleanup cannot assume the original files are still exclusive. '
                             + CLEANUP_WARNING))
    return dict(status='verified-windeploy', managed=True,
                message='Created by WinDeploy; recorded partition and owned boot files match.',
                installation_uuid=record['installation_uuid'], record_sha256=sha(record))


def can_uninstall(observed, marker, ledger_record):
    """Only verified application-created partitions qualify, regardless of menu import."""
    try:
        paired_record(observed, marker, ledger_record)
        return True
    except (ValueError, TypeError, KeyError):
        return False


def format_plan(observed, marker, ledger_record, asset_hashes, all_records, scan_complete,
                unknown_ownership_acknowledged=False, bcd_files=None):
    """A plan is not consent. Executor still needs two confirmations + rechecks."""
    require(scan_complete is True, 'Incomplete installation scan; no format plan')
    require(can_uninstall(observed, marker, ledger_record), UNOWNED_WARNING)
    state = ownership(observed, marker, ledger_record, asset_hashes, bcd_files)
    if not state['managed']:
        require(unknown_ownership_acknowledged is True, state['message'])
        require(observed.get('filesystem') == 'ntfs' and observed.get('windows_verified') is True,
                'Selected item is not a verified NTFS Windows installation')
        require(type(observed.get('partition_bytes')) is int and observed['partition_bytes'] > 0
                and type(observed.get('partition_offset')) is int and observed['partition_offset'] >= 1024**2,
                'Incomplete partition identity')
        # Paired creation records permit removing our installation, but are not
        # permission to guess that an arbitrary
        # Microsoft/Boot or fallback loader belongs only to this installation.
        # A separate verified discovery mapping can identify exact boot assets;
        # without one, preserve them and explicitly report deferred cleanup.
        return dict(operation='format-ntfs', target=identity(observed),
                    erase_partition_guid=guid(observed['partition_guid']),
                    efi_cleanup=[], cleanup_status='unverified-assets-preserved',
                    preserved_efi_paths=list(observed.get('unattributed_efi_paths', [])),
                    ownership_warning=state['message'], ownership_warnings_required=2,
                    regenerate_menu=True, confirmations_required=2,
                    repartition=False, set_default_boot=False, backup_user_data=False)
    selected = identity(ledger_record)
    seen = set()
    references = set()
    for other in all_records:
        validate_record(other)
        key = identity(other)
        require(key not in seen, 'Duplicate installation identity')
        seen.add(key)
        if key == selected:
            require(other == ledger_record, 'Conflicting selected receipt')
        else:
            for asset in other['efi_assets']:
                references.add((guid(asset['partition_guid']), asset['path'].casefold()))
    require(selected in seen, 'Selected installation is missing from complete catalog')
    remove = []
    preserved = []
    for asset in ledger_record['efi_assets']:
        key = (guid(asset['partition_guid']), asset['path'].casefold())
        if key in references:
            preserved.append(asset['path'])
            continue
        # Contents inside the NTFS target disappear through formatting. Only
        # exclusive assets on the ESP need individual, hash-checked cleanup.
        if key[0] == guid(ledger_record['esp_guid']):
            # A serviced BCD is deleted only against its newly verified digest.
            remove.append(dict(asset, sha256=asset_hashes[(guid(asset['partition_guid']), asset['path'])]))
    return dict(operation='format-ntfs', target=selected, record_sha256=state['record_sha256'],
                erase_partition_guid=guid(observed['partition_guid']),
                efi_cleanup=remove, preserved_efi_paths=preserved,
                regenerate_menu=True, confirmations_required=2,
                repartition=False, set_default_boot=False, backup_user_data=False)
