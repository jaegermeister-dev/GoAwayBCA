"""Optional Windows 8-only NVMe backport, staged before any partition write.

The selected image retains its own kernel, Storport, Classpnp and WDF. This
experimental route is not used for Windows 7 or Windows 8.1 and newer.
"""
import ctypes as c
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import uuid
from offline_keyboard import Hive,Value,require
from pe_storage_audit import PE

DRIVER_SHA='1c60fd5f69c10d81a9f400ff5fc4e04496fdc873de496281a7384d964309098d'
SYSTEM='/Windows/System32/config/SYSTEM'
DRIVER='/Windows/System32/drivers/stornvme.sys'
INF='goawaybca-nvme.inf'
PACKAGE=INF+'_amd64_'+DRIVER_SHA[:16]
CLASS='{4d36e97b-e325-11ce-bfc1-08002be10318}'

def sha(data):return hashlib.sha256(data).hexdigest()

def detected_nvme(tree,device):
    """Match the chosen physical disk's actual IOService ancestry, not its model."""
    require(re.fullmatch(r'disk[0-9]+',device),'Invalid physical disk identifier')
    found=[]
    def walk(node,nvme=False):
        if not isinstance(node,dict):return
        classes=[str(node.get(key,'')) for key in ('IOObjectClass','IOClass')]
        protocol=node.get('Protocol Characteristics',{})
        if not isinstance(protocol,dict):protocol={}
        nvme=nvme or any('nvme' in name.lower() for name in classes) or protocol.get('Physical Interconnect')=='NVMe'
        if node.get('BSD Name')==device and node.get('Whole') is True:found.append(nvme)
        for child in node.get('IORegistryEntryChildren',[]):walk(child,nvme)
    for root in tree:walk(root)
    require(len(found)==1,'Cannot uniquely identify the selected drive in IORegistry')
    return found[0]

def configure(owner,target,edition):
    if edition['family']!='Windows 8':return False
    from install_windows7 import plist_command
    tree=plist_command('/usr/sbin/ioreg','-a','-l','-p','IOService')
    if not detected_nvme(tree,target['disk']['DeviceIdentifier']):return False
    value=owner.interaction.request('nvme_choice',
        'The selected destination is on an NVMe drive. Original Windows 8 does not include an NVMe driver. '
        'We recommend trying the optional NVMe driver below, unless your ISO already includes a suitable driver. '
        'This driver works in our Windows 7 preparation, but its Windows 8 use is experimental and untested; '
        'installation or boot may fail. Windows 8.1 or newer is the alternative with native NVMe support. '
        'This choice is separate from EFI patches and 512/4096-byte sector handling. '
        'The app checks its imports against your image and preserves the image\'s existing storage-system files.',
        default_enabled=True)
    require(type(value) is bool,'Invalid optional NVMe response')
    return value

def changes(controlsets):
    def sz(s):return (1,(s+'\0').encode('utf-16le'))
    def dword(n):return (4,struct.pack('<I',n))
    result={}
    for cs in controlsets:
        service=(cs,'Services','stornvme')
        result[service]={'Type':dword(1),'Start':dword(0),'ErrorControl':dword(3),
            'Group':sz('SCSI Miniport'),'DisplayName':sz('Microsoft Standard NVM Express Driver'),
            'ImagePath':(2,('system32\\drivers\\stornvme.sys\0').encode('utf-16le'))}
        result[service+('Parameters',)]={'BusType':dword(17),'IoTimeoutValue':dword(10)}
        result[service+('Parameters','PnpInterface')]={'5':dword(1)}
    db=('DriverDatabase',)
    result[db+('DriverInfFiles',INF)]={'':(7,(PACKAGE+'\0\0').encode('utf-16le')),'Active':sz(PACKAGE)}
    result[db+('DeviceIds','PCI','CC_010802')]={INF:(3,b'\x01\xff\x00\x00')}
    package=db+('DriverPackages',PACKAGE)
    result[package]={'Version':(3,b'\x00\xff\x09\x00'+bytes(4)+uuid.UUID(CLASS).bytes_le+bytes(24))}
    config=package+('Configurations','Stornvme_Inst')
    result[config]={'ConfigFlags':dword(0),'Service':sz('stornvme')}
    result[config+('Device','Interrupt Management','MessageSignaledInterruptProperties')]={'MSISupported':dword(1),'MessageNumberLimit':dword(65)}
    result[package+('Descriptors','PCI','CC_010802')]={'Configuration':sz('Stornvme_Inst')}
    return result

