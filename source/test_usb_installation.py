import struct
import unittest
from unittest.mock import Mock, patch

import usb_installation as usb


class Tests(unittest.TestCase):
    def test_internal_path_never_prompts_or_changes_hive(self):
        owner = Mock()
        self.assertIsNone(usb.prepare(owner, dict(disk=dict(Internal=True)), dict(family='Windows 10'), None, None))
        owner.choose.assert_not_called()

    def test_normal_usb_preparation_keeps_registry_unchanged(self):
        owner = Mock(); owner.choose.return_value = 1
        plan = usb.prepare(owner, dict(disk=dict(Internal=False, BusProtocol='USB')), dict(family='Windows 10'), None, None)
        self.assertIsNone(plan['system'])
        guard = Mock()
        self.assertEqual(usb.apply(owner, plan, '/never-open', guard)['mode'], 'normal')
        guard.assert_not_called()

    def test_windows7_has_no_windows_to_go_registry_plan(self):
        owner = Mock(); owner.choose.return_value = 1
        plan = usb.prepare(owner, dict(disk=dict(Internal=False, BusProtocol='USB')), dict(family='Windows 7'), None, None)
        self.assertEqual(plan['choice']['mode'], 'normal')
        self.assertIn('does not have official Windows To Go support', owner.choose.call_args[0][0])

    def test_preserves_existing_boot_flags_and_limits_edits(self):
        cs = 'ControlSet001'
        changes = usb.registry_changes([cs], lambda path, name: (4, struct.pack('<I', 0x21)))
        self.assertEqual(struct.unpack('<I', changes[(cs, 'Control')]['BootDriverFlags'][1])[0], 0x35)
        self.assertEqual(set(changes), {(cs, 'Control'), (cs, 'Services', 'partmgr', 'Parameters'),
            (cs, 'Control', 'Power'), (cs, 'Control', 'Session Manager', 'Power')})
        self.assertNotIn('Start', str(changes))

    def test_invalid_old_flags_or_unknown_device_refuse(self):
        with self.assertRaises(RuntimeError):
            usb.registry_changes(['ControlSet001'], lambda *args: (1, b'text'))
        with self.assertRaises(RuntimeError):
            usb.configure(Mock(), dict(disk=dict(Internal=False, BusProtocol='Thunderbolt')), dict(family='Windows 10'))

    def test_cancel_does_not_prepare(self):
        owner = Mock(); owner.choose.return_value = 2
        with patch.object(usb, 'prepare_hive') as prepare:
            with self.assertRaises(RuntimeError):
                usb.prepare(owner, dict(disk=dict(Internal=False, BusProtocol='USB')), dict(family='Windows 11'), None, None)
            prepare.assert_not_called()


if __name__ == '__main__': unittest.main()
