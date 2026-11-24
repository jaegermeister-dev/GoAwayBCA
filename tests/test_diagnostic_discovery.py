"""Diagnostic regression cases use synthetic metadata/files, never real disks."""
import argparse
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'source'))
import boot_diagnostics as bd
import manage_installations as manager
import ntfs_read as nt


def partition(fs='exfat',mount=''):
    return dict(DeviceIdentifier='disk9s2',ParentWholeDisk='disk9',DiskUUID='partition-guid',
        VolumeUUID='volume-guid',PartitionMapPartitionOffset=1048576,VolumeName='Windows',
        FilesystemType=fs,IOKitSize=64*1024**3,DeviceBlockSize=512,MountPoint=mount,
        ReadOnlyVolume=bool(mount))


class DiagnosticDiscoveryTests(unittest.TestCase):
    def probe(self,p,signature=True):
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(nt,'info',return_value=copy.deepcopy(p)))
        self.signature=stack.enter_context(patch.object(nt,'raw_ntfs_signature',return_value=signature))
        return stack

    def test_stale_exfat_keeps_original_identity_and_reads_ntfs(self):
        p=partition();original=copy.deepcopy(p);s=self.probe(p)
        names=s.enter_context(patch.object(nt,'raw_names',return_value=['Windows','Users']))
        read=s.enter_context(patch.object(nt,'raw_read',return_value=b'MZfixture'))
        with nt.Probe(p,'/tools/ntfscat',verify_filesystem=True) as probe:
            self.assertTrue(probe.is_ntfs);self.assertTrue(probe.stale_filesystem)
            self.assertEqual(probe.names(),['Windows','Users'])
            self.assertEqual(probe.read('/Windows/System32/winload.efi',4096),b'MZfixture')
        self.assertEqual(p,original)
        names.assert_called_once_with(Path('/tools/ntfsls'),'/dev/disk9s2')
        read.assert_called_once_with('/tools/ntfscat','/dev/disk9s2','/Windows/System32/winload.efi',4096)

    def test_existing_ntfs_uses_existing_path(self):
        p=partition('ntfs');s=self.probe(p)
        names=s.enter_context(patch.object(nt,'root_names',return_value=['Windows']))
        with nt.Probe(p,'/tools/ntfscat',verify_filesystem=True) as probe:
            self.assertTrue(probe.is_ntfs);self.assertFalse(probe.stale_filesystem)
            self.assertEqual(probe.names(),['Windows'])
        self.signature.assert_not_called();names.assert_called_once()

    def test_real_exfat_is_not_mistaken_for_windows(self):
        p=partition();self.probe(p,False)
        with nt.Probe(p,'/tools/ntfscat',verify_filesystem=True) as probe:
            self.assertFalse(probe.is_ntfs)
            with self.assertRaises(nt.Refusal):probe.names()

    def test_mounted_exfat_is_never_raw_probed(self):
        p=partition(mount='/Volumes/Data');s=self.probe(p)
        node=argparse.Namespace(st_mode=stat.S_IFBLK,st_rdev=123)
        s.enter_context(patch.object(nt.os,'stat',return_value=node))
        with nt.Probe(p,'/tools/ntfscat',verify_filesystem=True) as probe:
            self.assertFalse(probe.is_ntfs)
        self.signature.assert_not_called()

    def test_other_callers_do_not_enable_fallback(self):
        p=partition();self.probe(p)
        with self.assertRaises(nt.Refusal):
            with nt.Probe(p,'/tools/ntfscat'):pass
        self.signature.assert_not_called()

    def test_partition_replacement_and_mount_changes_still_fail(self):
        p=partition()
        for change in ({'DiskUUID':'different'},{'MountPoint':'/Volumes/Changed'},
                       {'FilesystemType':'ntfs'},{'ReadOnlyVolume':True}):
            with self.subTest(change=change),patch.object(nt,'info',side_effect=[p,dict(p,**change)]),\
                    patch.object(nt,'raw_ntfs_signature',return_value=True):
                with self.assertRaises(nt.Refusal):
                    with nt.Probe(p,'/tools/ntfscat',verify_filesystem=True):pass

    @unittest.skipUnless(os.name=='posix','POSIX partition-descriptor flags')
    def test_signature_probe_reads_only_one_partition_sector_readonly(self):
        for size in (512,4096):
            p=dict(partition(),DeviceBlockSize=size)
            header=bytearray(size);header[3:11]=b'NTFS    ';header[510:512]=b'\x55\xaa'
            node=argparse.Namespace(st_mode=stat.S_IFBLK,st_rdev=123)
            with ExitStack() as s:
                s.enter_context(patch.object(nt.os,'stat',return_value=node))
                s.enter_context(patch.object(nt.os,'fstat',return_value=node))
                opened=s.enter_context(patch.object(nt.os,'open',return_value=88))
                read=s.enter_context(patch.object(nt.os,'read',return_value=bytes(header)))
                closed=s.enter_context(patch.object(nt.os,'close'))
                self.assertTrue(nt.raw_ntfs_signature(p))
                opened.assert_called_once_with('/dev/disk9s2',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                read.assert_called_once_with(88,size);closed.assert_called_once_with(88)

    def discover_fixture(self,reported='exfat',actual=True,loader_ok=True,hive_ok=True,read_error=None):
        p=partition(reported);target=dict(partition=p,disk={'DeviceIdentifier':'disk9'},esp='disk9s1')
        obj=manager.InstallationManager(argparse.Namespace());obj.ntfscat='/tools/ntfscat';obj.note=Mock()
        obj.mount_esp=Mock(return_value=Path('/fixture'));obj.discovery_json=Mock(return_value=None)
        def read(path,limit,prefix=False):
            if read_error:raise OSError(read_error)
            if path.endswith('winload.efi'):return (b'MZ' if loader_ok else b'XX')+bytes(2048)
            if path.endswith('/SYSTEM'):return (b'regf' if hive_ok else b'xxxx')+bytes(4092)
            raise OSError('No metadata record on externally installed Windows')
        with ExitStack() as s:
            s.enter_context(patch.object(manager,'discovery_inventory',return_value=[target]))
            s.enter_context(patch.object(nt,'info',return_value=copy.deepcopy(p)))
            s.enter_context(patch.object(nt,'raw_ntfs_signature',return_value=actual))
            s.enter_context(patch.object(nt.Probe,'names',return_value=['Windows']))
            s.enter_context(patch.object(nt.Probe,'read',side_effect=read))
            s.enter_context(patch.object(manager,'gpt_for',return_value={'guid':'disk-guid','sector_size':4096}))
            s.enter_context(patch.object(manager,'match_gpt',return_value=({'guid':'partition-guid','first':256},{'guid':'esp-guid'})))
            s.enter_context(patch.object(manager,'partition_summary',return_value='Partition identity'))
            result=obj.discover(verify_filesystem=True)
        return result,obj

    def test_fresh_windows_found_without_ownership_record(self):
        report,obj=self.discover_fixture()
        self.assertTrue(report['scan_complete']);self.assertEqual(len(report['installations']),1)
        row=report['installations'][0]
        self.assertEqual(row['target']['partition']['FilesystemType'],'exfat')
        self.assertEqual(row['observed']['partition_offset'],1048576)
        self.assertIn('NTFS verified',row['summary'])
        self.assertEqual(row['ownership']['status'],'origin unverified')

    def test_existing_ntfs_found_without_ownership_record(self):
        report,_=self.discover_fixture(reported='ntfs')
        self.assertEqual(len(report['installations']),1);self.assertTrue(report['scan_complete'])

    def test_real_exfat_excluded_without_read_failure(self):
        report,obj=self.discover_fixture(actual=False)
        self.assertFalse(report['installations']);self.assertTrue(report['scan_complete'])
        obj.mount_esp.assert_not_called()

    def test_signature_is_not_enough_to_claim_windows(self):
        for args in ({'loader_ok':False},{'hive_ok':False},{'read_error':'Read denied'}):
            report,obj=self.discover_fixture(**args)
            self.assertFalse(report['installations']);self.assertFalse(report['scan_complete'])
            self.assertEqual(report['scan_failures'][0]['partition'],'disk9s2')
            obj.mount_esp.assert_not_called()

    def test_empty_metadata_candidates_saved_without_ntfs_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj=bd.BootDiagnostics(argparse.Namespace());obj.session=Path(tmp)
            obj.begin=Mock();obj.note=Mock();obj.choose=Mock();obj.discover=Mock(side_effect=AssertionError('must not inspect Windows'))
            with patch.object(bd,'discovery_inventory',return_value=[]),self.assertRaises(nt.Refusal):obj.execute()
            report=json.loads((Path(tmp)/'diagnostic-scan.json').read_text())
            self.assertFalse(report['windows_contents_inspected']);self.assertEqual(report['partition_candidates'],[])
            obj.discover.assert_not_called();obj.choose.assert_not_called()


if __name__=='__main__':unittest.main()
