"""Debug-reset fixtures are ordinary temporary files, never real disks."""
import argparse
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import tempfile
import struct
import unittest
from unittest.mock import patch

import reset_menu as app
import menu_lifecycle as lifecycle
from install_windows7 import Refusal, tree_manifest
import test_menu_lifecycle as fixtures
from test_menu_lifecycle import D, E, PARTS, store, plan as add_plan, apply


class PlanTests(unittest.TestCase):
    def files(self):
        return fixtures.LifecycleTests().populated()

    def plan(self, files, disk=D, esp=E):
        return app.plan(files, {k:lifecycle.sha(v) for k,v in files.items()}, disk, esp)

    def test_reset_then_fresh_install_without_windows_partitions(self):
        files = self.files()
        files['EFI/OC/OpenCore.efi'] = b'OpenCore'
        files['EFI/Apple/firmware.scap'] = b'Apple'
        files['EFI/WinDeploy/Reports/example.txt'] = b'diagnostic evidence'
        files['EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi'] = b'per-installation GPU loader'
        result = self.plan(files)
        self.assertFalse(result['needs_review'])
        self.assertTrue(result['canonical_removed'])
        remaining = apply(files, result)
        self.assertEqual(set(remaining), {'EFI/OC/OpenCore.efi','EFI/Apple/firmware.scap',
            'EFI/WinDeploy/Reports/example.txt','EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi'})
        fresh = apply({}, add_plan({}, [], add=0))
        self.assertEqual(len(lifecycle.inspect(fresh,D,E)['catalog']['entries']), 1)

    def test_hash_owned_aliases_only_and_foreign_fallback_preserved(self):
        files = self.files()
        chooser = b'known selector'; driver = b'known driver'
        for path in lifecycle.ALIASES: files[path] = chooser
        files['EFI/Boot/BOOTX64.EFI'] = b'OpenCore fallback'
        files[lifecycle.BASE+'OpenNtfsDxe.efi'] = driver
        with patch.object(lifecycle,'CHOOSER_SHA',lifecycle.sha(chooser)), \
             patch.object(lifecycle,'NTFS_SHA',lifecycle.sha(driver)):
            result = self.plan(files)
        remaining = apply(files,result)
        self.assertEqual(remaining, {'EFI/Boot/BOOTX64.EFI':b'OpenCore fallback'})
        self.assertTrue(any('not a recognised' in line for line in result['preserved']))

    def test_unknown_canonical_not_deleted_even_with_our_menu(self):
        for modification in ('foreign-bcd','bad-state','bad-config','different-esp','different-disk'):
            with self.subTest(modification=modification):
                files = self.files()
                if modification == 'foreign-bcd': files[lifecycle.CANON] = store(2)
                if modification == 'bad-state': files[lifecycle.STATE] = b'bad'
                if modification == 'bad-config': files[lifecycle.CONFIG] = b'bad'
                result = self.plan(files, PARTS[1] if modification=='different-disk' else D,
                                   PARTS[2] if modification=='different-esp' else E)
                self.assertTrue(result['needs_review'])
                self.assertFalse(result['canonical_removed'])
                self.assertIn(lifecycle.CANON, apply(files,result))

    def test_unknown_recovery_semantics_do_not_block_exact_owned_snapshot(self):
        files = self.files()
        # Ownership is established by an ESP-bound active record + identical
        # private snapshot, not by permissively parsing an unknown BCD path.
        active=struct.unpack_from('<I',files[lifecycle.STATE],16)[0]
        files[lifecycle.CANON] = files[lifecycle.PREFIX+'Private-%d.BCD'%active] = b'future recovery form'
        result = self.plan(files)
        self.assertTrue(result['canonical_removed'])

    def test_serviced_canonical_uses_strict_identity(self):
        from bcd import personalise
        template=(Path(__file__).parent/'BCD10.template').read_bytes()
        files=self.files()
        files[lifecycle.CANON]=personalise(template,lifecycle.sha(template),D,E,PARTS[0])
        self.assertTrue(self.plan(files)['canonical_removed'])

    def test_corrupt_or_missing_menu_recovered_from_catalog_for_ownership(self):
        for value in (b'corrupt',None):
            files=self.files()
            if value is None: files.pop(lifecycle.MENU)
            else: files[lifecycle.MENU]=value
            self.assertTrue(self.plan(files)['canonical_removed'])

    def test_unknown_journal_retained_and_reported(self):
        files=self.files();files[lifecycle.PREFIX+'Pending.bin']=b'pending evidence'
        result=self.plan(files)
        self.assertTrue(result['needs_review'])
        self.assertEqual(apply(files,result),{lifecycle.PREFIX+'Pending.bin':b'pending evidence'})

    def test_archived_diagnostic_logs_do_not_block_a_fresh_menu(self):
        files=self.files();path=lifecycle.PREFIX+'Log-0000000000000001-0-1.bin'
        files[path]=b'archived log'
        result=self.plan(files)
        self.assertFalse(result['needs_review'])
        remaining=apply(files,result)
        self.assertEqual(remaining,{path:b'archived log'})
        fresh=apply(remaining,add_plan(remaining,[],add=0))
        self.assertEqual(len(lifecycle.inspect(fresh,D,E)['catalog']['entries']),1)

    def test_no_menu_and_repeated_reset_are_noop(self):
        files={'EFI/OC/OpenCore.efi':b'OpenCore'}
        self.assertEqual(self.plan(files)['changes'],[])
        self.assertEqual(self.plan(apply(self.files(),self.plan(self.files())))['changes'],[])

    def test_case_collisions_and_snapshot_mismatch_rejected(self):
        files=self.files()
        with self.assertRaises((Refusal,ValueError)):
            self.plan(dict(files,**{lifecycle.MENU.lower():b'collision'}))
        with self.assertRaises((Refusal,ValueError)):
            app.plan(files,{k:'0'*64 for k in files},D,E)


