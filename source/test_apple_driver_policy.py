from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import zipfile
import apple_driver_policy as policy
import bootcamp_download as b

class PolicyTests(unittest.TestCase):
    def product(self):return dict(id='041-98143',toc_sha1=policy.KNOWN_6_TOC,post_date='2019-10-10')
    def test_same_model_different_os_uses_appropriate_original_package(self):
        p=self.product()
        self.assertEqual(policy.rank('MacBookPro11,3','Windows 7',[p])[0]['id'],'Apple-5.1.5640')
        for family in ('Windows 8.1','Windows 10'):
            self.assertEqual(policy.rank('MacBookPro11,3',family,[p])[0]['id'],p['id'])
            self.assertTrue(policy.rank('MacBookPro11,5',family,[p])[0]['os_match'])
        self.assertFalse(policy.rank('MacBookPro11,5','Windows 7',[p])[0]['os_match'])
        self.assertFalse(policy.rank('MacBookPro11,4','Windows 7',[p])[0]['os_match'])

    def test_future_newest_is_not_blindly_preferred_or_called_supported(self):
        rows=policy.rank('MacBookPro11,5','Windows 8.1',[self.product(),dict(id='999-99999',post_date='2099')])
        self.assertEqual(rows[0]['id'],'041-98143');self.assertFalse(rows[-1]['os_match'])
        changed=self.product();changed['toc_sha1']='0'*40
        self.assertFalse(policy.rank('MacBookPro11,5','Windows 8.1',[changed])[0]['os_match'])

    def test_unsupported_fallback_is_explicit_and_skippable(self):
        class UI:
            def choose(self,text,options):
                self.text=text;return len(options)-1
        ui=UI()
        self.assertIsNone(policy.choose('MacBookPro11,5','Windows 7',[self.product()],ui,lambda _:None))
        self.assertIn('may reject',ui.text)

    def test_package_os_and_model_lists_do_not_prove_their_cross_product(self):
        rows=policy.rank('MacPro6,1','Windows 7',[self.product()])
        self.assertEqual(rows[0]['id'],'Apple-5.1.5640')
        self.assertFalse(any(r['os_match'] for r in rows))
        self.assertEqual(rows[0]['apple_support_status'],'not-listed')
        ui=Mock();ui.choose.return_value=0
        picked=policy.choose('MacPro6,1','Windows 7',[self.product()],ui,lambda _:None)
        ui.choose.assert_called_once()
        self.assertEqual(picked['id'],'Apple-5.1.5640')
        self.assertFalse(picked['os_match'])

    def test_windows8_does_not_inherit_known_windows81_package_support(self):
        rows=policy.rank('MacBookPro11,5','Windows 8',[self.product()])
        self.assertFalse(rows[0]['os_match'])
        self.assertEqual(rows[0]['driver_family_basis'],'Windows 8')
        self.assertIn('Windows 8 compatibility is not established',rows[0]['reason'])
        older=policy.rank('MacBookPro11,3','Windows 8',[self.product()])
        self.assertEqual(older[0]['id'],'Apple-5.1.5640')
        self.assertFalse(any(r['os_match'] for r in older))
        self.assertEqual(older[0]['apple_support_status'],'unknown')

    def test_windows11_uses_same_original_windows10_package_with_honest_receipt(self):
        row=policy.rank('MacBookPro11,5','Windows 11',[self.product()])[0]
        self.assertTrue(row['os_match'])
        self.assertEqual(row['driver_family_basis'],'Windows 10')
        self.assertEqual(row['requested_family'],'Windows 11')
        self.assertIn('unchanged Windows 10',row['reason'])

    def test_shared_model_variant_uncertainty_is_not_silently_accepted(self):
        rows=policy.rank('iMac15,1','Windows 7',[])
        self.assertEqual(rows[0]['id'],'Apple-5.1.5769')
        self.assertEqual(rows[0]['apple_support_status'],'variant-dependent')
        self.assertFalse(rows[0]['os_match'])

    def test_fallback_selection_requires_a_real_bounded_integer(self):
        ui=Mock()
        for value in (-1,True,'0',None,100):
            ui.choose.return_value=value
            with self.assertRaisesRegex(ValueError,'Invalid official driver fallback'):
                policy.choose('MacBookPro11,5','Windows 7',[self.product()],ui,lambda _:None)

    def test_wrong_catalog_model_and_unknown_windows_family_are_rejected(self):
        product=dict(self.product(),model='MacBookPro11,3')
        with self.assertRaisesRegex(ValueError,'another Mac'):
            policy.rank('MacBookPro11,5','Windows 10',[product])
        with self.assertRaisesRegex(ValueError,'Unknown Windows family'):
            policy.rank('MacBookPro11,5','Windows Vista',[])

    def test_official_zip_regular_files_verified_and_no_scripts_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'apple.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('BootCamp5.1.5640/BootCamp/Drivers/Apple/BootCamp.msi',b'never execute')
                z.writestr('BootCamp5.1.5640/BootCamp/Setup.exe',b'original')
            lines=[];files=b.unpack_official_zip(archive,root/'out',lines.append)
            self.assertEqual(len(files),2);self.assertEqual((root/'out/BootCamp/Setup.exe').read_bytes(),b'original')
            self.assertEqual(lines[-1].progress['percent'],100)
        for name in ('../escape','/absolute','C:/evil','folder\\evil'):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);archive=root/'a.zip'
                with zipfile.ZipFile(archive,'w') as z:z.writestr(name,b'x')
                with self.assertRaises(ValueError):b.unpack_official_zip(archive,root/'out',lambda _:None)

if __name__=='__main__':unittest.main()
