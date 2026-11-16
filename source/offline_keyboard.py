"""Offline SOFTWARE DevicePath preparation in a NEW output hive only.

No live registry, Setup/OOBE, services, signatures, UAC or device-specific binds.
The caller supplies a verified copied source and bundles the original inbox
keyboard package at WinDeploy-InboxKeyboard on the Windows volume before boot.
"""
import ctypes as c
import hashlib
import os
from pathlib import Path
import re

KEY=('Microsoft','Windows','CurrentVersion')
VALUE='DevicePath'
SUFFIX='WinDeploy-InboxKeyboard'


def require(value,message):
    if not value:raise ValueError(message)


def append_path(kind,data,system_root):
    require(kind in (1,2) and len(data)%2==0 and 2<=len(data)<=32768,'Unexpected DevicePath type/length')
    text=data.decode('utf-16le')
    require(text.endswith('\0') and '\0' not in text[:-1],'Unexpected DevicePath terminator')
    text=text[:-1];require(text and not any(ord(x)<32 for x in text),'Invalid DevicePath contents')
    require(type(system_root) is str and re.fullmatch(r'[A-Za-z]:\\[^;\r\n]+',system_root),'Invalid Windows system root')
    added=(r'%SystemDrive%' if kind==2 else system_root[:2])+'\\'+SUFFIX
    paths=text.split(';')
    if any(p.rstrip('\\').casefold()==added.casefold() for p in paths):return data
    result=text.rstrip(';')+';'+added
    require(len(result)<=16000,'DevicePath would be too large')
    return (result+'\0').encode('utf-16le')


class Value(c.Structure):
    _fields_=[('key',c.c_char_p),('kind',c.c_int),('length',c.c_size_t),('data',c.c_void_p)]


class Hive:
    def __init__(self,library,path,write=False):
        self.lib=c.CDLL(str(library),use_errno=True);self.memory=c.CDLL('/usr/lib/libSystem.B.dylib')
        self.memory.free.argtypes=[c.c_void_p];self.memory.free.restype=None
        ptr=c.c_void_p;node=c.c_size_t
        signatures={
            'open':([c.c_char_p,c.c_int],ptr),'close':([ptr],c.c_int),'root':([ptr],node),
            'node_get_child':([ptr,node,c.c_char_p],node),'node_get_value':([ptr,node,c.c_char_p],node),
            'node_children':([ptr,node],ptr),'node_values':([ptr,node],ptr),
            'node_name':([ptr,node],ptr),'value_key':([ptr,node],ptr),
            'value_value':([ptr,node,c.POINTER(c.c_int),c.POINTER(c.c_size_t)],ptr),
            'node_set_value':([ptr,node,c.POINTER(Value),c.c_int],c.c_int),
            'commit':([ptr,c.c_char_p,c.c_int],c.c_int)}
        for name,(args,result) in signatures.items():
            function=getattr(self.lib,'hivex_'+name);function.argtypes=args;function.restype=result
        self.handle=self.lib.hivex_open(os.fsencode(path),4 if write else 0)
        require(self.handle,'Cannot open offline hive: '+str(c.get_errno()))
    def close(self):
        if self.handle:
            result=self.lib.hivex_close(self.handle);self.handle=None
            require(result==0,'Offline hive close failed')
    def node(self,parts):
        current=self.lib.hivex_root(self.handle);require(current,'Missing hive root')
        for part in parts:
            current=self.lib.hivex_node_get_child(self.handle,current,part.encode('utf-8'))
            require(current,'Missing offline key component '+part)
        return current
    def read_value(self,handle):
        kind=c.c_int();length=c.c_size_t()
        data=self.lib.hivex_value_value(self.handle,handle,c.byref(kind),c.byref(length))
        require(data and length.value<=64*1024**2,'Invalid offline value')
        try:return kind.value,c.string_at(data,length.value)
        finally:self.memory.free(data)
    def value(self,parts,name):
        v=self.lib.hivex_node_get_value(self.handle,self.node(parts),name.encode('utf-8'))
        require(v,'Missing offline registry value '+name);return self.read_value(v)
    def handles(self,kind,node):
        pointer=getattr(self.lib,'hivex_node_'+kind)(self.handle,node)
        require(pointer,'Cannot enumerate offline hive')
        try:
            array=c.cast(pointer,c.POINTER(c.c_size_t));result=[]
            for i in range(1000000):
                if not array[i]:return result
                result.append(array[i])
            raise ValueError('Oversized offline hive array')
        finally:self.memory.free(pointer)
    def name(self,kind,handle):
        pointer=getattr(self.lib,'hivex_'+kind)(self.handle,handle)
        require(pointer,'Cannot read offline hive name')
        try:return c.string_at(pointer).decode('utf-8')
        finally:self.memory.free(pointer)
    def snapshot(self):
        result={};seen=set()
        def walk(node,path,depth):
            require(node not in seen and depth<512 and len(seen)<500000,'Invalid hive tree')
            seen.add(node);values={}
            for handle in self.handles('values',node):
                name=self.name('value_key',handle);require(name not in values,'Duplicate registry value')
                kind,data=self.read_value(handle)
                values[name]=(kind,len(data),hashlib.sha256(data).hexdigest())
            result[path]=values
            for child in self.handles('children',node):walk(child,path+(self.name('node_name',child),),depth+1)
        walk(self.lib.hivex_root(self.handle),(),0);return result


