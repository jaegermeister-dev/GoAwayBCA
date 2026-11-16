"""Hash-checked, journalled publication of an explicit Windows EFI file set.

Caller must hold the deployment lock, obtain the owner's installation/format
confirmations, and supply a mount/GPT identity recheck. This module does not
mount, format, set NVRAM, alter OpenCore, scan for files to delete, or reboot.
Unrelated paths are fingerprinted and preserved. No recursive cleanup.
"""
import hashlib
import os
from pathlib import Path
import stat
import tempfile

from boot_catalog import efi_path
from install_windows7 import Refusal, require, tree_manifest, protected_efi_files, write_receipt


def digest(data): return hashlib.sha256(data).hexdigest()


def safe_relative(path):
    path=efi_path(path)
    require(not path.casefold().startswith(('efi/oc/','efi/apple/')),
            'OpenCore/Apple EFI files are protected')
    return path


def node(root, relative):
    root=Path(root)
    require(root.is_dir() and not root.is_symlink() and root.resolve()==root,
            'Publication root is not a canonical directory')
    parts=safe_relative(relative).split('/')
    current=root
    for index,part in enumerate(parts):
        current=current/part
        require(not current.is_symlink(), 'Symlink in EFI publication path')
        if current.exists():
            observed=current.stat()
            require(observed.st_dev==root.stat().st_dev, 'EFI publication crossed a filesystem boundary')
            require(stat.S_ISREG(observed.st_mode) if index==len(parts)-1 else stat.S_ISDIR(observed.st_mode),
                    'Wrong EFI path type')
            if index==len(parts)-1:
                require(observed.st_nlink==1, 'Hard-linked EFI target is not exclusive')
    return current


def current_hash(path):
    if not path.exists():return None
    require(path.stat().st_size<=16*1024*1024, 'EFI publication target is unexpectedly large')
    return digest(path.read_bytes())


