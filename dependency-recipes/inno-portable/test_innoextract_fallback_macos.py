"""Check normal and missing-API extraction through the frozen app code.

No installer execution, installed-app modification, privileged access or disk
operations. DYLD interposition is limited to one scratch extraction process.
Compatible with the app's Python 3.7 and 3.11 runtimes.
"""
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

REFERENCE_SHA = 'ad096cb8e17819e3a48a524f6d4f285255d567486b53f236266e5c8b7d05b112'
ARCHIVE_SHA = 'ce682a987d240438512029f43b3f24a6cf58c6ad48561b91a48a2ea1f7c5abe9'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024*1024), b''):
            h.update(data)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build', 'fixtures', 'reference-resources', 'interposer', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    assert platform.system() == 'Darwin' and os.geteuid() != 0
    build = args.build.resolve(strict=True)
    fixtures = args.fixtures.resolve(strict=True)
    reference = args.reference_resources.resolve(strict=True)
    interposer = args.interposer.resolve(strict=True)
    out = args.output.absolute()
    assert out.parent.resolve(strict=True) == out.parent and not out.exists()
    receipt = json.loads((build / 'BUILD-RESULT.json').read_text())
    assert receipt['status'] == 'PASS'
    built = Path(receipt['tool']['path'])
    assert digest(built) == receipt['tool']['sha256']
    old = reference / 'publisher-tools/macos-10.15/innoextract'
    assert digest(old) == REFERENCE_SHA
    recipe = json.loads((fixtures / 'driver-recipe.json').read_text())
    archive = fixtures / 'Lenovo-9cke01wf.exe'
    assert digest(archive) == ARCHIVE_SHA == recipe['sources']['broadcom-wifi']['sha256']
    members = [r for r in recipe['files'] if r['parent'] == 'broadcom-wifi']
    assert len(members) == 10
    sys.path.insert(0, str(reference))
    import publisher_drivers as publisher
    assert Path(publisher.__file__).resolve() == reference / 'publisher_drivers.py'
    os.umask(0o077)
    out.mkdir(mode=0o700)
    (out / 'logs').mkdir()
    relocation = out / 'relocated extractor with spaces'
    relocation.mkdir()
    tool = relocation / 'innoextract'
    shutil.copy2(str(built), str(tool))
    report = dict(status='RUNNING', runtime=sys.version, tool_sha256=digest(tool),
        reference_sha256=REFERENCE_SHA, archive_sha256=ARCHIVE_SHA,
        app_module_sha256=digest(reference / 'publisher_drivers.py'),
        interposer_sha256=digest(interposer), outputs={}, timestamps={},
        historical_host_tested=False, installed_app_modified=False,
        runtime_replacement_approved=False)

    def run(command, label):
        result = subprocess.run([str(x) for x in command], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=600)
        (out / 'logs' / (label + '.txt')).write_bytes(result.stdout + b'\nSTDERR:\n' + result.stderr)
        assert result.returncode == 0, (label, result.stderr)
        return result.stdout.decode('utf-8')

    try:
        links = run(['/usr/bin/otool', '-L', tool], 'links')
        dependencies = [x.strip().split(' (', 1)[0] for x in links.splitlines()[1:]]
        assert all(x.startswith(('/usr/lib/', '/System/Library/')) for x in dependencies)
        commands = run(['/usr/bin/otool', '-l', tool], 'load-commands')
        minimum = re.findall(r'\bminos\s+(\S+)', commands) or re.findall(
            r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\S+)', commands)
        assert minimum == ['10.7']
        assert run(['/usr/bin/lipo', '-archs', tool], 'architecture').strip() == 'x86_64'
        imports = set(run(['/usr/bin/nm', '-ju', tool], 'imports').splitlines())
        old_imports = set(run(['/usr/bin/nm', '-ju', old], 'reference-imports').splitlines())
        report['imports'] = dict(added=sorted(imports-old_imports), removed=sorted(old_imports-imports))
        assert imports-old_imports == {'_dlsym', '_utimes'}, report['imports']
        assert old_imports-imports == {'_utimensat'}, report['imports']
        listings = {}
        for label, executable in (('reference', old), ('candidate', tool), ('missing-api', tool)):
            if label != 'missing-api':
                listings[label] = run([executable, '--list', '--color=0', '--progress=0', archive], label+'-listing')
                run([executable, '--test', '--silent', archive], label+'-archive-checksums')
            target = out / label
            target.mkdir()
            scratch = out / (label + '-scratch')
            scratch.mkdir()
            requests = [dict(row, output=str(target / row['path'])) for row in members]
            saved = {key: os.environ.get(key) for key in ('DYLD_INSERT_LIBRARIES', 'MWM_TIMESTAMP_PROBE_LOG')}
            assert saved['DYLD_INSERT_LIBRARIES'] is None
            try:
                if label == 'missing-api':
                    os.environ['DYLD_INSERT_LIBRARIES'] = str(interposer)
                    os.environ['MWM_TIMESTAMP_PROBE_LOG'] = str(out / 'logs/missing-api-probe.txt')
                with (out / 'logs' / (label + '-progress.txt')).open('w') as log:
                    publisher.extract_inno(executable, archive, requests, scratch,
                                           lambda line: log.write(str(line) + '\n'))
            finally:
                for key, value in saved.items():
                    if value is None: os.environ.pop(key, None)
                    else: os.environ[key] = value
            assert {p for p in target.rglob('*') if p.is_file()} == {Path(row['output']) for row in requests}
            scratch_roots = list(scratch.iterdir())
            assert len(scratch_roots) == 1 and scratch_roots[0].is_dir()
            results, stamps = [], {}
            for row in requests:
                path = Path(row['output'])
                assert path.stat().st_size == row['bytes'] and digest(path) == row['sha256']
                original = scratch_roots[0] / row['member']
                assert digest(original) == row['sha256']
                stamps[row['member']] = original.stat().st_mtime_ns
                results.append(dict(file=path.relative_to(out).as_posix(), member=row['member'],
                                    bytes=row['bytes'], sha256=row['sha256']))
            report['outputs'][label] = results
            report['timestamps'][label] = stamps
            print('PASS ' + label + ': 10 exact publisher outputs', flush=True)
        assert listings['reference'] == listings['candidate']
        assert report['timestamps']['reference'] == report['timestamps']['candidate']
        for member, value in report['timestamps']['reference'].items():
            assert report['timestamps']['missing-api'][member] == (value // 1000) * 1000
        calls = (out / 'logs/missing-api-probe.txt').read_text().splitlines()
        assert calls.count('missing-utimensat') >= 1 and calls.count('utimes-fallback') >= 10, calls
        assert set(calls) == {'missing-utimensat', 'utimes-fallback'}
        assert digest(old) == REFERENCE_SHA and digest(archive) == ARCHIVE_SHA
        assert digest(built) == receipt['tool']['sha256']
        report.update(status='PASS', selected_outputs=30, listings_match=True,
                      normal_timestamps_match=True, fallback_timestamps_match_microseconds=True,
                      simulated_missing_api=True, fallback_calls=len(calls),
                      full_archive_checksum_tests=2, independent_audit_required=True)
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        (out / 'ACCEPTANCE-RESULT.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
