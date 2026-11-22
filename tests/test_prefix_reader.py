"""Exercise real pipe/subprocess failure modes without opening any disk."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'source'))
import ntfs_read as nt


@unittest.skipUnless(os.name=='posix','macOS/POSIX pipe lifecycle')
class PrefixReaderTests(unittest.TestCase):
    def run_reader(self,body,timeout=3):
        with tempfile.TemporaryDirectory() as folder:
            helper=Path(folder)/'reader.py'
            helper.write_text(body)
            actual=subprocess.Popen;children=[]
            def launch(command,**kwargs):
                child=actual([sys.executable,'-I','-B',str(helper)],**kwargs)
                children.append(child);return child
            start=time.monotonic()
            try:
                with patch.object(nt.subprocess,'Popen',side_effect=launch):
                    return nt.raw_prefix_read('fixture','/not-a-disk','/Windows/System32/config/SYSTEM',4096,timeout)
            finally:
                self.assertLess(time.monotonic()-start,8)
                for child in children:
                    if child.poll() is None:
                        child.kill();child.wait(timeout=3)
                        self.fail('Reader was left running')
                    self.assertTrue(child.stdout.closed)

    def test_full_pipe_with_ignored_termination_is_reaped(self):
        data=self.run_reader('''import os,signal,time
signal.signal(signal.SIGTERM,signal.SIG_IGN)
signal.signal(signal.SIGPIPE,signal.SIG_IGN)
os.write(1,b'regf'+bytes(4092))
while True:
    try:os.write(1,b'x'*65536)
    except BrokenPipeError:time.sleep(0.01)
''')
        self.assertEqual(data,b'regf'+bytes(4092))

    def test_no_output_times_out_and_reaps(self):
        with self.assertRaisesRegex(nt.Refusal,'Timed out reading'):
            self.run_reader('import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)',timeout=0.25)

    def test_short_success_is_returned_for_callers_validation(self):
        self.assertEqual(self.run_reader("import os;os.write(1,b'short')"),b'short')

    def test_short_failure_reports_stderr(self):
        with self.assertRaisesRegex(nt.Refusal,'fixture failure'):
            self.run_reader("import sys;sys.stderr.write('fixture failure');sys.exit(3)")

    def test_eof_without_exit_is_bounded(self):
        with self.assertRaisesRegex(nt.Refusal,'Timed out waiting'):
            self.run_reader('import os,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);os.close(1);time.sleep(30)',timeout=0.25)

if __name__=='__main__':unittest.main()
