import unittest
from efi_mounts import remount_for_write

class Tests(unittest.TestCase):
    def setUp(self):
        self.row=dict(DeviceIdentifier='disk1s1',DiskUUID='abc',ParentWholeDisk='disk1',Internal=False,
                      Content='EFI',Whole=False,MountPoint='/Volumes/EFI',ReadOnlyVolume=True)
    def test_cancel_never_unmounts(self):
        commands=[]
        with self.assertRaises(RuntimeError):remount_for_write('disk1s1',self.row,lambda d:self.row,commands.append,lambda *a:1)
        self.assertEqual(commands,[])
    def test_identity_change_never_unmounts(self):
        commands=[]
        with self.assertRaises(RuntimeError):remount_for_write('disk1s1',self.row,lambda d:dict(self.row,DiskUUID='changed'),commands.append,lambda *a:0)
        self.assertEqual(commands,[])
    def test_normal_unmount_only(self):
        commands=[];states=iter([self.row,dict(self.row,MountPoint='')])
        result=remount_for_write('disk1s1',self.row,lambda d:next(states),commands.append,lambda *a:0)
        self.assertFalse(result['MountPoint'])
        self.assertEqual(commands,[['/usr/sbin/diskutil','unmount','disk1s1']])
