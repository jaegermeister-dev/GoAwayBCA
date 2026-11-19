import io
import json
import struct
import unittest
import zlib

import backup_archive as a
import backup_metadata as b
from test_backup_metadata import location


class ShortWriter(io.BytesIO):
    def write(self, data):
        return super().write(data[:17])


class Tests(unittest.TestCase):
    def metadata(self):
        return b.create(location(), dict(family='Windows 7', architecture=9), False)

    def archive(self, raw=b'used NTFS clusters' * 1024):
        stream = io.BytesIO()
        a.capture(io.BytesIO(raw), stream, self.metadata())
        return stream.getvalue()

    def test_stream_roundtrip_and_partial_writes(self):
        raw = b'NTFS content\x00' * 400000
        output = ShortWriter()
        saved = a.capture(io.BytesIO(raw), output, self.metadata())
        restored = ShortWriter()
        checked = a.verify(io.BytesIO(output.getvalue()), restored)
        self.assertEqual(raw, restored.getvalue())
        self.assertEqual(checked['content_sha256'], saved['content_sha256'])
        self.assertEqual(checked['chunks'], 2)
        self.assertFalse(checked['metadata']['uninstall_eligible'])

    def test_corruption_truncation_trailing_data_rejected(self):
        archive = self.archive()
        for value in (archive[:-1], archive + b'x', archive[:20], archive[:-40] + b'X' + archive[-39:]):
            with self.subTest(size=len(value)), self.assertRaises((ValueError, zlib.error)):
                a.verify(io.BytesIO(value))

    def test_wrong_header_and_oversized_metadata_rejected(self):
        for data in (b'other backup', a.MAGIC + struct.pack('<I', a.MAX_METADATA + 1)):
            with self.assertRaises(ValueError):
                a.verify(io.BytesIO(data))

    def test_bounded_decompression_rejects_declared_small_chunk(self):
        archive = self.archive(b'A' * a.BLOCK)
        stream = io.BytesIO(archive)
        a.header(stream)
        offset = stream.tell()
        damaged = archive[:offset] + struct.pack('<I', 1) + archive[offset + 4:]
        with self.assertRaises(ValueError):
            a.verify(io.BytesIO(damaged))

    def test_empty_input_never_produces_valid_backup(self):
        output = io.BytesIO()
        with self.assertRaises(ValueError):
            a.capture(io.BytesIO(), output, self.metadata())
        with self.assertRaises(ValueError):
            a.verify(io.BytesIO(output.getvalue()))

    def test_metadata_is_not_ownership_proof(self):
        checked = a.verify(io.BytesIO(self.archive()))
        self.assertEqual(checked['metadata']['origin']['kind'], 'external-or-unverified')
        self.assertFalse(checked['metadata']['uninstall_eligible'])

    def test_duplicate_metadata_keys_rejected(self):
        with self.assertRaises(ValueError):
            json.loads('{"x":1,"x":2}', object_pairs_hook=a.unique_object)

    def test_streaming_reader_crosses_chunk_boundaries_and_checks_footer(self):
        raw=b'full stream\x00'*400000
        archive=self.archive(raw)
        reader=a.ImageReader(io.BytesIO(archive))
        self.assertIsNone(reader.result)
        parts=[]
        while True:
            part=reader.read(313337)
            if not part:break
            parts.append(part)
        self.assertEqual(b''.join(parts),raw)
        self.assertEqual(reader.result,a.verify(io.BytesIO(archive)))
        self.assertEqual(reader.read(1),b'')
        for size in (-1,a.BLOCK+1,True):
            with self.assertRaises(ValueError):reader.read(size)

    def test_streaming_reader_never_accepts_damaged_footer(self):
        archive=self.archive()
        for data in (archive[:-1],archive+b'x',archive[:-40]+b'X'+archive[-39:]):
            reader=a.ImageReader(io.BytesIO(data))
            with self.assertRaises(ValueError):
                while reader.read(a.BLOCK):pass
            self.assertIsNone(reader.result)

    def test_failed_producer_never_creates_completion_record(self):
        output=io.BytesIO()
        def failed():raise ValueError('producer failed')
        with self.assertRaisesRegex(ValueError,'producer failed'):
            a.capture(io.BytesIO(b'some data'),output,self.metadata(),finish=failed)
        with self.assertRaises(ValueError):a.verify(io.BytesIO(output.getvalue()))
        reader=a.ImageReader(io.BytesIO(output.getvalue()))
        with self.assertRaises(ValueError):
            while reader.read(a.BLOCK):pass


if __name__ == '__main__':
    unittest.main()
