"""Exercise rebuilt hivex on new synthetic hives with frozen app code.

Runs on Python 3.7+; all hives are disposable files below a NEW output directory.
The accepted library creates fixtures and supplies a comparison implementation.
Windows Offreg independently checks the resulting hives in the controller audit.
"""
import argparse
import ctypes as c
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

REFERENCE_SHA = '7f527fc5e5d72eb6bc390151c4816ef1350b3ff1604a409331b66d9d60fcfcc1'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def text(s):
    return (s + '\0').encode('utf-16le')


def snapshot(module, library, path):
    hive = module.Hive(library, path)
    try:
        return hive.snapshot()
    finally:
        hive.close()


def canonical(value):
    return {'\\'.join(key): {name: list(data) for name, data in values.items()}
            for key, values in value.items()}


def make_fixture(module, library, minimal, destination, kind, path_data, root_data):
    hive = module.Hive(library, minimal, True)
    try:
        function = hive.lib.hivex_node_add_child
        function.argtypes = [c.c_void_p, c.c_size_t, c.c_char_p]
        function.restype = c.c_size_t

        def node(parts):
            current = hive.lib.hivex_root(hive.handle)
            for name in parts:
                existing = hive.lib.hivex_node_get_child(hive.handle, current, name.encode('utf-8'))
                current = existing or function(hive.handle, current, name.encode('utf-8'))
                assert current, (parts, c.get_errno())
            return current

        def value(parts, name, typ, data):
            buffer = c.create_string_buffer(data)
            change = module.Value(name.encode('utf-8'), typ, len(data), c.cast(buffer, c.c_void_p))
            assert hive.lib.hivex_node_set_value(hive.handle, node(parts), c.byref(change), 0) == 0

        value(module.KEY, module.VALUE, kind, path_data)
        value(('Microsoft', 'Windows NT', 'CurrentVersion'), 'SystemRoot', 1, root_data)
        value(('Unrelated', 'Unicode-\u00e9'), 'Text-\u00a3', 1, text('Preserve \u00e9 \u03a9 \u65e5\u672c'))
        value(('Unrelated',), '', 1, text('default value'))
        value(('Unrelated',), 'Empty', 3, b'')
        value(('Unrelated',), 'Binary', 3, bytes(bytearray(range(256))) * 32)
        value(('Unrelated',), 'Dword', 4, (0x12345678).to_bytes(4, 'little'))
        value(('Unrelated',), 'Qword', 11, (0x123456789abcdef0).to_bytes(8, 'little'))
        value(('Unrelated',), 'Multiple', 7, text('first\0second\0'))
        node(('Unrelated', 'EmptyKey'))
        for number in range(160):
            value(('Unrelated', 'Tree', 'Node-%03d' % number), 'Data', 3,
                  hashlib.sha256(str(number).encode()).digest())
        assert hive.lib.hivex_commit(hive.handle, os.fsencode(destination), 0) == 0
    finally:
        hive.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--reference-resources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert platform.system() == 'Darwin' and os.geteuid() != 0
    build = args.build.resolve(strict=True)
    reference = args.reference_resources.resolve(strict=True)
    out = args.output.absolute()
    assert out.parent.resolve(strict=True) == out.parent and not out.exists() and not out.is_symlink()
    receipt = json.loads((build / 'BUILD-RESULT.json').read_text())
    assert receipt['status'] == 'PASS'
    built = Path(receipt['library']['path'])
    assert built.is_file() and digest(built) == receipt['library']['sha256']
    old = reference / 'lib/libhivex.0.dylib'
    assert digest(old) == REFERENCE_SHA
    module_file = reference / 'offline_keyboard.py'
    spec = importlib.util.spec_from_file_location('frozen_offline_keyboard', str(module_file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    os.umask(0o077)
    out.mkdir(mode=0o700)
    (out / 'logs').mkdir()
    relocated = out / 'relocated library with spaces'
    relocated.mkdir()
    library = relocated / built.name
    shutil.copy2(str(built), str(library))
    minimal = build / 'sources/hivex-1.3.24/images/minimal'
    before_inputs = {str(p): digest(p) for p in (old, built, module_file, minimal)}
    report = dict(status='RUNNING', runtime=sys.version, interpreter=sys.executable,
                  module_sha256=digest(module_file), library_sha256=digest(library),
                  reference_library_sha256=REFERENCE_SHA, cases=[], refusals=[],
                  installed_hives_accessed=False, reference_modified=False,
                  limits=['Synthetic files, not Windows first-boot/hardware validation',
                          'Only current macOS host tested, not Lion or other historical hosts',
                          'Functional parity is not whole-file reproducibility'])
    report_path = out / 'ACCEPTANCE-RESULT.json'

    def run(command, label):
        result = subprocess.run([str(x) for x in command], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=120)
        (out / 'logs' / (label + '.txt')).write_bytes(result.stdout + b'\nSTDERR:\n' + result.stderr)
        assert result.returncode == 0, (label, result.returncode, result.stderr)
        return result.stdout.decode()

    try:
        links = run(['/usr/bin/otool', '-L', library], 'links')
        dependencies = [line.strip().split(' (', 1)[0] for line in links.splitlines()[1:]]
        assert dependencies[0] == '@loader_path/libhivex.0.dylib'
        assert all(x.startswith('/usr/lib/') for x in dependencies[1:])
        commands = run(['/usr/bin/otool', '-l', library], 'load-commands')
        minimum = re.findall(r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\S+)', commands)
        assert minimum == ['10.7'], minimum
        assert run(['/usr/bin/lipo', '-archs', library], 'architecture').strip() == 'x86_64'
        symbols = sorted(set(run(['/usr/bin/nm', '-ju', library], 'imports').splitlines()))
        old_symbols = sorted(set(run(['/usr/bin/nm', '-ju', old], 'reference-imports').splitlines()))
        exports = sorted(set(run(['/usr/bin/nm', '-gjU', library], 'exports').splitlines()))
        old_exports = sorted(set(run(['/usr/bin/nm', '-gjU', old], 'reference-exports').splitlines()))
        assert not (set(symbols) & {'_open_memstream', '_futimens', '_utimensat', '_clock_gettime', '_getentropy'})
        report['binary'] = dict(dependencies=dependencies, deployment_target=minimum[0],
                               imports_match=old_symbols == symbols,
                               exports_match=old_exports == exports,
                               byte_identical=digest(old) == digest(library),
                               added_imports=sorted(set(symbols)-set(old_symbols)),
                               removed_imports=sorted(set(old_symbols)-set(symbols)))
        assert old_symbols == symbols and old_exports == exports, 'Review changed library ABI/imports'
        valid = [
            ('expand', 2, text(r'%SystemRoot%\inf;C:\Other'), text(r'C:\Windows'),
             r'%SystemRoot%\inf;C:\Other;%SystemDrive%\WinDeploy-InboxKeyboard'),
            ('literal-other-drive', 1, text(r'D:\Windows\Inf'), text(r'D:\Windows'),
             r'D:\Windows\Inf;D:\WinDeploy-InboxKeyboard'),
            ('already-present', 2, text(r'%SystemRoot%\Inf;%SystemDrive%\WinDeploy-InboxKeyboard'),
             text(r'C:\Windows'), r'%SystemRoot%\Inf;%SystemDrive%\WinDeploy-InboxKeyboard'),
        ]
        for name, kind, data, root, expected in valid:
            part = out / name
            part.mkdir()
            source = part / 'SOFTWARE.source'
            make_fixture(module, old, minimal, source, kind, data, root)
            source_hash = digest(source)
            baseline = snapshot(module, old, source)
            assert snapshot(module, library, source) == baseline
            prepared, reference_output, repeated = (part / x for x in ('SOFTWARE.prepared', 'SOFTWARE.reference', 'SOFTWARE.repeated'))
            result = module.prepare(library, source, prepared)
            module.prepare(old, source, reference_output)
            module.prepare(library, prepared, repeated)
            after = snapshot(module, library, prepared)
            intended = {key: dict(values) for key, values in baseline.items()}
            intended[module.KEY][module.VALUE] = (kind, len(text(expected)), hashlib.sha256(text(expected)).hexdigest())
            assert after == intended
            assert snapshot(module, old, prepared) == intended
            assert snapshot(module, library, reference_output) == intended
            assert snapshot(module, library, repeated) == intended
            assert digest(source) == source_hash
            (part / 'expected-snapshot.json').write_text(json.dumps(canonical(intended), indent=2) + '\n')
            row = dict(name=name, keys=len(baseline), values=sum(len(v) for v in baseline.values()),
                       source_sha256=source_hash, output_sha256=digest(prepared),
                       reference_semantics_equal=True, idempotent=True, result=result)
            report['cases'].append(row)
            print('PASS fixture ' + name, flush=True)
        invalid = [('wrong-type', 4, (1).to_bytes(4, 'little'), text(r'C:\Windows')),
                   ('missing-terminator', 2, r'%SystemRoot%\Inf'.encode('utf-16le'), text(r'C:\Windows')),
                   ('invalid-system-root', 2, text(r'%SystemRoot%\Inf'), text('relative'))]
        for name, kind, data, root in invalid:
            source = out / (name + '.source')
            destination = out / (name + '.output')
            make_fixture(module, old, minimal, source, kind, data, root)
            original = digest(source)
            try:
                module.prepare(library, source, destination)
                raise AssertionError('Invalid hive accepted: ' + name)
            except ValueError as error:
                assert digest(source) == original and not destination.exists()
                report['refusals'].append(dict(name=name, error=str(error), source_unchanged=True))
        source = out / 'expand/SOFTWARE.source'
        protected = out / 'preexisting.output'
        protected.write_bytes(b'preserve this existing destination')
        existing_hash = digest(protected)
        for name, src, dst in [('existing-output', source, protected), ('same-source-output', source, source)]:
            try:
                module.prepare(library, src, dst)
                raise AssertionError('Unsafe output accepted: ' + name)
            except ValueError as error:
                assert digest(protected) == existing_hash
                report['refusals'].append(dict(name=name, error=str(error)))
        assert all(digest(Path(path)) == sha for path, sha in before_inputs.items())
        report.update(status='PASS', original_inputs_unchanged=True,
                      independent_windows_offreg_audit_required=True)
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
