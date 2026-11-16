"""Pure publication plan for the owner's next Win10/Win7 coexistence test.

This private migration profile is intentionally exact: it must not adopt an
unrecognised public boot loader or pretend a legacy installation has ownership
records. It preserves Win7's child loader, BCD, ACPI helper and OpenCore. No I/O.
General discovery/adoption for release is a separate, not-yet-validated path.
"""
import hashlib
import json
from boot_catalog import guid, rebuild
from chooser_config import encode
from installation_records import validate_record, canonical

AUTO = 'e883cafc83e127c09ccec1c6cef96d4cf0b2f39242ba66e68adaa0279c2d7c07'
ORIGINAL = '6470d503fc884f23b515457a03018ece8bf6f6c5bd358f3b5518711ebadb5161'
BCD = 'b7b394069e7a5b2cf26570fcae7767d7d4e031bc31b2c0cc1bfc0e8c63616053'
MARKER = '784bd1fbe529be3c24c82daf84e92ce972792d46ca87a7fb48be899fc1dc10ed'
NTFS_DRIVER = '463bf0b24d58f21d3a9d594a7a4fa68310e1b59e376de0bb5b2dcfb1948d1734'
DISK = 'a7b3c06b-74bd-46c3-8a28-29921964c9ee'
WIN7 = '1f76f485-2580-4d2b-924a-2c8ea0686620'
WIN10 = '64874413-fe59-47f4-b073-de95c716f318'
ESP = 'f5f7be10-32e6-418e-bd32-0b5674d7efa7'
PUBLIC = ('EFI/Microsoft/Boot/bootmgfw.efi', 'EFI/Boot/BOOTX64.EFI')
PINS = {
    PUBLIC[0]: AUTO, PUBLIC[1]: AUTO,
    'EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi': AUTO,
    'EFI/W7DEP-WIN7-AUTO/ALLOW-AUTO.txt': MARKER,
    'EFI/Microsoft/Boot/bootmgfw-w7dep-original.efi': ORIGINAL,
    'EFI/Boot/BOOTX64-W7DEP-ORIGINAL.EFI': ORIGINAL,
    'EFI/Microsoft/Boot/BCD': BCD,
}


def require(value, message):
    if not value: raise ValueError(message)


def manifest_index(manifest):
    result = {}
    for path, digest in manifest.items():
        key = path.casefold()
        require(key not in result, 'Case-colliding EFI files')
        result[key] = (path, digest)
    return result


def private_win7_entry(scan, esp_guid):
    require(scan.get('scan_complete') is True, 'Incomplete Windows discovery')
    rows = scan['installations']
    require(len(rows) == 1, 'Private first-coexistence profile expects exactly one Windows installation')
    row = rows[0]
    observed = row['observed']
    require(guid(observed['disk_guid']) == DISK and guid(observed['partition_guid']) == WIN7
            and guid(esp_guid) == ESP, 'Not the recorded private Win7/EFI identity')
    require(observed.get('windows_verified') is True and observed.get('filesystem') == 'ntfs'
            and observed.get('partition_bytes') == 149835218944
            and observed.get('partition_offset') == 600499552256,
            'Private Win7 geometry/filesystem changed')
    manifest = manifest_index(row['efi_manifest'])
    for path, expected in PINS.items():
        require(manifest.get(path.casefold(), (None, None))[1] == expected,
                'Working Win7 boot dependency changed or missing: ' + path)
    return dict(disk_guid=DISK, partition_guid=WIN7, esp_guid=ESP, boot_partition_guid=ESP,
                title='Windows 7', loader_path='EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi',
                bcd_path='EFI/Microsoft/Boot/BCD', single_os_bcd_verified=True, available=True,
                requires_ntfs=False, hash_locked=True, loader_sha256=AUTO,
                ownership='legacy-origin-unverified', compatibility='recorded-11,5-M370X')


