"""Verify old-API timestamp semantics on disposable files, not live volumes."""
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def exercise(output, header, probe_source):
    output = Path(output)
    output.mkdir()
    library = output / 'timestamp-test-only.dylib'
    command = ['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
               '-mmacosx-version-min=10.7', '-arch', 'x86_64', '-dynamiclib',
               '-I', str(header.parent), str(probe_source), '-o', str(library)]
    build = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (output / 'compile.txt').write_bytes(repr(command).encode() + b'\n' + build.stdout + build.stderr)
    assert build.returncode == 0, build.stderr
    lib = ctypes.CDLL(str(library), use_errno=True)
    lib.mw_time_omit.restype = lib.mw_time_now.restype = ctypes.c_long
    omit, now = lib.mw_time_omit(), lib.mw_time_now()
    setter = lib.mw_time_probe
    setter.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int] + [ctypes.c_long] * 4 + [ctypes.c_int]
    setter.restype = ctypes.c_int
    path = output / 'timestamp target.txt'
    path.write_bytes(b'untouched fixture content\n')
    link = output / 'link to target'
    link.symlink_to(path.name)
    rows = []

    def snapshot(p=path):
        s = p.lstat()
        return (s.st_atime_ns, s.st_mtime_ns, s.st_ctime_ns)

    def call(name, a=(1000000000, 123456789), m=(1000000100, 987654321),
             mode=0, target=path, fd=-1, null=False, expected_errno=0):
        before = snapshot()
        ctypes.set_errno(0)
        result = setter(os.fsencode(str(target)), fd, mode, *a, *m, int(null))
        error = ctypes.get_errno()
        assert result == (-1 if expected_errno else 0), (name, result, error)
        if expected_errno:
            assert error == expected_errno, (name, error)
            assert snapshot() == before, name
        rows.append(dict(case=name, return_code=result, error=error if result else None,
                         actual_timestamp_ns=snapshot()))

    def micros(ns):
        return ns // 1000 * 1000

    call('path-explicit')
    assert snapshot()[:2] == (1000000000123456000, 1000000100987654000)
    fd = os.open(str(path), os.O_RDWR)
    try:
        call('descriptor-explicit', mode=2, fd=fd, a=(1000000200, 321987654), m=(1000000300, 654321987))
        assert snapshot()[:2] == (1000000200321987000, 1000000300654321000)
        before = snapshot()
        call('descriptor-both-omit', mode=2, fd=fd, a=(0, omit), m=(0, omit))
        assert snapshot() == before
        before_time = time.time_ns()
        call('descriptor-null-now', mode=2, fd=fd, null=True)
        after_time = time.time_ns()
        assert all(micros(before_time) <= x <= after_time for x in snapshot()[:2])
    finally:
        os.close(fd)
    before = snapshot()
    call('path-both-omit', a=(0, omit), m=(0, omit))
    assert snapshot() == before
    call('preserve-atime', a=(0, omit))
    assert snapshot()[0] == micros(before[0])
    before = snapshot()
    call('preserve-mtime', m=(0, omit))
    assert snapshot()[1] == micros(before[1])
    for name, null in (('path-now-sentinel', False), ('path-null-now', True)):
        before_time = time.time_ns()
        call(name, a=(0, now), m=(0, now), null=null)
        after_time = time.time_ns()
        assert all(micros(before_time) <= x <= after_time for x in snapshot()[:2])
    before = snapshot()
    call('symlink-no-follow', target=link, mode=1)
    assert snapshot() == before
    assert snapshot(link)[:2] == (1000000000123456000, 1000000100987654000)
    link_before = snapshot(link)
    call('symlink-follow', target=link, a=(1000000400, 0), m=(1000000500, 0))
    assert snapshot()[1] == 1000000500000000000
    # Following a link may update its access time; it must not change its mtime.
    assert snapshot(link)[1] == link_before[1]
    # Darwin uses negative values for its valid NOW/OMIT sentinels.
    call('invalid-negative-nsec', a=(0, min(omit, now, -1) - 1), expected_errno=errno.EINVAL)
    call('invalid-large-nsec', m=(0, 1000000000), expected_errno=errno.EINVAL)
    call('invalid-flags', mode=3, expected_errno=errno.EINVAL)
    call('missing-file', target=output / 'not-present', expected_errno=errno.ENOENT)
    call('invalid-descriptor', mode=2, expected_errno=errno.EBADF)
    assert path.read_bytes() == b'untouched fixture content\n'
    report = dict(status='PASS', cases=rows, case_count=len(rows),
                  header_sha256=hashlib.sha256(header.read_bytes()).hexdigest(),
                  fixture_library_only=True, historical_os_tested=False,
                  timestamp_precision='microseconds', content_unchanged=True)
    (output / 'RESULT.json').write_text(json.dumps(report, indent=2) + '\n')
    return report
