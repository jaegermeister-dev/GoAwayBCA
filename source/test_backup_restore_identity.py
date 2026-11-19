import unittest
import uuid

import backup_restore_identity as identity


class Tests(unittest.TestCase):
    def setUp(self):
        self.source = str(uuid.uuid4()); self.target = str(uuid.uuid4())
        self.old = (3, identity.gpt_identifier(self.source))
        self.new = (3, identity.gpt_identifier(self.target))

    def test_non_c_system_drive_and_its_aliases_move_together(self):
        values = {'\\DosDevices\\D:': self.old, '\\??\\Volume{source}': self.old,
                  '\\DosDevices\\C:': (3, b'unrelated volume')}
        changed, removed = identity.mappings(values, 'D', self.source, self.target)
        self.assertEqual(changed['\\DosDevices\\D:'], self.new)
        self.assertEqual(changed['\\??\\Volume{source}'], self.new)
        self.assertEqual(changed['\\DosDevices\\C:'], values['\\DosDevices\\C:'])
        self.assertEqual(removed, [])

    def test_previous_destination_alias_is_removed_only_from_restored_hive(self):
        values = {'\\DosDevices\\C:': self.old, '\\DosDevices\\F:': self.new}
        changed, removed = identity.mappings(values, 'C', self.source, self.target)
        self.assertEqual(changed, {'\\DosDevices\\C:': self.new})
        self.assertEqual(removed, ['\\DosDevices\\F:'])
        self.assertIn('\\DosDevices\\F:', values)

    def test_same_partition_keeps_existing_aliases(self):
        values = {'\\DosDevices\\C:': self.old, '\\??\\Volume{source}': self.old}
        self.assertEqual(identity.mappings(values, 'C', self.source, self.source), (values, []))

    def test_missing_mapping_is_created_without_c_assumption(self):
        changed, removed = identity.mappings({}, 'E', self.source, self.target)
        self.assertEqual(changed, {'\\DosDevices\\E:': self.new})

    def test_conflicting_actual_system_mapping_is_not_overwritten(self):
        with self.assertRaises(ValueError):
            identity.mappings({'\\DosDevices\\C:': (3, b'not this backup')}, 'C', self.source, self.target)

    def test_case_insensitive_mapping_retains_spelling(self):
        changed, _ = identity.mappings({'\\dosdevices\\c:': self.old}, 'C', self.source, self.target)
        self.assertEqual(changed, {'\\dosdevices\\c:': self.new})

    def test_duplicate_case_aliases_refuse(self):
        with self.assertRaises(ValueError):
            identity.mappings({'\\DosDevices\\C:': self.old, '\\dosdevices\\c:': self.old},
                              'C', self.source, self.target)

    def test_gpt_identifier_has_exact_native_layout(self):
        self.assertEqual(identity.gpt_identifier('00112233-4455-6677-8899-aabbccddeeff'),
                         bytes.fromhex('444d494f3a49443a33221100554477668899aabbccddeeff'))

    def test_system_root_and_malformed_values(self):
        self.assertEqual(identity.system_letter(1, 'd:\\Windows\0'.encode('utf-16le')), 'D')
        for root in ('%SystemDrive%\\Windows\0', 'C:\\Windows', 'C:\\Win\0dows\0', '\\\\server\\Windows\0'):
            with self.assertRaises(ValueError):
                identity.system_letter(1, root.encode('utf-16le'))


if __name__ == '__main__':
    unittest.main()
