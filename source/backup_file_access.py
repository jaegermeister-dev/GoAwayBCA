"""Open a selected backup as the GUI user, not the privileged disk worker.

The isolated child drops all root identities before walking/opening any supplied
path. It sends one regular-file descriptor back. New output is exclusive: an
existing backup is never overwritten. A failed capture has no valid completion
record and cannot be restored; retain it for the user's inspection/removal.
"""
import array
from contextlib import contextmanager
import json
import os
from pathlib import PurePosixPath
import resource
import socket
import stat

from user_driver_store import user_home, open_dir, open_file
from descriptor_io import open_at


def require(ok,message):
    if not ok:raise ValueError(message)


def _child(connection,user,path,create):
    keep=connection.fileno()
    limit=resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    require(type(limit) is int and 3<limit<=1048576,'Unknown descriptor limit')
    os.closerange(3,keep);os.closerange(keep+1,limit)
    if os.geteuid()==0:
        os.setgroups([]);os.setgid(user.pw_gid);os.setuid(user.pw_uid)
    require(os.getuid()==os.geteuid()==user.pw_uid and os.getgid()==os.getegid()==user.pw_gid,
            'Could not enter the signed-in user account for backup file access.')
    os.chdir(user.pw_dir)
    parent=open_dir(str(path.parent))
    descriptor=None
    try:
        if create:
            descriptor=open_at(parent,path.name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        else:
            descriptor=open_file(parent,path.name)
        require(stat.S_ISREG(os.fstat(descriptor).st_mode),'Choose a regular backup file.')
        connection.sendmsg([b'{"ready":true}'],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[descriptor]))])
    finally:
        if descriptor is not None:os.close(descriptor)
        os.close(parent)


@contextmanager
def open_backup(uid,path,create=False):
    """Yield a binary file object opened with only the user's own permissions."""
    user=user_home(uid)
    require(type(create) is bool and type(path) is str and path.startswith('/') and
            '\\' not in path and not any(ord(c)<32 for c in path),'Invalid backup file selection.')
    selected=PurePosixPath(path)
    require(selected.name not in ('','.','..') and '..' not in selected.parts,'Invalid backup file path.')
    parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)
    pid=os.fork()
    if pid==0:
        parent.close()
        try:
            _child(child,user,selected,create)
        except BaseException as error:
            try:child.send(json.dumps(dict(error=str(error)[:1500])).encode('utf8'))
            except BaseException:pass
            os._exit(1)
        child.close();os._exit(0)
    child.close()
    descriptors=[]
    try:
        parent.settimeout(30)
        message,ancillary,flags,_=parent.recvmsg(8192,socket.CMSG_SPACE(16*array.array('i').itemsize))
        for level,kind,data in ancillary:
            if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                values=array.array('i');values.frombytes(data[:len(data)//values.itemsize*values.itemsize]);descriptors.extend(values)
        reply=json.loads(message.decode('utf8'))
        require(not flags & (socket.MSG_TRUNC|socket.MSG_CTRUNC),'Incomplete backup file reply.')
        require(type(reply) is dict,'Invalid backup file reply.')
        require(reply==dict(ready=True),reply.get('error','Could not open the selected backup file.'))
        _,status=os.waitpid(pid,0);pid=None
        require(os.WIFEXITED(status) and os.WEXITSTATUS(status)==0,'User file helper failed.')
        require(len(descriptors)==1 and stat.S_ISREG(os.fstat(descriptors[0]).st_mode),'Invalid backup file descriptor.')
        descriptor=descriptors.pop()
        os.set_inheritable(descriptor,False)
        with os.fdopen(descriptor,'r+b' if create else 'rb') as stream:
            yield stream
    finally:
        parent.close()
        for descriptor in descriptors:os.close(descriptor)
        if pid is not None:
            # Only our own short-lived IO child. It has no device privileges.
            try:os.kill(pid,15)
            except ProcessLookupError:pass
            os.waitpid(pid,0)
