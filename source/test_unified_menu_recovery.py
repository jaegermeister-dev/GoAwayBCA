"""Reset/import recovery with fixture EFI files and read-only fake NTFS probes."""
import argparse
import copy
import json
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import backtoseven_menu_actions as actions
import menu_lifecycle as lifecycle
from test_efi_loader_acceptance import application,installed_loader,os_loader
import reset_menu
import rebuild_menu
from install_windows7 import tree_manifest
from installation_records import make_marker
from test_backtoseven_menu import D,E,P,store,child,apply,ROOT as BASE
import test_backtoseven_menu as fixtures

base_child=child
def child(index):
    result=base_child(index)
    data=installed_loader(index)
    result['_files'][result['loader_path']]=data
    result['loader_sha256']=lifecycle.sha(data)
    result['public_loader_hashes']=[lifecycle.sha(data)]
    return result


def creation_record():
    # Synthetic committed record; no private payload or installed disk is used.
    return dict(schema=1, creator='WinDeploy', state='committed',
        installation_uuid='bbbbbbbb-2222-4333-8444-111111111111',
        disk_guid=D, partition_guid=P[0], esp_guid=E,
        partition_bytes=293000000*512, partition_offset=500004096*512,
        family='Windows 7', edition='Windows 7 Ultimate', image_index=1,
        wim_sha256='a'*64, efi_assets=[])


