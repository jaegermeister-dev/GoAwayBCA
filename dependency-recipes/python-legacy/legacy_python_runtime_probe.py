"""Exercise ordinary runtime operations in caller-selected disposable scratch."""
import argparse
import bz2
import ctypes
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import tarfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
import zlib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.runtime.resolve(strict=True)
    out = args.output.absolute()
    assert out.parent.resolve(strict=True) == out.parent and not out.exists()
    assert Path(sys.prefix).resolve() == root and sys.version_info[:3] == (3, 7, 17)
    assert all(root in Path(p).resolve().parents or Path(p).resolve() == root for p in sys.path)
    out.mkdir(mode=0o700)
    data = ('Runtime fixture: Ελληνικά 日本語 £\n'.encode('utf-8') + bytes(range(256))) * 256
    original = out / 'original.bin'
    original.write_bytes(data)
    outputs = {}
    # gzip.compress gained its mtime argument after Python 3.7.
    gzip_buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=gzip_buffer, mode='wb', mtime=0) as stream:
        stream.write(data)
    for name, compressed, decoded in (
        ('zlib.bin', zlib.compress(data), zlib.decompress),
        ('bzip2.bin', bz2.compress(data), bz2.decompress),
        ('gzip.bin', gzip_buffer.getvalue(), gzip.decompress),
    ):
        assert decoded(compressed) == data
        (out / name).write_bytes(compressed)
        outputs[name] = dict(bytes=len(compressed), sha256=hashlib.sha256(compressed).hexdigest())
    document = {'name': 'λ fixture', 'bytes': b'\x00\xff\x01', 'number': 9223372036854775807,
                'flags': [True, False], 'nested': {'empty': ''}}
    for format_name, fmt in (('xml', plistlib.FMT_XML), ('binary', plistlib.FMT_BINARY)):
        encoded = plistlib.dumps(document, fmt=fmt)
        assert plistlib.loads(encoded) == document
        (out / ('fixture-' + format_name + '.plist')).write_bytes(encoded)
    assert json.loads(json.dumps({'text': '日本語', 'values': [1, None, True]}))['text'] == '日本語'
    assert ET.fromstring('<root><item value="ok" /></root>').find('item').get('value') == 'ok'
    with zipfile.ZipFile(str(out / 'fixture.zip'), 'w', compression=zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo('nested/日本語.bin', (1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, data)
    with zipfile.ZipFile(str(out / 'fixture.zip')) as z:
        assert z.testzip() is None and z.read('nested/日本語.bin') == data
    with tarfile.open(str(out / 'fixture.tar'), 'w') as t:
        info = tarfile.TarInfo('nested/日本語.bin')
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    with tarfile.open(str(out / 'fixture.tar')) as t:
        assert t.extractfile('nested/日本語.bin').read() == data
    db = sqlite3.connect(':memory:')
    db.execute('create table sample (value blob)')
    db.execute('insert into sample values (?)', (data,))
    assert db.execute('select value from sample').fetchone()[0] == data
    db.close()
    buffer = ctypes.create_string_buffer(data)
    assert ctypes.string_at(buffer, len(data)) == data
    assert len(os.urandom(32)) == 32 and uuid.uuid4().version == 4
    link = out / 'relative-link'
    link.symlink_to('original.bin')
    assert link.read_bytes() == data and os.readlink(str(link)) == 'original.bin'
    renamed = out / 'renamed.bin'
    renamed.write_bytes(b'old')
    replacement = out / 'replacement.bin'
    replacement.write_bytes(data)
    os.replace(str(replacement), str(renamed))
    assert renamed.read_bytes() == data and renamed.stat().st_size == len(data)
    child = subprocess.check_output([sys.executable, '-I', '-B', '-c',
        'import sys,hashlib;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'], input=data)
    assert child.decode().strip() == hashlib.sha256(data).hexdigest()
    modules = {}
    for name, module in list(sys.modules.items()):
        location = getattr(module, '__file__', None)
        if location and name != '__main__':
            path = Path(location).resolve()
            assert root in path.parents, (name, location)
            modules[name] = path.relative_to(root).as_posix()
    result = dict(status='PASS', version=sys.version, prefix=str(root),
        original_sha256=hashlib.sha256(data).hexdigest(), compressed=outputs,
        imported_modules=modules, outside_runtime_modules=False, system_install=False)
    (out / 'PROBE-RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(status='PASS', modules=len(modules), payload_bytes=len(data))))


if __name__ == '__main__':
    main()
