"""Pure-EFI architecture checks. No disk writes or legacy-boot fallback.

The host app ABI and CPU width are deliberately not firmware evidence.
The only accepted observation is firmware-abi on the IODeviceTree efi node.
This module does not claim that matching bitness guarantees Windows boots.
"""
import plistlib
import subprocess
from xml.parsers.expat import ExpatError

MAX_PLIST_BYTES = 2 * 1024 * 1024
IOREG_COMMAND = ('/usr/sbin/ioreg', '-a', '-p', 'IODeviceTree', '-r', '-n', 'efi', '-d', '1')
FIRMWARE_REFERENCE = 'https://learn.microsoft.com/en-us/windows-hardware/drivers/bringup/frequently-asked-questions'
LEGACY_GUIDES = (
    {
        'title': 'Legacy installation example: Windows 7 / Vista x64 on older Macs',
        'url': 'https://blog.andersonshatch.com/2009/11/08/select-cd-rom-boot-type-prompt-during-vista-server-2008-r2-windows-7-x64-boot-on-macbook/',
        'families': ('Windows 7', 'Windows Vista'),
        'scope': 'Historical third-party optical-disc guide; not tested by this project. Model and driver compatibility vary.'
    },
    {
        'title': 'Legacy installation example: Windows 10 on Mac Pro (2006)',
        'url': 'https://virtuallyfun.com/2018/12/26/installing-windows-10-pro-onto-an-older-mac-pro/',
        'families': ('Windows 10',),
        'scope': 'Author-reported MacPro1,1 optical-disc installation; not a guarantee for another Mac or Windows release.'
    },
    {
        'title': 'Background: identifying EFI versus BIOS boot mode',
        'url': 'https://www.rodsbooks.com/refind/bootmode.html',
        'families': (),
        'scope': 'Boot-manager author documentation. Explains boot modes; not an installation method supplied by this app.'
    }
)


def _text(value):
    if isinstance(value, bytes):
        try:
            return value.rstrip(b'\0').decode('ascii')
        except UnicodeError:
            return None
    if isinstance(value, str):
        return value.rstrip('\0')
    return None


def unknown(reason):
    return dict(status='unknown', bits=None, abi=None, source='IODeviceTree:/efi/firmware-abi', reason=reason)


