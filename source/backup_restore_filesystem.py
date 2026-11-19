"""Prepare and validate a complete restore image using private regular files.

Physical destination writes belong to the separately confirmed restore executor.
Nothing here accepts a block device. A failed preparation leaves scratch files
for the operation report and never edits the selected backup container.
"""
import hashlib
import os
from pathlib import Path
import subprocess

from backup_restore_identity import regular, boot_geometry, require
import backup_restore_preparation
import ntfsclone_image


def command(arguments, log, note=lambda text: None, answer=None):
    with Path(log).open('xb') as output:
        process = subprocess.Popen([str(x) for x in arguments],
            stdin=subprocess.PIPE if answer is not None else subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, env=dict(os.environ, LC_ALL='C'))
        try:
            if answer is not None:
                process.stdin.write(answer); process.stdin.close()
            while True:
                try:
                    status = process.wait(timeout=5)
                    break
                except subprocess.TimeoutExpired:
                    note('Preparing the private restore image; the destination has not been changed...')
            require(status == 0, 'Restore image preparation failed. No destination was changed. See '+str(log))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def resize_private(image, destination_bytes, expected_sector, tool, work, note=lambda text: None):
    image = regular(image); work = Path(work)
    require(type(destination_bytes) is int and destination_bytes > 0 and
            expected_sector in (512, 4096) and destination_bytes % expected_sector == 0,
            'The restore destination capacity is not aligned to its logical sector size.')
    with image.open('rb') as source:
        original = boot_geometry(source)
    require(original['sector_size'] == expected_sector, 'An NTFS image cannot be resized across logical sector sizes.')
    if original['bytes'] == destination_bytes:
        return dict(resized=False, requires_windows_filesystem_check=False)
    note('Checking whether the backup filesystem fits the selected partition...')
    if destination_bytes > original['bytes']:
        with image.open('r+b') as source:
            source.truncate(destination_bytes)
    # Neither run overrides a pre-existing dirty or damaged filesystem. The
    # confirmation is supplied only after the read-only dry run succeeded.
    command([tool, '--no-progress-bar', '--no-action', '--size', destination_bytes, image],
            work/'resize-preview.log', note)
    command([tool, '--no-progress-bar', '--size', destination_bytes, image],
            work/'resize-prepare.log', note, b'y\n')
    if destination_bytes < original['bytes']:
        with image.open('r+b') as source:
            source.truncate(destination_bytes)
    with image.open('rb') as source:
        verified = boot_geometry(source)
    require(verified['sector_size'] == expected_sector and verified['bytes'] == destination_bytes,
            'The prepared filesystem does not fit the selected destination geometry.')
    # ntfsresize deliberately schedules Windows' filesystem check. Keep it.
    return dict(resized=True, source_bytes=original['bytes'], destination_bytes=destination_bytes,
                requires_windows_filesystem_check=True)


def convert_private(prepared, destination, tools, probe, work, note=lambda text: None):
    """Different logical sectors require a new private filesystem, not raw copy."""
    work = Path(work); tools = Path(tools)
    regular(prepared['image'])
    converted = backup_restore_preparation.prepare_conversion(
        prepared, work, tools/'wimlib-imagex', probe, note)
    size, sector = destination['partition_bytes'], destination['logical_sector_size']
    require(type(size) is int and sector in (512, 4096) and size > 0 and size % sector == 0,
            'Invalid restore destination geometry.')
    image = work/'destination.ntfs'
    with image.open('xb') as output:
        output.truncate(size)
    regular(image)
    command([tools/'mkntfs', '-F', '-Q', '-s', sector, '-L', 'Windows', image],
            work/'conversion-format-private.log', note)
    command([tools/'wimlib-imagex', 'apply', converted['wim'], '1', image, '--check', '--strict-acls'],
            work/'conversion-apply-private.log', note)
    # Refuse warnings for skipped metadata even when the process exits zero.
    import re
    with (work/'conversion-apply-private.log').open('rb') as output:
        require(not any(re.search(rb'(?:^|\r)\s*(?:\[WARNING\]|WARNING:)', line, re.I) for line in output),
                'The restore conversion reported a metadata warning. No destination was changed.')
    with image.open('rb') as source:
        verified = boot_geometry(source)
    require(verified['sector_size'] == sector and verified['bytes'] == size,
            'The converted filesystem has unexpected destination geometry.')
    return dict(image=image, conversion=converted, requires_windows_filesystem_check=False)


def export_verified(image, clone, work, resized=False, note=lambda text: None):
    """Export a checked sparse stream AFTER all private-image adaptations.

    The force flag is permitted only for this private image after our successful
    resize, because ntfsresize intentionally leaves the consistency-check flag.
    ntfsclone still performs its allocation consistency checks; no rescue flag.
    """
    image = regular(image); work = Path(work)
    with image.open('rb') as source:
        geometry = boot_geometry(source)
    output = work/'prepared-restore.ntfsclone'
    require(not output.exists() and not output.is_symlink(), 'Use a new prepared restore stream.')
    args = [clone, '--save-image', '--full-logfile']
    if resized:
        args.append('--force')
    args += ['--output', output, image]
    command(args, work/'prepared-image-check.log', note)
    note('Verifying the complete prepared restore stream...')
    digest = hashlib.sha256()
    with output.open('rb') as stream:
        class Reader:
            def read(self, size=-1):
                data = stream.read(size); digest.update(data); return data
        checked = ntfsclone_image.inspect(Reader(), geometry['bytes'], geometry['sector_size'])
    return dict(image=output, sha256=digest.hexdigest(), geometry=checked,
                requires_windows_filesystem_check=resized)