def plan_first_coexistence(scan, record, chooser, ntfs_driver, *, model):
    require(model == 'MacBookPro11,5', 'Private migration is only validated for this 11,5 profile')
    validate_record(record)
    require(record['family'] == 'Windows 10' and guid(record['disk_guid']) == DISK
            and guid(record['partition_guid']) == WIN10 and guid(record['esp_guid']) == ESP
            and record['partition_bytes'] == 249951158272
            and record['partition_offset'] == 750470037504,
            'Not the owner-designated Windows 10 test partition')
    require(type(chooser) is bytes and len(chooser) >= 1024 and chooser[:2] == b'MZ',
            'Missing compiled chooser')
    require(hashlib.sha256(ntfs_driver).hexdigest() == NTFS_DRIVER, 'NTFS firmware driver changed')
    old = private_win7_entry(scan, record['esp_guid'])
    assets = {(guid(a['partition_guid']), a['path'].casefold()): a['sha256'] for a in record['efi_assets']}
    loader = 'EFI/Microsoft/Boot/bootmgfw.efi'
    bcd = 'EFI/Microsoft/Boot/BCD'
    require(all(guid(a['partition_guid']) == WIN10 for a in record['efi_assets']),
            'New Win10 owns only boot files on its own NTFS partition; shared chooser is not exclusive')
    require((WIN10,loader.casefold()) in assets and (WIN10,bcd.casefold()) in assets,
            'Independent Win10 loader/store not inventoried')
    new = dict(disk_guid=DISK, partition_guid=WIN10, esp_guid=ESP, boot_partition_guid=WIN10,
               title='Windows 10', loader_path=loader, bcd_path=bcd, single_os_bcd_verified=True,
               available=True, requires_ntfs=True, hash_locked=False,
               loader_sha256=assets[(WIN10,loader.casefold())], ownership='verified-windeploy')
    catalog = rebuild(dict(schema=1,generation=0,entries=[]), [old,new], scan_complete=True)
    manifest = manifest_index(scan['installations'][0]['efi_manifest'])
    new_files = [
        ('EFI/WinDeploy/OpenNtfsDxe.efi',ntfs_driver),
        ('EFI/WinDeploy/Windows.efi',chooser),
        ('EFI/WinDeploy/Records/'+WIN10+'.json', canonical(record)+b'\n'),
        ('EFI/WinDeploy/catalog.json',canonical(catalog)+b'\n'),
        ('EFI/WinDeploy/menu.bin',encode(catalog['entries'])),
    ]
    changes = []
    for path, data in new_files:
        require(path.casefold() not in manifest, 'Existing chooser needs an update transaction, not first migration')
        changes.append(dict(path=path,expected=None,data=data))
    # Publish both launch aliases LAST. Retain exact original spelling from FAT.
    for path in PUBLIC:
        spelling, digest = manifest[path.casefold()]
        changes.append(dict(path=spelling,expected=digest,data=chooser))
    changed = {c['path'].casefold() for c in changes}
    preserved = {path:digest for path,digest in scan['installations'][0]['efi_manifest'].items()
                 if path.casefold() not in changed}
    return dict(changes=changes, catalog=catalog, preserved=preserved,
                public_name='Windows', set_default_boot=False, modify_existing_bcd=False,
                modify_opencore=False, old_installation_claimed_owned=False,
                hardware_boot_acceptance_pending=True)


def plan_first_system_stores(scan, record, chooser, ntfs_driver, *, model,
                             canonical_win7, canonical_win10):
    """First publication with independent, menu-bound canonical store snapshots.

    Still a pure plan. The caller remains hardware-locked until acceptance.
    Initial publication retains the working canonical Win7 bytes. At boot the
    explicit system-store chooser activates only the selected OS's own store.
    No merged BCD, installation adoption, NVRAM or OpenCore edits.
    """
    from store_publication import first_publication
    plan = plan_first_coexistence(scan, record, chooser, ntfs_driver, model=model)
    require(type(canonical_win7) is bytes and hashlib.sha256(canonical_win7).hexdigest() == BCD,
            'Working Win7 canonical store differs from its verified backup')
    entries = plan['catalog']['entries']
    stores = {WIN7: canonical_win7, WIN10: canonical_win10}
    menu = next(c['data'] for c in plan['changes'] if c['path'] == 'EFI/WinDeploy/menu.bin')
    publication = first_publication(menu, DISK, ESP,
        [dict(disk_guid=e['disk_guid'], partition_guid=e['partition_guid']) for e in entries],
        [stores[e['partition_guid']] for e in entries], WIN7, canonical_win7,
        scan['installations'][0]['efi_manifest'])
    # All verified snapshots/state must exist BEFORE either public alias can
    # enter the new chooser. The transaction backs up both old aliases first.
    plan['changes'][-2:-2] = publication['changes']
    plan['store_activation'] = {k:publication[k] for k in
        ('active_index', 'configuration_sha256', 'changes_canonical_bcd', 'merges_bcd_entries')}
    plan['boot_time_canonical_activation'] = True
    return plan
