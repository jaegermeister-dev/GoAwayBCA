"""Bounded firmware-menu serialization. No disk writes or boot-default changes.

The checksum detects accidental corruption, not hostile configuration edits.
Each child is selected by its boot-partition GPT GUID and file path, never by
an assumed disk number. Microsoft loaders may be serviced by Windows, so only
our own exact wrapper/fixture is hash-locked; ordinary stock loaders are not.
"""
import hashlib
import re
import struct
import uuid
from boot_catalog import guid, efi_path

MAGIC = b'W7DEP001'
MAX_ENTRIES = 16
RECORD_SIZE = 772
HEADER_SIZE = 44
AVAILABLE, NTFS, HASH_LOCKED = 1, 2, 4


def require(value, message):
    if not value: raise ValueError(message)


def field(value, units, path=False):
    require(type(value) is str and value and all(32 <= ord(c) < 127 for c in value),
            'This firmware format requires printable ASCII labels and EFI paths')
    if path:
        efi_path(value)
        require(not value.casefold().startswith(('efi/oc/','efi/apple/')) and
                value.casefold() != 'efi/windeploy/windows.efi', 'Not a Windows child loader')
        require(value.lower().endswith('.efi'), 'Expected an EFI executable')
        value = '\\' + value.replace('/', '\\')
    encoded = value.encode('utf-16le')
    require(len(encoded) < units*2, 'Firmware text field too long')
    return encoded + bytes(units*2-len(encoded))


def encode(entries):
    require(type(entries) is list and 0 <= len(entries) <= MAX_ENTRIES, 'Too many chooser entries')
    body = bytearray()
    seen = set()
    for item in entries:
        boot = guid(item['boot_partition_guid'])
        windows = guid(item['partition_guid'])
        location = (boot, efi_path(item['loader_path']).casefold())
        require(location not in seen, 'Duplicate boot entry')
        seen.add(location)
        for name in ('available','requires_ntfs','hash_locked'):
            require(type(item.get(name)) is bool, 'Firmware flags must be explicit')
        require(not item['requires_ntfs'] or boot == windows,
                'NTFS child boot files must be on its own Windows partition')
        expected = item.get('loader_sha256', '')
        require(type(expected) is str and re.fullmatch('[0-9a-f]{64}',expected), 'Invalid loader digest')
        flags = (AVAILABLE if item['available'] else 0) | (NTFS if item['requires_ntfs'] else 0) | (HASH_LOCKED if item['hash_locked'] else 0)
        body.extend(struct.pack('<I',flags))
        body.extend(uuid.UUID(boot).bytes_le)
        body.extend(uuid.UUID(windows).bytes_le)
        body.extend(bytes.fromhex(expected))
        body.extend(field(item['title'],96))
        body.extend(field(item['loader_path'],256,path=True))
    return MAGIC+struct.pack('<I',len(entries))+hashlib.sha256(body).digest()+body


def decode(data):
    require(type(data) is bytes and len(data) >= HEADER_SIZE and data[:8] == MAGIC, 'Invalid chooser header')
    count = struct.unpack_from('<I',data,8)[0]
    body = data[HEADER_SIZE:]
    require(count <= MAX_ENTRIES and len(body) == count*RECORD_SIZE and
            hashlib.sha256(body).digest() == data[12:44], 'Invalid chooser length/checksum')
    result = []
    def text(raw):
        text = raw.decode('utf-16le')
        end = text.find('\0')
        require(end >= 0 and not text[end:].strip('\0'), 'Invalid firmware string terminator')
        return text[:end]
    for offset in range(0,len(body),RECORD_SIZE):
        row = body[offset:offset+RECORD_SIZE]
        flags = struct.unpack_from('<I',row)[0]
        require(flags & ~7 == 0, 'Unknown firmware flags')
        path = text(row[260:772])
        require(path.startswith('\\'), 'Invalid firmware loader path')
        result.append(dict(boot_partition_guid=str(uuid.UUID(bytes_le=row[4:20])),
            partition_guid=str(uuid.UUID(bytes_le=row[20:36])),loader_sha256=row[36:68].hex(),
            title=text(row[68:260]), loader_path=path[1:].replace('\\','/'),
            available=bool(flags&AVAILABLE),requires_ntfs=bool(flags&NTFS),hash_locked=bool(flags&HASH_LOCKED)))
    require(encode(result) == data, 'Noncanonical chooser configuration')
    return result
