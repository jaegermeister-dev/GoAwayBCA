"""The third SIP choice attempts a check; it never declares the disk safe."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import startup_storage as s


class UserAutomaticCheck(unittest.TestCase):
    def exercise(self, choice, result, expected_error=None):
        with tempfile.TemporaryDirectory() as temp:
            ui = Mock()
            ui.request.return_value = choice
            installer = s.StartupCheck(argparse.Namespace(work_dir=temp), interaction=ui)
            installer.ntfscat = 'fixture-ntfscat'
            status = {'status':'partial-or-unknown', 'automatic':False}
            with patch.object(installer, 'tools'), patch('startup_storage.DeploymentLock'), \
                 patch('drive_geometry.collect', return_value={'schema':1,'drives':{}}), \
                 patch('startup_storage.sip_status', return_value=status), \
                 patch('startup_storage.inspect', return_value=result) as inspect:
                if expected_error:
                    with self.assertRaisesRegex(Exception, expected_error): installer.execute()
                else:
                    installer.execute()
                self.assertEqual(inspect.call_count, 1 if choice == 'automatic' else 0)
            self.assertEqual(ui.request.call_count, 1)
            self.assertEqual(json.loads((installer.session/'sip-status.json').read_text()), status)
            self.assertFalse((installer.session/'startup-layout-consent.json').exists())
            if choice == 'automatic':
                self.assertEqual(json.loads((installer.session/'startup-disk-check.json').read_text()), result)
                receipt = json.loads((installer.session/'sip-choice.json').read_text())
                self.assertEqual(receipt, {'choice':'automatic','detected_status':'partial-or-unknown','sip_changed':False})

    def test_current_permissions_can_pass_without_claiming_sip_disabled(self):
        self.exercise('automatic', {'blocked':[],'errors':[],'allowed':True})

    def test_hybrid_blocks_after_user_requests_automatic_check(self):
        self.exercise('automatic', {'blocked':['disk0: hybrid MBR'],'errors':[],'allowed':False}, 'THIS CONFIGURATION IS NOT SUPPORTED')

    def test_unreadable_does_not_fall_through_to_unchecked_session(self):
        self.exercise('automatic', {'blocked':[],'errors':['disk0: Operation not permitted'],'allowed':False}, 'could not be checked')

    def test_instructions_do_not_inspect_disk(self):
        self.exercise('guide', None, 'csrutil disable')

    def test_invalid_or_cancelled_choice_does_not_inspect_disk(self):
        for choice in ('cancel', 'unknown', None):
            with self.subTest(choice=choice): self.exercise(choice, None, 'cancelled')


if __name__ == '__main__': unittest.main()