class EFITransaction:
    def __init__(self, root, work, check_identity):
        require(callable(check_identity), 'A fresh EFI identity check is required')
        # Do not resolve away a supplied symlink before checking it. The
        # caller must pass the canonical mount and evidence paths explicitly.
        self.root=Path(root)
        self.work=Path(work)
        require(self.root.is_absolute() and self.root.is_dir()
                and not self.root.is_symlink() and self.root.resolve()==self.root,
                'EFI root must be an existing canonical directory')
        require(self.work.is_absolute() and self.work.resolve()==self.work
                and not self.work.is_symlink(), 'Evidence path is not canonical')
        require(not self.work.exists(), 'Use a new EFI transaction evidence directory')
        require(self.root not in self.work.parents and self.root!=self.work,
                'Keep recovery evidence off the EFI being changed')
        self.check_identity=check_identity
        self.journal=None

    def save(self): write_receipt(self.work/'journal.json',self.journal)

    def prepare(self, changes):
        """Ordered list: path, old digest/None, new bytes (None = exact deletion).

        Deletion requires an existing nonempty file and a verified backup.
        Callers must establish exclusive ownership before submitting removal.
        """
        self.check_identity()
        require(type(changes) is list and 0 < len(changes) <= 1024, 'Invalid publication file list')
        entries=[]
        seen=set()
        for change in changes:
            relative=safe_relative(change['path'])
            canonical=relative.casefold()
            require(canonical not in seen, 'Duplicate/case-colliding EFI publication path')
            seen.add(canonical)
            data=change['data']
            deleting=data is None
            require(deleting or (type(data) is bytes and 0 < len(data) <= 16*1024*1024), 'Invalid staged EFI file')
            path=node(self.root,relative)
            before=current_hash(path)
            require(before==change['expected'], 'EFI file changed or already exists: '+relative)
            require(not deleting or before is not None,'Cannot delete an absent/unattributed EFI file')
            entries.append(dict(path=relative,before=before,after=None if deleting else digest(data),
                                bytes=0 if deleting else len(data),delete=deleting,
                                written=False,restored=False))
        self.work.mkdir(mode=0o700)
        (self.work/'before').mkdir()
        (self.work/'after').mkdir()
        for index,(change,entry) in enumerate(zip(changes,entries)):
            if not entry['delete']:
                (self.work/'after'/str(index)).write_bytes(change['data'])
                require(current_hash(self.work/'after'/str(index))==entry['after'],'Stage copy failed')
            if entry['before'] is not None:
                source=node(self.root,entry['path'])
                backup=self.work/'before'/str(index)
                backup.write_bytes(source.read_bytes())
                require(current_hash(backup)==entry['before'],'EFI backup verification failed')
        self.check_identity()
        self.journal=dict(schema=1,status='prepared',files=entries,
            before_manifest=protected_efi_files(tree_manifest(self.root)),
            set_default_boot=False,repartition=False)
        self.save()
        return self.journal

    def verify_unrelated(self):
        excluded={entry['path'].casefold() for entry in self.journal['files']}
        def remaining(manifest):return {k:v for k,v in manifest.items() if k.casefold() not in excluded}
        require(remaining(protected_efi_files(tree_manifest(self.root)))==remaining(self.journal['before_manifest']),
                'Unrelated EFI files changed during publication')

    def replace(self, entry, data, expected):
        self.check_identity()
        destination=node(self.root,entry['path'])
        require(current_hash(destination)==expected,'EFI file changed before atomic publication: '+entry['path'])
        destination.parent.mkdir(parents=True,exist_ok=True)
        node(self.root,entry['path'])
        descriptor,pending=tempfile.mkstemp(prefix='w7dep-pending-',dir=str(destination.parent))
        pending=Path(pending)
        try:
            with os.fdopen(descriptor,'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            require(current_hash(pending)==digest(data),'EFI pending-file readback failed')
            self.check_identity()
            require(current_hash(node(self.root,entry['path']))==expected,'EFI target changed during staged write')
            os.replace(str(pending),str(destination))
            entry['written']=True
            require(current_hash(destination)==digest(data),'EFI publication readback failed')
        finally:
            # Only the exact temporary file created by this method, never a tree.
            if pending.exists() and not pending.is_symlink():pending.unlink()

    def commit(self, after_each=None):
        require(self.journal is not None and self.journal['status']=='prepared','EFI transaction not prepared')
        self.check_identity()
        self.verify_unrelated()
        try:
            for index,entry in enumerate(self.journal['files']):
                self.journal['status']='publishing'
                self.journal['pending_index']=index
                self.save()
                if entry.get('delete'):
                    self.check_identity()
                    destination=node(self.root,entry['path'])
                    require(current_hash(destination)==entry['before'],'EFI cleanup target changed')
                    require(current_hash(self.work/'before'/str(index))==entry['before'],'EFI cleanup backup changed')
                    destination.unlink()
                    entry['written']=True
                    require(not destination.exists(),'EFI cleanup readback failed')
                else:
                    data=(self.work/'after'/str(index)).read_bytes()
                    require(digest(data)==entry['after'],'Staged EFI file changed')
                    self.replace(entry,data,entry['before'])
                entry['written']=True
                self.save()
                if after_each:after_each(index)
            self.verify_unrelated()
            self.journal['status']='committed'
            self.save()
        except Exception:
            self.rollback()
            raise
        return self.journal

    def rollback(self):
        """Restore only known new hashes; retain evidence and foreign changes."""
        errors=[]
        for index in reversed(range(len(self.journal['files']))):
            entry=self.journal['files'][index]
            if not entry['written']:
                continue
            try:
                self.check_identity()
                destination=node(self.root,entry['path'])
                actual=current_hash(destination)
                if actual==entry['before']:
                    entry['restored']=True
                    continue
                require(actual==entry['after'],'Refusing to overwrite a later EFI change: '+entry['path'])
                if entry['before'] is None:
                    # This exact hash-checked file did not exist before this run.
                    destination.unlink()
                else:
                    data=(self.work/'before'/str(index)).read_bytes()
                    require(digest(data)==entry['before'],'Rollback backup changed')
                    self.replace(entry,data,entry['after'])
                entry['restored']=True
            except Exception as error:errors.append(str(error))
        self.journal['status']='rollback-needs-attention' if errors else 'rolled-back'
        self.journal['rollback_errors']=errors
        self.save()
        return errors
