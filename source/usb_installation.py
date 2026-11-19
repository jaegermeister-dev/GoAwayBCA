"""Opt-in portable Windows preparation, restricted to USB destinations.

Internal installation follows its existing path unchanged. This does not add a
storage driver or make a USB device certified for Windows To Go. Registry edits
are limited to a copied SYSTEM hive and verified before/after publication.
"""
import hashlib
import os
from pathlib import Path
import re
import struct

from install_windows7 import require, run
from offline_keyboard import Hive
from win8_nvme import WritableHive

SYSTEM = '/Windows/System32/config/SYSTEM'
REFERENCES = (
    'https://learn.microsoft.com/windows/deployment/planning/windows-to-go-overview',
    'https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/deployment/windows-to-go/deploy-windows-to-go',
    'https://learn.microsoft.com/en-us/windows-hardware/drivers/install/specifying-driver-load-order',
    'https://github.com/pbatard/rufus/wiki/Usage-Notes',
)


def configure(owner, target, edition):
    if target['disk'].get('Internal') is True:
        return None
    require(target['disk'].get('Internal') is False and target['disk'].get('BusProtocol') == 'USB',
            'The external installation destination must be a verified USB drive.')
    family = edition['family']
    shared = ('USB installation is EXPERIMENTAL AND UNTESTED. A drive that works in macOS may not boot Windows. '
              'The USB controller, adapter, fixed/removable reporting, power and drive performance all matter. '
              'Connect it directly for the first boot; do not disconnect it while Windows is running. '
              'Only Windows installations on this same drive are included in its Windows boot menu. ')
    if family == 'Windows 7':
        require(owner.choose(shared + '\n\nWindows 7 does not have official Windows To Go support. '
            'Its prepared USB drivers do not guarantee booting from USB, especially through USB 3.x or UASP adapters. '
            'This uses normal installation preparation; no Windows To Go settings are applied. '
            'An internal destination or a newer Windows version is recommended.',
            ['Cancel and review the destination', 'Try Windows 7 on USB (experimental)']) == 1,
            'Windows 7 USB installation cancelled. Nothing was formatted.')
        return dict(mode='normal', family=family, experimental=True, confirmed_usb=True)
    require(family in ('Windows 8', 'Windows 8.1', 'Windows 10', 'Windows 11'), 'Unknown portable Windows version.')
    choice = owner.choose(shared + '\n\nWe recommend portable preparation for this USB installation. '
        'It enables the USB boot-driver flags, marks Windows as portable, keeps internal disks offline by default '
        'and disables hibernation/Fast Startup in this Windows installation. It does not provide missing drivers. '
        'You may instead try normal preparation without those changes.\n\nMicrosoft\'s historical Windows To Go '
        'support was limited to particular editions and drives and was removed from Windows 10 version 2004 onward. '
        'Neither option here is a claim of official Windows To Go support. Results can vary between Windows builds. '
        'See the USB guidance in the documentation.',
        ['Portable preparation (recommended; experimental)',
         'Normal preparation without portable settings (experimental)', 'Cancel'])
    require(type(choice) is int and choice in (0, 1), 'USB installation cancelled. Nothing was formatted.')
    return dict(mode='portable' if choice == 0 else 'normal', family=family, experimental=True, confirmed_usb=True)


def registry_changes(controlsets, value):
    """Preserve existing boot flags; do not change driver Start/Group/signatures."""
    require(controlsets and all(re.fullmatch(r'ControlSet[0-9]{3}', name) for name in controlsets),
            'No valid Windows control set was found for portable preparation.')
    edits = {}
    dword = lambda n: (4, struct.pack('<I', n))
    for cs in controlsets:
        control = (cs, 'Control')
        old = value(control, 'BootDriverFlags')
        require(old is None or (old[0] == 4 and len(old[1]) == 4), 'Unexpected Windows USB boot-flag format.')
        flags = struct.unpack('<I', old[1])[0] if old else 0
        edits[control] = {'BootDriverFlags': dword(flags | 0x14), 'PortableOperatingSystem': dword(1)}
        edits[(cs, 'Services', 'partmgr', 'Parameters')] = {'SanPolicy': dword(4)}
        edits[(cs, 'Control', 'Power')] = {'HibernateEnabled': dword(0)}
        edits[(cs, 'Control', 'Session Manager', 'Power')] = {'HiberbootEnabled': dword(0)}
    return edits


