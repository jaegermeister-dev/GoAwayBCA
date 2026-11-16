"""Separate root-owned receipts authenticate files in a user-writable cache.

A hash written alongside a cached folder is NEVER its trust authority. Records
are created only from a completed, already authenticated preparation, and bind
the GUI user, exact selected model/Windows family, origin and every file hash.
"""
import json
import os
from pathlib import Path
import re
import stat
import uuid
from user_driver_store import open_dir,open_file,require,MAX_METADATA
from descriptor_io import open_at


class Registry:
    def __init__(self,anchor=Path('/private/var/root'),owner_uid=0):
        self.anchor=Path(anchor);self.owner_uid=owner_uid

    def directory(self,create=False):
        fd=open_dir(str(self.anchor))
        try:
            for name in (None,'Library','Application Support','WinDeploy','DriverReceipts'):
                if name is not None:
                    following=open_dir(name,create,fd);os.close(fd);fd=following
                info=os.fstat(fd)
                require(info.st_uid==self.owner_uid and not stat.S_IMODE(info.st_mode)&0o022,
                        'Driver trust directory is not protected')
            return fd
        except BaseException:
            os.close(fd);raise

    def save(self,uid,model,family,category,files,receipt):
        require(type(uid) is int and uid>0,'Missing GUI user for driver receipt')
        require(category in ('official','community'),'Only authenticated preparations enter the trust registry')
        value=dict(schema=1,uid=uid,model=model,family=family,category=category,files=files,receipt=receipt)
        encoded=json.dumps(value,indent=2).encode('utf-8')
        require(len(encoded)<=MAX_METADATA,'Driver trust receipt is oversized')
        fd=self.directory(True);identity=uuid.uuid4().hex
        try:
            file=open_at(fd,identity+'.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(file,'wb') as out:out.write(encoded);out.flush();os.fsync(out.fileno())
        finally:os.close(fd)
        return identity

    def record(self,identity,uid,model,family,category):
        require(type(identity) is str and re.fullmatch('[0-9a-f]{32}',identity),'Saved-driver authentication record is missing')
        directory=self.directory()
        try:
            fd=open_file(directory,identity+'.json')
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno())
                require(info.st_uid==self.owner_uid and not stat.S_IMODE(info.st_mode)&0o077,
                        'Driver authentication receipt is not protected')
                data=stream.read(MAX_METADATA+1);require(len(data)<=MAX_METADATA,'Oversized trust receipt')
        finally:os.close(directory)
        value=json.loads(data)
        require(type(value) is dict and value.get('schema')==1 and value.get('uid')==uid and
                value.get('model')==model and value.get('family')==family and value.get('category')==category,
                'Saved driver receipt does not match this user/model/Windows selection')
        require(type(value.get('files')) is dict and value['files'],'Empty driver authentication manifest')
        return value

    def read(self,identity,uid,model,family,category,files):
        value=self.record(identity,uid,model,family,category)
        require(value['files']==files,'Saved drivers changed or are incomplete; download again or choose another source')
        return value['receipt']
