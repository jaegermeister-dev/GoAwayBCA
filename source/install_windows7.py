#!/usr/bin/env python3
"""Interactive, partition-only Windows 7 x64 deployment from Intel macOS.

External programs are invoked as argv arrays, never shell commands. No driver
installation, whole-disk writes, partition creation, bless, NVRAM or reboot.
Run --list-targets for read-only inventory; --dry-run completes preflight only.
"""
import argparse
import codecs
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zlib

from bcd import personalise
from deployment_lock import DeploymentLock
import prepared_media

ROOT = Path(__file__).resolve().parent
BCD_SHA256 = '30b61e409ece9dbc5c071eaac7a9ff6804a16025c57dae93e7951b38cc639252'
ESP_TYPE = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
BASIC_TYPE = 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'
DISK_RE = re.compile(r'disk[0-9]+\Z')
PART_RE = re.compile(r'(disk[0-9]+)s([1-9][0-9]*)\Z')
GIB = 1024 ** 3


class Refusal(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def write_receipt(path, value):
    """Publish complete, flushed recovery metadata without truncating its predecessor.

    Keep an incomplete pending file if the write fails; never risk removing the
    last readable receipt. The caller supplies its private session path only.
    """
    path = Path(path)
    data = (json.dumps(value, indent=2, default=str) + '\n').encode('utf-8')
    descriptor, pending = tempfile.mkstemp(prefix='receipt-pending-', suffix='.json', dir=str(path.parent))
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, str(path))


def run(argv, *, data=None, capture=True):
    p = subprocess.run([str(x) for x in argv], input=data,
                       stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    if p.returncode:
        error = (p.stderr or b'').decode('utf-8', 'replace')[-3000:]
        raise Refusal('Command failed (%s): %s\n%s' %
                      (p.returncode, ' '.join(shlex.quote(str(x)) for x in argv), error))
    return p.stdout


def run_logged(argv, note):
    """Drain long-running helper output while preserving GUI/file progress.

    No shell is involved. Losing the GUI must not kill an already-started apply.
    Even a logging failure drains/reaps the helper before reporting the error.
    """
    command = [str(x) for x in argv]
    tail = deque(maxlen=20)
    logging_error = None
    pending = ''
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    def emit(line):
        nonlocal logging_error
        line = line.strip()
        if not line:
            return
        tail.append(line[-2000:])
        if logging_error is None:
            try:
                note(line[-2000:])
            except Exception as error:
                logging_error = error
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0) as child:
        while True:
            chunk = child.stdout.read(4096)
            if not chunk:
                break
            pending += decoder.decode(chunk).replace('\r', '\n')
            while '\n' in pending:
                line, pending = pending.split('\n', 1)
                emit(line)
            if len(pending) > 4096:
                emit(pending)
                pending = ''
        emit(pending + decoder.decode(b'', final=True))
        code = child.wait()
    require(code == 0, 'Helper failed (%d): %s\n%s' %
            (code, ' '.join(shlex.quote(x) for x in command), '\n'.join(tail)))
    require(logging_error is None, 'Helper completed, but saving progress failed: %s' % logging_error)


def plist_command(*args):
    return plistlib.loads(run(args))


def raw_info(device):
    return normalize_info(plist_command('/usr/sbin/diskutil', 'info', '-plist', device))


def info(device):
    from partition_detection import info as detected_info
    return detected_info(device)


def require_sources_off_target(paths, device):
    """Compare kernel filesystem identities, not diskutil on arbitrary folders.

    diskutil info accepts a disk or volume mount point, not /Users/l/Desktop.
    stat follows symlinks and APFS firmlinks to the actual containing filesystem.
    On macOS a mounted filesystem's st_dev equals its block node's st_rdev.
    Only metadata is queried here: no device is opened, mounted or modified.
    """
    require(PART_RE.fullmatch(str(device)), 'Invalid erase-target partition identifier.')
    try:
        node = os.stat('/dev/' + device)
    except OSError as error:
        raise Refusal('Cannot verify erase-target device identity: %s' % error)
    require(stat.S_ISBLK(node.st_mode) and type(node.st_rdev) is int and node.st_rdev > 0,
            'Erase target does not identify a block-device partition.')
    for path in paths:
        try:
            source = os.stat(str(path))
        except OSError as error:
            raise Refusal('Cannot verify source filesystem for %s: %s' % (path, error))
        require((stat.S_ISREG(source.st_mode) or stat.S_ISDIR(source.st_mode))
                and type(source.st_dev) is int and source.st_dev > 0,
                'Source is not an identifiable regular file or directory: ' + str(path))
        require(source.st_dev != node.st_rdev,
                'ISO/app/work/prepared-media files are on the selected erase target: ' + str(path))


def normalize_info(d):
    d = dict(d)
    # Monterey uses WholeDisk/WritableMedia/WritableVolume, not the textual
    # diskutil report's field labels. Accept both plist schemas explicitly.
    for old, new in (('WholeDisk', 'Whole'),):
        if new not in d and isinstance(d.get(old), bool):
            d[new] = d[old]
    for old, new in (('WritableMedia', 'ReadOnlyMedia'), ('WritableVolume', 'ReadOnlyVolume')):
        if new not in d and isinstance(d.get(old), bool):
            d[new] = not d[old]
    return d


def filesystem(d):
    return str(d.get('FilesystemType', d.get('FileSystemPersonality', ''))).lower()


def partition_summary(p, disk, multiline=False):
    """Display identities and byte counts; never infer missing free-space data.

    diskutil's FreeSpace can be zero when unmounted, so only show that value
    for a mounted filesystem. Physical capacity is distinct from TotalSize.
    Quoted labels escape control characters rather than allowing UI spoofing.
    """
    capacity = media_size(p)
    free = p.get('FreeSpace')
    known = bool(p.get('MountPoint')) and type(free) is int and 0 <= free <= p['TotalSize']
    free_text = ('%.2f GB (%.2f GiB)' % (free / 10**9, free / GIB)
                 if known else 'unavailable (not reported for this mounted volume)')
    fields = [
        'Label: ' + json.dumps(p.get('VolumeName') or '(unnamed)', ensure_ascii=False),
        'Capacity: %.2f GB (%.2f GiB)' % (capacity / 10**9, capacity / GIB),
        'Free space: ' + free_text,
        'Filesystem: ' + filesystem(p).upper(),
        'Partition: ' + p['DeviceIdentifier'],
        'Drive: ' + json.dumps(disk.get('MediaName') or '(unnamed internal drive)', ensure_ascii=False),
        'Partition UUID: ' + p['DiskUUID']]
    return ('\n' if multiline else ' | ').join(fields)


