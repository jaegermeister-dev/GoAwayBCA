"""Select and verify bundled, relocatable publisher-package extraction tools."""
import json
import os
from pathlib import Path
import plistlib
import stat
from community_payload import require, relative, digest
from publisher_drivers import file_spec
from runtime_profiles import version


def select(resources, os_version=None):
    root = Path(resources).resolve(strict=True)
    manifest = root/'publisher-helpers.json'
    require(manifest.is_file() and not manifest.is_symlink() and manifest.stat().st_size < 65536,
            'Bundled publisher-helper manifest missing or invalid')
    spec = json.loads(manifest.read_text())
    require(type(spec) is dict and spec.get('schema') == 1 and type(spec.get('profiles')) is list,
            'Invalid publisher-helper profiles')
    if os_version is None:
        os_version = plistlib.loads(Path('/System/Library/CoreServices/SystemVersion.plist').read_bytes())['ProductVersion']
    host = version(os_version)
    selected = [p for p in spec['profiles'] if version(p['minimum']) <= host <= version(p['maximum'])]
    require(len(selected) == 1, 'No compatible bundled publisher extractor for macOS '+os_version+
            '. Driver preparation cannot continue; no external dependency installation will be requested.')
    files = selected[0].get('tools')
    require(type(files) is dict and set(files) == {'7zip','inno'}, 'Incomplete publisher helpers')
    result = {}
    for name,row in files.items():
        file_spec(row); member = relative(row.get('path')); path = root/member
        require(path.resolve(strict=True) == path and stat.S_ISREG(path.lstat().st_mode)
                and os.access(str(path),os.X_OK) and path.stat().st_size == row['bytes']
                and digest(path) == row['sha256'], 'Bundled extraction helper changed: '+name)
        result[name] = path
    return result
