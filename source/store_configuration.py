"""Data-only binding of chooser entries to independent canonical BCD stores.

Experimental; not an installer publication path. The firmware and host consume
the same bounded format. Its checksum detects corruption, NOT malicious edits.
The exact menu bytes, ESP identity and ordered Windows identities are bound to
transaction state. Reordering/removing entries requires a separate migration;
an old Active.bin must never be silently applied to the new order.
"""
import hashlib
import struct
import uuid

from boot_catalog import guid
import chooser_config

MAGIC = b'W7DEPC01'
HEADER_SIZE = 112
RECORD_SIZE = 32
MAX_ENTRIES = chooser_config.MAX_ENTRIES


def require(value, message):
    if not value:
        raise ValueError(message)


def encode(menu_bytes, esp_disk, esp_partition, identities):
    entries = chooser_config.decode(menu_bytes)
    require(0 < len(entries) <= MAX_ENTRIES, 'Store configuration requires Windows entries')
    require(type(identities) is list and len(identities) == len(entries),
            'Every menu entry requires one ordered store identity')
    esp_disk, esp_partition = guid(esp_disk), guid(esp_partition)
    body = bytearray()
    seen = set()
    for entry, expected in zip(entries, identities):
        disk, partition = guid(expected['disk_guid']), guid(expected['partition_guid'])
        require(partition == entry['partition_guid'], 'Menu/store order or identity mismatch')
        require(partition != esp_partition, 'Windows partition cannot be the ESP')
        require(partition not in seen, 'Ambiguous repeated Windows partition GUID')
        seen.add(partition)
        boot = entry['boot_partition_guid']
        require(boot in (esp_partition, partition), 'Loader is on an unrelated partition')
        require(entry['requires_ntfs'] == (boot == partition),
                'Independent Windows-partition loader must use the NTFS access profile')
        body.extend(uuid.UUID(disk).bytes_le + uuid.UUID(partition).bytes_le)
    prefix = (MAGIC + struct.pack('<II', 1, len(entries)) +
              uuid.UUID(esp_disk).bytes_le + uuid.UUID(esp_partition).bytes_le +
              hashlib.sha256(menu_bytes).digest())
    return prefix + hashlib.sha256(prefix + body).digest() + body


def decode(data, menu_bytes):
    require(type(data) is bytes and len(data) >= HEADER_SIZE and data[:8] == MAGIC,
            'Invalid store configuration header')
    version, count = struct.unpack_from('<II', data, 8)
    require(version == 1 and 0 < count <= MAX_ENTRIES and
            len(data) == HEADER_SIZE + count * RECORD_SIZE, 'Invalid store configuration length/version')
    require(hashlib.sha256(data[:80] + data[HEADER_SIZE:]).digest() == data[80:112],
            'Invalid store configuration checksum')
    require(hashlib.sha256(menu_bytes).digest() == data[48:80],
            'Store configuration belongs to a different menu')
    identities = []
    for offset in range(HEADER_SIZE, len(data), RECORD_SIZE):
        identities.append(dict(disk_guid=str(uuid.UUID(bytes_le=data[offset:offset+16])),
                               partition_guid=str(uuid.UUID(bytes_le=data[offset+16:offset+32]))))
    disk = str(uuid.UUID(bytes_le=data[16:32]))
    esp = str(uuid.UUID(bytes_le=data[32:48]))
    require(encode(menu_bytes, disk, esp, identities) == data, 'Noncanonical store configuration')
    return dict(esp_disk_guid=disk, esp_partition_guid=esp, identities=identities,
                menu_sha256=data[48:80].hex(), configuration_sha256=hashlib.sha256(data).hexdigest())


def verify_stores(data, menu_bytes, stores, *, for_removal=False):
    """Validate actual BCD structure/devices/references; no byte-search shortcut.

    These are the private snapshots of the canonical ESP BCD, not a loader's
    separately colocated NTFS boot store. No state or file is created here.
    """
    from bcd_semantics import identity
    require(type(for_removal) is bool, 'Invalid boot-store validation purpose')
    config = decode(data, menu_bytes)
    require(type(stores) is list and len(stores) == len(config['identities']),
            'Missing or extra private BCD stores')
    results = []
    for expected, store in zip(config['identities'], stores):
        require(type(store) is bytes and 0 < len(store) <= 2 * 1024 * 1024,
                'BCD store is not a bounded byte sequence')
        results.append(identity(store, config['esp_disk_guid'], config['esp_partition_guid'],
                                expected['disk_guid'], expected['partition_guid'], for_removal=for_removal))
    return dict(configuration=config, stores=results, mutations=False)
