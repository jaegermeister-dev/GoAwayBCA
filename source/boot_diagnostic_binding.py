"""Bind one diagnostic EFI template to persistent disk/partition identities."""
import hashlib
import struct
import re
import uuid

MAGIC=b'GoAwayBCA-EFI-DIAGNOSTIC-V1'.ljust(32,b'\0')
SIZE=512

def next_folder(existing_names):
    occupied={name.casefold() for name in existing_names}
    for number in range(1,1000):
        candidate='Test-%03d' % number
        if candidate.casefold() not in occupied:return candidate
    raise ValueError('All diagnostic folder names Test-001 through Test-999 are in use. Existing tools and logs were preserved.')

def configuration(session_id,disk_guid,esp_guid,windows_guid,sector,pci_hash,acpi_hash,model,folder_name=None):
    if sector not in (512,4096):raise ValueError('Unsupported logical sector size')
    result=bytearray(SIZE);result[:32]=MAGIC
    struct.pack_into('<II',result,32,1 if folder_name is None else 2,sector)
    if folder_name is not None:
        if not re.fullmatch(r'Test-[0-9]{3}',folder_name) or folder_name=='Test-000':
            raise ValueError('Invalid diagnostic folder name')
        result[264:272]=folder_name.encode('ascii')
    for offset,value in ((40,session_id),(56,disk_guid),(72,esp_guid),(88,windows_guid)):
        item=uuid.UUID(value)
        if item.int==0:raise ValueError('Missing persistent diagnostic identity')
        result[offset:offset+16]=item.bytes_le
    for offset,value in ((104,pci_hash),(136,acpi_hash)):
        data=bytes.fromhex(value)
        if len(data)!=32:raise ValueError('Invalid collector digest')
        result[offset:offset+32]=data
    encoded=model.encode('ascii')
    if not 0<len(encoded)<64 or any(c<32 or c>126 for c in encoded):raise ValueError('Invalid model identifier')
    result[200:200+len(encoded)]=encoded
    result[168:200]=hashlib.sha256(result[:168]+result[200:]).digest()
    return bytes(result)

def bind(template,config):
    if len(config)!=SIZE or config[:32]!=MAGIC:raise ValueError('Invalid diagnostic configuration')
    if hashlib.sha256(config[:168]+config[200:]).digest()!=config[168:200]:raise ValueError('Diagnostic binding checksum mismatch')
    marker=MAGIC+bytes(SIZE-32)
    if template.count(marker)!=1:raise ValueError('Diagnostic EFI template is missing its unique unbound configuration')
    if template[:2]!=b'MZ':raise ValueError('Invalid diagnostic EFI image')
    offset=struct.unpack_from('<I',template,60)[0]
    if (template[offset:offset+4]!=b'PE\0\0' or struct.unpack_from('<H',template,offset+4)[0]!=0x8664
        or struct.unpack_from('<H',template,offset+24)[0]!=0x20b
        or struct.unpack_from('<H',template,offset+24+68)[0]!=10):
        raise ValueError('The diagnostic template is not an x64 EFI application')
    result=template.replace(marker,config)
    return result
