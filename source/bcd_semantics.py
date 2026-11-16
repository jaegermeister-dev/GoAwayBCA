"""Read-only identity checks for WinDeploy single-installation BCD stores.

Deliberately conservative: unknown boot applications/device forms do not pass.
Does not rewrite an unfamiliar store or grant authority to replace its files.
"""
import struct
import uuid
import re

from bcd import BOOTMGR, device_blob
from bcd_registry import Registry
from bcd_store_sanity import require
import bcd_references


def text(value, kind=1):
    actual, data = value
    require(actual == kind and len(data) % 2 == 0, 'Unexpected BCD string type')
    result = data.decode('utf-16le', 'strict')
    require(result.endswith('\0'), 'Unterminated BCD string')
    if kind == 1:
        result = result.rstrip('\0')
        require('\0' not in result, 'Embedded BCD string terminator')
        return result
    return result[:-1]


def recovery_sdi_path(path):
    lower = path.casefold()
    require(lower == r'\recovery\windowsre\winre.wim' or
            re.fullmatch(r'\\recovery\\[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\\winre\.wim', lower),
            'Unexpected recovery image path')
    return path.rsplit('\\', 1)[0] + r'\boot.sdi'


def management_recovery_sdi_path(path):
    """Validate a same-volume WinRE location for removal, not firmware boot.

    REAgentC allows a configured directory, so the firmware's tested-directory
    whitelist is not an ownership rule. Never follow this path or edit WinRE.
    The caller still proves the ramdisk and SDI both name the selected GPT
    volume and requires an exact matching SDI directory and WinPE object.
    """
    require(isinstance(path, str) and 0 < len(path) <= 1024 and
            path.startswith('\\') and not path.startswith('\\\\') and
            not any(ord(c) < 32 or c in '/:"<>|?*' for c in path),
            'Unsafe recovery image path')
    parts = path[1:].split('\\')
    require(all(p and p not in ('.', '..') and not p.endswith((' ', '.')) for p in parts)
            and parts[-1].casefold() == 'winre.wim', 'Unsafe recovery image path')
    return path.rsplit('\\', 1)[0] + r'\boot.sdi'


