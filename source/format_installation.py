"""GUI-confirmed NTFS reformat and exact installation EFI/menu cleanup.

Never invoked by build/tests on a physical disk. Runtime management remains
disabled until the complete workflow and recovery fixtures pass.
"""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import xml.etree.ElementTree as ET
from manage_installations import InstallationManager
from manage_format_plan import plan as make_plan
from boot_catalog import identity
from efi_transaction import EFITransaction
from install_windows7 import (ROOT,require,info,run,run_logged,gpt_for,match_gpt,
    assert_same_target,partition_summary,tree_manifest,require_efi_unchanged,
    write_receipt,require_sources_off_target,ensure_target_unmounted,assert_postformat_target)
from installation_policy import CLEANUP_WARNING, UNOWNED_WARNING
from installation_records import can_uninstall
from menu_lifecycle import snapshot
from operation_progress import phase,run_phase,Message


def review_text(ownership, cleanup_warning):
    """One short warning, with the full manual-cleanup guidance in Details."""
    text=ownership['message'].replace(CLEANUP_WARNING,'').strip()
    if ownership['status']=='unverified':
        text=('GoAwayBCA cannot verify who created this installation. We recommend '
              'leaving it unmanaged unless you are sure you want to erase it.')
    if cleanup_warning:
        text+=('\n\nShared or unidentified boot files will be kept. Files left untouched '
               'are listed in your saved log. Leftover boot files may affect booting. '
               'OpenCore and Apple boot files are preserved.')
    return text


def mount_formatted_target(target, gpt):
    """Mount only the freshly verified partition; never force or mount its disk."""
    device=target['partition']['DeviceIdentifier'];parent=info(target['disk']['DeviceIdentifier'])
    current=info(device)
    assert_postformat_target(target,current,parent)
    require(gpt_for(target)['digest']==gpt['digest'],'GPT layout changed before mounting')
    run(['/usr/sbin/diskutil','mount',device])
    mounted=info(device)
    # Mounting refreshes cached volume UUID/label from before the format.
    # Verify physical identity and geometry, not obsolete filesystem metadata.
    require(mounted.get('FilesystemType','').lower()=='ntfs' and bool(mounted.get('MountPoint')),
            'macOS did not report the formatted NTFS volume mounted')
    assert_postformat_target(target,dict(mounted,MountPoint=''),info(parent['DeviceIdentifier']))
    require(gpt_for(target)['digest']==gpt['digest'],'GPT layout changed while mounting')
    require(bool(mounted.get('MountPoint')),'macOS did not report a mounted volume')
    return mounted['MountPoint']


def empty_readback(tool,device,session):
    """Capture is a read-only filesystem check, not a backup of the erased data."""
    image=Path(session)/'formatted-empty.wim';xml=Path(session)/'formatted-empty.xml'
    require(not image.exists() and not xml.exists(),'Fresh format-check paths required')
    run([tool,'capture',device,image,'WinDeploy formatted NTFS verification','--compress=none'])
    run([tool,'info',image,'--extract-xml='+str(xml)])
    root=ET.fromstring(xml.read_bytes());images=root.findall('IMAGE')
    require(len(images)==1 and images[0].findtext('FILECOUNT')=='0'
        and images[0].findtext('TOTALBYTES')=='0' and images[0].findtext('DIRCOUNT')=='1',
        'Formatted filesystem did not read back as empty NTFS')


