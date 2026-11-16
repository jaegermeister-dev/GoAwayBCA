"""Actual parsing/gating/IPC outcomes; no disk or firmware writes."""
import argparse
import plistlib
import subprocess
import unittest
from unittest.mock import Mock
import firmware_compatibility as fw
import windows11_guidance as cpu


def observation(bits=64):
    return fw.parse_ioreg(plistlib.dumps([dict(IORegistryEntryName='efi',
                                            **{'firmware-abi':('EFI'+str(bits)).encode()+b'\0'})]))


class FirmwareTests(unittest.TestCase):
    def test_xml_binary_and_nested_ioreg(self):
        for bits in (32,64):
            for fmt in (plistlib.FMT_XML,plistlib.FMT_BINARY):
                for abi in ('EFI'+str(bits),('EFI'+str(bits)).encode()+b'\0'):
                    raw=plistlib.dumps([dict(IORegistryEntryChildren=[dict(IORegistryEntryName='efi',**{'firmware-abi':abi})])],fmt=fmt)
                    result=fw.parse_ioreg(raw)
                    self.assertEqual((result['status'],result['bits']),('verified',bits))

    def test_malformed_empty_missing_conflicting_and_wrong_nodes_are_unknown(self):
        cases=[b'',None,b'x'*(fw.MAX_PLIST_BYTES+1),b'<?xml version="1.0"?><plist><dict>',b'garbage',
            plistlib.dumps({'firmware-abi':'EFI64'}),
            plistlib.dumps([{'IORegistryEntryName':'CPU','firmware-abi':'EFI64'}]),
            plistlib.dumps([{'IORegistryEntryName':'efi','firmware-abi':'EFI128'}]),
            plistlib.dumps([{'IORegistryEntryName':'efi'}]),
            plistlib.dumps([{'IORegistryEntryName':'efi','firmware-abi':'EFI64'},
                           {'IORegistryEntryName':'efi','firmware-abi':'EFI32'}])]
        for value in cases:
            self.assertEqual(fw.parse_ioreg(value)['status'],'unknown')

    def test_probe_only_reads_actual_efi_and_handles_command_failure(self):
        run=Mock(return_value=argparse.Namespace(returncode=0,stdout=plistlib.dumps([
            {'IORegistryEntryName':'efi','firmware-abi':b'EFI64\0'}])))
        self.assertEqual(fw.read_firmware(run)['bits'],64)
        self.assertEqual(run.call_args[0][0],list(fw.IOREG_COMMAND))
        self.assertEqual(run.call_args[1]['timeout'],10)
        for problem in (OSError('missing'),subprocess.TimeoutExpired('ioreg',10)):
            run.side_effect=problem
            self.assertEqual(fw.read_firmware(run)['status'],'unknown')

    def test_architecture_matrix_separate_from_windows_loader(self):
        for architecture,image_bits in ((0,32),(9,64)):
            for efi in (32,64):
                edition=dict(architecture=architecture,family='Windows 7')
                self.assertEqual(fw.assess(edition,observation(efi))['allowed'],image_bits==efi)
        self.assertFalse(fw.assess_windows_loader(dict(architecture=0,family='Windows 7'))['allowed'])
        self.assertTrue(fw.assess_windows_loader(dict(architecture=9,family='Windows 7'))['allowed'])

    def test_invalid_image_or_forged_firmware_cannot_pass(self):
        for arch in (None,True,False,'9',12,9.0):
            self.assertFalse(fw.assess({'architecture':arch},observation())['allowed'])
        for value in (None,{},dict(status='verified',bits=64,abi='EFI32'),
                      dict(status='unknown',bits=64,abi='EFI64'),dict(status='verified',bits='64',abi='EFI64')):
            self.assertFalse(fw.assess({'architecture':9},value)['allowed'])

    def test_ui_cannot_override_known_mismatch_or_unknown(self):
        edition=dict(architecture=9,family='Windows 10')
        for observed in (observation(32),fw.unknown('unavailable')):
            for answer in (True,1,'continue','try-anyway','choose-image',None):
                ui=Mock();ui.request.return_value=answer
                with self.assertRaises(fw.FirmwareCompatibilityError):
                    fw.configure(edition,ui,lambda:observed)
                kind,text=ui.request.call_args[0]
                self.assertEqual(kind,'firmware_block')
                # The native protocol is XML plist: no None or unsupported types.
                plistlib.dumps(dict(type=kind,text=text,**ui.request.call_args[1]))
        ui.request.return_value='retry'
        with self.assertRaises(fw.FirmwareCompatibilityError):
            fw.configure(edition,ui,lambda:observation(32))

    def test_unknown_retry_reobserves_firmware_before_proceeding(self):
        reader=Mock(side_effect=[fw.unknown('first read failed'),observation()])
        ui=Mock();ui.request.return_value='retry'
        self.assertTrue(fw.configure(dict(architecture=9,family='Windows 10'),ui,reader)['allowed'])
        self.assertEqual(reader.call_count,2)
        self.assertEqual(ui.request.call_count,1)

    def test_matching_architecture_does_not_bypass_loader_check(self):
        ui=Mock();ui.request.return_value=True
        with self.assertRaises(fw.FirmwareCompatibilityError) as error:
            fw.configure(dict(architecture=0,family='Windows 7'),ui,lambda:observation(32))
        self.assertEqual(error.exception.result['code'],'windows-x86-no-efi-loader')

    def test_receipt_revalidated_and_mismatch_stops(self):
        edition=dict(architecture=9,family='Windows 10')
        previous=fw.require_compatible(edition,lambda:observation())
        self.assertTrue(fw.revalidate(edition,previous,lambda:observation())['allowed'])
        with self.assertRaises(fw.FirmwareCompatibilityError):
            fw.revalidate(edition,previous,lambda:observation(32))
        with self.assertRaises(fw.FirmwareCompatibilityError):
            fw.revalidate(dict(edition,architecture=0),previous,lambda:observation())
        with self.assertRaises(ValueError):
            fw.revalidate(edition,dict(previous,allowed=False),lambda:observation())

    def test_guides_do_not_call_win10_recipe_win11_support(self):
        result=fw.assess(dict(architecture=9,family='Windows 11'),observation(32))
        self.assertFalse(any('virtuallyfun.com' in link['url'] for link in result['links']))
        self.assertTrue(result['links'])

    def test_matching_firmware_still_requires_cpu_acknowledgement(self):
        edition=dict(architecture=9,family='Windows 11',version=(10,0,26100))
        ui=Mock();ui.choose.return_value=0
        self.assertTrue(fw.configure(edition,ui,lambda:observation())['allowed'])
        with self.assertRaises(ValueError):cpu.configure(edition,ui,lambda:frozenset({'SSE2'}))
        ui.choose.return_value=1
        result=cpu.configure(edition,ui,lambda:frozenset({'SSE2'}))
        self.assertTrue(result['continued_despite_cpu_warning'])
        ui.reset_mock()
        cpu.configure(edition,ui,lambda:frozenset({'POPCNT','SSE4.2'}))
        ui.choose.assert_not_called()

    def test_backtoseven_component_blocks_before_download_or_target_selection(self):
        # Exercise the second execute override, rather than relying on the
        # broader installer's test to imply this branch is guarded too.
        from contextlib import ExitStack
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        import install_backtoseven as component
        for observed in (observation(32),fw.unknown('fixture')):
            with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
                root=Path(tmp).resolve();(root/'media/sources').mkdir(parents=True)
                (root/'test.iso').write_bytes(b'ISO');(root/'media/sources/install.wim').write_bytes(b'WIM')
                (root/'editions.xml').write_bytes(b'fixture')
                ui=Mock();ui.request.return_value=True;ui.choose.return_value=0
                engine=component.BackToSevenInstaller(argparse.Namespace(iso=str(root/'test.iso')),ui)
                def begin():engine.session=root;engine.wim='fake-wim'
                engine.begin=begin;engine.note=Mock();engine.scan=Mock(return_value={})
                engine.mount_iso=Mock(return_value=root/'media')
                for name,value in (('system','Darwin'),('machine','x86_64')):
                    stack.enter_context(patch.object(component.platform,name,return_value=value))
                stack.enter_context(patch.object(component.os,'geteuid',return_value=0,create=True))
                stack.enter_context(patch.object(component,'run',return_value=b'MacBookPro11,5'))
                stack.enter_context(patch.object(component,'confirm_model',return_value='MacBookPro11,5'))
                stack.enter_context(patch('backtoseven_experimental_policy.installation_choice',return_value=False))
                stack.enter_context(patch.object(component,'inspect_editions',return_value=[dict(index=1,name='Windows 7',family='Windows 7',architecture=9)]))
                stack.enter_context(patch.object(fw,'read_firmware',return_value=observed))
                drivers=stack.enter_context(patch.object(component.driver_preparation,'prepare'))
                inventory=stack.enter_context(patch.object(component,'inventory'))
                with self.assertRaises(fw.FirmwareCompatibilityError):engine.execute()
                drivers.assert_not_called();inventory.assert_not_called()


if __name__=='__main__':unittest.main()
