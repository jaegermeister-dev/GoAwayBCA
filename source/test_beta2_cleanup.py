import unittest
from storage_cleanup import PREPARATION

class CleanupBoundary(unittest.TestCase):
    def test_restore_scratch_is_selectable_but_recovery_and_backups_are_not(self):
        for name in ('usb-preparation','restore-preparation','verified-source.ntfs'):
            self.assertIsNotNone(PREPARATION.fullmatch(name))
        for name in ('efi-before','efi-transaction','restore-receipt.json','installation.json',
                     'personal.gabackup','backup.gabackup','worker.log','../verified-source.ntfs',
                     'restore-preparation/../efi-before','diagnostic-export'):
            self.assertIsNone(PREPARATION.fullmatch(name))
