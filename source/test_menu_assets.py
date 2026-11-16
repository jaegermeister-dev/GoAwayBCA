"""Imported-copy ownership and install/remove/rebuild lifecycle on fake ESPs."""
import json
from pathlib import Path
import tempfile
import unittest

import menu_assets as assets
import menu_lifecycle as lifecycle
from manage_format_plan import plan as format_plan
from installation_records import canonical, make_marker, ownership
from test_backtoseven_menu import D,E,P,child,store,apply
import test_backtoseven_menu as fixtures


class Copies(unittest.TestCase):
    def fixture(self):
        value=child(0)
        receipt=assets.record_copies({},D,E,{lifecycle.identity(value):value})
        return value,dict(value['_files'],**receipt)

    def cleanup(self,files,references=(),unknown=False):
        return assets.cleanup(files,{p:assets.sha(v) for p,v in files.items()},D,E,P[0],references,unknown)

    def test_only_new_copies_are_owned_not_preexisting_or_shared_files(self):
        value=child(0);loader=value['loader_path'];bcd=value['bcd_path']
        value['_files']['EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi']=b'not a private menu copy'
        receipts=assets.record_copies({loader:value['_files'][loader]},D,E,{lifecycle.identity(value):value})
        actual=assets.decode(receipts[assets.receipt_path(P[0])],D,E,P[0])
        self.assertEqual(actual['assets'],{bcd:assets.sha(store(0))})

    def test_application_refresh_updates_only_already_owned_copy(self):
        value,files=self.fixture();value['_files'][value['bcd_path']]=b'new isolated store'
        receipts=assets.record_copies(files,D,E,{lifecycle.identity(value):value})
        record=assets.decode(receipts[assets.receipt_path(P[0])],D,E,P[0])
        self.assertEqual(record['assets'][value['bcd_path']],assets.sha(b'new isolated store'))
        files[value['bcd_path']]=b'changed outside app'
        self.assertEqual(assets.record_copies(files,D,E,{lifecycle.identity(value):value}),{})

    def test_changed_copy_is_retained_with_receipt(self):
        value,files=self.fixture();files[value['loader_path']]=b'changed'
        result=self.cleanup(files)
        self.assertEqual({c['path'] for c in result['changes']},{value['bcd_path']})
        self.assertEqual(set(result['retained']),{value['loader_path'],assets.receipt_path(P[0])})

    def test_shared_or_unknown_neighbour_copies_are_retained(self):
        value,files=self.fixture()
        result=self.cleanup(files,[(E,value['loader_path'])])
        self.assertIn(value['loader_path'],result['retained'])
        self.assertNotIn(value['loader_path'],[c['path'] for c in result['changes']])
        result=self.cleanup(files,unknown=True)
        self.assertEqual(result['changes'],[])
        self.assertEqual(set(result['retained']),set(files))

    def test_corrupt_wrong_disk_or_escaping_receipt_never_authorizes_cleanup(self):
        value,files=self.fixture();path=assets.receipt_path(P[0]);original=json.loads(files[path])
        variants=[b'invalid JSON',canonical(dict(original,disk_guid=P[1])),
                  canonical(dict(original,esp_guid=P[1])),
                  canonical(dict(original,partition_guid=P[1])),
                  canonical(dict(original,assets={'EFI/OC/OpenCore.efi':'a'*64})),
                  canonical(dict(original,assets={child(1)['loader_path']:'a'*64}))]
        for data in variants:
            with self.subTest(data=data):
                modified=dict(files);modified[path]=data
                self.assertEqual(self.cleanup(modified)['changes'],[])
                self.assertEqual(assets.record_copies(modified,D,E,{lifecycle.identity(value):value}),{})

    def test_missing_receipt_never_adopts_existing_files(self):
        value,files=self.fixture();files.pop(assets.receipt_path(P[0]))
        self.assertEqual(self.cleanup(files)['changes'],[])
        self.assertEqual(assets.record_copies(files,D,E,{lifecycle.identity(value):value}),{})

    def test_extra_files_are_reported_not_recursively_removed(self):
        value,files=self.fixture();extra='EFI/BackToSeven/Children/'+P[0]+'/user-backup.efi'
        files[extra]=b'owner file';result=self.cleanup(files)
        self.assertIn(extra,result['retained']);self.assertIn(assets.receipt_path(P[0]),result['retained'])
        self.assertNotIn(extra,[c['path'] for c in result['changes']])

    def test_receipt_hash_and_case_collisions_are_checked(self):
        _,files=self.fixture();manifest={p:assets.sha(v) for p,v in files.items()}
        manifest[assets.receipt_path(P[0])]='a'*64
        self.assertEqual(assets.cleanup(files,manifest,D,E,P[0],())['changes'],[])
        files[assets.receipt_path(P[0]).upper()]=b'collision'
        with self.assertRaisesRegex(ValueError,'Case-colliding'):self.cleanup(files)


