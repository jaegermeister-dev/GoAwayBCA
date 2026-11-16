"""Single-install Windows EFI publication, no chooser or default-boot changes.

The installation receipt pairs the selected GPT partition with its owned boot
files. The engine identifier and the application name are separate fields.
"""
import hashlib
import json
import re
from install_windows7 import require
from boot_catalog import efi_path
from installation_records import canonical, validate_record
from boot_dependencies import optional_memory_diagnostic
import reset_menu
import menu_lifecycle as lifecycle
from efi_transaction import node


def cleanup(snapshot_root, existing, record, other_installations, recovered_changes=()):
    """Plan exact, backed-up stale files; never operate on the live ESP here.

    Caller must have completed its Windows scan with zero installations. Menu
    aliases need known binary hashes; BCD needs an ESP-bound active snapshot;
    per-installation files need a validated ledger plus matching content hashes.
    Diagnostics and unknown files remain. EFITransaction performs the combined
    cleanup/publication only after both confirmations and image application.
    """
    require(not other_installations, 'An existing Windows installation must be preserved.')
    original = lifecycle.indexed(existing)
    require(all(c['data'] is None and original.get(c['path'].casefold()) ==
                (c['path'], c['expected']) for c in recovered_changes), 'Recovery evidence changed')
    recovered_keys = {c['path'].casefold() for c in recovered_changes}
    existing = {p:h for p,h in existing.items() if p.casefold() not in recovered_keys}
    from legacy_boot_cleanup import plan as legacy_plan
    legacy=legacy_plan(snapshot_root,existing,record['disk_guid'],record['esp_guid'])
    excluded={c['path'].casefold() for c in legacy}
    existing={p:h for p,h in existing.items() if p.casefold() not in excluded}
    files = reset_menu.snapshot(snapshot_root, existing)
    proposed = reset_menu.plan(files, existing, record['disk_guid'], record['esp_guid'])
    require(not proposed['needs_review'],
            'Existing Windows boot ownership could not be verified. Files were preserved; no partition was formatted.')
    changes = {c['path'].casefold(): c for c in proposed['changes']}
    indexed = lifecycle.indexed(existing)
    for path in existing:
        if not re.fullmatch(r'EFI/WinDeploy/Records/[0-9a-f-]{36}\.json', path, re.I):
            continue
        entry = node(snapshot_root, path)
        require(entry.stat().st_size <= 1024**2, 'Oversized installation record')
        data = entry.read_bytes()
        require(lifecycle.sha(data) == existing[path], 'Installation record changed during review')
        try:
            old = validate_record(json.loads(data))
        except (ValueError, KeyError, TypeError):
            continue  # Never guess ownership from a filename alone.
        if (old['disk_guid'].lower() != record['disk_guid'].lower() or
                old['esp_guid'].lower() != record['esp_guid'].lower() or
                path.rsplit('/', 1)[-1][:-5].lower() != old['partition_guid'].lower()):
            continue
        for asset in old['efi_assets']:
            key = asset['path'].casefold()
            if (asset['partition_guid'].lower() != record['esp_guid'].lower() or
                    not key.startswith(('efi/microsoft/boot/', 'efi/w7dep-win7-auto/'))):
                continue
            found = indexed.get(key)
            if found and found[1] == asset['sha256']:
                changes[key] = dict(path=found[0], expected=found[1], data=None)
        # Earlier builds could restore the prepared original at the public
        # Microsoft alias. Its exact pinned bytes, paired saved-original asset
        # and ESP-bound menu state establish that specific legacy configuration.
        # Never generalise this exception to arbitrary Microsoft/OpenCore files.
        from win7_accepted_efi import ORIGINAL_SHA
        public = indexed.get('efi/microsoft/boot/bootmgfw.efi')
        saved = indexed.get('efi/microsoft/boot/bootmgfw-w7dep-original.efi')
        if (proposed['canonical_removed'] and public and saved and
                public[1] == saved[1] == ORIGINAL_SHA and
                'efi/microsoft/boot/bootmgfw-w7dep-original.efi' in changes):
            changes[public[0].casefold()] = dict(path=public[0],expected=public[1],data=None)
        changes[path.casefold()] = dict(path=path, expected=existing[path], data=None)
    return list(recovered_changes)+legacy+list(changes.values())


