"""Compare a rebuilt modern runtime with frozen r10 in isolated user scratch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

REFERENCE_SHA = '7a5ea13746d2fd198f1ec102d3aaebf86bc66428362f45a0085dadfb74cdeca3'
MAGICS = (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def natives(root):
    found = {}
    for p in root.rglob('*'):
        if p.is_file() and not p.is_symlink():
            with p.open('rb') as stream:
                if stream.read(4) in MAGICS:
                    found[p.relative_to(root).as_posix()] = p
    return found


def load_paths(commands, kind, field):
    return re.findall(r'cmd ' + kind + r'\s+cmdsize \d+\s+' + field + r' (.*?) \(offset \d+\)', commands)


def validate_links(dependencies, commands, binary, executable, executable_commands, root):
    ids = load_paths(commands, 'LC_ID_DYLIB', 'name')
    rpaths = [(p, binary.parent) for p in load_paths(commands, 'LC_RPATH', 'path')]
    rpaths += [(p, executable.parent) for p in load_paths(executable_commands, 'LC_RPATH', 'path')]

    def expand(path, loader):
        return path.replace('@loader_path', str(loader)).replace('@executable_path', str(executable.parent))

    for dependency in dependencies:
        if dependency in ids or dependency.startswith(('/usr/lib/', '/System/Library/')):
            continue
        if dependency.startswith('@rpath/'):
            candidates = [Path(expand(rp, loader)) / dependency.removeprefix('@rpath/')
                          for rp, loader in rpaths]
        else:
            candidates = [Path(expand(dependency, binary.parent))]
        matches = [p.resolve() for p in candidates if p.is_file()]
        assert matches and any(root in p.parents for p in matches), (binary, dependency, candidates)
        assert all(root in p.parents for p in matches), (binary, dependency, matches)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'reference-resources', 'reference-source', 'test-list', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    assert platform.system() == 'Darwin' and os.geteuid() != 0
    prepared = args.prepared.resolve(strict=True)
    unpack = json.loads((prepared / 'UNPACK-RESULT.json').read_text())
    assert unpack['status'] == 'PASS'
    runtime = Path(unpack['runtime']).resolve(strict=True)
    assert runtime.parent == prepared
    executable = runtime / 'bin/python3.11'
    assert digest(executable) == unpack['python_sha256']
    resources = args.reference_resources.resolve(strict=True)
    source = args.reference_source.resolve(strict=True)
    old = resources / 'payloads/modern/python'
    assert digest(old / 'bin/python3.11') == REFERENCE_SHA
    tests = json.loads(args.test_list.read_text())
    assert len(tests) == 74 and all(re.fullmatch('test_[a-z0-9_]+', t) for t in tests)
    out = args.output.absolute()
    assert out.parent.resolve(strict=True) == out.parent and not out.exists()
    os.umask(0o077)
    out.mkdir(mode=0o700)
    for name in ('logs', 'tmp', 'native-binaries', 'tls-fixture'):
        (out / name).mkdir()
    env = dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin', HOME=os.environ['HOME'],
               TMPDIR=str(out / 'tmp'), LC_ALL='en_US.UTF-8', PYTHONDONTWRITEBYTECODE='1')
    report = dict(status='RUNNING', binaries=[], runtime=str(runtime),
                  app_unit_tests=None, historical_os_tested=False,
                  installed_runtime_modified=False, installer_executed=False, efi_modified=False)

    def run(label, command, cwd=out, timeout=600):
        completed = subprocess.run([str(x) for x in command], cwd=cwd, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        (out / 'logs' / (label + '.txt')).write_bytes(
            ('COMMAND: ' + repr([str(x) for x in command]) + '\nEXIT: ' + str(completed.returncode) + '\n').encode()
            + completed.stdout + b'\nSTDERR:\n' + completed.stderr)
        assert completed.returncode == 0, (label, completed.stderr[-6000:])
        return completed.stdout.decode('utf-8'), completed.stderr.decode('utf-8')

    try:
        new_natives, old_natives = natives(runtime), natives(old)
        assert set(new_natives) == set(old_natives), (set(new_natives)-set(old_natives), set(old_natives)-set(new_natives))
        executable_commands = run('executable-load-commands', ['/usr/bin/otool', '-l', executable])[0]
        for index, (name, binary) in enumerate(sorted(new_natives.items())):
            prefix = '%03d' % index
            imports = run(prefix + '-imports', ['/usr/bin/nm', '-ju', binary])[0]
            old_imports = run(prefix + '-reference-imports', ['/usr/bin/nm', '-ju', old_natives[name]])[0]
            links = run(prefix + '-links', ['/usr/bin/otool', '-L', binary])[0]
            dependencies = [line.strip().split(' (', 1)[0] for line in links.splitlines()[1:]]
            commands = run(prefix + '-load-commands', ['/usr/bin/otool', '-l', binary])[0]
            validate_links(dependencies, commands, binary, executable, executable_commands, runtime)
            minimum = re.findall(r'\bminos\s+(\S+)', commands) or re.findall(
                r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\S+)', commands)
            assert len(minimum) == 1 and tuple(map(int, minimum[0].split('.'))) <= (10, 15, 0), (name, minimum)
            assert run(prefix + '-architecture', ['/usr/bin/lipo', '-archs', binary])[0].strip() == 'x86_64'
            row = dict(path=name, bytes=binary.stat().st_size, sha256=digest(binary),
                       reference_sha256=digest(old_natives[name]), dependencies=dependencies,
                       imports_match=set(imports.splitlines()) == set(old_imports.splitlines()),
                       added_imports=sorted(set(imports.splitlines())-set(old_imports.splitlines())),
                       removed_imports=sorted(set(old_imports.splitlines())-set(imports.splitlines())),
                       minimum=minimum[0], byte_identical=digest(binary) == digest(old_natives[name]),
                       evidence_file='native-binaries/' + prefix + '.bin')
            shutil.copy2(binary, out / row['evidence_file'])
            report['binaries'].append(row)
        # Record import differences for an independent SDK/toolchain review.
        # Functional success alone must not silently approve a runtime replacement.
        report['import_review_required'] = any(not row['imports_match'] for row in report['binaries'])
        print('PASS runtime structure: %d native files; import review required: %s' %
              (len(new_natives), report['import_review_required']), flush=True)
        fixture = out / 'tls-fixture'
        config = fixture / 'certificate.conf'
        config.write_text('[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n'
                          '[dn]\nCN=runtime-fixture.local\n[ext]\n'
                          'subjectAltName=DNS:runtime-fixture.local\n'
                          'basicConstraints=critical,CA:TRUE\n'
                          'keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n'
                          'extendedKeyUsage=serverAuth\n')
        run('make-disposable-tls-fixture', ['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048',
            '-nodes', '-sha256', '-days', '1', '-config', config,
            '-keyout', fixture / 'key.pem', '-out', fixture / 'cert.pem'])
        probe = Path(__file__).with_name('modern_python_runtime_probe.py')
        for label, root in (('rebuilt', runtime), ('reference', old)):
            run(label + '-runtime-probe', [root / 'bin/python3.11', '-I', '-B', probe,
                '--runtime', root, '--tls-fixture', fixture, '--output', out / (label + '-probe')])
        new_probe = json.loads((out / 'rebuilt-probe/PROBE-RESULT.json').read_text())
        old_probe = json.loads((out / 'reference-probe/PROBE-RESULT.json').read_text())
        assert new_probe['original_sha256'] == old_probe['original_sha256']
        assert set(new_probe['imported_modules']) == set(old_probe['imported_modules'])
        report['compressed_output_hashes_match'] = new_probe['compressed'] == old_probe['compressed']
        code = ('import sys,unittest;sys.path[:0]=sys.argv[1:3];'
                'unittest.main(module=None,argv=["packaged"]+sys.argv[3:])')
        stdout, stderr = run('frozen-app-unit-tests', [executable, '-I', '-B', '-c', code,
            resources, source] + tests, cwd=source, timeout=900)
        text = stdout + stderr
        assert re.findall(r'Ran (\d+) tests? in', text) == ['609']
        assert re.search(r'^OK \(skipped=3\)$', text, re.M)
        report.update(status='PASS', app_unit_tests=dict(tests=609, skipped=3, status='PASS'),
                      runtime_relocation_passed=True, probe_modules=len(new_probe['imported_modules']),
                      byte_identical_count=sum(row['byte_identical'] for row in report['binaries']),
                      runtime_replacement_approved=False, independent_audit_required=True)
        print('PASS relocated runtime and 609 app tests; independent review remains.', flush=True)
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        (out / 'ACCEPTANCE-RESULT.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
