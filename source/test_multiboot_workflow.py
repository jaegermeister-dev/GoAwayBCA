"""New installer orchestration; every physical device/command is faked."""
import argparse
from contextlib import ExitStack
import copy
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import install_multiboot as app
import install_windows7 as base
from test_menu_lifecycle import D,E,PARTS

class Workflow(unittest.TestCase):
    def fixture(self,scenario,family='Windows 10',model='MacBookPro11,5',efi=False,unified=False,standard=False):
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp).resolve();media=root/'media';(media/'sources').mkdir(parents=True)
            wim=media/'sources/install.wim';wim.write_bytes(b'WIM');iso=root/'x.iso';iso.write_bytes(b'ISO')
            esp_root=root/'esp';(esp_root/'EFI/OC').mkdir(parents=True)
            (esp_root/'EFI/OC/OpenCore.efi').write_bytes(b'keep OC')
            if scenario in ('shared-diagnostic','critical-dependency'):
                old=esp_root/'EFI/Microsoft/Boot/memtest.efi';old.parent.mkdir(parents=True)
                old.write_bytes(b'existing diagnostic')
                if scenario=='critical-dependency':
                    (old.parent/'bootmgfw-w7dep-original.efi').write_bytes(b'conflicting loader')
            for name in ('Windows.efi','OpenNtfsDxe.efi','Ntfs4Kn.efi'):
                (root/'efi').mkdir(exist_ok=True);(root/'efi'/name).write_bytes(b'fake EFI')
            session=root/'session';session.mkdir();before=base.tree_manifest(esp_root)
            p=dict(DeviceIdentifier='disk99s3',DiskUUID=PARTS[0],TotalSize=100000000000,IOKitSize=100000000000,
                Size=100000000000,FilesystemType='exfat',VolumeName='TEST',FreeSpace=90000000000)
            disk=dict(DeviceIdentifier='disk99',MediaName='fake disk',DeviceBlockSize=512,Internal=True)
            target=dict(partition=p,disk=disk,esp='disk99s1')
            gpt=dict(guid=D,digest='layout');win=dict(guid=PARTS[0],first=2048,last=2048+195312500-1);esp=dict(guid=E)
            mounted=True;readonly=True;formatted=False;scans=0;events=[];approvals=[];firmware_reads=0
            class UI:
                def log(self,s):pass
                def choose(self,q,options):
                    if 'Proceed without downloading drivers' in options:
                        events.append('older-windows-driver-consent')
                        return options.index('Proceed without downloading drivers')
                    if options==['Choose another image / cancel','Try anyway']:
                        events.append('cpu-warning')
                        return 1 if scenario=='cpu-acknowledge' else 0
                    if options==['Cancel and review the Windows image','Continue with pure EFI']:
                        events.append('compatibility-warning')
                        return 0 if scenario=='compatibility-cancel' else 1
                    if q.startswith('Choose the destination partition'):
                        assert q==app.destination_prompt(family)
                        events.append('destination-guidance')
                    return 0
                def request(self,kind,text,**kw):
                    if kind=='driver_model':return model
                    if kind=='firmware_block':
                        events.append('firmware-block');return True  # Cannot forge permission to continue.
                    return kw.get('default',kw.get('title','My Windows'))
                def efi_choice(self,text,default_enabled=True):events.append('efi-choice');return efi
                def keyboard_choice(self,text,default_enabled=True):events.append('keyboard-choice');return True
                def approve(self,kind,text):
                    if kind=='compatibility_review':
                        events.append('compatibility-warning')
                        return scenario!='compatibility-cancel'
                    assert all(s in text for s in ('Label:','Capacity:','Free space:'))
                    approvals.append(kind);return scenario!='cancel-'+kind
                def confirm(self,text,phrase):
                    assert phrase=='confirm' and approvals==['format_ready','review']
                    approvals.append('confirm')
                    if scenario=='source-changed':wim.write_bytes(b'changed')
                    if scenario=='efi-changed':(esp_root/'EFI/OC/OpenCore.efi').write_bytes(b'other actor')
                    if scenario=='driver-changed':(session/'drivers.wim').write_bytes(b'changed')
                    return scenario!='cancel-confirm'
            args=argparse.Namespace(iso=str(iso),boot_manager=None,prepared_media=None,work_dir=str(session),
                tools_dir=str(root/'tools'),dry_run=scenario=='dry-run',win7_accepted_extras=True)
            engine=app.MultiBootInstaller(args,UI())
            engine.unified_menu=unified
            def begin():engine.session=session;engine.wim='wim';engine.mkntfs='mkntfs';engine.ntfscat='cat'
            def mount_esp(device,readonly):
                nonlocal access
                access=readonly;return esp_root
            access=True
            def info(device):
                if device=='disk99':return disk
                if device=='disk99s1':return dict(DiskUUID=E,MountPoint=str(esp_root),ReadOnlyVolume=access)
                assert device=='disk99s3'
                return dict(p,MountPoint='/fake' if mounted else '',FilesystemType='ntfs' if formatted else 'exfat')
            def scan():
                nonlocal scans
                scans+=1
                occupied=scenario=='occupied' or (scenario=='occupied-late' and scans>1)
                return dict(scan_complete=scenario!='incomplete',installations=[dict(target=target)] if occupied else [])
            def unmount(t,note):
                nonlocal mounted
                mounted=False
            def stage(*a):
                events.append('stage')
                overlay=session/'overlay.wim';overlay.write_bytes(b'overlay')
                record=dict(disk_guid=D,partition_guid=PARTS[0],esp_guid=E,family=family,
                    efi_assets=[dict(path='EFI/Microsoft/Boot/bootmgfw.efi',sha256='a'*64)])
                dependencies={}
                if scenario in ('shared-diagnostic','critical-dependency'):
                    dependencies['EFI/Microsoft/Boot/memtest.efi']=b'ISO diagnostic'
                    dependencies['EFI/Microsoft/Boot/bootmgfw-w7dep-original.efi']=b'required loader'
                boot=session/'overlay/EFI/Microsoft/Boot';boot.mkdir(parents=True,exist_ok=True)
                (boot/'bootmgfw.efi').write_bytes(b'synthetic Microsoft loader')
                return dict(root=session/'overlay',wim=overlay,wim_sha256=base.sha256(overlay),files={'marker':base.sha256(overlay)},
                    record=record,canonical_bcd=b'BCD',esp_files=dependencies,entry=app.stock_entry(record))
            def plan(*a,**kw):
                assert kw['addition']['title']=='My Windows'
                return dict(changes=[dict(path='EFI/WinDeploy/menu.bin',expected=None,data=b'new menu')],catalog={})
            def prepare_drivers(*a):
                events.append('drivers-prepared');f=session/'drivers.wim';f.write_bytes(b'drivers')
                return dict(wim=f,wim_sha256=base.sha256(f),bytes=7,receipt={})
            def firmware():
                nonlocal firmware_reads
                firmware_reads+=1;events.append('firmware-read')
                if scenario=='firmware-unknown':return app.firmware_compatibility.unknown('fixture')
                bits=32 if scenario=='firmware32' or (scenario=='firmware-changed' and firmware_reads>1) else 64
                return dict(status='verified',bits=bits,abi='EFI'+str(bits),source='fixture',reason='')
            def cpu():
                events.append('cpu-guidance')
                result=app.cpu_compatibility.unknown('fixture')
                result['status']='observed'
                result['features']={name:True for name in app.cpu_compatibility.FEATURES}
                if scenario in ('cpu-cancel','cpu-acknowledge'):result['features']['CMPXCHG16B']=False
                if scenario=='cpu-unknown':return app.cpu_compatibility.unknown('fixture timed out')
                return result
            def driver_apply(*a,**kw):events.append('driver-apply');a[-2]()
            def run(argv):
                argv=[str(v) for v in argv]
                if argv==['/usr/sbin/sysctl','-n','hw.model']:return model.encode()
                if argv==['/bin/sync']:return b''
                if argv[:2]==['wim','info']:
                    Path(argv[-1].split('=',1)[1]).write_bytes(b'fake xml');return b''
                if argv[:2]==['wim','extract']:return b'original Windows file'
                if argv[0]=='cat':
                    if scenario=='bad-image':return b'bad'
                    if argv[-1]=='/marker':return b'bad' if scenario=='bad-overlay' else b'overlay'
                    return b'original Windows file'
                raise AssertionError('Unmocked command '+str(argv))
            def logged(argv,note):
                nonlocal formatted,mounted
                argv=[str(v) for v in argv]
                if argv[:2]==['wim','verify']:return
                assert approvals==['format_ready','review','confirm']
                if argv[0]=='mkntfs':
                    assert argv[-2]=='/dev/disk99s3' and '-F' not in argv
                    assert argv[argv.index('-L')+1]==family.replace(' ','-')
                    formatted=True;events.append('format')
                    if scenario=='auto-mounted':mounted=True
                else:
                    assert argv[:2]==['wim','apply'] and argv[4]=='/dev/disk99s3';events.append('apply')
            original_stat=app.os.stat
            def fake_stat(path,*a,**kw):
                if str(path)=='/dev/disk99s3':return argparse.Namespace(st_mode=stat.S_IFBLK)
                return original_stat(path,*a,**kw)
            stack.enter_context(patch.object(app,'ROOT',root))
            if standard:
                stack.enter_context(patch('standard_boot.choose',return_value=False))
                stack.enter_context(patch('standard_boot.validate_efi_application'))
            # Native picker and fresh device probing have their own fixtures.
            # Preserve this workflow's explicit destination-selection event.
            stack.enter_context(patch('destination_picker.choose',side_effect=lambda owner,targets,family:
                targets[owner.choose(app.destination_prompt(family),['Synthetic destination'])]))
            stack.enter_context(patch.object(app.platform,'system',return_value='Darwin'))
            stack.enter_context(patch.object(app.platform,'machine',return_value='x86_64'))
            stack.enter_context(patch.object(app.os,'geteuid',return_value=0,create=True))
            stack.enter_context(patch.object(app.os,'stat',side_effect=fake_stat))
            stack.enter_context(patch('installation_destination.check',side_effect=lambda *a:events.append('root-windows-guard')))
            # Actual transport selection has separate NVMe policy tests; this
            # synthetic disk99 workflow must never query the host hardware.
            stack.enter_context(patch('win8_nvme.prepare',return_value=None))
            for name,value in dict(run=run,run_phase=lambda argv,note,label:logged(argv,note),info=info,inventory=lambda:[target],gpt_for=lambda t:gpt,
                match_gpt=lambda t,g:(win,esp),require_sources_off_target=lambda p,d:None,
                assert_same_target=lambda t,p,d:None,assert_postformat_target=lambda t,p,d:None,
                ensure_target_unmounted=unmount,prompt_entry_name=lambda *a:'My Windows',
                inspect_editions=lambda data:[dict(index=1,name='Test',family=family,architecture=9,version=(10,0,26100),bytes=1000000000)]).items():
                stack.enter_context(patch.object(app,name,side_effect=value))
            stack.enter_context(patch.object(app.firmware_compatibility,'read_firmware',side_effect=firmware))
            stack.enter_context(patch.object(app.cpu_compatibility,'read_cpu',side_effect=cpu))
            stack.enter_context(patch.object(app.lifecycle,'inspect',return_value=dict(catalog={'entries':[]})))
            stack.enter_context(patch.object(app.lifecycle,'transition',side_effect=plan))
            if unified:
                import backtoseven_menu_actions as menu_actions
                def unified_plan(owner,backup,files,disk,esp,rows,*,addition,preserve_addition_paths,confirmed_absent):
                    assert confirmed_absent==[]
                    assert owner is engine and preserve_addition_paths is True
                    assert disk==D and esp==E and rows==[]
                    events.append('unified-menu-plan')
                    result=plan(addition=addition['entry'])
                    result['windows_sources']=[]
                    return result
                def verify_sources(owner,sources):
                    assert owner is engine and sources==[]
                    events.append('existing-loaders-rechecked')
                    if scenario=='imported-loader-changed':
                        raise base.Refusal('Existing Windows loader changed during review')
                stack.enter_context(patch.object(menu_actions,'assemble',side_effect=unified_plan))
                stack.enter_context(patch.object(menu_actions,'verify_sources',side_effect=verify_sources))
            stack.enter_context(patch.object(app.windows10_boot,'stage',side_effect=stage))
            stack.enter_context(patch.object(app.experimental_efi,'wrap_stock',side_effect=lambda *a:events.append('experimental-wrapper')))
            stack.enter_context(patch.object(app.win7_iso_source,'prepare',return_value=(wim,{})))
            stack.enter_context(patch.object(app.driver_preparation,'configure',side_effect=lambda *a:dict(source='test')))
            stack.enter_context(patch.object(app.driver_preparation,'prepare',side_effect=prepare_drivers))
            stack.enter_context(patch.object(app.driver_preparation,'apply',side_effect=driver_apply))
            stack.enter_context(patch('optional_verification.verify_wim',return_value={'status':'verified'}))
            engine.begin=begin;engine.mount_iso=lambda i:media;engine.mount_esp=mount_esp;engine.scan=scan
            engine.smoke_tools=lambda:None;engine.prepare_win7=lambda *a:stage();engine.apply_accepted_additions=lambda *a:None
            caught=None
            try:engine.execute()
            except Exception as e:caught=e
            after=base.tree_manifest(esp_root)
            if scenario!='efi-changed':self.assertEqual(before['EFI/OC/OpenCore.efi'],after['EFI/OC/OpenCore.efi'])
            if scenario in ('shared-diagnostic','critical-dependency'):
                self.assertEqual(before['EFI/Microsoft/Boot/memtest.efi'],after['EFI/Microsoft/Boot/memtest.efi'])
                if scenario=='shared-diagnostic':
                    import json
                    receipt=json.loads((session/'receipt.json').read_text())
                    self.assertEqual(receipt['preserved_boot_dependencies'][0]['path'],'EFI/Microsoft/Boot/memtest.efi')
            if standard:
                self.assertFalse((esp_root/'EFI/WinDeploy/menu.bin').exists())
                published=(esp_root/'EFI/Microsoft/Boot/bootmgfw.efi').exists()
                if published:
                    self.assertEqual((esp_root/'EFI/Microsoft/Boot/bootmgfw.efi').read_bytes(),b'synthetic Microsoft loader')
                    self.assertEqual((esp_root/'EFI/Microsoft/Boot/BCD').read_bytes(),b'BCD')
            else:published=(esp_root/'EFI/WinDeploy/menu.bin').exists()
            return caught,formatted,published,approvals,events

    def test_success_all_families_only_after_confirmations(self):
        for family in app.FAMILIES:
            error,formatted,published,approvals,events=self.fixture('success',family)
            self.assertIsNone(error,repr(error));self.assertTrue(formatted and published)
            self.assertEqual(approvals,['format_ready','review','confirm'])
            self.assertIn('destination-guidance',events)
            self.assertLess(events.index('drivers-prepared'),events.index('format'))
            self.assertLess(events.index('firmware-read'),events.index('cpu-guidance'))
            self.assertLess(events.index('cpu-guidance'),events.index('drivers-prepared'))
            self.assertEqual(events.count('firmware-read'),2)
            if family in ('Windows 8','Windows 8.1','Windows 10'):
                self.assertNotIn('efi-choice',events);self.assertNotIn('experimental-wrapper',events)
            if family=='Windows 11':
                self.assertIn('efi-choice',events);self.assertNotIn('experimental-wrapper',events)

    def test_default_volume_labels_are_not_menu_names(self):
        for family,label in (('Windows 7','Windows-7'),('Windows 8.1','Windows-8.1'),('Windows 10','Windows-10')):
            self.assertEqual(app.volume_label(family),label)
        with self.assertRaises(base.Refusal):app.volume_label('My custom Windows')

    def test_unified_menu_import_is_rechecked_before_format_and_cancel_still_stops(self):
        for scenario in ('success','cancel-confirm','imported-loader-changed','bad-overlay'):
            with self.subTest(scenario=scenario):
                error,formatted,published,approvals,events=self.fixture(scenario,unified=True)
                self.assertIn('unified-menu-plan',events)
                if scenario=='success':
                    self.assertIsNone(error,repr(error))
                    self.assertTrue(formatted and published)
                    self.assertLess(events.index('existing-loaders-rechecked'),events.index('format'))
                else:
                    self.assertIsNotNone(error)
                    self.assertFalse(published)
                    self.assertEqual(formatted,scenario=='bad-overlay')
                if scenario=='cancel-confirm':self.assertNotIn('existing-loaders-rechecked',events)

    def test_partition_prompt_names_selected_windows_and_both_supported_filesystems(self):
        for family in app.FAMILIES:
            text=app.destination_prompt(family)
            self.assertIn("If you don't see the partition you intend to install "+family+' to',text)
            self.assertIn("make sure it's formatted as exFAT or NTFS.",text)
            self.assertIn('Partitions that already contain Windows are not listed.',text)
            self.assertIn('Manage Installations',text)
        with self.assertRaises(base.Refusal):app.destination_prompt('Untrusted menu label')

    def test_experimental_stock_choice_is_real_and_optional(self):
        for model in ('MacBookPro10,1','MacBookPro9,1','MacBookPro11,3','MacBookPro11,4'):
            for enabled in (False,True):
                error,_,_,_,events=self.fixture('success','Windows 8.1',model,enabled)
                self.assertIsNone(error,repr(error))
                selectable=model not in ('MacBookPro11,3','MacBookPro11,4')
                self.assertEqual('efi-choice' in events,selectable)
                self.assertEqual('experimental-wrapper' in events,enabled and selectable)

    def test_cancel_conflict_and_driver_mutation_never_format(self):
        for scenario in ('cancel-format_ready','cancel-review','cancel-confirm','source-changed','driver-changed',
                         'efi-changed','incomplete','occupied','occupied-late'):
            with self.subTest(scenario=scenario):
                error,formatted,published,_,_=self.fixture(scenario)
                self.assertIsNotNone(error);self.assertFalse(formatted or published)

    def test_readback_failure_never_publishes(self):
        for scenario in ('auto-mounted','bad-image','bad-overlay'):
            error,formatted,published,_,_=self.fixture(scenario)
            self.assertIsNotNone(error);self.assertTrue(formatted);self.assertFalse(published)

    def test_preview_never_formats(self):
        error,formatted,published,approvals,_=self.fixture('dry-run')
        self.assertIsNone(error,repr(error));self.assertFalse(formatted or published or approvals)

    def test_win7_after_reset_keeps_shared_diagnostic_and_continues(self):
        error,formatted,published,approvals,_=self.fixture('shared-diagnostic','Windows 7')
        self.assertIsNone(error,repr(error));self.assertTrue(formatted and published)
        self.assertEqual(approvals,['format_ready','review','confirm'])

    def test_conflicting_loader_still_stops_before_any_format_or_confirmation(self):
        error,formatted,published,approvals,_=self.fixture('critical-dependency','Windows 7')
        self.assertIn('bootmgfw-w7dep-original.efi',str(error))
        self.assertFalse(formatted or published or approvals)

    def test_firmware_mismatch_unknown_and_changed_never_format(self):
        for scenario in ('firmware32','firmware-unknown','firmware-changed'):
            with self.subTest(scenario=scenario):
                error,formatted,published,approvals,events=self.fixture(scenario)
                self.assertIsNotNone(error)
                self.assertFalse(formatted or published)
                if scenario!='firmware-changed':
                    self.assertFalse(approvals)
                    self.assertNotIn('drivers-prepared',events)
                    self.assertNotIn('stage',events)
                    self.assertIn('firmware-block',events)

    def test_historical_driver_architecture_warning_can_cancel_before_download(self):
        error,formatted,published,approvals,events=self.fixture('compatibility-cancel','Windows 7','MacBookPro3,1')
        self.assertIn('Mac/Windows compatibility guidance',str(error))
        self.assertFalse(formatted or published or approvals)
        self.assertNotIn('drivers-prepared',events)
        self.assertIn('compatibility-warning',events)

    def test_cpu_missing_or_unknown_cancel_stops_before_driver_download(self):
        for scenario in ('cpu-cancel','cpu-unknown'):
            error,formatted,published,approvals,events=self.fixture(scenario,'Windows 10')
            self.assertIn('CPU compatibility guidance',str(error))
            self.assertFalse(formatted or published or approvals)
            self.assertNotIn('drivers-prepared',events)
            self.assertNotIn('stage',events)

    def test_cpu_warning_try_anyway_still_requires_all_format_confirmations(self):
        error,formatted,published,approvals,events=self.fixture('cpu-acknowledge','Windows 10')
        self.assertIsNone(error,repr(error));self.assertTrue(formatted and published)
        self.assertIn('cpu-warning',events)
        self.assertEqual(approvals,['format_ready','review','confirm'])

    def test_historical_driver_warning_does_not_hard_block_user_attempt(self):
        error,formatted,published,approvals,events=self.fixture('success','Windows 7','MacBookPro3,1')
        self.assertIsNone(error,repr(error))
        self.assertTrue(formatted and published)
        self.assertLess(events.index('compatibility-warning'),events.index('drivers-prepared'))

if __name__=='__main__':unittest.main()
