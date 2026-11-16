import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import mac_compatibility as compat


class CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.db=compat.load()

    def result(self,model,family='Windows 7',architecture=9,**kw):
        return compat.assess(model,dict(family=family,architecture=architecture),self.db,**kw)

    def test_all_records_keep_apple_mode_separate_from_restore_policy(self):
        self.assertGreaterEqual(len(self.db['models']),120)
        for model,row in self.db['models'].items():
            self.assertRegex(model,compat.MODEL)
            for family in compat.FAMILIES:
                self.assertEqual(row['boot_camp_installation_mode'][family]['status'],'unknown')
            self.assertFalse(row['winclone_restore_policy']['apple_boot_camp_mode_proven'])
            self.assertFalse(row['winclone_restore_policy']['experimental_efi_impossible'])

    def test_no_apple_silicon_or_invented_source_model(self):
        for model in ('MacBookAir10,1','MacBookPro17,1','Macmini9,1','iMac21,1','Macmini4,2'):
            self.assertNotIn(model,self.db['models'])

    def test_win7_32_bit_driver_only_does_not_mean_32_bit_cpu(self):
        result=self.result('MacBookPro3,1')
        self.assertEqual(result['official_status'],'architecture-not-supported')
        self.assertEqual(result['official_architectures'],[32])
        self.assertIn('official-driver-architecture',[w['code'] for w in result['warnings']])
        self.assertNotIn('cpu_bits',result)
        self.assertNotIn('efi_bits',result)

    def test_primary_identity_expansion_maps_both_2007_pro_display_sizes(self):
        record=self.db['models']['MacBookPro3,1']
        self.assertEqual({v['name'] for v in record['names']},
            {'MacBook Pro (15-inch, 2.4/2.2GHz)','MacBook Pro (17-inch, 2.4 GHz)'})
        for variant in record['names']:
            self.assertIn('apple-firmware-restoration-17',variant['sources'])
            result=self.result('MacBookPro3,1',architecture=0,variant=variant['name'])
            self.assertEqual(result['official_status'],'supported')
            self.assertEqual(result['official_architectures'],[32])

    def test_same_air_variant_spelling_does_not_create_false_ambiguity(self):
        result=self.result('MacBookAir2,1')
        self.assertEqual(result['official_status'],'architecture-not-supported')
        self.assertEqual(result['official_architectures'],[32])
        self.assertNotIn('variant-dependent',[w['code'] for w in result['warnings']])

    def test_identity_source_does_not_invent_missing_windows_support(self):
        known=self.result('iMac4,1',variant='iMac (17-inch, Early 2006)')
        unknown=self.result('iMac4,1',variant='iMac (20-inch, Early 2006)')
        self.assertEqual(known['official_status'],'not-supported')
        self.assertEqual(unknown['official_status'],'not-listed')
        self.assertEqual(self.result('iMac4,1')['official_status'],'variant-dependent')
        self.assertIsNone(unknown['definitive_official_minimum'])
        self.assertEqual(unknown['apple_boot_camp_mode'],'unknown')

    def test_not_listed_advisory_does_not_claim_explicit_apple_refusal(self):
        result=self.result('MacBookPro13,3')
        self.assertEqual(result['official_status'],'not-listed')
        warning=next(w['text'] for w in result['warnings'] if w['code']=='apple-support-unconfirmed')
        self.assertIn('does not list',warning)
        self.assertIn('not proof',warning)
        self.assertNotIn('explicitly marks',warning)
        self.assertIn('You may continue',warning)
        self.assertIn('older-than-Apple-supported',warning)

    def test_explicit_unsupported_table_row_uses_different_guidance(self):
        result=self.result('iMac4,1',variant='iMac (17-inch, Early 2006)')
        warning=next(w['text'] for w in result['warnings'] if w['code']=='apple-support-unconfirmed')
        self.assertIn('explicitly marks Windows 7 as unsupported',warning)
        self.assertIn('You may continue',warning)

    def test_confirmed_apple_support_avoids_unconfirmed_support_warning(self):
        for model,family in (('MacBookPro11,3','Windows 7'),('MacBookPro11,5','Windows 10')):
            result=self.result(model,family)
            self.assertEqual(result['official_status'],'supported')
            self.assertNotIn('apple-support-unconfirmed',[w['code'] for w in result['warnings']])

    def test_same_identifier_resolves_x64_but_not_x86_variant(self):
        self.assertEqual(self.result('MacBookPro10,1')['official_status'],'supported')
        result=self.result('MacBookPro10,1',architecture=0)
        self.assertEqual(result['official_status'],'variant-dependent')
        result=self.result('MacBookPro10,1',architecture=0,variant='MacBook Pro (Retina, 15-inch, Early 2013)')
        self.assertEqual(result['official_status'],'architecture-not-supported')
        result=self.result('MacBookPro10,1',architecture=0,variant='MacBook Pro (Retina, 15-inch, Mid 2012)')
        self.assertEqual(result['official_status'],'supported')

    def test_2015_community_exception_does_not_become_apple_support(self):
        for model in ('MacBookPro11,4','MacBookPro11,5'):
            result=self.result(model)
            self.assertEqual(result['official_status'],'not-listed')
            self.assertEqual(result['project_test']['community_component'],'BackToSeven')
            self.assertFalse(result['project_test']['fresh_unified_acceptance'])
            self.assertFalse(result['warnings'])
            self.assertTrue(all('2015' in v['name'] for v in result['variant_details']))

    def test_newer_windows_driver_guidance_is_conditional(self):
        for model in ('MacBookPro11,3','MacBookPro11,4','MacBookPro11,5'):
            for family in ('Windows 7','Windows 8.1','Windows 10'):
                self.assertNotIn('newer-windows-driver-guidance',
                    [w['code'] for w in self.result(model,family)['warnings']])
            warning=next(w['text'] for w in self.result(model,'Windows 11')['warnings']
                         if w['code']=='newer-windows-driver-guidance')
            self.assertIn('Windows Update',warning)
            self.assertIn('only those missing drivers',warning)
            self.assertIn('Setup.exe',warning)

    def test_113_is_official_win7_x64_without_claiming_project_test(self):
        result=self.result('MacBookPro11,3')
        self.assertEqual(result['official_status'],'supported')
        self.assertIsNone(result['project_test'])
        self.assertEqual(result['apple_boot_camp_mode'],'unknown')
        self.assertNotIn('historical-legacy-path',[w['code'] for w in result['warnings']])

    def test_windows81_on_2011_can_be_apple_supported_and_legacy_restore(self):
        result=self.result('MacBookPro8,2','Windows 8.1')
        self.assertEqual(result['official_status'],'supported')
        self.assertTrue(result['restore_policy']['listed_legacy'])
        self.assertEqual(result['apple_boot_camp_mode'],'unknown')

    def test_windows10_2012_support_does_not_prove_efi(self):
        result=self.result('MacBookPro9,1','Windows 10')
        self.assertEqual(result['official_status'],'supported')
        self.assertTrue(result['restore_policy']['listed_legacy'])
        self.assertTrue(result['warnings'])

    def test_family_minimum_is_not_inferred_from_partial_records(self):
        result=self.result('MacPro6,1','Windows 8')
        self.assertEqual(result['official_status'],'unknown')
        self.assertIsNone(result['definitive_official_minimum'])
        self.assertIsNone(result['definitive_official_maximum'])
        self.assertEqual(result['known_supported_families'],['Windows 8.1','Windows 10'])

    def test_unknown_is_not_an_unsupported_hard_block(self):
        result=self.result('MacBookPro99,9')
        self.assertEqual(result['official_status'],'unknown')
        self.assertEqual([w['code'] for w in result['warnings']],['unknown-model-evidence'])
        ui=Mock();ui.approve.return_value=True;ui.choose.return_value=1
        continued=compat.configure('MacBookPro99,9',dict(family='Windows 7',architecture=9),ui,self.db)
        ui.approve.assert_called_once()
        self.assertEqual(continued['official_status'],'unknown')
        self.assertEqual(continued['acknowledged_warning_codes'],['unknown-model-evidence'])
        ui.approve.return_value=False
        with self.assertRaisesRegex(ValueError,'no partition was formatted'):
            compat.configure('MacBookPro99,9',dict(family='Windows 7',architecture=9),ui,self.db)

    def test_not_listed_warning_requires_acknowledgement_but_does_not_block(self):
        edition=dict(family='Windows 7',architecture=9);ui=Mock();ui.choose.return_value=1
        for answer in (0,1,'1',None,2):
            ui.approve.return_value=answer
            with self.assertRaisesRegex(ValueError,'no partition was formatted'):
                compat.configure('MacBookPro13,3',edition,ui,self.db)
        ui.approve.return_value=True
        result=compat.configure('MacBookPro13,3',edition,ui,self.db)
        self.assertIn('apple-support-unconfirmed',result['acknowledged_warning_codes'])

    def test_windows8_and_11_are_not_falsely_marked_apple_supported_or_tested(self):
        for family in ('Windows 8','Windows 11'):
            result=self.result('MacBookPro11,5',family)
            self.assertEqual(result['official_status'],'unknown')
            self.assertIsNone(result['project_test'])
            codes=[w['code'] for w in result['warnings']]
            self.assertEqual(codes,['newer-windows-driver-guidance'] if family=='Windows 11' else [])

    def test_consent_cancel_and_forged_boolean(self):
        ui=Mock()
        for answer in (0,1,'1',None,2):
            ui.approve.return_value=answer
            with self.assertRaises(ValueError):
                compat.configure('MacBookPro3,1',dict(family='Windows 7',architecture=9),ui,self.db)
        ui.approve.return_value=True
        result=compat.configure('MacBookPro3,1',dict(family='Windows 7',architecture=9),ui,self.db)
        self.assertIn('official-driver-architecture',result['acknowledged_warning_codes'])

    def test_identifier_confirmation_prefilled_and_not_restricted_to_2015(self):
        ui=Mock();ui.request.return_value='MacBookPro11,3'
        self.assertEqual(compat.confirm_model(ui,'MacBookPro11,5'),'MacBookPro11,3')
        self.assertEqual(ui.request.call_args[1]['model'],'MacBookPro11,5')
        self.assertIn('accurate',ui.request.call_args[0][1])
        ui.request.side_effect=['wrong','MacPro5,1'];ui.choose.return_value=0
        self.assertEqual(compat.confirm_model(ui,'MacPro5,1'),'MacPro5,1')

    def test_evidence_references_exist(self):
        for model,row in self.db['models'].items():
            for alias in row['names']:
                for source in alias['sources']:self.assertIn(source,self.db['sources'])
            for family in compat.FAMILIES:
                result=self.result(model,family)
                self.assertTrue(all(url.startswith('https://') for url in result['source_urls']))

    def test_database_load_refuses_wrong_schema(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'db.json';path.write_text('{"schema":99,"models":{},"sources":{}}')
            with self.assertRaises(ValueError):compat.load(path)


if __name__=='__main__':unittest.main()
