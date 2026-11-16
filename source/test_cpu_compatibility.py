import argparse
import json
import subprocess
import unittest
from unittest.mock import Mock
import cpu_compatibility as cpu


def raw(**changes):
    value=dict(schema=1,vendor='GenuineIntel',brand='Fixture CPU',max_basic=7,max_extended=0x80000004,
               leaf1=[0x306c3,0,(1<<13)|(1<<19)|(1<<20)|(1<<23),(1<<6)|(1<<26)],
               extended1=[0,0,1,(1<<20)|(1<<29)])
    value.update(changes)
    return json.dumps(value).encode('ascii')


def image(family='Windows 10',build=19045,architecture=9):
    return dict(family=family,version=(10,0,build),architecture=architecture)


class CpuTests(unittest.TestCase):
    def test_cpuid_and_executed_prefetch_without_advertised_bit(self):
        result=cpu.parse(raw(),0)
        self.assertEqual(result['status'],'observed')
        self.assertTrue(all(result['features'].values()))
        self.assertFalse(result['evidence']['prefetchw_advertised'])
        self.assertTrue(result['features']['PREFETCHW'])

    def test_instruction_rejection_unknown_and_unavailable_leaf_are_distinct(self):
        self.assertIs(cpu.parse(raw(),77)['features']['PREFETCHW'],False)
        for code in (78,-11,None,True,'0'):
            self.assertIsNone(cpu.parse(raw(),code)['features']['PREFETCHW'])
        result=cpu.parse(raw(max_basic=0,max_extended=0x80000000),0)
        self.assertIsNone(result['features']['NX'])
        self.assertIsNone(result['features']['CMPXCHG16B'])
        self.assertTrue(result['features']['PREFETCHW'])

    def test_malformed_or_forged_registers_are_not_missing_features(self):
        cases=[b'{}',b'null',b'[]',b'not JSON',b'\xff',b'x'*(cpu.MAX_OUTPUT+1),
               raw(schema=True),raw(leaf1=[True,0,0,0]),raw(extended1=[0,0,0,-1]),
               raw(max_extended='2147483649'),raw(brand={})]
        for value in cases:
            result=cpu.parse(value,0)
            self.assertEqual(result['status'],'unknown')
            self.assertTrue(all(x is None for x in result['features'].values()))

    def test_native_invocation_is_bounded_and_has_no_shell(self):
        runner=Mock(side_effect=[argparse.Namespace(returncode=0,stdout=raw()),
                                 argparse.Namespace(returncode=0,stdout=b'')])
        result=cpu.read_cpu('/bundled/cpu-probe',runner)
        self.assertTrue(result['features']['PREFETCHW'])
        self.assertEqual(runner.call_args_list[0][0][0],['/bundled/cpu-probe'])
        self.assertEqual(runner.call_args_list[1][0][0],['/bundled/cpu-probe','--prefetchw'])
        for call in runner.call_args_list:
            self.assertEqual(call[1]['timeout'],5)
            self.assertNotIn('shell',call[1])

    def test_missing_timed_out_or_failed_probe_does_not_claim_absence(self):
        for problem in (OSError('missing'),subprocess.TimeoutExpired('probe',5)):
            result=cpu.read_cpu(runner=Mock(side_effect=problem))
            self.assertEqual(result['status'],'unknown')
        result=cpu.read_cpu(runner=Mock(return_value=argparse.Namespace(returncode=1,stdout=raw())))
        self.assertEqual(result['status'],'unknown')
        runner=Mock(side_effect=[argparse.Namespace(returncode=0,stdout=raw()),OSError('failed')])
        result=cpu.read_cpu(runner=runner)
        self.assertTrue(result['features']['SSE2']);self.assertIsNone(result['features']['PREFETCHW'])

    def test_each_windows_generation_uses_its_own_requirements(self):
        self.assertEqual(cpu.requirements(image('Windows 7',7601))['required'],['X64'])
        self.assertEqual(cpu.requirements(image('Windows 8',9200))['required'],['X64','PAE','NX','SSE2'])
        for family,build in (('Windows 8.1',9600),('Windows 10',19045)):
            self.assertEqual(cpu.requirements(image(family,build))['required'],
                             ['X64','PAE','NX','SSE2','CMPXCHG16B','LAHF/SAHF','PREFETCHW'])

    def test_image_build_not_edition_name_controls_24h2_checks(self):
        for build in (22000,22621,22631,26100,26200):
            for name in ('Pro','Enterprise','IoT Enterprise','LTSC'):
                edition=dict(image('Windows 11',build),name=name)
                required=cpu.requirements(edition)['required']
                self.assertEqual('POPCNT' in required,build>=26100)
                self.assertEqual('SSE4.2' in required,build>=26100)
                self.assertEqual('SSE4.1' in required,build>=26100)

    def test_23h2_and_older_do_not_warn_for_missing_sse4_or_popcnt(self):
        # A first-generation Core 2-style capability set: actual bits, never
        # guessed from the model. Its missing SSE4.1 must not become a false
        # boot-failure warning merely because Microsoft's certification lists it.
        older=cpu.parse(raw(leaf1=[0,0,1<<13,(1<<6)|(1<<26)]),0)
        for build in (22000,22621,22631):
            ui=Mock()
            result=cpu.configure(image('Windows 11',build),ui,lambda:older)
            self.assertFalse(result['warning']);ui.choose.assert_not_called()
            self.assertEqual(result['rule']['required'],cpu.requirements(image('Windows 10'))['required'])

    def test_unrecognized_preview_build_is_not_called_23h2_or_compatible(self):
        for build in (22635,25000,25905,26080):
            result=cpu.assess(image('Windows 11',build),cpu.parse(raw(),0))
            self.assertTrue(result['rule']['release_requirements_unknown'])
            self.assertIn('Preview builds',result['warning'])

    def test_unknown_windows11_build_warns_even_when_measured_features_pass(self):
        for version in (None,(10,0,True),(10,0,'26100'),(10,0),(-1,0,26100)):
            edition=dict(image('Windows 11'),version=version)
            result=cpu.assess(edition,cpu.parse(raw(),0))
            self.assertTrue(result['rule']['build_unknown'])
            self.assertIn('build could not be established',result['warning'])

    def test_all_compatible_images_are_silent_without_claiming_full_support(self):
        for family in cpu.FAMILIES:
            ui=Mock()
            result=cpu.configure(image(family,26100),ui,lambda:cpu.parse(raw(),0))
            ui.choose.assert_not_called()
            self.assertFalse(result['full_windows_eligibility_claimed'])

    def test_same_model_does_not_override_actual_processor(self):
        edition=dict(image('Windows 11',26100),model='MacBookPro11,5')
        capable=cpu.parse(raw(),0)
        older=cpu.parse(raw(leaf1=[0,0,(1<<13)|(1<<19),(1<<6)|(1<<26)]),0)
        self.assertFalse(cpu.assess(edition,capable)['warning'])
        missing=cpu.assess(edition,older)
        self.assertEqual(missing['missing'],['POPCNT','SSE4.2'])
        self.assertIn('23H2',missing['warning'])

    def test_warning_needs_real_acknowledgement(self):
        ui=Mock();reader=lambda:cpu.unknown('fixture')
        for answer in (0,True,'1',None,2):
            ui.choose.return_value=answer
            with self.assertRaisesRegex(ValueError,'no partition was formatted'):
                cpu.configure(image(),ui,reader)
        ui.choose.return_value=1
        result=cpu.configure(image(),ui,reader)
        self.assertTrue(result['continued_despite_cpu_warning'])
        self.assertFalse(result['missing']);self.assertTrue(result['unknown'])

    def test_unsupported_architecture_is_not_normalized_to_x64(self):
        for architecture in (True,False,9.0,'9',None,12):
            with self.assertRaises(ValueError):cpu.requirements(image(architecture=architecture))
        with self.assertRaises(ValueError):cpu.requirements(image('Windows XP'))


if __name__=='__main__':unittest.main()
