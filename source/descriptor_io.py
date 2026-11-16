"""Descriptor-relative IO for both bundled Python builds.

The Lion-targeted Python omits *at APIs even on newer hosts. Prefer the native
operations there. Older Darwin hosts use a pinned directory and Darwin's
thread-local directory syscall; the process working directory never changes.
Cross-directory legacy moves are handled by verified copy/retire in the user
IO child, never by resolving descriptor paths back into mutable absolute paths.
"""
from contextlib import contextmanager
import ctypes
import os
import sys
import threading

_lib = None
_directory_state = threading.local()


class MissingNativeAPI(ValueError):
    pass


class CrossDirectoryRenameUnavailable(ValueError):
    pass


def native(name, result, arguments):
    global _lib
    if sys.platform != 'darwin':
        raise ValueError('Descriptor-relative driver storage is unavailable on this host')
    if _lib is None:
        _lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    try:
        fn = getattr(_lib, name)
    except AttributeError:
        raise MissingNativeAPI('Driver storage needs an unavailable filesystem operation: '+name)
    fn.restype = result
    fn.argtypes = arguments
    return fn


def checked(value, path):
    if value < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), path)
    return value


def leaf(name):
    if not isinstance(name, (str, bytes)):
        raise ValueError('A single directory entry is required')
    value = os.fsdecode(name)
    if not value or value in ('.', '..') or '/' in value or '\x00' in value:
        raise ValueError('A single directory entry is required')
    return name


@contextmanager
def thread_directory(fd):
    """Pin only this thread; do not change process cwd or resolve fd paths.

    Darwin's __pthread_fchdir exists on the tested Mavericks host. -1 restores
    inheritance from the process cwd. Nested calls restore the enclosing fd.
    This follows the Darwin syscall semantics also used by node-pty for older
    macOS; it is not ordinary fchdir/chdir.
    """
    change = native('__pthread_fchdir', ctypes.c_int, [ctypes.c_int])
    previous = getattr(_directory_state, 'fd', -1)
    checked(change(fd), 'driver directory')
    _directory_state.fd = fd
    try:
        yield
    finally:
        try:
            checked(change(previous), 'restore thread directory')
        finally:
            _directory_state.fd = previous


def open_at(fd, name, flags, mode=0o777):
    leaf(name)
    if os.open in os.supports_dir_fd:
        return os.open(name, flags, mode, dir_fd=fd)
    try:
        fn = native('openat', ctypes.c_int,
                    [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint])
    except MissingNativeAPI:
        with thread_directory(fd):
            return os.open(name, flags, mode)
    return checked(fn(fd, os.fsencode(name), flags, mode), name)


def mkdir_at(fd, name, mode=0o700):
    leaf(name)
    if os.mkdir in os.supports_dir_fd:
        return os.mkdir(name, mode, dir_fd=fd)
    try:
        fn = native('mkdirat', ctypes.c_int, [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint])
    except MissingNativeAPI:
        with thread_directory(fd):
            return os.mkdir(name, mode)
    checked(fn(fd, os.fsencode(name), mode), name)


def rename_at(source_fd, source, dest_fd, dest):
    leaf(source); leaf(dest)
    if os.rename in os.supports_dir_fd:
        return os.rename(source, dest, src_dir_fd=source_fd, dst_dir_fd=dest_fd)
    try:
        fn = native('renameat', ctypes.c_int,
                    [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p])
    except MissingNativeAPI:
        a, b = os.fstat(source_fd), os.fstat(dest_fd)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise CrossDirectoryRenameUnavailable('Legacy cross-directory move requires verified copy')
        with thread_directory(source_fd):
            return os.rename(source, dest)
    checked(fn(source_fd, os.fsencode(source), dest_fd, os.fsencode(dest)), source)


def unlink_at(fd, name, directory=False):
    leaf(name)
    operation = os.rmdir if directory else os.unlink
    if operation in os.supports_dir_fd:
        return operation(name, dir_fd=fd)
    try:
        fn = native('unlinkat', ctypes.c_int, [ctypes.c_int, ctypes.c_char_p, ctypes.c_int])
    except MissingNativeAPI:
        with thread_directory(fd):
            return operation(name)
    checked(fn(fd, os.fsencode(name), 0x80 if directory else 0), name)  # Darwin AT_REMOVEDIR


def stat_at(fd, name):
    leaf(name)
    if os.stat in os.supports_dir_fd:
        return os.stat(name, dir_fd=fd, follow_symlinks=False)
    # fstat avoids binding a version-dependent Darwin stat structure. Symlinks
    # are rejected rather than traversed; NONBLOCK avoids waiting on a FIFO.
    handle = open_at(fd, name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        return os.fstat(handle)
    finally:
        os.close(handle)


class _Dirent64(ctypes.Structure):
    # Verified against the local Apple SDK sys/dirent.h (__DARWIN_STRUCT_DIRENTRY).
    _pack_ = 4
    _fields_ = [('ino', ctypes.c_uint64), ('seekoff', ctypes.c_uint64),
                ('reclen', ctypes.c_uint16), ('namlen', ctypes.c_uint16),
                ('kind', ctypes.c_uint8), ('name', ctypes.c_char * 1024)]


def list_fd(fd):
    if os.listdir in os.supports_fd:
        return os.listdir(fd)
    assert _Dirent64.name.offset == 21
    try:
        begin = native('fdopendir', ctypes.c_void_p, [ctypes.c_int])
    except MissingNativeAPI:
        begin = None
    read = native('readdir$INODE64', ctypes.POINTER(_Dirent64), [ctypes.c_void_p])
    end = native('closedir', ctypes.c_int, [ctypes.c_void_p])
    rewind = native('rewinddir', None, [ctypes.c_void_p])
    if begin is not None:
        copied = os.dup(fd)
        directory = begin(copied)
    else:
        copied = None
        open_directory = native('opendir', ctypes.c_void_p, [ctypes.c_char_p])
        with thread_directory(fd):
            directory = open_directory(b'.')
    if not directory:
        error = ctypes.get_errno()
        if copied is not None:
            os.close(copied)
        raise OSError(error, os.strerror(error))
    try:
        rewind(directory)
        names = []
        while True:
            ctypes.set_errno(0)
            entry = read(directory)
            if not entry:
                error = ctypes.get_errno()
                if error:
                    raise OSError(error, os.strerror(error))
                return names
            row = entry.contents
            if not 0 < row.namlen < 1024 or row.reclen < 22 + row.namlen:
                raise ValueError('Invalid native directory entry')
            name = os.fsdecode(ctypes.string_at(ctypes.addressof(row) + 21, row.namlen))
            if name not in ('.', '..'):
                names.append(name)
            if len(names) > 200000:
                raise ValueError('Driver directory exceeds entry limit')
    finally:
        end(directory)
