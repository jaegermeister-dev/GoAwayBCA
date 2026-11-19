import copy
import unittest
from unittest.mock import patch
import install_windows7 as app
import destination_picker as picker
import drive_geometry
from test_storage_fixtures import fixtures


class Tests(unittest.TestCase):
    def tearDown(self): drive_geometry.install_session(None)

    def test_usb_must_be_explicit_gpt_and_partition_parent_must_match(self):
        d,p,_=fixtures(); d.update(Internal=False,BusProtocol='USB');p['Internal']=False
        self.assertTrue(app.valid_partition(p,d))
        for value in ('FireWire', 'Thunderbolt', None):
            self.assertFalse(app.valid_partition(p,dict(d,BusProtocol=value)))
        self.assertFalse(app.valid_partition(p,dict(d,Content='FDisk_partition_scheme')))
        self.assertFalse(app.valid_partition(dict(p,Internal=True),d))

    def test_external_geometry_is_rechecked_for_each_operation(self):
        drive_geometry.install_session(dict(schema=1,drives={}))
        layout=dict(guid='aaaaaaaa-1111-4111-8111-111111111111',sector_size=512,bytes=4096000,
                    device='disk9',device_path='USB:port1/device1',connection='USB')
        self.assertEqual(drive_geometry.confirm(layout),512)
        for field,value in [('sector_size',4096),('device_path','USB:port2/device1'),('bytes',8192000),('device','disk8')]:
            with self.assertRaises(ValueError):drive_geometry.confirm(dict(layout,**{field:value}))
        drive_geometry.install_session(dict(schema=1,drives={}))
        self.assertEqual(drive_geometry.confirm(dict(layout,sector_size=4096)),4096)

    def test_picker_incompatible_rows_visible_but_disabled(self):
        d,p,listing=fixtures();d.update(Internal=False,BusProtocol='USB');p['Internal']=False
        target=dict(disk=d,partition=p,esp='disk0s1')
        listing['AllDisksAndPartitions'][0]['Partitions'].append(dict(DeviceIdentifier='disk0s4',Content='Apple_HFS'))
        view=picker.rows([target],listing,lambda name:d)
        self.assertTrue(view[0]['enabled']);self.assertEqual(view[0]['location'],'External')
        enabled=[r for r in view if r['kind']=='partition' and r['enabled']]
        self.assertEqual([r['id'] for r in enabled],[0])
        self.assertTrue(any('disk0s4' in r['title'] and not r['enabled'] for r in view))
        mbr=picker.rows([],listing,lambda name:dict(d,Content='FDisk_partition_scheme'))
        self.assertFalse(any(r['enabled'] for r in mbr))
        self.assertIn('MBR',mbr[0]['detail'])


if __name__=='__main__':unittest.main()