class WorkflowTests(unittest.TestCase):
    def test_empty_scan_blocks_owned_foreign_and_incomplete_installation_lists(self):
        for scan in (dict(scan_complete=False,installations=[]),
                     dict(scan_complete=True,installations=[dict(ownership={'managed':True})]),
                     dict(scan_complete=True,installations=[dict(ownership={'managed':False})])):
            manager=app.ResetMenu(argparse.Namespace(),None);manager.tools=lambda:None
            manager.scan=lambda:scan
            with self.assertRaises(Refusal):manager.empty_scan()

    def test_empty_scan_requires_coverage_of_all_ntfs_partitions(self):
        import install_windows7 as base
        import partition_detection as detection
        manager=app.ResetMenu(argparse.Namespace(),None);manager.tools=lambda:None
        scan=dict(scan_complete=True,installations=[],non_windows=[]);manager.scan=lambda:scan
        rows={'disk7s2':dict(node=dict(Whole=False),protocol={'Physical Interconnect Location':'Internal'})}
        with patch.object(base,'plist_command',return_value=[]),patch.object(detection,'registry_index',return_value=rows),patch.object(base,'raw_info',return_value={'FilesystemType':'ntfs'}):
            with self.assertRaisesRegex(Refusal,'Not every NTFS'):manager.empty_scan()
            scan['non_windows']=[dict(partition='disk7s2')]
            self.assertEqual(manager.empty_scan(),scan)

    def scenario(self, mode='success', failure_position=None):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as patches:
            root=Path(temp).resolve();esp=root/'esp';session=root/'session';session.mkdir()
            files=fixtures.LifecycleTests().populated()
            files['EFI/OC/OpenCore.efi']=b'untouched OpenCore'
            files['EFI/Boot/BOOTX64.EFI']=b'untouched foreign fallback'
            for name,data in files.items():
                path=esp/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            before=tree_manifest(esp);events=[];messages=[];readonly=True
            selected=dict(DeviceIdentifier='disk7s1',DiskUUID=E,IOKitSize=512*400,
                Content='EFI',Whole=False,Internal=True,PartitionMapPartitionOffset=512*40)
            parent=dict(DeviceIdentifier='disk7',Internal=True,Whole=True,DeviceBlockSize=512)
            gpt=dict(guid=D,digest='stable GPT',partitions={1:dict(type=app.ESP_TYPE,guid=E,first=40,last=439)})
            test=self
            class UI:
                def log(self,message):
                    messages.append(str(message))
                    if failure_position is not None and str(message).startswith('Resetting Windows boot menu:'):
                        completed=int(str(message).split(': ')[1].split(' ')[0])
                        if completed==failure_position:raise RuntimeError('injected failure')
                def choose(self,text,options):events.append('choose');return 0
                def approve(self,kind,text):
                    test.assertEqual(kind,'reset_review');test.assertIn('WARNING:',text)
                    number=sum(e in ('first-confirmation','second-confirmation') for e in events)+1
                    events.append('first-confirmation' if number==1 else 'second-confirmation')
                    return mode!=('cancel-first' if number==1 else 'cancel-second')
                def confirm(self,text,phrase):
                    events.append('third-confirmation');test.assertEqual(phrase,'confirm')
                    if mode=='gpt-changed':gpt['digest']='changed GPT'
                    if mode=='efi-changed':(esp/lifecycle.CATALOG).write_bytes(b'externally changed')
                    return mode!='cancel-third'
            args=argparse.Namespace(dry_run=mode=='dry-run',work_dir=str(session))
            manager=app.ResetMenu(args,UI());manager.begin=lambda:setattr(manager,'session',session)
            def empty_scan():
                events.append('empty-scan')
                if mode=='windows-present' or (mode=='windows-appeared' and events.count('empty-scan')>1):
                    raise Refusal('Windows installations still exist')
            manager.empty_scan=empty_scan
            def mount(device,readonly=True):
                test.assertEqual(device,'disk7s1');set_access(readonly);events.append('mount-ro' if readonly else 'mount-rw')
                return esp
            def set_access(value):
                nonlocal readonly
                readonly=value
            def info(device):
                if device=='disk7':return copy.deepcopy(parent)
                test.assertEqual(device,'disk7s1')
                return dict(selected,MountPoint=str(esp),ReadOnlyVolume=readonly)
            manager.mount_esp=mount
            def run(argv):test.assertEqual(argv,['/bin/sync']);events.append('sync');return b''
            for name,value in dict(candidates=lambda:[selected],info=info,
                                   gpt_for=lambda target:copy.deepcopy(gpt),run=run).items():
                patches.enter_context(patch.object(app,name,value))
            if mode=='backup-fails':patches.enter_context(patch.object(app.shutil,'copytree',side_effect=OSError('backup failure')))
            error=None
            try:manager.execute()
            except Exception as caught:error=caught
            after=tree_manifest(esp)
            self.assertFalse(manager.formatted)
            self.assertEqual(after['EFI/OC/OpenCore.efi'],before['EFI/OC/OpenCore.efi'])
            self.assertEqual(after['EFI/Boot/BOOTX64.EFI'],before['EFI/Boot/BOOTX64.EFI'])
            if mode=='success' and failure_position is None:
                self.assertIsNone(error,repr(error))
                self.assertEqual(len(after),2)
                self.assertEqual(tree_manifest(session/'efi-before'),before)
                receipt=json.loads((session/'reset-receipt.json').read_text())
                self.assertEqual(receipt['status'],'reset-complete')
                self.assertLess(events.index('third-confirmation'),events.index('mount-rw'))
                self.assertEqual(events[-1],'sync')
                self.assertEqual(events.count('empty-scan'),3)
                journal=json.loads((session/'efi-transaction/journal.json').read_text())
                self.assertEqual(journal['status'],'committed')
                for index,entry in enumerate(journal['files']):
                    self.assertEqual(lifecycle.sha((session/'efi-transaction/before'/str(index)).read_bytes()),entry['before'])
            else:
                self.assertEqual(error is None,mode=='dry-run')
                if mode=='efi-changed':before[lifecycle.CATALOG]=lifecycle.sha(b'externally changed')
                self.assertEqual(after,before)
                self.assertNotIn('sync',events)
                if failure_position is not None:
                    journal=json.loads((session/'efi-transaction/journal.json').read_text())
                    self.assertEqual(journal['status'],'rolled-back')
                if mode in ('dry-run','cancel-first','cancel-second','cancel-third','backup-fails'):
                    self.assertNotIn('mount-rw',events)

    def test_complete_delete_with_verified_backup_and_empty_scans(self):self.scenario()

    def test_cancellation_preview_changes_and_backup_failure(self):
        for mode in ('dry-run','cancel-first','cancel-second','cancel-third','backup-fails','gpt-changed','efi-changed',
                     'windows-present','windows-appeared'):
            with self.subTest(mode=mode):self.scenario(mode)

    def test_each_interrupted_deletion_rolls_back(self):
        count=len(PlanTests().plan(fixtures.LifecycleTests().populated())['changes'])
        for position in range(1,count+1):
            with self.subTest(position=position):self.scenario(failure_position=position)


if __name__=='__main__':unittest.main()
