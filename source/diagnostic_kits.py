"""Explicit selection and confined removal of recognised EFI diagnostic kits."""
import hashlib
import json
import re
import struct
import uuid
from pathlib import Path
from boot_diagnostic_binding import MAGIC, SIZE
from manage_installations import InstallationManager
from install_windows7 import require, info, tree_manifest, write_receipt, run, Refusal
from efi_access import candidates as esp_candidates, same
from efi_transaction import EFITransaction

PREFIX='EFI/WinDeploy/Diagnostics'


def inspect(root, esp_guid):
    """List unknown folders too, but grant removal only with a bound kit EFI."""
    base=Path(root)/PREFIX
    if not base.exists():return []
    require(not any(p.is_symlink() for p in (base,)+tuple(base.parents)), 'Diagnostic directory is a symlink')
    rows=[]
    for folder in sorted(base.iterdir()):
        if not folder.is_dir() or folder.is_symlink():continue
        row=dict(folder=folder.name,path=PREFIX+'/'+folder.name,version='Unknown version',target='Unknown Windows target',verified=False)
        try:
            require(bool(re.fullmatch(r'Test-[0-9]{3}|[0-9a-f]{32}',folder.name)), 'Unrecognised diagnostic folder name')
            report=folder/'macos-report.json'
            if report.is_file() and not report.is_symlink() and report.stat().st_size<=1024**2:
                data=json.loads(report.read_text(encoding='utf8'))
                if isinstance(data,dict):
                    row['version']=str(data.get('app_version') or 'Unknown version')[:80]
                    row['target']=str(data.get('menu_title') or 'Unknown Windows target')[:160]
            for name in ('Test1.efi','WindowsDiagnostics.efi'):
                executable=folder/name
                if not executable.is_file() or executable.is_symlink():continue
                require(executable.stat().st_size<=16*1024**2,'Oversized diagnostic executable')
                raw=executable.read_bytes();at=raw.find(MAGIC)
                if at<0 or raw.find(MAGIC,at+1)>=0:continue
                config=raw[at:at+SIZE]
                require(len(config)==SIZE and hashlib.sha256(config[:168]+config[200:]).digest()==config[168:200], 'Invalid kit binding')
                require(str(uuid.UUID(bytes_le=config[72:88]))==esp_guid.lower(),'Kit belongs to another EFI partition')
                version=struct.unpack_from('<I',config,32)[0]
                expected=config[264:280].split(b'\0')[0].decode('ascii') if version==2 else config[40:56].hex() if version==1 else None
                require(expected==folder.name,'Kit folder and executable binding disagree')
                row['verified']=True;break
        except (OSError,ValueError,UnicodeError,Refusal) as error:
            row['reason']=str(error)
        if not row['verified']:row.setdefault('reason','No valid target-bound diagnostic executable found')
        rows.append(row)
    return rows


def selected_files(root, rows):
    """No unselected sibling, linked node or path outside diagnostics is eligible."""
    root=Path(root);files={}
    for row in rows:
        require(row['verified'],'Cannot remove an unverified folder')
        folder=root/row['path']
        require(folder.parent==root/PREFIX and folder.name==row['folder'] and not folder.is_symlink(),'Invalid kit path')
        for p in folder.rglob('*'):
            require(not p.is_symlink() and p.stat().st_dev==root.stat().st_dev,'Linked diagnostic content is preserved')
            if p.is_file():
                require(p.stat().st_nlink==1 and p.stat().st_size<=16*1024**2,'Unexpected diagnostic file; preserved')
                files[p.relative_to(root).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
            else:require(p.is_dir(),'Unexpected diagnostic node; preserved')
    return files


class DiagnosticKits(InstallationManager):
    def execute(self):
        self.begin();all_rows=[]
        for esp in esp_candidates():
            try:
                if self.esp_owned:require(not self.close_esp(),'Could not close earlier EFI mount')
                root=self.mount_esp(esp['DeviceIdentifier'],readonly=True)
                for row in inspect(root,esp['DiskUUID']):
                    row['esp']=esp;all_rows.append(row)
            except (OSError,ValueError,Refusal) as error:
                self.note(esp['DeviceIdentifier']+': Could not list diagnostic kits: '+str(error))
        if not all_rows:
            self.interaction.request('notice','No EFI diagnostic kits were found. No files changed.',button_title='Done');return
        chosen=set()
        while True:
            labels=[('[DELETE] ' if i in chosen else '[Keep] ')+r['folder']+' — '+r['version']+'\n'+
                r['target']+' | '+r['esp']['parent_description']+' | '+r['esp']['DeviceIdentifier']+
                ('' if r['verified'] else ' | Unverified: cannot delete') for i,r in enumerate(all_rows)]
            answer=self.choose('Select a kit to mark or unmark it for deletion. Versions come from each saved report; unknown versions are not guessed. '
                'Logs inside selected kits will also be removed. Windows, OpenCore and other boot files are not selected.',
                labels+['Review deletion of %d selected kit(s)'%len(chosen),'Cancel'])
            if answer==len(labels)+1:return
            if answer==len(labels):
                if chosen:break
                continue
            if not all_rows[answer]['verified']:
                self.interaction.request('notice',all_rows[answer]['reason']+'. This folder will be kept.',button_title='Done');continue
            if answer in chosen:chosen.remove(answer)
            else:chosen.add(answer)
        selected=[all_rows[i] for i in sorted(chosen)]
        preview='\n'.join(r['folder']+' — '+r['version']+' — '+r['target']+' — '+r['esp']['DeviceIdentifier'] for r in selected)
        require(self.approve('review','Delete only these diagnostic kits and their collected logs?\n\n'+preview+
            '\n\nKeep a copy of any logs you want first. All unselected kits and normal boot files are preserved.'),'Deletion cancelled')
        receipts=[]
        for device in dict.fromkeys(r['esp']['DeviceIdentifier'] for r in selected):
            group=[r for r in selected if r['esp']['DeviceIdentifier']==device];esp=group[0]['esp']
            if self.esp_owned:require(not self.close_esp(),'Could not close previous EFI mount')
            same(esp,info(device));root=self.mount_esp(device,readonly=False)
            def guard():
                fresh=info(device);same(esp,fresh)
                require(fresh.get('MountPoint')==str(root) and fresh.get('ReadOnlyVolume') is False,'EFI mount changed')
            verified={r['folder']:r for r in inspect(root,esp['DiskUUID'])}
            require(all(verified.get(r['folder'],{}).get('verified') for r in group),'Kit identity changed during review')
            files=selected_files(root,group)
            require(files,'No recognised kit files to remove')
            tx=EFITransaction(root,self.session/('diagnostic-removal-'+device),guard)
            tx.prepare([dict(path=p,expected=h,data=None) for p,h in files.items()]);tx.commit()
            # rmdir only removes empty directories; never recursively delete.
            for row in group:
                folder=root/row['path']
                for d in sorted([p for p in folder.rglob('*') if p.is_dir()],key=lambda p:len(p.parts),reverse=True):
                    guard();require(not d.is_symlink(),'Directory changed');d.rmdir()
                guard();folder.rmdir()
            receipts.append(dict(esp=device,kits=[r['folder'] for r in group],deleted_files=files))
        run(['/bin/sync']);write_receipt(self.session/'diagnostic-removal.json',dict(deletions=receipts))
        self.interaction.request('notice','Selected EFI diagnostic kits deleted. Other diagnostic kits and normal boot files were preserved.',button_title='Done')
