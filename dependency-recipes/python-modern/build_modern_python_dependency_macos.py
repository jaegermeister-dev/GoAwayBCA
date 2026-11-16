"""Run the exact pinned upstream macOS recipe in an isolated developer tree."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile

UPSTREAM_SHA = '2c4bc5e43a0276046f080f40f0bd9586b10f2a6aa1714c592557f57ef0821e0c'
COMMIT = '5e46737f6480fc315ebfea83866910cbfcc772f0'
UV_SHA = 'fd04bf01ba5f2829f4f94ca8f0c24a743f21e73d75045613ed6fc2fd66fc7bda'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    args = parser.parse_args()
    assert platform.system() == 'Darwin' and platform.machine() == 'x86_64' and os.geteuid() != 0
    inputs = args.inputs.resolve(strict=True)
    out = args.output.absolute()
    sdk = args.sdk.resolve(strict=True)
    assert not out.exists() and out.parent.resolve(strict=True) == out.parent
    assert not any(c.isspace() for c in str(out))
    assert shutil.disk_usage(out.parent).free >= 4 * 1024**3, 'Need 4 GiB free before preparing this serial build'
    manifest = json.loads((inputs / 'INPUTS.json').read_text())
    assert manifest['status'] == 'PASS' and len(manifest['inputs']) == 20
    rows = {row['component']: row for row in manifest['inputs']}
    assert len(rows) == 20
    for row in rows.values():
        assert PurePosixPath(row['filename']).name == row['filename']
        path = inputs / row['filename']
        assert path.is_file() and not path.is_symlink()
        assert path.stat().st_size == row['bytes'] and digest(path) == row['sha256']
    archive = inputs / ('python-build-standalone-' + COMMIT + '.tar.gz')
    assert digest(archive) == UPSTREAM_SHA
    assert rows['uv-build-orchestrator']['sha256'] == UV_SHA
    os.umask(0o077)
    out.mkdir(mode=0o700)
    for name in ('logs', 'tmp', 'tools', 'notices'):
        (out / name).mkdir()
    report = dict(status='RUNNING', source_commit=COMMIT, source_sha256=UPSTREAM_SHA,
        recipe_sha256=digest(__file__), steps=[], deployment_target='10.15',
        source_build_completed=False, previous_app_replaced=False,
        installed_runtime_modified=False, system_install=False,
        upstream_reference_sdk='15.0', sdk_path=str(sdk),
        build_python=str(Path(sys.executable).resolve()),
        inputs={k:dict(version=r['version'], sha256=r['sha256']) for k,r in rows.items()})
    receipt = out / 'BUILD-RESULT.json'
    env = dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin', HOME=os.environ['HOME'],
        TMPDIR=str(out / 'tmp'), LC_ALL='en_US.UTF-8', PYTHONDONTWRITEBYTECODE='1',
        UV_CACHE_DIR=str(out / 'uv-cache'), UV_PROJECT_ENVIRONMENT=str(out / 'venv'),
        UV_PYTHON_DOWNLOADS='never', UV_LINK_MODE='copy', UV_NO_PROGRESS='1',
        PYBUILD_RELEASE_TAG='20261108T0000', APPLE_SDK_PATH=str(sdk))

    def save():
        receipt.write_text(json.dumps(report, indent=2) + '\n')

    def run(name, command, cwd, timeout):
        row = dict(name=name, command=[str(x) for x in command], status='RUNNING')
        report['steps'].append(row)
        save()
        print('BUILD ' + name, flush=True)
        started = time.monotonic()
        with (out / 'logs' / (name + '.log')).open('xb') as stream:
            completed = subprocess.run(row['command'], cwd=cwd, env=env,
                stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
        row.update(exit_code=completed.returncode, seconds=round(time.monotonic()-started, 3),
                   status='PASS' if completed.returncode == 0 else 'FAIL')
        save()
        if completed.returncode:
            print((out / 'logs' / (name + '.log')).read_text(errors='replace')[-8000:], flush=True)
            raise RuntimeError(name + ' failed')

    try:
        report['host'] = subprocess.check_output(['/usr/bin/sw_vers'], text=True)
        report['sdk_settings'] = json.loads((sdk / 'SDKSettings.json').read_text())['Version']
        report['matches_upstream_sdk'] = report['sdk_settings'] == '15.0'
        save()
        upstream = out / 'upstream'
        upstream.mkdir()
        originals = {}
        with tarfile.open(archive) as t:
            for member in t.getmembers():
                path = PurePosixPath(member.name)
                assert not path.is_absolute() and '..' not in path.parts
                assert path.parts[0] == 'python-build-standalone-' + COMMIT
                if len(path.parts) == 1:
                    continue
                assert member.isfile() or member.isdir()
                dest = copy.copy(member)
                dest.name = '/'.join(path.parts[1:])
                t.extract(dest, upstream, filter='data')
                if member.isfile():
                    originals[dest.name] = digest(upstream / dest.name)
        (out / 'UPSTREAM-FILE-HASHES.json').write_text(json.dumps(originals, indent=2) + '\n')
        downloads = json.loads((upstream / 'pythonbuild/downloads.json').read_text())
        cache = upstream / 'build/downloads'
        cache.mkdir(parents=True)
        for key, row in rows.items():
            if key != 'uv-build-orchestrator':
                assert downloads[key]['sha256'] == row['sha256'] and downloads[key]['size'] == row['bytes']
                assert downloads[key]['url'].rsplit('/', 1)[1] == row['filename']
                shutil.copy2(inputs / row['filename'], cache / row['filename'])
        uv = out / 'tools/uv'
        with zipfile.ZipFile(inputs / rows['uv-build-orchestrator']['filename']) as z:
            uv.write_bytes(z.read('uv-0.13.7.data/scripts/uv'))
            uv.chmod(0o700)
            for name in ('LICENSE-APACHE', 'LICENSE-MIT'):
                (out / 'notices' / ('uv-' + name)).write_bytes(z.read('uv-0.13.7.dist-info/licenses/' + name))
        shutil.copy2(upstream / 'LICENSE', out / 'notices/python-build-standalone-LICENSE')
        run('bootstrap-locked-build-environment', [uv, 'sync', '--frozen', '--no-dev', '--python', sys.executable], upstream, 1200)
        env['PATH'] = str(out / 'venv/bin') + ':/usr/bin:/bin:/usr/sbin:/sbin'
        python = out / 'venv/bin/python3'
        common = [python, '-B', upstream / 'build.py', '--python', 'cpython-3.11',
                  '--target-triple', 'x86_64-apple-darwin', '--options', 'pgo+lto', '--serial', '--no-docker']
        run('upstream-build-plan', common + ['--make-target', 'empty'], upstream, 180)
        run('upstream-source-build', common, upstream, 10800)
        for relative, sha in originals.items():
            assert digest(upstream / relative) == sha, relative
        artifacts = []
        for p in sorted((upstream / 'dist').iterdir()):
            if p.is_file():
                artifacts.append(dict(path=str(p), bytes=p.stat().st_size, sha256=digest(p)))
        assert artifacts
        report.update(status='PASS', source_build_completed=True, upstream_files_unchanged=True,
                      artifacts=artifacts, runtime_acceptance_required=True,
                      byte_identical_to_release_not_claimed=True)
        print('UPSTREAM BUILD COMPLETE; output acceptance is still required.', flush=True)
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
