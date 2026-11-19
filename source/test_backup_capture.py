import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

import backup_archive
import backup_capture
import backup_metadata
from test_backup_metadata import location
from test_ntfsclone_image import fixture


class Tests(unittest.TestCase):
    def run_capture(self, sector=512, code=0, guard=None, raw=None):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        folder = Path(root.name)
        self.last_folder = folder
        meta = backup_metadata.create(dict(location(sector=sector), partition_bytes=8192),
                                     dict(family='Windows 10', architecture=9), False)
        child = Mock(stdout=io.BytesIO(fixture(sector) if raw is None else raw))
        child.wait.return_value = code; child.poll.return_value = code
        events = []
        with (folder/'output.gabackup').open('w+b') as output, \
                patch.object(backup_capture.subprocess, 'Popen', return_value=child) as launch, \
                patch.object(backup_capture, 'ProcessReader', side_effect=lambda stream, note: stream):
            result = backup_capture.capture('/fixture/ntfsclone', '/fixture/source.ntfs', output,
                meta, folder/'capture.log', guard or (lambda: events.append('guard')), events.append)
        self.assertEqual(launch.call_args[0][0], ['/fixture/ntfsclone', '--save-image', '--full-logfile',
                                               '--output', '-', '/fixture/source.ntfs'])
        return result, folder, events

    def test_complete_readback_both_geometries(self):
        for sector in (512,4096):
            result, folder, events = self.run_capture(sector)
            self.assertTrue(result['full_readback_verified'])
            self.assertFalse(result['source_modified'])
            self.assertFalse(result['restore_boot_tested'])
            self.assertEqual(result['geometry']['logical_sector_size'], sector)
            self.assertEqual(events.count('guard'), 3)
            with (folder/'output.gabackup').open('rb') as stream:
                self.assertFalse(backup_archive.verify(stream)['metadata']['uninstall_eligible'])

    def test_producer_failure_prevents_footer(self):
        with self.assertRaisesRegex(ValueError, 'could not finish'):
            self.run_capture(code=1)
        with (self.last_folder/'output.gabackup').open('rb') as stream, self.assertRaises(ValueError):
            backup_archive.verify(stream)

    def test_source_change_prevents_success(self):
        calls = [0]
        def changed():
            calls[0] += 1
            if calls[0] == 2: raise ValueError('Source replaced')
        with self.assertRaisesRegex(ValueError, 'Source replaced'):
            self.run_capture(guard=changed)
        with (self.last_folder/'output.gabackup').open('rb') as stream, self.assertRaises(ValueError):
            backup_archive.verify(stream)

    def test_invalid_filesystem_never_reports_success(self):
        with self.assertRaises(ValueError):
            self.run_capture(raw=fixture()[:-20])


if __name__ == '__main__': unittest.main()
