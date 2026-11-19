"""Confirmed partition-only writes of a fully prepared NTFS clone stream.

The executor owns consent, fresh target identity checks and EFI publication.
Only allocated NTFS clusters and the alternate boot sector are copied. Complete
readback precedes success. No whole-disk path is accepted by the device opener.
"""
import hashlib
import os
import re
import stat
import time

import ntfsclone_image
from backup_archive import write_all
from backup_restore_identity import require, regular


def transfer(source, target, expected_bytes, expected_sector, expected_sha256, guard, note=lambda text:None):
    """Core stream operation, also exercised against synthetic regular files."""
    initial=os.fstat(source.fileno())
    require(stat.S_ISREG(initial.st_mode), 'The prepared restore stream is not a regular file.')
    stamp=lambda n:(n.st_dev,n.st_ino,n.st_size,n.st_mtime_ns,n.st_ctime_ns)
    digest=hashlib.sha256()
    source.seek(0)
    class Reader:
        def read(self,size=-1):
            data=source.read(size);digest.update(data);return data
    ntfsclone_image.inspect(Reader(),expected_bytes,expected_sector)
    require(digest.hexdigest()==expected_sha256, 'The prepared restore stream changed before writing.')
    require(stamp(os.fstat(source.fileno()))==stamp(initial), 'The prepared restore stream changed while being checked.')
    source.seek(0);guard()
    start=0;buffer=bytearray();written=0;last=time.monotonic()
    def flush():
        nonlocal buffer,written,last
        if not buffer:return
        target.seek(start);write_all(target,buffer);written+=len(buffer);buffer=bytearray()
        if time.monotonic()-last>=5:
            guard();note('Restoring Windows partition: %.1f GiB written.'%(written/1024**3));last=time.monotonic()
    def copy(offset,data):
        nonlocal start,buffer
        if buffer and (offset!=start+len(buffer) or len(buffer)+len(data)>4*1024**2):flush()
        if not buffer:start=offset
        buffer.extend(data)
    note('Writing the prepared Windows filesystem to the confirmed partition...')
    geometry=ntfsclone_image.inspect(source,expected_bytes,expected_sector,readback=copy)
    flush();target.flush();os.fsync(target.fileno());guard()
    require(stamp(os.fstat(source.fileno()))==stamp(initial), 'The prepared stream changed during restoration.')
    source.seek(0);last=time.monotonic();checked=0;digest=hashlib.sha256()
    def compare(offset,data):
        nonlocal checked,last
        target.seek(offset)
        require(target.read(len(data))==data, 'Restored partition data failed readback. Keep the original backup.')
        checked+=len(data)
        if time.monotonic()-last>=5:
            guard();note('Checking restored Windows partition: %.1f GiB verified.'%(checked/1024**3));last=time.monotonic()
    note('Checking all restored filesystem data before updating the Windows boot menu...')
    ntfsclone_image.inspect(Reader(),expected_bytes,expected_sector,readback=compare)
    require(digest.hexdigest()==expected_sha256 and stamp(os.fstat(source.fileno()))==stamp(initial),
            'The prepared stream changed during restore verification.')
    guard()
    return dict(allocated_bytes_written=written,allocated_bytes_verified=checked,geometry=geometry,
                source_sha256=expected_sha256,complete_readback=True)


def write_partition(prepared, device, guard, note=lambda text:None):
    require(type(device) is str and re.fullmatch(r'/dev/disk[0-9]+s[0-9]+',device),
            'Restore writes require one selected partition, never a whole disk.')
    source=regular(prepared['image'])
    guard()
    descriptor=os.open(device,os.O_RDWR|os.O_NOFOLLOW)
    with os.fdopen(descriptor,'r+b',buffering=4*1024**2) as target,source.open('rb') as stream:
        node=os.fstat(target.fileno());path=os.stat(device)
        require(stat.S_ISBLK(node.st_mode) and node.st_rdev==path.st_rdev,
                'The restore destination is not the verified partition block device.')
        guard()
        geometry=prepared['geometry']
        return transfer(stream,target,geometry['bytes'],geometry['logical_sector_size'],
                        prepared['sha256'],guard,note)
