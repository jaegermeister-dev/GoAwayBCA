"""Bounded descriptor-relative inventory/deletion, never following links or mounts."""
import hashlib
import json
import os
import stat
from descriptor_io import list_fd,stat_at,unlink_at
from user_driver_store import open_dir,require

def snapshot(parent,name):
    first=stat_at(parent,name);device=os.fstat(parent).st_dev
    rows=[];total=0
    def walk(fd,leaf,relative):
        nonlocal total
        node=stat_at(fd,leaf)
        require(node.st_dev==device,'Mounted storage cannot be cleaned here')
        require(stat.S_ISREG(node.st_mode) or stat.S_ISDIR(node.st_mode),'Linked/special saved file; inspect manually')
        rows.append([relative,node.st_mode,node.st_ino,node.st_size,node.st_mtime_ns])
        require(len(rows)<=200000,'Saved item is too large to inspect safely')
        if stat.S_ISDIR(node.st_mode):
            child=open_dir(leaf,base=fd)
            try:
                require(os.fstat(child).st_ino==node.st_ino,'Saved directory changed')
                for part in sorted(list_fd(child)):walk(child,part,relative+'/'+part)
            finally:os.close(child)
        else:total+=node.st_size
    walk(parent,name,name)
    after=stat_at(parent,name)
    require((first.st_ino,first.st_mtime_ns)==(after.st_ino,after.st_mtime_ns),'Saved item changed while reading')
    return dict(bytes=total,entries=len(rows),signature=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest())

def remove(parent,name,expected):
    require(snapshot(parent,name)==expected,'Saved files changed since review; refresh the list')
    device=os.fstat(parent).st_dev
    def walk(fd,leaf):
        node=stat_at(fd,leaf)
        require(node.st_dev==device,'Mounted storage cannot be removed')
        require(stat.S_ISREG(node.st_mode) or stat.S_ISDIR(node.st_mode),'Linked/special saved file refused')
        if stat.S_ISDIR(node.st_mode):
            child=open_dir(leaf,base=fd)
            try:
                require(os.fstat(child).st_ino==node.st_ino,'Saved directory changed')
                for part in list_fd(child):walk(child,part)
            finally:os.close(child)
            require(stat_at(fd,leaf).st_ino==node.st_ino,'Saved directory was replaced')
            unlink_at(fd,leaf,True)
        else:unlink_at(fd,leaf)
    walk(parent,name)