def parse_ioreg(data):
    """Parse XML/binary plist. Missing, malformed or conflicting data is unknown."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_PLIST_BYTES:
        return unknown('Firmware response is empty, invalid or too large.')
    try:
        tree = plistlib.loads(data)
    except (ValueError, TypeError, OverflowError, RecursionError, plistlib.InvalidFileException, ExpatError):
        return unknown('Firmware response is not a valid property list.')
    stack, observations = [tree], []
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            if _text(node.get('IORegistryEntryName')) == 'efi':
                observations.append(_text(node.get('firmware-abi')))
            children = node.get('IORegistryEntryChildren')
            if isinstance(children, list):
                stack.extend(children)
    if not observations:
        return unknown('The EFI node did not expose its firmware architecture.')
    values = set(observations)
    if len(values) != 1 or next(iter(values)) not in ('EFI32', 'EFI64'):
        return unknown('Firmware architecture data is conflicting or unrecognised.')
    abi = next(iter(values))
    return dict(status='verified', bits=int(abi[3:]), abi=abi,
                source='IODeviceTree:/efi/firmware-abi', reason='')


def read_firmware(run=subprocess.run):
    """Read only. Do not substitute uname, hw.cpu64bit_capable or a model table."""
    try:
        result = run(list(IOREG_COMMAND), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return unknown('Could not read EFI architecture from macOS. Retry or collect a hardware report.')
    if result.returncode != 0:
        return unknown('macOS could not read EFI architecture. Retry or collect a hardware report.')
    return parse_ioreg(result.stdout)


def _guides(family):
    # Never advertise a Windows 10 recipe as a verified Windows 11 procedure.
    return [dict(item) for item in LEGACY_GUIDES if not item['families'] or family in item['families']]


def assess(edition, firmware):
    """Hard architecture gate; compatibility advisories elsewhere may be bypassed."""
    if not isinstance(firmware, dict):
        firmware = unknown('Invalid firmware observation.')
    family = edition.get('family') or 'the selected Windows version'
    architecture = edition.get('architecture')
    bits = {0: 32, 9: 64}.get(architecture) if type(architecture) is int else None
    result = dict(allowed=False, code=None, image_bits=bits, firmware=firmware,
                  message='', links=[], buttons=['Choose another Windows image', 'Cancel'])
    if bits is None:
        result.update(code='image-architecture-unknown', message=
            'The selected image is not a verified x86 or x64 Windows image. '
            'Its architecture must be established before installation. Nothing will be formatted.')
        return result
    if (firmware.get('status') != 'verified' or type(firmware.get('bits')) is not int
            or firmware.get('bits') not in (32, 64)
            or firmware.get('abi') != 'EFI' + str(firmware['bits'])):
        result.update(code='firmware-architecture-unknown', message=
            'The Mac\'s EFI architecture could not be confirmed. CPU bitness does not establish '
            'firmware bitness. Retry detection or collect a hardware report before installing. '
            'Nothing will be formatted.', buttons=['Retry detection', 'Cancel'])
        return result
    if bits != firmware['bits']:
        result.update(code='firmware-image-mismatch', message=
            'The selected image contains %d-bit %s, but this Mac reports %d-bit EFI. '
            'Windows\' normal EFI boot requires matching architectures. '
            'This app only supports pure EFI installation and cannot install this combination. '
            'Nothing will be formatted.' % (bits, family, firmware['bits']))
        if bits == 64 and firmware['bits'] == 32:
            result['message'] += (
                '\n\nIf the CPU supports 64-bit Windows, other people have installed it on some '
                'older Macs using BIOS/legacy boot. You can read their methods separately. '
                'Those methods may require optical media, different partitioning and model-specific '
                'drivers. They are outside this app\'s support and are not guaranteed for your Mac '
                'or Windows version. Back up your data before following an external guide. '
                'This app will not perform those steps or create a hybrid MBR.')
            result.update(links=_guides(family),
                buttons=['Read alternative installation guide', 'Choose another Windows image', 'Cancel'])
        return result
    result.update(allowed=True, code='architecture-match', message='', buttons=[])
    return result


def assess_windows_loader(edition):
    """Separate OS capability check, not an inference from matching bitness.

    A successful result is not verification of the loader files on the ISO.
    The deployment engine still validates/extracts the selected image's loader.
    """
    family = edition.get('family')
    result = dict(allowed=True, code='loader-version-check', message='', links=[])
    if type(edition.get('architecture')) is int and edition['architecture'] == 0 and family in ('Windows 7', 'Windows Vista'):
        result.update(allowed=False, code='windows-x86-no-efi-loader', message=
            '32-bit %s does not provide the Windows EFI boot path required by this app. '
            'Matching 32-bit firmware does not change that. This app will not switch to BIOS '
            'boot or create a hybrid MBR. Nothing will be formatted.' % family,
            links=_guides(family),
            buttons=['Read alternative installation guide', 'Choose another Windows image', 'Cancel'])
        return result
    return result


class FirmwareCompatibilityError(ValueError):
    def __init__(self, result):
        self.result = result
        super().__init__(result['message'])


def require_compatible(edition, reader=None):
    result = assess(edition, (reader or read_firmware)())
    if not result['allowed']:
        raise FirmwareCompatibilityError(result)
    return result


def configure(edition, interaction, reader=None):
    """No permissive GUI reply can override a known mismatch or unknown ABI.

    External guides are opened by the unelevated native UI, which retains the
    blocking dialog. Only unknown firmware can request a new observation.
    """
    while True:
        result = assess(edition, (reader or read_firmware)())
        if result['allowed']:
            loader = assess_windows_loader(edition)
            if loader['allowed']:
                return result
            result = loader
        reply = interaction.request('firmware_block', result['message'], code=result['code'],
            links=[{key: item[key] for key in ('title', 'url', 'scope')} for item in result['links']])
        if result['code'] == 'firmware-architecture-unknown' and reply == 'retry':
            continue
        # Includes a fabricated Continue/Try anyway, and the legitimate
        # Choose another image action. The user can select a new ISO in the app.
        raise FirmwareCompatibilityError(result)


def revalidate(edition, previous, reader=None):
    current = require_compatible(edition, reader)
    loader = assess_windows_loader(edition)
    if not loader['allowed']:
        raise FirmwareCompatibilityError(loader)
    if (not previous.get('allowed') or previous.get('image_bits') != current['image_bits']
            or previous.get('firmware', {}).get('abi') != current['firmware']['abi']):
        raise ValueError('Image or firmware architecture changed after preparation. Start again before formatting.')
    return current