def publication(existing, files, record, other_installations, cleanup_changes=()):
    require(not other_installations,
            'This installation method prepares one Windows installation. '
            'An existing Windows installation was found; no partition has been formatted. '
            'Use the separate experimental boot-menu option to review coexistence.')
    original = lifecycle.indexed(existing)
    removed = {}
    for change in cleanup_changes:
        key = efi_path(change['path']).casefold()
        require(key not in removed and change['data'] is None and
                original.get(key) == (change['path'], change['expected']), 'Invalid stale-file cleanup plan')
        removed[key] = change
    existing = {p: h for p, h in existing.items() if p.casefold() not in removed}
    indexed = {}
    for path, digest in existing.items():
        if not path.casefold().startswith('efi/'):
            continue  # Preserve volume metadata outside the EFI namespace.
        key = efi_path(path).casefold()
        require(key not in indexed, 'Case-colliding existing EFI files')
        indexed[key] = (path, digest)
    # Diagnostic logs retained by the verified reset do not activate a menu.
    require(not any((k.startswith('efi/windeploy/systemstores/') and not re.fullmatch(
                    r'efi/windeploy/systemstores/log-[0-9a-f]{16}-[0-9]+-[012]\.bin', k)) or
                    k.startswith('efi/windeploy/system/') or
                    k.startswith('efi/windeploy/stores/') or
                    k.endswith('/catalog.json') or k.endswith('/menu.bin')
                    for k in indexed),
            'Unverified legacy Windows boot-menu files remain. Files were preserved; no partition was formatted.')
    changes, owned, preserved = [], [], []
    seen = set()
    for path, data in sorted(files.items()):
        key = efi_path(path).casefold()
        require(key not in seen, 'Duplicate staged EFI path')
        seen.add(key)
        require(key.startswith(('efi/microsoft/boot/', 'efi/boot/', 'efi/w7dep-win7-auto/')) or
                key == ('efi/backtoseven/children/'+record['partition_guid']+'/bcd').casefold(),
                'Unexpected standalone Windows EFI destination')
        digest = hashlib.sha256(data).hexdigest()
        old = indexed.get(key)
        # OpenCore commonly owns this fallback path. Leave ALL existing
        # fallback files alone and use Microsoft/Boot/bootmgfw.efi for Windows.
        if key.startswith('efi/boot/') and old:
            preserved.append(path)
            continue
        if old:
            if old[1] != digest and optional_memory_diagnostic(path):
                preserved.append(path)
                continue
            require(old[1] == digest and not key.endswith('/bcd'),
                    'Existing Windows boot file requires ownership review before '
                    'installation: ' + path + '. No file was replaced.')
            preserved.append(path)
            continue
        previous = removed.pop(key, None)
        changes.append(dict(path=previous['path'] if previous else path,
                            expected=previous['expected'] if previous else None, data=data))
        owned.append(dict(partition_guid=record['esp_guid'], path=path,
                          sha256=digest, exclusive=True))
    record['efi_assets'] = owned
    record['product'] = 'BackToSeven'
    record['product_version'] = '0.1.0-test.4'
    record['boot_layout'] = 'standalone-canonical-esp-v1'
    ledger = 'EFI/WinDeploy/Records/' + record['partition_guid'] + '.json'
    require(ledger.casefold() not in indexed, 'Existing installation ownership record must be reviewed')
    previous = removed.pop(ledger.casefold(), None)
    # Keep the paired record last; caller refreshes its final model evidence.
    changes = list(removed.values()) + changes
    changes.append(dict(path=previous['path'] if previous else ledger,
                        expected=previous['expected'] if previous else None, data=canonical(record) + b'\n'))
    require(changes and owned, 'No standalone Windows boot files were prepared')
    return dict(changes=changes, preserved=preserved, record=record,
                stale_files=[c['path'] for c in cleanup_changes])
