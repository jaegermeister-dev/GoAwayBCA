import argparse,copy,hashlib,json,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import Mock,patch
import boot_diagnostic_binding as binding
import diagnostic_candidates as candidates
import diagnostic_kits as kits
import standard_boot as standard
import test_multiboot_workflow as workflow
import boot_diagnostics as bd
import installation_policy as policy
import menu_lifecycle as m
from product_scope import require_family
from install_windows7 import Refusal

D,E,W=[str(uuid.uuid5(uuid.NAMESPACE_URL,'beta6-fixture-'+x)) for x in ('disk','esp','windows')]

class Beta6(unittest.TestCase):
 def test_standard_installation_orchestration_keeps_guards(self):
  for family in ('Windows 8.1','Windows 10','Windows 11'):
   for scenario in ('success','cancel-confirm','bad-overlay','source-changed','driver-changed','dry-run'):
    with self.subTest(family=family,scenario=scenario):
     error,formatted,published,approvals,events=workflow.Workflow().fixture(scenario,family,unified=True,standard=True)
     if scenario in ('success','dry-run'):self.assertIsNone(error,repr(error))
     else:self.assertIsNotNone(error)
     self.assertEqual(formatted,scenario in ('success','bad-overlay'))
     self.assertEqual(published,scenario=='success')
     self.assertNotIn('unified-menu-plan',events);self.assertNotIn('experimental-wrapper',events)
 def test_kit_deletion_transaction_preserves_other_kits_and_boot_files(self):
  from efi_transaction import EFITransaction
  with tempfile.TemporaryDirectory() as temp:
   base=Path(temp).resolve();root=base/'esp';root.mkdir()
   self.kit(root);self.kit(root,'Test-002')
   boot=root/'EFI/OC/OpenCore.efi';boot.parent.mkdir();boot.write_bytes(b'keep boot file')
   rows=kits.inspect(root,E);files=kits.selected_files(root,[rows[0]])
   tx=EFITransaction(root,base/'transaction',lambda:None)
   tx.prepare([dict(path=p,expected=h,data=None) for p,h in files.items()]);tx.commit()
   self.assertTrue(all(not (root/p).exists() for p in files))
   self.assertEqual(boot.read_bytes(),b'keep boot file')
   self.assertEqual((root/kits.PREFIX/'Test-002/test1-000.log').read_text(),'keep if not selected')
 def test_supported_installation_orchestration_keeps_guards(self):
  for family in ('Windows 8.1','Windows 10','Windows 11'):
   for scenario in ('success','cancel-confirm','bad-overlay','source-changed','driver-changed','dry-run'):
    with self.subTest(family=family,scenario=scenario),patch('standard_boot.choose',return_value=True):
     error,formatted,published,approvals,events=workflow.Workflow().fixture(scenario,family,unified=True)
     if scenario in ('success','dry-run'):self.assertIsNone(error,repr(error))
     else:self.assertIsNotNone(error)
     self.assertEqual(formatted,scenario in ('success','bad-overlay'))
     self.assertEqual(published,scenario=='success')
     self.assertNotIn('efi-choice',events);self.assertNotIn('experimental-wrapper',events)
 def test_partition_picker_does_not_read_windows(self):
  target=dict(partition=dict(DeviceIdentifier='disk1s3',DiskUUID=W,FilesystemType='ntfs',VolumeName='My Windows',TotalSize=50*1024**3),disk=dict(DeviceIdentifier='disk1',MediaName='Fixture',Internal=True),esp='disk1s1')
  with patch.object(candidates,'partition_summary',return_value='disk1s3'),patch('ntfs_read.Probe',side_effect=AssertionError('No NTFS inspection')):
   for fs in ('ntfs','exfat'):
    target['partition']['FilesystemType']=fs;rows=candidates.candidates([target]);self.assertEqual(len(rows),1)
    self.assertIn('not been inspected',rows[0]['summary'])
   target['partition']['FilesystemType']='hfs';self.assertEqual(candidates.candidates([target]),[])
 def test_picker_reached_even_when_old_windows_scan_would_fail(self):
  with tempfile.TemporaryDirectory() as temp:
   obj=bd.BootDiagnostics(argparse.Namespace());obj.begin=Mock();obj.session=Path(temp);obj.note=Mock()
   obj.discover=Mock(side_effect=AssertionError('NTFS root identity changed'))
   obj.choose=Mock(side_effect=ValueError('user cancelled at picker'))
   rows=[dict(title='Windows',summary='disk1s3',target={})]
   with patch.object(bd,'discovery_inventory',return_value=[]),patch.object(bd,'candidates',return_value=rows),self.assertRaisesRegex(ValueError,'user cancelled'):
    obj.execute()
   obj.choose.assert_called_once();obj.discover.assert_not_called()
 def test_supported_versions_never_offer_or_apply_generic_patch(self):
  for model in ('MacBook5,1','MacBookPro8,1','MacBookPro11,3','MacBookPro11,4','MacBookPro11,5'):
   for family in ('Windows 8.1','Windows 10','Windows 11'):
    p=policy.efi_policy(model,family);self.assertFalse(p['show_choice']);self.assertFalse(p['default_enabled'])
    with self.assertRaises(ValueError):policy.validate_efi_choice(model,family,True)
  p=policy.efi_policy('MacBookPro11,5','Windows 7');self.assertTrue(p['mandatory']);self.assertTrue(p['default_enabled'])
  for family in ('Windows 7','Windows 8'):
   with self.assertRaises(ValueError):require_family(family)
 def kit(self,root,name='Test-001',version='0.5.0-beta.5',esp=E):
  p=root/kits.PREFIX/name;p.mkdir(parents=True)
  config=binding.configuration(str(uuid.uuid4()),D,esp,W,512,'11'*32,'22'*32,'MacBook5,1',folder_name=name)
  (p/'Test1.efi').write_bytes(b'MZfixture'+config)
  if version:(p/'macos-report.json').write_text(json.dumps(dict(app_version=version,menu_title='My named Windows')))
  (p/'test1-000.log').write_text('keep if not selected');return p
 def test_kit_list_uses_report_versions_and_keeps_unknown_explicit(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp).resolve();self.kit(root);self.kit(root,'Test-002',None)
   rows=kits.inspect(root,E);self.assertEqual(rows[0]['version'],'0.5.0-beta.5');self.assertEqual(rows[1]['version'],'Unknown version')
   self.assertTrue(all(r['verified'] for r in rows));selected=kits.selected_files(root,[rows[0]])
   self.assertTrue(all('/Test-001/' in p for p in selected));self.assertTrue((root/kits.PREFIX/'Test-002/test1-000.log').exists())
 def test_wrong_esp_or_tampered_binding_cannot_be_deleted(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp).resolve();p=self.kit(root,esp=W);rows=kits.inspect(root,E)
   self.assertFalse(rows[0]['verified'])
   with self.assertRaises(Refusal):kits.selected_files(root,rows)
 def test_standard_entry_is_real_microsoft_not_chooser(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp).resolve();boot=root/'EFI/Microsoft/Boot';boot.mkdir(parents=True)
   (boot/'bootmgfw.efi').write_bytes(b'MZ-original');(boot/'BCD').write_bytes(b'colocated')
   overlay=dict(root=root,canonical_bcd=b'canonical-selected',record=dict(disk_guid=D,partition_guid=W))
   files={'EFI/Boot/BOOTX64.EFI':b'OpenCore-owned','EFI/OC/OpenCore.efi':b'unchanged'}
   previous=dict(catalog=dict(entries=[]),stores={})
   with patch.object(standard,'validate_efi_application'):
    plan=standard.plan(overlay,files,{p:m.sha(v) for p,v in files.items()},previous,[])
   new={c['path']:c['data'] for c in plan['changes']}
   self.assertEqual(new['EFI/Microsoft/Boot/bootmgfw.efi'],b'MZ-original');self.assertEqual(new[m.CANON],b'canonical-selected')
   self.assertNotIn('EFI/Boot/BOOTX64.EFI',new);self.assertFalse(any(p.startswith('EFI/OC') for p in new))
   self.assertNotIn(m.MENU,new);self.assertNotIn(m.CATALOG,new);self.assertNotIn('EFI/WinDeploy/Windows.efi',new)
 def test_menu_recommendation_matches_single_or_multi_intent(self):
  owner=Mock();owner.choose.side_effect=[0,0]
  self.assertFalse(standard.choose(owner,[],dict(disk=dict(DeviceIdentifier='disk0'))))
  owner=Mock();owner.choose.side_effect=[1,0]
  self.assertTrue(standard.choose(owner,[],dict(disk=dict(DeviceIdentifier='disk0'))))

if __name__=='__main__':unittest.main()
