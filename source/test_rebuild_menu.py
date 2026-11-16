"""Menu-rebuild execution boundaries use ordinary files, never real disks."""
import argparse
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import rebuild_menu as app
import menu_lifecycle as lifecycle
from install_windows7 import tree_manifest
from test_menu_lifecycle import LifecycleTests,D,E,PARTS,entry
from test_menu_presence import proof


class RebuildWorkflowTests(unittest.TestCase):
    def fixture(self,scenario):
        with tempfile.TemporaryDirectory() as temporary,ExitStack() as patches:
            root=Path(temporary).resolve();esp=root/'esp';session=root/'session';session.mkdir()
            resources=root/'resources';(resources/'efi').mkdir(parents=True)
            chooser=b'MZ verified fixture selector';ntfs=b'MZ verified fixture ntfs'
            (resources/'efi/Windows.efi').write_bytes(chooser);(resources/'efi/OpenNtfsDxe.efi').write_bytes(ntfs)
            (resources/'efi/Ntfs4Kn.efi').write_bytes((Path(__file__).resolve().parent/'efi/Ntfs4Kn.efi').read_bytes())
            files=LifecycleTests().populated();files['EFI/OC/OpenCore.efi']=b'protected OpenCore'
            if scenario=='foreign-fallback':files['EFI/Boot/BOOTX64.EFI']=b'OpenCore firmware fallback'
            files[lifecycle.MENU]=b'broken redundant menu'
            for name,data in files.items():
                path=esp/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            before=tree_manifest(esp);messages=[];events=[];scans=0;readonly=True
            target=dict(esp='disk7s1',partition=dict(DeviceIdentifier='disk7s2'),disk=dict(DeviceIdentifier='disk7'))
            row=dict(target=target,observed=entry(0),efi_manifest=before,title='Windows 7',summary='Fixture Windows')
            all_removed=scenario.startswith('all-externally-removed')
            other_disk=scenario=='all-externally-removed-other-disk'
            class UI:
                def log(self,message):messages.append(str(message))
                def request(self,kind,text,**fields):
                    self_test.assertEqual(kind,'notice')
                    self_test.assertEqual(fields,dict(button_title='Done'))
                    self_test.assertIn('boot menu has been rebuilt',text)
                    events.append('popup')
                def approve(self,kind,text):
                    self_test.assertEqual(kind,'rebuild_review')
                    self_test.assertIn('does not format',text)
                    events.append('approve')
                    if scenario=='efi-changed':(esp/'EFI/OC/OpenCore.efi').write_bytes(b'external change')
                    return scenario!='cancel'
            self_test=self
            args=argparse.Namespace(iso=None,prepared_media=None,boot_manager=None,tools_dir=str(resources),
                                    work_dir=str(session),dry_run=scenario=='dry-run')
            manager=app.RebuildMenu(args,UI())
            def begin():manager.session=session
            def scan():
                nonlocal scans
                scans+=1
                rows=[copy.deepcopy(dict(row,observed=entry(i))) for i in range(3)]
                if all_removed:rows=[]
                if other_disk:
                    other=dict(row,target=dict(esp='disk9s1',disk=dict(DeviceIdentifier='disk9')),
                        observed=dict(entry(0),disk_guid=PARTS[1]),summary='Other disk Windows')
                    rows=[other]
                if scenario=='availability-changed' and scans>1:rows.pop()
                return dict(scan_complete=not(scenario=='incomplete' or (scenario=='fresh-incomplete' and scans>1)),
                            installations=rows,scan_failures=[])
            def mount(device,readonly=True):
                self.assertEqual(device,'disk7s1');set_readonly(readonly);return esp
            def set_readonly(value):
                nonlocal readonly
                readonly=value
            def info(device):
                self.assertEqual(device,'disk7s1')
                return dict(DiskUUID=E,MountPoint=str(esp),ReadOnlyVolume=readonly)
            def run(command):
                self.assertEqual(command,['/bin/sync']);events.append('sync');return b''
            gpt=dict(guid=D,digest='unchanged GPT',partitions={1:dict(guid=E)})
            for name,value in dict(ROOT=resources,info=info,gpt_for=lambda *a:gpt,
                match_gpt=lambda *a:(dict(guid=PARTS[0]),dict(guid=E)),run=run).items():
                patches.enter_context(patch.object(app,name,value))
            patches.enter_context(patch.object(lifecycle,'CHOOSER_SHA',lifecycle.sha(chooser)))
            patches.enter_context(patch.object(lifecycle,'NTFS_SHA',lifecycle.sha(ntfs)))
            empty_targets={}
            if all_removed:
                empty=dict(row,esp_guid=E,boot_menu_only=True)
                empty_targets={'disk7s1':empty}
                patches.enter_context(patch('menu_presence.collect',return_value=[proof(i) for i in range(3)]))
            discover=patches.enter_context(patch('menu_presence.empty_menu_targets',return_value=empty_targets))
            def choose(message,choices):
                self.assertTrue(other_disk)
                self.assertEqual(len(choices),2)
                self.assertTrue(choices[0].startswith('disk9s1'))
                self.assertTrue(choices[1].startswith('disk7s1'))
                return 1
            manager.choose=choose
            manager.begin=begin;manager.scan=scan;manager.mount_esp=mount
            error=None
            try:manager.execute()
            except Exception as caught:error=caught
            after=tree_manifest(esp)
            self.assertFalse(manager.formatted)
            if all_removed:self.assertNotIn(lifecycle.CANON,after)
            else:self.assertEqual(after[lifecycle.CANON],before[lifecycle.CANON])
            for name in files:
                if name.startswith(lifecycle.PREFIX+'Private-') and not all_removed:
                    self.assertEqual(after[name],before[name])
            if scenario!='efi-changed':self.assertEqual(after['EFI/OC/OpenCore.efi'],before['EFI/OC/OpenCore.efi'])
            if scenario in ('success','foreign-fallback') or all_removed:
                self.assertIsNone(error,repr(error));self.assertEqual(events,['approve','sync','popup'])
                inspected=lifecycle.inspect(lifecycle.snapshot(esp,after),D,E)
                self.assertEqual(len(inspected['stores']),0 if all_removed else 3)
                if all_removed:
                    self.assertEqual(inspected['catalog']['entries'],[])
                    self.assertIn(lifecycle.MENU,after)
                result=json.loads((session/'rebuild-receipt.json').read_text())
                self.assertEqual(result['status'],'rebuild-complete')
                self.assertFalse(result['partition_formatted'])
                self.assertFalse(result['default_changed'])
                self.assertEqual(after[lifecycle.BASE+'Ntfs4Kn.efi'],lifecycle.NTFS_4KN_SHA)
                if scenario=='foreign-fallback':
                    self.assertEqual(after['EFI/Boot/BOOTX64.EFI'],before['EFI/Boot/BOOTX64.EFI'])
                if other_disk:self.assertEqual(discover.call_args[1]['excluded_esps'],{'disk9s1'})
            else:
                self.assertEqual(error is None,scenario=='dry-run')
                self.assertNotIn('sync',events)
                self.assertNotIn('popup',events)
                for name in before:
                    if name!='EFI/OC/OpenCore.efi':self.assertEqual(after[name],before[name])
                self.assertFalse(any(name in after for name in lifecycle.ALIASES))

    def test_rebuild_without_any_partition_write(self):self.fixture('success')

    def test_rebuild_preserves_opencore_owned_fallback(self):self.fixture('foreign-fallback')

    def test_rebuild_after_all_windows_were_removed_externally(self):self.fixture('all-externally-removed')

    def test_empty_menu_remains_selectable_when_another_disk_still_has_windows(self):
        self.fixture('all-externally-removed-other-disk')

    def test_cancel_preview_and_changed_identity_leave_menu_untouched(self):
        for scenario in ('cancel','dry-run','incomplete','fresh-incomplete','availability-changed','efi-changed'):
            with self.subTest(scenario=scenario):self.fixture(scenario)


if __name__=='__main__':unittest.main()
