import copy
import struct
import unittest
import argparse
import io
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock,patch
import startup_storage as s
from startup_storage import classify_mbr,inspect,enforce,BLOCK


def sector(*rows):
    data=bytearray(512);data[510:]=b'\x55\xaa'
    for i,(kind,start,size) in enumerate(rows):
        data[446+16*i+4]=kind;struct.pack_into('<II',data,446+16*i+8,start,size)
    return bytes(data)


class Storage(unittest.TestCase):
    def test_protective_mbr_is_not_hybrid(self):
        self.assertEqual(classify_mbr(sector((0xee,1,9999)),True),'gpt')
        self.assertEqual(classify_mbr(sector((7,2048,9000)),False),'mbr')
        self.assertEqual(classify_mbr(bytes(512),False),'other')
        self.assertEqual(classify_mbr(sector(),False),'empty')

    def test_hybrid_with_or_without_protective_entry_blocks(self):
        for data in (sector((0xee,1,100),(7,101,999)),sector((7,100,999)),
                     sector((0xee,1,100),(0xaf,101,999))):
            self.assertEqual(classify_mbr(data,True),'hybrid')
        self.assertEqual(classify_mbr(sector((0xee,1,100),(7,101,999)),False),'hybrid')

    def test_inconclusive_layout_is_not_passed(self):
        for data,gpt in ((b'',False),(bytes(512),True),(sector(),True),
                         (sector((0xee,1,100)),False),(sector((0xee,2,100)),True),
                         (sector((7,100,0)),False)):
            with self.assertRaises(ValueError):classify_mbr(data,gpt)

    def scan(self,mode='gpt',windows=False,error=False):
        listing={'AllDisksAndPartitions':[dict(DeviceIdentifier='disk0',Partitions=[dict(DeviceIdentifier='disk0s1')])]}
        registry={'disk0':dict(node=dict(Whole=True))}
        rows={d:dict(DeviceIdentifier=d,IOKitSize=1024**3,DeviceBlockSize=512) for d in ('disk0','disk0s1')}
        def layout(*_):
            if error:raise OSError('device unavailable')
            return mode
        return inspect('fixture-cat',listing,registry,lambda d:copy.deepcopy(rows[d]),layout,lambda *_:windows)

    def test_mbr_data_disk_allowed_but_windows_is_not(self):
        for mode,has_windows,allowed in (('gpt',True,True),('mbr',False,True),
                                        ('mbr',True,False),('hybrid',False,False)):
            result=self.scan(mode,has_windows);self.assertEqual(result['allowed'],allowed)
            if allowed:enforce(result)
            else:
                with self.assertRaisesRegex(ValueError,'PURE GPT/EFI'):enforce(result)
        self.assertFalse(self.scan(error=True)['allowed'])
        with self.assertRaisesRegex(ValueError,'could not be checked'):enforce(self.scan(error=True))

    def test_missing_physical_disk_inventory_does_not_pass(self):
        with self.assertRaises(Exception):inspect('cat',{'AllDisksAndPartitions':[]},{'disk0':dict(node=dict(Whole=True))})

    def test_original_launch_reader_accepts_512_and_4096(self):
        for block in (512,4096):
            data=bytearray(block+8);data[:512]=sector((0xee,1,9999));data[block:]=b'EFI PART'
            with patch('startup_storage.os.stat',return_value=SimpleNamespace(st_mode=stat.S_IFBLK)), \
                 patch('builtins.open',return_value=io.BytesIO(data)):
                self.assertEqual(s.inspect_layout('disk0',block),'gpt')

    def test_sip_detection_does_not_assume_partial_or_error_is_disabled(self):
        for text,code,automatic in (('System Integrity Protection status: disabled.',0,True),
            ('System Integrity Protection status: enabled.',0,False),
            ('System Integrity Protection status: unknown (Custom Configuration).',0,False),
            ('System Integrity Protection status: disabled.',1,False)):
            with patch('platform.mac_ver',return_value=('10.14.6',(),'x86_64')), \
                 patch('startup_storage.subprocess.run',return_value=SimpleNamespace(returncode=code,stdout=text.encode())):
                self.assertEqual(s.sip_status()['automatic'],automatic)
        with patch('platform.mac_ver',return_value=('10.9.5',(),'x86_64')), \
             patch('startup_storage.subprocess.run',side_effect=AssertionError('No SIP on Mavericks')):
            self.assertTrue(s.sip_status()['automatic'])

    def test_enabled_sip_manual_path_never_opens_or_scans_a_disk(self):
        with tempfile.TemporaryDirectory() as temp:
            ui=Mock();ui.request.side_effect=['manual',{'confirmed':True,'remembered':False}]
            installer=s.StartupCheck(argparse.Namespace(work_dir=temp),interaction=ui)
            with patch.object(installer,'tools'),patch('startup_storage.DeploymentLock'), \
                 patch('drive_geometry.collect',return_value={'schema':1,'drives':{}}), \
                 patch('startup_storage.sip_status',return_value={'status':'enabled','automatic':False}), \
                 patch('startup_storage.subprocess.check_output',return_value=b'MacBookPro11,4\n'), \
                 patch('mac_compatibility.load',return_value={'models':{}}), \
                 patch('startup_storage.inspect',side_effect=AssertionError('No automatic scan')):
                installer.execute()
            self.assertTrue((installer.session/'startup-layout-consent.json').is_file())
            self.assertFalse((installer.session/'startup-disk-check.json').exists())

    def test_disabled_sip_scans_and_blocks_hybrid_without_questions(self):
        with tempfile.TemporaryDirectory() as temp:
            ui=Mock();ui.request.side_effect=AssertionError('No questions on automatic route')
            installer=s.StartupCheck(argparse.Namespace(work_dir=temp),interaction=ui)
            result={'blocked':['disk0: hybrid MBR'],'errors':[],'allowed':False}
            installer.ntfscat='fixture-ntfscat'
            with patch.object(installer,'tools'),patch('startup_storage.DeploymentLock'), \
                 patch('drive_geometry.collect',return_value={'schema':1,'drives':{}}), \
                 patch('startup_storage.sip_status',return_value={'status':'disabled','automatic':True}), \
                 patch('startup_storage.inspect',return_value=result) as scan:
                with self.assertRaisesRegex(ValueError,'THIS CONFIGURATION IS NOT SUPPORTED'):
                    installer.execute()
                scan.assert_called_once()

    def test_manual_cancellation_is_not_consent(self):
        with tempfile.TemporaryDirectory() as temp:
            ui=Mock();ui.request.side_effect=['manual',{'confirmed':False}]
            installer=s.StartupCheck(argparse.Namespace(work_dir=temp),interaction=ui)
            with patch.object(installer,'tools'),patch('startup_storage.DeploymentLock'), \
                 patch('drive_geometry.collect',return_value={'schema':1,'drives':{}}), \
                 patch('startup_storage.sip_status',return_value={'status':'enabled','automatic':False}), \
                 patch('startup_storage.subprocess.check_output',return_value=b'MacBookPro11,4\n'), \
                 patch('mac_compatibility.load',return_value={'models':{}}), \
                 patch('startup_storage.inspect',side_effect=AssertionError('No scan')):
                with self.assertRaisesRegex(Exception,'cancelled'):installer.execute()


if __name__=='__main__':unittest.main()
