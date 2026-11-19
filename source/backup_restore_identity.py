"""Adapt copies of restored filesystem/registry identity, never live devices.

The caller supplies an already verified private image and copied registry hives.
GPT MountedDevices identifiers are DMIO:ID: plus the partition GUID in little
endian form. No installed registry or original backup is opened for writing.
"""
import ctypes as c
import hashlib
import os
from pathlib import Path
import re
import stat
import struct
import uuid

from offline_keyboard import Hive, Value


def require(value, message):
    if not value:
        raise ValueError(message)


def regular(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path and
            stat.S_ISREG(path.lstat().st_mode), 'Use a canonical private regular file for restore preparation.')
    return path


def gpt_identifier(partition_guid):
    return b'DMIO:ID:' + uuid.UUID(partition_guid).bytes_le


def system_letter(kind, data):
    require(kind in (1, 2) and len(data) <= 32768 and len(data) % 2 == 0,
            'The backed-up Windows system path is invalid.')
    root = data.decode('utf-16le')
    require(root.endswith('\0') and '\0' not in root[:-1] and
            re.fullmatch(r'[A-Za-z]:\\[^\x00-\x1f]+', root[:-1]),
            'The backed-up Windows system drive letter could not be determined.')
    return root[0].upper()


def mappings(values, letter, source_guid, destination_guid):
    """Preserve other volumes; replace only this OS's source/target bindings."""
    require(re.fullmatch('[A-Z]', letter), 'Invalid Windows system drive letter.')
    require(type(values) is dict and len(values) < 100000, 'Invalid Windows drive mappings.')
    names = {name.casefold(): name for name in values}
    require(len(names) == len(values), 'Ambiguous Windows drive mapping names.')
    source, target = gpt_identifier(source_guid), gpt_identifier(destination_guid)
    drive = '\\DosDevices\\' + letter + ':'
    existing = names.get(drive.casefold())
    require(existing is None or values[existing] == (3, source),
            'The saved Windows system-drive mapping does not match the backed-up partition. '
            'No restore destination was changed.')
    result = {}
    removed = []
    for name, value in values.items():
        require(type(name) is str and type(value) is tuple and len(value) == 2 and
                type(value[0]) is int and type(value[1]) is bytes, 'Invalid Windows drive mapping value.')
        if value == (3, source):
            result[name] = (3, target)
        elif source != target and value == (3, target):
            # The previous contents of the restore destination are being
            # replaced. Do not keep their old drive letter/volume GUID alias.
            removed.append(name)
        else:
            result[name] = value
    result[existing or drive] = (3, target)
    return result, removed


def prepare_hive(library, source_system, source_software, output, source_guid, destination_guid):
    source_system, source_software = regular(source_system), regular(source_software)
    output = Path(output)
    require(output.is_absolute() and output.resolve() == output and output.parent.is_dir() and
            not output.exists() and not output.is_symlink(), 'Use a new private restored SYSTEM hive.')
    originals = [p.read_bytes() for p in (source_system, source_software)]
    software = Hive(library, source_software)
    try:
        letter = system_letter(*software.value(('Microsoft', 'Windows NT', 'CurrentVersion'), 'SystemRoot'))
    finally:
        software.close()
    hive = Hive(library, source_system, True)
    try:
        before = hive.snapshot()
        root = hive.lib.hivex_root(hive.handle)
        node = hive.lib.hivex_node_get_child(hive.handle, root, b'MountedDevices')
        values = {}
        if node:
            for handle in hive.handles('values', node):
                name = hive.name('value_key', handle)
                require(name not in values, 'Duplicate drive mapping in the saved registry.')
                values[name] = hive.read_value(handle)
        else:
            add = hive.lib.hivex_node_add_child
            add.argtypes = [c.c_void_p, c.c_size_t, c.c_char_p]; add.restype = c.c_size_t
            node = add(hive.handle, root, b'MountedDevices')
            require(node, 'Cannot prepare the restored Windows drive mappings.')
        changed, removed = mappings(values, letter, source_guid, destination_guid)
        buffers = [c.create_string_buffer(data) for kind, data in changed.values()]
        entries = (Value * len(changed))(*[
            Value(name.encode('utf8'), kind, len(data), c.cast(buffer, c.c_void_p))
            for (name, (kind, data)), buffer in zip(changed.items(), buffers)])
        function = hive.lib.hivex_node_set_values
        function.argtypes = [c.c_void_p, c.c_size_t, c.c_size_t, c.POINTER(Value), c.c_int]
        function.restype = c.c_int
        require(function(hive.handle, node, len(changed), entries, 0) == 0,
                'Cannot prepare the restored Windows drive mappings.')
        # Preserve the original key spelling as hive enumeration is case-sensitive.
        path = next((p for p in before if len(p) == 1 and p[0].casefold() == 'mounteddevices'), ('MountedDevices',))
        expected = {p: dict(v) for p, v in before.items()}
        expected[path] = {name: (kind, len(data), hashlib.sha256(data).hexdigest())
                          for name, (kind, data) in changed.items()}
        require(hive.snapshot() == expected, 'Unrelated registry data changed during drive-mapping preparation.')
        require(not output.exists() and hive.lib.hivex_commit(hive.handle, os.fsencode(output), 0) == 0,
                'Cannot save the restored Windows drive mappings.')
    finally:
        hive.close()
    check = Hive(library, output)
    try:
        require(check.snapshot() == expected, 'Restored drive mappings failed readback verification.')
    finally:
        check.close()
    require([p.read_bytes() for p in (source_system, source_software)] == originals,
            'A source registry copy changed during restore preparation.')
    return dict(system_drive=letter+':', source_sha256=hashlib.sha256(originals[0]).hexdigest(),
                output_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                destination_partition_guid=str(uuid.UUID(destination_guid)),
                removed_destination_aliases=removed, unrelated_registry_preserved=True)


