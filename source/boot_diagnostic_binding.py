"""Bind one diagnostic EFI template to persistent disk/partition identities."""
import hashlib
import struct
import uuid

MAGIC=b'GoAwayBCA-EFI-DIAGNOSTIC-V1'.ljust(32,b'\0')
SIZE=512

def configuration(session_id,disk_guid,esp_guid,windows_guid,sector,pci_hash,acpi_hash,model):
    if sector not in (512,4096):raise ValueError('Unsupported logical sector size')
    result=bytearray(SIZE);result[:32]=MAGIC
    struct.pack_into('<II',result,32,1,sector)
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