def prepare_hive(library, source, output):
    source, output = Path(source), Path(output)
    require(source.is_absolute() and source.resolve() == source and source.is_file() and not source.is_symlink(),
            'Use a verified copied SYSTEM hive for portable preparation.')
    require(output.is_absolute() and output.resolve() == output and output.parent.is_dir()
            and not output.exists() and not output.is_symlink(), 'Use a new portable SYSTEM output file.')
    original = source.read_bytes()
    hive = WritableHive(library, source, True)
    try:
        before = hive.snapshot()
        controlsets = sorted(p[0] for p in before if len(p) == 1 and re.fullmatch(r'ControlSet[0-9]{3}', p[0]))
        def value(path, name):
            if name not in before.get(path, {}):
                return None
            return hive.value(path, name)
        edits = registry_changes(controlsets, value)
        expected = {path: dict(values) for path, values in before.items()}
        for path, values in edits.items():
            for length in range(1, len(path) + 1):
                expected.setdefault(path[:length], {})
            for name, (kind, data) in values.items():
                hive.put(path, name, kind, data)
                expected[path][name] = (kind, len(data), hashlib.sha256(data).hexdigest())
        require(hive.snapshot() == expected, 'Unrelated registry data changed during USB preparation.')
        require(hive.lib.hivex_commit(hive.handle, os.fsencode(output), 0) == 0, 'Cannot save portable Windows settings.')
    finally:
        hive.close()
    check = Hive(library, output)
    try:
        require(check.snapshot() == expected, 'Portable Windows settings failed readback.')
    finally:
        check.close()
    require(source.read_bytes() == original, 'The original Windows registry copy changed.')
    return dict(source_sha256=hashlib.sha256(original).hexdigest(),
                prepared_sha256=hashlib.sha256(output.read_bytes()).hexdigest(), controlsets=controlsets)


def prepare(owner, target, edition, wim, resources, system_source=None):
    choice = configure(owner, target, edition)
    if choice is None or choice['mode'] == 'normal':
        return dict(choice=choice, system=None) if choice else None
    folder = owner.session / 'usb-preparation'
    folder.mkdir(mode=0o700)
    source = folder / 'SYSTEM.original'
    # Compose with optional storage preparation rather than overwrite its edits.
    data = Path(system_source).read_bytes() if system_source is not None else run(
        [owner.wim, 'extract', wim, str(edition['index']), SYSTEM, '--to-stdout'])
    require(4096 <= len(data) <= 256 * 1024**2 and data[:4] == b'regf', 'Invalid Windows SYSTEM hive.')
    source.write_bytes(data)
    output = folder / 'SYSTEM.portable'
    receipt = prepare_hive(Path(resources) / 'lib/libhivex.0.dylib', source, output)
    return dict(choice=choice, system=output, receipt=receipt)


def apply(owner, plan, raw, guard):
    if plan is None or plan['system'] is None:
        return plan['choice'] if plan else None
    record = plan['receipt']
    sha = lambda data: hashlib.sha256(data).hexdigest()
    guard()
    require(sha(Path(plan['system']).read_bytes()) == record['prepared_sha256'], 'Prepared USB settings changed.')
    require(sha(run([owner.ntfscat, raw, SYSTEM])) == record['source_sha256'],
            'Installed Windows registry differs from the prepared USB source.')
    owner.note('Applying the selected portable USB settings to this Windows installation.')
    guard()
    run([owner.ntfscp, raw, plan['system'], SYSTEM])
    guard()
    require(sha(run([owner.ntfscat, raw, SYSTEM])) == record['prepared_sha256'], 'USB settings readback failed.')
    return dict(plan['choice'], settings=record, status='prepared-awaiting-first-boot')
