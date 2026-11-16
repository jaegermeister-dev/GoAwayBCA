"""Prepare an isolated install-only runtime from a completed, verified build."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import tarfile

import zstandard

DROP_EXTENSIONS = {
    '_ctypes_test', '_testbuffer', '_testcapi', '_testexternalinspection',
    '_testimportmultiple', '_testinternalcapi', '_testlimitedcapi',
    '_testmultiphase', '_testsinglephase',
}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert platform.system() == 'Darwin' and os.geteuid() != 0
    build = args.build.resolve(strict=True)
    result = json.loads((build / 'BUILD-RESULT.json').read_text())
    assert result['status'] == 'PASS' and result['source_build_completed']
    candidates = [a for a in result['artifacts'] if
                  Path(a['path']).name.startswith('cpython-3.11.17-x86_64-apple-darwin-pgo+lto-')
                  and a['path'].endswith('.tar.zst')]
    assert len(candidates) == 1, candidates
    artifact = candidates[0]
    archive = Path(artifact['path']).resolve(strict=True)
    assert archive.parent == build / 'upstream/dist'
    assert archive.stat().st_size == artifact['bytes'] and digest(archive) == artifact['sha256']
    out = args.output.absolute()
    assert out.parent.resolve(strict=True) == out.parent and not out.exists()
    os.umask(0o077)
    out.mkdir(mode=0o700)
    runtime = out / 'relocated runtime with spaces'
    runtime.mkdir()
    report = dict(status='RUNNING', artifact=artifact, runtime=str(runtime),
                  stripped=False, installer_executed=False, installed_runtime_modified=False)
    try:
        with archive.open('rb') as compressed, zstandard.ZstdDecompressor().stream_reader(compressed) as reader:
            with tarfile.open(fileobj=reader, mode='r|') as tar:
                first = next(iter(tar))
                assert first.name == 'python/PYTHON.json' and first.isfile()
                metadata = json.load(tar.extractfile(first))
                (out / 'PYTHON.json').write_text(json.dumps(metadata, indent=2) + '\n')
                assert metadata['python_version'] == '3.11.17'
                assert metadata['apple_sdk_deployment_target'] == '10.15'
                stdlib = metadata['python_paths']['stdlib']
                drops = {'python/' + entry['shared_lib']
                    for name, entries in metadata['build_info']['extensions'].items()
                    if name in DROP_EXTENSIONS for entry in entries if entry.get('shared_lib')}
                test_dirs = ['python/' + stdlib + '/' + name.replace('.', '/') + '/'
                             for name in metadata['python_stdlib_test_packages']]
                kept, omitted = [], []
                for member in tar:
                    path = PurePosixPath(member.name)
                    assert not path.is_absolute() and '..' not in path.parts
                    if not member.name.startswith('python/install/'):
                        continue
                    if (('/libpython' in member.name and member.name.endswith('.a'))
                            or member.name in drops
                            or any(member.name.startswith(p) for p in test_dirs)):
                        omitted.append(member.name)
                        continue
                    assert member.isfile() or member.isdir() or member.issym() or member.islnk()
                    dest = copy.copy(member)
                    dest.name = member.name.removeprefix('python/install/')
                    if not dest.name:
                        continue
                    if member.islnk():
                        assert member.linkname.startswith('python/install/')
                        dest.linkname = member.linkname.removeprefix('python/install/')
                    tar.extract(dest, runtime, filter='data')
                    kept.append(dest.name)
        for entry in runtime.rglob('*'):
            if entry.is_symlink():
                assert runtime in entry.resolve(strict=True).parents
        assert (runtime / 'bin/python3.11').is_file()
        report.update(status='PASS', retained_entries=len(kept), omitted_paths=omitted,
                      python_sha256=digest(runtime / 'bin/python3.11'),
                      source_sdk=metadata['apple_sdk_version'],
                      extraction_policy='upstream install_only selection; native debug symbols retained')
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        (out / 'UNPACK-RESULT.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