def internal_physical(d):
    return (d.get('Internal') is True and d.get('Whole') is True
            and d.get('VirtualOrPhysical') == 'Physical'
            and d.get('ReadOnlyMedia') is False
            and d.get('DeviceTreePath')
            and d.get('Content') == 'GUID_partition_scheme'
            and DISK_RE.fullmatch(str(d.get('DeviceIdentifier', ''))))


def media_size(d):
    """Physical media length, never a mounted filesystem's TotalSize.

    diskutil on Tahoe reports exFAT TotalSize smaller than the GPT partition.
    Require explicit media-size evidence; guessing would be unsafe for mkntfs.
    """
    values = [d[k] for k in ('IOKitSize', 'Size') if k in d]
    require(values and all(type(v) is int and v > 0 for v in values),
            'Missing or invalid physical media size in diskutil information.')
    require(len(set(values)) == 1, 'Conflicting physical media sizes in diskutil information.')
    require(values[0] % 512 == 0, 'Physical media size is not sector aligned.')
    return values[0]


def valid_partition(p, disk):
    match = PART_RE.fullmatch(str(p.get('DeviceIdentifier', '')))
    try:
        physical_bytes = media_size(p)
    except Refusal:
        return False
    return bool(match and match[1] == disk.get('DeviceIdentifier')
                and internal_physical(disk) and p.get('Whole') is False
                and p.get('ParentWholeDisk') == disk['DeviceIdentifier']
                and p.get('Internal') is True and p.get('ReadOnlyMedia') is False
                # macOS normally mounts NTFS read-only. That does not make its
                # physical partition read-only; formatting needs raw access.
                and (not p.get('MountPoint') or p.get('ReadOnlyVolume') is False
                     or filesystem(p) == 'ntfs')
                and filesystem(p) in ('exfat', 'ntfs')
                and p.get('Content') == 'Microsoft Basic Data'
                and p.get('MountPoint') != '/'
                # Apple's NTFS reader may omit the filesystem UUID after our
                # mkntfs format. The GPT partition GUID remains mandatory.
                and p.get('DiskUUID') and (p.get('VolumeUUID') or filesystem(p) == 'ntfs')
                and type(p.get('PartitionMapPartitionOffset')) is int
                and p['PartitionMapPartitionOffset'] > 0
                and p['PartitionMapPartitionOffset'] % 512 == 0
                and type(p.get('TotalSize')) is int
                and 0 < p['TotalSize'] <= physical_bytes)


def find_targets(listing, read_info):
    targets = []
    for item in listing.get('AllDisksAndPartitions', []):
        name = item.get('DeviceIdentifier', '')
        if not DISK_RE.fullmatch(name):
            continue
        disk = read_info(name)
        if not internal_physical(disk):
            continue
        esps = [p['DeviceIdentifier'] for p in item.get('Partitions', [])
                if p.get('Content') == 'EFI']
        if len(esps) != 1:
            continue
        for entry in item.get('Partitions', []):
            if entry.get('Content') != 'Microsoft Basic Data':
                continue
            p = read_info(entry['DeviceIdentifier'])
            if valid_partition(p, disk):
                targets.append({'partition': p, 'disk': disk, 'esp': esps[0]})
    return targets


def inventory(listing=None):
    # Lion's documented list syntax has no internal/physical filter arguments.
    # Enumerate the plist with the common syntax, then apply our existing strict
    # per-disk checks. Missing identity/physical evidence still fails closed.
    from partition_detection import inventory as detect
    return detect(listing)


def partition_identity(p):
    identity = {k: p.get(k) for k in ('DeviceIdentifier', 'ParentWholeDisk',
                'DiskUUID', 'VolumeUUID', 'PartitionMapPartitionOffset', 'VolumeName')}
    identity.update(PhysicalSize=media_size(p), Filesystem=filesystem(p))
    return identity


def assert_same_target(target, current, parent):
    require(valid_partition(current, parent), 'Target is no longer an eligible internal exFAT/NTFS partition.')
    selected = target['partition']
    before, after = partition_identity(selected), partition_identity(current)
    changed = sorted(k for k in before if before[k] != after[k])
    require(not changed, 'Partition identity changed (%s). Start again.' % ', '.join(changed))
    # Tahoe reports the mounted filesystem capacity as TotalSize, then the
    # physical partition length after unmount. Neither is a resize. Only allow
    # that specific mount-state transition; same-state capacity changes fail.
    if current['TotalSize'] != selected['TotalSize']:
        changed_mount_state = bool(current.get('MountPoint')) != bool(selected.get('MountPoint'))
        unmounted = selected if not selected.get('MountPoint') else current
        require(changed_mount_state and unmounted['TotalSize'] == media_size(unmounted),
                'Filesystem capacity changed unexpectedly (TotalSize). Start again.')
    for key in ('DeviceIdentifier', 'DeviceTreePath', 'TotalSize', 'MediaName'):
        require(parent.get(key) == target['disk'].get(key), 'Physical disk identity changed.')
    require(media_size(parent) == media_size(target['disk']), 'Physical disk size changed.')


def assert_postformat_target(target, current, parent):
    """After our format, label/volume UUID/filesystem capacity can differ.

    Do not relax any physical identity or GPT checks. diskutil may still cache
    exFAT metadata; actual NTFS reads/writes provide the filesystem validation.
    This is NOT usable as consent to format or as a pre-format identity check.
    """
    require(valid_partition(current, parent), 'Formatted target is no longer an eligible partition')
    require(current.get('MountPoint') == '', 'Formatted target mount state is not confirmed unmounted')
    selected = target['partition']
    for key in ('DeviceIdentifier','ParentWholeDisk','DiskUUID','PartitionMapPartitionOffset'):
        require(current.get(key) == selected.get(key), 'Formatted partition physical identity changed: ' + key)
    require(media_size(current) == media_size(selected), 'Formatted partition physical size changed')
    for key in ('DeviceIdentifier','DeviceTreePath','TotalSize','MediaName'):
        require(parent.get(key) == target['disk'].get(key), 'Formatted partition parent changed')
    require(media_size(parent) == media_size(target['disk']), 'Formatted partition parent size changed')


def ensure_target_unmounted(target, note):
    """Prepare either mount state, using verified metadata rather than error text.

    An unmount can race with another normal unmount. A nonzero return is safe
    to tolerate ONLY when fresh metadata verifies this same eligible partition
    is now unmounted. Busy, replaced, disappeared and unknown targets fail.
    Never force-unmount, remount, or format here.
    """
    device = target['partition']['DeviceIdentifier']
    parent_device = target['disk']['DeviceIdentifier']

    def checked_state():
        current = info(device)
        assert_same_target(target, current, info(parent_device))
        require(type(current.get('MountPoint')) is str,
                'Cannot verify target mount state; no partition has been erased.')
        return current

    current = checked_state()
    if current['MountPoint']:
        try:
            run(['/usr/sbin/diskutil', 'unmount', device])
        except Refusal as error:
            current = checked_state()
            if current['MountPoint']:
                raise
            note('Unmount command returned an error, but fresh identity checks confirm '
                 'the selected partition is now unmounted: ' + str(error))
    else:
        note('Selected partition is already unmounted; no unmount command is needed.')
    # Recheck even if we skipped the command, catching changes/remounts since
    # the first observation. Never treat a missing MountPoint key as evidence.
    current = checked_state()
    require(current['MountPoint'] == '', 'Target is still mounted; no forced unmount is allowed.')
    return current


