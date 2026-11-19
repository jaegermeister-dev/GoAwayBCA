"""Regular-file tests, including no-overwrite and no-follow behaviour."""
import os
from pathlib import Path
import tempfile
import unittest
import sys

if sys.platform=='darwin':
    import backup_file_access


@unittest.skipUnless(sys.platform=='darwin' and os.geteuid()!=0,'Ordinary-user macOS fixture only')
class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='goawaybca-backup-file-')
        self.root=Path(self.temp.name).resolve()
    def tearDown(self):self.temp.cleanup()

    def test_new_output_and_readback(self):
        path=str(self.root/'fixture.gabackup')
        with backup_file_access.open_backup(os.getuid(),path,True) as output:
            output.write(b'fixture');output.flush();os.fsync(output.fileno())
        with backup_file_access.open_backup(os.getuid(),path) as source:
            self.assertEqual(source.read(),b'fixture')
        self.assertEqual(os.stat(path).st_uid,os.getuid())
        self.assertEqual(os.stat(path).st_mode & 0o777,0o600)

    def test_existing_file_and_symlinks_are_never_overwritten(self):
        original=self.root/'keep';original.write_bytes(b'preserve')
        (self.root/'alias').symlink_to('keep')
        for path in (original,self.root/'alias'):
            with self.assertRaises(ValueError):
                with backup_file_access.open_backup(os.getuid(),str(path),True):pass
        with self.assertRaises(ValueError):
            with backup_file_access.open_backup(os.getuid(),str(self.root/'alias')):pass
        self.assertEqual(original.read_bytes(),b'preserve')

    def test_symlink_directory_and_special_file_refused(self):
        folder=self.root/'folder';folder.mkdir();(self.root/'alias').symlink_to('folder')
        with self.assertRaises(ValueError):
            with backup_file_access.open_backup(os.getuid(),str(self.root/'alias/new'),True):pass
        fifo=self.root/'pipe';os.mkfifo(str(fifo))
        with self.assertRaises(ValueError):
            with backup_file_access.open_backup(os.getuid(),str(fifo)):pass


if __name__=='__main__':unittest.main()
