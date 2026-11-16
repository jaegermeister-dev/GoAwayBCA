import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from gui_protocol import GUIInteraction, receive, send
from gui_session import serve, run_child


def isolated_fixture(connection, ui, resources, config, profile, version, operation,
                     request, verified, close_connection):
    assert verified and not close_connection
    assert ui.user_uid==501
    ui.log('Isolated operation '+operation)
    (resources/'cleanup-finished').write_text(str(os.getpid()))
    send(connection,dict(type='done',success=True,text='Fixture cleaned up'))
    return 0


class Session(unittest.TestCase):
    def sequence(self,requests,successes=None):
        events=[];operations=[];caches=[]
        ui=type('UI',(),{'log':lambda *a:None})()
        def run(*args):
            operation=args[-1]['operation'];operations.append(operation)
            return dict(type='done',success=(successes or {}).get(operation,True),text='Fixture',
                        **({'drive_geometry':{'schema':1,'drives':{}}} if operation=='startup-check' else {}))
        def validate(*args):caches.append(args[-1])
        with patch('gui_session.receive',side_effect=requests+[ConnectionError('GUI closed')]), \
             patch('gui_session.send',side_effect=lambda c,e:events.append(e)), \
             patch('gui_session.validate_payload',side_effect=validate):
            result=serve(object(),ui,Path('.'),{}, {},'10.14.6',None,run)
        return result,events,operations,caches

    def test_multiple_operations_reuse_one_session_and_cache(self):
        requests=[dict(type='configuration',operation=x) for x in ('startup-check','manage','install','saved-files')]
        result,events,operations,caches=self.sequence(requests)
        self.assertEqual(result,0)
        self.assertEqual(operations,['startup-check','manage','install','saved-files'])
        self.assertTrue(all(e['session_alive'] for e in events))
        self.assertTrue(all(c is caches[0] for c in caches))

    def test_install_cannot_bypass_launch_check(self):
        result,events,operations,_=self.sequence([dict(type='configuration',operation='install')])
        self.assertEqual(result,1);self.assertEqual(operations,[])
        self.assertFalse(events[-1]['session_alive'])

    def test_failed_startup_ends_session_before_next_operation(self):
        _,events,operations,_=self.sequence([dict(type='configuration',operation=x) for x in ('startup-check','install')],
            {'startup-check':False})
        self.assertEqual(operations,['startup-check']);self.assertFalse(events[-1]['session_alive'])

    def test_cancelled_operation_does_not_require_new_authorization(self):
        _,events,operations,_=self.sequence([dict(type='configuration',operation=x) for x in ('startup-check','install','manage')],
            {'install':False})
        self.assertEqual(operations,['startup-check','install','manage'])
        self.assertFalse(events[1]['success']);self.assertTrue(events[1]['session_alive'])

    def test_malformed_or_unknown_operation_ends_session(self):
        for request in (dict(type='command',command='anything'),dict(type='configuration',operation='shell')):
            result,events,operations,_=self.sequence([request])
            self.assertEqual(result,1);self.assertEqual(operations,[])
            self.assertFalse(events[-1]['session_alive'])

    @unittest.skipUnless(hasattr(os,'fork'),'Operation isolation uses macOS fork')
    def test_real_child_exits_before_completion_is_returned(self):
        with tempfile.TemporaryDirectory() as temp:
            gui,worker=socket.socketpair()
            try:
                gui.settimeout(5);worker.settimeout(5)
                ui=GUIInteraction(worker);ui.user_uid=501
                done=run_child(isolated_fixture,worker,ui,Path(temp),{}, {},'10.14.6',
                               dict(type='configuration',operation='manage'))
                self.assertTrue(done['success'])
                pid=int((Path(temp)/'cleanup-finished').read_text())
                self.assertNotEqual(pid,os.getpid())
                with self.assertRaises(ChildProcessError):os.waitpid(pid,os.WNOHANG)
                self.assertEqual(receive(gui)['text'],'Isolated operation manage')
            finally:gui.close();worker.close()


if __name__=='__main__':unittest.main()
