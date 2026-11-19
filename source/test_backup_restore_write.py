import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import backup_restore_write as w
from test_ntfsclone_image import fixture


class Tests(unittest.TestCase):
    def run_fixture(self,data,sector=512,expected=None,guard=None,target_wrapper=None):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'prepared';source.write_bytes(data)
            target=Path(directory)/'destination';target.write_bytes(b'X'*8192)
            with source.open('rb') as stream,target.open('r+b') as destination:
                output=target_wrapper(destination) if target_wrapper else destination
                return w.transfer(stream,output,8192,sector,expected or hashlib.sha256(data).hexdigest(),guard or Mock())

    def test_readback_in_both_sector_geometries(self):
        for sector in (512,4096):
            result=self.run_fixture(fixture(sector),sector)
            self.assertTrue(result['complete_readback'])
            self.assertEqual(result['allocated_bytes_written'],8192)
            self.assertEqual(result['allocated_bytes_verified'],8192)

    def test_corruption_and_wrong_geometry_refuse_before_target_write(self):
        good=fixture()
        for data,sector,digest in ((good,512,'0'*64),(good,4096,None),(good+b'junk',512,None),(good[:-1],512,None)):
            writes=[]
            class Target:
                def write(self,value):writes.append(value);raise AssertionError('unexpected write')
            with self.assertRaises(ValueError):
                self.run_fixture(data,sector,digest,target_wrapper=lambda unused:Target())
            self.assertEqual(writes,[])

    def test_destination_identity_failure_prevents_write(self):
        guard=Mock(side_effect=ValueError('destination changed'))
        with self.assertRaisesRegex(ValueError,'destination changed'):
            self.run_fixture(fixture(),guard=guard,target_wrapper=lambda unused:None)

    def test_readback_mismatch_is_not_success(self):
        class Target:
            def __init__(self,stream):self.stream=stream
            def __getattr__(self,name):return getattr(self.stream,name)
            def read(self,size):return b'X'*size
        with self.assertRaisesRegex(ValueError,'failed readback'):
            self.run_fixture(fixture(),target_wrapper=Target)

    def test_whole_disk_and_arbitrary_paths_never_opened(self):
        for path in ('/dev/disk0','/dev/rdisk0s2','/tmp/disk0s2','/dev/disk0s2/../disk0','/dev/disk0s2x'):
            with patch.object(w.os,'open') as opened,self.assertRaises(ValueError):
                w.write_partition({},path,Mock())
            opened.assert_not_called()


if __name__=='__main__':unittest.main()
