"""Bounded read-only NTFS reads, without changing a user's mount state.

Use the existing filesystem when mounted: ntfs-3g deliberately refuses raw
access to a volume already opened by macOS. Descriptor-relative, no-follow
reads keep a crafted marker/path from escaping the selected partition.
"""
import os
import json
import select
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

if __name__ == '__main__':
    # Isolated bundled-Python child: import only the adjacent audited modules.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_windows7 import (Refusal, require, info, filesystem, PART_RE,
                              partition_identity)


def components(path):
    require(type(path) is str and path.startswith('/') and '\\' not in path
            and ':' not in path and all(ord(c) >= 32 for c in path),
            'Unsafe NTFS read path')
    parts = path[1:].split('/')
    require(parts and all(p and p not in ('.', '..') for p in parts),
            'Unsafe NTFS read path')
    return parts


def mounted_read(mount, path, device_id, limit, prefix=False):
    """macOS/POSIX only; open each component relative to a checked directory."""
    if os.open not in os.supports_dir_fd:
        # Python built for Lion cannot expose openat, even on newer hosts.
        # A dedicated child can use fchdir without changing the worker's cwd.
        # It still pins every directory descriptor and refuses symlinks/devices.
        require(os.name == 'posix', 'Mounted NTFS reader requires macOS/POSIX')
        components(path)
        require(type(limit) is int and 0 < limit <= 256*1024*1024, 'Invalid NTFS read limit')
        result = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve()),
            '--prefix-child' if prefix else '--mounted-child',mount,path,str(device_id),str(limit)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
        if result.returncode == 72:
            raise OSError(result.stderr[:3000].decode('utf-8','replace'))
        require(result.returncode == 0,
                'Read-only mounted helper failed: '+result.stderr[:3000].decode('utf-8','replace'))
        require(len(result.stdout) <= limit, 'NTFS file exceeds the read limit')
        return result.stdout
    return _mounted_read(mount,path,device_id,limit,prefix=prefix)


