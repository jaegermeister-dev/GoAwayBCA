"""8/8.1 and 11/10 share deployment paths, not hardware-test claims."""
import unittest
from unittest.mock import patch
import apple_driver_policy
import installation_policy
import installation_records
import windows10_boot
from test_installation_records import RecordsTests


class SharedWorkflows(unittest.TestCase):
    def test_shared_deployment_does_not_imply_identical_driver_eligibility(self):
        products=[dict(id=apple_driver_policy.KNOWN_6,
                       toc_sha1=apple_driver_policy.KNOWN_6_TOC)]
        for model in ('MacBookPro11,5','MacBookPro11,3','MacBookPro10,1'):
            ten=apple_driver_policy.rank(model,'Windows 10',products)
            eleven=apple_driver_policy.rank(model,'Windows 11',products)
            self.assertEqual(len(ten),len(eleven))
            for first,second in zip(ten,eleven):
                # Identical packages/bytes, while recording the real requested
                # Windows family and the Windows 10 driver basis honestly.
                for key in ('id','kind','url','toc_sha1','size','rank','os_match','driver_family_basis'):
                    self.assertEqual(first.get(key),second.get(key))
                self.assertEqual(first['requested_family'],'Windows 10')
                self.assertEqual(second['requested_family'],'Windows 11')
            eight=apple_driver_policy.rank(model,'Windows 8',products)
            catalog=next(r for r in eight if r['id']==apple_driver_policy.KNOWN_6)
            self.assertFalse(catalog['os_match'])
            self.assertEqual(catalog['driver_family_basis'],'Windows 8')

    def test_same_stock_policy_on_115_other_models_keep_user_choice(self):
        for family,baseline in (('Windows 8','Windows 8.1'),('Windows 11','Windows 10')):
            current=installation_policy.efi_policy('MacBookPro11,5',family)
            prior=installation_policy.efi_policy('MacBookPro11,5',baseline)
            for field in ('default_enabled','mandatory'):
                self.assertEqual(current[field],prior[field])
            self.assertEqual(current['show_choice'], family=='Windows 11')
            self.assertNotIn('tested',current['status'])
            for model in ('MacBookPro11,3','MacBookPro11,4','MacBookPro10,1'):
                policy=installation_policy.efi_policy(model,family)
                for enabled in (True,False):
                    if policy['show_choice'] or enabled==policy['default_enabled']:
                        installation_policy.validate_efi_choice(model,family,enabled)
                    else:
                        with self.assertRaises(ValueError):
                            installation_policy.validate_efi_choice(model,family,enabled)

    def test_records_keep_real_selected_windows_name(self):
        fixture=RecordsTests();fixture.setUp()
        for family in ('Windows 8','Windows 11'):
            record=dict(fixture.record,family=family)
            marker=installation_records.make_marker(record)
            self.assertEqual(installation_records.validate_record(record)['family'],family)
            self.assertEqual(marker['record_sha256'],installation_records.sha(record))

    def test_template_selection_matches_baseline_without_device_access(self):
        class Stop(Exception):pass
        for family,expected in (('Windows 8',windows10_boot.TEMPLATE81_SHA),
                                ('Windows 11',windows10_boot.TEMPLATE_SHA)):
            seen=[]
            def guard(value,message):
                seen.append(value)
                if len(seen)==2:raise Stop()
                self.assertTrue(value)
            with patch.object(windows10_boot,'require',side_effect=guard),self.assertRaises(Stop):
                windows10_boot.stage(None,None,dict(family=family),None,None,None,None,None,None)
            self.assertEqual(seen[1],expected)


if __name__=='__main__':unittest.main()
