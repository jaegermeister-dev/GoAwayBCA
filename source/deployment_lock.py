"""One privileged deployment per Mac, across app copies and work directories.

Use a kernel-held advisory lock, not a PID/age guess. Never delete the lockfile:
unlinking it could let a new process lock a different inode during a live run.
"""
import errno
import os
from pathlib import Path
import stat

LOCK_PATH = Path('/private/var/run/windows-deployer-install.lock')


class DeploymentBusy(RuntimeError):
    pass


class DeploymentLock:
    def __init__(self, path=LOCK_PATH, expected_uid=0):
        self.path = Path(path)
        self.expected_uid = expected_uid
        self.descriptor = None

    def acquire(self):
        import fcntl
        if self.descriptor is not None:
            raise RuntimeError('Deployment lock already held by this object')
        # NOFOLLOW must exist: never weaken the checks on an unexpected host.
        descriptor = os.open(str(self.path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            node = os.fstat(descriptor)
            if not (stat.S_ISREG(node.st_mode) and node.st_uid == self.expected_uid
                    and stat.S_IMODE(node.st_mode) == 0o600 and node.st_nlink == 1):
                raise RuntimeError('Unsafe deployment lockfile; nothing will be installed')
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    raise DeploymentBusy('Another deployment or preflight is still running. '
                                         'Wait for it to finish; do not interrupt it.') from error
                raise
            current = self.path.lstat()
            if (current.st_dev, current.st_ino) != (node.st_dev, node.st_ino):
                raise RuntimeError('Deployment lockfile changed; nothing will be installed')
            os.set_inheritable(descriptor, False)
            self.descriptor = descriptor
            return self
        finally:
            if self.descriptor is None:
                os.close(descriptor)

    def release(self):
        if self.descriptor is not None:
            descriptor, self.descriptor = self.descriptor, None
            os.close(descriptor)
