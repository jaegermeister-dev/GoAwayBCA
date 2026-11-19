"""Exercise restore adaptation on synthetic hives and regular NTFS files only."""
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

from offline_keyboard import Hive
from win8_nvme import WritableHive
import backup_restore_identity as identity
import backup_restore_filesystem as filesystem
import backup_restore_write as writer


def sha(path):
    value=hashlib.sha256()
    with path.open('rb') as source:
        for data in iter(lambda: source.read(1024**2), b''): value.update(data)
    return value.hexdigest()


def main():
    parser=argparse.ArgumentParser()
    for name in ('tools','helpers','library','template','output'):
        parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args()
    assert sys.platform=='darwin' and os.geteuid()!=0
    args.output.mkdir(mode=0o700)
    original_system=args.output/'SYSTEM.source';software=args.output/'SOFTWARE.source'
    source_guid=str(uuid.uuid4());destination_guid=str(uuid.uuid4())
    old=(3,identity.gpt_identifier(source_guid));new=(3,identity.gpt_identifier(destination_guid))
    h=WritableHive(args.library,args.template,True)
    try:
        h.put(('Unrelated',),'Sentinel',3,b'preserve my driver data')
        h.put(('MountedDevices',),'\\DosDevices\\D:',*old)
        h.put(('MountedDevices',),'\\??\\Volume{source}',*old)
        h.put(('MountedDevices',),'\\DosDevices\\E:',*new)
        h.put(('MountedDevices',),'\\DosDevices\\C:',3,b'unrelated partition')
        assert h.lib.hivex_commit(h.handle,os.fsencode(original_system),0)==0
    finally:h.close()
    h=WritableHive(args.library,args.template,True)
    try:
        h.put(('Microsoft','Windows NT','CurrentVersion'),'SystemRoot',1,'D:\\Windows\0'.encode('utf-16le'))
        assert h.lib.hivex_commit(h.handle,os.fsencode(software),0)==0
    finally:h.close()
    original_hash=sha(original_system);software_hash=sha(software)
    prepared=args.output/'SYSTEM.prepared'
    receipt=identity.prepare_hive(args.library,original_system,software,prepared,source_guid,destination_guid)
    assert receipt['system_drive']=='D:' and receipt['removed_destination_aliases']==['\\DosDevices\\E:']
    h=Hive(args.library,prepared)
    try:
        assert h.value(('MountedDevices',),'\\DosDevices\\D:')==new
        assert h.value(('MountedDevices',),'\\??\\Volume{source}')==new
        assert h.value(('MountedDevices',),'\\DosDevices\\C:')==(3,b'unrelated partition')
        assert h.value(('Unrelated',),'Sentinel')==(3,b'preserve my driver data')
    finally:h.close()
    evidence=[]
    for sector in (512,4096):
        for target_mib in (48,64,80):
            folder=args.output/('%d-%d'%(sector,target_mib));folder.mkdir()
            source=folder/'source.ntfs';image=folder/'private.ntfs'
            with source.open('xb') as output:output.truncate(64*1024**2)
            index=0
            def run(argv):
                nonlocal index
                index+=1
                result=subprocess.run([str(v) for v in argv],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
                (folder/('command-%02d.log'%index)).write_bytes(result.stdout+result.stderr)
                assert result.returncode==0,(result.stdout+result.stderr)[-3000:]
                return result.stdout
            run([args.tools/'mkntfs','-F','-Q','-s',sector,'-c',4096,source])
            payload=folder/'payload.bin';payload.write_bytes(bytes(range(256))*8192)
            run([args.tools/'ntfscp',source,payload,'/payload.bin'])
            run([args.tools/'ntfscp','-N','NamedStream',source,payload,'/payload.bin'])
            run([args.tools/'ntfscp',source,original_system,'/SYSTEM'])
            source_hash=sha(source);shutil.copyfile(source,image)
            run([args.tools/'ntfscp',image,prepared,'/SYSTEM'])
            resize=filesystem.resize_private(image,target_mib*1024**2,sector,args.helpers/'ntfsresize',folder)
            volume=identity.prepare_volume(image,2*1024**2,sector)
            exported=filesystem.export_verified(image,args.helpers/'ntfsclone',folder,resize['resized'])
            restored=folder/'readback.ntfs'
            with restored.open('x+b') as destination,exported['image'].open('rb') as stream:
                destination.truncate(target_mib*1024**2)
                written=writer.transfer(stream,destination,target_mib*1024**2,sector,
                    exported['sha256'],lambda:None)
            assert written['complete_readback'] and written['allocated_bytes_written']==written['allocated_bytes_verified']
            # ntfsresize deliberately requests a Windows consistency check.
            # Readback may bypass that flag only on this known resized fixture.
            cat=[args.tools/'ntfscat']+(['--force'] if resize['resized'] else [])
            assert run(cat+[restored,'/payload.bin'])==payload.read_bytes()
            assert run(cat+['-n','NamedStream',restored,'/payload.bin'])==payload.read_bytes()
            assert run(cat+[restored,'/SYSTEM'])==prepared.read_bytes()
            with restored.open('rb') as stream:
                geometry=identity.boot_geometry(stream)
            assert geometry['bytes']==target_mib*1024**2 and geometry['sector_size']==sector
            assert struct.unpack_from('<I',geometry['primary'],28)[0]==2*1024**2//sector
            assert geometry['serial']==int(volume['restored_serial'],16)
            assert sha(source)==source_hash and sha(original_system)==original_hash and sha(software)==software_hash
            evidence.append(dict(sector=sector,destination_mib=target_mib,resize=resize,
                source_unchanged=True,content_and_named_stream_verified=True,adapted_hive_readback=True,
                primary_and_alternate_identity_verified=True,clone_stream_verified=True,
                production_writer_readback=written))
            print('Fixture passed: %d-byte sectors, %d MiB target'%(sector,target_mib),flush=True)
    result=dict(status='PASS',python=sys.version.split()[0],physical_devices_accessed=False,
                windows_installed=False,source_registry_unchanged=True,non_c_drive_mapped=True,
                unrelated_registry_preserved=True,fixtures=evidence)
    (args.output/'RESULTS.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