class Management(unittest.TestCase):
    def rows(self,files,indices,unowned=()):
        rows=[]
        manifest={p:assets.sha(v) for p,v in files.items()}
        for i in indices:
            record=dict(schema=1,creator='WinDeploy',state='committed',disk_guid=D,
                partition_guid=P[i],esp_guid=E,installation_uuid=P[i],family=child(i)['title'],
                partition_bytes=32*1024**3,partition_offset=(i+1)*1024**2,wim_sha256='a'*64,
                efi_assets=[dict(partition_guid=P[i],path='EFI/Microsoft/Boot/bootmgfw.efi',
                                sha256=assets.sha(('installed loader %d'%i).encode()),exclusive=True)])
            observed=dict(record,filesystem='ntfs',windows_verified=True)
            readbacks={(E,p):v for p,v in manifest.items()}
            readbacks[(P[i],'EFI/Microsoft/Boot/bootmgfw.efi')]=record['efi_assets'][0]['sha256']
            marker=make_marker(record)
            if i in unowned:marker=record=None
            rows.append(dict(observed=observed,record=record,marker=marker,esp_guid=E,
                ownership=ownership(observed,marker,record,readbacks),efi_manifest=manifest,
                asset_readbacks=[dict(partition_guid=d,path=p,sha256=v) for (d,p),v in readbacks.items()]))
        return rows

    def remove(self,files,indices,selected,unowned=()):
        rows=self.rows(files,indices,unowned)
        target=next(r for r in rows if r['observed']['partition_guid']==P[selected])
        return format_plan(target,rows,scan_complete=True,
            catalog_bytes=files.get(lifecycle.CATALOG),menu_bytes=files.get(lifecycle.MENU),
            lifecycle_files=files,unknown_acknowledged=selected in unowned)

    def test_import_then_remove_each_os_and_reinstall_after_last_removal(self):
        # Exercise actual BCD identity checks, active-store switching and menu
        # regeneration. Only ordinary in-memory fake-ESP files are changed.
        for order in ((0,1,2),(2,1,0),(1,0,2)):
            with self.subTest(order=order):
                protected={'EFI/OC/OpenCore.efi':b'preserve OpenCore',
                           'EFI/Boot/BOOTX64.EFI':b'preserve foreign fallback'}
                files=apply(protected,fixtures.ReconcileTests().plan(protected,[0,1,2]))
                indices=[0,1,2]
                for i in order:
                    before=dict(files);plan=self.remove(files,indices,i)
                    self.assertTrue(plan['apply_only_after_verified_format'])
                    self.assertEqual(files,before)
                    files=apply(files,plan);indices.remove(i)
                    state=lifecycle.inspect(files,D,E)
                    self.assertEqual({e['partition_guid'] for e in state['catalog']['entries']},{P[x] for x in indices})
                    self.assertFalse(any(p.startswith(assets.CHILDREN+P[i]+'/') for p in files))
                    self.assertNotIn(assets.receipt_path(P[i]),files)
                    for path,data in protected.items():self.assertEqual(files[path],data)
                    for remaining in indices:
                        self.assertEqual(files[child(remaining)['loader_path']],before[child(remaining)['loader_path']])
                self.assertEqual(lifecycle.inspect(files,D,E)['catalog']['entries'],[])
                files=apply(files,fixtures.ReconcileTests().plan(files,[0]))
                self.assertEqual(lifecycle.inspect(files,D,E)['catalog']['entries'][0]['partition_guid'],P[0])

    def test_imported_menu_entry_does_not_grant_uninstall_permission(self):
        files=apply({},fixtures.ReconcileTests().plan({},[0,1]))
        files['EFI/SomeOtherLoader/boot.efi']=b'not owned'
        before=dict(files)
        with self.assertRaisesRegex(ValueError,'cannot uninstall'):
            self.remove(files,[0,1],0,unowned=(0,))
        self.assertEqual(files,before)

    def test_another_installation_receipt_also_protects_a_shared_copy(self):
        files=apply({},fixtures.ReconcileTests().plan({},[0,1]));rows=self.rows(files,[0,1])
        shared=child(0)['loader_path']
        rows[1]['record']['efi_assets'].append(dict(partition_guid=E,path=shared,
            sha256=assets.sha(files[shared]),exclusive=True))
        rows[1]['marker']=make_marker(rows[1]['record'])
        result=format_plan(rows[0],rows,scan_complete=True,catalog_bytes=files[lifecycle.CATALOG],
                           menu_bytes=files[lifecycle.MENU],lifecycle_files=files)
        self.assertIn(shared,result['preserved_efi_paths'])
        self.assertEqual(apply(files,result)[shared],files[shared])

    def standalone(self):
        files={lifecycle.CANON:store(0),'EFI/Microsoft/Boot/bootmgfw.efi':b'original loader 0',
               child(0)['bcd_path']:store(0),'EFI/OC/OpenCore.efi':b'preserve OpenCore',
               'EFI/Boot/BOOTX64.EFI':b'preserve foreign fallback'}
        rows=self.rows(files,[0]);row=rows[0]
        row['record']['boot_layout']='standalone-canonical-esp-v1'
        row['record']['efi_assets']=[dict(partition_guid=E,path=p,sha256=assets.sha(files[p]),exclusive=True)
            for p in (lifecycle.CANON,'EFI/Microsoft/Boot/bootmgfw.efi',child(0)['bcd_path'])]
        row['marker']=make_marker(row['record'])
        path='EFI/WinDeploy/Records/'+P[0]+'.json'
        files[path]=canonical(row['record'])+b'\n'
        self.refresh_row(row,files)
        return files,rows,path

    def refresh_row(self,row,files):
        hashes={(E,p):assets.sha(v) for p,v in files.items()}
        row['efi_manifest']={p:h for (d,p),h in hashes.items()}
        row['asset_readbacks']=[dict(partition_guid=d,path=p,sha256=h) for (d,p),h in hashes.items()]
        row['observed']['unattributed_efi_paths']=list(files)
        row['ownership']=ownership(row['observed'],row['marker'],row['record'],hashes)

    def test_sole_standalone_removes_only_its_owned_public_entry(self):
        files,rows,record_path=self.standalone()
        result=format_plan(rows[0],rows,scan_complete=True,lifecycle_files=files)
        final=apply(files,result)
        self.assertNotIn(lifecycle.CANON,final)
        self.assertNotIn('EFI/Microsoft/Boot/bootmgfw.efi',final)
        self.assertNotIn(record_path,final)
        self.assertEqual(final['EFI/Boot/BOOTX64.EFI'],files['EFI/Boot/BOOTX64.EFI'])
        self.assertEqual(final['EFI/OC/OpenCore.efi'],files['EFI/OC/OpenCore.efi'])

    def test_standalone_aliases_are_retained_when_another_windows_exists(self):
        files,rows,_=self.standalone()
        rows+=self.rows(files,[1],unowned=(1,))
        result=format_plan(rows[0],rows,scan_complete=True,lifecycle_files=files)
        final=apply(files,result)
        for path in (lifecycle.CANON,'EFI/Microsoft/Boot/bootmgfw.efi'):
            self.assertEqual(final[path],files[path]);self.assertIn(path,result['preserved_efi_paths'])

    def test_serviced_boot_files_do_not_leave_paired_record_blocking_reinstall(self):
        files,rows,path=self.standalone();files['EFI/Microsoft/Boot/bootmgfw.efi']=b'serviced loader'
        self.refresh_row(rows[0],files)
        self.assertFalse(rows[0]['ownership']['managed'])
        result=format_plan(rows[0],rows,scan_complete=True,lifecycle_files=files,unknown_acknowledged=True)
        final=apply(files,result)
        self.assertNotIn(path,final)
        self.assertEqual(final['EFI/Microsoft/Boot/bootmgfw.efi'],b'serviced loader')
        self.assertIn('EFI/Microsoft/Boot/bootmgfw.efi',result['preserved_efi_paths'])
        self.assertEqual(result['operation']['ownership_warnings_required'],2)

    def test_missing_pair_never_authorizes_deleting_an_installation_record(self):
        files,rows,path=self.standalone();rows[0]['marker']=None
        self.refresh_row(rows[0],files)
        before=dict(files)
        with self.assertRaisesRegex(ValueError,'cannot uninstall'):
            format_plan(rows[0],rows,scan_complete=True,lifecycle_files=files,unknown_acknowledged=True)
        self.assertEqual(files,before)
        self.assertIn(path,files)

    def test_unrecorded_import_and_unknown_neighbour_are_retained_and_reported(self):
        for case in ('old-import','unknown-neighbour','changed-file','corrupt-receipt'):
            with self.subTest(case=case):
                files=apply({},fixtures.ReconcileTests().plan({},[0,1]))
                if case=='old-import':files.pop(assets.receipt_path(P[0]))
                if case=='changed-file':files[child(0)['loader_path']]=b'changed'
                if case=='corrupt-receipt':files[assets.receipt_path(P[0])]=b'corrupt'
                result=self.remove(files,[0,1],0,unowned=(1,) if case=='unknown-neighbour' else ())
                final=apply(files,result)
                self.assertTrue(result['cleanup_warning'])
                self.assertIn(child(0)['loader_path'],result['preserved_efi_paths'])
                self.assertEqual(final[child(0)['loader_path']],files[child(0)['loader_path']])
                self.assertEqual([e['partition_guid'] for e in lifecycle.inspect(final,D,E)['catalog']['entries']],[P[1]])

    def test_snapshot_and_reset_preserve_private_receipts_for_future_cleanup(self):
        import reset_menu
        files=apply({},fixtures.ReconcileTests().plan({},[0]))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,data in files.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            manifest={p:assets.sha(v) for p,v in files.items()}
            snapshot=lifecycle.snapshot(root,manifest)
            self.assertIn(assets.receipt_path(P[0]),snapshot)
            files=apply(files,reset_menu.plan(files,manifest,D,E))
            self.assertIn(assets.receipt_path(P[0]),files)
            # Removal after Reset must clean private copies without requiring
            # a menu/catalog or creating a new one as a side effect.
            result=self.remove(files,[0],0)
            final=apply(files,result)
            self.assertNotIn(assets.receipt_path(P[0]),final)
            self.assertNotIn(child(0)['loader_path'],final)
            self.assertNotIn(lifecycle.CATALOG,final)


if __name__=='__main__':unittest.main()
