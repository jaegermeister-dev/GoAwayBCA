"""Prepare a verified backup for restore without opening the destination.

An ntfsclone stream preserves its source NTFS geometry. Sector-size changes
therefore require file-level capture/apply onto a freshly formatted destination.
This module only creates private scratch files. The executor owns consent,
destination identity checks, formatting, boot adaptation and menu publication.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

import backup_archive
import ntfsclone_image


def require(condition,message):
    if not condition:raise ValueError(message)


def file_identity(stream):
    node=os.fstat(stream.fileno())
    require(stat.S_ISREG(node.st_mode),'Select a regular Windows backup file.')
    return (node.st_dev,node.st_ino,node.st_size,node.st_mtime_ns,node.st_ctime_ns)


def materialize(source,work,note=lambda message:None):
    """Verify both container and NTFS image while recreating a sparse file.

    No selected target is touched, even if a late checksum/footer fails. The
    returned image is usable only after this method completes successfully.
    """
    work=Path(work)
    require(work.is_dir() and not work.is_symlink(),'A private restore working folder is required.')
    initial=file_identity(source)
    source.seek(0)
    reader=backup_archive.ImageReader(source)
    location=reader.metadata['source']
    destination=work/'verified-source.ntfs'
    note('Checking the backup and preparing its filesystem image...')
    descriptor=os.open(str(destination),os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'w+b') as output:
        output.truncate(location['partition_bytes'])
        def write(offset,data):
            output.seek(offset);backup_archive.write_all(output,data)
        geometry=ntfsclone_image.inspect(reader,location['partition_bytes'],
            location['logical_sector_size'],readback=write)
        require(reader.result is not None,'The Windows backup has no verified completion record.')
        require(file_identity(source)==initial,'The selected backup changed during verification.')
        output.flush();os.fsync(output.fileno())
    note('Backup checks completed. The restore destination has not been changed.')
    return dict(image=destination,metadata=reader.metadata,geometry=geometry,
        archive_sha256=reader.result['content_sha256'],source_identity=initial)


def conversion_check(image,probe,log):
    """Refuse lossy WIM conversion rather than silently omitting user data."""
    with Path(log).open('xb') as errors:
        completed=subprocess.run([str(probe),str(image)],stdout=subprocess.PIPE,stderr=errors,timeout=1800)
    require(completed.returncode==0,'The backup filesystem metadata could not be completely checked. '
            'No restore destination was changed. See the saved log.')
    require(len(completed.stdout)<=8192,'Invalid backup metadata inspection response.')
    value=json.loads(completed.stdout.decode('utf-8'))
    counts=('records_inspected','encrypted_records','extended_attribute_records','externally_backed_wof_records')
    require(type(value) is dict and value.get('schema')==1 and value.get('read_only') is True and
            all(type(value.get(k)) is int and value[k]>=0 for k in counts),
            'Incomplete backup metadata inspection response.')
    require(value['records_inspected']>0 and value.get('conversion_allowed') is True and
            all(value[k]==0 for k in counts[1:]),
            'This backup contains encrypted files, NTFS extended attributes or externally backed Windows files '
            'that cannot be safely converted to a different sector size by the included tools. '
            'Restore using the original logical sector size instead. No destination was changed.')
    return value


def inspect_windows(prepared, ntfscat):
    """Read the OS identity and marker from the VERIFIED private NTFS image.

    Archive labels cannot enable a disabled Windows version. File access is
    bounded and read-only; no physical destination or mounted filesystem is used.
    """
    from backup_windows import windows_identity
    from ntfs_read import raw_read
    from install_windows7 import Refusal
    from manage_installations import bounded_json, MARKER
    image=Path(prepared['image'])
    require(not image.is_symlink() and image.is_file(),'A verified regular filesystem image is required.')
    with image.open('rb') as stream:
        before=file_identity(stream)
        windows=windows_identity(raw_read(ntfscat,str(image),'/Windows/System32/winload.efi',8*1024**2))
        saved=prepared['metadata']['windows']
        require((windows['family'],windows['architecture'])==(saved['family'],saved['architecture']),
                'The Windows version or architecture in the backup does not match its saved metadata.')
        marker=None
        if prepared['metadata']['uninstall_eligible']:
            try:marker=bounded_json(raw_read(ntfscat,str(image),MARKER,1024**2))
            except (Refusal,ValueError,UnicodeError):pass
        require(file_identity(stream)==before,'The verified filesystem image changed during inspection.')
    return dict(windows=windows,marker=marker)


def prepare_conversion(prepared,work,wim,probe,note=lambda message:None):
    """Preserve NTFS semantics during a geometry change; never a macOS copy."""
    work=Path(work)
    check=conversion_check(prepared['image'],probe,work/'metadata-inspection.log')
    config=work/'capture-all-files.ini'
    with config.open('x',encoding='ascii') as output:output.write('[ExclusionList]\n')
    image=work/'restore-files.wim'
    require(not image.exists(),'Restore preparation already exists; choose a new working folder.')
    note('Preparing files for the destination filesystem geometry...')
    log=work/'conversion-capture.log'
    with log.open('xb') as output:
        completed=subprocess.run([str(wim),'capture',str(prepared['image']),str(image),
            'Windows partition restore','--compress=LZX','--check','--strict-acls','--norpfix',
            '--config='+str(config)],stdout=output,stderr=subprocess.STDOUT,
            env=dict(os.environ,LC_ALL='C'))
    require(completed.returncode==0,'Preparing the sector-size conversion failed. No destination was changed.')
    # libntfs-3g/wimlib may warn about otherwise-skipped metadata. Do not let a
    # successful subprocess status turn that warning into a successful backup.
    with log.open('rb') as output:
        for line in output:
            require(not re.search(rb'(?:^|\r)\s*(?:\[WARNING\]|WARNING:)',line,re.I),
                    'A filesystem conversion warning requires review before '
                    'restoring. No destination was changed; see conversion-capture.log.')
    digest=hashlib.sha256()
    with image.open('rb') as stream:
        for data in iter(lambda:stream.read(1024*1024),b''):digest.update(data)
    return dict(wim=image,wim_sha256=digest.hexdigest(),metadata_check=check,
        source_logical_sector_size=prepared['geometry']['logical_sector_size'])
