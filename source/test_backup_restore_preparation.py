"""Do not accept a helper failure or a lossy conversion as a valid restore."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import backup_restore_preparation as preparation
from install_windows7 import Refusal
from test_backup_windows import os_loader
import ntfs_read

class Tests(unittest.TestCase):
    def check(self,changes=None,exit_code=0):
        value=dict(schema=1,read_only=True,records_inspected=20,encrypted_records=0,
            extended_attribute_records=0,externally_backed_wof_records=0,conversion_allowed=True)
        value.update(changes or {})
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(preparation.subprocess,'run',return_value=
                subprocess.CompletedProcess([],exit_code,json.dumps(value).encode())):
                return preparation.conversion_check('fixture.ntfs','fixture-helper',Path(temporary)/'probe.log')

    def test_each_unsupported_metadata_kind_blocks_even_if_helper_flag_disagrees(self):
        for field in ('encrypted_records','extended_attribute_records','externally_backed_wof_records'):
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'cannot be safely converted'):
                self.check({field:1})

    def test_failure_incomplete_scan_and_bad_counts_never_allow_conversion(self):
        for changes in ({'conversion_allowed':False},{'read_only':False},{'records_inspected':0},
                        {'encrypted_records':True},{'extended_attribute_records':-1}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.check(changes)
        with self.assertRaisesRegex(ValueError,'completely checked'):self.check(exit_code=1)

    def test_valid_read_only_observation(self):
        self.assertEqual(self.check()['records_inspected'],20)

    def test_windows_identity_is_read_from_image_not_trusted_from_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            image=Path(temporary)/'verified.ntfs';image.write_bytes(b'fixture')
            prepared=dict(image=image,metadata=dict(windows=dict(family='Windows 10',architecture=9),uninstall_eligible=False))
            with patch.object(ntfs_read,'raw_read',return_value=os_loader()) as reader:
                result=preparation.inspect_windows(prepared,'fixture-ntfscat')
            self.assertEqual(result['windows']['family'],'Windows 10')
            self.assertIsNone(result['marker']);self.assertEqual(reader.call_count,1)
            for raw in (os_loader((6,3,9600,1)),os_loader(machine=0x14c),os_loader((6,1,7601,1))):
                with patch.object(ntfs_read,'raw_read',return_value=raw),self.assertRaises((Refusal,ValueError)):
                    preparation.inspect_windows(prepared,'fixture-ntfscat')

    def test_unreadable_creation_marker_never_becomes_ownership_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            image=Path(temporary)/'verified.ntfs';image.write_bytes(b'fixture')
            prepared=dict(image=image,metadata=dict(windows=dict(family='Windows 10',architecture=9),uninstall_eligible=True))
            with patch.object(ntfs_read,'raw_read',side_effect=[os_loader(),Refusal('file not found')]):
                result=preparation.inspect_windows(prepared,'fixture-ntfscat')
            self.assertIsNone(result['marker'])

if __name__=='__main__':unittest.main()
