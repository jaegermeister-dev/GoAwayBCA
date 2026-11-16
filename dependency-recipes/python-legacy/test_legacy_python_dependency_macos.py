"""Compare rebuilt runtime binaries, relocation and the frozen app's unit suite."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

REFERENCE_SHA = '647754d892b9c2c4f4d99ef0ecd799cc56fa7b7fcdc2319a8e9f01c7bf9ed3c5'
MAGICS = (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def natives(root):
    result = {}
    for path in root.rglob('*'):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as stream:
                if stream.read(4) in MAGICS:
                    result[path.relative_to(root).as_posix()] = path
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build', 'reference-resources', 'reference-source', 'test-list', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    assert platform.system() == 'Darwin' and os.geteuid() != 0
    build = args.build.resolve(strict=True)
    resources = args.reference_resources.resolve(strict=True)
    source = args.reference_source.resolve(strict=True)
    tests = json.loads(args.test_list.read_text())
    assert len(tests) == 74 and all(re.fullmatch('test_[a-z0-9_]+', t) for t in tests)
    result = json.loads((build / 'BUILD-RESULT.json').read_text())
    assert result['status'] == 'PASS'
    built = Path(result['runtime']).resolve(strict=True)
    assert digest(built / 'bin/python3.7') == result['python']['sha256']
    old = resources / 'payloads/legacy/python'
    assert digest(old / 'bin/python3.7') == REFERENCE_SHA
    out = args.output.absolute()
    assert out.parent.resolve(strict=True) == out.parent and not out.exists()
    os.umask(0o077)
    out.mkdir(mode=0o700)
    (out / 'logs').mkdir()
    (out / 'tmp').mkdir()
    (out / 'native-binaries').mkdir()
    relocated = out / 'relocated runtime with spaces'
    shutil.copytree(built, relocated, symlinks=True)
    env = dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin', HOME=os.environ['HOME'],
               TMPDIR=str(out / 'tmp'), LC_ALL='en_US.UTF-8', PYTHONDONTWRITEBYTECODE='1')
    report = dict(status='RUNNING', binaries=[], python_sha256=result['python']['sha256'],
        reference_sha256=REFERENCE_SHA, app_unit_tests=None, historical_os_tested=False,
        installed_runtime_modified=False, installer_executed=False, efi_modified=False)
    receipt = out / 'ACCEPTANCE-RESULT.json'

    def run(label, argv, cwd=out, timeout=600):
        completed = subprocess.run([str(v) for v in argv], cwd=cwd, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        (out / 'logs' / (label + '.txt')).write_bytes(
            ('COMMAND: ' + repr([str(v) for v in argv]) + '\nEXIT: ' + str(completed.returncode) + '\n').encode()
            + completed.stdout + b'\nSTDERR:\n' + completed.stderr)
        assert completed.returncode == 0, (label, completed.stderr[-6000:])
        return completed.stdout.decode('utf-8'), completed.stderr.decode('utf-8')

    try:
        new_natives, old_natives = natives(relocated), natives(old)
        assert set(new_natives) == set(old_natives), (set(new_natives)-set(old_natives), set(old_natives)-set(new_natives))
        # r10 contains 62 extension modules, two regular executable entries,
        # and the installed developer object config-3.7m-darwin/python.o.
        assert len(new_natives) == 65
        assert 'lib/python3.7/config-3.7m-darwin/python.o' in new_natives
        for index, (name, binary) in enumerate(sorted(new_natives.items())):
            prefix = '%03d' % index
            imports, _ = run(prefix + '-imports', ['/usr/bin/nm', '-ju', binary])
            old_imports, _ = run(prefix + '-reference-imports', ['/usr/bin/nm', '-ju', old_natives[name]])
            links, _ = run(prefix + '-links', ['/usr/bin/otool', '-L', binary])
            dependencies = [line.strip().split(' (', 1)[0] for line in links.splitlines()[1:]]
            assert all(p.startswith(('/usr/lib/', '/System/Library/')) for p in dependencies), (name, dependencies)
            commands, _ = run(prefix + '-load-commands', ['/usr/bin/otool', '-l', binary])
            minimum = re.findall(r'\bminos\s+(\S+)', commands) or re.findall(
                r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\S+)', commands)
            assert minimum == ['10.7'], (name, minimum)
            assert run(prefix + '-architecture', ['/usr/bin/lipo', '-archs', binary])[0].strip() == 'x86_64'
            row = dict(path=name, bytes=binary.stat().st_size, sha256=digest(binary),
                reference_sha256=digest(old_natives[name]), dependencies=dependencies,
                imports_match=set(imports.splitlines()) == set(old_imports.splitlines()),
                added_imports=sorted(set(imports.splitlines())-set(old_imports.splitlines())),
                removed_imports=sorted(set(old_imports.splitlines())-set(imports.splitlines())),
                minimum=minimum[0], byte_identical=digest(binary) == digest(old_natives[name]))
            row['evidence_file'] = 'native-binaries/' + prefix + '.bin'
            shutil.copy2(binary, out / row['evidence_file'])
            report['binaries'].append(row)
            assert row['imports_match'], row
        print('PASS native runtime audit: 65 Mach-O entries', flush=True)
        probe = Path(__file__).with_name('legacy_python_runtime_probe.py')
        for label, runtime in (('rebuilt', relocated), ('reference', old)):
            run(label + '-runtime-probe', [runtime / 'bin/python3.7', '-I', '-B', probe,
                '--runtime', runtime, '--output', out / (label + '-probe')])
        new_probe = json.loads((out / 'rebuilt-probe/PROBE-RESULT.json').read_text())
        old_probe = json.loads((out / 'reference-probe/PROBE-RESULT.json').read_text())
        assert new_probe['compressed'] == old_probe['compressed']
        assert new_probe['original_sha256'] == old_probe['original_sha256']
        assert set(new_probe['imported_modules']) == set(old_probe['imported_modules'])
        code = ('import sys,unittest;sys.path[:0]=sys.argv[1:3];'
                'unittest.main(module=None,argv=["packaged"]+sys.argv[3:])')
        stdout, stderr = run('frozen-app-unit-tests', [relocated / 'bin/python3.7', '-I', '-B', '-c',
            code, resources, source] + tests, cwd=source, timeout=900)
        text = stdout + stderr
        assert re.findall(r'Ran (\d+) tests? in', text) == ['609']
        assert re.search(r'^OK \(skipped=3\)$', text, re.M)
        report['app_unit_tests'] = dict(tests=609, skipped=3, status='PASS', historical_os_tested=False)
        print('PASS frozen application suite: 609 tests, 3 skips', flush=True)
        report.update(status='PASS', runtime_relocation_passed=True,
            probe_modules=len(new_probe['imported_modules']), original_runtime_unchanged=True,
            byte_identical_count=sum(row['byte_identical'] for row in report['binaries']),
            previous_app_needed_for_build=False)
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        receipt.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