def prepare(library,source,destination):
    source=Path(source);destination=Path(destination)
    require(source.is_absolute() and source.resolve()==source and source.is_file() and not source.is_symlink(),'Use a canonical copied source hive')
    require(destination.is_absolute() and destination.resolve()==destination and not destination.exists()
        and destination.parent.is_dir() and source!=destination,'Use a new canonical output hive')
    require(4096<=source.stat().st_size<=256*1024**2,'Unexpected SOFTWARE hive size')
    original=source.read_bytes();require(original[:4]==b'regf','Not a registry hive')
    hive=Hive(library,source,True)
    try:
        before=hive.snapshot();kind,data=hive.value(KEY,VALUE)
        root_kind,root_data=hive.value(('Microsoft','Windows NT','CurrentVersion'),'SystemRoot')
        require(root_kind in (1,2),'Unexpected SystemRoot registry type')
        root=root_data.decode('utf-16le').rstrip('\0')
        after=append_path(kind,data,root);buffer=c.create_string_buffer(after)
        change=Value(VALUE.encode(),kind,len(after),c.cast(buffer,c.c_void_p))
        require(hive.lib.hivex_node_set_value(hive.handle,hive.node(KEY),c.byref(change),0)==0,'DevicePath offline update failed')
        require(not destination.exists(),'Output appeared during staging')
        require(hive.lib.hivex_commit(hive.handle,os.fsencode(destination),0)==0,'Cannot save new output hive')
    finally:hive.close()
    require(source.read_bytes()==original,'Source hive was unexpectedly modified')
    check=Hive(library,destination)
    try:
        require(check.value(KEY,VALUE)==(kind,after),'DevicePath output readback failed')
        expected=before.copy();expected[KEY]=dict(expected[KEY]);expected[KEY][VALUE]=(kind,len(after),hashlib.sha256(after).hexdigest())
        require(check.snapshot()==expected,'An unrelated registry key/value changed')
    finally:check.close()
    return dict(status='offline-keyboard-path-prepared',source_sha256=hashlib.sha256(original).hexdigest(),
        output_sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),kind=kind,
        before=data.decode('utf-16le').rstrip('\0'),after=after.decode('utf-16le').rstrip('\0'),
        all_other_keys_values_unchanged=True,setup_modified=False,security_policy_modified=False,
        keyboard_hardware_validated=False)