def select_number(prompt, count, read=input):
    while True:
        answer = read(prompt).strip()
        if answer.lower() in ('q', 'quit', 'cancel', ''):
            raise Refusal('Cancelled; no installation requested.')
        if answer.isascii() and answer.isdecimal() and 1 <= int(answer) <= count:
            return int(answer) - 1
        print('Enter a displayed number, or q to cancel.')


def parse_editions(xml_data):
    root = ET.fromstring(xml_data)
    result = []
    seen = set()
    for node in root.findall('IMAGE'):
        idx = int(node.attrib['INDEX'])
        require(idx > 0 and idx not in seen, 'Invalid/duplicate WIM image index.')
        seen.add(idx)
        arch = node.findtext('WINDOWS/ARCH', '')
        version = tuple(node.findtext('WINDOWS/VERSION/' + key, '')
                        for key in ('MAJOR', 'MINOR', 'BUILD'))
        supported = (arch == '9' and version[:2] == ('6', '1')
                     and version[2] in ('7600', '7601')
                     and node.findtext('WINDOWS/PRODUCTTYPE', '') == 'WinNT')
        result.append({'index': idx, 'name': node.findtext('DISPLAYNAME') or
                       node.findtext('NAME') or 'Image %d' % idx,
                       'bytes': int(node.findtext('TOTALBYTES', '0')),
                       'architecture': arch, 'version': '.'.join(version),
                       'supported': supported})
    require(result, 'No images found in install.wim.')
    return result


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def ci_child(parent, name):
    matches = [x for x in Path(parent).iterdir() if x.name.casefold() == name.casefold()]
    require(len(matches) == 1 and not matches[0].is_symlink(),
            'Missing, ambiguous or symlinked path: %s/%s' % (parent, name))
    return matches[0]


