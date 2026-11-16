"""Simulate backend completion/cleanup; no root process or installer runs."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import plistlib
import unittest
from unittest.mock import patch

import gui_worker as worker
import install_coexistence
import manage_installations
import format_installation
import install_multiboot
import rebuild_menu
import reset_menu
import install_unified


class WorkerTests(unittest.TestCase):
    def test_workflow_routing_and_source_rules(self):
        for case, operation, workflow, prepared, expected in (
                ('win10', 'install', 'win10-coexistence-private', '', 'win10'),
                ('win10-reject-win7-media', 'install', 'win10-coexistence-private', 'unexpected', None),
                ('win7', 'install', 'private-win7', str(Path.cwd()/'media'), 'win7'),
                ('win7-needs-media', 'install', 'private-win7', '', None),
                ('manage', 'manage', 'win10-coexistence-private', '', 'manage'),
                ('multiboot-iso-only', 'install', 'multiboot-lifecycle', '', 'multiboot'),
                ('multiboot-reject-prepared', 'install', 'multiboot-lifecycle', 'unexpected', None),
                ('rebuild', 'rebuild', 'multiboot-lifecycle', '', 'rebuild'),
                ('reset-menu', 'reset-menu', 'multiboot-lifecycle', '', 'reset-menu'),
                ('unified-iso', 'install', 'goawaybca', '', 'unified'),
                ('unified-reject-prepared', 'install', 'goawaybca', 'unexpected', None),
                ('unified-manage', 'manage', 'goawaybca', '', 'manage'),
                ('unified-rebuild', 'rebuild', 'goawaybca', '', 'rebuild'),
                ('unified-reset', 'reset-menu', 'goawaybca', '', 'reset-menu'),
                ('manage-cannot-install', 'install', 'win10-coexistence-private', '', None)):
            with self.subTest(case=case), ExitStack() as stack:
                calls=[]; messages=[]
                config=dict(installation_enabled=True, management_enabled=True,workflow=workflow,
                    profiles=[dict(id='fixture',minimum='10.15',maximum='26.99',available=True,
                        architectures=['x86_64'],wim='tools/wimlib-imagex')])
                class Connection:
                    def close(self): calls.append('closed')
                def fake_installer(name):
                    class Fake:
                        session=Path('invented-session');formatted=False
                        def __init__(self,options,**_):
                            calls.append(name)
                            self_test.assertEqual(_['interaction'].user_uid,501)
                            if name in ('win10','multiboot','unified'):self_test.assertIsNone(options.prepared_media)
                            if workflow=='goawaybca':self_test.assertTrue(options.allow_rename)
                        def execute(self): calls.append('execute')
                        def close(self): calls.append('cleanup');return []
                        def note(self,_):pass
                    return Fake
                self_test=self
                def read_bytes(path):
                    return plistlib.dumps(dict(ProductVersion='26.7.1') if path.name=='SystemVersion.plist' else config)
                args=argparse.Namespace(socket='/invented',token='0'*64,profile='fixture',
                    operation='manage' if case=='manage-cannot-install' else operation)
                for owner,name,value in (
                    (worker.sys,'platform','darwin'),(worker.os,'geteuid',lambda:0),
                    (worker.argparse.ArgumentParser,'parse_args',lambda *_:args),
                    (worker.Path,'read_bytes',read_bytes),
                    (worker.subprocess,'check_output',lambda cmd,**_: 'x86_64\n' if cmd[0]=='/usr/bin/uname' else b'MacBookPro11,5\n'),
                    (worker,'validate_payload',lambda *_:None),(worker,'connect_gui',lambda *_:(Connection(),501)),
                    (worker,'receive',lambda _:dict(type='configuration',operation=operation,
                        iso=str(Path.cwd()/'invented.iso'),prepared_media=prepared)),
                    (worker,'send',lambda _,message:messages.append(message)),
                    (worker,'Installer',fake_installer('win7')),
                    (install_coexistence,'CoexistenceInstaller',fake_installer('win10')),
                    (format_installation,'FormatInstallation',fake_installer('manage')),
                    (install_multiboot,'MultiBootInstaller',fake_installer('multiboot')),
                    (install_unified,'UnifiedInstaller',fake_installer('unified')),
                    (rebuild_menu,'RebuildMenu',fake_installer('rebuild')),
                    (reset_menu,'ResetMenu',fake_installer('reset-menu'))):
                    stack.enter_context(patch.object(owner,name,value,create=True))
                self.assertEqual(worker.main(),0 if expected else 1)
                self.assertEqual(calls,[expected,'execute','cleanup','closed'] if expected else ['closed'])
                self.assertEqual(messages[-1]['success'],bool(expected))

    def test_done_is_sent_after_cleanup_even_on_failure(self):
        for case in ('success', 'execute-fails', 'cleanup-needs-attention'):
            with self.subTest(case=case), ExitStack() as stack:
                events = []
                configuration = dict(installation_enabled=True, profiles=[dict(id='fixture',
                    minimum='10.15', maximum='26.99', available=True, architectures=['x86_64'],
                    wim='not-real-tools/wimlib-imagex')])
                class Connection:
                    def close(self):
                        events.append('connection-close')
                class Installer:
                    session = Path('invented-session')
                    formatted = False
                    def __init__(self, *_args, **_kwargs):
                        pass
                    def execute(self):
                        events.append('execute')
                        if case == 'execute-fails':
                            raise ValueError('invented failure')
                    def close(self):
                        events.append('cleanup')
                        return ['invented cleanup issue'] if case == 'cleanup-needs-attention' else []
                    def note(self, _message):
                        pass
                done = []
                def send(_connection, message):
                    self.assertEqual(events[-1], 'cleanup')
                    events.append('done')
                    done.append(message)
                def read_bytes(path):
                    value = dict(ProductVersion='26.7.1') if path.name == 'SystemVersion.plist' else configuration
                    return plistlib.dumps(value)
                def check_output(command, **kwargs):
                    return 'x86_64\n' if command[0] == '/usr/bin/uname' else b'MacBookPro11,5\n'
                options = argparse.Namespace(socket='/not-a-socket', token='0'*64, profile='fixture')
                for owner, name, replacement in (
                    (worker.sys, 'platform', 'darwin'), (worker.os, 'geteuid', lambda: 0),
                    (worker.argparse.ArgumentParser, 'parse_args', lambda *_: options),
                    (worker.Path, 'read_bytes', read_bytes),
                    (worker.subprocess, 'check_output', check_output),
                    (worker, 'validate_payload', lambda *_: None),
                    (worker, 'connect_gui', lambda *_: (Connection(),501)),
                    (worker, 'receive', lambda _: dict(type='configuration',
                        iso=str(Path.cwd() / 'invented.iso'), prepared_media=str(Path.cwd() / 'invented-media'))),
                    (worker, 'send', send), (worker, 'Installer', Installer)):
                    stack.enter_context(patch.object(owner, name, replacement, create=True))
                self.assertEqual(worker.main(), 0 if case == 'success' else 1)
                self.assertEqual(events, ['execute', 'cleanup', 'done', 'connection-close'])
                self.assertEqual(done[0]['success'], case == 'success')


if __name__ == '__main__':
    unittest.main(verbosity=2)
