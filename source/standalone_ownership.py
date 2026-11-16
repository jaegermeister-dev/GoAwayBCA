"""Recognise Windows-serviced standalone BCDs without guessing ownership.

The caller supplies a paired installation receipt, fresh hashes and BCD bytes.
Old completed-format receipts can recover evidence lost by earlier builds, but
only from the protected local log directory and for the exact selected target.
This module makes plans only; it never writes to an EFI or Windows partition.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from boot_catalog import guid, identity
from bcd_semantics import identity as verify_bcd

CANON = 'EFI/Microsoft/Boot/BCD'
LOG_ROOT = Path('/private/var/root/WindowsDeployer-Logs')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verified_assets(record, hashes, files):
    """Return current asset digests after validating a serviced, single-OS BCD."""
    from installation_records import validate_record
    validate_record(record)
    require(record.get('product') == 'BackToSeven' and
            record.get('boot_layout') == 'standalone-canonical-esp-v1', 'Not a standalone receipt')
    require(isinstance(hashes, dict) and isinstance(files, dict), 'Missing boot-file readback')
    esp = guid(record['esp_guid'])
    child = 'EFI/BackToSeven/Children/' + guid(record['partition_guid']) + '/BCD'
    assets = {(guid(a['partition_guid']), a['path'].casefold()): a for a in record['efi_assets']}
    actual = {(guid(d), p.casefold()): h for (d, p), h in hashes.items()}
    require(len(actual) == len(hashes), 'Case-colliding boot assets')
    contents = {p.casefold(): b for p, b in files.items()}
    require(len(contents) == len(files), 'Case-colliding boot snapshots')
    canonical = assets[(esp, CANON.casefold())]
    original = assets[(esp, child.casefold())]
    require(original['sha256'] == canonical['sha256'], 'Unpaired original BCD')
    require((esp, 'efi/microsoft/boot/bootmgfw.efi') in assets, 'Missing recorded Windows loader')
    result = {}
    for key, asset in assets.items():
        digest = actual.get(key)
        require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), 'Missing boot asset')
        if key != (esp, CANON.casefold()):
            require(digest == asset['sha256'], 'A boot file other than BCD changed')
        result[(guid(asset['partition_guid']), asset['path'])] = digest
    for path in (CANON, child):
        data = contents[path.casefold()]
        require(isinstance(data, bytes) and len(data) <= 16*1024**2 and
                sha(data) == actual[(esp, path.casefold())], 'BCD snapshot does not match fresh hash')
        verify_bcd(data, record['disk_guid'], esp, record['disk_guid'],
                   record['partition_guid'], for_removal=True)
    return result


def recovery_plan(scan, receipt, target, manifest, files):
    """Recover only an already completed erase with unchanged retained files."""
    from installation_records import paired_record, canonical
    require(receipt.get('status') == 'format-complete' and receipt.get('confirmations') == 2,
            'No completed, confirmed format')
    require(scan.get('scan_complete') is True and len(scan['installations']) == 1,
            'Historical erase was not a sole installation')
    row = scan['installations'][0]
    record = paired_record(row['observed'], row['marker'], row['record'])
    require(identity(record) == identity(target) and guid(record['esp_guid']) == guid(target['esp_guid'])
            and all(record[k] == target[k] for k in ('partition_bytes', 'partition_offset')),
            'Erased installation does not match selected partition')
    require(receipt['target'] == row['target'], 'Format receipt and scan disagree')
    ledger = 'EFI/WinDeploy/Records/' + guid(record['partition_guid']) + '.json'
    ledger_sha = sha(canonical(record) + b'\n')
    require(receipt['efi_before'].get(ledger) == ledger_sha and
            dict(path=ledger, before=ledger_sha, after=None) in receipt['changes'],
            'No verified removal of paired ledger')
    require(ledger not in receipt['efi_after'] and ledger not in manifest,
            'An installation record exists; do not recover an older erase')
    # No active picker or other standalone child may share these aliases.
    child = 'efi/backtoseven/children/' + guid(record['partition_guid']) + '/bcd'
    for path in manifest:
        key = path.casefold()
        require(not key.startswith(('efi/windeploy/systemstores/', 'efi/windeploy/records/')) and
                key not in ('efi/windeploy/menu.bin', 'efi/windeploy/catalog.json') and
                (not key.startswith('efi/backtoseven/children/') or key == child),
                'Other boot ownership remains')
    hashes = {(guid(record['esp_guid']), p): h for p, h in manifest.items()}
    current = verified_assets(record, hashes, files)
    changes = []
    for asset in record['efi_assets']:
        require(guid(asset['partition_guid']) == guid(record['esp_guid']), 'Unexpected non-ESP asset')
        path = asset['path']; digest = current[(guid(record['esp_guid']), path)]
        require(receipt['efi_after'].get(path) == digest, 'Retained boot file changed after erase')
        changes.append(dict(path=path, expected=digest, data=None))
    # BCD transaction logs belong to the same proven, inactive standalone store.
    for suffix in ('.LOG', '.LOG1', '.LOG2'):
        path = CANON + suffix
        if path in manifest:
            require(receipt['efi_after'].get(path) == manifest[path], 'BCD log changed after erase')
            changes.append(dict(path=path, expected=manifest[path], data=None))
    return changes


def _protected(path, directory=False):
    st = path.lstat()
    require(st.st_uid == 0 and not st.st_mode & 0o022 and
            (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)),
            'Unprotected management evidence')
    if not directory:
        require(st.st_size <= 8*1024**2, 'Oversized management evidence')


def recover_completed_format(target, manifest, files):
    """Use root-owned local history, never arbitrary user-supplied receipts."""
    if os.geteuid() != 0 or not LOG_ROOT.exists():
        return dict(changes=[], evidence=None)
    # /var is a macOS symlink; use the canonical /private/var path throughout.
    for directory in (LOG_ROOT, *LOG_ROOT.parents):
        _protected(directory, directory=True)
    candidates = []
    for session in LOG_ROOT.iterdir():
        if not re.fullmatch(r'manage-[a-zA-Z0-9_-]+', session.name):
            continue
        try:
            _protected(session, directory=True)
            receipt_file = session/'format-receipt.json'; scan_file = session/'installation-scan.json'
            _protected(receipt_file); _protected(scan_file)
            receipt = json.loads(receipt_file.read_bytes()); scan = json.loads(scan_file.read_bytes())
            changes = recovery_plan(scan, receipt, target, manifest, files)
            candidates.append(dict(changes=changes, evidence=str(session)))
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            continue
    require(not candidates or all(c['changes'] == candidates[0]['changes'] for c in candidates),
            'Conflicting completed-format evidence')
    return candidates[0] if candidates else dict(changes=[], evidence=None)
