"""Host compatibility selection; never substitute an incompatible payload."""
from pathlib import Path
import hashlib
import re


class IncompatibleHost(ValueError):
    pass


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', value):
        raise IncompatibleHost('Unrecognised macOS version')
    parts = tuple(int(x) for x in value.split('.'))
    return parts + (0,) * (3 - len(parts))


def select(profiles, os_version, architecture):
    host = version(os_version)
    if not ((host[0] == 10 and 7 <= host[1] <= 15) or host[0] in (11, 12, 13, 14, 15, 26)):
        raise IncompatibleHost('macOS version is outside the explicitly targeted host families')
    matches = []
    for p in profiles:
        low, high = version(p['minimum']), version(p['maximum'])
        if low > high:
            raise IncompatibleHost('Invalid runtime profile bounds')
        if (p.get('available') is True and architecture in p['architectures']
                and low <= host <= high):
            matches.append(p)
    if not matches:
        raise IncompatibleHost('No validated bundled runtime for macOS %s (%s). '
                               'No installation will be attempted.' % (os_version, architecture))
    if len(matches) != 1:
        raise IncompatibleHost('Overlapping runtime profiles; refusing an ambiguous choice')
    return matches[0]


def validate_payload(resources, profile, operation='install', note=None, cache=None):
    if operation not in ('install','manage','rebuild','diagnostics','efi-access','reset-menu','experimental','saved-files','startup-check'):
        raise IncompatibleHost('Unknown payload operation')
    root = Path(resources).resolve(strict=True)
    if not profile.get('files'):
        raise IncompatibleHost('Empty runtime integrity manifest')
    selected = {name:expected for name,expected in profile['files'].items()
                if not (operation != 'install' and isinstance(name,str) and
                        name.startswith(('private-win7-media/','private-win7-delta/','community-drivers/')))}
    if note:
        from operation_progress import Meter
        meter = Meter(note,'Checking bundled dependencies',len(selected),'files checked')
    hashed = reused = 0
    for number, (name, expected) in enumerate(selected.items(),1):
        if not isinstance(name, str) or name.startswith('/') or '..' in Path(name).parts or '\\' in name:
            raise IncompatibleHost('Unsafe payload path')
        # Management neither installs Windows nor deploys these payloads.
        # Keep verifying all code, runtimes, tools and menu assets; do not hash
        # several GB of unused installation media merely to open the list.
        if operation != 'install' and name.startswith(('private-win7-media/','private-win7-delta/','community-drivers/')):
            continue
        path = (root / name).resolve(strict=True)
        if root not in path.parents or not path.is_file():
            raise IncompatibleHost('Payload escapes the app bundle')
        def identity(node):
            return (str(path), expected, node.st_dev, node.st_ino, node.st_size,
                    node.st_mtime_ns, node.st_ctime_ns, node.st_mode)
        before = path.stat()
        stamp = identity(before)
        # HFS+ can report change times only to the second. An edit followed by
        # restoring mtime can then retain every cached field in that second.
        # Rehash on coarse timestamps rather than trust that cache hit.
        precise_change_time = before.st_ctime_ns % 1000000000 != 0
        if precise_change_time and cache is not None and cache.get(name) == stamp:
            reused += 1
            if note:meter.update(number,name)
            continue
        actual = hashlib.sha256()
        with path.open('rb') as stream:
            import os
            if identity(os.fstat(stream.fileno())) != stamp:
                raise IncompatibleHost('Bundled file changed during verification: ' + name)
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                actual.update(chunk)
            if identity(os.fstat(stream.fileno())) != stamp or identity(path.stat()) != stamp:
                raise IncompatibleHost('Bundled file changed during verification: ' + name)
        if actual.hexdigest() != expected:
            raise IncompatibleHost('Bundled file failed verification: ' + name)
        if cache is not None:cache[name] = stamp
        hashed += 1
        if note:meter.update(number,name)
    for name in ('python', 'worker', 'wim', 'mkntfs', 'ntfscat'):
        if profile.get(name) not in profile['files']:
            raise IncompatibleHost('Unverified executable entry: ' + name)
    if note and cache is not None:
        note('Bundled dependencies: %d file(s) hashed; %d unchanged file(s) already verified in this app session.' % (hashed,reused))
