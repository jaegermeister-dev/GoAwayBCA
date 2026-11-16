"""Build hivex from pinned sources in a new ordinary-user directory.

Developer prerequisites: Intel macOS, Python 3.11+, Apple Command Line Tools.
No downloads, existing app, system install, sudo, mounts, or OS hives required.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shlex
import shutil
import subprocess
import tarfile
import time

SOURCES = {
    'pkgconf': ('pkgconf-3.0.7.tar.xz', 'pkgconf-3.0.7',
                'c926ff491cbd9a331a589160811bd97ab1749b4d5198a519338f2cdfabe6940a'),
    'hivex': ('hivex-1.3.24.tar.gz', 'hivex-1.3.24',
              'a52fa45cecc9a78adb2d28605d68261e4f1fd4514a778a5473013d2ccc8a193c'),
}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if platform.system() != 'Darwin' or platform.machine() != 'x86_64' or os.geteuid() == 0:
        raise RuntimeError('Build as an ordinary user on Intel macOS')
    inputs = args.inputs.resolve(strict=True)
    output = args.output.absolute()
    if output.exists() or output.is_symlink() or output.parent.resolve(strict=True) != output.parent:
        raise RuntimeError('Use a new output under an existing canonical directory')
    if any(c.isspace() for c in str(output)):
        raise RuntimeError('Autotools output path must not contain whitespace')
    if shutil.disk_usage(output.parent).free < 1024**3:
        raise RuntimeError('At least 1 GiB of free build space is required')
    verified = []
    for component, (filename, root, sha) in SOURCES.items():
        path = inputs / filename
        if path.is_symlink() or not path.is_file() or digest(path) != sha:
            raise RuntimeError('Source verification failed: ' + filename)
        verified.append(dict(component=component, file=filename, root=root,
                             sha256=sha, bytes=path.stat().st_size))
    os.umask(0o077)
    output.mkdir(mode=0o700)
    for name in ('logs', 'sources', 'tmp', 'lib'):
        (output / name).mkdir()
    report = dict(status='RUNNING', inputs=verified, steps=[], deployment_target='10.7',
                  previous_build_required=False, system_install=False,
                  physical_devices_accessed=False, live_registry_modified=False,
                  historical_os_tested=False, recipe_sha256=digest(__file__))
    result_path = output / 'BUILD-RESULT.json'
    save(result_path, report)
    clean = dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin', HOME=os.environ['HOME'],
                 TMPDIR=str(output / 'tmp'), LC_ALL='en_US.UTF-8')
    try:
        sdk = subprocess.check_output(['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-path'],
                                      text=True, env=clean).strip()
        report.update(sdk=sdk,
            host=subprocess.check_output(['/usr/bin/sw_vers'], text=True, env=clean),
            compiler=subprocess.check_output(['/usr/bin/clang', '--version'], text=True, env=clean))
        sources = {}
        for component, (filename, root, _) in SOURCES.items():
            with tarfile.open(inputs / filename) as archive:
                for member in archive.getmembers():
                    path = PurePosixPath(member.name)
                    if (path.is_absolute() or '..' in path.parts or not path.parts
                            or path.parts[0] != root or not (member.isfile() or member.isdir())):
                        raise RuntimeError('Unexpected archive member: ' + member.name)
                archive.extractall(output / 'sources', filter='data')
            sources[component] = output / 'sources' / root

        def step(name, command, cwd, env):
            print('BUILD ' + name, flush=True)
            row = dict(name=name, command=[str(x) for x in command], cwd=str(cwd),
                       log='logs/' + name + '.log', status='RUNNING')
            report['steps'].append(row)
            save(result_path, report)
            started = time.monotonic()
            with (output / row['log']).open('wb') as stream:
                result = subprocess.run(row['command'], cwd=cwd, env=env, stdout=stream,
                                        stderr=subprocess.STDOUT, timeout=900)
            row.update(exit_code=result.returncode, seconds=round(time.monotonic()-started, 3),
                       status='PASS' if result.returncode == 0 else 'FAIL')
            save(result_path, report)
            if result.returncode:
                print((output / row['log']).read_text(errors='replace')[-6000:], flush=True)
                raise RuntimeError('Build step failed: ' + name)

        def environment(target):
            return dict(clean, CC='/usr/bin/clang', MACOSX_DEPLOYMENT_TARGET=target,
                        CFLAGS='-O2 -mmacosx-version-min=' + target + ' -isysroot ' + shlex.quote(sdk),
                        LDFLAGS='-mmacosx-version-min=' + target + ' -isysroot ' + shlex.quote(sdk))

        host_dir = output / 'pkgconf-build'
        host_dir.mkdir()
        host_prefix = output / 'build-tools'
        env = environment('10.15')
        step('pkgconf-configure', ['/bin/sh', sources['pkgconf'] / 'configure',
             '--prefix=' + str(host_prefix), '--disable-shared', '--enable-static',
             '--with-pkg-config-dir=' + str(host_prefix / 'lib/pkgconfig')], host_dir, env)
        step('pkgconf-build', ['/usr/bin/make', '-j2'], host_dir, env)
        step('pkgconf-install-private', ['/usr/bin/make', 'install'], host_dir, env)
        env = environment('10.7')
        env['LDFLAGS'] += ' -Wl,-e,start -Wl,-adhoc_codesign'
        env.update(PKG_CONFIG=str(host_prefix / 'bin/pkgconf'), PKG_CONFIG_PATH='',
                   PKG_CONFIG_LIBDIR=str(host_prefix / 'lib/pkgconfig'))
        for function in ('open_memstream', 'futimens', 'utimensat', 'clock_gettime', 'getentropy'):
            env['ac_cv_func_' + function] = 'no'
        report['environment'] = env
        source = sources['hivex']
        step('hivex-configure', ['/bin/sh', source / 'configure', '--prefix=' + str(output / 'prefix'),
             '--disable-shared', '--enable-static', '--disable-ocaml', '--disable-perl',
             '--disable-python', '--disable-ruby', '--without-readline', '--disable-nls'], source, env)
        step('hivex-gnulib', ['/usr/bin/make', '-C', 'gnulib/lib', '-j2'], source, env)
        step('hivex-library', ['/usr/bin/make', '-C', 'lib', '-j2', 'VERSION_SCRIPT_FLAGS='], source, env)
        step('hivex-header-check', ['/usr/bin/make', '-C', 'lib', 'check', 'VERSION_SCRIPT_FLAGS='], source, env)
        library = output / 'lib/libhivex.0.dylib'
        step('hivex-dylib', ['/usr/bin/clang', '-arch', 'x86_64', '-mmacosx-version-min=10.7',
             '-isysroot', sdk, '-dynamiclib', '-Wl,-adhoc_codesign',
             '-Wl,-install_name,@loader_path/libhivex.0.dylib', '-Wl,-all_load',
             source / 'lib/.libs/libhivex.a', '-liconv', '-o', library], source, env)
        report.update(status='PASS', library=dict(path=str(library), sha256=digest(library),
                                                  bytes=library.stat().st_size),
                      acceptance_tests_required=True)
        print('BUILD COMPLETE: independent fixture/relocation checks still required.', flush=True)
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        save(result_path, report)


if __name__ == '__main__':
    main()