def boot_geometry(stream):
    """Validate primary and alternate sectors of a regular NTFS image."""
    node = os.fstat(stream.fileno())
    require(stat.S_ISREG(node.st_mode), 'Restore identity preparation accepts regular files only.')
    stream.seek(0); first = stream.read(512)
    require(len(first) == 512 and first[3:11] == b'NTFS    ' and first[510:512] == b'\x55\xaa',
            'The private restore image has an invalid NTFS boot sector.')
    sector = struct.unpack_from('<H', first, 11)[0]
    require(sector in (512, 4096) and node.st_size % sector == 0, 'Unexpected restore-image sector size.')
    total = struct.unpack_from('<Q', first, 40)[0]
    require(0 < total <= node.st_size//sector-1, 'The NTFS filesystem extends outside its private restore image.')
    alternate = node.st_size-sector
    stream.seek(0); primary = stream.read(sector)
    stream.seek(alternate)
    require(stream.read(sector) == primary, 'The private NTFS image has inconsistent alternate boot data.')
    return dict(bytes=node.st_size, sector_size=sector, alternate_offset=alternate,
                serial=struct.unpack_from('<Q', primary, 72)[0], primary=primary,
                volume_sectors=total)


def prepare_volume(image, destination_offset, expected_sector, new_serial=None):
    """Update only this private image's serial and partition-offset fields.

    Like upstream ntfslabel, preserve the remainder of both boot sectors.
    NTFS's 32-bit BPB hidden-sector field wraps on GPT offsets above 2^32;
    EFI boot addressing uses the separately regenerated GPT/BCD identifiers.
    """
    image = regular(image)
    require(type(destination_offset) is int and destination_offset >= 0 and
            destination_offset % expected_sector == 0, 'Unaligned restore partition offset.')
    with image.open('r+b') as stream:
        original = boot_geometry(stream)
        require(original['sector_size'] == expected_sector, 'The private image has the wrong destination sector size.')
        if new_serial is None:
            new_serial = int.from_bytes(os.urandom(8), 'little')
        require(type(new_serial) is int and 0 < new_serial < 2**64 and new_serial != original['serial'],
                'A distinct restored NTFS serial number is required.')
        changed = bytearray(original['primary'])
        struct.pack_into('<I', changed, 28, (destination_offset//expected_sector) & 0xffffffff)
        struct.pack_into('<Q', changed, 72, new_serial)
        for offset in (0, original['alternate_offset']):
            stream.seek(offset); require(stream.write(changed) == len(changed), 'Incomplete NTFS identity write.')
        stream.flush(); os.fsync(stream.fileno())
        verified = boot_geometry(stream)
        require(verified['primary'] == changed, 'Restored NTFS identity failed readback.')
    return dict(original_serial='%016x' % original['serial'], restored_serial='%016x' % new_serial,
                logical_sector_size=expected_sector, destination_offset=destination_offset,
                backup_boot_sector_verified=True, physical_device_changed=False)
