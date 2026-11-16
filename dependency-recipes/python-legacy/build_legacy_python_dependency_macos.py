"""Build the accepted legacy Python recipe in a new ordinary-user directory.

Consolidates the historical configure/startup/fallback/module steps without
needing an earlier app or developer build tree. Does not replace any runtime.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import tarfile
import time

SOURCE_SHA = '7911051ed0422fd54b8f59ffc030f7cf2ae30e0f61bda191800bb040dce4f9d2'
FALLBACKS = ('GETENTROPY', 'GETRANDOM', 'CLOCK_GETTIME', 'CLOCK_GETRES',
    'CLOCK_SETTIME', 'FACCESSAT', 'FCHMODAT', 'FCHOWNAT', 'FSTATAT',
    'FDOPENDIR', 'FUTIMENS', 'UTIMENSAT', 'LINKAT', 'MKDIRAT',
    'MKFIFOAT', 'MKNODAT', 'OPENAT', 'READLINKAT', 'RENAMEAT',
    'SYMLINKAT', 'UNLINKAT', 'PREADV', 'PWRITEV')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert platform.system() == 'Darwin' and platform.machine() == 'x86_64' and os.geteuid() != 0
    archive = args.source.resolve(strict=True)
    assert digest(archive) == SOURCE_SHA
    out = args.output.absolute()
    assert out.parent.resolve(strict=True) == out.parent and not out.exists() and not out.is_symlink()
    assert not any(c.isspace() for c in str(out)), 'Use a build path without whitespace'
    assert shutil.disk_usage(out.parent).free >= 2 * 1024**3
    os.umask(0o077)
    out.mkdir(mode=0o700)
    for name in ('logs', 'tmp', 'notices'):
        (out / name).mkdir()
    report = dict(status='RUNNING', source_sha256=SOURCE_SHA, recipe_sha256=digest(__file__),
        steps=[], deployment_target='10.7', previous_build_required=False,
        system_install=False, historical_os_tested=False, ssl_included=False)
    receipt = out / 'BUILD-RESULT.json'
    sdk = subprocess.check_output(['/usr/bin/xcrun', '--show-sdk-path'], text=True).strip()
    env = dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin', HOME=os.environ['HOME'],
        TMPDIR=str(out / 'tmp'), LC_ALL='en_US.UTF-8', CC='/usr/bin/clang',
        MACOSX_DEPLOYMENT_TARGET='10.7',
        CFLAGS='-O2 -mmacosx-version-min=10.7 -Wno-error=implicit-function-declaration',
        CPPFLAGS='-isysroot ' + sdk,
        LDFLAGS='-mmacosx-version-min=10.7 -isysroot ' + sdk)

    def save():
        receipt.write_text(json.dumps(report, indent=2) + '\n')

    def run(label, command, cwd, timeout=2400):
        command = [str(x) for x in command]
        row = dict(name=label, command=command, cwd=str(cwd), status='RUNNING')
        report['steps'].append(row)
        save()
        print('BUILD ' + label, flush=True)
        started = time.monotonic()
        with (out / 'logs' / (label + '.log')).open('xb') as stream:
            process = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                     stderr=subprocess.STDOUT, timeout=timeout)
        row.update(exit_code=process.returncode, seconds=round(time.monotonic()-started, 3),
                   status='PASS' if process.returncode == 0 else 'FAIL')
        save()
        if process.returncode:
            print((out / 'logs' / (label + '.log')).read_text(errors='replace')[-6000:], flush=True)
            raise RuntimeError(label + ' failed')

    try:
        report['host'] = subprocess.check_output(['/usr/bin/sw_vers'], env=env, text=True)
        report['compiler'] = subprocess.check_output(['/usr/bin/clang', '--version'], env=env, text=True)
        report['sdk'] = sdk
        save()
        with tarfile.open(archive) as package:
            for item in package.getmembers():
                p = PurePosixPath(item.name)
                assert not p.is_absolute() and '..' not in p.parts
                assert p.parts[0] == 'Python-3.7.17' and (item.isfile() or item.isdir())
            package.extractall(out, filter='data')
        source = out / 'Python-3.7.17'
        prefix = out / 'prefix'
        shutil.copy2(source / 'LICENSE', out / 'notices/Python-3.7.17-LICENSE.txt')
        run('configure', ['/bin/sh', './configure', '--prefix=' + str(prefix),
            '--without-ensurepip', '--disable-shared', '--with-openssl=' + str(out / 'no-openssl')], source)
        patches = []
        fallback_changes = []
        for name in ('Makefile', 'pyconfig.h'):
            path = source / name
            original = path.read_text()
            updated = original
            if name == 'Makefile':
                assert '-Wl,-stack_size,1000000' in original
                updated = updated.replace('-Wl,-stack_size,1000000', '-Wl,-e,start -Wl,-adhoc_codesign')
            else:
                for feature in FALLBACKS:
                    enabled = '#define HAVE_' + feature + ' 1'
                    if enabled in updated:
                        fallback_changes.append(feature)
                    updated = updated.replace(enabled,
                        '/* #undef HAVE_' + feature + ' -- Lion compatibility fallback */')
                    assert enabled not in updated
            patches.extend(difflib.unified_diff(original.splitlines(True), updated.splitlines(True),
                fromfile=name + '.generated', tofile=name + '.compatibility'))
            path.write_text(updated)
        (out / 'generated-configuration.diff').write_text(''.join(patches))
        report['compatibility'] = dict(startup_entry='start', adhoc_signature=True,
            fallback_policy=list(FALLBACKS), changed_generated_defines=fallback_changes,
            upstream_c_source_modified=False)
        run('build', ['/usr/bin/make', '-j2'], source)
        run('install-private-prefix', ['/usr/bin/make', 'install'], source)
        python = prefix / 'bin/python3.7'
        suffix = subprocess.check_output([str(python), '-I', '-B', '-c',
            'import sysconfig;print(sysconfig.get_config_var("EXT_SUFFIX"))'], env=env, text=True).strip()
        assert suffix == '.cpython-37m-darwin.so'
        for module, filename, library in (('zlib', 'zlibmodule.c', '-lz'), ('_bz2', '_bz2module.c', '-lbz2')):
            destination = prefix / 'lib/python3.7/lib-dynload' / (module + suffix)
            # These files are confined to this newly created private prefix.
            # Always use the historical explicit SDK build, independent of
            # setup.py's optional-module autodetection on a particular SDK.
            if destination.exists():
                shutil.copy2(destination, out / (module + '-autodetected-module.so'))
            run('build-' + module, ['/usr/bin/clang', '-arch', 'x86_64',
                '-mmacosx-version-min=10.7', '-isysroot', sdk, '-O2', '-bundle',
                '-undefined', 'dynamic_lookup', '-Wl,-adhoc_codesign',
                '-I' + str(source / 'Include'), '-I' + str(source),
                source / 'Modules' / filename, library, '-o', destination], source)
        run('required-imports', [python, '-I', '-B', '-c',
            'import ctypes,socket,plistlib,hashlib,zlib,bz2,uuid,tempfile,subprocess,xml.etree.ElementTree;'
            'assert zlib.decompress(zlib.compress(b"fixture"))==b"fixture";'
            'assert bz2.decompress(bz2.compress(b"fixture"))==b"fixture";'
            'print("Required legacy runtime imports passed")'], out)
        runtime = out / 'runtime'
        shutil.copytree(prefix, runtime, symlinks=True,
            ignore=shutil.ignore_patterns('__pycache__', 'test', 'tests', 'idlelib',
                                         'tkinter', 'turtledemo', 'ensurepip', '*.a'))
        report.update(status='PASS', runtime=str(runtime),
            python=dict(path=str(runtime / 'bin/python3.7'),
                        bytes=(runtime / 'bin/python3.7').stat().st_size,
                        sha256=digest(runtime / 'bin/python3.7')),
            acceptance_required=True)
        print('BUILD COMPLETE; relocation and application acceptance still required.', flush=True)
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
