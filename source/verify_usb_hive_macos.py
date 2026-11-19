"""Native-library verification on disposable registry files, never devices."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from offline_keyboard import Hive
from win8_nvme import WritableHive
import usb_installation


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',required=True,type=Path)
    parser.add_argument('--template',required=True,type=Path)
    args=parser.parse_args()
    assert sys.platform=='darwin' and os.geteuid()!=0
    with tempfile.TemporaryDirectory(prefix='goawaybca-hive-test-') as folder:
        root=Path(folder).resolve();original=root/'SYSTEM';prepared=root/'SYSTEM.portable'
        h=WritableHive(args.library,args.template,True)
        try:
            h.put(('Unrelated',),'Sentinel',3,b'preserve exact bytes')
            for cs in ('ControlSet001','ControlSet002'):
                h.put((cs,'Control'),'BootDriverFlags',4,struct.pack('<I',0x21))
                h.put((cs,'Services','ExistingStorage'),'Start',4,struct.pack('<I',3))
                h.put((cs,'Services','ExistingStorage'),'Group',1,'SCSI Miniport\0'.encode('utf-16le'))
            assert h.lib.hivex_commit(h.handle,os.fsencode(original),0)==0
        finally:h.close()
        before=hashlib.sha256(original.read_bytes()).hexdigest()
        receipt=usb_installation.prepare_hive(args.library,original,prepared)
        assert hashlib.sha256(original.read_bytes()).hexdigest()==before==receipt['source_sha256']
        h=Hive(args.library,prepared)
        try:
            assert h.value(('Unrelated',),'Sentinel')==(3,b'preserve exact bytes')
            for cs in ('ControlSet001','ControlSet002'):
                assert h.value((cs,'Control'),'BootDriverFlags')==(4,struct.pack('<I',0x35))
                assert h.value((cs,'Control'),'PortableOperatingSystem')==(4,struct.pack('<I',1))
                assert h.value((cs,'Services','partmgr','Parameters'),'SanPolicy')==(4,struct.pack('<I',4))
                assert h.value((cs,'Services','ExistingStorage'),'Start')==(4,struct.pack('<I',3))
                assert h.value((cs,'Control','Power'),'HibernateEnabled')==(4,bytes(4))
                assert h.value((cs,'Control','Session Manager','Power'),'HiberbootEnabled')==(4,bytes(4))
        finally:h.close()
    print(json.dumps(dict(status='PASS',python=sys.version.split()[0],native_hive_roundtrip=True,
        original_unchanged=True,unrelated_registry_preserved=True,device_access=False)))

if __name__=='__main__':main()
