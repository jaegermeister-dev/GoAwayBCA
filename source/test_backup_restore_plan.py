import copy
import unittest

import backup_metadata
import backup_restore_plan as r
from installation_records import make_marker
from test_backup_metadata import location
import test_installation_records


class Tests(unittest.TestCase):
    def plan(self,metadata,geometry,destination,marker):
        return r.plan(metadata,geometry,destination,marker,metadata['windows'])
    def metadata(self, sector=512):
        return backup_metadata.create(dict(location(sector=sector),serial_kind='physical-drive'),
                                      dict(family='Windows 10',architecture=9),False)

    def geometry(self,metadata):
        return dict(bytes=metadata['source']['partition_bytes'],logical_sector_size=metadata['source']['logical_sector_size'],
                    allocated_bytes=8*1024**3)

    def test_routes_follow_actual_sector_size_not_connection(self):
        for source_sector in (512,4096):
            for target_sector in (512,4096):
                for connection in ('Internal','USB'):
                    metadata=self.metadata(source_sector)
                    destination=dict(location(sector=target_sector,connection=connection),serial_kind='physical-drive')
                    result=self.plan(metadata,self.geometry(metadata),destination,None)
                    self.assertEqual(result['route'],'ntfs-image' if source_sector==target_sector else 'file-conversion')
                    self.assertFalse(result['destination_writes'])
                    self.assertTrue(result['capacity_preparation_required'])

    def test_different_drive_same_connection_still_warns(self):
        metadata=self.metadata()
        destination=dict(location(serial='different SSD'),serial_kind='physical-drive')
        result=self.plan(metadata,self.geometry(metadata),destination,None)
        text,details=r.review(result)
        self.assertIn('DIFFERENT PHYSICAL DRIVE',text)
        self.assertIn('Drive serial number',details)
        self.assertIn('Internal → Internal',text)
        self.assertIn('512 → 512',text)

    def test_repeated_restore_uses_latest_source_and_preserves_original_location(self):
        first=self.metadata()
        second_location=dict(location(serial='second drive',sector=4096,connection='USB'),serial_kind='physical-drive')
        history=backup_metadata.restored_history(first,second_location)
        second=backup_metadata.create(second_location,first['windows'],False,history)
        result=self.plan(second,self.geometry(second),location(serial='third drive'),None)
        self.assertEqual(result['source']['serial'],'second drive')
        self.assertEqual(result['restored_history']['origin']['location']['serial'],first['source']['serial'])
        self.assertEqual([item['event'] for item in result['restored_history']['history']],['backup','restore','backup','restore'])

    def test_metadata_flag_and_copied_marker_alone_never_grant_ownership(self):
        metadata=self.metadata();metadata['origin']['kind']='app';metadata['uninstall_eligible']=True
        metadata['creation_evidence']=dict(marker={'invented':True},record={})
        result=self.plan(metadata,self.geometry(metadata),location(),{'different':True})
        self.assertFalse(result['provenance']['eligible'])
        self.assertFalse(result['restored_history']['uninstall_eligible'])
        self.assertIn('not enable uninstalling',r.review(result)[0])

    def test_genuine_creation_pair_is_carried_and_foreign_origin_is_never_promoted(self):
        fixture=test_installation_records.RecordsTests();fixture.setUp();record=copy.deepcopy(fixture.record)
        record['family']='Windows 10';marker=make_marker(record)
        source=dict(location(),disk_guid=record['disk_guid'],partition_guid=record['partition_guid'],bytes=200*1024**3,
                    partition_bytes=record['partition_bytes'],partition_offset=record['partition_offset'])
        metadata=backup_metadata.create(source,dict(family='Windows 10',architecture=9),True)
        metadata['creation_evidence']=dict(record=record,marker=marker)
        result=self.plan(metadata,self.geometry(metadata),source,marker)
        self.assertTrue(result['provenance']['eligible'])
        self.assertEqual(result['provenance']['record'],record)
        metadata['uninstall_eligible']=False;metadata['origin']['kind']='external-or-unverified'
        self.assertFalse(self.plan(metadata,self.geometry(metadata),source,marker)['provenance']['eligible'])

    def test_disabled_os_bad_geometry_and_impossible_capacity_refuse(self):
        for family in ('Windows 7','Windows 8'):
            metadata=self.metadata();metadata['windows']['family']=family
            with self.assertRaisesRegex(ValueError,'not enabled'):self.plan(metadata,self.geometry(metadata),location(),None)
        metadata=self.metadata();geometry=self.geometry(metadata);geometry['logical_sector_size']=4096
        with self.assertRaisesRegex(ValueError,'geometry'):self.plan(metadata,geometry,location(),None)
        with self.assertRaisesRegex(ValueError,'cannot contain'):
            self.plan(metadata,self.geometry(metadata),dict(location(),partition_bytes=4*1024**3),None)

    def test_architecture_and_version_require_independent_source_observation(self):
        metadata=self.metadata()
        for observed in (None,{},dict(family='Windows 7',architecture=9),dict(family='Windows 10',architecture=0)):
            with self.assertRaisesRegex(ValueError,'verified from'):
                r.plan(metadata,self.geometry(metadata),location(),None,observed)


if __name__=='__main__':unittest.main()
