"""Build the older-host Inno fallback plus Boost/liblzma from original archives.

Needs Python 3.11+ and Apple's command-line build tools on Intel macOS. All
archives are explicit, pinned inputs. CMake is a developer tool, not an app
runtime dependency. No old app, prebuilt static library or previous build
directory is required. Only unused Boost documentation is omitted from scratch
extraction; the complete, pinned source archive remains an explicit input.
"""
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

INPUTS = {
    'cmake-3.31.8-macos-universal.tar.gz': 'd1449f969c54d5c00886d5b643340d493dfb3c81cb39ee29b35453395c11ebf7',
    'boost_1_83_0.tar.bz2': '6478edfe2f3305127cffe8caf73ea0176c53769f4bf1585be237eb30798c3b8e',
    'xz.tar.xz': '890966ec3f5d5cc151077879e157c0593500a522f413ac50ba26d22a9a145214',
    'innoextract.tar.gz': '6344a69fc1ed847d4ed3e272e0da5998948c6b828cb7af39c6321aba6cf88126',
}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def selected(name, member):
    parts = PurePosixPath(member.name).parts
    assert not member.name.startswith('/') and '..' not in parts
    assert member.isfile() or member.isdir() or (name.startswith('cmake-') and member.issym())
    if name == 'boost_1_83_0.tar.bz2':
        # Retain all sources, headers, build configuration and test code.
        if 'doc' in parts[1:-1] or 'docs' in parts[1:-1] or parts[1:2] == ('more',):
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    assert platform.system() == 'Darwin' and platform.machine() == 'x86_64' and os.geteuid() != 0
    inputs = args.inputs.resolve(strict=True)
    for name, expected in INPUTS.items():
        assert digest(inputs / name) == expected, name
    out = args.output.absolute()
    assert out.parent.resolve(strict=True) == out.parent and not out.exists() and not out.is_symlink()
    assert not any(c.isspace() for c in str(out)), 'Use a build path without whitespace'
    # Account for allocated file blocks, directory entries and build outputs
    # before extracting. Keep at least 768 MiB free for the host throughout.
    plans = {}
    for name in INPUTS:
        row = dict(included_files=0, included_bytes=0, excluded_files=0,
                   excluded_bytes=0, conservative_allocated_bytes=0)
        with tarfile.open(inputs / name) as archive:
            for member in archive:
                keep = selected(name, member)
                if member.isfile():
                    field = 'included' if keep else 'excluded'
                    row[field + '_files'] += 1
                    row[field + '_bytes'] += member.size
                if keep:
                    row['conservative_allocated_bytes'] += ((member.size + 4095) // 4096 + 1) * 4096
        plans[name] = row
    reserve = 768 * 1024**2
    required = sum(row['conservative_allocated_bytes'] for row in plans.values()) + 128 * 1024**2 + reserve
    free = shutil.disk_usage(out.parent).free
    assert free >= required, ('Insufficient scratch space', free, required)
    os.umask(0o077)
    out.mkdir(mode=0o700)
    for name in ('source', 'build-tools', 'tools', 'prefix', 'logs', 'notices', 'tmp'):
        (out / name).mkdir()
    report = dict(status='RUNNING', recipe_sha256=digest(__file__), sources=INPUTS,
                  steps=[], deployment_target='10.7', system_install=False,
                  previous_build_required=False, historical_os_tested=False,
                  original_source_patched=False, extraction_plan=plans,
                  required_free_bytes=required, initial_free_bytes=free,
                  reserved_host_free_bytes=reserve, dynamic_timestamp_fallback=True)
    receipt = out / 'BUILD-RESULT.json'
    env = dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin', HOME=os.environ['HOME'],
               TMPDIR=str(out / 'tmp'), LC_ALL='en_US.UTF-8', MACOSX_DEPLOYMENT_TARGET='10.7')

    def save():
        receipt.write_text(json.dumps(report, indent=2) + '\n')

    def run(label, command, cwd, timeout=1800):
        assert shutil.disk_usage(out).free >= reserve, 'Host free-space reserve reached'
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
        save()
        report['host'] = subprocess.check_output(['/usr/bin/sw_vers'], env=env, text=True)
        report['compiler'] = subprocess.check_output(['/usr/bin/clang', '--version'], env=env, text=True)
        sdk = subprocess.check_output(['/usr/bin/xcrun', '--show-sdk-path'], env=env, text=True).strip()
        report['sdk'] = sdk
        for name in INPUTS:
            print('EXTRACT ' + name, flush=True)
            destination = out / ('build-tools' if name.startswith('cmake-') else 'source')
            with tarfile.open(inputs / name) as archive:
                members = [member for member in archive.getmembers() if selected(name, member)]
                archive.extractall(destination, members=members, filter='data')
        prefix = out / 'prefix'
        boost = out / 'source/boost_1_83_0'
        xz = out / 'source/xz-5.8.2'
        source = out / 'source/innoextract-1.9'
        cmakes = list((out / 'build-tools').glob('cmake-*/CMake.app/Contents/bin/cmake'))
        assert len(cmakes) == 1
        cmake = cmakes[0]
        report['cmake_version'] = subprocess.check_output([str(cmake), '--version'], env=env, text=True)
        for component, paths in (
            ('innoextract', [source / 'LICENSE']),
            ('boost', [boost / 'LICENSE_1_0.txt']),
            ('xz', sorted(xz.glob('COPYING*'))),
        ):
            folder = out / 'notices' / component
            folder.mkdir()
            for path in paths:
                shutil.copy2(path, folder / path.name)
        run('boost-bootstrap', ['/bin/sh', './bootstrap.sh', '--with-toolset=clang',
            '--with-libraries=filesystem,iostreams,date_time,system,program_options',
            '--prefix=' + str(prefix)], boost)
        run('boost-static-build', ['./b2', '-j2', 'toolset=clang', 'variant=release', 'link=static',
            'threading=multi', 'cxxstd=11', 'cxxflags=-mmacosx-version-min=10.7',
            'cflags=-mmacosx-version-min=10.7', 'linkflags=-mmacosx-version-min=10.7',
            '-sBZIP2_INCLUDE=' + sdk + '/usr/include', '-sZLIB_INCLUDE=' + sdk + '/usr/include', 'stage'], boost)
        run('xz-configure', ['./configure', '--prefix=' + str(prefix), '--disable-shared',
            '--enable-static', '--disable-xz', '--disable-xzdec', '--disable-lzmadec',
            '--disable-lzmainfo', '--disable-doc', '--disable-scripts', '--disable-nls'], xz)
        run('xz-build', ['/usr/bin/make', '-j2'], xz)
        run('xz-check', ['/usr/bin/make', '-j2', 'check'], xz)
        run('xz-private-install', ['/usr/bin/make', 'install'], xz)
        build = out / 'inno-build'
        run('inno-configure', [cmake, '-S', source, '-B', build, '-DCMAKE_BUILD_TYPE=Release',
            '-DCMAKE_OSX_DEPLOYMENT_TARGET=10.7', '-DCMAKE_OSX_ARCHITECTURES=x86_64',
            '-DCMAKE_PREFIX_PATH=' + str(prefix), '-DBOOST_ROOT=' + str(boost),
            '-DBoost_INCLUDE_DIR=' + str(boost), '-DBOOST_LIBRARYDIR=' + str(boost / 'stage/lib'),
            '-DBoost_NO_BOOST_CMAKE=ON', '-DBoost_NO_SYSTEM_PATHS=ON', '-DBoost_USE_STATIC_LIBS=ON',
            '-DLZMA_USE_STATIC_LIBS=ON', '-DCXX_STD_VERSION=2011', '-DSET_OPTIMIZATION_FLAGS=OFF',
            '-DINNOEXTRACT_HAVE_UTIMENSAT:BOOL=OFF', '-DUSE_DYNAMIC_UTIMENSAT:BOOL=ON',
            '-DCMAKE_INSTALL_PREFIX=' + str(prefix)], out)
        config = (build / 'configure.hpp').read_text()
        for name, value in (('INNOEXTRACT_HAVE_UTIMENSAT', 0),
                            ('INNOEXTRACT_HAVE_DYNAMIC_UTIMENSAT', 1), ('INNOEXTRACT_HAVE_UTIMES', 1)):
            assert '#define %s %d' % (name, value) in config
        (out / 'generated-config.hpp').write_text(config)
        run('inno-build', [cmake, '--build', build, '-j2'], out)
        tool = out / 'tools/innoextract'
        shutil.copy2(build / 'innoextract', tool)
        links = subprocess.check_output(['/usr/bin/otool', '-L', str(tool)], text=True)
        assert all(line.strip().startswith(('/usr/lib/', '/System/Library/'))
                   for line in links.splitlines()[1:]), links
        imports = subprocess.check_output(['/usr/bin/nm', '-ju', str(tool)], text=True).splitlines()
        assert '_utimensat' not in imports and '_dlsym' in imports and '_utimes' in imports
        (out / 'imports.txt').write_text('\n'.join(imports) + '\n')
        libraries = sorted((boost / 'stage/lib').glob('*.a')) + [prefix / 'lib/liblzma.a']
        assert len(libraries) == 7
        report.update(status='PASS', tool=dict(path=str(tool), bytes=tool.stat().st_size,
                      sha256=digest(tool)), dependencies=links, acceptance_required=True,
                      fresh_static_libraries={p.relative_to(out).as_posix(): digest(p) for p in libraries},
                      final_free_bytes=shutil.disk_usage(out).free)
        print('BUILD COMPLETE; extraction acceptance still required.', flush=True)
    except BaseException as error:
        report.update(status='FAIL', error=repr(error))
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