def _mounted_read(mount, path, device_id, limit, isolated_cwd=False, prefix=False):
    """isolated_cwd is used ONLY in the disposable helper process below."""
    require(type(limit) is int and 0 < limit <= 256*1024*1024, 'Invalid NTFS read limit')
    parts = components(path)
    require(Path(mount).is_absolute() and str(Path(mount).resolve()) == mount,
            'Mounted NTFS path is not canonical')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(mount, flags | os.O_DIRECTORY)
    try:
        root = os.fstat(descriptor)
        require(stat.S_ISDIR(root.st_mode) and root.st_dev == device_id,
                'NTFS mount does not belong to the selected block device')
        for index, part in enumerate(parts):
            last = index == len(parts)-1
            mode = flags | (0 if last else os.O_DIRECTORY)
            if isolated_cwd:
                os.fchdir(descriptor)
                child = os.open(part, mode)
            else:
                child = os.open(part, mode, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            observed = os.fstat(descriptor)
            require(observed.st_dev == device_id, 'NTFS read crossed a filesystem boundary')
            require(stat.S_ISREG(observed.st_mode) if last else stat.S_ISDIR(observed.st_mode),
                    'NTFS path is not a regular file/directory')
        require(prefix or observed.st_size <= limit, 'NTFS file exceeds the read limit')
        chunks, size = [], 0
        while True:
            remaining = (min(observed.st_size,limit) if prefix else limit+1)-size
            if not remaining:break
            data = os.read(descriptor, min(1024*1024, remaining))
            if not data:
                break
            chunks.append(data)
            size += len(data)
            require(size <= limit, 'NTFS file exceeds the read limit')
        after = os.fstat(descriptor)
        require((observed.st_dev, observed.st_ino, observed.st_size, observed.st_mtime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) and
                size == (min(after.st_size,limit) if prefix else after.st_size),
                'NTFS file changed during inspection')
        return b''.join(chunks)
    finally:
        os.close(descriptor)


def raw_read(tool, raw, path, limit):
    # Only the child we start can be terminated, and only to bound its output.
    # No --force: retain ntfs-3g's mounted/hibernated-volume safety checks.
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen([str(tool), raw, path], stdout=subprocess.PIPE,
                              stderr=errors) as child:
            data = child.stdout.read(limit+1)
            if len(data) > limit:
                child.kill()
                child.wait()
                raise Refusal('NTFS file exceeds the read limit')
            code = child.wait()
            errors.seek(0)
            error = errors.read(3000).decode('utf-8', 'replace')
            require(code == 0, 'Read-only NTFS helper failed: ' + error)
            return data


def raw_ntfs_signature(partition):
    """Read one sector of this unmounted partition, never its whole disk.

    Disk Arbitration can retain the old exFAT label after mkntfs. This is only
    a discovery hint: the NTFS reader must still open and verify Windows files.
    Keep the original diskutil identity unchanged for all later write guards.
    """
    device = partition.get('DeviceIdentifier')
    require(PART_RE.fullmatch(str(device)) and partition.get('MountPoint') == '',
            'Filesystem probing requires an identified, unmounted partition')
    size = partition.get('DeviceBlockSize')
    require(type(size) is int and size in (512, 4096), 'Unknown partition sector size')
    raw = '/dev/' + device
    node = os.stat(raw)
    require(stat.S_ISBLK(node.st_mode) and node.st_rdev > 0, 'Expected a partition block device')
    fd = os.open(raw, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        require(stat.S_ISBLK(opened.st_mode) and opened.st_rdev == node.st_rdev,
                'Partition device changed during filesystem probing')
        data = os.read(fd, size)
        require(len(data) == size, 'Could not read the partition filesystem header')
    finally:
        os.close(fd)
    return data[3:11] == b'NTFS    ' and data[510:512] == b'\x55\xaa'


def raw_names(tool, raw):
    """Bounded NTFS root listing inside an already checked Probe batch."""
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen([str(tool), '-a', '-p', '/', raw], stdout=subprocess.PIPE,
                stderr=errors, env=dict(os.environ, LC_ALL='C')) as child:
            data = child.stdout.read(1024**2 + 1)
            if len(data) > 1024**2:
                child.kill(); child.wait()
                raise Refusal('NTFS root listing exceeds limit')
            code = child.wait(); errors.seek(0)
            require(code == 0, 'Read-only NTFS directory inspection failed: ' +
                    errors.read(3000).decode('utf-8', 'replace'))
            return data.decode('utf-8').splitlines()


def _mounted_names(mount,device_id,isolated_cwd=False):
    require(Path(mount).is_absolute() and str(Path(mount).resolve())==mount,'Noncanonical NTFS mount')
    descriptor=os.open(mount,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        node=os.fstat(descriptor)
        require(stat.S_ISDIR(node.st_mode) and node.st_dev==device_id,'NTFS root identity changed')
        if isolated_cwd:
            os.fchdir(descriptor);names=os.listdir('.')
        else:names=os.listdir(descriptor)
        require(len(names)<=65536 and sum(len(n) for n in names)<=1024*1024,'Oversized NTFS root listing')
        require(all(type(n) is str and n and '/' not in n and n not in ('.','..') for n in names),'Invalid root names')
        return names
    finally:os.close(descriptor)


def root_names(partition,tool):
    """Prove Windows is absent via a successful root listing, not a failed read."""
    device=partition.get('DeviceIdentifier')
    require(PART_RE.fullmatch(str(device)),'Invalid NTFS partition device')
    before=info(device)
    require(filesystem(before)=='ntfs' and partition_identity(before)==partition_identity(partition),'NTFS identity changed')
    mount=before.get('MountPoint');require(type(mount) is str and mount!='/','Unknown NTFS mount state')
    if mount:
        node=os.stat('/dev/'+device)
        require(stat.S_ISBLK(node.st_mode) and node.st_rdev>0,'Expected NTFS partition block device')
        if os.listdir in os.supports_fd:names=_mounted_names(mount,node.st_rdev)
        else:
            result=subprocess.run([sys.executable,'-I','-B',str(Path(__file__).resolve()),
                '--root-names-child',mount,str(node.st_rdev)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
            require(result.returncode==0 and len(result.stdout)<=4*1024**2,'NTFS root listing helper failed')
            names=json.loads(result.stdout.decode('utf-8'))
    else:
        with tempfile.TemporaryFile() as errors:
            with subprocess.Popen([str(tool),'-a','-p','/','/dev/'+device],stdout=subprocess.PIPE,stderr=errors,
                                  env=dict(os.environ,LC_ALL='C')) as child:
                data=child.stdout.read(1024**2+1)
                if len(data)>1024**2:
                    child.kill();child.wait();raise Refusal('NTFS root listing exceeds limit')
                code=child.wait();errors.seek(0)
                require(code==0,'Read-only NTFS directory inspection failed: '+errors.read(3000).decode('utf-8','replace'))
                names=data.decode('utf-8').splitlines()
    require(type(names) is list and all(type(n) is str for n in names),'Invalid NTFS listing')
    after=info(device)
    require(partition_identity(before)==partition_identity(after) and before.get('MountPoint')==after.get('MountPoint')
        and before.get('ReadOnlyVolume')==after.get('ReadOnlyVolume'),'NTFS identity or mount state changed during listing')
    return names


def read_file(partition, path, tool, limit):
    require(type(limit) is int and 0 < limit <= 256*1024*1024, 'Invalid NTFS read limit')
    components(path)
    device = partition.get('DeviceIdentifier')
    require(PART_RE.fullmatch(str(device)), 'Invalid NTFS partition device')
    before = info(device)
    require(filesystem(before) == 'ntfs' and partition_identity(before) == partition_identity(partition),
            'NTFS partition identity changed before inspection')
    require(type(before.get('MountPoint')) is str and before['MountPoint'] != '/',
            'Unknown or invalid NTFS mount state')
    raw = '/dev/' + device
    if before['MountPoint']:
        require(type(before.get('ReadOnlyVolume')) is bool, 'Unknown mounted-volume write state')
        node = os.stat(raw)
        require(stat.S_ISBLK(node.st_mode) and node.st_rdev > 0, 'Expected a block-device partition')
        data = mounted_read(before['MountPoint'], path, node.st_rdev, limit)
    else:
        data = raw_read(tool, raw, path, limit)
    after = info(device)
    require(partition_identity(before) == partition_identity(after) and
            before.get('MountPoint') == after.get('MountPoint') and
            before.get('ReadOnlyVolume') == after.get('ReadOnlyVolume'),
            'NTFS identity or mount state changed during inspection')
    return data


def stop_reader(child):
    """Close the pipe and reap only this read-only helper, with bounded waits."""
    child.stdout.close()
    if child.poll() is None:
        try:child.terminate()
        except ProcessLookupError:pass
    try:child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:child.kill()
        except ProcessLookupError:pass
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            raise Refusal('The read-only NTFS helper could not be stopped. Close this operation and retry.')


def raw_prefix_read(tool,raw,path,limit,timeout=60):
    """ntfscat has no range option; never wait indefinitely on its full output."""
    components(path)
    require(type(limit) is int and 0<limit<=16*1024**2,'Invalid discovery read limit')
    deadline=time.monotonic()+timeout
    with tempfile.TemporaryFile() as errors:
        child=subprocess.Popen([str(tool),raw,path],stdout=subprocess.PIPE,stderr=errors,bufsize=0)
        try:
            data=bytearray()
            while len(data)<limit:
                remaining=deadline-time.monotonic()
                require(remaining>0,'Timed out reading the Windows registry header from '+raw)
                ready,_,_=select.select([child.stdout],[],[],remaining)
                require(ready,'Timed out reading the Windows registry header from '+raw)
                part=os.read(child.stdout.fileno(),limit-len(data))
                if not part:break
                data.extend(part)
            if len(data)<limit:
                try:code=child.wait(timeout=max(0.01,deadline-time.monotonic()))
                except subprocess.TimeoutExpired:
                    raise Refusal('Timed out waiting for the read-only NTFS helper on '+raw)
                errors.seek(0)
                require(code==0,'NTFS header read failed: '+errors.read(3000).decode('utf-8','replace'))
            return bytes(data)
        finally:stop_reader(child)


class Probe:
    """One short read-only discovery batch, with identity/mount checks at both ends.

    These are display hints, never destructive-operation authority. Do not keep
    this context open across GUI questions or use it to cache a write guard.
    """
    def __init__(self,partition,tool,verify_filesystem=False):
        self.partition=partition;self.tool=tool
        self.verify_filesystem=verify_filesystem

    def __enter__(self):
        device=self.partition.get('DeviceIdentifier')
        require(PART_RE.fullmatch(str(device)),'Invalid NTFS partition device')
        self.device=device;self.before=info(device)
        reported=filesystem(self.before)
        require(reported in (('ntfs','exfat') if self.verify_filesystem else ('ntfs',)) and
            partition_identity(self.before)==partition_identity(self.partition),'NTFS identity changed')
        self.mount=self.before.get('MountPoint')
        require(type(self.mount) is str and self.mount!='/','Unknown NTFS mount state')
        self.is_ntfs=reported=='ntfs'
        self.stale_filesystem=False
        if not self.is_ntfs and not self.mount:
            self.is_ntfs=raw_ntfs_signature(self.before)
            self.stale_filesystem=self.is_ntfs
        if self.mount:
            node=os.stat('/dev/'+device)
            require(stat.S_ISBLK(node.st_mode) and node.st_rdev>0,'Expected NTFS partition block device')
            self.device_id=node.st_rdev
        return self

    def __exit__(self,*_):
        after=info(self.device)
        require(partition_identity(self.before)==partition_identity(after) and
            self.before.get('MountPoint')==after.get('MountPoint') and
            self.before.get('ReadOnlyVolume')==after.get('ReadOnlyVolume'),
            'NTFS identity or mount state changed during discovery')

    def read(self,path,limit,prefix=False):
        require(self.is_ntfs,'The selected partition was not identified as NTFS')
        components(path)
        require(type(limit) is int and 0<limit<=16*1024**2,'Invalid discovery read limit')
        if self.mount:
            return mounted_read(self.mount,path,self.device_id,limit,prefix=prefix)
        if not prefix:return raw_read(self.tool,'/dev/'+self.device,path,limit)
        return raw_prefix_read(self.tool,'/dev/'+self.device,path,limit)

    def names(self):
        require(self.is_ntfs,'The selected partition was not identified as NTFS')
        if not self.mount:
            if self.stale_filesystem:
                return raw_names(Path(self.tool).with_name('ntfsls'),'/dev/'+self.device)
            return root_names(self.before,Path(self.tool).with_name('ntfsls'))
        if os.listdir in os.supports_fd:return _mounted_names(self.mount,self.device_id)
        result=subprocess.run([sys.executable,'-I','-B',str(Path(__file__).resolve()),
            '--root-names-child',self.mount,str(self.device_id)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
        require(result.returncode==0 and len(result.stdout)<=4*1024**2,'NTFS root listing failed')
        return json.loads(result.stdout.decode('utf-8'))


if __name__ == '__main__':
    try:
        if os.name=='posix' and len(sys.argv)==4 and sys.argv[1]=='--root-names-child':
            sys.stdout.write(json.dumps(_mounted_names(sys.argv[2],int(sys.argv[3]),isolated_cwd=True)))
            sys.exit(0)
        require(os.name == 'posix' and len(sys.argv) == 6 and sys.argv[1] in ('--mounted-child','--prefix-child'),
                'This module only exposes a bounded read-only helper')
        data = _mounted_read(sys.argv[2],sys.argv[3],int(sys.argv[4]),int(sys.argv[5]),isolated_cwd=True,
                            prefix=sys.argv[1]=='--prefix-child')
        sys.stdout.buffer.write(data)
    except OSError as error:
        sys.stderr.write(str(error)[:3000]);sys.exit(72)
    except Exception as error:
        sys.stderr.write(str(error)[:3000]);sys.exit(73)
