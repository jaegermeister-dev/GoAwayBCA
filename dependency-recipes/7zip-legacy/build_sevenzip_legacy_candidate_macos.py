"""Build a separate 10.7-targeted candidate with recorded legacy source changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import tarfile
import time
import importlib.util

_patch_spec = importlib.util.spec_from_file_location(
    'private_legacy_source_patch', Path(__file__).with_name('patch_sevenzip_legacy.py'))
_patch_module = importlib.util.module_from_spec(_patch_spec)
_patch_spec.loader.exec_module(_patch_module)
apply = _patch_module.apply

SOURCE = '7zip-26.04-source.tar.xz'
SHA256 = '9691944c0fe0d01bb49373a704fb983fd33bc98b1738695179dfbf99ac1734f6'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    assert platform.system() == 'Darwin' and platform.machine() == 'x86_64' and os.geteuid() != 0
    source = args.source.resolve(strict=True)
    assert source.is_file() and digest(source) == SHA256
    out = args.output.absolute()
    assert out.parent.resolve(strict=True) == out.parent and not out.exists() and not out.is_symlink()
    assert not any(c.isspace() for c in str(out)), 'Use a build path without whitespace'
    assert shutil.disk_usage(out.parent).free >= 1024**3
    os.umask(0o077)
    out.mkdir(mode=0o700)
    for name in ('source', 'tools', 'logs', 'tmp'):
        (out / name).mkdir()
    result_path = out / 'BUILD-RESULT.json'
    report = dict(status='RUNNING', source_sha256=SHA256, recipe_sha256=digest(__file__),
                  deployment_target='10.7', steps=[], system_install=False,
                  historical_os_tested=False, previous_build_required=False)
    env = dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin', HOME=os.environ['HOME'],
               TMPDIR=str(out / 'tmp'), LC_ALL='en_US.UTF-8', MACOSX_DEPLOYMENT_TARGET='10.7')
    try:
        with tarfile.open(source) as archive:
            for member in archive.getmembers():
                p = PurePosixPath(member.name)
                assert not p.is_absolute() and '..' not in p.parts and (member.isfile() or member.isdir())
            archive.extractall(out / 'source', filter='data')
        report['legacy_source_patch'] = apply(out / 'source', out / 'SOURCE-PATCH.json')
        report['host'] = subprocess.check_output(['/usr/bin/sw_vers'], env=env, text=True)
        report['compiler'] = subprocess.check_output(['/usr/bin/clang', '--version'], env=env, text=True)
        report['sdk'] = subprocess.check_output(['/usr/bin/xcrun', '--show-sdk-path'], env=env, text=True).strip()
        cwd = out / 'source/CPP/7zip/Bundles/Alone2'
        # Upstream's Mac wrapper sets O=b/m_x64, IS_X64, -arch x86_64,
        # clang and C++11. Merely setting PLATFORM on makefile.gcc does not.
        command = ['/usr/bin/make', '-f', '../../cmpl_mac_x64.mak', '-j2']
        row = dict(name='7zip-build', command=command, cwd=str(cwd), status='RUNNING')
        report['steps'].append(row)
        result_path.write_text(json.dumps(report, indent=2) + '\n')
        print('BUILD 7zip', flush=True)
        started = time.monotonic()
        with (out / 'logs/build.log').open('wb') as stream:
            process = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                     stderr=subprocess.STDOUT, timeout=1200)
        row.update(exit_code=process.returncode, seconds=round(time.monotonic()-started, 3),
                   status='PASS' if process.returncode == 0 else 'FAIL')
        if process.returncode:
            print((out / 'logs/build.log').read_text(errors='replace')[-6000:], flush=True)
            raise RuntimeError('7-Zip build failed')
        tool = out / 'tools/7zz'
        shutil.copy2(cwd / 'b/m_x64/7zz', tool)
        report.update(status='PASS', tool=dict(path=str(tool), bytes=tool.stat().st_size,
                                               sha256=digest(tool)), acceptance_required=True, installed_app_modified=False)
        print('BUILD COMPLETE; extraction acceptance still required.', flush=True)
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        result_path.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
