"""Small, read-only Windows identity hints; never authority to erase or clean EFI.

Inspect one loader, the SYSTEM header and our small paired metadata. Do not
hash installed boot assets, copy registry hives or walk EFI trees for the list.
"""
import struct
from installation_records import validate_record,make_marker
from boot_catalog import identity


def validate_efi_application(data):
    """Check an x64 EFI application's structure, never its release/version.

    Windows boot-manager FileVersion is not the installed Windows version.
    Resource strings, signatures and version resources are not required here;
    this tool also accepts user-supplied installation media. Target identity
    is checked independently against the partition and its Windows BCD.
    """
    try:
        if len(data) < 64 or data[:2] != b'MZ':
            raise ValueError('not a PE executable')
        pe = struct.unpack_from('<I', data, 60)[0]
        if pe < 64 or pe + 24 > len(data) or data[pe:pe+4] != b'PE\0\0':
            raise ValueError('invalid PE header')
        machine, count = struct.unpack_from('<HH', data, pe+4)
        optional = struct.unpack_from('<H', data, pe+20)[0]
        base = pe+24
        if machine != 0x8664:
            raise ValueError('not an x64 executable')
        if optional < 70 or base+optional > len(data):
            raise ValueError('truncated optional header')
        if struct.unpack_from('<H', data, base)[0] != 0x20b:
            raise ValueError('not a PE32+ executable')
        if struct.unpack_from('<H', data, base+68)[0] != 10:
            raise ValueError('not an EFI application')
        table = base+optional
        if not 0 < count <= 96 or table+40*count > len(data):
            raise ValueError('invalid section table')
        for index in range(count):
            size, offset = struct.unpack_from('<II', data, table+40*index+16)
            if size and (offset < table+40*count or offset+size > len(data)):
                raise ValueError('truncated section data')
    except (struct.error, IndexError) as error:
        raise ValueError('Invalid Windows EFI executable: truncated header') from error
    except ValueError as error:
        raise ValueError('Invalid Windows EFI executable: '+str(error)) from error
    return dict(machine='x64',subsystem='EFI application')


def loader_version(data):
    """Read VS_FIXEDFILEINFO only inside a bounded PE resource section."""
    try:
        if len(data)<1024 or data[:2]!=b'MZ':return None
        pe=struct.unpack_from('<I',data,60)[0]
        if data[pe:pe+4]!=b'PE\0\0':return None
        sections=struct.unpack_from('<H',data,pe+6)[0]
        optional=struct.unpack_from('<H',data,pe+20)[0]
        if not 0<sections<=96:return None
        table=pe+24+optional
        key='VS_VERSION_INFO\0'.encode('utf-16le')
        versions=set()
        for n in range(sections):
            section=table+n*40
            if data[section:section+8].rstrip(b'\0')!=b'.rsrc':continue
            size,offset=struct.unpack_from('<II',data,section+16)
            if not size or offset+size>len(data):return None
            resources=data[offset:offset+size];at=0
            while True:
                at=resources.find(key,at)
                if at<0:break
                start=at-6;value=(at+len(key)+3)&~3
                if start>=0 and value+52<=len(resources):
                    length,value_length,kind=struct.unpack_from('<HHH',resources,start)
                    signature,structure,ms,ls=struct.unpack_from('<IIII',resources,value)
                    if (kind==0 and value_length==52 and length>=value+52-start and
                            start+length<=len(resources) and signature==0xFEEF04BD and structure==0x10000):
                        versions.add((ms>>16,ms&65535,ls>>16,ls&65535))
                at+=len(key)
        return next(iter(versions)) if len(versions)==1 else None
    except (ValueError,struct.error,IndexError):return None


def version_label(version):
    if version is None:return 'Windows (version not identified)'
    major,minor,build,_=version
    family={(6,1):'Windows 7',(6,2):'Windows 8',(6,3):'Windows 8.1'}.get((major,minor))
    if (major,minor)==(10,0):
        family='Windows 11' if 22000<=build<30000 else 'Windows 10' if 10240<=build<=19045 else None
    return (family or 'Windows')+' (loader '+'.'.join(str(v) for v in version)+')'


def recorded_origin(observed,marker,record):
    """Paired records identify creation, not current exclusive EFI ownership."""
    try:
        validate_record(record)
        if (marker!=make_marker(record) or identity(record)!=identity(observed) or
            record['partition_bytes']!=observed['partition_bytes'] or
            record['partition_offset']!=observed['partition_offset']):raise ValueError('Record mismatch')
    except (ValueError,TypeError,KeyError):
        return dict(status='origin unverified',managed=False,
            message='No matching WinDeploy creation records. This may be an older WinDeploy or another installation.')
    return dict(status='created by WinDeploy',managed=False,
        message='Matching WinDeploy creation records. Boot-file cleanup is verified only after choosing an action.')


def display_name(catalog,disk_guid,partition_guid,fallback):
    try:
        entries=catalog['entries']
        if type(entries) is not list:return fallback
        names=[e['title'] for e in entries if e.get('disk_guid')==disk_guid and
            e.get('partition_guid')==partition_guid]
        if len(names)!=1 or type(names[0]) is not str or not 0<len(names[0])<=120:return fallback
        if any(ord(c)<32 or ord(c)==127 for c in names[0]):return fallback
        return names[0]
    except (KeyError,TypeError,AttributeError):return fallback
