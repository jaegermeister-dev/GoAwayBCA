"""Restore orchestration only: all device access replaced, no OS installed."""
import argparse
from contextlib import ExitStack,contextmanager
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import restore_windows as r
import backup_metadata
import backtoseven_menu
import backtoseven_menu_actions
import destination_picker
import entry_names
import existing_windows_consent
import installation_destination
from install_windows7 import Refusal
from test_backup_metadata import location


class Tests(unittest.TestCase):
    def test_foreign_restore_keeps_foreign_ownership_and_reconciles_menu(self):self.workflow()
    def test_owned_restore_publishes_only_prepared_ownership(self):self.workflow(owned=True)
    def test_backup_open_cancel_never_prepares_or_writes(self):self.workflow(failure='open')
    def test_cancel_review_never_prepares_or_writes(self):self.workflow(failure='restore_review')
    def test_cancel_ready_never_writes(self):self.workflow(failure='restore_ready')
    def test_cancel_first_confirmation_never_writes(self):self.workflow(failure='review')
    def test_cancel_final_confirmation_never_writes(self):self.workflow(failure='confirm')
    def test_failed_preparation_never_writes(self):self.workflow(failure='prepare')
    def test_incomplete_scan_never_prepares_or_writes(self):self.workflow(failure='scan')
    def test_changed_installations_never_write(self):self.workflow(failure='changed')
    def test_reinserted_drive_never_writes(self):self.workflow(failure='drive')
    def test_nonempty_destination_never_writes(self):self.workflow(failure='occupied')
    def test_busy_destination_never_writes(self):self.workflow(failure='unmount')
    def test_write_failure_never_publishes_boot_menu(self):self.workflow(failure='write')
    def test_changed_efi_after_write_never_publishes_menu(self):self.workflow(failure='efi')

    def workflow(self,failure=None,owned=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);esp_path=root/'esp';esp_path.mkdir()
            obj=object.__new__(r.WindowsRestore)
            obj.session=root;obj.begin=Mock();obj.note=Mock();obj.formatted=False
            obj.args=argparse.Namespace(tools_dir=str(root),dry_run=False)
            obj.ntfscat='fixture';obj.esp_owned=False;obj.esp_device='disk99s1'
            obj.mount_esp=Mock(return_value=esp_path);obj.close_esp=Mock(return_value=[])
            events=[];obj.interaction=Mock(user_uid=501)
            def request(kind,*args,**kwargs):
                events.append(kind)
                if failure=='open' and kind=='backup_open_file':raise ValueError('cancelled')
                return '/fixture/Windows.gabackup' if kind=='backup_open_file' else True
            obj.interaction.request.side_effect=request
            def approve(kind,*args,**kwargs):events.append(kind);return failure!=kind
            obj.approve=approve
            def confirm(*args,**kwargs):events.append('confirm');return failure!='confirm'
            obj.confirm_erase=confirm
            target=dict(partition=dict(DeviceIdentifier='disk99s2'),disk=dict(DeviceIdentifier='disk99'),esp='disk99s1')
            source=location();dest=dict(location(),partition_guid='33333333-3333-4333-8333-333333333333')
            windows=dict(family='Windows 10',architecture=9,version=[10,0,19045,1])
            meta=backup_metadata.create(source,windows,False);meta['menu_title']='My exact saved name'
            geometry=dict(bytes=source['partition_bytes'],logical_sector_size=source['logical_sector_size'],allocated_bytes=1024**2)
            prepared=dict(metadata=meta,geometry=geometry)
            inspected=dict(windows=windows,marker=None)
            gpt=dict(guid=dest['disk_guid'],sector_size=dest['logical_sector_size'],digest='gpt fixture')
            win=dict(guid=dest['partition_guid']);esp=dict(guid='11111111-1111-4111-8111-111111111111')
            row=dict(title='Other Windows',partition_guid='22222222-2222-4222-8222-222222222222')
            removed=dict(title='Removed externally',partition_guid='44444444-4444-4444-8444-444444444444')
            scan=dict(scan_complete=failure!='scan',installations=[row])
            fresh=copy.deepcopy(scan)
            if failure=='changed':fresh['installations'].append(dict(title='Added during confirmation'))
            obj.scan=Mock(side_effect=[scan,fresh])
            old=dict(catalog=dict(entries=[row,removed]))
            entry=dict(title='My exact saved name',partition_guid=win['guid'])
            record={'fixture_owned':True} if owned else None
            staged=dict(overlay=dict(entry=entry,record=record),stream=dict(image=root/'prepared.stream',sha256='verified'),
                resize=dict(requires_windows_filesystem_check=False))
            menu=dict(catalog=dict(entries=[row,entry]),changes=[dict(path='EFI/fixture',expected=None,data=b'menu')],windows_sources=['other'])
            def prepare(*args):
                events.append('prepare')
                if failure=='prepare':raise ValueError('private preparation failed')
                return staged
            def assembled(*args,**kwargs):
                events.append('assemble')
                self.assertEqual(kwargs['confirmed_absent'],[removed])
                self.assertEqual(kwargs['addition']['entry']['title'],'My exact saved name')
                return menu
            def info(device):
                if device=='disk99s1':return dict(DiskUUID=esp['guid'],MountPoint=str(esp_path),ReadOnlyVolume=False)
                return {}
            @contextmanager
            def opened(uid,path):
                self.assertEqual((uid,path),(501,'/fixture/Windows.gabackup'))
                yield io.BytesIO(b'archive fixture')
            def unmount(*args):
                events.append('unmount')
                if failure=='unmount':raise ValueError('busy target')
            def write(stream,device,guard,note):
                events.append('write');guard();self.assertEqual(device,'/dev/disk99s2')
                self.assertIn('confirm',events)
                if failure=='write':raise ValueError('readback failure')
                events.append('write-verified');return dict(complete_readback=True)
            def unchanged(*args):
                if failure=='efi' and 'write' in events:raise ValueError('EFI changed')
            tx=Mock();tx.prepare.side_effect=lambda changes:events.append('journal')
            tx.commit.side_effect=lambda **kwargs:events.append('commit')
            locations=[dest,dict(dest,serial='changed') if failure=='drive' else dest]
            def check_target(*args):
                if failure=='occupied':raise ValueError('Windows folder already exists')
            patches=[(r,'open_backup',opened),(r.backup_archive,'ImageReader',Mock(return_value=argparse.Namespace(metadata=meta))),
                (r.preparation,'materialize',Mock(return_value=prepared)),(r.preparation,'inspect_windows',Mock(return_value=inspected)),
                (r,'run',Mock(return_value=b'MacBookPro11,4')),(r.mac_compatibility,'confirm_model',Mock(return_value='MacBookPro11,4')),
                (r.firmware_compatibility,'configure',Mock(return_value={})),(r.firmware_compatibility,'revalidate',Mock()),
                (r.mac_compatibility,'configure',Mock(return_value={})),(r.cpu_compatibility,'configure',Mock(return_value={})),
                (existing_windows_consent,'confirm',Mock()),(r,'inventory',Mock(return_value=[])),
                (r,'eligible_targets',Mock(return_value=[target])),(destination_picker,'choose',Mock(return_value=target)),
                (installation_destination,'check',check_target),(r,'require_sources_off_target',Mock()),
                (r.backup_drive,'describe',Mock(side_effect=locations)),(entry_names,'prompt',Mock(return_value=entry['title'])),
                (r,'gpt_for',Mock(return_value=gpt)),(r,'match_gpt',Mock(return_value=(win,esp))),
                (r,'tree_manifest',Mock(return_value={})),(backtoseven_menu_actions,'read_efi',Mock(return_value={})),
                (backtoseven_menu,'previous',Mock(return_value=old)),(r.menu_presence,'collect',Mock(return_value=[removed])),
                (r.menu_presence,'recheck',Mock()),(r,'prepare_private',prepare),(backtoseven_menu_actions,'assemble',assembled),
                (backtoseven_menu_actions,'verify_sources',Mock()),(r,'info',info),(r,'partition_summary',Mock(return_value='Fixture destination')),
                (r,'assert_same_target',Mock()),(r,'assert_postformat_target',Mock()),(r,'require_efi_unchanged',unchanged),
                (r,'hash_file',Mock(return_value='verified')),(r,'EFITransaction',Mock(return_value=tx)),
                (r,'ensure_target_unmounted',unmount),(r.backup_restore_write,'write_partition',write)]
            with ExitStack() as stack:
                for owner,name,value in patches:stack.enter_context(patch.object(owner,name,value))
                if failure:
                    with self.assertRaises((ValueError,Refusal)):obj.execute()
                else:obj.execute()
            if failure:
                self.assertNotIn('commit',events);self.assertNotIn('notice',events)
                if failure not in ('write','efi'):self.assertNotIn('write',events)
                if failure in ('open','scan','occupied','restore_review'):self.assertNotIn('prepare',events)
            else:
                for first,second in (('prepare','restore_ready'),('restore_ready','review'),('review','confirm'),
                    ('confirm','journal'),('journal','write'),('write-verified','commit'),('commit','notice')):
                    self.assertLess(events.index(first),events.index(second))
                records=[c for c in menu['changes'] if c['path'].startswith('EFI/WinDeploy/Records/')]
                self.assertEqual(len(records),int(owned))
                report=json.loads((root/'restore-receipt.json').read_text())
                self.assertEqual(report['status'],'restored-awaiting-first-boot')
                self.assertEqual(report['restore']['title'],'My exact saved name')
                self.assertEqual(report['confirmations'],3)


if __name__=='__main__':unittest.main()
