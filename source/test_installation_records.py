import copy
import unittest
from installation_records import make_marker, ownership, format_plan, validate_record

D = '11111111-1111-4111-8111-111111111111'
P = '22222222-2222-4222-8222-222222222222'
E = '33333333-3333-4333-8333-333333333333'
I = '44444444-4444-4444-8444-444444444444'
Q = '55555555-5555-4555-8555-555555555555'


class RecordsTests(unittest.TestCase):
    def setUp(self):
        self.record = dict(schema=1, creator='WinDeploy', state='committed', disk_guid=D,
            partition_guid=P, esp_guid=E, installation_uuid=I, family='Windows 7',
            partition_bytes=150*1024**3, partition_offset=1024**2, wim_sha256='a'*64,
            efi_assets=[dict(partition_guid=E, path='EFI/WinDeploy/install-a/boot.efi',
                             sha256='b'*64, exclusive=True)])
        self.observed = dict(self.record, filesystem='ntfs', windows_verified=True)
        self.marker = make_marker(self.record)
        self.hashes = {(E, 'EFI/WinDeploy/install-a/boot.efi'): 'b'*64}

    def test_all_evidence_required(self):
        self.assertTrue(ownership(self.observed, self.marker, self.record, self.hashes)['managed'])
        self.assertFalse(ownership(self.observed, self.marker)['managed'])
        for field in ('partition_bytes', 'partition_offset', 'partition_guid', 'filesystem', 'windows_verified'):
            changed = dict(self.observed, **{field: None})
            self.assertFalse(ownership(changed, self.marker, self.record, self.hashes)['managed'])
        self.assertFalse(ownership(self.observed, {}, self.record, self.hashes)['managed'])
        self.assertFalse(ownership(self.observed, self.marker, self.record, {})['managed'])
        self.assertEqual(ownership(self.observed, self.marker, self.record, {})['status'],
                         'windeploy-boot-files-changed')

    def test_format_is_partition_specific(self):
        plan = format_plan(self.observed, self.marker, self.record, self.hashes, [self.record], True)
        self.assertEqual(plan['erase_partition_guid'], P)
        self.assertEqual(plan['confirmations_required'], 2)
        self.assertFalse(plan['repartition'])
        self.assertFalse(plan['set_default_boot'])
        self.assertTrue(plan['regenerate_menu'])

    def test_incomplete_refused_shared_assets_preserved(self):
        for records, complete in (([self.record], False), ([], True), ([self.record, self.record], True)):
            with self.assertRaises(ValueError):
                format_plan(self.observed, self.marker, self.record, self.hashes, records, complete)
        other = dict(self.record, partition_guid=Q)
        plan=format_plan(self.observed, self.marker, self.record, self.hashes, [self.record, other], True)
        self.assertEqual(plan['efi_cleanup'],[])
        self.assertEqual(plan['preserved_efi_paths'],['EFI/WinDeploy/install-a/boot.efi'])

    def test_unowned_cannot_proceed_even_after_acknowledgement(self):
        self.observed['unattributed_efi_paths'] = ['EFI/Microsoft/Boot/BCD', 'EFI/Boot/BOOTX64.EFI']
        for acknowledged in (False, True):
            with self.assertRaisesRegex(ValueError, 'cannot uninstall'):
                format_plan(self.observed, None, None, None, [], True,
                            unknown_ownership_acknowledged=acknowledged)

    def test_our_install_with_changed_boot_files_preserves_unknown_assets(self):
        self.observed['unattributed_efi_paths'] = ['EFI/Microsoft/Boot/BCD', 'EFI/Boot/BOOTX64.EFI']
        plan = format_plan(self.observed, self.marker, self.record, {}, [self.record], True,
                           unknown_ownership_acknowledged=True)
        self.assertEqual(plan['target'], D+':'+P)
        self.assertEqual(plan['confirmations_required'], 2)
        self.assertEqual(plan['ownership_warnings_required'], 2)
        self.assertEqual(plan['efi_cleanup'], [])
        self.assertEqual(plan['preserved_efi_paths'], self.observed['unattributed_efi_paths'])
        self.assertIn('unusual or unexpected', plan['ownership_warning'])
        self.assertIn('preserved', plan['cleanup_status'])
        self.assertTrue(plan['regenerate_menu'])

    def test_marker_or_geometry_mismatch_cannot_be_overridden(self):
        for marker, record, observed in ((None, self.record, self.observed),
            (self.marker, None, self.observed),
            (self.marker, self.record, dict(self.observed,partition_guid=Q)),
            (self.marker, self.record, dict(self.observed,partition_bytes=80*1024**3))):
            with self.assertRaisesRegex(ValueError, 'cannot uninstall'):
                format_plan(observed, marker, record, self.hashes, [self.record], True,
                            unknown_ownership_acknowledged=True)

    def test_protected_paths_and_ambiguous_assets_refused(self):
        for path in ('EFI/OC/OpenCore.efi', 'EFI/Apple/boot.efi', 'EFI/../boot.efi'):
            bad = copy.deepcopy(self.record)
            bad['efi_assets'][0]['path'] = path
            with self.assertRaises(ValueError):
                validate_record(bad)
        for field, value in (('state','format-started'), ('wim_sha256','fake')):
            bad = dict(self.record, **{field:value})
            with self.assertRaises(ValueError):
                validate_record(bad)


if __name__ == '__main__':
    unittest.main()
