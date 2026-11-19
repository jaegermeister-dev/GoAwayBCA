"""Read-only NTFS capture and complete readback before reporting success.

The caller owns discovery, consent, source identity/unmount checks and the
user-opened output descriptor. This module never formats, repairs, restores,
mounts or changes the source. A failed capture is retained, never deleted.
"""
import os
from pathlib import Path
import select
import subprocess
import time

import backup_archive
import ntfsclone_image


def require(condition, message):
    if not condition:
        raise ValueError(message)


class ProcessReader:
    """Drain one producer, with honest wait messages during long reads."""
    def __init__(self, pipe, note):
        self.pipe, self.note = pipe, note
        self.started = time.monotonic()
        self.last = self.started

    def read(self, size):
        parts = []; remaining = size
        while remaining:
            ready, _, _ = select.select([self.pipe], [], [], 5)
            now = time.monotonic()
            if now-self.last >= 5:
                self.note('Reading the Windows filesystem — %d seconds elapsed.' % (now-self.started))
                self.last = now
            if not ready:
                continue
            chunk = os.read(self.pipe.fileno(), min(remaining, 1024*1024))
            if not chunk:
                break
            parts.append(chunk); remaining -= len(chunk)
        return b''.join(parts)


def capture(tool, source, output, metadata, log, guard, note=lambda message: None):
    """Capture an unmounted source and verify the resulting user-owned file.

    guard is required before opening the source and before the footer. Never
    use rescue, ignore-check or force options to produce a nominal success.
    """
    import backup_metadata
    backup_metadata.validate(metadata)
    guard()
    require(output.tell() == 0 and os.fstat(output.fileno()).st_size == 0,
            'A new, empty backup file is required. Existing backups are never overwritten.')
    command = [str(tool), '--save-image', '--full-logfile', '--output', '-', str(source)]
    started = time.monotonic(); last = [0.0]
    def progress(amount):
        now = time.monotonic()
        if now-last[0] >= 1:
            note('Backing up used Windows filesystem data: %.1f MiB read (%d seconds elapsed).' %
                 (amount/1024**2, now-started))
            last[0] = now
    with Path(log).open('xb') as errors:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
        try:
            def finish():
                while True:
                    try:
                        code = child.wait(timeout=5)
                        break
                    except subprocess.TimeoutExpired:
                        note('Waiting for the Windows filesystem backup to finish...')
                require(code == 0, 'Windows backup could not finish. The incomplete file was retained; '
                        'do not use it as a backup. See the saved log. No source files were changed.')
                guard()
            result = backup_archive.capture(ProcessReader(child.stdout, note), output, metadata, progress, finish)
        finally:
            child.stdout.close()
            if child.poll() is None:
                child.kill()
            child.wait()
    output.flush(); os.fsync(output.fileno())
    note('Checking the entire saved backup and its NTFS filesystem image...')
    output.seek(0)
    last[0] = 0.0
    def checking(amount):
        now = time.monotonic()
        if now-last[0] >= 1:
            note('Verifying saved Windows backup data: %.1f MiB checked.' % (amount/1024**2))
            last[0] = now
    reader = backup_archive.ImageReader(output, checking)
    location = metadata['source']
    geometry = ntfsclone_image.inspect(reader, location['partition_bytes'], location['logical_sector_size'])
    require(reader.result is not None and reader.result['content_sha256'] == result['content_sha256'],
            'Saved backup readback did not match. Keep the original Windows installation.')
    guard()
    note('Backup data and filesystem image verified. This is not a test of restoring or booting Windows.')
    return dict(result, geometry=geometry, archive_bytes=os.fstat(output.fileno()).st_size,
                full_readback_verified=True, source_modified=False, restore_boot_tested=False)
