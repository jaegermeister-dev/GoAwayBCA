"""Private acceptance profile for the owner's audited FlashBoot Win7 media.

Contains identifiers/hashes only, not FlashBoot or Microsoft payloads. It does
not reproduce patches, change licensing/activation, or download drivers.
Unaudited prepared media is deliberately refused in this engineering version.
"""
import hashlib
from pathlib import Path
import shutil

PROFILE = 'owner-flashboot-3.4-win7-sp1-x64-20261007'
STOCK_WIM_SHA256 = '6439b89b59bf07dda1c3a345fc76a97f207fabf698a5946271eb676de053d62f'
PREPARED_WIM_SHA256 = '42f6c3e23a0325620b295c6bae1973659be8d58dc836ab8f3e74ebe81de78f58'
PREPARED_WIM_BYTES = 2903245695
LOADER_SHA256 = '6470d503fc884f23b515457a03018ece8bf6f6c5bd358f3b5518711ebadb5161'
READBACK_PATHS = (
    '/Windows/Boot/EFI/bootmgfw.efi',
    '/Windows/System32/drivers/usbxhci.sys',
    '/Windows/System32/drivers/ucx01000.sys',
    '/Windows/System32/drivers/usbhub3.sys',
    '/Windows/System32/drivers/usbd8.sys',
    '/Windows/System32/drivers/Wdf01000.sys',
    '/Windows/System32/drivers/WdfLdr.sys',
    '/Windows/System32/drivers/stornvme.sys',
    '/Windows/System32/drivers/storport.sys',
    '/Windows/System32/drivers/Classpnp.sys',
    '/Windows/inf/oem23.inf',
    '/Windows/inf/oem28.inf',
    '/Windows/System32/config/SYSTEM',
    '/Windows/System32/config/SOFTWARE',
)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def child(root, name):
    matches = [p for p in root.iterdir() if p.name.casefold() == name.casefold()]
    if len(matches) != 1 or matches[0].is_symlink():
        raise ValueError('Missing, ambiguous or symlinked prepared-media path: ' + name)
    return matches[0]


def snapshot(media, stock_wim, session, note=None):
    """Copy to the private session and verify BEFORE selecting/erasing a target.

    This fixes the source for the whole run and leaves the owner's USB/backup
    untouched. No fallback to a stock WIM is permitted after a profile failure.
    """
    media = Path(media).expanduser().resolve(strict=True)
    if not media.is_dir():
        raise ValueError('Prepared media must be a directory containing sources/install.wim and efi/boot/bootx64.efi.')
    source = child(child(media, 'sources'), 'install.wim')
    loader = child(child(child(media, 'efi'), 'boot'), 'bootx64.efi')
    if not source.is_file() or source.stat().st_size != PREPARED_WIM_BYTES:
        raise ValueError('Prepared WIM does not match the audited image size.')
    if not loader.is_file() or digest(loader) != LOADER_SHA256:
        raise ValueError('Prepared EFI loader does not match the audited image.')
    from operation_progress import Meter,hash_file
    hash_progress=lambda path,label: hash_file(path,note,label) if note else digest(path)
    if hash_progress(stock_wim,'Verifying original Windows 7 source') != STOCK_WIM_SHA256:
        raise ValueError('The selected ISO is not the original ISO used for this prepared-media profile.')
    if shutil.disk_usage(session).free < PREPARED_WIM_BYTES + 1024**3:
        raise ValueError('Need at least prepared-WIM size plus 1 GiB free for the private source snapshot.')
    dest = Path(session) / 'prepared-source'
    dest.mkdir(mode=0o700)
    copied = dest / 'install.wim'
    meter=Meter(note,'Preparing private Windows 7 image',PREPARED_WIM_BYTES) if note else None
    done=0
    with source.open('rb') as src, copied.open('xb') as dst:
        for data in iter(lambda:src.read(1024*1024),b''):
            dst.write(data);done+=len(data)
            if meter:meter.update(done)
    copied.chmod(0o400)
    if copied.stat().st_size != PREPARED_WIM_BYTES or hash_progress(copied,'Verifying private Windows 7 image') != PREPARED_WIM_SHA256:
        raise ValueError('Prepared WIM hash mismatch. Stopping before any target erase.')
    return copied, {'profile': PROFILE, 'source_directory': str(media),
                    'snapshot': str(copied), 'wim_sha256': PREPARED_WIM_SHA256,
                    'boot_manager_sha256': LOADER_SHA256,
                    'boot_tested': False, 'includes_supplied_storage_usb_drivers': True}