class Recovery(unittest.TestCase):
    def test_paired_sector_route_survives_missing_menu_for_both_graphics_routes(self):
        import win7_sector as sector
        for gpu in (False,True):
            with self.subTest(gpu=gpu),tempfile.TemporaryDirectory() as tmp,patch.object(sector,'HELPER_SHA',lifecycle.sha(b'sector helper')):
                root=Path(tmp);owner,rows,content=self.fixture(root,[0])
                prefix=sector.paths(P[0])[0].rsplit('/',1)[0]+'/'
                loader='EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi' if gpu else prefix+'bootmgfw.efi'
                profile=dict(schema=1,profile=sector.PROFILE,logical_sector_bytes=4096,
                    kernel_sha256=sector.KERNEL_SHA,helper_sha256=sector.HELPER_SHA)
                boot_files,path,route=sector.wrap({loader:b'fixture helper' if gpu else installed_loader(0)},
                    P[0],loader,profile,b'sector helper')
                record=creation_record()
                record.update(disk_guid=D,partition_guid=P[0],esp_guid=E,
                    confirmed_model='MacBookPro11,5' if gpu else 'MacBookPro11,4',
                    sector_compatibility=route,efi_assets=[dict(partition_guid=E,path=k,
                        sha256=lifecycle.sha(v),exclusive=True) for k,v in boot_files.items()])
                rows[0].update(record=record,marker=make_marker(record))
                files=dict(boot_files,**{lifecycle.CANON:store(0)})
                plan=actions.assemble(owner,root,files,D,E,rows)
                final=apply(files,plan);saved=lifecycle.inspect(final,D,E)['catalog']['entries'][0]
                self.assertEqual(saved['loader_path'],path)
                self.assertEqual(saved['loader_sha256'],sector.HELPER_SHA)
                for name,data in boot_files.items():self.assertEqual(final[name],data)
                # Rebuilding again with an existing catalog must keep the same route.
                again=actions.assemble(owner,root,final,D,E,rows)
                self.assertEqual(again['catalog']['entries'][0]['loader_path'],path)

    def test_windows10_addition_imports_win7_11_1_manager_and_windows81(self):
        for version in ((11,1,7601,26022),(99,42,1,2),None):
            with self.subTest(version=version),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);owner,rows,content=self.fixture(root,[0,1])
                content['disk77s2']['/Windows/Boot/EFI/bootmgfw.efi']=application(version)
                files=apply({},fixtures.ReconcileTests().plan({},[0,1]))
                ntfs=root/'ten';loader=ntfs/'EFI/Microsoft/Boot/bootmgfw.efi'
                loader.parent.mkdir(parents=True);loader.write_bytes(installed_loader(2))
                new=dict(child(2),boot_partition_guid=P[2],requires_ntfs=True,hash_locked=False,
                    loader_path='EFI/Microsoft/Boot/bootmgfw.efi',bcd_path='EFI/Microsoft/Boot/BCD',
                    loader_sha256=lifecycle.sha(loader.read_bytes()))
                addition=dict(entry=new,root=ntfs,esp_files={},canonical_bcd=store(2))
                plan=actions.assemble(owner,root,files,D,E,rows,addition=addition,preserve_addition_paths=True)
                actions.verify_sources(owner,plan['windows_sources'])
                final=lifecycle.inspect(apply(files,plan),D,E)
                self.assertEqual({e['partition_guid'] for e in final['catalog']['entries']},set(P))
                self.assertEqual(plan['windows_sources'][0]['windows_version'][:2],(6,1))
                self.assertEqual(plan['windows_sources'][0]['boot_manager_version'],version)

    def test_missing_menu_uses_os_version_not_boot_manager_version_for_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner,rows,content=self.fixture(root,[0])
            plan=actions.assemble(owner,root,{lifecycle.CANON:store(0)},D,E,rows)
            self.assertEqual(plan['catalog']['entries'][0]['title'],'Windows 7')

    def fixture(self,root,indices):
        resources=root/'resources'
        (resources/'efi/accepted-win7').mkdir(parents=True)
        for name in ('Windows.efi','OpenNtfsDxe.efi','Ntfs4Kn.efi'):
            (resources/'efi'/name).write_bytes((BASE/'efi'/name).read_bytes())
        (resources/'efi/accepted-win7/WIN7AUTO.efi').write_bytes(b'fixture helper')
        (resources/'efi/accepted-win7/ALLOW-AUTO.txt').write_bytes(b'fixture permission')
        owner=argparse.Namespace(session=root,ntfscat='fixture-cat',confirmed_model='MacBookPro11,5')
        rows=[];content={}
        for i in indices:
            part=dict(DeviceIdentifier='disk77s'+str(i+2),DiskUUID=P[i])
            rows.append(dict(target=dict(partition=part,esp='disk77s1'),observed=child(i),
                             esp_guid=E,title=child(i)['title']))
            content[part['DeviceIdentifier']]={
                '/Windows/Boot/EFI/bootmgfw.efi':installed_loader(i),
                '/Windows/System32/winload.efi':os_loader(i),
                '/EFI/Microsoft/Boot/BCD':store(i)}
        class Probe:
            def __init__(self,part,tool):self.device=part['DeviceIdentifier']
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,path,limit):
                try:return content[self.device][path]
                except KeyError:raise OSError('fixture file absent')
        def isolate(data,disk,esp,win_disk,win,*args):
            lifecycle.verify_bcd(data,disk,esp,win_disk,win)
            return data
        patches=ExitStack()
        patches.enter_context(patch.object(fixtures,'child',side_effect=child))
        patches.enter_context(patch.object(actions,'ROOT',resources))
        patches.enter_context(patch.object(actions,'Probe',Probe))
        patches.enter_context(patch.object(actions.bcd_isolation,'isolate',side_effect=isolate))
        patches.enter_context(patch.object(actions.gpu,'HELPER_SHA',lifecycle.sha(b'fixture helper')))
        patches.enter_context(patch.object(actions.gpu,'MARKER_SHA',lifecycle.sha(b'fixture permission')))
        patches.enter_context(patch.object(actions.gpu,'ORIGINAL_SHA',lifecycle.sha(installed_loader(0))))
        self.addCleanup(patches.close)
        return owner,rows,content

    def test_reset_then_recover_all_three_installations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner,rows,content=self.fixture(root,[0,1,2])
            files=apply({},fixtures.ReconcileTests().plan({},[0,1,2]))
            files['EFI/OC/OpenCore.efi']=b'OpenCore must survive'
            files['EFI/Boot/BOOTX64.EFI']=b'foreign fallback must survive'
            manifest={p:lifecycle.sha(v) for p,v in files.items()}
            reset=reset_menu.plan(files,manifest,D,E)
            files=apply(files,reset)
            self.assertNotIn(lifecycle.CATALOG,files)
            before=dict(files)
            plan=actions.assemble(owner,root,files,D,E,rows)
            actions.verify_sources(owner,plan['windows_sources'])
            result=apply(files,plan)
            actual=lifecycle.inspect(result,D,E)
            self.assertEqual({e['partition_guid'] for e in actual['catalog']['entries']},set(P))
            for protected in ('EFI/OC/OpenCore.efi','EFI/Boot/BOOTX64.EFI'):
                self.assertEqual(result[protected],before[protected])
            self.assertEqual(files,before)

    def test_new_windows10_imports_standalone_windows7_and_keeps_ntfs_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner,rows,content=self.fixture(root,[0])
            files={lifecycle.CANON:store(0),'EFI/Microsoft/Boot/bootmgfw.efi':installed_loader(0),
                   'EFI/OC/config.plist':b'unchanged'}
            ntfs=root/'new-ntfs';loader=ntfs/'EFI/Microsoft/Boot/bootmgfw.efi'
            loader.parent.mkdir(parents=True);loader.write_bytes(b'new Windows 10 loader')
            new=dict(child(2),boot_partition_guid=P[2],requires_ntfs=True,hash_locked=False,
                     loader_path='EFI/Microsoft/Boot/bootmgfw.efi',bcd_path='EFI/Microsoft/Boot/BCD',
                     loader_sha256=lifecycle.sha(loader.read_bytes()))
            addition=dict(entry=new,root=ntfs,esp_files={},canonical_bcd=store(2))
            plan=actions.assemble(owner,root,files,D,E,rows,addition=addition,preserve_addition_paths=True)
            final=apply(files,plan);actual=lifecycle.inspect(final,D,E)
            self.assertEqual({e['partition_guid'] for e in actual['catalog']['entries']},{P[0],P[2]})
            ten=next(e for e in actual['catalog']['entries'] if e['partition_guid']==P[2])
            for field in ('boot_partition_guid','requires_ntfs','hash_locked','loader_path','bcd_path'):
                self.assertEqual(ten[field],new[field])
            self.assertEqual(final['EFI/OC/config.plist'],b'unchanged')

    def test_older_owned_helper_record_survives_missing_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner,rows,content=self.fixture(root,[0])
            record=creation_record()
            record.update(disk_guid=D,partition_guid=P[0],esp_guid=E,
                efi_assets=[dict(partition_guid=E,path='EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi',
                    sha256=actions.gpu.HELPER_SHA,exclusive=True)])
            rows[0].update(record=record,marker=make_marker(record))
            files={lifecycle.CANON:store(0),'EFI/Microsoft/Boot/bootmgfw.efi':b'fixture helper'}
            plan=actions.assemble(owner,root,files,D,E,rows)
            entry=plan['catalog']['entries'][0]
            self.assertEqual(entry['loader_path'],'EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi')
            self.assertEqual(entry['loader_sha256'],actions.gpu.HELPER_SHA)

    def test_changed_installed_boot_file_refuses_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner,rows,content=self.fixture(root,[0])
            files={lifecycle.CANON:store(0)}
            plan=actions.assemble(owner,root,files,D,E,rows)
            content['disk77s2']['/Windows/Boot/EFI/bootmgfw.efi']=b'changed'
            with self.assertRaisesRegex(Exception,'loader changed'):
                actions.verify_sources(owner,plan['windows_sources'])

    def test_existing_ntfs_route_loader_is_also_rechecked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();owner,rows,content=self.fixture(root,[2])
            loader_path='EFI/Microsoft/Boot/bootmgfw.efi'
            ntfs_child=dict(child(2),boot_partition_guid=P[2],requires_ntfs=True,hash_locked=False,
                            loader_path=loader_path,bcd_path='EFI/Microsoft/Boot/BCD',_files={})
            content['disk77s4']['/'+loader_path]=installed_loader(2)
            initial=actions.menu.reconcile({},D,E,rows,{lifecycle.identity(ntfs_child):ntfs_child},
                {lifecycle.identity(ntfs_child):store(2)},
                (BASE/'efi/Windows.efi').read_bytes(),(BASE/'efi/OpenNtfsDxe.efi').read_bytes())
            files=apply({},initial)
            plan=actions.assemble(owner,root,files,D,E,rows)
            actions.verify_sources(owner,plan['windows_sources'])
            content['disk77s4']['/'+loader_path]=b'changed route loader'
            with self.assertRaisesRegex(Exception,'menu loader changed'):
                actions.verify_sources(owner,plan['windows_sources'])

    def test_broader_windows7_preparation_keeps_a_store_for_reset_recovery(self):
        import install_multiboot as installer
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as patches:
            root=Path(tmp).resolve();owner,rows,content=self.fixture(root,[0])
            stage=root/'stage';(stage/'Microsoft/Boot').mkdir(parents=True)
            (stage/'Microsoft/Boot/bootmgfw.efi').write_bytes(installed_loader(0))
            (stage/'Microsoft/Boot/BCD').write_bytes(store(0))
            owner.wim='fixture-wim';owner.extract_boot=lambda *args:stage
            owner.prepare_accepted_additions=lambda *args:None
            def capture(argv):
                self.assertEqual(argv[:2],['fixture-wim','capture'])
                Path(argv[3]).write_bytes(b'fixture boot overlay')
                return b''
            patches.enter_context(patch.object(installer,'run',side_effect=capture))
            addition=installer.MultiBootInstaller.prepare_win7(owner,root/'fixture.wim',
                dict(family='Windows 7',index=1,name='Windows 7 Professional'),
                dict(guid=D),dict(guid=P[0],first=2048,last=2048+64*1024**2-1),dict(guid=E),'0'*64,
                'MacBookPro11,3',False)
            retained='EFI/WinDeploy/Installs/'+P[0]+'/BCD'
            self.assertEqual(addition['esp_files'][retained],store(0))
            self.assertTrue(any(a['path']==retained and a['exclusive'] is True
                                for a in addition['record']['efi_assets']))
            files=dict(addition['esp_files'])
            installed=lifecycle.transition(files,{p:lifecycle.sha(v) for p,v in files.items()},D,E,[],
                addition=addition['entry'],new_store=addition['canonical_bcd'])
            files=apply(files,installed)
            reset=reset_menu.plan(files,{p:lifecycle.sha(v) for p,v in files.items()},D,E)
            files=apply(files,reset)
            self.assertEqual(files[retained],store(0))
            content['disk77s2'].pop('/EFI/Microsoft/Boot/BCD')
            rebuilt=actions.assemble(owner,root,files,D,E,rows)
            self.assertEqual(rebuilt['catalog']['entries'][0]['partition_guid'],P[0])

    def test_missing_catalog_executor_requires_approval_and_stable_sources(self):
        for scenario in ('success','cancel','changed-source','import-new','repair-serviced',
                         'unknown-public','bad-metadata'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as tmp,ExitStack() as patches:
                root=Path(tmp).resolve();owner,rows,content=self.fixture(root,[0,2])
                esp=root/'esp';(esp/'EFI/OC').mkdir(parents=True)
                (esp/'EFI/OC/OpenCore.efi').write_bytes(b'preserve OpenCore')
                existing=scenario in ('import-new','repair-serviced','unknown-public','bad-metadata')
                if existing:
                    files=apply({},fixtures.ReconcileTests().plan({},[0] if scenario=='import-new' else [0,2]))
                    renamed=lifecycle.transition(files,{p:lifecycle.sha(v) for p,v in files.items()},D,E,
                        [dict(observed=child(i)) for i in (0,2)],
                        rename=(lifecycle.identity(child(0)),'Legacy games'))
                    files=apply(files,renamed)
                    files['EFI/Boot/BOOTX64.EFI']=b'preserve foreign fallback'
                    if scenario in ('import-new','repair-serviced'):
                        files[lifecycle.CANON]=store(2)
                        files['EFI/Microsoft/Boot/bootmgfw.efi']=installed_loader(2)
                    if scenario=='unknown-public':files['EFI/Microsoft/Boot/bootmgfw.efi']=b'unknown loader'
                    if scenario=='bad-metadata':files[lifecycle.CONFIG]=b'corrupt saved configuration'
                    for name,data in files.items():
                        path=esp/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
                before=tree_manifest(esp)
                for row in rows:row['efi_manifest']=before;row['summary']=row['title']
                scan=dict(scan_complete=True,installations=rows)
                events=[];access=[True]
                class UI:
                    def log(self,text):pass
                    def request(self,kind,text,**fields):
                        if kind=='entry_name':
                            self_test.assertIn('Partition: disk77s',text)
                            self_test.assertIn('not verified',text)
                            return fields['name']
                        self_test.assertEqual(kind,'notice')
                        self_test.assertEqual(fields,dict(button_title='Done'))
                        self_test.assertIn('boot menu has been rebuilt',text)
                        events.append('popup')
                    def approve(self,kind,text):
                        self_test.assertEqual(kind,'rebuild_review')
                        self_test.assertIn('reconciled' if existing else 'absent menu catalog',text)
                        events.append('approval')
                        if scenario=='changed-source':
                            content['disk77s2']['/Windows/Boot/EFI/bootmgfw.efi']=b'changed'
                        return scenario!='cancel'
                self_test=self
                manager=rebuild_menu.RebuildMenu(argparse.Namespace(dry_run=False),UI())
                manager.session=root/'session';manager.session.mkdir();manager.ntfscat='fixture-cat'
                manager.begin=lambda:None
                manager.scan=lambda:copy.deepcopy(scan)
                def mount(device,readonly=True):access[0]=readonly;return esp
                manager.mount_esp=mount
                patches.enter_context(patch('menu_presence.empty_menu_targets',return_value={}))
                manager.choose=lambda text,options:0
                patches.enter_context(patch('menu_drive_review.choices',return_value=['Fixture drive menu']))
                patches.enter_context(patch.object(rebuild_menu,'ROOT',root/'resources'))
                patches.enter_context(patch.object(rebuild_menu,'gpt_for',return_value=dict(guid=D,digest='layout')))
                patches.enter_context(patch.object(rebuild_menu,'match_gpt',return_value=({},dict(guid=E))))
                patches.enter_context(patch.object(rebuild_menu,'info',side_effect=lambda _:dict(
                    DiskUUID=E,MountPoint=str(esp),ReadOnlyVolume=access[0])))
                patches.enter_context(patch.object(rebuild_menu,'run',side_effect=lambda _:events.append('sync')))
                if scenario in ('success','import-new','repair-serviced'):
                    manager.execute()
                    result=json.loads((manager.session/'rebuild-receipt.json').read_text())
                    self.assertEqual(result['recovered_missing_catalog'],not existing)
                    self.assertEqual(result['status'],'rebuild-complete')
                    self.assertTrue((esp/lifecycle.CATALOG).is_file())
                    self.assertEqual(events,['approval','sync','popup'])
                    final=actions.read_efi(esp,tree_manifest(esp))
                    entries=lifecycle.inspect(final,D,E)['catalog']['entries']
                    self.assertEqual({e['partition_guid'] for e in entries},{P[0],P[2]})
                    if existing:
                        self.assertEqual(next(e['title'] for e in entries if e['partition_guid']==P[0]),'Legacy games')
                        self.assertEqual((esp/'EFI/Boot/BOOTX64.EFI').read_bytes(),b'preserve foreign fallback')
                        self.assertTrue(result['reconciled_public_loader'])
                        self.assertEqual(result['added_installations'],['Windows 10'] if scenario=='import-new' else [])
                else:
                    with self.assertRaises(Exception):manager.execute()
                    self.assertEqual(tree_manifest(esp),before)
                    self.assertNotIn('popup',events)
                self.assertFalse(manager.formatted)
                self.assertEqual((esp/'EFI/OC/OpenCore.efi').read_bytes(),b'preserve OpenCore')


if __name__=='__main__':unittest.main()