def under(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def tree_manifest(root, note=None, label='Verifying files'):
    result = {}
    paths=sorted(Path(root).rglob('*'))
    if note is not None:
        from operation_progress import Meter
        meter=Meter(note,label,sum(p.is_file() for p in paths),'files verified')
    for p in paths:
        require(not p.is_symlink(), 'Unexpected symlink in EFI tree: ' + str(p))
        if p.is_file():
            result[p.relative_to(root).as_posix()] = sha256(p)
            if note is not None:meter.update(len(result),p.name)
    return result


def protected_efi_files(manifest):
    # FAT mounts can legitimately update macOS bookkeeping. Exclude only these
    # exact ROOT directories, never similarly named files inside EFI or System.
    automatic_roots = {'.fseventsd', '.spotlight-v100', '.trashes'}
    return {name: value for name, value in manifest.items()
            if not ('/' in name and name.split('/', 1)[0].casefold() in automatic_roots)}


def require_efi_unchanged(actual, expected, message):
    require(protected_efi_files(actual) == protected_efi_files(expected), message)


def require_x64_pe(path):
    data = Path(path).read_bytes()
    require(len(data) > 256 and data[:2] == b'MZ', 'Invalid EFI/PE file: ' + str(path))
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    require(offset + 26 < len(data) and data[offset:offset + 4] == b'PE\0\0'
            and struct.unpack_from('<H', data, offset + 4)[0] == 0x8664,
            'Boot file is not x64: ' + str(path))


def read_gpt(stream, sector_size, total_bytes):
    """Read and CRC-check both GPTs, rejecting hybrid MBRs. Never write disk."""
    require(sector_size == 512, 'This Windows 7 version requires a 512-byte logical-sector disk.')
    require(type(total_bytes) is int and total_bytes >= 4 * sector_size
            and total_bytes % sector_size == 0, 'Invalid whole-disk media length.')
    stream.seek(0)
    mbr = stream.read(512)
    require(len(mbr) == 512 and mbr[510:512] == b'\x55\xaa', 'Invalid protective MBR.')
    mbr_entries = [mbr[446 + 16 * i:462 + 16 * i] for i in range(4)]
    types = [entry[4] for entry in mbr_entries]
    require(types.count(0xee) == 1 and all(t in (0, 0xee) for t in types),
            'Hybrid/legacy MBR is NEVER supported; pure GPT native-EFI deployment is required.')
    require(all(entry == bytes(16) for entry in mbr_entries if entry[4] == 0),
            'Nonempty legacy MBR entry refused; no hybrid-layout bypass is available.')
    protective = mbr_entries[types.index(0xee)]
    start, length = struct.unpack_from('<II', protective, 8)
    require(protective[0] == 0 and start == 1 and length == min(total_bytes // sector_size - 1, 0xffffffff),
            'Protective MBR has unexpected active flag or coverage. No layout conversion is attempted.')

    def header(lba):
        stream.seek(lba * sector_size)
        raw = stream.read(sector_size)
        require(len(raw) == sector_size and raw[:8] == b'EFI PART', 'GPT header missing or truncated.')
        require(struct.unpack_from('<I', raw, 8)[0] == 0x10000 and raw[20:24] == bytes(4),
                'Unsupported GPT revision or reserved header fields.')
        size, crc = struct.unpack_from('<II', raw, 12)
        require(92 <= size <= sector_size, 'Unsupported GPT header size.')
        body = bytearray(raw[:size])
        body[16:20] = bytes(4)
        require(zlib.crc32(body) == crc, 'GPT header checksum failed.')
        current, other, first, last = struct.unpack_from('<QQQQ', raw, 24)
        require(current == lba and 2 <= first <= last < total_bytes // sector_size - 1,
                'Invalid GPT bounds.')
        table_lba, count, entry_size, entries_crc = struct.unpack_from('<QIII', raw, 72)
        require(1 <= count <= 4096 and 128 <= entry_size <= 1024
                and entry_size & (entry_size - 1) == 0
                and table_lba * sector_size + count * entry_size <= total_bytes,
                'Invalid GPT entry-table bounds.')
        table_end = table_lba + (count * entry_size + sector_size - 1) // sector_size
        if lba == 1:
            require(other == total_bytes // sector_size - 1
                    and 2 <= table_lba < table_end <= first,
                    'Primary GPT metadata overlaps usable space or has an invalid backup location.')
        else:
            require(lba == total_bytes // sector_size - 1 and other == 1
                    and last < table_lba < table_end <= lba,
                    'Backup GPT metadata overlaps usable space or its header.')
        stream.seek(table_lba * sector_size)
        table = stream.read(count * entry_size)
        require(len(table) == count * entry_size and zlib.crc32(table) == entries_crc,
                'GPT partition-table checksum failed.')
        return raw, table, (other, first, last, count, entry_size)

    primary, entries, bounds = header(1)
    backup, backup_entries, other = header(bounds[0])
    require(bounds[0] == total_bytes // sector_size - 1 and other[0] == 1
            and entries == backup_entries and primary[56:72] == backup[56:72]
            and bounds[1:] == other[1:], 'Primary and backup GPT disagree.')
    partitions = {}
    occupied = []
    disk_guid = uuid.UUID(bytes_le=primary[56:72])
    require(disk_guid.int != 0, 'GPT disk UUID is missing.')
    identities = {disk_guid}
    for n in range(bounds[3]):
        e = entries[n * bounds[4]:(n + 1) * bounds[4]]
        if e[:16] == bytes(16):
            continue
        partition_guid = uuid.UUID(bytes_le=e[16:32])
        require(partition_guid.int != 0 and partition_guid not in identities,
                'Missing or duplicate GPT identity; boot target would be ambiguous.')
        identities.add(partition_guid)
        first, last, flags = struct.unpack_from('<QQQ', e, 32)
        require(bounds[1] <= first <= last <= bounds[2], 'Partition outside GPT bounds.')
        require(not any(first <= b and last >= a for a, b in occupied), 'Overlapping GPT partitions.')
        occupied.append((first, last))
        partitions[n + 1] = {'type': str(uuid.UUID(bytes_le=e[:16])),
                              'guid': str(partition_guid),
                              'first': first, 'last': last, 'flags': flags}
    return {'guid': str(disk_guid), 'partitions': partitions,
            'digest': hashlib.sha256(mbr + primary + entries + backup).hexdigest()}


def gpt_for(target):
    from disk_layout import layout
    return layout(target['disk'])


def match_gpt(target, gpt):
    # macOS BSD slice numbers can remain nonsequential after repartitioning.
    # A disk0s4 device may occupy GPT entry 3. Bind both selected devices by
    # UUID, parent disk and exact geometry, never by the slice-number suffix.
    def resolve(p, expected_type):
        match = PART_RE.fullmatch(p.get('DeviceIdentifier', ''))
        require(match and match[1] == target['disk']['DeviceIdentifier']
                and p.get('ParentWholeDisk') == match[1],
                'Selected partition does not belong to the expected physical disk.')
        guid = p.get('DiskUUID', '').lower()
        matches = [row for row in gpt['partitions'].values()
                   if guid and row['guid'].lower() == guid]
        require(len(matches) == 1 and matches[0]['type'] == expected_type,
                'GPT identity/type does not match the selected partition.')
        row = matches[0]
        require((row['last'] - row['first'] + 1) * gpt.get('sector_size',512) == media_size(p)
                and row['first'] * gpt.get('sector_size',512) == p.get('PartitionMapPartitionOffset'),
                'macOS partition identity and geometry disagree about the target.')
        return row
    win = resolve(target['partition'], BASIC_TYPE)
    esp = resolve(info(target['esp']), ESP_TYPE)
    require(not (win['flags'] & (1 << 60)), 'GPT marks target read-only.')
    require(win['first'] <= 0xffffffff, 'NTFS partition offset exceeds this deployment version.')
    return win, esp


class Installer:
    def __init__(self, args, interaction=None):
        self.args = args
        self.interaction = interaction
        self.iso_device = None
        self.esp_owned = False
        self.esp_device = None
        self.esp_identity = None
        self.esp_mount_path = None
        self.formatted = False
        self.session = None
        self.prepared_profile = None
        self.prepared_readback = {}
        self.accepted_extras = None
        self.accepted_gpu = None
        self.deployment_lock = None

    def note(self, message):
        if self.session:
            with (self.session / 'progress.log').open('a', encoding='utf-8') as f:
                f.write(time.strftime('%Y-%m-%dT%H:%M:%S%z ') + message + '\n')
                f.flush()
        if self.interaction is not None:
            self.interaction.log(message)
        else:
            print(message, flush=True)

    def choose(self, question, choices):
        if self.interaction is not None:
            selected = self.interaction.choose(question, choices)
            require(type(selected) is int and 0 <= selected < len(choices), 'Invalid selection.')
            return selected
        for number, choice in enumerate(choices, 1):
            print('%d. %s' % (number, choice))
        return select_number(question, len(choices))

    def confirm_erase(self, text, phrase, **fields):
        if self.interaction is not None:
            return self.interaction.confirm(text, phrase, **fields) is True
        if fields.get('details'): print(fields['details'])
        return input('\n%s\nType %s to continue: ' % (text, phrase)).strip().lower() == phrase.lower()

    def approve(self, kind, text, **fields):
        if self.interaction is not None:
            return self.interaction.approve(kind, text, **fields) is True
        if fields.get('details'): print(fields['details'])
        return input('\n%s\nType YES to continue, or anything else to cancel: ' % text) == 'YES'

    def tools(self):
        folder = Path(self.args.tools_dir).expanduser().resolve()
        tools = {}
        for name in ('wimlib-imagex', 'mkntfs', 'ntfscat'):
            path = folder / name
            require(path.is_file() and os.access(path, os.X_OK),
                    'Missing macOS helper: %s\nSee DEPENDENCIES.txt. No partition has been erased.' % path)
            tools[name] = str(path)
        require(sha256(ROOT / 'BCD.template') == BCD_SHA256, 'BCD.template failed integrity check.')
        self.wim, self.mkntfs, self.ntfscat = (tools[x] for x in ('wimlib-imagex', 'mkntfs', 'ntfscat'))
        if getattr(self.args, 'win7_accepted_extras', False):
            path = folder / 'ntfscp'
            require(path.is_file() and os.access(path, os.X_OK), 'Missing bundled NTFS stream writer')
            self.ntfscp = str(path)
            tools['ntfscp'] = str(path)
        return {name: sha256(path) for name, path in tools.items()}

    def prepare_accepted_additions(self, wim, edition, stage):
        """Private profile hook. Preparation only, always before formatting."""
        if not getattr(self.args, 'win7_accepted_extras', False):
            return
        import windows7_extras
        import win7_accepted_efi
        require(self.prepared_profile is not None,
                'The accepted private fixes require the audited Windows 7 SP1 image')
        model = run(['/usr/sbin/sysctl', '-n', 'hw.model']).decode().strip()
        require(model == 'MacBookPro11,5', 'This private accepted-graphics profile is 11,5 only')
        copy_drivers = self.choose('Copy the community Windows 7 driver installer to the Windows partition root? '
                'Run it yourself after normal Windows setup finishes.',
                ['Copy the driver installer', 'Skip the driver installer']) == 0
        self.accepted_extras = windows7_extras.prepare(self.wim, wim, edition['index'],
            self.session, ROOT / 'lib/libhivex.0.dylib', ROOT, model, copy_drivers)
        assets = ROOT / 'efi/accepted-win7'
        self.accepted_gpu = win7_accepted_efi.stage(self.session, stage,
            assets / 'WIN7AUTO.efi', assets / 'ALLOW-AUTO.txt', model, 'Windows 7')
        self.note('Staged mandatory tested 11,5 graphics helper and original inbox keyboard discovery files.')
        self.note('Normal Windows setup, UAC and driver-signature enforcement are unchanged.')

    def apply_accepted_additions(self, raw, target, gpt):
        if self.accepted_extras is None:
            return None
        import windows7_extras
        def guard():
            device = target['partition']['DeviceIdentifier']
            require(raw == '/dev/' + device, 'Extras target differs from confirmed partition')
            current = info(device)
            assert_postformat_target(target, current, info(target['disk']['DeviceIdentifier']))
            require(gpt_for(target)['digest'] == gpt['digest'], 'GPT changed during extras delivery')
            require(stat.S_ISBLK(os.stat(raw).st_mode), 'Extras target is not a partition block device')
        return windows7_extras.apply(self.accepted_extras, self.wim, self.ntfscp,
            self.ntfscat, raw, guard, self.note)

    def mount_iso(self, iso):
        dest = self.session / 'iso'
        dest.mkdir()
        data = plist_command('/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse',
                             '-plist', '-mountpoint', str(dest), str(iso))
        entities = data.get('system-entities', [])
        mounted = [e for e in entities if e.get('mount-point') == str(dest)]
        require(len(mounted) == 1, 'ISO mount did not produce one expected volume.')
        self.iso_device = mounted[0]['dev-entry']
        return dest

    def mount_esp(self, device, readonly):
        require(isinstance(device, str) and PART_RE.fullmatch(device), 'Invalid EFI device identifier.')
        require(type(readonly) is bool, 'Explicit EFI access mode required.')
        d = info(device)
        require(d.get('Content') == 'EFI' and d.get('Whole') is False
                and d.get('Internal') is True, 'Unexpected EFI partition.')
        identity = d.get('DiskUUID')
        require(identity, 'EFI partition UUID is missing.')
        if self.esp_owned:
            require(device == self.esp_device and identity == self.esp_identity
                    and d.get('MountPoint') == str(self.esp_mount_path),
                    'Owned EFI mount identity/path changed; refusing to touch it.')
        if d.get('MountPoint') and not readonly and d.get('ReadOnlyVolume'):
            require(self.esp_owned, 'EFI was already mounted read-only by another process; refusing to remount it.')
            run(['/sbin/umount', str(self.esp_mount_path)])
            self.esp_owned = False
            d = info(device)
            require(not d.get('MountPoint') and d.get('DiskUUID') == identity,
                    'EFI unmount/identity verification failed.')
        if not d.get('MountPoint'):
            # Use macOS's FAT helper directly. Tahoe's FSKit diskutil route can
            # reject a dirty flag even when a no-repair filesystem check passes.
            # No fsck repair is attempted. A failed read-only check stops safely.
            helper = Path('/sbin/mount_msdos')
            require(helper.is_file() and os.access(helper, os.X_OK), 'macOS FAT mount helper is unavailable.')
            raw = '/dev/r' + device
            run(['/sbin/fsck_msdos', '-n', raw])
            owned_mount = self.session / 'efi-mount'
            owned_mount.mkdir(mode=0o700, exist_ok=True)
            require(not owned_mount.is_symlink() and not any(owned_mount.iterdir()), 'Unsafe or nonempty EFI mountpoint.')
            run([str(helper), '-o', 'rdonly,nobrowse' if readonly else 'nobrowse',
                 '/dev/' + device, str(owned_mount)])
            # Record ownership immediately after a successful mount, so cleanup
            # can safely verify it even if a later access-mode check fails.
            self.esp_device, self.esp_identity = device, identity
            self.esp_mount_path, self.esp_owned = owned_mount, True
            d = info(device)
            require(d.get('MountPoint') == str(owned_mount) and d.get('DiskUUID') == identity,
                    'Unexpected EFI mount identity/path.')
            require(d.get('ReadOnlyVolume') is readonly, 'EFI mount access does not match the requested mode.')
        mount = d.get('MountPoint')
        require(mount and mount != '/' and Path(mount).is_dir(), 'EFI mount missing or unsafe.')
        if not readonly:
            require(d.get('ReadOnlyVolume') is False, 'EFI is not writable.')
        return Path(mount)

    def check_efi_free(self, mount):
        candidates = [p for p in mount.iterdir() if p.name.casefold() == 'efi']
        require(len(candidates) <= 1, 'Ambiguous EFI directory.')
        if candidates:
            efi = candidates[0]
            require(efi.is_dir() and not efi.is_symlink(), 'Unsafe EFI directory.')
            names = ('boot', 'microsoft')
            if getattr(self.args, 'win7_accepted_extras', False):
                names += ('w7dep-win7-auto',)
            reserved = [p.name for p in efi.iterdir() if p.name.casefold() in names]
            require(not reserved,
                    'Existing EFI paths must be preserved: %s.\n'
                    'Stopping BEFORE format. This version cannot add a separate native picker entry '
                    'without occupying those paths. No existing loader will be replaced.' % ', '.join(reserved))
            return efi
        return mount / 'EFI'

    def smoke_tools(self):
        """Exercise raw NTFS application on a scratch FILE, never a device."""
        self.note('Checking NTFS/WIM helper compatibility on a 32 MiB scratch file...')
        scratch = self.session / 'helper-test'
        scratch.mkdir()
        source = scratch / 'source'
        source.mkdir()
        marker = b'Windows7 native deployment helper self-test\n'
        (source / 'probe.txt').write_bytes(marker)
        image = scratch / 'test.ntfs'
        with image.open('xb') as f:
            f.truncate(32 * 1024 * 1024)
        require(image.is_file() and under(image, self.session), 'Unsafe helper-test path.')
        run([self.mkntfs, '-F', '-Q', '-s', '512', '-L', 'TEST', str(image)])
        wim = scratch / 'test.wim'
        run([self.wim, 'capture', str(source), str(wim), 'Helper test'])
        run([self.wim, 'apply', str(wim), '1', str(image), '--check'])
        actual = run([self.ntfscat, str(image), '/probe.txt'])
        require(actual == marker, 'Raw NTFS WIM apply/readback test failed.')

    def extract_boot(self, wim, edition, gpt, win, esp):
        stage = self.session / 'staged-efi'
        stage.mkdir()
        extract = self.session / 'extracted-boot'
        extract.mkdir()
        for path in ('/Windows/Boot/EFI', '/Windows/Boot/Fonts'):
            run([self.wim, 'extract', str(wim), str(edition['index']), path,
                 '--dest-dir=' + str(extract), '--no-acls', '--no-attributes'])
        run([self.wim, 'extract', str(wim), str(edition['index']),
             '/Windows/System32/winload.efi', '--dest-dir=' + str(extract),
             '--no-acls', '--no-attributes'])
        require_x64_pe(ci_child(extract, 'winload.efi'))
        boot = stage / 'Microsoft' / 'Boot'
        boot.parent.mkdir()
        shutil.copytree(ci_child(extract, 'EFI'), boot)
        fonts = ci_child(extract, 'Fonts')
        require(not (boot / 'Fonts').exists(), 'Unexpected Fonts path in WIM EFI directory.')
        shutil.copytree(fonts, boot / 'Fonts')
        manager = ci_child(boot, 'bootmgfw.efi')
        require_x64_pe(manager)
        if self.prepared_profile:
            require(not self.args.boot_manager, 'Do not mix a prepared WIM with an unrelated loader override.')
            require(sha256(manager) == prepared_media.LOADER_SHA256,
                    'The selected prepared edition has an unexpected EFI boot manager.')
            # Hash the supplied driver/hive data before formatting, and compare
            # the actual NTFS copies after apply. Do not edit those hives.
            for path in prepared_media.READBACK_PATHS:
                data = run([self.wim, 'extract', str(wim), str(edition['index']), path, '--to-stdout'])
                require(data, 'Missing prepared Windows component: ' + path)
                self.prepared_readback[path] = hashlib.sha256(data).hexdigest()
        bcd = personalise((ROOT / 'BCD.template').read_bytes(), BCD_SHA256,
                          gpt['guid'], esp['guid'], win['guid'])
        require(not (boot / 'BCD').exists(), 'WIM unexpectedly supplied an existing BCD.')
        (boot / 'BCD').write_bytes(bcd)
        fallback = stage / 'Boot'
        fallback.mkdir()
        shutil.copyfile(manager, fallback / 'BOOTX64.EFI')
        # Optional owner-supplied, already-tested boot manager.
        # This does not patch a loader or redistribute compatibility material.
        if self.args.boot_manager:
            custom = Path(self.args.boot_manager).expanduser().resolve()
            require_x64_pe(custom)
            shutil.copyfile(custom, manager)
            shutil.copyfile(custom, fallback / 'BOOTX64.EFI')
        return stage

    def execute(self):
        require(sys.platform == 'darwin' and platform.machine() == 'x86_64', 'Run this on an Intel Mac.')
        if self.args.list_targets:
            print(json.dumps(inventory(), indent=2, default=str))
            return
        tool_hashes = self.tools()
        require(self.interaction is not None or sys.stdin.isatty(),
                'Interactive GUI or Terminal is required; unattended erasure is prohibited.')
        require(self.interaction is None or os.geteuid() == 0,
                'GUI worker must receive administrator authorisation before it starts.')
        iso_text = self.args.iso or input('Windows 7 ISO path (drag it here, then Return): ').strip()
        if not self.args.iso:
            values = shlex.split(iso_text)
            require(len(values) == 1, 'Provide one ISO path.')
            iso_text = values[0]
        iso = Path(iso_text).expanduser().resolve()
        require(iso.is_file() and iso.suffix.lower() == '.iso', 'Select a Windows 7 .iso file.')
        require(not (self.args.prepared_media and self.args.boot_manager),
                'Use the matching loader inside the prepared WIM, not --boot-manager as well.')
        if os.geteuid() != 0:
            # Pass an argv array through sudo, not a shell expression.
            args = [sys.executable, '-B', str(Path(__file__).resolve()), '--iso', str(iso),
                    '--tools-dir', str(Path(self.args.tools_dir).expanduser().resolve()),
                    '--work-dir', str(Path(self.args.work_dir).expanduser().resolve())]
            if self.args.dry_run:
                args.append('--dry-run')
            if self.args.boot_manager:
                args += ['--boot-manager', str(Path(self.args.boot_manager).expanduser().resolve())]
            if self.args.prepared_media:
                args += ['--prepared-media', str(Path(self.args.prepared_media).expanduser().resolve())]
            print('Administrator access is needed to inspect GPT/EFI and, after confirmation, deploy Windows.', flush=True)
            os.execv('/usr/bin/sudo', ['sudo', '--'] + args)
        # Serialize every privileged run across app copies and work directories.
        # A failed/closed UI is not proof that its worker has finished writing.
        self.deployment_lock = DeploymentLock().acquire()
        base = Path(self.args.work_dir).expanduser().resolve()
        base.mkdir(parents=True, exist_ok=True)
        require(not base.is_symlink(), 'Work directory cannot be a symlink.')
        self.session = Path(tempfile.mkdtemp(prefix='session-', dir=str(base)))
        os.chmod(self.session, 0o700)
        self.note('Windows 7 is not supported in GoAwayBCA Beta 1; support is being developed for a future update.')
        self.note('Session and EFI backup: ' + str(self.session))
        self.note('No driver downloads, activation changes, partition resizing or default-boot changes.')
        media = self.mount_iso(iso)
        wim = ci_child(ci_child(media, 'sources'), 'install.wim')
        require(wim.is_file(), 'install.wim must be a regular file.')
        if self.args.prepared_media:
            self.note('Verifying and privately snapshotting your audited prepared Windows 7 image...')
            wim, self.prepared_profile = prepared_media.snapshot(self.args.prepared_media, wim, self.session)
            self.note('Using the supplied image including its USB/storage drivers and matching EFI patch.')
            self.note('This preserves image contents; it does NOT establish working hardware drivers yet.')
        xml_path = self.session / 'editions.xml'
        run([self.wim, 'info', str(wim), '--extract-xml=' + str(xml_path)])
        editions = parse_editions(xml_path.read_bytes())
        edition_options = ['%s [WIM index %d; %s]%s' % (e['name'], e['index'], e['version'],
                           '' if e['supported'] else ' -- NOT supported (requires Windows 7 x64)')
                           for e in editions]
        edition = editions[self.choose('Choose the Windows 7 edition:', edition_options)]
        require(edition['supported'] and edition['bytes'] > 0, 'Only Windows 7 client x64 build 7600/7601 is supported.')
        targets = inventory()
        require(targets, 'No eligible internal physical GPT exFAT/NTFS partitions. Nothing has been erased.\n'
                'Create a separate disposable exFAT PARTITION yourself; this script will not repartition the disk.')
        target_options = []
        for t in targets:
            target_options.append(partition_summary(t['partition'], t['disk']))
        target = targets[self.choose('Choose the internal exFAT or NTFS partition to REPLACE:', target_options)]
        p = target['partition']
        device = p['DeviceIdentifier']
        require(p['TotalSize'] >= max(20 * GIB, edition['bytes'] + 4 * GIB), 'Target is too small for this edition.')
        protected_paths = [iso, ROOT, self.session]
        if self.args.prepared_media:
            protected_paths.append(Path(self.args.prepared_media).expanduser().resolve())
        if self.args.boot_manager:
            protected_paths.append(Path(self.args.boot_manager).expanduser().resolve())
        require_sources_off_target(protected_paths, device)
        assert_same_target(target, info(device), info(target['disk']['DeviceIdentifier']))
        from disk_layout import require_windows_sector_support
        require_windows_sector_support(target,'Windows 7')
        gpt = gpt_for(target)
        win, esp = match_gpt(target, gpt)
        require(info(target['esp']).get('DiskUUID', '').lower() == esp['guid'], 'EFI identity mismatch.')
        mount = self.mount_esp(target['esp'], readonly=True)
        self.check_efi_free(mount)
        before_efi = tree_manifest(mount)
        backup = self.session / 'efi-before'
        shutil.copytree(mount, backup)
        require(tree_manifest(backup) == before_efi, 'EFI backup verification failed.')
        self.note('EFI backup verified. Verifying the complete WIM before any format...')
        run_logged([self.wim, 'verify', str(wim)], self.note)
        self.smoke_tools()
        stage = self.extract_boot(wim, edition, gpt, win, esp)
        self.prepare_accepted_additions(wim, edition, stage)
        require(shutil.disk_usage(mount).free > sum(x.stat().st_size for x in stage.rglob('*') if x.is_file()) + 8 * 1024**2,
                'Not enough free space on the existing EFI partition.')
        receipt = {'status': 'preflight-only', 'iso': str(iso), 'wim_sha256': sha256(wim),
                   'edition': edition, 'target': target, 'gpt': gpt, 'helper_sha256': tool_hashes,
                   'efi_before': before_efi, 'staged_efi': tree_manifest(stage),
                   'boot_manager_override': self.args.boot_manager,
                   'prepared_profile': self.prepared_profile,
                   'prepared_readback_sha256': self.prepared_readback,
                   'accepted_gpu_staging': self.accepted_gpu,
                   'accepted_keyboard_staged': self.accepted_extras is not None,
                   'community_drivers_staged': bool(self.accepted_extras and self.accepted_extras['drivers']),
                   'hardware_drivers_validated': False}
        receipt_path = self.session / 'receipt.json'
        write_receipt(receipt_path, receipt)
        self.note('\nSelected: %s -> %s (%s, %.1f GiB)' %
                  (edition['name'], device, p.get('VolumeName', ''), p['TotalSize'] / GIB))
        self.note('ALL contents of that selected partition will be erased and replaced by NTFS Windows.')
        self.note('EFI additions: EFI/Microsoft/Boot and EFI/Boot/BOOTX64.EFI on ' + target['esp'])
        if self.accepted_gpu:
            self.note('Additional verified helper files: EFI/W7DEP-WIN7-AUTO (new folder only).')
        self.note('macOS/recovery/other partitions and existing EFI files will not be replaced.')
        if not self.args.boot_manager and not self.prepared_profile:
            self.note('Using the ISO\'s STOCK Windows 7 loader. This does NOT reproduce FlashBoot\'s firmware fixes;'
                      '\n2015 Mac first boot may need a separately validated compatible boot manager.')
        if self.args.dry_run:
            self.note('DRY RUN COMPLETE. No target/EFI data was written. Bootability is not yet proven.')
            return
        # Refresh displayed metadata, then require a format-button request AND
        # two independent confirmations. Selection/startup consent never counts.
        current = info(device)
        assert_same_target(target, current, info(target['disk']['DeviceIdentifier']))
        summary = partition_summary(current, target['disk'], multiline=True)
        warning = ('Double-check the label, capacity, free space and partition identifier. '
                   'Selecting the wrong partition will cause data loss. ALL existing contents '
                   'of the selected partition will be erased, including any Windows installation. '
                   'Its contents will NOT be backed up by this tool. The tool and its authors '
                   'are not responsible for data loss caused by selecting the wrong partition.')
        require(self.approve('format_ready', 'Ready to format as NTFS and install %s.\n\n%s\n\n'
                'Click Format selected partition and install to begin the two confirmations. '
                'Nothing is formatted by this click.' % (edition['name'], summary)),
                'Formatting was not requested. No partition has been erased.')
        require(self.approve('review', 'Confirmation 1 of 2 — check the selected partition.\n\n%s\n\n%s' %
                (summary, warning)), 'First confirmation declined. No partition has been erased.')
        phrase = 'confirm'
        require(self.confirm_erase('Confirmation 2 of 2 — final erase confirmation.\n\n%s\n\n%s\n\n'
                'Erase ONLY this partition and install %s?' % (summary, warning, edition['name']), phrase),
                'Confirmation did not match. No partition has been erased.')
        receipt['format_confirmations'] = {'count': 2, 'target_summary': summary, 'format': 'NTFS'}
        # Establish write access before erasing Windows, not after deployment.
        # Merely mounting does not publish/replace any EFI payload.
        mount = self.mount_esp(target['esp'], readonly=False)
        require(info(target['esp']).get('DiskUUID', '').lower() == esp['guid'], 'EFI identity changed.')
        assert_same_target(target, info(device), info(target['disk']['DeviceIdentifier']))
        require(gpt_for(target)['digest'] == gpt['digest'], 'Disk layout changed during preflight.')
        require_efi_unchanged(tree_manifest(mount), before_efi, 'EFI boot/project contents changed during preflight.')
        self.check_efi_free(mount)
        require(tree_manifest(stage) == receipt['staged_efi'], 'Staged EFI changed before formatting')
        if self.accepted_extras is not None:
            import windows7_extras
            windows7_extras.validate_plan(self.accepted_extras)
        if self.prepared_profile:
            require(sha256(wim) == prepared_media.PREPARED_WIM_SHA256,
                    'Private prepared image changed during preflight. Nothing has been formatted.')
        require_sources_off_target(protected_paths, device)
        current = ensure_target_unmounted(target, self.note)
        raw = '/dev/' + device
        require(stat.S_ISBLK(os.stat(raw).st_mode), 'Target is not a block-device partition.')
        receipt['status'] = 'format-started'
        write_receipt(receipt_path, receipt)
        self.formatted = True
        self.note('Formatting selected partition ONLY: ' + raw)
        run_logged([self.mkntfs, '-Q', '-s', '512', '-p', str(win['first']), '-H', '255', '-S', '63',
             '-L', 'Windows-7', raw, str(win['last'] - win['first'] + 1)], self.note)
        receipt['status'] = 'ntfs-formatted'
        write_receipt(receipt_path, receipt)
        require(not info(device).get('MountPoint'), 'macOS auto-mounted the target; stopping before WIM application.')
        self.note('Applying the entire selected edition, including NTFS/Windows metadata...')
        receipt['status'] = 'image-apply-started'
        write_receipt(receipt_path, receipt)
        run_logged([self.wim, 'apply', str(wim), str(edition['index']), raw, '--check', '--strict-acls'], self.note)
        self.note('Checking required installed files...')
        for path in ('/Windows/System32/winload.efi', '/Windows/System32/config/SYSTEM',
                     '/Windows/System32/config/SOFTWARE'):
            data = run([self.ntfscat, raw, path])
            require(len(data) > 1024 and (data[:2] == b'MZ' if path.endswith('.efi') else data[:4] == b'regf'),
                    'Installed file verification failed: ' + path)
        for path, expected in self.prepared_readback.items():
            actual = hashlib.sha256(run([self.ntfscat, raw, path])).hexdigest()
            require(actual == expected, 'Prepared Windows component readback failed: ' + path)
        # Baseline source verification MUST precede the deliberate SOFTWARE
        # DevicePath addition. Extras never run Windows setup or an EXE.
        receipt['accepted_additions'] = self.apply_accepted_additions(raw, target, gpt)
        require(gpt_for(target)['digest'] == gpt['digest'], 'GPT unexpectedly changed; stopping before EFI publication.')
        receipt['status'] = 'image-verified'
        write_receipt(receipt_path, receipt)
        mount = self.mount_esp(target['esp'], readonly=False)
        require(info(target['esp']).get('DiskUUID', '').lower() == esp['guid'], 'EFI device changed.')
        require_efi_unchanged(tree_manifest(mount), before_efi, 'EFI changed before publication; refusing to replace anything.')
        efi = self.check_efi_free(mount)
        require(tree_manifest(stage) == receipt['staged_efi'], 'Staged EFI changed before publication')
        efi.mkdir(exist_ok=True)
        # The helper's supporting directory must exist before either launch
        # alias can reach it. Never merge it with an existing directory.
        if self.accepted_gpu:
            receipt['status'] = 'accepted-helper-publication-started'
            write_receipt(receipt_path, receipt)
            shutil.copytree(stage / 'W7DEP-WIN7-AUTO', efi / 'W7DEP-WIN7-AUTO')
            require(tree_manifest(efi / 'W7DEP-WIN7-AUTO') == tree_manifest(stage / 'W7DEP-WIN7-AUTO'),
                    'Accepted helper publication readback failed')
        # Publish Microsoft payload first and the firmware fallback entry LAST.
        # copytree refuses an existing destination; no overwrite/merge mode.
        receipt['status'] = 'microsoft-efi-publication-started'
        write_receipt(receipt_path, receipt)
        shutil.copytree(stage / 'Microsoft', efi / 'Microsoft')
        require(tree_manifest(efi / 'Microsoft') == tree_manifest(stage / 'Microsoft'), 'EFI payload verification failed.')
        receipt['status'] = 'fallback-efi-publication-started'
        write_receipt(receipt_path, receipt)
        shutil.copytree(stage / 'Boot', efi / 'Boot')
        require(tree_manifest(efi / 'Boot') == tree_manifest(stage / 'Boot'), 'Fallback loader verification failed.')
        after = tree_manifest(mount)
        require(all(after.get(k) == v for k, v in protected_efi_files(before_efi).items()),
                'Pre-existing EFI boot/project contents changed.')
        run(['/bin/sync'])
        receipt['status'] = 'deployed-awaiting-first-boot'
        receipt['efi_after'] = after
        receipt['macos_bookkeeping_changed'] = sorted(k for k in set(before_efi) | set(after)
            if before_efi.get(k) != after.get(k) and k not in protected_efi_files(dict(before_efi, **after)))
        write_receipt(receipt_path, receipt)
        self.note('\nDEPLOYMENT COMPLETE. First boot and hardware support remain unverified.')
        self.note('Restart when ready, hold Option, and look for EFI Boot / Windows.')
        self.note('Select it and allow Windows first-boot setup to finish. Hardware driver testing remains a later step.')
        self.note('No automatic restart or default startup-disk change was made.')

    def close(self):
        errors = []
        if self.esp_owned and self.esp_device:
            try:
                current = info(self.esp_device)
                require(current.get('DiskUUID') == self.esp_identity
                        and current.get('MountPoint') == str(self.esp_mount_path),
                        'EFI ownership changed; leaving the current mount untouched.')
                run(['/sbin/umount', str(self.esp_mount_path)])
                self.esp_owned = False
            except Exception as e:
                errors.append('Could not unmount owned EFI mount: ' + str(e))
        if self.iso_device:
            try:
                run(['/usr/bin/hdiutil', 'detach', self.iso_device])
                self.iso_device = None
            except Exception as e:
                errors.append('Could not detach ISO: ' + str(e))
        # Keep logs, manifests, staged files and EFI backup. Never recursively
        # delete a mountpoint or any user-selected directory during cleanup.
        try:
            for error in errors:
                self.note(error)
        finally:
            if self.deployment_lock is not None:
                self.deployment_lock.release()
                self.deployment_lock = None
        return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso')
    parser.add_argument('--prepared-media', help='Owner-supplied audited FlashBoot media directory; preserves prepared WIM and loader together')
    parser.add_argument('--tools-dir', default=str(ROOT / 'tools' / 'bin'))
    parser.add_argument('--work-dir', default=str(Path.home() / 'Windows7Work' / 'deployment-logs'))
    parser.add_argument('--boot-manager', help='Optional owner-supplied compatible bootmgfw.efi; no patching is performed')
    parser.add_argument('--dry-run', action='store_true', help='Full preflight, no format/WIM apply to device/EFI publication')
    parser.add_argument('--list-targets', action='store_true', help='Read-only internal exFAT inventory; no dependencies/sudo needed')
    args = parser.parse_args()
    installer = Installer(args)
    code = 0
    try:
        installer.execute()
    except (Exception, KeyboardInterrupt) as e:
        installer.note('\nSTOPPED: ' + (str(e) or 'Interrupted'))
        if installer.formatted:
            installer.note('The selected target may now contain a PARTIAL installation. Do not assume it is bootable.'
                           '\nThe selected partition\'s old files were NOT backed up. See this session\'s progress log and EFI backup.')
        else:
            installer.note('No target partition was formatted by this run.')
        code = 1
    finally:
        installer.close()
    return code


if __name__ == '__main__':
    sys.exit(main())
