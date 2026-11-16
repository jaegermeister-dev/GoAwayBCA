"""Stage a separate old-host extractor profile and test its real selection path."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('reference-resources', 'sevenzip-build', 'inno-build', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    assert sys.platform == 'darwin' and os.geteuid() != 0
    reference = args.reference_resources.resolve(strict=True)
    root = args.output.absolute()
    assert root.parent.resolve(strict=True) == root.parent and not root.exists()
    root.mkdir(mode=0o700)
    report = dict(status='RUNNING', historical_os_tested=False,
                  installed_app_changed=False, source_manifest_sha256=digest(reference / 'publisher-helpers.json'))
    try:
        original = json.loads((reference / 'publisher-helpers.json').read_text())
        assert len(original['profiles']) == 1 and original['profiles'][0]['minimum'] == '10.15'
        staged = root / 'candidate profile with spaces'
        staged.mkdir()
        old = dict(minimum='10.7', maximum='10.14.99', tools={})
        for name, source in [('7zip', args.sevenzip_build / 'tools/7zz'),
                             ('inno', args.inno_build / 'tools/innoextract')]:
            receipt = json.loads((source.parent.parent / 'BUILD-RESULT.json').read_text())
            assert receipt['status'] == 'PASS'
            # Rebuild receipts use the same single-tool key for these candidates.
            assert digest(source) == receipt['tool']['sha256']
            relative = 'publisher-tools/macos-10.7/' + source.name
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(destination))
            old['tools'][name] = dict(path=relative, bytes=destination.stat().st_size, sha256=digest(destination))
        for row in original['profiles'][0]['tools'].values():
            source, destination = reference / row['path'], staged / row['path']
            assert source.stat().st_size == row['bytes'] and digest(source) == row['sha256']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(destination))
        manifest = dict(original, profiles=[old] + original['profiles'])
        (staged / 'publisher-helpers.json').write_text(json.dumps(manifest, indent=2) + '\n')
        sys.path.insert(0, str(reference))
        import publisher_helpers
        assert Path(publisher_helpers.__file__).resolve() == reference / 'publisher_helpers.py'
        selections = []
        for host in ('10.7', '10.7.5', '10.8.5', '10.9.5', '10.10.5', '10.11.6',
                     '10.12.6', '10.13.6', '10.14.6', '10.15.7', '11.7.10', '12.7.6',
                     '13.7.8', '14.8', '15.7', '26.7.1'):
            selected = publisher_helpers.select(staged, host)
            legacy = tuple(int(x) for x in host.split('.')) < (10, 15)
            expected = old if legacy else original['profiles'][0]
            assert all(selected[name] == staged / row['path'] for name, row in expected['tools'].items())
            selections.append(dict(host=host, selected_minimum=expected['minimum']))
        for host in ('10.6.8', '27.0'):
            try:
                publisher_helpers.select(staged, host)
            except (ValueError, RuntimeError) as error:
                assert 'No compatible bundled publisher extractor' in str(error)
            else:
                raise AssertionError('Out-of-range profile was accepted')
        # Exercise patched directory enumeration and actual file timestamp writes.
        source = root / 'source tree'
        source.mkdir()
        nested = source / 'nested space' / 'unicode-\u03a9'
        nested.mkdir(parents=True)
        files = {source / 'plain.txt': b'plain\n', nested / 'data.bin': bytes(range(256)) * 100,
                 nested / 'empty.bin': b'', source / '\u00e9.txt': b'utf8 filename\n'}
        expected_times = {}
        for index, (path, data) in enumerate(files.items()):
            path.write_bytes(data)
            stamp = 1600000000123456000 + index * 1000000000
            os.utime(str(path), ns=(stamp, stamp))
            expected_times[path.relative_to(source).as_posix()] = stamp
        (source / 'relative link').symlink_to('plain.txt')
        archive = root / 'directory-roundtrip.7z'
        extracted = root / 'extracted'
        tool = publisher_helpers.select(staged, '10.9.5')['7zip']
        for name, command in [('create', [tool, 'a', '-snl', archive, source.name]),
                              ('extract', [tool, 'x', '-snl', '-o' + str(extracted), archive])]:
            process = subprocess.run([str(x) for x in command], cwd=str(root),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            (root / (name + '.log')).write_bytes(process.stdout + process.stderr)
            assert process.returncode == 0, (name, process.stderr)
        extracted_source = extracted / source.name
        output_rows = []
        for path, data in files.items():
            relative = path.relative_to(source).as_posix()
            output = extracted_source / relative
            assert output.read_bytes() == data and output.stat().st_mtime_ns == expected_times[relative]
            output_rows.append(dict(path=output.relative_to(root).as_posix(), bytes=len(data),
                sha256=digest(output), mtime_ns=expected_times[relative]))
        assert (extracted_source / 'relative link').is_symlink()
        assert os.readlink(str(extracted_source / 'relative link')) == 'plain.txt'
        assert digest(reference / 'publisher-helpers.json') == report['source_manifest_sha256']
        report.update(status='PASS', manifest=manifest, host_selections=selections,
                      rejected_hosts=['10.6.8', '27.0'], directory_outputs=output_rows,
                      relative_symlink_preserved=True, public_release_ready=False)
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        (root / 'RESULT.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
