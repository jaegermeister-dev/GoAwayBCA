"""Synthetic EFI-shaped bytes only; stage restore records without booting an OS."""
import argparse
import copy
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import uuid

import backup_restore_boot as boot
from installation_records import can_uninstall
from bcd_semantics import identity as bcd_identity
from bcd import device_blob, BOOTMGR, LOADER
from bcd_semantics import Registry


def main():
    parser=argparse.ArgumentParser()
    for name in ('tools','resources','output'):parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--architecture',type=int,choices=(0,9),default=9)
    parser.add_argument('--family',choices=('Windows 8.1','Windows 10'),default='Windows 10')
    args=parser.parse_args()
    assert sys.platform=='darwin' and os.geteuid()!=0
    args.output.mkdir(mode=0o700)
    source=args.output/'files';source.mkdir()
    efi=source/'Windows/Boot/EFI';efi.mkdir(parents=True)
    fonts=source/'Windows/Boot/Fonts';fonts.mkdir()
    loader=bytearray(512);loader[:2]=b'MZ';struct.pack_into('<I',loader,60,128)
    loader[128:132]=b'PE\0\0';struct.pack_into('<H',loader,132,0x14c if args.architecture==0 else 0x8664)
    struct.pack_into('<H',loader,152,0x10b if args.architecture==0 else 0x20b)
    (efi/'bootmgfw.efi').write_bytes(loader)
    (efi/'en-US').mkdir();(efi/'en-US/bootmgfw.efi.mui').write_bytes(b'locale fixture')
    (fonts/'fixture.ttf').write_bytes(b'font fixture')
    (source/'Documents').mkdir();(source/'Documents/private.txt').write_bytes(b'NOT A BOOT FILE')
    (source/'Windows/System32').mkdir();(source/'Windows/System32/unrelated.txt').write_bytes(b'NOT A BOOT FILE')
    index=0
    def run(argv):
        nonlocal index
        index+=1
        r=subprocess.run([str(v) for v in argv],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
        (args.output/('command-%02d.log'%index)).write_bytes(r.stdout+r.stderr)
        assert r.returncode==0,(r.stdout+r.stderr)[-2400:]
        return r.stdout
    source_wim=args.output/'source.wim';image=args.output/'source.ntfs'
    with image.open('xb') as output:output.truncate(64*1024**2)
    run([args.tools/'wimlib-imagex','capture',source,source_wim,'Synthetic files','--compress=none'])
    run([args.tools/'mkntfs','-F','-Q','-s','4096',image])
    run([args.tools/'wimlib-imagex','apply',source_wim,'1',image,'--strict-acls'])
    image_hash=boot.sha(image)
    results=[]
    for owned in (False,True):
        folder=args.output/('owned' if owned else 'external');folder.mkdir()
        disk,partition,esp_guid=[str(uuid.uuid4()) for _ in range(3)]
        gpt=dict(guid=disk,sector_size=4096)
        win=dict(guid=partition,first=1024,last=1024+32*1024**3//4096-1)
        esp=dict(guid=esp_guid)
        history=dict(backup_id=str(uuid.uuid4()),origin=dict(kind='app' if owned else 'external-or-unverified'),
                     uninstall_eligible=owned,history=[])
        plan=dict(family=args.family,architecture=args.architecture,title='My restored Windows',
                  provenance=dict(eligible=owned,status='verified-application-origin' if owned else 'external-or-unverified'),
                  destination=dict(disk_guid=disk,partition_guid=partition),restored_history=history)
        staged=boot.prepare(image,plan,folder,args.resources,args.tools/'wimlib-imagex',gpt,win,esp)
        listing=run([args.tools/'wimlib-imagex','dir',folder/'restore-boot/restore-boot-files.wim','1']).decode()
        assert '/Windows/Boot/EFI/bootmgfw.efi' in listing and '/Windows/Boot/Fonts/fixture.ttf' in listing
        assert 'private.txt' not in listing and 'unrelated.txt' not in listing and '/Documents' not in listing
        assert staged['entry']['title']=='My restored Windows'
        bcd_identity(staged['canonical_bcd'],disk,esp_guid,disk,partition)
        colocated=(staged['root']/'EFI/Microsoft/Boot/BCD').read_bytes()
        registry=Registry(colocated)
        for obj,element in ((BOOTMGR,'11000001'),(LOADER,'11000001'),(LOADER,'21000001')):
            assert registry.value(('Objects',obj,'Elements',element),'Element')==(3,device_blob(disk,partition))
        read_marker=json.loads(run([args.tools/'wimlib-imagex','extract',staged['wim'],'1',
                                   '/WinDeploy/installation.json','--to-stdout']))
        observed=dict(disk_guid=disk,partition_guid=partition,partition_bytes=32*1024**3,
                      partition_offset=4096*1024,filesystem='ntfs',windows_verified=True)
        assert can_uninstall(observed,read_marker,staged['record']) is owned
        assert json.loads(run([args.tools/'wimlib-imagex','extract',staged['wim'],'1',
                               '/GoAwayBCA-Backup-History.json','--to-stdout']))==history
        deployment=json.loads(run([args.tools/'wimlib-imagex','extract',staged['wim'],'1',
                                   '/WinDeploy/deployment.json','--to-stdout']))
        assert deployment['operation']=='restore' and deployment['source_backup']==history['backup_id']
        assert boot.sha(image)==image_hash
        results.append(dict(original_app_origin=owned,restore_uninstall_eligible=owned,
            source_unchanged=True,only_boot_files_captured=True,destination_bcd_verified=True,
            menu_title_preserved=True,history_readback=True))
    report=dict(status='PASS',python=sys.version.split()[0],architecture=args.architecture,family=args.family,physical_devices_accessed=False,
                windows_installed=False,synthetic_efi_not_executed=True,fixtures=results)
    (args.output/'RESULTS.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