class WritableHive(Hive):
    def ensure(self,parts):
        function=self.lib.hivex_node_add_child
        function.argtypes=[c.c_void_p,c.c_size_t,c.c_char_p];function.restype=c.c_size_t
        node=self.lib.hivex_root(self.handle)
        for part in parts:
            child=self.lib.hivex_node_get_child(self.handle,node,part.encode())
            if not child:child=function(self.handle,node,part.encode())
            require(child,'Cannot create offline driver key');node=child
        return node
    def put(self,path,name,kind,data):
        buffer=c.create_string_buffer(data);value=Value(name.encode(),kind,len(data),c.cast(buffer,c.c_void_p))
        require(self.lib.hivex_node_set_value(self.handle,self.ensure(path),c.byref(value),0)==0,'Cannot write offline driver value')

def prepare_hive(library,original,output):
    original=Path(original);output=Path(output)
    require(original.is_absolute() and original.resolve()==original and original.is_file()
        and not original.is_symlink(),'Use a canonical copied SYSTEM hive')
    require(output.is_absolute() and output.resolve()==output and output.parent.is_dir()
        and original!=output,'Use a new canonical output SYSTEM hive')
    require(not Path(output).exists(),'Use a new prepared SYSTEM hive')
    source=Path(original).read_bytes();h=WritableHive(library,original,True)
    try:
        before=h.snapshot()
        require(('DriverDatabase',) in before,'Selected Windows 8 image lacks its driver database')
        controlsets=sorted(p[0] for p in before if len(p)==1 and re.fullmatch('ControlSet[0-9]{3}',p[0]))
        require(controlsets,'No Windows control set')
        require(not any((cs,'Services','stornvme') in before for cs in controlsets),'An NVMe service is already present; it must not be replaced')
        edits=changes(controlsets);expected={k:dict(v) for k,v in before.items()}
        for path,values in edits.items():
            for i in range(1,len(path)+1):expected.setdefault(path[:i],{})
            for name,(kind,data) in values.items():
                require(name not in before.get(path,{}),'Existing driver registration must not be overwritten')
                h.put(path,name,kind,data);expected[path][name]=(kind,len(data),sha(data))
        require(h.snapshot()==expected,'Unrelated SYSTEM data changed during preparation')
        require(h.lib.hivex_commit(h.handle,os.fsencode(output),0)==0,'Cannot save prepared SYSTEM hive')
    finally:h.close()
    verify=Hive(library,output)
    try:require(verify.snapshot()==expected,'Prepared NVMe registry readback differs')
    finally:verify.close()
    require(Path(original).read_bytes()==source,'Original SYSTEM hive changed')
    return dict(original_sha256=sha(source),prepared_sha256=sha(Path(output).read_bytes()))

def existing_driver(snapshot,listing):
    """Refuse partial/custom registration rather than overwrite any image driver."""
    service=any(len(p)==3 and p[1].casefold()=='services' and p[2].casefold()=='stornvme' for p in snapshot)
    paths={line.strip().replace('\\','/').casefold() for line in listing.splitlines()}
    present=DRIVER.casefold() in paths
    require(service==present,'The image has a partial NVMe driver registration. Its existing files will not be replaced. '
            'Skip the optional NVMe driver or use an original Windows image. Nothing was formatted.')
    return service

