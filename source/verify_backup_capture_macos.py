"""Exercise the actual capture component against disposable NTFS files only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import backup_archive
import backup_capture
import backup_metadata
from backup_file_access import open_backup


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(1024*1024),b''):digest.update(data)
    return digest.hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--tools',required=True,type=Path)
    parser.add_argument('--clone',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    assert sys.platform=='darwin' and os.geteuid()!=0
    args.output.mkdir(mode=0o700)
    evidence=[]
    for sector in (512,4096):
        folder=args.output/str(sector);folder.mkdir()
        original=folder/'source.ntfs';size=64*1024**2
        with original.open('xb') as stream:stream.truncate(size)
        def run(name,command):
            result=subprocess.run([str(value) for value in command],stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT,timeout=90)
            (folder/(name+'.log')).write_bytes(result.stdout)
            assert result.returncode==0,result.stdout[-3000:]
            return result.stdout
        run('format',[args.tools/'mkntfs','-F','-Q','-s',str(sector),'-c','4096',original])
        data=folder/'fixture.txt';data.write_bytes(bytes(range(256))*1000+b'Windows backup component fixture')
        run('populate',[args.tools/'ntfscp',original,data,'/fixture.txt'])
        before=sha(original)
        location=dict(serial='fixture',serial_kind='physical-drive',model='Fixture',
            disk_guid=str(uuid.uuid4()),partition_guid=str(uuid.uuid4()),logical_sector_size=sector,
            physical_sector_size=None,connection='Internal',controller='regular-file fixture',adapter=None,
            connection_path=None,bytes=size+1024**2,partition_bytes=size,partition_offset=1024**2,layout=[])
        meta=backup_metadata.create(location,dict(family='Windows 10',architecture=9),False)
        output=folder/'backup.gabackup';guards=[];messages=[]
        def guard():
            assert sha(original)==before
            guards.append('unchanged')
        with open_backup(os.getuid(),str(output),True) as stream:
            result=backup_capture.capture(args.clone,original,stream,meta,folder/'capture.log',guard,messages.append)
        assert len(guards)==3 and sha(original)==before
        saved=folder/'backup.ntfsclone'
        with open_backup(os.getuid(),str(output)) as stream,saved.open('xb') as raw:
            checked=backup_archive.verify(stream,raw)
        assert checked['content_sha256']==result['content_sha256']
        assert checked['metadata']['uninstall_eligible'] is False
        restored=folder/'restored.ntfs'
        run('restore-fixture',[args.clone,'--restore-image','--output',restored,saved])
        assert sha(restored)==before
        assert run('readback',[args.tools/'ntfscat',restored,'/fixture.txt'])==data.read_bytes()
        # User file helper must refuse both replacement and a symlink alias.
        for candidate in (output,folder/'link.gabackup'):
            if candidate!=output:candidate.symlink_to(output)
            try:
                with open_backup(os.getuid(),str(candidate),True):pass
            except (ValueError,OSError):pass
            else:raise AssertionError('Existing output was accepted')
        assert sha(original)==before
        evidence.append(dict(sector_size=sector,archive_bytes=output.stat().st_size,
            source_unchanged=True,restored_fixture_identical=True,readback=True,
            user_owned_output=output.stat().st_uid==os.getuid(),existing_file_refused=True,
            symlink_refused=True,guards=len(guards),progress_messages=len(messages)))
    result=dict(status='PASS',python=sys.version.split()[0],physical_devices_opened=False,
                windows_installed=False,fixtures=evidence)
    (args.output/'RESULTS.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
