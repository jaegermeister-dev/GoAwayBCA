"""Make an independent boot-store COPY for a positively identified Windows.

Never edits the source store. Unknown devices/references fail validation before
publication. Existing per-OS options and recovery/resume objects are preserved.
"""
import ctypes as c
import os
from pathlib import Path
import struct
import uuid
from bcd import BOOTMGR, device_blob
from bcd_registry import Registry
from bcd_semantics import identity, text
from bcd_references import names, ABSENT_INHERIT, NULL_GUID


def require(ok, message):
    if not ok:
        raise ValueError(message)


def specification(data, disk, esp, windows_disk, windows):
    hive = Registry(data)
    kinds = {}
    for path in hive.keys:
        if len(path) == 2 and path[0] == 'objects':
            require(path[1] == '{' + str(uuid.UUID(path[1])) + '}', 'Noncanonical BCD object')
            kind, value = hive.value(path + ('description',), 'type')
            require(kind == 4 and len(value) == 4, 'Invalid BCD object description')
            kinds[path[1]] = struct.unpack('<I', value)[0]
    require(kinds.get(BOOTMGR) == 0x10100002, 'No Windows EFI boot manager')
    def element(obj, field):
        return hive.value(('objects', obj, 'elements', field), 'element')
    descriptor = (3, device_blob(windows_disk, windows))
    candidates = []
    for obj, kind in kinds.items():
        if kind != 0x10200003:
            continue
        if element(obj, '11000001') == descriptor and element(obj, '21000001') == descriptor:
            require(text(element(obj, '12000002')).casefold() == r'\windows\system32\winload.efi',
                    'Unsupported Windows loader path')
            require(text(element(obj, '22000002')).casefold() == r'\windows', 'Unsupported Windows directory')
            candidates.append(obj)
    require(len(candidates) == 1, 'No unique normal loader for this Windows partition in the saved BCD')
    selected = candidates[0]
    replacements = {
        '11000001': (3, device_blob(disk, esp)),
        '12000002': (1, ('\\EFI\\Microsoft\\Boot\\bootmgfw.efi\0').encode('utf-16le')),
        '23000003': (1, (selected + '\0').encode('utf-16le')),
        '24000001': (7, (selected + '\0\0').encode('utf-16le')),
    }
    # A pending first-boot/setup/recovery sequence is not discarded. Its exact
    # referenced applications have to pass the per-installation validator.
    keep, queue = set(), [BOOTMGR, selected]
    while queue:
        obj = queue.pop()
        if obj in keep:
            continue
        require(obj in kinds, 'Missing BCD object needed by this installation')
        keep.add(obj)
        for path, values in hive.keys.items():
            if len(path) != 4 or path[:3] != ('objects', obj, 'elements'):
                continue
            number = int(path[3], 16)
            value = replacements.get(path[3], values.get('element')) if obj == BOOTMGR else values.get('element')
            require(value is not None, 'Missing BCD element')
            if ((number >> 24) & 15) in (3, 4):
                for target in names(value, number):
                    if target not in kinds and (target in ABSENT_INHERIT or target == NULL_GUID):
                        continue
                    queue.append(target)
            elif ((number >> 24) & 15) == 1:
                kind, raw = value
                if kind == 3 and len(raw) >= 16 and raw[:16] != bytes(16):
                    # Recovery device header references its ramdisk options.
                    queue.append('{' + str(uuid.UUID(bytes_le=raw[:16])) + '}')
    require(len(keep) <= 512, 'Oversized per-installation BCD')
    changes = {('objects', BOOTMGR, 'elements', field): {'element': value}
               for field, value in replacements.items()}
    expected = {path: values for path, values in hive.keys.items()
                if not (len(path) >= 2 and path[0] == 'objects' and path[1] not in keep)}
    for path, value in changes.items():
        require(path in expected, 'Expected boot-manager field is missing; preserve source')
        require(set(expected[path]) == {'element'}, 'Unexpected boot-manager field values')
        expected[path] = value
    return dict(remove=sorted(set(kinds) - keep), changes=changes, expected=expected, selected=selected)


def isolate(data, disk, esp, windows_disk, windows, directory, library):
    """Writes only newly created files under an already private session directory."""
    from offline_keyboard import Hive, Value
    spec = specification(data, disk, esp, windows_disk, windows)
    folder = Path(directory)
    folder.mkdir()  # refuse reuse/overwrite of any existing evidence
    source, output = folder/'source.bcd', folder/'isolated.bcd'
    source.write_bytes(data)
    hive = Hive(library, source, True)
    delete = hive.lib.hivex_node_delete_child
    delete.argtypes = [c.c_void_p, c.c_size_t]
    delete.restype = c.c_int
    try:
        for obj in spec['remove']:
            require(delete(hive.handle, hive.node(('Objects', obj))) == 0, 'Could not isolate copied BCD')
        for path, values in spec['changes'].items():
            kind, raw = values['element']
            buffer = c.create_string_buffer(raw)
            value = Value(b'Element', kind, len(raw), c.cast(buffer, c.c_void_p))
            require(hive.lib.hivex_node_set_value(hive.handle, hive.node(path), c.byref(value), 0) == 0,
                    'Could not set copied boot-manager field')
        require(hive.lib.hivex_commit(hive.handle, os.fsencode(output), 0) == 0, 'Could not save independent BCD')
    finally:
        hive.close()
    result = output.read_bytes()
    require(Registry(result).keys == spec['expected'], 'BCD copy changed an unexpected value')
    identity(result, disk, esp, windows_disk, windows)
    require(source.read_bytes() == data, 'Copied source BCD changed')
    return result