def prepare(owner,target,edition,wim,resources):
    if not configure(owner,target,edition):return None
    root=owner.session/'windows8-nvme';root.mkdir(mode=0o700)
    def extract(member):
        data=subprocess.check_output([str(owner.wim),'extract',str(wim),str(edition['index']),member,'--to-stdout'])
        require(0<len(data)<=256*1024**2,'Unexpected Windows image file size')
        return data
    original=root/'SYSTEM.original';original.write_bytes(extract(SYSTEM))
    h=Hive(Path(resources)/'lib/libhivex.0.dylib',original)
    try:
        snapshot=h.snapshot()
    finally:h.close()
    listing=subprocess.check_output([str(owner.wim),'dir',str(wim),str(edition['index']),
        '--path=/Windows/System32/drivers']).decode('utf-8-sig')
    existing=existing_driver(snapshot,listing)
    if existing:
        require(extract(DRIVER)[:2]==b'MZ','Existing NVMe registration has no driver')
        owner.note('The selected Windows 8 image already has an NVMe driver. It is retained unchanged.')
        return None
    driver=Path(resources)/'private-nvme/stornvme.sys'
    require(sha(driver.read_bytes())==DRIVER_SHA,'Optional NVMe driver integrity mismatch')
    imports=PE(driver).imports();dependencies={}
    require({x['dll'].lower() for x in imports}=={'storport.sys','ntoskrnl.exe'},'Unexpected NVMe driver dependencies')
    for item in imports:
        dll=item['dll'].lower();member='/Windows/System32/'+('drivers/' if dll=='storport.sys' else '')+dll
        native=root/dll;native.write_bytes(extract(member));pe=PE(native)
        require(pe.machine==0x8664,'NVMe driver requires an x64 image')
        exports=pe.exports()
        require(all(name in exports and exports[name] is None for name in item['functions']),
                'This Windows 8 image does not provide the NVMe driver\'s required exports. Nothing was formatted.')
        dependencies[member]=sha(native.read_bytes())
    output=root/'SYSTEM.prepared'
    receipt=prepare_hive(Path(resources)/'lib/libhivex.0.dylib',original,output)
    overlay=root/'overlay';folder=overlay/'Windows/System32/drivers';folder.mkdir(parents=True)
    (folder/'stornvme.sys').write_bytes(driver.read_bytes())
    inf=(Path(resources)/'private-nvme'/INF).read_bytes()
    (overlay/'Windows/INF').mkdir();(overlay/'Windows/INF'/INF).write_bytes(inf)
    repo=overlay/'Windows/System32/DriverStore/FileRepository'/PACKAGE;repo.mkdir(parents=True)
    (repo/INF).write_bytes(inf);(repo/'stornvme.sys').write_bytes(driver.read_bytes())
    files={'/'+p.relative_to(overlay).as_posix():sha(p.read_bytes()) for p in overlay.rglob('*') if p.is_file()}
    image=root/'nvme-overlay.wim'
    subprocess.run([str(owner.wim),'capture',str(overlay),str(image),'Optional Windows 8 NVMe driver','--compress=none','--check'],check=True)
    receipt.update(experimental=True,windows8_boot_tested=False,native_dependencies=dependencies,
        driver_sha256=DRIVER_SHA,files=files,image_sha256=sha(image.read_bytes()),signature_enforcement_changed=False)
    (root/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return dict(system=output,wim=image,receipt=receipt)

def apply(plan,owner,target,guard):
    if plan is None:return None
    r=plan['receipt']
    require(sha(Path(plan['system']).read_bytes())==r['prepared_sha256'] and sha(Path(plan['wim']).read_bytes())==r['image_sha256'],
            'Prepared Windows 8 NVMe files changed')
    def read(member):
        guard();return subprocess.check_output([str(owner.ntfscat),target,member])
    require(sha(read(SYSTEM))==r['original_sha256'],'Installed SYSTEM differs from selected image')
    for member,digest in r['native_dependencies'].items():require(sha(read(member))==digest,'Native storage dependency changed')
    guard();subprocess.run([str(owner.wim),'apply',str(plan['wim']),'1',target,'--check'],check=True)
    for member,digest in r['files'].items():require(sha(read(member))==digest,'Optional NVMe copy verification failed')
    guard();subprocess.run([str(owner.ntfscp),target,str(plan['system']),SYSTEM],check=True)
    require(sha(read(SYSTEM))==r['prepared_sha256'],'NVMe SYSTEM verification failed')
    for member,digest in r['native_dependencies'].items():require(sha(read(member))==digest,'Native storage dependency changed')
    return dict(r,status='prepared-experimental-unbooted')
