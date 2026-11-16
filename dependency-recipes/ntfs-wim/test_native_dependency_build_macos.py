"""Check rebuilt helpers using new regular-file NTFS images and a pinned fixture.

All destructive helper calls target newly created ordinary files. No installer,
mounts, privileges, disk devices, real NTFS volumes, or EFI files are accessed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess


FIXTURE_HASH = 'f7f77f41161011b3234f58293a28548e546cb36b46d57016906ac33a811046e8'
FALLBACK_SYMBOLS = ('_futimens', '_utimensat', '_openat', '_fstatat', '_readlinkat',
                    '_fdopendir', '_clock_gettime', '_clock_getres', '_getentropy',
                    '_preadv', '_pwritev')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def metadata(text):
    records = {}
    keys = ('Short Name', 'Attributes', 'Security Descriptor', 'Creation Time',
            'Last Write Time', 'Last Access Time', 'Link Count', 'Hash', 'Uncompressed size')
    for block in text.split('-' * 76):
        match = re.search(r'^Full Path\s+= "(.*)"$', block, re.M)
        if not match:
            continue
        selected = []
        for line in block.splitlines():
            item = line.strip()
            if ('=' in item and item.split('=', 1)[0].strip() in keys
                    or item.startswith(('Named data stream', 'Unnamed data stream'))):
                selected.append(item)
        records[match[1]] = selected
    return records


def hardlinks(text):
    groups = {}
    for block in text.split('-' * 76):
        name = re.search(r'^Full Path\s+= "(.*)"$', block, re.M)
        group = re.search(r'^Link Group ID\s+= (.*)$', block, re.M)
        if name and group:
            groups[name[1]] = int(group[1], 16)
    assert groups['/payload.txt'] and groups['/payload.txt'] == groups['/hardlink.txt']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, type=Path)
    parser.add_argument('--fixture', required=True, type=Path)
    parser.add_argument('--reference-resources', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    assert platform.system() == 'Darwin' and platform.machine() == 'x86_64' and os.geteuid() != 0
    build = args.build.resolve(strict=True)
    fixture = args.fixture.resolve(strict=True)
    reference = args.reference_resources.resolve(strict=True)
    out = args.output.absolute()
    assert out.parent.resolve(strict=True) == out.parent and not out.exists() and not out.is_symlink()
    assert digest(fixture) == FIXTURE_HASH
    receipt = json.loads((build / 'BUILD-RESULT.json').read_text())
    assert receipt['status'] == 'PASS'
    os.umask(0o077)
    out.mkdir(mode=0o700)
    (out / 'logs').mkdir()
    report = dict(status='RUNNING', fixture_sha256=FIXTURE_HASH, profiles=[],
                  physical_devices_accessed=False, privileges_used=False,
                  installer_executed=False, reference_replaced=False,
                  limitations=['Synthetic fixtures, not a Windows boot test',
                               'Audit SACL not tested',
                               'Both helper profiles tested on Tahoe only',
                               'Functional compatibility is not byte-identical reproducibility'])
    env = dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin', HOME=os.environ['HOME'],
               TMPDIR='/private/tmp', LC_ALL='en_US.UTF-8')
    counter = 0

    def run(command, label, expected_exit=0, expected_error=None):
        nonlocal counter
        counter += 1
        command = [str(x) for x in command]
        p = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=180, env=env, cwd=out)
        log = out / 'logs' / ('%03d-%s.txt' % (counter, label))
        log.write_bytes(('COMMAND: ' + repr(command) + '\nEXIT: ' + str(p.returncode) + '\n').encode()
                        + b'STDOUT:\n' + p.stdout + b'\nSTDERR:\n' + p.stderr)
        if p.returncode != expected_exit:
            raise RuntimeError(label + ': ' + p.stderr.decode(errors='replace')[-3000:])
        if expected_error is not None:
            assert expected_error.encode() in p.stderr, label
        return p.stdout

    try:
        for profile in receipt['profiles']:
            name = profile['name']
            part = out / name
            part.mkdir()
            relocated = part / 'relocated tools'
            relocated.mkdir()
            binaries = {}
            paths = {}
            tool_inventory = dict(profile['tools'])
            # make install also produces the app's listing and stream-copy tools.
            # Include those, not only the initial format/apply/readback helpers.
            for tool, relative in [('ntfsls', 'bin/ntfsls'), ('ntfscp', 'sbin/ntfscp')]:
                source = Path(profile['prefix']) / relative
                assert source.is_file() and os.access(source, os.X_OK)
                tool_inventory[tool] = dict(path=str(source), sha256=digest(source), bytes=source.stat().st_size)
            for tool, info in tool_inventory.items():
                source = Path(info['path'])
                assert digest(source) == info['sha256']
                path = relocated / tool
                shutil.copy2(source, path)
                assert digest(path) == info['sha256']
                old = reference / 'payloads' / name / 'tools' / tool
                old_hash = digest(old)
                links = run(['/usr/bin/otool', '-L', path], name + '-' + tool + '-links').decode()
                dependencies = [line.strip().split(' (', 1)[0] for line in links.splitlines()[1:]]
                assert dependencies and all(x.startswith(('/usr/lib/', '/System/Library/')) for x in dependencies)
                commands = run(['/usr/bin/otool', '-l', path], name + '-' + tool + '-load-commands').decode()
                minimum = re.findall(r'\bminos\s+(\S+)', commands) or re.findall(
                    r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\S+)', commands)
                assert minimum == [profile['deployment_target']], minimum
                assert ('LC_UNIXTHREAD' in commands) if name == 'legacy' else ('LC_MAIN' in commands)
                symbols = sorted(set(run(['/usr/bin/nm', '-ju', path], name + '-' + tool + '-symbols').decode().splitlines()))
                old_symbols = sorted(set(run(['/usr/bin/nm', '-ju', old], name + '-' + tool + '-reference-symbols').decode().splitlines()))
                if name == 'legacy':
                    assert not (set(FALLBACK_SYMBOLS) & set(symbols))
                arch = run(['/usr/bin/lipo', '-archs', path], name + '-' + tool + '-architecture').decode().strip()
                assert arch == 'x86_64', arch
                binaries[tool] = dict(sha256=info['sha256'], reference_sha256=old_hash,
                                     exact_reference_match=info['sha256'] == old_hash,
                                     dependencies=dependencies, minimum_macos=minimum[0],
                                     added_undefined_symbols=sorted(set(symbols) - set(old_symbols)),
                                     removed_undefined_symbols=sorted(set(old_symbols) - set(symbols)))
                paths[tool] = path
            wim, mkntfs, cat = (paths[key] for key in ('wimlib-imagex', 'mkntfs', 'ntfscat'))
            run([wim, '--version'], name + '-wim-version')
            image = part / 'scratch.ntfs'
            with image.open('xb') as stream:
                stream.truncate(64 * 1024 * 1024)
            identity = image.lstat()

            def guard():
                current = image.lstat()
                assert stat.S_ISREG(current.st_mode) and current.st_ino == identity.st_ino
                assert current.st_dev == identity.st_dev and current.st_size == identity.st_size
                assert image.resolve().parent == part

            guard()
            run([mkntfs, '-F', '-Q', '-s', '512', '-L', 'FILETEST', image], name + '-format-regular-file')
            guard()
            run([wim, 'verify', fixture], name + '-verify-fixture')
            run([wim, 'apply', fixture, '1', image, '--check', '--strict-acls'], name + '-apply-fixture')
            expected = {'/payload.txt': b'Native NTFS deployment metadata test\r\n' * 100,
                        '/hardlink.txt': b'Native NTFS deployment metadata test\r\n' * 100,
                        '/unicode-\u03bb.txt': b'Unicode file name test\n', '/empty-file': b''}
            for filename, data in expected.items():
                assert run([cat, image, filename], name + '-payload-readback') == data
            assert run([cat, '-n', 'teststream', image, '/payload.txt'], name + '-ADS') == b'Alternate data stream must survive.\r\n'
            recaptured = part / 'recaptured.wim'
            run([wim, 'capture', image, recaptured, 'NTFS roundtrip', '--compress=none', '--check'], name + '-recapture')
            before = run([wim, 'dir', fixture, '1', '--detailed'], name + '-metadata-before').decode()
            after = run([wim, 'dir', recaptured, '1', '--detailed'], name + '-metadata-after').decode()
            assert len(metadata(before)) == 6 and metadata(before) == metadata(after)
            hardlinks(before)
            hardlinks(after)
            (part / 'metadata-before.txt').write_text(before)
            (part / 'metadata-after.txt').write_text(after)
            listing = run([paths['ntfsls'], '-a', '-p', '/', image], name + '-list-root').decode()
            assert all(filename in listing for filename in ('payload.txt', 'hardlink.txt', 'unicode-\u03bb.txt'))
            # Offline registry updates depend on stream replacement retaining ACLs,
            # hard links and named streams. Deliberately change the stream length.
            replacement = part / 'replacement.bin'
            replacement_payload = b'Offline stream replacement test\r\n' * 237
            replacement.write_bytes(replacement_payload)
            guard()
            run([paths['ntfscp'], image, replacement, '/payload.txt'], name + '-replace-stream')
            for filename in ('/payload.txt', '/hardlink.txt'):
                assert run([cat, image, filename], name + '-replace-stream-readback') == replacement_payload
                expected[filename] = replacement_payload
            assert run([cat, '-n', 'teststream', image, '/payload.txt'], name + '-preserved-ADS') == b'Alternate data stream must survive.\r\n'
            replaced_wim = part / 'replaced.wim'
            run([wim, 'capture', image, replaced_wim, 'Stream replacement', '--compress=none', '--check'], name + '-capture-replacement')
            replaced_text = run([wim, 'dir', replaced_wim, '1', '--detailed'], name + '-replacement-metadata').decode()
            hardlinks(replaced_text)
            old_records, new_records = metadata(after), metadata(replaced_text)
            def security(record):
                return [line for line in record if line.startswith('Security Descriptor')]
            assert all(security(old_records[key]) == security(new_records[key]) and security(old_records[key]) for key in old_records)
            # Install-time optional-driver overlays must preserve existing files.
            overlay = part / 'overlay'
            overlay.mkdir()
            (overlay / 'DriverSupport').mkdir()
            payload = bytes(range(256)) * 4096
            (overlay / 'DriverSupport' / 'roundtrip.bin').write_bytes(payload)
            compression_results = []
            for compression in ('XPRESS', 'LZX', 'LZMS'):
                compressed = part / ('overlay-' + compression + '.wim')
                run([wim, 'capture', overlay, compressed, 'Overlay', '--compress=' + compression, '--check'], name + '-capture-' + compression)
                run([wim, 'verify', compressed], name + '-verify-' + compression)
                guard()
                trial = part / ('overlay-' + compression + '.ntfs')
                assert not trial.exists()
                shutil.copyfile(image, trial)
                trial_identity = trial.lstat()
                assert stat.S_ISREG(trial_identity.st_mode) and trial.resolve().parent == part
                run([wim, 'apply', compressed, '1', trial, '--check'], name + '-overlay-' + compression)
                assert run([cat, trial, '/DriverSupport/roundtrip.bin'], name + '-overlay-readback-' + compression) == payload
                for filename, data in expected.items():
                    assert run([cat, trial, filename], name + '-preserved-file') == data
                assert trial.lstat().st_ino == trial_identity.st_ino and trial.stat().st_size == identity.st_size
                if compression == 'XPRESS':
                    # The raw NTFS backend cannot recreate existing directories.
                    # Verify that the accepted reference has the same limitation.
                    run([wim, 'apply', compressed, '1', trial, '--check'], name + '-duplicate-overlay-rebuild',
                        expected_exit=46, expected_error='File exists')
                    reference_image = part / 'reference-duplicate-overlay.ntfs'
                    assert not reference_image.exists()
                    shutil.copyfile(image, reference_image)
                    assert stat.S_ISREG(reference_image.lstat().st_mode) and reference_image.resolve().parent == part
                    reference_wim = reference / 'payloads' / name / 'tools/wimlib-imagex'
                    run([reference_wim, 'apply', compressed, '1', reference_image, '--check'], name + '-reference-first-overlay')
                    run([reference_wim, 'apply', compressed, '1', reference_image, '--check'], name + '-duplicate-overlay-reference',
                        expected_exit=46, expected_error='File exists')
                compression_results.append(dict(compression=compression, wim_bytes=compressed.stat().st_size,
                                                payload_sha256=hashlib.sha256(payload).hexdigest(),
                                                existing_files_preserved=True))
            guard()
            report['profiles'].append(dict(name=name, status='PASS', binaries=binaries,
                ntfs_regular_file_bytes=identity.st_size, metadata_records=6,
                compression_and_overlay_tests=compression_results,
                duplicate_overlay=dict(rebuild_exit=46, reference_exit=46,
                    matching_error='File exists', limitation='Raw NTFS apply does not merge an already existing overlay directory'),
                checks=['content', 'Unicode filename', 'alternate stream', 'hardlink relationship',
                        'owner/group/DACL', 'attributes', 'timestamps', 'short names',
                        'root listing', 'stream replacement retaining ACLs/hardlinks/ADS',
                        'relocation with system-only dependencies', 'XPRESS/LZX/LZMS overlay preservation']))
            print('PASS ' + name + ': metadata, relocation, compression and overlays', flush=True)
        assert digest(fixture) == FIXTURE_HASH
        report['status'] = 'PASS'
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        report['logged_commands'] = counter
        (out / 'ACCEPTANCE-RESULT.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
