"""Complete restore preparation with synthetic files/hives, never an OS install."""
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

import backup_metadata
import backup_restore_identity as identity
import backup_restore_plan
import backup_restore_write
import restore_windows
from bcd import BOOTMGR,LOADER,device_blob
from bcd_semantics import Registry
from offline_keyboard import Hive
from win8_nvme import WritableHive
from test_backup_metadata import location


def main():
    parser=argparse.ArgumentParser()
    for name in ('tools','helpers','probe','resources','output'):parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args();assert sys.platform=='darwin' and os.geteuid()!=0
    args.output.mkdir(mode=0o700)
    tools=args.output/'tools';tools.mkdir()
    for name in ('wimlib-imagex','ntfscp','ntfscat','mkntfs'):(tools/name).symlink_to(args.tools/name)
    for name in ('ntfsclone','ntfsresize'):(tools/name).symlink_to(args.helpers/name)
    (tools/'backup-metadata-probe').symlink_to(args.probe)
    restore_windows.ROOT=args.resources
    results=[];counter=0
    def run(command):
        nonlocal counter
        counter+=1
        p=subprocess.run([str(v) for v in command],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
        (args.output/('command-%03d.log'%counter)).write_bytes(p.stdout+p.stderr)
        assert p.returncode==0,(p.stdout+p.stderr)[-3000:]
        return p.stdout
    for source_sector,dest_sector,connection in ((512,512,'Internal'),(4096,4096,'USB'),(4096,512,'Internal'),(512,4096,'USB')):
        case=args.output/('%d-%d-%s'%(source_sector,dest_sector,connection));case.mkdir()
        files=case/'files';efi=files/'Windows/Boot/EFI';efi.mkdir(parents=True)
        fonts=files/'Windows/Boot/Fonts';fonts.mkdir()
        config=files/'Windows/System32/config';config.mkdir(parents=True)
        loader=bytearray(512);loader[:2]=b'MZ';struct.pack_into('<I',loader,60,128)
        loader[128:132]=b'PE\0\0';struct.pack_into('<H',loader,132,0x8664);struct.pack_into('<H',loader,152,0x20b)
        (efi/'bootmgfw.efi').write_bytes(loader);(fonts/'fixture.ttf').write_bytes(b'font fixture')
        (files/'user-file.txt').write_bytes(b'preserve personal content')
        src_guid=str(uuid.uuid4());dest_guid=str(uuid.uuid4());disk=str(uuid.uuid4());esp_guid=str(uuid.uuid4())
        h=WritableHive(args.resources/'lib/libhivex.0.dylib',args.resources/'BCD.template',True)
        try:
            h.put(('MountedDevices',),'\\DosDevices\\D:',3,identity.gpt_identifier(src_guid))
            h.put(('ControlSet001','Control'),'BootDriverFlags',4,struct.pack('<I',1))
            h.put(('Unrelated',),'Sentinel',3,b'preserve')
            assert h.lib.hivex_commit(h.handle,os.fsencode(config/'SYSTEM'),0)==0
        finally:h.close()
        h=WritableHive(args.resources/'lib/libhivex.0.dylib',args.resources/'BCD.template',True)
        try:
            h.put(('Microsoft','Windows NT','CurrentVersion'),'SystemRoot',1,'D:\\Windows\0'.encode('utf-16le'))
            assert h.lib.hivex_commit(h.handle,os.fsencode(config/'SOFTWARE'),0)==0
        finally:h.close()
        wim=case/'source.wim';source=case/'source.ntfs';private=case/'private.ntfs'
        with source.open('xb') as f:f.truncate(64*1024**2)
        run([tools/'wimlib-imagex','capture',files,wim,'Synthetic restore input','--compress=none'])
        run([tools/'mkntfs','-F','-Q','-s',source_sector,source])
        run([tools/'wimlib-imagex','apply',wim,'1',source,'--strict-acls'])
        digest=hashlib.sha256(source.read_bytes()).hexdigest();shutil.copyfile(source,private)
        source_location=dict(location(),partition_guid=src_guid,logical_sector_size=source_sector,partition_bytes=64*1024**2)
        destination=dict(location(),disk_guid=disk,partition_guid=dest_guid,logical_sector_size=dest_sector,
                         partition_bytes=24*1024**3,partition_offset=4*1024**2,connection=connection)
        windows=dict(family='Windows 10',architecture=9,version=[10,0,19045,1])
        metadata=backup_metadata.create(source_location,windows,False);metadata['menu_title']='Restored personal Windows'
        geometry=dict(bytes=source_location['partition_bytes'],logical_sector_size=source_sector,allocated_bytes=12*1024**2)
        plan=backup_restore_plan.plan(metadata,geometry,destination,None,windows)
        owner=argparse.Namespace(args=argparse.Namespace(tools_dir=str(tools)),session=case,
            ntfscat=tools/'ntfscat',ntfscp=tools/'ntfscp',wim=tools/'wimlib-imagex',note=lambda text:None,choose=lambda *a:0)
        target=dict(disk=dict(Internal=connection=='Internal',BusProtocol='USB' if connection=='USB' else 'PCI-Express'))
        first=destination['partition_offset']//dest_sector
        win=dict(guid=dest_guid,first=first,last=first+destination['partition_bytes']//dest_sector-1)
        prepared=dict(image=private,metadata=metadata,geometry=geometry)
        result=restore_windows.prepare_private(owner,prepared,plan,target,dict(guid=disk,sector_size=dest_sector),
            win,dict(guid=esp_guid),dict(windows,index=1))
        restored=case/'readback.ntfs'
        with restored.open('x+b') as out,result['stream']['image'].open('rb') as stream:
            out.truncate(destination['partition_bytes'])
            verified=backup_restore_write.transfer(stream,out,destination['partition_bytes'],dest_sector,
                result['stream']['sha256'],lambda:None)
        cat=[tools/'ntfscat']+(['--force'] if result['resize']['resized'] else [])
        assert run(cat+[restored,'/user-file.txt'])==b'preserve personal content'
        system=case/'SYSTEM.readback';system.write_bytes(run(cat+[restored,'/Windows/System32/config/SYSTEM']))
        h=Hive(args.resources/'lib/libhivex.0.dylib',system)
        try:
            assert h.value(('MountedDevices',),'\\DosDevices\\D:')==(3,identity.gpt_identifier(dest_guid))
            assert h.value(('Unrelated',),'Sentinel')==(3,b'preserve')
            flags=h.value(('ControlSet001','Control'),'BootDriverFlags')
            assert flags==(4,struct.pack('<I',0x15 if connection=='USB' else 1))
        finally:h.close()
        marker=json.loads(run(cat+[restored,'/WinDeploy/installation.json']))
        assert marker['creator']=='GoAwayBCA-restored-unverified' and marker['uninstall_eligible'] is False
        bcd=Registry(run(cat+[restored,'/EFI/Microsoft/Boot/BCD']))
        for obj,element in ((BOOTMGR,'11000001'),(LOADER,'11000001'),(LOADER,'21000001')):
            assert bcd.value(('Objects',obj,'Elements',element),'Element')==(3,device_blob(disk,dest_guid))
        history=json.loads(run(cat+[restored,'/GoAwayBCA-Backup-History.json']))
        assert history['source']==source_location and history['history'][-1]['destination']==destination
        assert result['overlay']['entry']['title']=='Restored personal Windows' and result['overlay']['record'] is None
        assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
        results.append(dict(source_sector=source_sector,destination_sector=dest_sector,connection=connection,
            complete_preparation_and_readback=True,source_unchanged=True,drive_mapping_verified=True,
            portable_flags_correct=True,foreign_ownership_preserved=True,destination_bcd_verified=True,
            exact_title_preserved=True,history_verified=True,allocated_bytes=verified['allocated_bytes_verified']))
        print('Complete restore preparation passed: '+case.name,flush=True)
    report=dict(status='PASS',python=sys.version.split()[0],physical_devices_accessed=False,
                windows_installed=False,synthetic_efi_not_executed=True,fixtures=results)
    (args.output/'RESULTS.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
