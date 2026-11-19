#!/usr/bin/env python3
"""Reproduce the Beta 2 app from matching source and pinned dependency inputs.

This command never installs Windows, mounts EFI, or changes startup settings.
"""
import argparse
import calendar
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import zipfile

sys.dont_write_bytecode = True


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inside(root, relative):
    p = Path(relative)
    require(isinstance(relative, str) and relative and not p.is_absolute()
            and '..' not in p.parts and '\\' not in relative, 'Unsafe build path')
    result = root / p
    require(root in result.resolve().parents, 'Build path escapes its root')
    return result


def manifest(root):
    rows = {}
    for p in sorted(root.rglob('*')):
        name = p.relative_to(root).as_posix()
        mode = p.lstat().st_mode
        if stat.S_ISLNK(mode):
            require(root in p.resolve(strict=True).parents, 'External symlink: ' + name)
            rows[name] = {'link': os.readlink(p)}
        elif stat.S_ISREG(mode):
            rows[name] = {'bytes': p.stat().st_size, 'sha256': sha(p), 'mode': stat.S_IMODE(mode)}
        else:
            require(stat.S_ISDIR(mode), 'Unexpected file type: ' + name)
    return rows


def run(command, log, timeout=180):
    with log.open('wb') as out:
        p = subprocess.run([str(x) for x in command], stdout=out, stderr=subprocess.STDOUT, timeout=timeout)
    require(p.returncode == 0, 'Build/check failed; see ' + str(log))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, help='GoAwayBCA-Beta-2-Build-Inputs.zip')
    parser.add_argument('--output', required=True, help='A new directory outside this source tree')
    args = parser.parse_args()
    require(sys.platform == 'darwin' and platform.machine() == 'x86_64' and os.geteuid() != 0,
            'Build using an ordinary account on Intel macOS, without sudo.')
    root = Path(__file__).resolve().parent
    output = Path(args.output).expanduser().absolute()
    require(output.parent.is_dir() and not output.exists() and not output.is_symlink(), 'Output must be a new directory.')
    output = output.resolve()
    require(root != output and root not in output.parents, 'Output must be outside the source tree.')
    require(shutil.disk_usage(output.parent).free > 1024**3, 'Allow at least 1 GiB of free space.')
    inputs = Path(args.inputs).expanduser().resolve(strict=True)
    recipe = json.loads((root / 'recipe/assembly.json').read_text())
    expected = json.loads((root / 'recipe/APP-FILE-HASHES.json').read_text())
    require(recipe['schema'] == 1 and recipe['version'] == '0.2.0-beta.2', 'Unknown build recipe.')
    require(inputs.stat().st_size == recipe['input_bytes'] and sha(inputs) == recipe['input_sha256'], 'Dependency input checksum mismatch.')
    toolchain = json.loads((root / 'recipe/toolchain.json').read_text())
    compiler = subprocess.check_output(['/usr/bin/xcrun', 'clang', '--version'], universal_newlines=True)
    sdk = subprocess.check_output(['/usr/bin/xcrun', '--show-sdk-version'], universal_newlines=True).strip()
    require(compiler == toolchain['compiler'] and sdk == toolchain['sdk_version'],
            'Exact reproduction requires the recorded compiler and SDK; see BUILDING.md.')
    output.mkdir(mode=0o700)
    app = output / 'GoAwayBCA.app'
    app.mkdir()
    with zipfile.ZipFile(inputs) as z:
        require(len(z.namelist()) == len(set(z.namelist())), 'Duplicate build input paths.')
        wanted = {row['input'] for row in recipe['copies'].values() if 'input' in row}
        require(set(z.namelist()) == wanted, 'Unexpected/missing dependency inputs.')
        for name, row in recipe['copies'].items():
            data = inside(root, row['source']).read_bytes() if 'source' in row else z.read(row['input'])
            require(len(data) == expected[name]['bytes'] and hashlib.sha256(data).hexdigest() == expected[name]['sha256'],
                    'Source/input differs from the approved release: ' + name)
            target = inside(app, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as f:
                f.write(data)
            target.chmod(row['mode'])
            timestamp = calendar.timegm(tuple(row['timestamp']) + (0, 0, 0))
            os.utime(target, (timestamp, timestamp))
    for name, target in recipe['links'].items():
        link = inside(app, name)
        require(not Path(target).is_absolute(), 'Absolute symlink is not permitted.')
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
    for name in recipe['links']:
        require(app in (app / name).resolve(strict=True).parents, 'Symlink escapes rebuilt app.')
    (app / 'Contents/MacOS').mkdir(exist_ok=True)
    (app / 'Contents/Resources/bin').mkdir(parents=True, exist_ok=True)
    flags = ['/usr/bin/xcrun', 'clang', '-arch', 'x86_64', '-mmacosx-version-min=10.7', '-Wl,-e,start', '-Wl,-adhoc_codesign']
    print('Compiling the application and CPU probe from release source.', flush=True)
    run(flags + ['-Wunguarded-availability', '-fno-objc-arc', '-Wno-deprecated-declarations', '-Wall', '-Wextra',
                 '-framework', 'Cocoa', root / 'source/WindowsDeployer.m', '-o', app / 'Contents/MacOS/WindowsDeployer'], output / 'compile.log')
    run(flags + ['-Wall', '-Wextra', '-Werror', '-O2', root / 'source/cpu_probe.c', '-o', app / 'Contents/Resources/bin/cpu-probe'], output / 'cpu-compile.log')
    for name in recipe['generated']:
        (app / name).chmod(expected[name]['mode'])
    actual = manifest(app)
    differences = {name: {'expected':expected.get(name), 'actual':actual.get(name)}
                   for name in sorted(set(expected) | set(actual)) if actual.get(name) != expected.get(name)}
    (output / 'APP-COMPARISON.json').write_text(json.dumps({'exact_match':not differences,'entries':len(actual),'differences':differences}, indent=2)+'\n')
    require(not differences, 'The rebuilt app differs. See APP-COMPARISON.json; do not call it an exact reproduction.')
    resources = app / 'Contents/Resources'
    code = '''import sys,unittest,plistlib
sys.path[:0]=sys.argv[1:3]
from pathlib import Path
from runtime_profiles import validate_payload
resources=Path(sys.argv[1])
config=plistlib.loads((resources/'runtime-profiles.plist').read_bytes())
for profile in config['profiles']:validate_payload(resources,profile)
suite=unittest.defaultTestLoader.loadTestsFromNames(['test_startup_storage','test_startup_user_automatic','test_gui_session'])
result=unittest.TextTestRunner(stream=sys.stdout,verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
'''
    for profile in ('legacy', 'modern'):
        run([resources / ('payloads/'+profile+'/python/bin/python3'), '-I', '-B', '-c', code, resources, root / 'tests'],
            output / (profile+'-checks.log'))
    run([app / 'Contents/MacOS/WindowsDeployer', '--self-test'], output / 'native-checks.log')
    require(manifest(app) == expected, 'Checks changed the rebuilt app.')
    result = {'status':'PASS','app_entries':len(actual),'exact_app_match':True,
              'application_and_cpu_probe_compiled':True,'dependency_and_firmware_binaries_pinned':True,
              'both_runtime_checks_passed':True,'native_ui_checks_passed':True,
              'windows_installed':False,'boot_files_changed':False,
              'compiler':compiler,'sdk_version':sdk}
    (output / 'REBUILD-VERIFIED.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2), flush=True)


if __name__ == '__main__':
    main()