def identity(data, esp_disk, esp_partition, windows_disk, windows_partition, *, for_removal=False):
    require(type(for_removal) is bool, 'Invalid boot-store validation purpose')
    hive = Registry(data)
    objects = {}
    for path in hive.keys:
        if len(path) == 2 and path[0] == 'objects':
            name = path[1]
            require(name.startswith('{') and name.endswith('}') and
                    '{' + str(uuid.UUID(name)) + '}' == name, 'Invalid BCD object name')
            kind, raw = hive.value(path + ('description',), 'Type')
            require(kind == 4 and len(raw) == 4, 'Invalid BCD object type')
            objects[name] = struct.unpack('<I', raw)[0]
    require(objects.get(BOOTMGR) == 0x10100002, 'Missing Windows EFI boot manager')
    def element(obj, number):
        return hive.value(('Objects', obj, 'Elements', number), 'Element')
    loader = text(element(BOOTMGR, '23000003')).casefold()
    require(objects.get(loader) == 0x10200003, 'BCD default is not a Windows OS loader')
    references=bcd_references.validate(objects,{(path[1],path[3]):values['element']
        for path,values in hive.keys.items() if len(path)==4 and path[0]=='objects'
        and path[2]=='elements'},BOOTMGR,loader)
    # Firmware entries can be reflected into an exported Windows BCD. Preserve
    # them verbatim; they are not selected by this boot manager's display order.
    require(all(kind in (0x10100001, 0x10100002, 0x101fffff, 0x10200003,
                         0x10200004, 0x30000000) or kind >> 28 == 2
                for kind in objects.values()), 'Unrecognised BCD boot application')
    require([name for name, kind in objects.items() if kind == 0x10100002] == [BOOTMGR],
            'Multiple Windows boot managers in one store')
    order = text(element(BOOTMGR, '24000001'), 7)
    require(order.endswith('\0') and order[:-1].casefold() == loader,
            'BCD display order includes another installation')
    require(text(element(BOOTMGR, '12000002')).casefold() == r'\efi\microsoft\boot\bootmgfw.efi',
            'Unexpected Windows boot manager path')
    require(text(element(loader, '12000002')).casefold() == r'\windows\system32\winload.efi',
            'Unexpected Windows OS loader path')
    require(text(element(loader, '22000002')).casefold() == r'\windows', 'Unexpected Windows root')
    system = device_blob(esp_disk, esp_partition)
    windows = device_blob(windows_disk, windows_partition)
    require(system != windows, 'Canonical profile requires a separate system partition')
    require(element(BOOTMGR, '11000001') == (3, system), 'BCD system device does not match the ESP')
    require(element(loader, '11000001') == (3, windows) and
            element(loader, '21000001') == (3, windows), 'Default OS device is not this Windows partition')
    recovery_count = 0
    def recovery_device(value):
        # Explicit observed Windows RE descriptor profile, not a byte search or
        # catch-all device parser. Unknown future encodings stop activation.
        kind, raw = value
        require(kind == 3 and 142 <= len(raw) <= 4096 and len(raw) % 2 == 0,
                'Unsupported recovery device size/type')
        options = '{' + str(uuid.UUID(bytes_le=raw[:16])) + '}'
        require(objects.get(options) == 0x30000000, 'Recovery ramdisk options are absent')
        require(struct.unpack_from('<5I', raw, 16) == (0, 1, len(raw)-16, 0, 3) and
                raw[36:56] == bytes(20) and
                struct.unpack_from('<3I', raw, 56) == (1, len(raw)-56, 5),
                'Unknown recovery file-device encoding')
        require(raw[68:140] == windows[16:], 'Recovery image belongs to another volume')
        path = text((1, raw[140:])).casefold()
        sdi_path = (management_recovery_sdi_path if for_removal else recovery_sdi_path)(path)
        require(element(options, '31000003') == (3, windows), 'Recovery SDI belongs to another volume')
        require(text(element(options, '32000004')).casefold() == sdi_path,
                'Recovery SDI and image directories differ')
        return True
    for obj, kind in objects.items():
        if kind in (0x10100001, 0x101fffff):
            # Firmware mirrors may name macOS, Linux or disconnected devices.
            # Windows references cannot select/inherit these objects (checked above).
            # Preserve their bytes without interpreting their device descriptors.
            continue
        if kind in (0x10200003, 0x10200004):
            a, b = element(obj, '11000001'), element(obj, '21000001')
            require(a == b, 'BCD loader uses different OS/device partitions')
            if a != (3, windows):
                require(kind == 0x10200003 and obj != loader and recovery_device(a),
                        'BCD OS device belongs to another partition')
                require(element(obj, '26000022') == (3, b'\x01'), 'Recovery loader is not marked WinPE')
                recovery_count += 1
            elif kind == 0x10200003:
                require(obj == loader, 'Another regular Windows loader is present')
            else:
                require(text(element(obj, '12000002')).casefold() == r'\windows\system32\winresume.efi',
                        'Unexpected resume application path')
        # Device-valued elements on any object must be one of these exact GPT
        # descriptors. A matching byte sequence in an unrelated cell is useless.
        for path, values in hive.keys.items():
            if len(path) == 4 and path[:3] == ('objects', obj, 'elements'):
                number = int(path[3], 16)
                if (number >> 24) & 0xf == 1:
                    value = values.get('element')
                    require(value in ((3, system), (3, windows)) or
                            (kind == 0x10200003 and obj != loader and recovery_device(value)),
                            'BCD contains an unsupported or foreign device element')
    return dict(status='single-installation-identity-verified', loader=loader,
                recovery_loaders=recovery_count, object_count=len(objects),
                keys=len(hive.keys), sequence=hive.info['sequence'],
                references_verified=references,
                recovery_path_policy='same-volume-removal-only' if for_removal else 'known-boot-layouts',
                unsupported_device_encodings='rejected', file_mutations=False)
