"""Verify archive extraction with frozen app code and pinned/synthetic inputs.

Ordinary files only. No vendor installer, driver, Windows deployment or EFI
program is executed. Compatible with both bundled Python 3.7/3.11 runtimes.
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

REFERENCE_SHA = '91873fcacb87b0d77bfab5aca6131c73db76f9745821a846298ac180f0740835'


def digest(path):
    with Path(path).open('rb') as stream:
        h = hashlib.sha256()
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
        return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build', 'fixtures', 'reference-resources', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    assert platform.system() == 'Darwin' and os.geteuid() != 0
    build = args.build.resolve(strict=True)
    fixtures = args.fixtures.resolve(strict=True)
    reference = args.reference_resources.resolve(strict=True)
    out = args.output.absolute()
    assert out.parent.resolve(strict=True) == out.parent and not out.exists() and not out.is_symlink()
    receipt = json.loads((build / 'BUILD-RESULT.json').read_text())
    assert receipt['status'] == 'PASS'
    built = Path(receipt['tool']['path'])
    assert digest(built) == receipt['tool']['sha256']
    old = reference / 'publisher-tools/macos-10.15/7zz'
    assert digest(old) == REFERENCE_SHA
    fixture_spec = json.loads((fixtures / 'FIXTURES.json').read_text())
    for row in fixture_spec['fixtures']:
        p = fixtures / row['file']
        assert p.parent == fixtures and p.is_file() and not p.is_symlink()
        assert p.stat().st_size == row['bytes'] and digest(p) == row['sha256']
    sys.path.insert(0, str(reference))
    import publisher_drivers as publisher
    assert Path(publisher.__file__).resolve() == reference / 'publisher_drivers.py'
    os.umask(0o077)
    out.mkdir(mode=0o700)
    (out / 'logs').mkdir()
    relocated = out / 'relocated extractor with spaces'
    relocated.mkdir()
    tool = relocated / '7zz'
    shutil.copy2(str(built), str(tool))
    report = dict(status='RUNNING', runtime=sys.version, tool_sha256=digest(tool),
        reference_sha256=REFERENCE_SHA, app_module_sha256=digest(reference / 'publisher_drivers.py'),
        fixtures_sha256=digest(fixtures / 'FIXTURES.json'), cases=[], refusals=[],
        vendor_installers_executed=False, installed_drivers_modified=False,
        efi_modified=False, historical_os_tested=False, live_partition_accessed=False)
    result_path = out / 'ACCEPTANCE-RESULT.json'

    def run(command, label):
        process = subprocess.run([str(x) for x in command], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=120)
        (out / 'logs' / (label + '.txt')).write_bytes(
            ('COMMAND: ' + repr(command) + '\nEXIT: ' + str(process.returncode) + '\n').encode()
            + process.stdout + b'\nSTDERR:\n' + process.stderr)
        assert process.returncode == 0, (label, process.stderr)
        return process.stdout.decode('utf-8')

    try:
        links = run(['/usr/bin/otool', '-L', tool], 'links')
        dependencies = [line.strip().split(' (', 1)[0] for line in links.splitlines()[1:]]
        assert all(x.startswith(('/usr/lib/', '/System/Library/')) for x in dependencies)
        commands = run(['/usr/bin/otool', '-l', tool], 'load-commands')
        minimum = re.findall(r'\bminos\s+(\S+)', commands) or re.findall(
            r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\S+)', commands)
        assert minimum == ['10.7'], minimum
        assert run(['/usr/bin/lipo', '-archs', tool], 'architecture').strip() == 'x86_64'
        imports = sorted(set(run(['/usr/bin/nm', '-ju', tool], 'imports').splitlines()))
        old_imports = sorted(set(run(['/usr/bin/nm', '-ju', old], 'reference-imports').splitlines()))
        report['binary'] = dict(dependencies=dependencies, deployment_target=minimum[0],
            imports_match=imports == old_imports, byte_identical=digest(tool) == digest(old),
            added_imports=sorted(set(imports)-set(old_imports)),
            removed_imports=sorted(set(old_imports)-set(imports)))
        assert report['binary']['added_imports'] == ['_futimes', '_lutimes', '_utimes']
        assert report['binary']['removed_imports'] == [
            '____chkstk_darwin', '_dirfd', '_fstatat$INODE64', '_futimens', '_timespec_get', '_utimensat']
        assert not set(imports).intersection({'_utimensat', '_futimens', '_fstatat$INODE64',
                                             '_timespec_get', '_clock_gettime', '_dirfd'})
        import importlib.util
        spec = importlib.util.spec_from_file_location('legacy_time_acceptance',
            Path(__file__).with_name('test_sevenzip_legacy_times_macos.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report['timestamp_adapters'] = module.exercise(out / 'timestamp-fixtures',
            Path(__file__).with_name('sevenzip_legacy_time.h'),
            Path(__file__).with_name('sevenzip_legacy_time_probe.cpp'))
        assert report['timestamp_adapters']['case_count'] == 16
        for fixture in fixture_spec['fixtures']:
            part = out / fixture['name']
            part.mkdir()
            archive = fixtures / fixture['file']
            versions = {}
            listings = {}
            for label, executable in (('rebuilt', tool), ('reference', old)):
                target = part / label
                target.mkdir()
                raw_listing = run([executable, 'l', '-ba', '-slt', '-sccUTF-8', '-spd', '--', archive],
                                  fixture['name'] + '-' + label + '-listing')
                listings[label] = publisher.listing(raw_listing)
                requests = [dict(row, output=str(target / ('member-%03d.bin' % index)))
                            for index, row in enumerate(fixture['members'])]
                with (out / 'logs' / (fixture['name'] + '-' + label + '-progress.txt')).open('w') as log:
                    publisher.extract_seven(executable, archive, requests, lambda line: log.write(str(line) + '\n'))
                assert set(target.iterdir()) == {Path(r['output']) for r in requests}
                outputs = []
                for row in requests:
                    path = Path(row['output'])
                    assert path.stat().st_size == row['bytes'] and digest(path) == row['sha256']
                    outputs.append(dict(file=path.relative_to(out).as_posix(), member=row['member'],
                                        bytes=row['bytes'], sha256=row['sha256']))
                versions[label] = outputs
            assert listings['rebuilt'] == listings['reference']
            assert digest(archive) == fixture['sha256']
            report['cases'].append(dict(name=fixture['name'], archive_sha256=fixture['sha256'],
                listed_members=len(listings['rebuilt']), selected_members=len(fixture['members']), outputs=versions))
            print('PASS ' + fixture['name'] + ': ' + str(len(fixture['members'])) + ' selected members', flush=True)
        archive = fixtures / 'synthetic.zip'
        first = next(r for r in fixture_spec['fixtures'] if r['file'] == 'synthetic.zip')['members'][0]
        for label, executable in (('rebuilt', tool), ('reference', old)):
            for name, changes in [('missing-member', dict(member='not-present.txt')),
                                  ('wrong-length', dict(bytes=first['bytes']+1)),
                                  ('wrong-hash', dict(sha256='0'*64))]:
                target = out / (label + '-' + name + '.bin')
                request = dict(first, output=str(target))
                request.update(changes)
                try:
                    publisher.extract_seven(executable, archive, [request], lambda line: None)
                    raise AssertionError('Invalid archive request was accepted')
                except ValueError as error:
                    if name != 'wrong-hash':
                        assert not target.exists()
                    report['refusals'].append(dict(tool=label, case=name, error=str(error),
                        partial_scratch_file_present=target.exists(), success_reported=False))
        assert digest(old) == REFERENCE_SHA and digest(built) == receipt['tool']['sha256']
        assert all(digest(fixtures/r['file']) == r['sha256'] for r in fixture_spec['fixtures'])
        report.update(status='PASS', original_inputs_unchanged=True,
                      byte_identical=report['binary']['byte_identical'])
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        result_path.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
