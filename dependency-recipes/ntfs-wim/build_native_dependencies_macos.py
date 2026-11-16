"""Build pinned pkgconf, NTFS-3G and wimlib in a new, ordinary-user prefix.

Developer recipe: no previous app/build directory is needed, no downloads,
sudo, mount commands, devices or installed-system modifications are used.
Python 3.11+ and Apple's Command Line Tools are build-time prerequisites.
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
    'ntfs': ('ntfs-3g_ntfsprogs-2026.9.28.tgz', 'ntfs-3g-2026.9.28',
             '350d9415c59f7c3fa74e23985ad2d56423c6a168337368f672e2e362d8f295c8'),
    'wim': ('wimlib-1.14.5.tar.gz', 'wimlib-1.14.5',
            '84221a3abd5b91228f15f8e6065c335a336237b5738197b75bf419eea561a194'),
}
LEGACY_FALLBACKS = ('futimens', 'utimensat', 'openat', 'fstatat', 'readlinkat',
                    'fdopendir', 'clock_gettime', 'clock_getres', 'getentropy',
                    'preadv', 'pwritev')


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
        raise RuntimeError('Output must be a new path under an existing real directory')
    if any(c.isspace() for c in str(output)):
        raise RuntimeError('The Autotools build output path must not contain whitespace')
    if shutil.disk_usage(output.parent).free < 3 * 1024**3:
        raise RuntimeError('At least 3 GiB of free build space is required')
    verified = []
    for component, (filename, root, sha256) in SOURCES.items():
        path = inputs / filename
        if path.is_symlink() or not path.is_file() or digest(path) != sha256:
            raise RuntimeError('Source archive verification failed: ' + filename)
        verified.append(dict(component=component, file=filename, root=root,
                             sha256=sha256, bytes=path.stat().st_size))
    os.umask(0o077)
    output.mkdir(mode=0o700)
    (output / 'logs').mkdir()
    (output / 'sources').mkdir()
    (output / 'tmp').mkdir()
    report = dict(status='RUNNING', inputs=verified, steps=[], profiles=[],
                  previous_build_required=False, system_install=False,
                  physical_devices_accessed=False, historical_os_tested=False)
    report_path = output / 'BUILD-RESULT.json'
    save(report_path, report)
    clean = dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin', HOME=os.environ['HOME'],
                 TMPDIR=str(output / 'tmp'), LC_ALL='en_US.UTF-8')
    try:
        sdk = subprocess.check_output(['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-path'],
                                      env=clean, text=True).strip()
        report['host'] = subprocess.check_output(['/usr/bin/sw_vers'], env=clean, text=True)
        report['compiler'] = subprocess.check_output(['/usr/bin/clang', '--version'], env=clean, text=True)
        report['sdk'] = sdk
        report['sdk_version'] = subprocess.check_output(
            ['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-version'], env=clean, text=True).strip()
        report['recipe_sha256'] = digest(__file__)
        sources = {}
        for component, (filename, root, _) in SOURCES.items():
            with tarfile.open(inputs / filename) as archive:
                members = archive.getmembers()
                # The pinned archives have only ordinary files/directories.
                # Reject rather than interpret links, devices, or traversal.
                for member in members:
                    path = PurePosixPath(member.name)
                    if (path.is_absolute() or '..' in path.parts or not path.parts
                            or path.parts[0] != root or not (member.isfile() or member.isdir())):
                        raise RuntimeError('Unexpected source archive member: ' + member.name)
                archive.extractall(output / 'sources', filter='data')
            sources[component] = output / 'sources' / root

        def step(name, command, cwd, env):
            print('BUILD ' + name, flush=True)
            row = dict(name=name, command=[str(x) for x in command], cwd=str(cwd),
                       log='logs/' + name + '.log', status='RUNNING')
            report['steps'].append(row)
            save(report_path, report)
            started = time.monotonic()
            with (output / row['log']).open('wb') as log:
                result = subprocess.run(row['command'], cwd=cwd, env=env,
                                        stdout=log, stderr=subprocess.STDOUT, timeout=1200)
            row.update(exit_code=result.returncode, seconds=round(time.monotonic() - started, 3),
                       status='PASS' if result.returncode == 0 else 'FAIL')
            save(report_path, report)
            if result.returncode:
                print((output / row['log']).read_text(errors='replace')[-7000:], flush=True)
                raise RuntimeError('Build step failed: ' + name)

        def build_env(target, legacy=False):
            env = dict(clean, CC='/usr/bin/clang', CXX='/usr/bin/clang++',
                       MACOSX_DEPLOYMENT_TARGET=target,
                       CFLAGS='-O2 -mmacosx-version-min=' + target + ' -isysroot ' + shlex.quote(sdk),
                       LDFLAGS='-mmacosx-version-min=' + target + ' -isysroot ' + shlex.quote(sdk))
            env['CXXFLAGS'] = env['CFLAGS']
            if legacy:
                env['LDFLAGS'] += ' -Wl,-e,start -Wl,-adhoc_codesign'
                for function in LEGACY_FALLBACKS:
                    env['ac_cv_func_' + function] = 'no'
            return env

        host_dir = output / 'build-pkgconf'
        host_dir.mkdir()
        host_prefix = output / 'build-tools'
        env = build_env('10.15')
        step('pkgconf-configure', ['/bin/sh', sources['pkgconf'] / 'configure',
             '--prefix=' + str(host_prefix), '--disable-shared', '--enable-static',
             '--with-pkg-config-dir=' + str(host_prefix / 'lib/pkgconfig')], host_dir, env)
        step('pkgconf-build', ['/usr/bin/make', '-j2'], host_dir, env)
        step('pkgconf-install-private', ['/usr/bin/make', 'install'], host_dir, env)
        for name, target in [('modern', '10.15'), ('legacy', '10.7')]:
            part = output / name
            part.mkdir()
            prefix = part / 'prefix'
            env = build_env(target, legacy=name == 'legacy')
            env.update(PKG_CONFIG=str(host_prefix / 'bin/pkgconf'),
                       PKG_CONFIG_LIBDIR=str(prefix / 'lib/pkgconfig'), PKG_CONFIG_PATH='')
            report['profiles'].append(dict(name=name, deployment_target=target,
                                           environment=env, prefix=str(prefix)))
            ntfs = part / 'ntfs'
            ntfs.mkdir()
            config = ['/bin/sh', sources['ntfs'] / 'configure',
                      '--prefix=' + str(prefix), '--exec-prefix=' + str(prefix),
                      '--disable-ntfs-3g', '--enable-ntfsprogs', '--disable-shared',
                      '--enable-static', '--without-uuid']
            if name == 'legacy':
                config.append('--disable-plugins')
            step(name + '-ntfs-configure', config, ntfs, env)
            step(name + '-ntfs-build', ['/usr/bin/make', '-j2'], ntfs, env)
            # Upstream otherwise defaults root* directories outside --prefix.
            step(name + '-ntfs-install-private', ['/usr/bin/make', 'install',
                 'rootbindir=' + str(prefix / 'bin'), 'rootsbindir=' + str(prefix / 'sbin'),
                 'rootlibdir=' + str(prefix / 'lib')], ntfs, env)
            wim = part / 'wim'
            wim.mkdir()
            overrides = dict(LIBNTFS_3G_CFLAGS='-I' + str(prefix / 'include'),
                             LIBNTFS_3G_LIBS='-L' + str(prefix / 'lib') +
                             ' -lntfs-3g -framework CoreFoundation')
            wim_env = dict(env, **overrides)
            step(name + '-wim-configure', ['/bin/sh', sources['wim'] / 'configure',
                 '--prefix=' + str(prefix), '--with-ntfs-3g', '--without-fuse',
                 '--disable-shared', '--enable-static'], wim, wim_env)
            make_args = [key + '=' + value for key, value in overrides.items()]
            step(name + '-wim-build', ['/usr/bin/make', '-j2'] + make_args, wim, wim_env)
            step(name + '-wim-install-private', ['/usr/bin/make', 'install'] + make_args, wim, wim_env)
            tools = {}
            for filename, relative in [('wimlib-imagex', 'bin/wimlib-imagex'),
                                       ('mkntfs', 'sbin/mkntfs'), ('ntfscat', 'bin/ntfscat')]:
                path = prefix / relative
                if not path.is_file() or not os.access(path, os.X_OK):
                    raise RuntimeError('Expected helper missing: ' + str(path))
                tools[filename] = dict(path=str(path), sha256=digest(path), bytes=path.stat().st_size)
            report['profiles'][-1]['tools'] = tools
        report.update(status='PASS', acceptance_tests_required=True)
        print('BUILD COMPLETE: both profiles; acceptance/relocation tests still required.', flush=True)
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        save(report_path, report)


if __name__ == '__main__':
    main()
