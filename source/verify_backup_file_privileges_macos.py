"""Root-to-GUI-user descriptor transfer, using disposable regular files only."""
import argparse
import json
import os
from pathlib import Path
import pwd
import stat
import sys

from backup_file_access import open_backup


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--uid',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    assert sys.platform=='darwin' and os.getuid()==os.geteuid()==0
    user=pwd.getpwuid(args.uid);assert user.pw_uid!=0
    root=args.output;root.mkdir(mode=0o755)
    personal=root/'user';personal.mkdir(mode=0o700);os.chown(personal,user.pw_uid,user.pw_gid)
    private=root/'root-only';private.mkdir(mode=0o700)
    secret=root/'root-secret';secret.write_bytes(b'root-only fixture');secret.chmod(0o600)
    source=personal/'source';source.write_bytes(b'ordinary-user fixture');source.chmod(0o600)
    os.chown(source,user.pw_uid,user.pw_gid)
    with open_backup(user.pw_uid,str(source)) as stream:assert stream.read()==b'ordinary-user fixture'
    output=personal/'new.gabackup'
    with open_backup(user.pw_uid,str(output),True) as stream:
        stream.write(b'owned by GUI user');stream.flush();os.fsync(stream.fileno())
    node=output.stat();assert node.st_uid==user.pw_uid and node.st_gid==user.pw_gid and stat.S_IMODE(node.st_mode)==0o600
    blocked=[]
    for path,create in ((secret,False),(private/'new',True),(output,True)):
        try:
            with open_backup(user.pw_uid,str(path),create):pass
        except ValueError:blocked.append(str(path))
        else:raise AssertionError('Privileged file access escaped GUI-user permissions: '+str(path))
    assert secret.read_bytes()==b'root-only fixture' and output.read_bytes()==b'owned by GUI user'
    assert not (private/'new').exists() and os.getuid()==os.geteuid()==0
    result=dict(status='PASS',python=sys.version.split()[0],gui_uid=user.pw_uid,
        root_permissions_dropped_in_child=True,root_only_read_denied=True,root_only_create_denied=True,
        existing_backup_not_overwritten=True,new_backup_owned_by_gui_user=True,physical_devices_accessed=False)
    (root/'RESULT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
