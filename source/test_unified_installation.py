"""Routing and early refusal checks; all devices and command execution faked."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import install_backtoseven as component
import install_multiboot as broader
from install_unified import UnifiedInstaller, uses_backtoseven
from install_windows7 import Refusal


class UnifiedSelection(unittest.TestCase):
    def test_component_only_for_windows7_on_two_models(self):
        for model in ('MacBookPro11,4','MacBookPro11,5','MacBookPro11,3','iMac12,2'):
            for family in broader.FAMILIES:
                self.assertEqual(uses_backtoseven(model,dict(family=family)),
                    family=='Windows 7' and model in ('MacBookPro11,4','MacBookPro11,5'))
        with self.assertRaises(ValueError):uses_backtoseven('MacBookPro11,5',dict(family='Windows Vista'))

    def selection(self,root,model,family):
        iso=root/'source.iso';iso.write_bytes(b'ISO')
        media=root/'media';(media/'sources').mkdir(parents=True)
        wim=media/'sources/install.wim';wim.write_bytes(b'WIM')
        edition=dict(family=family,index=1,name=family+' Professional',architecture=9,version=(6,1,7601),bytes=100)
        engine=UnifiedInstaller(argparse.Namespace(iso=str(iso)),Mock())
        engine.session=root/'session';engine.session.mkdir()
        (engine.session/'editions.xml').write_bytes(b'fixture')
        engine.wim='mock-wim'
        engine.begin=Mock();engine.mount_iso=Mock(return_value=media)
        engine.scan=Mock(return_value=dict(scan_complete=True,installations=[]))
        engine.choose=Mock(return_value=0)
        return engine,edition

    def test_one_selection_then_real_engine_stops_before_downloads(self):
        # Enter the actual selected engine, stopping at its driver dialog. A
        # duplicate ISO read, begin(), CPU/EFI check or model prompt is a bug.
        for model,family in (('MacBookPro11,4','Windows 7'),('MacBookPro11,5','Windows 7'),
                             ('MacBookPro11,3','Windows 7'),('MacBookPro11,5','Windows 10'),
                             ('iMac12,2','Windows 8.1'),('MacBookPro11,5','Windows 11')):
            with self.subTest(model=model,family=family),tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
                engine,edition=self.selection(Path(tmp),model,family)
                def run(argv):
                    if argv==['/usr/sbin/sysctl','-n','hw.model']:return model.encode()
                    self.assertEqual(argv[:2],['mock-wim','info'])
                    Path(argv[-1].split('=',1)[1]).write_bytes(b'fixture')
                    return b''
                stack.enter_context(patch.object(broader.platform,'system',return_value='Darwin'))
                stack.enter_context(patch.object(broader.platform,'machine',return_value='x86_64'))
                stack.enter_context(patch.object(broader.os,'geteuid',return_value=0,create=True))
                commands=stack.enter_context(patch.object(broader,'run',side_effect=run))
                stack.enter_context(patch.object(broader,'inventory',return_value=[dict(partition=dict(DeviceIdentifier='disk99s4'))]))
                stack.enter_context(patch.object(component,'inventory',return_value=[dict(partition=dict(DeviceIdentifier='disk99s4'))]))
                stack.enter_context(patch.object(broader,'inspect_editions',return_value=[edition]))
                model_ui=stack.enter_context(patch.object(broader.mac_compatibility,'confirm_model',return_value=model))
                mac_ui=stack.enter_context(patch.object(broader.mac_compatibility,'configure',return_value={'fixture':'model'}))
                firmware_ui=stack.enter_context(patch.object(broader.firmware_compatibility,'configure',return_value={'fixture':'EFI'}))
                cpu_ui=stack.enter_context(patch.object(broader.cpu_compatibility,'configure',return_value={'fixture':'CPU'}))
                stack.enter_context(patch.object(broader,'prompt_entry_name',return_value=family))
                stack.enter_context(patch.object(broader,'choose_efi',return_value=False))
                stack.enter_context(patch.object(component,'choose_efi',return_value=model=='MacBookPro11,5'))
                menu=stack.enter_context(patch('backtoseven_experimental_policy.installation_choice',return_value=False))
                community=stack.enter_context(patch.object(component,'configure_drivers',side_effect=Refusal('community dialog sentinel')))
                official=stack.enter_context(patch.object(broader.driver_preparation,'configure',side_effect=Refusal('official dialog sentinel')))
                download=stack.enter_context(patch.object(broader.driver_preparation,'prepare'))
                with self.assertRaisesRegex(Refusal,'dialog sentinel'):engine.execute()
                special=uses_backtoseven(model,edition)
                self.assertEqual(community.call_count,1 if special else 0)
                self.assertEqual(official.call_count,0 if special else 1)
                self.assertEqual(menu.call_count,0)  # Unified app always creates its menu.
                if special:self.assertTrue(engine.experimental_menu)
                for call in (model_ui,mac_ui,firmware_ui,cpu_ui,engine.begin,engine.scan,engine.mount_iso):
                    self.assertEqual(call.call_count,1)
                self.assertEqual(commands.call_count,2)
                self.assertEqual(engine.confirmed_model,model)
                self.assertNotIn('_unified_selection',engine.__dict__)
                download.assert_not_called()

    def test_unreadable_existing_install_stops_before_downloads_after_early_iso_notice(self):
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
            engine,edition=self.selection(Path(tmp),'MacBookPro11,5','Windows 7')
            engine.scan.return_value=dict(scan_complete=False,installations=[])
            stack.enter_context(patch.object(broader,'inspect_editions',return_value=[edition]))
            stack.enter_context(patch.object(broader.platform,'system',return_value='Darwin'))
            stack.enter_context(patch.object(broader.platform,'machine',return_value='x86_64'))
            stack.enter_context(patch.object(broader.os,'geteuid',return_value=0,create=True))
            stack.enter_context(patch.object(broader,'run',return_value=b'MacBookPro11,5'))
            stack.enter_context(patch.object(broader.mac_compatibility,'confirm_model',return_value='MacBookPro11,5'))
            with self.assertRaisesRegex(Refusal,'scan incomplete'):engine.execute()
            engine.mount_iso.assert_called_once()
            self.assertNotIn('_unified_selection',engine.__dict__)


if __name__=='__main__':unittest.main()
