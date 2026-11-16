"""Pure first-publication plan for verified independent canonical BCD snapshots.

Not wired into the production installer. No device/file writes, no BCD merging,
and no assertion that a caller's firmware can activate this format. Publishing
the plan requires the normal confirmed, backed-up EFI transaction. The active
canonical store is not changed by first publication.
"""
import hashlib
import struct
from boot_catalog import guid
from chooser_config import decode as decode_menu
from store_configuration import encode, verify_stores

PREFIX = 'EFI/WinDeploy/SystemStores/'


def require(value, message):
    if not value: raise ValueError(message)


def state_bytes(configuration, active):
    require(type(active) is int and 0 <= active < 16, 'Invalid initial active index')
    body = b'W7DEPS01' + struct.pack('<QI', 0, active) + bytes(4) + hashlib.sha256(configuration).digest()
    return body + hashlib.sha256(body).digest()


def first_publication(menu, esp_disk, esp_partition, identities, stores, active_partition,
                      canonical, existing_manifest):
    """Return new-only store files; caller must publish its chooser aliases last.

    A changed/reordered catalog needs a migration, never this initializer. Even
    an unknown file anywhere in the managed namespace prevents initialization.
    That is a configuration collision check, NOT an installation-ownership ban.
    """
    config = encode(menu, esp_disk, esp_partition, identities)
    verified = verify_stores(config, menu, stores)
    active_partition = guid(active_partition)
    matches = [i for i, item in enumerate(decode_menu(menu)) if item['partition_guid'] == active_partition]
    require(len(matches) == 1, 'Current canonical store must identify one menu entry')
    active = matches[0]
    require(type(canonical) is bytes and canonical == stores[active],
            'Initial private snapshot differs from the current canonical store')
    require(type(existing_manifest) is dict and all(type(path) is str for path in existing_manifest),
            'Complete existing EFI file manifest required')
    lower = [path.casefold() for path in existing_manifest]
    require(len(lower) == len(set(lower)), 'Case-colliding existing EFI files')
    observed = {path.casefold():digest for path,digest in existing_manifest.items()}
    require(observed.get('efi/microsoft/boot/bcd') == hashlib.sha256(canonical).hexdigest(),
            'Current canonical BCD does not match the existing EFI manifest')
    require(not any(path == PREFIX.rstrip('/').casefold() or path.startswith(PREFIX.casefold()) for path in lower),
            'Existing store namespace requires an update/recovery transaction')
    files = [(PREFIX+'Private-%d.BCD'%i, store) for i, store in enumerate(stores)]
    files += [(PREFIX+'Configuration.bin', config), (PREFIX+'Active.bin', state_bytes(config, active))]
    return dict(changes=[dict(path=path, expected=None, data=data) for path, data in files],
        active_index=active, configuration_sha256=hashlib.sha256(config).hexdigest(),
        verified=verified, changes_canonical_bcd=False, merges_bcd_entries=False,
        set_default_boot=False, modifies_opencore=False, hardware_acceptance_pending=True)
