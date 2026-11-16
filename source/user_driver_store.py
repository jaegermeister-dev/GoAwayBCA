"""User-privilege driver storage; never execute files selected by the user.

macOS worker forks a tiny IO child, drops ALL root identities/groups, then
streams regular files through pipes. The root worker never follows paths from
the user folder and never grants the child access to its private session.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import uuid
from descriptor_io import (open_at, mkdir_at, rename_at, stat_at, list_fd, unlink_at,
                          CrossDirectoryRenameUnavailable)

MAX_FILES=100000
MAX_BYTES=24*1024**3
MAX_METADATA=4*1024**2
BUFFER=1024**2


def require(ok,text):
    if not ok:raise ValueError(text)


def relative(value):
    require(type(value) is str and value and len(value.encode('utf-8'))<=4096 and
        not value.startswith('/') and '\\' not in value and ':' not in value and
        all(p not in ('','.','..') and not any(ord(c)<32 for c in p) for p in value.split('/')),
        'Unsafe driver path')
    return value


def packet(stream,value):
    data=json.dumps(value,separators=(',',':')).encode('utf-8')
    require(len(data)<=MAX_METADATA,'Oversized driver metadata')
    stream.write(struct.pack('!I',len(data))+data);stream.flush()


def exact(stream,count):
    output=bytearray()
    while len(output)<count:
        block=stream.read(count-len(output));require(block,'Driver transfer ended early');output.extend(block)
    return bytes(output)


def receive(stream):
    count=struct.unpack('!I',exact(stream,4))[0]
    require(0<count<=MAX_METADATA,'Invalid driver message length')
    value=json.loads(exact(stream,count))
    if type(value) is dict and value.get('error'):raise ValueError(value['error'])
    return value


def open_dir(path,create=False,base=None):
    """Walk with directory fds and O_NOFOLLOW, including existing parents."""
    path=str(path)
    if base is None:
        require(path.startswith('/') and '..' not in path.split('/'),'Absolute safe directory required')
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY);parts=path.split('/')[1:]
    else:
        relative(path);fd=os.dup(base);parts=path.split('/')
    try:
        for part in parts:
            if part=='':continue
            if create:
                try:mkdir_at(fd,part,0o700)
                except FileExistsError:pass
            following=open_at(fd,part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            os.close(fd);fd=following
        return fd
    except BaseException:
        os.close(fd);raise


def open_file(root,rel):
    parts=relative(rel).split('/');parent=os.dup(root)
    try:
        if len(parts)>1:
            os.close(parent);parent=open_dir('/'.join(parts[:-1]),base=root)
        fd=open_at(parent,parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):os.close(fd);raise ValueError('Only regular driver files are accepted')
        return fd
    finally:os.close(parent)


def inventory(root):
    files=[];seen=set();total=0
    def walk(fd,prefix):
        nonlocal total
        for name in sorted(list_fd(fd)):
            rel=relative(prefix+name);key=rel.casefold()
            require(key not in seen,'Case-colliding driver path');seen.add(key)
            require(len(seen)<=MAX_FILES*2,'Too many driver paths')
            info=stat_at(fd,name)
            if stat.S_ISDIR(info.st_mode):
                child=open_dir(name,base=fd)
                try:walk(child,rel+'/')
                finally:os.close(child)
            else:
                require(stat.S_ISREG(info.st_mode),'Linked/special driver file refused: '+rel)
                require(0<=info.st_size<=MAX_BYTES,'Invalid driver size')
                total+=info.st_size;files.append(dict(path=rel,bytes=info.st_size))
                require(len(files)<=MAX_FILES and total<=MAX_BYTES,'Driver folder exceeds limits')
    walk(root,'');require(files,'Driver folder is empty');return files


def checked_rows(rows):
    require(type(rows) is list and 0<len(rows)<=MAX_FILES,'Invalid driver file list')
    seen=set();total=0
    for row in rows:
        require(type(row) is dict,'Invalid driver file record')
        key=relative(row.get('path')).casefold()
        require(key not in seen,'Duplicate driver file');seen.add(key)
        size=row.get('bytes');require(type(size) is int and 0<=size<=MAX_BYTES,'Invalid driver file size')
        total+=size;require(total<=MAX_BYTES,'Oversized driver transfer')
    return total


def stream_tree(fd,output,only=None,note=None):
    rows=inventory(fd) if only is None else only
    total=checked_rows(rows);packet(output,rows)
    from operation_progress import Meter
    meter=Meter(note,'Saving drivers for future installations',total) if note else None
    done=0
    for row in rows:
        handle=open_file(fd,row['path'])
        try:
            before=os.fstat(handle);require(before.st_size==row['bytes'],'Driver changed before copy')
            left=row['bytes'];digest=hashlib.sha256()
            while left:
                data=os.read(handle,min(left,BUFFER));require(data,'Truncated driver source')
                output.write(data);digest.update(data);left-=len(data)
                done+=len(data)
                if meter and done<total:meter.update(done,row['path'])
            after=os.fstat(handle)
            require((before.st_size,before.st_mtime_ns,before.st_ino,before.st_dev)==
                    (after.st_size,after.st_mtime_ns,after.st_ino,after.st_dev), 'Driver changed while copying')
            packet(output,dict(sha256=digest.hexdigest()))
        finally:os.close(handle)
    # 100% is deliberately withheld until the receiving child confirms its
    # finished copy/receipt and successful atomic publication.
    return meter


def receive_tree(stream,fd,note=lambda _:None):
    from operation_progress import Meter
    rows=receive(stream);total=checked_rows(rows)
    meter=Meter(note,'Copying saved/local driver files',total);done=0;hashes={}
    for row in rows:
        parts=row['path'].split('/');parent=os.dup(fd)
        try:
            if len(parts)>1:
                os.close(parent);parent=open_dir('/'.join(parts[:-1]),True,fd)
            handle=open_at(parent,parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            digest=hashlib.sha256();left=row['bytes']
            with os.fdopen(handle,'wb') as out:
                while left:
                    data=exact(stream,min(left,BUFFER));out.write(data);digest.update(data)
                    done+=len(data);left-=len(data)
                    if done<total:meter.update(done,row['path'])
            expected=receive(stream)
            require(expected==dict(sha256=digest.hexdigest()),'Driver transfer hash mismatch')
            hashes[row['path']]=digest.hexdigest()
        finally:os.close(parent)
    meter.update(done,force=True);return hashes


def user_home(uid):
    import pwd
    require(type(uid) is int and uid>0,'Normal GUI user identity required for saved drivers')
    user=pwd.getpwuid(uid)
    require(user.pw_dir.startswith('/Users/') and user.pw_dir!='/Users/Shared', 'Unexpected GUI user home')
    return user


def remove_cache_tree(parent,name,budget):
    """Only called after explicit cache-package deletion consent, as GUI user.

    Descriptor-relative and no symlink traversal; never pass a workspace/home
    root. This utility's caller validates the exact drivers-UUID child first.
    """
    budget[0]+=1;require(budget[0]<=MAX_FILES*2+4,'Too many cache entries to remove safely')
    info=stat_at(parent,name)
    if stat.S_ISDIR(info.st_mode):
        child=open_dir(name,base=parent)
        try:
            require((os.fstat(child).st_dev,os.fstat(child).st_ino)==(info.st_dev,info.st_ino),
                    'Cache directory changed before deletion')
            for leaf in list_fd(child):remove_cache_tree(child,leaf,budget)
        finally:os.close(child)
        current=stat_at(parent,name)
        require((current.st_dev,current.st_ino)==(info.st_dev,info.st_ino),'Cache directory replaced')
        unlink_at(parent,name,True)
    else:
        require(stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode),'Special cache object refused')
        unlink_at(parent,name)


def copy_retire_cache(parent,name,destination):
    """Legacy move: pinned-fd copy, readback, publish, then reviewed deletion.

    Called only by the already unprivileged IO child after explicit move consent.
    No descriptor is converted back into an absolute path for rename(). A failed
    copy leaves the original intact and the destination visibly incomplete.
    """
    from saved_file_io import snapshot, remove
    before=snapshot(parent,name)
    partial=name+'.incomplete'
    mkdir_at(destination,partial,0o700)
    source=open_dir(name,base=parent);target=open_dir(partial,base=destination)
    try:
        rows=inventory(source);checked_rows(rows)
        for row in rows:
            pieces=row['path'].split('/');folder=os.dup(target)
            try:
                if len(pieces)>1:
                    os.close(folder);folder=open_dir('/'.join(pieces[:-1]),True,target)
                src=open_file(source,row['path'])
                dst=open_at(folder,pieces[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                h=hashlib.sha256()
                with os.fdopen(src,'rb') as inp,os.fdopen(dst,'wb') as out:
                    first=os.fstat(inp.fileno());require(first.st_size==row['bytes'],'Driver changed before move')
                    left=row['bytes']
                    while left:
                        data=inp.read(min(left,BUFFER));require(data,'Truncated driver during move')
                        out.write(data);h.update(data);left-=len(data)
                    out.flush();os.fsync(out.fileno());last=os.fstat(inp.fileno())
                    require((first.st_dev,first.st_ino,first.st_size,first.st_mtime_ns)==
                            (last.st_dev,last.st_ino,last.st_size,last.st_mtime_ns),'Driver changed during move')
                readback=open_file(target,row['path']);verify=hashlib.sha256()
                with os.fdopen(readback,'rb') as inp:
                    for data in iter(lambda:inp.read(BUFFER),b''):verify.update(data)
                require(verify.digest()==h.digest(),'Moved driver verification failed')
            finally:os.close(folder)
        require(inventory(target)==rows,'Copied driver inventory differs')
        require(snapshot(parent,name)==before,'Saved drivers changed during move; original retained')
    finally:os.close(source);os.close(target)
    rename_at(destination,partial,destination,name)
    remove(parent,name,before)


def _child(action,request,reader,writer,home):
    cache=home+'/Library/Application Support/BackToSeven/Drivers'
    if request.get('legacy_cache') is True:
        require(action in ('list','read-cache','move-cache','delete-cache'), 'Legacy cache is read/retire only')
        cache=home+'/Library/Application Support/WinDeploy/Drivers'
    if action=='report-export':cache=home+'/Desktop'
    if action=='export' or (action in ('move-cache','delete-cache') and request.get('output') is not None):
        # Output only. There is no UI/API route to reuse this custom location.
        cache=request['output']
        require(type(cache) is str and cache.startswith('/') and cache!=home and cache!='/',
                'Choose a destination folder, not a filesystem or home root')
    if action=='read-local':
        fd=open_dir(request['path'])
        try:stream_tree(fd,writer)
        finally:os.close(fd)
        return
    try:root=open_dir(cache,create=action=='save')
    except FileNotFoundError:
        require(action in ('list','cleanup-list'),'Saved driver folder is missing')
        packet(writer,dict(items=[],warnings=[]) if action=='cleanup-list' else []);return
    try:
        if action=='cleanup-list':
            from saved_file_io import snapshot
            result=[];warnings=[]
            for name in sorted(list_fd(root)):
                if not re.fullmatch(r'drivers-[0-9a-f]{32}(\.incomplete)?',name):continue
                try:
                    state=snapshot(root,name)
                    label='Incomplete or unrecognised download'
                    folder=open_dir(name,base=root)
                    try:
                        try:
                            with os.fdopen(open_file(folder,'receipt.json'),'rb') as stream:meta=json.loads(stream.read(MAX_METADATA+1))
                            if type(meta) is dict:label=' / '.join(str(meta.get(k,'Unknown')) for k in ('model','family','label'))
                        except (OSError,ValueError):pass
                    finally:os.close(folder)
                    result.append(dict(id=name,state=state,label=label[:300]))
                except (OSError,ValueError) as error:warnings.append(name+': '+str(error))
                require(len(result)<=128,'Too many saved driver downloads')
            packet(writer,dict(items=result,warnings=warnings))
        elif action=='cleanup-delete':
            from saved_file_io import remove
            name=request['id']
            require(re.fullmatch(r'drivers-[0-9a-f]{32}(\.incomplete)?',name),'Invalid saved-driver cleanup ID')
            remove(root,name,request['state']);packet(writer,dict(id=name,action='deleted'))
        elif action=='list':
            result=[]
            for name in sorted(list_fd(root)):
                if not re.fullmatch('drivers-[0-9a-f]{32}',name):continue
                try:
                    folder=open_dir(name,base=root)
                    try:
                        f=open_file(folder,'receipt.json')
                        with os.fdopen(f,'rb') as inp:meta=json.loads(inp.read(MAX_METADATA+1))
                        require(type(meta) is dict and meta.get('schema')==1,'Invalid saved-driver receipt')
                        result.append(dict(id=name,receipt=meta))
                    finally:os.close(folder)
                except (OSError,ValueError):continue
                require(len(result)<=128,'Too many saved-driver entries; inspect the saved-driver folder')
            packet(writer,result)
        elif action=='read-cache':
            name=request['id'];require(re.fullmatch('drivers-[0-9a-f]{32}',name),'Invalid saved-driver ID')
            folder=open_dir(name+'/Files',base=root)
            try:stream_tree(folder,writer)
            finally:os.close(folder)
        elif action in ('move-cache','delete-cache'):
            name=request['id'];require(re.fullmatch('drivers-[0-9a-f]{32}',name),'Invalid saved-driver ID')
            folder=open_dir(name,base=root)
            try:
                f=open_file(folder,'receipt.json')
                with os.fdopen(f,'rb') as inp:meta=json.loads(inp.read(MAX_METADATA+1))
                require(meta==request['receipt'],'Selected cache receipt changed; choose it again')
            finally:os.close(folder)
            if action=='move-cache':
                desktop=open_dir(home+'/Desktop')
                try:
                    new='BackToSeven-Drivers-To-Inspect-'+uuid.uuid4().hex
                    # Reserve a fresh parent so rename cannot overwrite an old
                    # Desktop item, even if a coincidental name already exists.
                    mkdir_at(desktop,new,0o700);reserved=open_dir(new,base=desktop)
                    try:
                        try:rename_at(root,name,reserved,name)
                        except CrossDirectoryRenameUnavailable:copy_retire_cache(root,name,reserved)
                    finally:os.close(reserved)
                    packet(writer,dict(path=home+'/Desktop/'+new+'/'+name,action='moved'))
                finally:os.close(desktop)
            else:
                remove_cache_tree(root,name,[0]);packet(writer,dict(action='deleted',id=name))
        elif action in ('save','export','report-export'):
            # A unique directory is not visible to discovery until its final
            # atomic rename. Failed copies remain clearly marked incomplete.
            name=('BackToSeven-Hardware-Report-' if action=='report-export' else 'drivers-')+uuid.uuid4().hex;partial=name+'.incomplete'
            mkdir_at(root,partial,0o700);folder=open_dir(partial,base=root)
            try:
                files=open_dir('Files',True,folder)
                try:
                    packet(writer,dict(ready=True))
                    hashes=receive_tree(reader,files)
                finally:os.close(files)
                meta=dict(request['receipt'],schema=1,files=hashes)
                with os.fdopen(open_at(folder,'receipt.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'wb') as out:
                    out.write(json.dumps(meta,indent=2).encode('utf-8'));out.flush();os.fsync(out.fileno())
            finally:os.close(folder)
            rename_at(root,partial,root,name)
            packet(writer,dict(id=name,path=cache+'/'+name,receipt=meta))
        else:raise ValueError('Unknown user driver-store operation')
    finally:os.close(root)


class Store:
    def __init__(self,uid):self.user=user_home(uid)

    def _call(self,action,request,send_root=None,destination=None,note=lambda _:None):
        require(action in ('list','read-local','read-cache','save','export','report-export','move-cache','delete-cache','cleanup-list','cleanup-delete'),'Unknown storage action')
        send_r,send_w=os.pipe();recv_r,recv_w=os.pipe()
        pid=os.fork()
        if pid==0:
            os.close(send_w);os.close(recv_r)
            with os.fdopen(send_r,'rb') as reader,os.fdopen(recv_w,'wb') as writer:
                try:
                    # Do not carry a root-open log, directory or authenticated
                    # GUI socket into the child, even after dropping identity.
                    import resource
                    limit=resource.getrlimit(resource.RLIMIT_NOFILE)[0]
                    require(type(limit) is int and 0<limit<=1048576,'Unexpected descriptor limit')
                    start=3
                    for kept in sorted((send_r,recv_w)):
                        os.closerange(start,kept);start=kept+1
                    os.closerange(start,limit)
                    if os.geteuid()==0:
                        os.setgroups([]);os.setgid(self.user.pw_gid);os.setuid(self.user.pw_uid)
                    require(os.getuid()==self.user.pw_uid and os.geteuid()==self.user.pw_uid and
                            os.getgid()==self.user.pw_gid and os.getegid()==self.user.pw_gid,
                            'Could not enter GUI user privileges')
                    os.chdir(self.user.pw_dir)
                    _child(action,request,reader,writer,self.user.pw_dir)
                    writer.flush()
                except BaseException as error:
                    try:packet(writer,dict(error=(type(error).__name__+': '+str(error))[:2000]))
                    except BaseException:pass
                    os._exit(1)
            os._exit(0)
        os.close(send_r);os.close(recv_w)
        meter=None
        try:
            with os.fdopen(send_w,'wb') as writer,os.fdopen(recv_r,'rb') as reader:
                if action in ('save','export','report-export'):
                    require(receive(reader)==dict(ready=True),'User driver store is not ready')
                    root=open_dir(str(send_root))
                    try:meter=stream_tree(root,writer,note=note)
                    finally:os.close(root)
                    writer.close()
                    result=receive(reader)
                elif action in ('read-local','read-cache'):
                    destination=Path(destination)
                    require(destination.is_absolute() and destination.resolve()==destination and not destination.exists(),
                            'Fresh private driver staging directory required')
                    destination.mkdir(mode=0o700);fd=open_dir(str(destination))
                    try:result=receive_tree(reader,fd,note)
                    finally:os.close(fd)
                else:result=receive(reader)
            _,status=os.waitpid(pid,0);pid=None
            require(os.WIFEXITED(status) and os.WEXITSTATUS(status)==0,'User driver-store operation failed')
            if meter:meter.update(meter.total,force=True)
            return result
        finally:
            if pid is not None:
                # Only our own IO child, and only after its pipes are closed.
                os.waitpid(pid,0)

    def candidates(self,model,family):
        rows=self._call('list',{})
        rows += [dict(r,legacy_cache=True) for r in self._call('list',dict(legacy_cache=True))]
        return [r for r in rows if r['receipt'].get('model')==model and r['receipt'].get('family')==family]

    def local(self,path,destination,note):
        return self._call('read-local',dict(path=str(path)),destination=destination,note=note)

    def restore(self,entry,destination,note):
        return self._call('read-cache',dict(id=entry['id'],legacy_cache=entry.get('legacy_cache',False)),destination=destination,note=note)

    def save(self,source,receipt,note=lambda _:None):
        return self._call('save',dict(receipt=receipt),send_root=source,note=note)

    def export(self,source,receipt,output,note=lambda _:None):
        return self._call('export',dict(receipt=receipt,output=output),send_root=source,note=note)

    def retire(self,entry,action,output=None):
        require(action in ('move-cache','delete-cache'),'Invalid cache disposition')
        if output is not None:
            require(entry['receipt'].get('custom_output_only') is True and
                entry.get('path')==str(Path(output)/entry['id']),'Custom output identity changed')
        return self._call(action,dict(id=entry['id'],receipt=entry['receipt'],output=output,
                                     legacy_cache=entry.get('legacy_cache',False)))

    def report(self,source,note=lambda _:None):
        return self._call('report-export',dict(receipt=dict(type='BackToSeven hardware report',automatic_upload=False)),
                          send_root=source,note=note)
