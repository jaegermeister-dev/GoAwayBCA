import copy
import unittest
from unittest.mock import patch
import backup_drive
import install_windows7 as app
from test_storage_fixtures import fixtures

D='aaaaaaaa-1111-4111-8111-111111111111'
E='bbbbbbbb-2222-4222-8222-222222222222'

class Tests(unittest.TestCase):
    def describe(self,sector=512,usb=False,change_identity=False):
        disk,part,_=fixtures()
        disk.update(Internal=not usb,BusProtocol='USB' if usb else 'PCI',DiskUUID=D,
                    DeviceBlockSize=sector)
        part.update(Internal=not usb)
        esp=dict(part,DeviceIdentifier='disk0s1',DiskUUID=E,Content='EFI',
            PartitionMapPartitionOffset=1024**2,IOKitSize=200*1024**2,Size=200*1024**2,TotalSize=200*1024**2)
        target=dict(disk=disk,partition=part,esp='disk0s1')
        def row(p,kind):return dict(guid=p['DiskUUID'].lower(),type=kind,flags=0,
            first=p['PartitionMapPartitionOffset']//sector,
            last=(p['PartitionMapPartitionOffset']+p['IOKitSize'])//sector-1)
        layout=dict(guid=D,bytes=disk['IOKitSize'],sector_size=sector,
            partitions={1:row(esp,app.ESP_TYPE),3:row(part,app.BASIC_TYPE)})
        tree=[{'Device Characteristics':{'Serial Number':'same-serial','Product Name':'SSD'},
            'Protocol Characteristics':{'Physical Interconnect':'USB' if usb else 'PCI',
                'Physical Interconnect Location':'External' if usb else 'Internal'},
            'IORegistryEntryChildren':[{'BSD Name':'disk0','Whole':True}]}]
        current=copy.deepcopy(disk)
        if change_identity:current['DeviceTreePath']='Different connection'
        with patch.object(app,'info',side_effect=lambda n:{'disk0':current,'disk0s1':esp,'disk0s3':part}[n]), \
             patch('disk_layout.layout',return_value=layout) as read, \
             patch('builtins.open',side_effect=AssertionError('No raw device reads permitted')):
            result=backup_drive.describe(target,tree)
        read.assert_called_once_with(disk,tree)
        return result

    def test_actual_logical_sectors_not_assumed_from_bus(self):
        for sector in (512,4096):
            for usb in (False,True):
                result=self.describe(sector,usb)
                self.assertEqual(result['logical_sector_size'],sector)
                self.assertEqual(result['connection'],'USB' if usb else 'Internal')
                self.assertIsNone(result['physical_sector_size'])
                self.assertEqual(result['partition_bytes'],40*app.GIB)

    def test_usb_bridge_serial_never_claimed_as_physical_drive_identity(self):
        self.assertEqual(self.describe(usb=True)['serial_kind'],'connection-reported')
        self.assertEqual(self.describe()['serial_kind'],'physical-drive')

    def test_changed_destination_identity_stops_before_metadata_accepted(self):
        with self.assertRaises(app.Refusal):self.describe(change_identity=True)

if __name__=='__main__':unittest.main()
