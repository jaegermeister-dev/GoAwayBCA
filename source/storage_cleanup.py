"""Explicit cleanup of app preparations/cache; preserve installation and recovery state."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from deployment_lock import DeploymentLock
from install_windows7 import Installer,require,write_receipt,Refusal
from user_driver_store import Store,open_dir,open_file
from descriptor_io import list_fd,stat_at
from saved_file_io import snapshot,remove
from operation_progress import Meter
import tempfile

SESSION=re.compile(r'(manage|session)-[A-Za-z0-9_]+\Z')
PREPARATION=re.compile(r'(iso-preparation|prepared-source|keyboard-preparation|helper-test|'
                       r'usb-preparation|restore-preparation|verified-source\.ntfs|'
                       r'driver-source-[A-Za-z0-9_]+|apple-drivers-[A-Za-z0-9_]+|'
                       r'community-publisher-[A-Za-z0-9_]+|community-setup-refresh-[A-Za-z0-9_]+|'
                       r'combined-drivers-[A-Za-z0-9_]+|ntfs-overlay\.wim|backtoseven-overlay\.wim)\Z')

def protected(fd,uid):
    s=os.fstat(fd)
    require(stat.S_ISDIR(s.st_mode) and s.st_uid==uid and not s.st_mode&0o022,'Preparation directory is not protected')

def session_fd(base,name,uid):
    require(SESSION.fullmatch(name),'Not an installation preparation session')
    root=open_dir(str(base))
    try:
        protected(root,uid)
        fd=open_dir(name,base=root)
        try:
            protected(fd,uid)
            log=open_file(fd,'progress.log');os.close(log)
            return fd
        except BaseException:os.close(fd);raise
    finally:os.close(root)

def preparations(base,exclude=None,uid=0):
    rows=[];warnings=[]
    root=open_dir(str(base))
    try:
        protected(root,uid);names=sorted(list_fd(root))
    finally:os.close(root)
    for session in names:
        if not SESSION.fullmatch(session) or session==exclude:continue
        try:fd=session_fd(base,session,uid)
        except (OSError,ValueError,Refusal) as error:
            warnings.append(session+': '+str(error));continue
        try:
            for name in sorted(list_fd(fd)):
                if not PREPARATION.fullmatch(name):continue
                try:state=snapshot(fd,name)
                except (OSError,ValueError) as error:
                    warnings.append(session+'/'+name+': '+str(error));continue
                if not state['bytes']:continue
                category=('Backup restore preparation' if name in ('restore-preparation','verified-source.ntfs') else
                          'USB preparation' if name=='usb-preparation' else
                          'ISO preparation' if name in ('iso-preparation','prepared-source') else
                          'Scratch test' if name=='helper-test' else 'Temporary driver/installation files')
                rows.append(dict(kind='preparation',session=session,name=name,state=state,
                    title=category+' — '+session,
                    detail=name+' — reusable saved drivers, logs and EFI backups are kept'))
        finally:os.close(fd)
    return rows,warnings

def delete_preparation(base,row,uid=0):
    require(row.get('kind')=='preparation' and PREPARATION.fullmatch(row['name']), 'Unexpected saved-file selection')
    fd=session_fd(base,row['session'],uid)
    try:remove(fd,row['name'],row['state'])
    finally:os.close(fd)

class StorageCleanup(Installer):
    def execute(self):
        require(os.geteuid()==0 and self.interaction is not None,'Use the authorized GUI')
        self.deployment_lock=DeploymentLock().acquire()
        base=Path(self.args.work_dir);base.mkdir(parents=True,exist_ok=True)
        self.session=Path(tempfile.mkdtemp(prefix='cleanup-',dir=str(base)));os.chmod(self.session,0o700)
        self.note('Reading saved preparation files and driver downloads. Nothing is selected or deleted automatically.')
        rows,warnings=preparations(base)
        store=Store(self.interaction.user_uid)
        cached=store._call('cleanup-list',{})
        warnings+=cached['warnings']
        for row in cached['items']:
            rows.append(dict(row,kind='cache',title='Saved drivers — '+row['label'],
                detail=row['id']+' — deleting this requires downloading the drivers again'))
        write_receipt(self.session/'saved-files-inventory.json',dict(items=rows,warnings=warnings))
        for text in warnings:self.note('Left out of cleanup: '+text)
        if not rows:
            self.interaction.request('notice','No removable saved files were found. Nothing changed.',button_title='Done');return
        options=[]
        for i,row in enumerate(rows):
            options.append(dict(id=i,title=row['title']+' (%.2f GB)'%(row['state']['bytes']/1e9),detail=row['detail']))
        selected=self.interaction.request('saved_files_selection',
            'Select the saved files to delete. Nothing is selected by default. Original ISOs, app builds, installation records, logs and EFI backups are kept.',items=options)
        require(type(selected) is list and all(type(i) is int and 0<=i<len(rows) for i in selected)
                and len(set(selected))==len(selected),'Invalid cleanup selection')
        if not selected:self.note('No saved files selected. Nothing changed.');return
        chosen=[rows[i] for i in selected];total=sum(r['state']['bytes'] for r in chosen)
        text=('Delete %d selected saved items (%.2f GB)?\n\n'%(len(chosen),total/1e9)+
              '\n'.join(r['title'] for r in chosen)+
              '\n\nThis permanently deletes only the selected saved files. Deleted driver caches need to be downloaded again. No Windows installation or boot configuration is removed.')
        require(self.interaction.request('saved_files_confirm',text) is True,'Cancelled saved-file cleanup')
        receipt=dict(selected=chosen,completed=[],status='in-progress')
        path=self.session/'cleanup-receipt.json';write_receipt(path,receipt)
        meter=Meter(self.note,'Deleting selected saved files',len(chosen),'items deleted')
        for i,row in enumerate(chosen,1):
            if row['kind']=='preparation':delete_preparation(base,row)
            else:store._call('cleanup-delete',dict(id=row['id'],state=row['state']))
            receipt['completed'].append(row);write_receipt(path,receipt);meter.update(i,row['title'])
        receipt['status']='complete';write_receipt(path,receipt)
        self.interaction.request('notice','Deleted %d selected saved items. Installation records, logs and EFI backups were kept.'%len(chosen),button_title='Done')
