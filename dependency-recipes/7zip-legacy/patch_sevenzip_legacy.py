"""Apply four narrowly scoped, recorded old-macOS adaptations to fresh source."""
from pathlib import Path
import hashlib
import json


def apply(root, evidence):
    root = Path(root)
    windows = root / 'CPP/Windows'
    original = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob('*') if p.is_file()}
    header = Path(__file__).with_name('sevenzip_legacy_time.h')
    destination = windows / 'GoAwayBCALegacyTime.h'
    assert not destination.exists()
    destination.write_bytes(header.read_bytes())
    changes = {
        'FileDir.cpp': [
            ('#include "StdAfx.h"', '#include "StdAfx.h"\n#include "GoAwayBCALegacyTime.h"'),
            ('  return utimensat(AT_FDCWD, path, times, flags) == 0;',
             '#if defined(__APPLE__) && __ENVIRONMENT_MAC_OS_X_VERSION_MIN_REQUIRED__ < 101300\n'
             '  return GoAwayBCAPathTimes(path, times, flags) == 0;\n#else\n'
             '  return utimensat(AT_FDCWD, path, times, flags) == 0;\n#endif')],
        'FileIO.cpp': [
            ('#include "StdAfx.h"', '#include "StdAfx.h"\n#include "GoAwayBCALegacyTime.h"'),
            ('        if (futimens(_handle, times) == 0)',
             '#if defined(__APPLE__) && __ENVIRONMENT_MAC_OS_X_VERSION_MIN_REQUIRED__ < 101300\n'
             '        if (GoAwayBCAFutimens(_handle, times) == 0)\n#else\n'
             '        if (futimens(_handle, times) == 0)\n#endif')],
        'FileFind.cpp': [
            ('  int res = fstatat(dirfd(_dir), de.Name, &st, followLink ? 0 : AT_SYMLINK_NOFOLLOW);',
             '#if defined(__APPLE__) && __ENVIRONMENT_MAC_OS_X_VERSION_MIN_REQUIRED__ < 101000\n'
             '  const FString path = _wildcard + de.Name;\n'
             '  int res = MY_lstat(path, &st, followLink);\n#else\n'
             '  int res = fstatat(dirfd(_dir), de.Name, &st, followLink ? 0 : AT_SYMLINK_NOFOLLOW);\n#endif')],
        'TimeUtils.cpp': [
            ('#if defined(TIME_UTC) && !defined(__ANDROID__)',
             '#if defined(__APPLE__) && __ENVIRONMENT_MAC_OS_X_VERSION_MIN_REQUIRED__ < 101500\n'
             '// Use the existing gettimeofday branch on older macOS.\n'
             '#elif defined(TIME_UTC) && !defined(__ANDROID__)')],
    }
    for name, replacements in changes.items():
        path = windows / name
        source = path.read_text()
        for old, new in replacements:
            assert source.count(old) == 1, (name, old)
            source = source.replace(old, new)
        path.write_text(source)
    after = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in root.rglob('*') if p.is_file()}
    changed = sorted(p for p in original if original[p] != after[p])
    assert changed == sorted('CPP/Windows/' + name for name in changes)
    assert set(after)-set(original) == {'CPP/Windows/GoAwayBCALegacyTime.h'}
    report = dict(status='PASS', changed={p: dict(before=original[p], after=after[p]) for p in changed},
                  added={'CPP/Windows/GoAwayBCALegacyTime.h': after['CPP/Windows/GoAwayBCALegacyTime.h']},
                  original_file_count=len(original), patched_file_count=len(after))
    Path(evidence).write_text(json.dumps(report, indent=2) + '\n')
    return report
