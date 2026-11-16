"""Compare original/rebuilt Inno extraction through frozen app code.

Only extracts and checks ordinary files. Does not run any Windows installer,
change drivers, access Windows partitions, or change the accepted Mac app.
Compatible with the bundled Python 3.7 and 3.11 runtimes.
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
    old = reference / 'publisher-tools/macos-10.15/innoextract'
    assert digest(old) == REFERENCE_SHA
    recipe = json.loads((fixtures / 'driver-recipe.json').read_text())
    archive = fixtures / 'Lenovo-9cke01wf.exe'
    assert digest(archive) == ARCHIVE_SHA == recipe['sources']['broadcom-wifi']['sha256']
    assert archive.stat().st_size == recipe['sources']['broadcom-wifi']['bytes']
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
        reference_sha256=REFERENCE_SHA, recipe_sha256=digest(fixtures / 'driver-recipe.json'),
        archive_sha256=ARCHIVE_SHA, app_module_sha256=digest(reference / 'publisher_drivers.py'),
        outputs={}, refusals=[], vendor_installers_executed=False, installed_drivers_modified=False,
        efi_modified=False, live_partition_accessed=False, historical_os_tested=False)
    result_path = out / 'ACCEPTANCE-RESULT.json'

    def run(command, label):
        process = subprocess.run([str(x) for x in command], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=600)
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
        assert imports == old_imports, 'Review changed imports before accepting the rebuild'
        listings = {}
        for label, executable in (('rebuilt', tool), ('reference', old)):
            run([executable, '--version'], label + '-version')
            listings[label] = run([executable, '--list', '--color=0', '--progress=0', archive], label + '-listing')
            run([executable, '--test', '--silent', archive], label + '-archive-checksums')
            target = out / label
            target.mkdir()
            scratch = out / (label + '-scratch')
            scratch.mkdir()
            requests = [dict(row, output=str(target / row['path'])) for row in members]
            with (out / 'logs' / (label + '-progress.txt')).open('w') as log:
                publisher.extract_inno(executable, archive, requests, scratch,
                                       lambda line: log.write(str(line) + '\n'))
            assert {p for p in target.rglob('*') if p.is_file()} == {Path(r['output']) for r in requests}
            outputs = []
            for row in requests:
                path = Path(row['output'])
                assert path.stat().st_size == row['bytes'] and digest(path) == row['sha256']
                outputs.append(dict(file=path.relative_to(out).as_posix(), member=row['member'],
                                    bytes=row['bytes'], sha256=row['sha256']))
            report['outputs'][label] = outputs
            print('PASS ' + label + ': full archive checksums and 10 selected driver files', flush=True)
            first = members[0]
            for name, changes in [('missing-member', dict(member='app/not-present.bin')),
                                  ('wrong-length', dict(bytes=first['bytes']+1)),
                                  ('wrong-hash', dict(sha256='0'*64)), ('reused-output', {})]:
                target_file = out / (label + '-' + name + '.bin')
                work = out / (label + '-' + name + '-scratch')
                work.mkdir()
                if name == 'reused-output':
                    target_file.write_bytes(b'preserve existing destination')
                request = dict(first, output=str(target_file))
                request.update(changes)
                try:
                    publisher.extract_inno(executable, archive, [request], work, lambda line: None)
                    raise AssertionError('Invalid archive request was accepted')
                except (ValueError, subprocess.CalledProcessError) as error:
                    if name == 'reused-output':
                        assert target_file.read_bytes() == b'preserve existing destination'
                    else:
                        assert not target_file.exists()
                    report['refusals'].append(dict(tool=label, case=name, error=str(error),
                                                  success_reported=False, destination_preserved=True))
        assert listings['rebuilt'] == listings['reference']
        assert digest(old) == REFERENCE_SHA and digest(built) == receipt['tool']['sha256']
        assert digest(archive) == ARCHIVE_SHA
        report.update(status='PASS', original_inputs_unchanged=True, listings_match=True,
                      full_archive_checksum_tests=2, byte_identical=report['binary']['byte_identical'])
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        result_path.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
