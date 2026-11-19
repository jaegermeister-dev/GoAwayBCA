"""Cross-sector NTFS fixtures only. No Windows or physical devices."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import uuid

import backup_archive
import backup_metadata
import backup_restore_preparation as preparation

def sha(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(1024*1024),b''):value.update(data)
    return value.hexdigest()

def main():
    parser=argparse.ArgumentParser()
    for name in ('tools','clone','probe','metadata-fixture','output'):parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args()
    assert sys.platform=='darwin' and os.geteuid()!=0
    root=args.output.resolve();root.mkdir(exist_ok=False)
    results=[]
    for source_sector,target_sector in ((512,4096),(4096,512)):
        work=root/('%s-to-%s'%(source_sector,target_sector));work.mkdir()
        size=64*1024**2
        original=work/'original.ntfs';target=work/'target.ntfs'
        for file in (original,target):
            with file.open('xb') as output:output.truncate(size)
        counter=0
        def run(argv):
            nonlocal counter
            counter+=1
            result=subprocess.run([str(x) for x in argv],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                  env=dict(os.environ,LC_ALL='C'),timeout=180)
            (work/('command-%02d.log'%counter)).write_bytes(result.stdout)
            assert result.returncode==0,result.stdout[-4000:]
            return result.stdout
        run([args.tools/'mkntfs','-F','-Q','-s',source_sector,'-c',4096,'-L','BackupSource',original])
        data=work/'fixture.bin';data.write_bytes(bytes(range(256))*8192+b'NTFS sector conversion fixture')
        run([args.tools/'ntfscp',original,data,'/fixture.bin'])
        stream_data=work/'stream.bin';stream_data.write_bytes(b'Named stream retained across sector conversion')
        run([args.tools/'ntfscp','-N','FixtureStream',original,stream_data,'/fixture.bin'])
        before=sha(original)
        source=dict(serial='file-fixture',serial_kind='physical-drive',model='Synthetic NTFS file',
            disk_guid=str(uuid.uuid4()),partition_guid=str(uuid.uuid4()),logical_sector_size=source_sector,
            physical_sector_size=None,connection='Internal',controller='regular-file-fixture',adapter=None,
            connection_path=None,bytes=size+1024**2,partition_bytes=size,partition_offset=1024**2,layout=[])
        metadata=backup_metadata.create(source,dict(family='Windows 10',architecture=9),False)
        archive=work/'fixture.gabackup'
        with (work/'clone.log').open('wb') as errors,archive.open('xb') as destination:
            child=subprocess.Popen([str(args.clone),'--save-image','--full-logfile','--output','-',str(original)],
                                   stdout=subprocess.PIPE,stderr=errors)
            def finish():assert child.wait(timeout=60)==0
            try:backup_archive.capture(child.stdout,destination,metadata,finish=finish)
            finally:
                child.stdout.close()
                if child.poll() is None:child.kill()
                child.wait()
        staging=work/'prepare';staging.mkdir()
        with archive.open('rb') as source_file:prepared=preparation.materialize(source_file,staging)
        assert sha(prepared['image'])==before,'Materialized NTFS changed the original content or metadata'
        converted=preparation.prepare_conversion(prepared,staging,args.tools/'wimlib-imagex',args.probe)
        run([args.tools/'mkntfs','-F','-Q','-s',target_sector,'-c',4096,'-L','RestoreTarget',target])
        run([args.tools/'wimlib-imagex','apply',converted['wim'],'1',target,'--check','--strict-acls'])
        assert run([args.tools/'ntfscat',target,'/fixture.bin'])==data.read_bytes()
        assert run([args.tools/'ntfscat','-n','FixtureStream',target,'/fixture.bin'])==stream_data.read_bytes()
        with target.open('rb') as stream:boot=stream.read(512)
        assert struct.unpack_from('<H',boot,11)[0]==target_sector
        assert sha(original)==before
        corrupt=work/'truncated.gabackup';raw=archive.read_bytes();corrupt.write_bytes(raw[:-16])
        bad=work/'bad';bad.mkdir()
        try:
            with corrupt.open('rb') as stream:preparation.materialize(stream,bad)
        except ValueError:pass
        else:raise AssertionError('Incomplete archive accepted')
        negative=[]
        for kind,key in (('ea','extended_attribute_records'),('encrypted','encrypted_records'),
                         ('wof','externally_backed_wof_records')):
            unsafe=work/(kind+'.ntfs');shutil.copyfile(original,unsafe)
            run([args.metadata_fixture,unsafe,kind])
            modified_hash=sha(unsafe)
            observed=json.loads(run([args.probe,unsafe]).decode('utf-8'))
            assert observed[key]>0 and observed['conversion_allowed'] is False,observed
            try:preparation.conversion_check(unsafe,args.probe,work/(kind+'-probe.log'))
            except ValueError:pass
            else:raise AssertionError('Unsupported metadata accepted: '+kind)
            assert sha(unsafe)==modified_hash,'Read-only probe changed metadata fixture'
            negative.append(kind)
        results.append(dict(source_sector=source_sector,destination_sector=target_sector,
            materialized_ntfs_identical=True,source_unchanged=True,content_readback=True,
            named_stream_readback=True,corrupted_archive_refused=True,unsupported_metadata_refused=negative,
            metadata_check=converted['metadata_check']))
    report=dict(status='PASS',python=sys.version.split()[0],physical_device_access=False,
                windows_installation=False,windows_boot_test=False,fixtures=results)
    (root/'RESULTS.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)

if __name__=='__main__':main()
