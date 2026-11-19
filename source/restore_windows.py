"""User-confirmed experimental Windows partition restoration.

All filesystem, registry and boot preparation happens in private regular files.
The existing install/driver/menu implementations are not modified by this path.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil

import backup_archive
import backup_drive
import backup_restore_boot
import backup_restore_filesystem as filesystem
import backup_restore_identity as volume_identity
import backup_restore_plan
import backup_restore_preparation as preparation
import backup_restore_write
from backup_windows import open_backup
from install_windows7 import (ROOT,require,run,info,inventory,partition_summary,
    require_sources_off_target,assert_same_target,assert_postformat_target,gpt_for,match_gpt,
    tree_manifest,require_efi_unchanged,write_receipt,ensure_target_unmounted)
from install_multiboot import eligible_targets
from manage_installations import InstallationManager
from installation_records import canonical,validate_record
from boot_catalog import identity
from efi_transaction import EFITransaction
from operation_progress import phase,hash_file
from ntfs_read import raw_read
import firmware_compatibility
import cpu_compatibility
import mac_compatibility
import menu_lifecycle
import menu_presence
import usb_installation

SYSTEM='/Windows/System32/config/SYSTEM'
SOFTWARE='/Windows/System32/config/SOFTWARE'


def prepare_private(owner,prepared,plan,target,gpt,win,esp,edition):
    """Complete preparation before the erase confirmations or any target write."""
    tools=Path(owner.args.tools_dir);work=owner.session/'restore-preparation';work.mkdir(mode=0o700)
    # Need space for files/metadata preparation as well as the already verified
    # materialized image. This is an estimate, not a claim of exact compression.
    require(shutil.disk_usage(work).free>plan['allocated_bytes']*2+1024**3,
            'Not enough temporary space to prepare this restore. The backup and destination are unchanged.')
    if plan['route']=='file-conversion':
        converted=filesystem.convert_private(prepared,plan['destination'],tools,tools/'backup-metadata-probe',work,owner.note)
        image=converted['image']
    else:
        image=prepared['image']
    system=work/'SYSTEM.original';software=work/'SOFTWARE.original'
    system.write_bytes(raw_read(owner.ntfscat,str(image),SYSTEM,256*1024**2))
    software.write_bytes(raw_read(owner.ntfscat,str(image),SOFTWARE,256*1024**2))
    mapped=work/'SYSTEM.mapped'
    mapping=volume_identity.prepare_hive(ROOT/'lib/libhivex.0.dylib',system,software,mapped,
        plan['source']['partition_guid'],plan['destination']['partition_guid'])
    usb=usb_installation.prepare(owner,target,edition,None,ROOT,system_source=mapped)
    final_system=usb['system'] if usb and usb['system'] is not None else mapped
    run([owner.ntfscp,image,final_system,SYSTEM])
    require(raw_read(owner.ntfscat,str(image),SYSTEM,256*1024**2)==final_system.read_bytes(),
            'The private restored registry failed readback. No destination was changed.')
    overlay=backup_restore_boot.prepare(image,plan,owner.session,ROOT,owner.wim,gpt,win,esp,owner.note)
    filesystem.command([owner.wim,'apply',overlay['wim'],'1',image,'--check','--strict-acls'],
                       work/'restore-overlay-apply.log',owner.note)
    for name,digest in overlay['files'].items():
        require(hashlib.sha256(raw_read(owner.ntfscat,str(image),'/'+name,16*1024**2)).hexdigest()==digest,
                'Private restore boot/history readback failed: '+name)
    # Keep this last: ntfsresize deliberately sets the consistency-check flag.
    resize=filesystem.resize_private(image,plan['destination']['partition_bytes'],
        plan['destination']['logical_sector_size'],tools/'ntfsresize',work,owner.note)
    volume=volume_identity.prepare_volume(image,plan['destination']['partition_offset'],
                                         plan['destination']['logical_sector_size'])
    stream=filesystem.export_verified(image,tools/'ntfsclone',work,resize['resized'],owner.note)
    return dict(stream=stream,overlay=overlay,mapping=mapping,volume=volume,resize=resize,
                usb_preparation=usb['choice'] if usb else None)


def menu_summary(before,after):
    def lines(entries):return '\n'.join('• '+entry['title']+' — '+entry['partition_guid'] for entry in entries) or '(no entries)'
    return ('Current saved menu (it may contain entries removed outside the app):\n'+lines(before)+
            '\n\nProposed menu after scanning this drive:\n'+lines(after))


class WindowsRestore(InstallationManager):
    def execute(self):
        self.begin()
        path=self.interaction.request('backup_open_file','Choose the Windows partition backup to restore.')
        require(type(path) is str and path.startswith('/'),'Choose an absolute Windows backup filename.')
        from product_scope import require_family
        with open_backup(self.interaction.user_uid,path) as source:
            preview=backup_archive.ImageReader(source).metadata
            require_family(preview['windows']['family'])
            prepared=preparation.materialize(source,self.session,self.note)
        inspected=preparation.inspect_windows(prepared,self.ntfscat)
        windows=inspected['windows']
        edition=dict(windows,name=windows['family'],version=windows['version'][:3])
        model=mac_compatibility.confirm_model(self.interaction,run(['/usr/sbin/sysctl','-n','hw.model']).decode().strip())
        self.confirmed_model=model
        firmware=firmware_compatibility.configure(edition,self.interaction)
        compatibility=mac_compatibility.configure(model,edition,self.interaction)
        cpu=cpu_compatibility.configure(edition,self.interaction)
        scan=self.scan();require(scan['scan_complete'],'Windows discovery is incomplete. Resolve the unreadable partitions before restoring.')
        from existing_windows_consent import confirm
        confirm(scan,self.interaction)
        targets=eligible_targets(inventory(),scan)
        require(targets,'No empty exFAT/NTFS destination is available. The backup and installed systems were not changed.')
        from destination_picker import choose
        target=choose(self,targets,windows['family']+' backup')
        from installation_destination import check as check_destination
        check_destination(target,Path(self.ntfscat).with_name('ntfsls'))
        device=target['partition']['DeviceIdentifier'];parent=target['disk']['DeviceIdentifier']
        protected=[Path(path),ROOT,self.session]
        require_sources_off_target(protected,device)
        location=backup_drive.describe(target)
        plan=backup_restore_plan.plan(prepared['metadata'],prepared['geometry'],location,inspected['marker'],windows)
        from entry_names import prompt
        plan['title']=prompt(self.interaction,plan['title'],context='Choose the boot-menu name for the restored installation.')
        text,details=backup_restore_plan.review(plan)
        if plan['source']['connection']=='USB' and location['connection']=='Internal':
            details+='\n\nExisting portable-Windows settings are preserved when restoring internally. Other internal volumes may remain offline; review Windows Disk Management after booting.'
        require(self.approve('restore_review',text,details=details),'Restore cancelled. No destination was changed.')
        gpt=gpt_for(target);win,esp=match_gpt(target,gpt)
        require(location['logical_sector_size']==gpt['sector_size'],'Destination sector geometry changed.')
        if self.esp_owned and self.esp_device!=target['esp']:require(not self.close_esp(),'Could not close the prior EFI mount.')
        phase(self.note,'Backing up existing EFI files before restore preparation')
        mount=self.mount_esp(target['esp'],readonly=True)
        before=tree_manifest(mount,self.note,'Checking existing EFI files')
        backup=self.session/'efi-before';shutil.copytree(mount,backup)
        require(tree_manifest(backup,self.note,'Checking EFI backup')==before,'EFI backup verification failed.')
        from backtoseven_menu_actions import read_efi,assemble,verify_sources
        import backtoseven_menu
        files=read_efi(backup,before);old=backtoseven_menu.previous(files,gpt['guid'],esp['guid'])
        removed=menu_presence.collect(self,old['catalog']['entries'],scan['installations'],target)
        staged=prepare_private(self,prepared,plan,target,gpt,win,esp,edition)
        overlay=staged['overlay']
        menu=assemble(self,backup,files,gpt['guid'],esp['guid'],scan['installations'],
            addition=overlay,preserve_addition_paths=True,confirmed_absent=removed)
        record_path='EFI/WinDeploy/Records/'+win['guid']+'.json'
        previous=menu_lifecycle.indexed(before).get(record_path.casefold(),(record_path,None))
        if overlay['record'] is not None:
            if previous[1] is not None:
                prior=validate_record(json.loads((backup/previous[0]).read_text()))
                require(identity(prior)==gpt['guid']+':'+win['guid'] and
                        identity(prior) in {identity(row) for row in removed},
                        'An existing ownership record must be reviewed before replacing it.')
            menu['changes'].insert(0,dict(path=previous[0],expected=previous[1],data=canonical(overlay['record'])+b'\n'))
        # For unverified origins, leave any old unattributed ledger file alone.
        # The new explicit unverified partition marker cannot pair with it.
        require(shutil.disk_usage(mount).free>sum(len(c['data'] or b'') for c in menu['changes'])+8*1024**2,
                'Not enough EFI free space. No destination was changed.')
        receipt=dict(status='prepared-no-destination-writes',backup=path,restore=plan,preparation=staged,
                     target=target,catalog=menu['catalog'],efi_before=before,externally_removed_entries=removed,
                     firmware=firmware,compatibility=compatibility,cpu=cpu)
        receipt_path=self.session/'restore-receipt.json';write_receipt(receipt_path,receipt)
        if self.args.dry_run:return
        summary=partition_summary(info(device),target['disk'],multiline=True)
        warning='ALL FILES ON THIS SELECTED PARTITION WILL BE ERASED. Check its name, capacity and partition identifier. Keep your original backup.'
        details=menu_summary(old['catalog']['entries'],menu['catalog']['entries'])
        require(self.approve('restore_ready','Restore “'+plan['title']+'” to:\n\n'+summary+'\n\n'+warning+
            '\n\nThis starts two further confirmations; it does not erase data yet.',details=details),'Restore was not requested.')
        require(self.approve('review','Confirmation 1 of 2.\n\n'+summary+'\n\n'+warning),'First restore confirmation declined.')
        require(self.confirm_erase('Confirmation 2 of 2.\n\n'+summary+'\n\n'+warning+'\n\nType confirm to continue.','confirm'),
                'Final restore confirmation declined.')
        firmware_compatibility.revalidate(edition,firmware)
        fresh=self.scan()
        require(fresh['scan_complete'] and fresh['installations']==scan['installations'],
                'Existing Windows installations changed during review. Start again.')
        require(any(t['partition']['DeviceIdentifier']==device for t in eligible_targets(inventory(),fresh)),
                'The selected destination is no longer empty or eligible.')
        menu_presence.recheck(self,old['catalog']['entries'],fresh['installations'],target,removed)
        verify_sources(self,menu['windows_sources'])
        require(backup_drive.describe(target)==location,'The selected drive, connection or geometry changed during review.')
        require_sources_off_target(protected,device)
        if self.esp_owned and self.esp_device!=target['esp']:require(not self.close_esp(),'Could not close the scan EFI mount.')
        mount=self.mount_esp(target['esp'],readonly=False)
        def guard():
            state=info(target['esp'])
            require(state.get('DiskUUID','').lower()==esp['guid'] and state.get('MountPoint')==str(mount)
                    and state.get('ReadOnlyVolume') is False,'EFI mount identity changed.')
            require(gpt_for(target)['digest']==gpt['digest'],'The destination partition layout changed.')
        guard();assert_same_target(target,info(device),info(parent))
        require_efi_unchanged(tree_manifest(mount),before,'EFI changed during restore review.')
        stream=staged['stream']
        require(hash_file(stream['image'],self.note,'Checking prepared restore data before writing')==stream['sha256'],
                'The prepared restore stream changed.')
        tx=EFITransaction(mount,self.session/'efi-transaction',guard);tx.prepare(menu['changes'])
        check_destination(target,Path(self.ntfscat).with_name('ntfsls'))
        ensure_target_unmounted(target,self.note)
        def target_guard():
            guard();assert_postformat_target(target,info(device),info(parent))
        receipt.update(status='restore-write-started',confirmations=3);write_receipt(receipt_path,receipt)
        self.formatted=True
        receipt['write_verification']=backup_restore_write.write_partition(stream,'/dev/'+device,target_guard,self.note)
        guard();verify_sources(self,menu['windows_sources'])
        require_efi_unchanged(tree_manifest(mount),before,'EFI changed before restore menu publication.')
        tx.commit(after_each=lambda index:self.note('Updating this drive\'s Windows menu: %d of %d files.'%(index+1,len(tx.journal['files']))))
        run(['/bin/sync'])
        receipt.update(status='restored-awaiting-first-boot',efi_after=tree_manifest(mount));write_receipt(receipt_path,receipt)
        message=('Windows partition restore completed and its data was checked.\n\nRestart when ready and select Windows, then “'+
            plan['title']+'” in its menu. With the Mac startup picker, hold Option while starting. With OpenCore, use its Windows entry.\n\n'
            'Keep the original backup until you have tested the restored Windows installation. Drivers, activation and booting may differ on another drive or connection.')
        if staged['resize']['requires_windows_filesystem_check']:
            message+='\n\nWindows may run a filesystem check and restart because the restored filesystem was resized. Let it finish.'
        message+='\n\nTemporary preparation files remain. Use Saved Files to review them for deletion.'
        self.interaction.request('notice',message,button_title='Done')