class FormatInstallation(InstallationManager):
    def execute(self):
        self.begin();phase(self.note,'Reading the Windows installation list');scan=self.discover()
        require(scan['scan_complete'],'Some partitions could not be inspected; no format started')
        write_receipt(self.session/'installation-discovery.json',scan)
        rows=scan['installations']
        if not rows:
            text='No Windows installations found. No files changed.'
            self.note(text)
            if self.interaction is not None:self.interaction.request('notice',text)
            return
        selected=rows[self.choose('Manage Installations - choose the Windows installation to review:',
            [r['title']+' | '+r['ownership']['status']+'\n'+r['summary'] for r in rows])]
        action=1
        if getattr(self.args,'allow_rename',False):
            actions=['Rename Windows picker entry (no formatting)']
            eligible=can_uninstall(selected['observed'],selected['marker'],selected['record'])
            if eligible:
                actions.append('Delete this installation, format as NTFS, clean up owned boot files and update the boot menu')
            action=self.choose('Choose an action for '+selected['title']+'\n\n'+selected['summary']+
                ('' if eligible else '\n\n'+UNOWNED_WARNING),actions)
        selected_id=identity(selected['observed'])
        phase(self.note,'Verifying boot-file ownership and shared EFI files for the selected action')
        verified=self.scan()
        require(verified['scan_complete'],'Some partitions could not be verified; no action started')
        matches=[r for r in verified['installations'] if identity(r['observed'])==selected_id]
        require(len(matches)==1,'Selected Windows installation changed since discovery')
        assert_same_target(selected['target'],matches[0]['target']['partition'],matches[0]['target']['disk'])
        selected=matches[0];scan=verified;rows=verified['installations']
        write_receipt(self.session/'installation-scan.json',verified)
        if action==0:
            from rename_installation import execute
            return execute(self,selected,scan)
        require(can_uninstall(selected['observed'],selected['marker'],selected['record']),UNOWNED_WARNING)
        target=selected['target'];device=target['partition']['DeviceIdentifier'];selected_id=identity(selected['observed'])
        assert_same_target(target,info(device),info(target['disk']['DeviceIdentifier']))
        require_sources_off_target([ROOT,self.session],device)
        gpt=gpt_for(target);win,esp=match_gpt(target,gpt)
        if self.esp_owned and self.esp_device!=target['esp']:
            require(not self.close_esp(),'Could not detach prior scan ESP')
        mount=self.mount_esp(target['esp'],readonly=True);before=tree_manifest(mount)
        require_efi_unchanged(before,selected['efi_manifest'],'EFI changed since discovery')
        catalog=mount/'EFI/WinDeploy/catalog.json';menu=mount/'EFI/WinDeploy/menu.bin'
        for p in (catalog,menu):
            require(not p.is_symlink() and (not p.exists() or p.stat().st_size<=1024*1024),'Unsafe chooser metadata')
        options=dict(scan_complete=True,catalog_bytes=catalog.read_bytes() if catalog.exists() else None,
            menu_bytes=menu.read_bytes() if menu.exists() else None,
            unknown_acknowledged=not selected['ownership']['managed'],
            lifecycle_files=snapshot(mount,before))
        import menu_refresh
        refresh=menu_refresh.prepare(self,mount,before,gpt['guid'],esp['guid'],rows,remove=selected_id,target=target)
        if refresh:options['menu_update']=refresh
        plan=make_plan(selected,rows,**options)
        backup=self.session/'efi-before';shutil.copytree(mount,backup)
        require(tree_manifest(backup,self.note,'Verifying EFI backup files')==before,'EFI backup verification failed')
        phase(self.note,'Checking format plan and bundled helpers')
        self.smoke_tools()
        summary=partition_summary(info(device),target['disk'],multiline=True)
        warning=('Double-check the label, capacity, free space and partition identifier. '
            'ALL files on ONLY this selected Windows partition will be erased. '
            'Your Windows data is NOT backed up by this tool. A wrong selection causes data loss; '
            'the tool and its authors are not responsible for data loss caused by selecting the wrong partition.')
        ownership_text=review_text(selected['ownership'],plan['cleanup_warning'])+menu_refresh.review(refresh)
        self.note(ownership_text)
        if plan['preserved_efi_paths']:
            self.note('EFI files retained:\n'+'\n'.join(plan['preserved_efi_paths']))
        guide=ROOT/'MANUAL-EFI-CLEANUP.txt'
        if guide.is_file():shutil.copy2(guide,self.session/guide.name)
        details=('EFI paths retained (not automatically deleted):\n'+
                 '\n'.join(plan['preserved_efi_paths'])+'\n\n'+ownership_text+
                 ('\n\n'+CLEANUP_WARNING if plan['cleanup_warning'] else ''))
        (self.session/'RETAINED-EFI-FILES.txt').write_text(details)
        details+='\n\nSaved file list: '+str(self.session/'RETAINED-EFI-FILES.txt')
        receipt=dict(status='review-only',target=target,ownership=selected['ownership'],
            preserved_efi_paths=plan['preserved_efi_paths'],efi_before=before,
            changes=[dict(path=c['path'],before=c['expected'],after=None if c['data'] is None else hashlib.sha256(c['data']).hexdigest()) for c in plan['changes']],
            repartition=False,set_default_boot=False,user_data_backed_up=False)
        receipt_path=self.session/'format-receipt.json';write_receipt(receipt_path,receipt)
        if self.args.dry_run:self.note('Format review complete; no physical writes.');return
        require(self.approve('format_ready','Format only this Windows partition as empty NTFS.\n\n'+summary+
            '\n\n'+ownership_text+'\n\nClick Format selected partition to begin the two confirmations; this click does not erase data.',details=details),
            'Formatting was not requested')
        require(self.approve('review','Confirmation 1 of 2.\n\n'+summary+'\n\n'+warning+'\n\n'+ownership_text,details=details),
            'First confirmation declined')
        require(self.confirm_erase('Confirmation 2 of 2.\n\n'+summary+'\n\n'+warning+'\n\n'+ownership_text+
            '\n\nType confirm to erase ONLY this Windows partition.','confirm',details=details),'Final confirmation declined')
        # Repeat discovery after both confirmations. No data from an earlier
        # stale selection authorises a changed cleanup list or target identity.
        fresh=self.scan();require(fresh['scan_complete'],'Installation discovery changed; no format started')
        if refresh:
            from backtoseven_menu_actions import verify_sources
            require(fresh['installations']==rows,'Windows discovery changed; start again before formatting')
            verify_sources(self,refresh['windows_sources'])
            menu_refresh.recheck(self,options['lifecycle_files'],gpt['guid'],esp['guid'],fresh['installations'],target,refresh)
        matches=[r for r in fresh['installations'] if identity(r['observed'])==selected_id]
        require(len(matches)==1,'Selected installation identity changed')
        require(make_plan(matches[0],fresh['installations'],**options)==plan,'Installation or cleanup plan changed; start again')
        if self.esp_owned and self.esp_device!=target['esp']:require(not self.close_esp(),'Could not detach prior scan ESP')
        mount=self.mount_esp(target['esp'],readonly=not bool(plan['changes']))
        def check_efi():
            state=info(target['esp'])
            require(state.get('DiskUUID','').lower()==esp['guid'] and state.get('MountPoint')==str(mount),'EFI identity changed')
            require(not plan['changes'] or state.get('ReadOnlyVolume') is False,'EFI is not writable')
            require(gpt_for(target)['digest']==gpt['digest'],'GPT layout changed')
        check_efi();require_efi_unchanged(tree_manifest(mount),before,'EFI changed before formatting')
        assert_same_target(target,info(device),info(target['disk']['DeviceIdentifier']))
        require_sources_off_target([ROOT,self.session],device)
        tx=None
        if plan['changes']:
            tx=EFITransaction(mount,self.session/'format-efi-transaction',check_efi);tx.prepare(plan['changes'])
        ensure_target_unmounted(target,self.note)
        raw='/dev/'+device;require(stat.S_ISBLK(os.stat(raw).st_mode),'Expected selected partition block device')
        receipt.update(status='format-started',confirmations=2);write_receipt(receipt_path,receipt);self.formatted=True
        run_phase([self.mkntfs,'-Q','-s',str(gpt.get('sector_size',512)),'-p',str(win['first']),'-H','255','-S','63',
            '-L','WINDOWS',raw,str(win['last']-win['first']+1)],self.note,'Formatting selected installation as empty NTFS')
        require(not info(device).get('MountPoint'),'Target auto-mounted before format readback')
        phase(self.note,'Verifying the formatted partition is empty');check_efi();empty_readback(self.wim,raw,self.session)
        receipt['status']='format-verified-menu-pending';write_receipt(receipt_path,receipt)
        # Only now may the erased installation disappear from our chooser.
        if tx:tx.commit(after_each=lambda index:self.note(Message(
            'Updating Windows menu: %d of %d files'%(index+1,len(tx.journal['files'])),
            'Updating Windows menu',100*(index+1)/len(tx.journal['files']))))
        phase(self.note,'Flushing writes and saving management receipt')
        run(['/bin/sync']);receipt['status']='format-complete';receipt['efi_after']=tree_manifest(mount)
        write_receipt(receipt_path,receipt)
        phase(self.note,'Mounting the empty NTFS partition')
        try:
            location=mount_formatted_target(target,gpt)
            receipt['mount_result']=dict(status='mounted',path=location)
            self.note('Empty NTFS partition mounted at '+location)
        except Exception as error:
            # Formatting and the menu transaction have already completed.
            receipt['mount_result']=dict(status='not-mounted',reason=str(error))
            self.note('Deletion completed, but macOS could not mount the empty partition: '+str(error))
        write_receipt(receipt_path,receipt)
        self.note('Selected Windows partition is now empty NTFS. Its WinDeploy menu entry has been removed if present.')
        self.note('Other installations, OpenCore and default startup were not changed. No automatic restart.')
        if plan['cleanup_warning']:self.note('Some EFI files were deliberately retained. See the saved report and Manual EFI Cleanup guide; do not remove them unless you understand their use.')
        if self.interaction is not None:
            text=('Windows has been removed from the selected partition. The partition is now empty NTFS, '
                  'and its entry has been removed from the Windows boot menu if present.')
            if receipt['mount_result']['status']=='mounted':
                text+='\n\nThe empty partition is mounted at '+receipt['mount_result']['path']+'.'
            else:text+='\n\nmacOS could not mount the empty partition. You can mount it in Disk Utility.'
            if plan['cleanup_warning']:
                text+='\n\nSome shared or unattributed EFI files were kept. The saved report lists them.'
            text+='\n\nReport: '+str(receipt_path)
            self.interaction.request('notice',text,button_title='Done')
