import argparse
from contextlib import contextmanager, ExitStack
import copy
import json
from pathlib import Path
import struct
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

import backup_metadata
import backup_windows as b
from install_windows7 import Refusal
from test_backup_metadata import location
from test_fast_discovery import loader
from test_installation_records import RecordsTests
from installation_records import make_marker


def os_loader(version=(10,0,19045,1), machine=0x8664):
    data=bytearray(loader(version)); offset=struct.unpack_from('<I',data,60)[0]
    struct.pack_into('<H',data,offset+4,machine)
    return bytes(data)


class Tests(unittest.TestCase):
    def test_supported_families_and_architectures(self):
        for version,family in (((6,3,9600,1),'Windows 8.1'),((10,0,19045,1),'Windows 10'),
                               ((10,0,26100,1),'Windows 11')):
            for machine,architecture in ((0x14c,0),(0x8664,9)):
                if family=='Windows 11' and architecture==0:
                    with self.assertRaises(Refusal):b.windows_identity(os_loader(version,machine))
                    continue
                result=b.windows_identity(os_loader(version,machine))
                self.assertEqual((result['family'],result['architecture']),(family,architecture))

    def test_disabled_os_or_unknown_cpu_not_reenabled_by_backup(self):
        for version,machine in (((6,1,7601,1),0x8664),((6,2,9200,1),0x8664),((10,0,19045,1),0xaa64)):
            with self.assertRaises((ValueError,Refusal)):b.windows_identity(os_loader(version,machine))

    def test_missing_history_is_normal_but_invalid_history_refuses(self):
        probe=Mock();probe.names.return_value=['Windows','Users','WinDeploy']
        self.assertIsNone(b.previous_history(probe));probe.read.assert_not_called()
        probe.names.return_value=[b.HISTORY_PATH[1:]];probe.read.return_value=b'bad json'
        with self.assertRaises(ValueError):b.previous_history(probe)
        meta=backup_metadata.create(location(),dict(family='Windows 10',architecture=9),False)
        probe.read.return_value=json.dumps(meta).encode()
        self.assertEqual(b.previous_history(probe),meta)

    def test_foreign_warning_does_not_promise_uninstall_or_boot(self):
        text,details=b.review_text(dict(title='Personal Windows',summary='partition fixture'),location(),False)
        self.assertIn('EXPERIMENTAL',text)
        self.assertIn('exact same physical drive',text)
        self.assertIn('will not enable uninstalling',text)
        self.assertIn('not a whole-disk or EFI-partition backup',text)
        self.assertIn(location()['partition_guid'],details)

    def test_refusal_before_file_selection_or_unmount(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'ntfsclone').write_bytes(b'fixture')
            obj=object.__new__(b.WindowsBackup)
            obj.args=argparse.Namespace(tools_dir=str(root));obj.session=root;obj.ntfscat='fixture'
            obj.interaction=Mock();obj.begin=Mock();obj.approve=Mock(return_value=False)
            obj.choose=Mock(return_value=0);obj.mount_esp=Mock(return_value=root)
            obj.discovery_json=Mock(return_value=None)
            target=dict(partition=dict(DeviceIdentifier='disk99s2'),disk=dict(DeviceIdentifier='disk99'),esp='disk99s1')
            row=dict(target=target,title='Windows 10',summary='fixture',observed={},marker=None,record=None)
            obj.discover=Mock(return_value=dict(scan_complete=True,installations=[row]))
            probe=Mock();probe.__enter__=Mock(return_value=probe);probe.__exit__=Mock(return_value=False)
            probe.read.return_value=os_loader();probe.names.return_value=['Windows']
            with patch.object(b,'Probe',return_value=probe),patch.object(b,'info'), \
                    patch.object(b,'assert_same_target'),patch.object(b.backup_drive,'describe',return_value=location()), \
                    patch.object(b.os,'access',return_value=True),patch.object(b,'open_backup') as opened, \
                    patch.object(b,'ensure_target_unmounted') as unmount:
                with self.assertRaisesRegex(Refusal,'cancelled'):obj.execute()
            opened.assert_not_called();unmount.assert_not_called();obj.interaction.request.assert_not_called()

    def test_workflow_preserves_foreign_provenance_and_verifies_before_notice(self):
        for connection in ('Internal','USB'):
            self.workflow(connection=connection)

    def test_workflow_carries_verified_creation_pair_for_owned_install(self):
        self.workflow(owned=True)

    def test_changed_source_after_dialog_cannot_start_capture(self):
        self.workflow(change_loader=True)

    def test_busy_unmount_cannot_start_capture(self):
        self.workflow(unmount_fails=True)

    def workflow(self,connection='Internal',change_loader=False,unmount_fails=False,owned=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'ntfsclone').write_bytes(b'fixture')
            destination=root/'backup.gabackup';events=[];selected_path='/fixture/Windows.gabackup'
            obj=object.__new__(b.WindowsBackup)
            obj.args=argparse.Namespace(tools_dir=str(root));obj.session=root;obj.ntfscat='fixture'
            obj.begin=Mock();obj.approve=Mock(return_value=True);obj.choose=Mock(return_value=0)
            obj.note=events.append;obj.close_esp=Mock(return_value=[])
            obj.mount_esp=Mock(return_value=root);obj.discovery_json=Mock(return_value=None)
            obj.interaction=Mock(user_uid=501)
            def request(kind,*args,**kwargs):
                events.append(kind)
                return selected_path if kind=='backup_save_file' else True
            obj.interaction.request.side_effect=request
            target=dict(partition=dict(DeviceIdentifier='disk99s2'),disk=dict(DeviceIdentifier='disk99'),esp='disk99s1')
            row=dict(target=target,title='Personal Windows',summary='fixture',observed={},marker=None,record=None)
            obj.discover=Mock(return_value=dict(scan_complete=True,installations=[row]))
            probe=Mock();probe.__enter__=Mock(return_value=probe);probe.__exit__=Mock(return_value=False)
            probe.read.side_effect=[os_loader(),os_loader((10,0,19045,2)) if change_loader else os_loader()]
            probe.names.return_value=['Windows']
            selected_location=dict(location(),connection=connection)
            if owned:
                fixture=RecordsTests();fixture.setUp();record=copy.deepcopy(fixture.record)
                record['family']='Windows 10'
                row.update(record=record,marker=make_marker(record),observed=dict(record,filesystem='ntfs',windows_verified=True))
                selected_location.update(disk_guid=record['disk_guid'],partition_guid=record['partition_guid'],
                    bytes=200*1024**3,partition_bytes=record['partition_bytes'],partition_offset=record['partition_offset'])
                probe.read.side_effect=[os_loader(),os_loader(),json.dumps(row['marker']).encode()]
            state=dict(MountPoint='')
            def unmount(*args):
                events.append('unmount')
                if unmount_fails:raise Refusal('source busy')
            @contextmanager
            def opened(uid,path,create=False):
                self.assertEqual((uid,path,create),(501,selected_path,True))
                events.append('open')
                with destination.open('x+b') as stream:yield stream
            actual_stat=b.os.stat
            def source_stat(path,*args,**kwargs):
                if str(path)=='/dev/disk99s2':return argparse.Namespace(st_mode=stat.S_IFBLK,st_rdev=99999999)
                return actual_stat(path,*args,**kwargs)
            def capture(tool,source,output,metadata,log,guard,note):
                events.append('capture');guard()
                self.assertEqual(metadata['uninstall_eligible'],owned)
                if owned:self.assertEqual(metadata['creation_evidence'],dict(marker=row['marker'],record=row['record']))
                else:self.assertIsNone(metadata['creation_evidence'])
                self.assertEqual(metadata['origin']['kind'],'app' if owned else 'external-or-unverified')
                self.assertEqual(metadata['source']['connection'],connection)
                self.assertEqual(source,'/dev/disk99s2')
                output.write(b'fixture');events.append('verified')
                return dict(full_readback_verified=True)
            with ExitStack() as stack:
                for owner,name,value in ((b,'Probe',Mock(return_value=probe)),(b,'info',Mock(return_value=state)),
                    (b,'assert_same_target',Mock()),(b.backup_drive,'describe',Mock(return_value=selected_location)),
                    (b.os,'access',Mock(return_value=True)),(b,'open_backup',opened),
                    (b,'require_sources_off_target',Mock()),(b,'ensure_target_unmounted',unmount),
                    (b.os,'stat',source_stat),(b.backup_capture,'capture',Mock(side_effect=capture))):
                    stack.enter_context(patch.object(owner,name,value))
                if change_loader or unmount_fails:
                    with self.assertRaisesRegex(Refusal,'changed during review' if change_loader else 'source busy'):obj.execute()
                    self.assertNotIn('capture',events);self.assertNotIn('notice',events)
                    self.assertFalse((root/'backup-complete.json').exists())
                else:
                    obj.execute()
                    self.assertLess(events.index('unmount'),events.index('capture'))
                    self.assertLess(events.index('verified'),events.index('notice'))
                    report=json.loads((root/'backup-complete.json').read_text())
                    self.assertTrue(report['result']['full_readback_verified'])
                    self.assertEqual(report['metadata']['uninstall_eligible'],owned)


if __name__=='__main__':unittest.main()
