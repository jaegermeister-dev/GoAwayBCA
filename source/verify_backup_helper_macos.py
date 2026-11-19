"""Exercise full used-cluster backup/restoration on disposable regular files.

No /dev path, mount, sudo, installed Windows, or physical drive is involved.
These are filesystem fixtures, not Windows boot acceptance tests.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

import backup_archive
import backup_metadata
import ntfsclone_image
import test_release_packaging


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', required=True, type=Path)
    parser.add_argument('--clone', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    assert sys.platform == 'darwin' and os.geteuid() != 0
    args.output.mkdir(mode=0o700)
    evidence = []
    for sector in (512,4096):
        directory = args.output / str(sector); directory.mkdir()
        original = directory / 'source.ntfs'
        restored = directory / 'restored.ntfs'
        archive = directory / 'fixture.gabackup'
        image = directory / 'ntfsclone.img'
        size = 64 * 1024**2
        with original.open('xb') as output:
            output.truncate(size)
        data = directory / 'fixture.bin'
        data.write_bytes(bytes(range(256))*2048 + b'complete fixture content')
        def execute(name, command):
            result = subprocess.run([str(x) for x in command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            (directory / (name+'.log')).write_bytes(result.stdout)
            assert result.returncode == 0, (name,result.stdout[-3000:])
            return result.stdout
        execute('format', [args.tools/'mkntfs', '-F', '-Q', '-s', str(sector), '-c', '4096', '-L', 'BackupFixture', original])
        execute('populate', [args.tools/'ntfscp', original, data, '/fixture.bin'])
        before = sha(original)
        location = dict(serial='synthetic-file-fixture', serial_kind='physical-drive', model='Fixture',
            disk_guid=str(uuid.uuid4()), partition_guid=str(uuid.uuid4()), logical_sector_size=sector,
            physical_sector_size=None, connection='Internal', controller='regular-file-fixture', adapter=None,
            connection_path=None, bytes=size+1024**2, partition_bytes=size, partition_offset=1024**2, layout=[])
        metadata = backup_metadata.create(location, dict(family='Windows 10',architecture=9), False)
        with (directory/'capture.log').open('wb') as log, archive.open('xb') as destination:
            child = subprocess.Popen([str(args.clone), '--save-image', '--full-logfile', '--output', '-', str(original)],
                                     stdout=subprocess.PIPE, stderr=log)
            try:
                captured = backup_archive.capture(child.stdout, destination, metadata)
                assert child.wait(timeout=30) == 0, (directory/'capture.log').read_text(errors='replace')[-3000:]
            finally:
                child.stdout.close()
                if child.poll() is None: child.kill()
                child.wait()
        assert sha(original) == before
        with archive.open('rb') as source, image.open('xb') as output:
            verified = backup_archive.verify(source, output)
        assert verified['content_sha256'] == captured['content_sha256']
        with image.open('rb') as saved:
            geometry=ntfsclone_image.inspect(saved,size,sector)
        execute('restore', [args.clone,'--restore-image','--output',restored,image])
        assert sha(original) == before
        assert sha(restored) == before, 'Fresh synthetic NTFS image differed after used-cluster restore'
        actual = execute('readback', [args.tools/'ntfscat',restored,'/fixture.bin'])
        assert actual == data.read_bytes()
        assert archive.stat().st_size < size//3, 'Backup copied unused space instead of allocated data'
        evidence.append(dict(sector=sector,source_bytes=size,backup_bytes=archive.stat().st_size,
            original_unchanged=True,restored_bytes_identical=True,content_readback=True,
            provenance=verified['metadata']['origin']['kind'],geometry=geometry))
    suite=unittest.defaultTestLoader.loadTestsFromModule(test_release_packaging)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    assert result.wasSuccessful() and not result.skipped
    report=dict(status='PASS',python=sys.version.split()[0],device_access=False,windows_boot_test=False,
        filesystem_fixtures=evidence,release_packaging_tests=result.testsRun)
    (args.output/'VERIFIED.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
