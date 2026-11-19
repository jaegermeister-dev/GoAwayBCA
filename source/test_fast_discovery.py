import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch,Mock
import windows_discovery as wd
import manage_installations as m
import format_installation as fmt
import ntfs_read as nt
from runtime_profiles import validate_payload,IncompatibleHost
from test_installation_records import RecordsTests,D,P,E


def loader(version=(6,1,7601,24545)):
    data=bytearray(2048);data[:2]=b'MZ';struct.pack_into('<I',data,60,128)
    data[128:132]=b'PE\0\0';struct.pack_into('<H',data,134,1)
    struct.pack_into('<H',data,148,240);section=128+24+240
    data[section:section+8]=b'.rsrc\0\0\0';struct.pack_into('<II',data,section+16,512,1024)
    key='VS_VERSION_INFO\0'.encode('utf-16le');data[1030:1030+len(key)]=key
    value=(1030+len(key)+3)&~3
    struct.pack_into('<HHH',data,1024,value+52-1024,52,0)
    struct.pack_into('<IIII',data,value,0xFEEF04BD,0x10000,
        version[0]<<16|version[1],version[2]<<16|version[3])
    return bytes(data)


class DiscoveryTests(unittest.TestCase):
    def test_versions_and_invalid_resources(self):
        for version,name in (((6,1,7601,24545),'Windows 7'),((6,3,9600,0),'Windows 8.1'),
                             ((10,0,19041,1),'Windows 10'),((10,0,26100,1),'Windows 11')):
            self.assertEqual(wd.loader_version(loader(version)),version)
            self.assertTrue(wd.version_label(version).startswith(name+' '))
        for data in (b'MZ',loader()[:1100],loader().replace(b'.rsrc',b'.text')):
            self.assertIsNone(wd.loader_version(data))

    def test_origin_does_not_claim_boot_file_ownership(self):
        f=RecordsTests();f.setUp()
        result=wd.recorded_origin(f.observed,f.marker,f.record)
        self.assertEqual(result['status'],'created by WinDeploy');self.assertFalse(result['managed'])
        for marker,record in ((None,f.record),(f.marker,None),({},f.record)):
            self.assertEqual(wd.recorded_origin(f.observed,marker,record)['status'],'origin unverified')
        changed=dict(f.observed,partition_offset=f.observed['partition_offset']+512)
        self.assertEqual(wd.recorded_origin(changed,f.marker,f.record)['status'],'origin unverified')

    def test_names_are_bounded_unambiguous_and_not_used_for_identity(self):
        entry=dict(disk_guid=D,partition_guid=P,title='My games')
        self.assertEqual(wd.display_name(dict(entries=[entry]),D,P,'fallback'),'My games')
        for catalog in (None,dict(entries=[entry,entry]),dict(entries=[dict(entry,title='bad\nname')]),
                        dict(entries=[dict(entry,partition_guid=E)])):
            self.assertEqual(wd.display_name(catalog,D,P,'fallback'),'fallback')

    def test_discovery_only_reads_minimal_files_and_no_asset_hashes(self):
        f=RecordsTests();f.setUp();calls=[]
        part=dict(DeviceIdentifier='disk9s2',FilesystemType='ntfs',IOKitSize=f.observed['partition_bytes'],
            VolumeName='Games');target=dict(partition=part,disk=dict(DeviceIdentifier='disk9'),esp='disk9s1')
        class Probe:
            def __init__(self,p,tool):self.p=p
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def names(self):return ['Windows','Users']
            def read(self,path,limit,prefix=False):
                calls.append((path,limit,prefix))
                if path.endswith('winload.efi'):return loader()
                if path.endswith('/SYSTEM'):return b'regf'+bytes(4092)
                if path==m.MARKER:return json.dumps(f.marker).encode()
                raise AssertionError('Unexpected read '+path)
        obj=object.__new__(m.InstallationManager);obj.ntfscat='fixture';obj.esp_owned=False
        obj.note=Mock()
        obj.mount_esp=Mock(return_value=Path('/fixture'))
        obj.discovery_json=lambda p:f.record if p.name.endswith(P+'.json') else dict(entries=[dict(disk_guid=D,partition_guid=P,title='My games')])
        with ExitStack() as s:
            for name,value in dict(discovery_inventory=lambda:[dict(target,partition=dict(part,FilesystemType='exfat')),target],
                Probe=Probe,gpt_for=lambda _:dict(guid=D),match_gpt=lambda *_:(dict(guid=P,first=f.observed['partition_offset']//512),dict(guid=E)),
                partition_summary=lambda *a,**kw:'Partition info',tree_manifest=Mock(side_effect=AssertionError('Full ESP hash forbidden')),
                read_file=Mock(side_effect=AssertionError('Per-asset reads forbidden'))).items():s.enter_context(patch.object(m,name,value))
            result=obj.discover()
        self.assertTrue(result['scan_complete']);self.assertTrue(result['display_only'])
        self.assertEqual(len(result['installations']),1);row=result['installations'][0]
        self.assertEqual(row['title'],'My games — Games');self.assertIn('Windows 7',row['summary'])
        self.assertEqual(row['ownership']['status'],'created by WinDeploy')
        self.assertNotIn('efi_manifest',row);self.assertNotIn('asset_readbacks',row)
        self.assertEqual(len(calls),3);self.assertIn(('/Windows/System32/config/SYSTEM',4096,True),calls)

    def test_choose_installation_and_action_before_full_scan(self):
        f=RecordsTests();f.setUp()
        row=dict(target=dict(partition={},disk={}),observed=f.observed,marker=f.marker,record=f.record,
                 title='Windows',ownership={'status':'recorded'},summary='Summary')
        report=dict(scan_complete=True,installations=[row])
        engine=object.__new__(fmt.FormatInstallation);engine.args=argparse.Namespace(allow_rename=True)
        engine.session=Path('/fixture');events=[];engine.begin=lambda:None
        engine.discover=lambda:report;engine.note=lambda _:None
        engine.choose=lambda text,options:events.append('choose') or 0
        def scan():
            self.assertEqual(events,['choose','choose']);events.append('verify');return report
        engine.scan=scan
        with patch.object(fmt,'write_receipt'),patch.object(fmt,'assert_same_target'),patch('rename_installation.execute') as rename:
            engine.execute()
            rename.assert_called_once_with(engine,row,report)
        self.assertEqual(events,['choose','choose','verify'])

    def test_cancel_list_does_not_start_full_scan(self):
        engine=object.__new__(fmt.FormatInstallation);engine.begin=lambda:None;engine.session=Path('/fixture')
        engine.discover=lambda:dict(scan_complete=True,installations=[dict(title='Windows',ownership={'status':'unknown'},summary='x')])
        engine.choose=Mock(side_effect=ValueError('cancelled'));engine.scan=Mock(side_effect=AssertionError())
        with patch.object(fmt,'write_receipt'),patch.object(engine,'note'),self.assertRaisesRegex(ValueError,'cancelled'):engine.execute()
        engine.scan.assert_not_called()

    def test_management_integrity_still_checks_code_but_not_unused_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'code').write_bytes(b'code');(root/'private-win7-media').mkdir()
            (root/'private-win7-media/install.wim').write_bytes(b'not loaded by management')
            profile={k:'code' for k in ('python','worker','wim','mkntfs','ntfscat')}
            profile['files']={'code':hashlib.sha256(b'code').hexdigest(),'private-win7-media/install.wim':'0'*64}
            validate_payload(root,profile,'manage')
            with self.assertRaises(IncompatibleHost):validate_payload(root,profile,'install')
            (root/'code').write_bytes(b'tampered')
            with self.assertRaises(IncompatibleHost):validate_payload(root,profile,'manage')

    @unittest.skipUnless(os.name=='posix','Descriptor-relative reads are macOS/POSIX')
    def test_prefix_read_is_small_even_for_large_hive_and_legacy_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();(root/'SYSTEM').write_bytes(b'regf'+bytes(1024**2))
            device=root.stat().st_dev
            self.assertEqual(nt.mounted_read(str(root),'/SYSTEM',device,4096,prefix=True),b'regf'+bytes(4092))
            with patch.object(nt.os,'supports_dir_fd',set()):
                self.assertEqual(nt.mounted_read(str(root),'/SYSTEM',device,4096,prefix=True),b'regf'+bytes(4092))
            with self.assertRaises(nt.Refusal):nt.mounted_read(str(root),'/SYSTEM',device,4096)

if __name__=='__main__':unittest.main()
