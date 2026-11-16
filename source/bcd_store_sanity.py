"""Conservative structural checks for a candidate boot-store snapshot.

Not a complete registry/BCD semantic parser and NOT permission to replace any
live file. The future firmware adapter must also verify ownership, active-state
records, expected EFI identity and all recovery snapshots. Never recovers a dirty
hive or edits a Microsoft BCD. Existing parsed store/partition metadata remains
necessary to establish the selected OS; byte matches alone are not that proof.
"""
import struct
from bcd import device_blob


def require(ok,message):
    if not ok:raise ValueError(message)


def clean_hive(data):
    require(type(data) is bytes and 8192<=len(data)<=16*1024**2 and len(data)%4096==0,
            'Unexpected boot-store size')
    require(data[:4]==b'regf','Not a registry hive')
    sequence1,sequence2=struct.unpack_from('<II',data,4)
    require(sequence1==sequence2,'Dirty BCD requires Windows registry recovery; do not switch it')
    checksum=0
    for value in struct.unpack('<127I',data[:508]):checksum^=value
    if checksum==0:checksum=1
    elif checksum==0xffffffff:checksum=0xfffffffe
    require(checksum==struct.unpack_from('<I',data,508)[0],'Boot-store header checksum failed')
    root,bins=struct.unpack_from('<II',data,0x24)
    require(bins+4096==len(data),'Unexpected hive trailing data or missing bins')
    require(0<=root<bins,'Invalid root offset')
    allocated=set();offset=4096
    while offset<len(data):
        require(data[offset:offset+4]==b'hbin','Missing hive bin')
        relative,size=struct.unpack_from('<II',data,offset+4)
        require(relative==offset-4096 and size>=4096 and size%4096==0 and offset+size<=len(data),
                'Invalid hive bin bounds')
        cell=offset+32;end=offset+size
        while cell<end:
            length=struct.unpack_from('<i',data,cell)[0]
            require(abs(length)>=8 and abs(length)%8==0 and cell+abs(length)<=end,'Invalid hive cell bounds')
            if length<0:allocated.add(cell-4096)
            cell+=abs(length)
        offset=end
    require(root in allocated and data[4096+root+4:4096+root+6]==b'nk','Invalid root key cell')
    return dict(sequence=sequence1,bytes=len(data),allocated_cells=len(allocated))


def expected_devices(data,esp_disk,esp_partition,windows_disk,windows_partition):
    result=clean_hive(data)
    esp=device_blob(esp_disk,esp_partition);windows=device_blob(windows_disk,windows_partition)
    require(esp!=windows,'System and Windows partitions must be distinct for this profile')
    require(data.count(esp)>=1 and data.count(windows)>=2,'Expected GPT device descriptors are absent')
    result.update(full_bcd_semantics_validated=False,expected_device_descriptors_present=True)
    return result
