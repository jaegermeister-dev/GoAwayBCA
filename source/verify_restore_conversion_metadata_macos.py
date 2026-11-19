"""Cross-sector restore preserves synthetic Windows NTFS metadata, not an OS."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import backup_restore_filesystem as filesystem
from verify_restore_identity_macos import sha
from test_native_helpers import metadata


def groups(text):
    result={}
    for block in text.split('-'*76):
        name=re.search(r'^Full Path\s+= "(.*)"$',block,re.M)
        group=re.search(r'^Link Group ID\s+= (.*)$',block,re.M)
        if name and group:result[name[1]]=int(group[1],16)
    assert result['/payload.txt'] and result['/payload.txt']==result['/hardlink.txt']


def main():
    p=argparse.ArgumentParser()
    for name in ('tools','probe','fixture','output'):p.add_argument('--'+name,required=True,type=Path)
    args=p.parse_args()
    assert sys.platform=='darwin' and os.geteuid()!=0
    args.output.mkdir(mode=0o700)
    results=[]
    for old,new in ((512,4096),(4096,512)):
        folder=args.output/('%d-to-%d'%(old,new));folder.mkdir()
        image=folder/'source.ntfs'
        with image.open('xb') as stream:stream.truncate(64*1024**2)
        index=0
        def run(argv):
            nonlocal index
            index+=1
            r=subprocess.run([str(x) for x in argv],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
            (folder/('command-%02d.log'%index)).write_bytes(r.stdout+r.stderr)
            assert r.returncode==0,(r.stdout+r.stderr)[-2000:]
            return r.stdout
        run([args.tools/'mkntfs','-F','-Q','-s',old,'-c',4096,image])
        run([args.tools/'wimlib-imagex','apply',args.fixture,'1',image,'--check','--strict-acls'])
        source_hash=sha(image)
        prepared=dict(image=image,geometry=dict(logical_sector_size=old))
        converted=filesystem.convert_private(prepared,dict(partition_bytes=80*1024**2,
            logical_sector_size=new),args.tools,args.probe,folder)
        target=converted['image']
        captured=folder/'readback.wim'
        run([args.tools/'wimlib-imagex','capture',target,captured,'Metadata readback',
            '--compress=none','--check','--strict-acls','--norpfix'])
        before=run([args.tools/'wimlib-imagex','dir',args.fixture,'1','--detailed']).decode('utf8')
        after=run([args.tools/'wimlib-imagex','dir',captured,'1','--detailed']).decode('utf8')
        (folder/'metadata-before.txt').write_text(before)
        (folder/'metadata-after.txt').write_text(after)
        a,b=metadata(before),metadata(after)
        assert len(a)==6 and all(a[name]==b.get(name) for name in a),(a,b)
        groups(before);groups(after)
        assert run([args.tools/'ntfscat','-n','teststream',target,'/payload.txt'])==b'Alternate data stream must survive.\r\n'
        assert sha(image)==source_hash
        results.append(dict(source_sector=old,destination_sector=new,source_unchanged=True,
            hardlink_relationship=True,owner_group_dacl=True,attributes=True,timestamps=True,
            named_stream=True,unicode_names=True,short_names=True,filesystem_capacity_changed=True))
    report=dict(status='PASS',python=sys.version.split()[0],physical_devices_accessed=False,
        windows_installed=False,fixtures=results,
        limitations=['Synthetic filesystem fixtures, not a Windows boot test.',
                     'The original fixture capture lacked audit SACL privileges; SACL preservation is not established.'])
    (args.output/'RESULTS.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
