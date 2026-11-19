import copy
import unittest
import backup_metadata as b

D='aaaaaaaa-1111-4111-8111-111111111111'
P='bbbbbbbb-2222-4222-8222-222222222222'
Q='cccccccc-3333-4333-8333-333333333333'

def location(serial='drive A',sector=512,connection='Internal',disk=D):
    return dict(serial=serial,model='SSD',disk_guid=disk,partition_guid=P,logical_sector_size=sector,
        physical_sector_size=4096,connection=connection,controller='controller',adapter=None,
        bytes=100*1024**3,partition_bytes=40*1024**3,partition_offset=1024**2,layout=[])

class Tests(unittest.TestCase):
    def backup(self,origin=True):return b.create(location(),dict(family='Windows 10',architecture=9),origin)
    def test_a_to_b_to_c_uses_fresh_b_source_but_retains_a_origin(self):
        first=self.backup();original=copy.deepcopy(first)
        second_location=location('drive B',4096,'USB',Q)
        restored=b.restored_history(first,second_location)
        second=b.create(second_location,first['windows'],True,previous=restored)
        self.assertEqual(second['origin']['location']['serial'],'drive A')
        self.assertEqual(second['source']['serial'],'drive B')
        self.assertEqual([r['event'] for r in second['history']],['backup','restore','backup'])
        comparison=b.compare(second,location('drive C'))
        self.assertTrue(comparison['sector_size_changed'])
        self.assertEqual(next(r for r in comparison['fields'] if r['field']=='serial')['source'],'drive B')
        self.assertEqual(first,original)
    def test_foreign_origin_never_gains_uninstall_eligibility(self):
        backup=self.backup(False)
        next_backup=b.create(location('B'),backup['windows'],True,previous=b.restored_history(backup,location('B')))
        self.assertFalse(next_backup['uninstall_eligible'])
        self.assertEqual(next_backup['origin']['kind'],'external-or-unverified')
        forged=copy.deepcopy(next_backup);forged['uninstall_eligible']=True
        with self.assertRaises(ValueError):b.validate(forged)
    def test_missing_serial_is_unknown_not_same_physical_drive(self):
        meta=b.create(location(None),dict(family='Windows 7',architecture=9),True)
        self.assertIsNone(b.compare(meta,location(None))['same_physical_drive'])
        self.assertIn('could not be confirmed',b.warning(meta,location(None)))
    def test_same_bus_still_warns_about_different_drive(self):
        text=b.warning(self.backup(),location('new internal SSD',4096))
        self.assertIn('THIS IS A DIFFERENT DRIVE',text)
        self.assertIn('Logical sector size: 512 → 4096',text)
    def test_adapter_serial_is_not_proof_of_same_physical_drive(self):
        original=location('USB bridge serial',connection='USB')
        meta=b.create(original,dict(family='Windows 10',architecture=9),False)
        self.assertIsNone(b.compare(meta,original)['same_physical_drive'])
        original.update(connection='Internal',serial_kind='physical-drive')
        meta=b.create(original,dict(family='Windows 10',architecture=9),True)
        self.assertTrue(b.compare(meta,original)['same_physical_drive'])
    def test_invalid_metadata_cannot_be_restored(self):
        meta=self.backup();meta['source']['logical_sector_size']=0
        with self.assertRaises(ValueError):b.compare(meta,location())
    def test_owned_backup_loses_uninstall_permission_without_fresh_pair(self):
        meta=self.backup()
        fresh=b.create(location(),meta['windows'],False,previous=meta)
        self.assertFalse(fresh['uninstall_eligible'])
        self.assertEqual(fresh['origin']['kind'],'app')

if __name__=='__main__':unittest.main()
