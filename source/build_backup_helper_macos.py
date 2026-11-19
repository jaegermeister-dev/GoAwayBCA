"""Build pinned NTFS backup tools in a new, ordinary-user development folder.

The output is not installed or copied into any existing application. The source
archive is GPL-2.0-or-later NTFS-3G; retain its notices and corresponding source.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import shlex
import subprocess
import sys
import tarfile

SOURCE_SHA256 = '350d9415c59f7c3fa74e23985ad2d56423c6a168337368f672e2e362d8f295c8'
SOURCE_URL = 'https://download.tuxera.com/opensource/ntfs-3g_ntfsprogs-2026.9.28.tgz'
PKGCONF_SHA256 = 'c926ff491cbd9a331a589160811bd97ab1749b4d5198a519338f2cdfabe6940a'


def build(archive, pkgconf_archive, out):
    assert sys.platform == 'darwin' and os.geteuid() != 0
    archive = Path(archive).resolve(strict=True)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == SOURCE_SHA256
    out = Path(out).absolute()
    out.mkdir(mode=0o700)  # Refuse to overwrite any previous build.
    sources = out / 'sources'; sources.mkdir()
    pkgconf_archive=Path(pkgconf_archive).resolve(strict=True)
    assert hashlib.sha256(pkgconf_archive.read_bytes()).hexdigest()==PKGCONF_SHA256
    for filename, expected_directory in ((archive,'ntfs-3g-2026.9.28'),(pkgconf_archive,'pkgconf-3.0.7')):
        with tarfile.open(str(filename)) as tar:
            for entry in tar.getmembers():
                name = PurePosixPath(entry.name)
                assert not name.is_absolute() and '..' not in name.parts
                assert name.parts[0] == expected_directory
                assert entry.isdir() or entry.isfile(), 'Unexpected source archive link/special file'
            tar.extractall(str(sources))
    source = sources / 'ntfs-3g-2026.9.28'
    sdk = subprocess.check_output(['/usr/bin/xcrun', '--show-sdk-path'], universal_newlines=True).strip()
    compiler = subprocess.check_output(['/usr/bin/clang', '--version'], universal_newlines=True)
    environment = dict(os.environ, CC='/usr/bin/clang', LC_ALL='C', TZ='UTC',
        SOURCE_DATE_EPOCH='1790553600', MACOSX_DEPLOYMENT_TARGET='10.7',
        CFLAGS='-O2 -mmacosx-version-min=10.7 -isysroot ' + sdk + ' -ffile-prefix-map=' + str(out) + '=/GoAwayBCA-NTFS',
        LDFLAGS='-mmacosx-version-min=10.7 -Wl,-e,start -Wl,-adhoc_codesign -isysroot ' + sdk)
    for feature in ('futimens', 'utimensat', 'openat', 'fstatat', 'readlinkat', 'fdopendir',
                    'clock_gettime', 'clock_getres', 'getentropy', 'preadv', 'pwritev'):
        environment['ac_cv_func_' + feature] = 'no'
    pkgwork=out/'pkgconf-build';pkgwork.mkdir()
    for name, command in (
        ('pkgconf-configure',['/bin/sh',str(sources/'pkgconf-3.0.7/configure'),'--disable-shared','--enable-static','--prefix=/GoAwayBCA-Pkgconf']),
        ('pkgconf-make',['/usr/bin/make','-j2'])):
        print(name,flush=True)
        with (out/(name+'.log')).open('wb') as log:
            completed=subprocess.run(command,cwd=str(pkgwork),env=environment,stdout=log,stderr=subprocess.STDOUT)
        if completed.returncode:
            print((out/(name+'.log')).read_text(errors='replace')[-6000:])
            raise RuntimeError(name+' failed')
    environment['PKG_CONFIG']=str(pkgwork/'pkgconf')
    environment['PKG_CONFIG_LIBDIR']=str(out/'no-system-packages')
    work = out / 'build'; work.mkdir()
    commands = [
        ('configure', ['/bin/sh', str(source / 'configure'), '--prefix=/GoAwayBCA-NTFS',
                       '--disable-ntfs-3g', '--enable-ntfsprogs', '--disable-shared', '--enable-static',
                       '--without-uuid', '--disable-plugins']),
        ('make', ['/usr/bin/make', '-j2'])]
    for name, command in commands:
        print(name + ' NTFS backup helpers', flush=True)
        with (out / (name + '.log')).open('wb') as log:
            result = subprocess.run(command, cwd=str(work), env=environment, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            print((out / (name + '.log')).read_text(errors='replace')[-8000:])
            raise RuntimeError(name + ' failed')
    tools = out / 'tools'; tools.mkdir()
    rows = []
    for name in ('ntfsclone', 'ntfsresize', 'ntfsinfo'):
        binary = work / 'ntfsprogs' / name
        assert binary.is_file() and binary.read_bytes()[:4] == b'\xcf\xfa\xed\xfe'
        target = tools / name
        shutil.copyfile(str(binary), str(target)); target.chmod(0o755)
        dependencies = subprocess.check_output(['/usr/bin/otool', '-L', str(target)], universal_newlines=True)
        assert all(line.strip().startswith(('/usr/lib/', '/System/Library/'))
                   for line in dependencies.splitlines()[1:] if line.strip()), dependencies
        rows.append(dict(name=name, sha256=hashlib.sha256(target.read_bytes()).hexdigest(), dependencies=dependencies))
    probe_source=Path(__file__).resolve().with_name('backup_metadata_probe.c')
    probe=tools/'backup-metadata-probe'
    command=[environment['CC']]+shlex.split(environment['CFLAGS'])+[
        '-DHAVE_CONFIG_H','-I'+str(work),'-I'+str(source/'include'),str(probe_source),
        str(work/'libntfs-3g/.libs/libntfs-3g.a'),'-framework','CoreFoundation']+shlex.split(environment['LDFLAGS'])+['-o',str(probe)]
    with (out/'metadata-probe-build.log').open('wb') as log:
        subprocess.run(command,env=environment,stdout=log,stderr=subprocess.STDOUT,check=True)
    dependencies=subprocess.check_output(['/usr/bin/otool','-L',str(probe)],universal_newlines=True)
    assert all(line.strip().startswith(('/usr/lib/','/System/Library/'))
               for line in dependencies.splitlines()[1:] if line.strip()),dependencies
    rows.append(dict(name=probe.name,sha256=hashlib.sha256(probe.read_bytes()).hexdigest(),
        source_sha256=hashlib.sha256(probe_source.read_bytes()).hexdigest(),dependencies=dependencies))
    result = dict(source_sha256=SOURCE_SHA256, source_url=SOURCE_URL, pkgconf_sha256=PKGCONF_SHA256, compiler=compiler, sdk=sdk,
                  minimum_requested='10.7', historical_host_not_tested=True, files=rows)
    (out / 'BUILD.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--pkgconf-archive', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    build(args.archive, args.pkgconf_archive, args.output)
