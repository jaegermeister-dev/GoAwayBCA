"""User-confirmed 7/8.1/10 deployment into an existing empty eligible partition.

No partition creation, security changes, automatic reboot or default selection.
Windows 8.1/10 retain original loaders. A ROM-table experiment is applied only
when selected by the explicit model/OS policy; 11,5 Windows 10 stays stock.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import uuid
from install_windows7 import (ROOT,GIB,require,run,run_logged,info,inventory,partition_summary,
    require_sources_off_target,assert_same_target,assert_postformat_target,gpt_for,match_gpt,
    tree_manifest,require_efi_unchanged,sha256,write_receipt,ensure_target_unmounted,ci_child)
from manage_installations import InstallationManager
from windows_images import inspect_editions
from installation_records import canonical,make_marker
from boot_catalog import identity
from efi_transaction import EFITransaction
import menu_lifecycle as lifecycle
import prepared_media
import win7_iso_source
import windows10_boot
import driver_preparation
from entry_names import prompt as prompt_entry_name
from installation_policy import efi_policy,validate_efi_choice
import experimental_efi
import boot_dependencies
import firmware_compatibility
import mac_compatibility
import cpu_compatibility
from operation_progress import phase, Meter, Message, hash_file, run_phase

FAMILIES=('Windows 7','Windows 8','Windows 8.1','Windows 10','Windows 11')

def volume_label(family):
    """Filesystem label only; custom picker names and GUID identity are separate."""
    require(family in FAMILIES,'Unsupported Windows family for partition label')
    return family.replace(' ','-')

def destination_prompt(family):
    """Keep eligibility guidance visible in the actual selection dialog."""
    require(family in FAMILIES,'Unsupported Windows family for partition selection')
    return ('Choose the destination partition for '+family+'.\n\n'
            "If you don't see the partition you intend to install "+family+
            " to, make sure it's formatted as exFAT or NTFS.\n\n"
            'Partitions that already contain Windows are not listed. Delete that installation '
            'using Manage Installations before installing again.')

def choose_efi(interaction,model,family,note):
    policy=efi_policy(model,family);note(policy['message'])
    enabled=(interaction.efi_choice(policy['message'],policy['default_enabled'])
             if policy['show_choice'] else policy['default_enabled'])
    validate_efi_choice(model,family,enabled)
    return enabled

def eligible_targets(targets,scan):
    require(scan['scan_complete'],'Installation scan incomplete; resolve the reported unreadable partition first')
    occupied={r['target']['partition']['DeviceIdentifier'] for r in scan['installations']}
    return [t for t in targets if t['partition']['DeviceIdentifier'] not in occupied]

def stock_entry(record):
    path='EFI/Microsoft/Boot/bootmgfw.efi'
    asset=next(a for a in record['efi_assets'] if a['path'].casefold()==path.casefold())
    return dict(disk_guid=record['disk_guid'],partition_guid=record['partition_guid'],esp_guid=record['esp_guid'],
        boot_partition_guid=record['partition_guid'],title=record['family'],loader_path=path,
        bcd_path='EFI/Microsoft/Boot/BCD',single_os_bcd_verified=True,available=True,
        requires_ntfs=True,hash_locked=False,loader_sha256=asset['sha256'],ownership='verified-windeploy')

class MultiBootInstaller(InstallationManager):
    def prepare_accepted_additions(self,wim,edition,stage):
        if not getattr(self.args,'win7_accepted_extras',False):return
        import windows7_extras
        model=getattr(self,'confirmed_model',None) or run(['/usr/sbin/sysctl','-n','hw.model']).decode().strip()
        from keyboard_policy import enabled_for
        if not enabled_for(model, getattr(self,'keyboard_preparation_enabled',None)):return
        # Driver selection was already made in the pre-format dialogs. Keep
        # it separate from mandatory keyboard/graphics boot preparation.
        self.accepted_extras=windows7_extras.prepare(self.wim,wim,edition['index'],
            self.session,ROOT/'lib/libhivex.0.dylib',ROOT,model,False)
        self.note('Original inbox keyboard discovery prepared. Normal Windows setup and UAC are unchanged.')

    def prepare_win7(self,wim,edition,gpt,win,esp,wim_sha,model,efi_enabled):
        require(edition['family']=='Windows 7','Windows 7 preparation requires a Windows 7 edition')
        validate_efi_choice(model,'Windows 7',efi_enabled)
        stage=self.extract_boot(wim,edition,gpt,win,esp)
        self.prepare_accepted_additions(wim,edition,stage)
        if model=='MacBookPro11,5':
            # The proven graphics helper is partition-independent. Its selected
            # Windows target is the BCD activated/validated by our EFI selector,
            # not an old lab GUID or a diskutil device number.
            import win7_accepted_efi
            assets=ROOT/'efi/accepted-win7'
            self.accepted_gpu=win7_accepted_efi.stage_for_target(self.session,stage,
                assets/'WIN7AUTO.efi',assets/'ALLOW-AUTO.txt',model,'Windows 7',gpt,win,esp)
            self.note('Windows 7 graphics helper prepared automatically for selected partition '+win['guid']+'.')
        canonical_bcd=(stage/'Microsoft/Boot/BCD').read_bytes()
        esp_files={}
        for path in stage.rglob('*'):
            if path.is_file():
                rel='EFI/'+path.relative_to(stage).as_posix()
                if rel not in ('EFI/Microsoft/Boot/BCD','EFI/Microsoft/Boot/bootmgfw.efi','EFI/Boot/BOOTX64.EFI'):
                    esp_files[rel]=path.read_bytes()
        if model=='MacBookPro11,5':
            loader='EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi'
        else:
            folder='EFI/WinDeploy/Installs/'+win['guid']+'/'
            original=(stage/'Microsoft/Boot/bootmgfw.efi').read_bytes()
            loader=folder+('Fix.efi' if efi_enabled else 'bootmgfw.efi')
            additions=experimental_efi.files(ROOT,original) if efi_enabled else {'bootmgfw.efi':original}
            esp_files.update({folder+name:data for name,data in additions.items()})
            if model not in ('MacBookPro11,4','MacBookPro11,5'):
                # Preserve a per-installation store outside the disposable
                # selector metadata, so Reset does not erase its only copy.
                # This is boot-record recovery, not a model-specific EFI fix.
                esp_files[folder+'BCD']=canonical_bcd
        sector_route=None
        if gpt.get('sector_size',512)==4096:
            import win7_sector
            esp_files,loader,sector_route=win7_sector.wrap(esp_files,win['guid'],loader,
                getattr(self,'prepared_4kn',None),(ROOT/'efi/Win7SectorBoot.efi').read_bytes())
        own=[dict(partition_guid=esp['guid'],path=p,sha256=lifecycle.sha(data),exclusive=True)
             for p,data in esp_files.items() if p==loader or p.startswith('EFI/WinDeploy/Installs/'+win['guid']+'/') or p in (
                 'EFI/W7DEP-WIN7-AUTO/WIN7AUTO.efi','EFI/W7DEP-WIN7-AUTO/ALLOW-AUTO.txt','EFI/Microsoft/Boot/bootmgfw-w7dep-original.efi',
                 'EFI/Boot/BOOTX64-W7DEP-ORIGINAL.EFI')]
        record=dict(schema=1,creator='WinDeploy',state='committed',installation_uuid=str(uuid.uuid4()),
            disk_guid=gpt['guid'],partition_guid=win['guid'],esp_guid=esp['guid'],
            partition_bytes=(win['last']-win['first']+1)*gpt.get('sector_size',512),partition_offset=win['first']*gpt.get('sector_size',512),
            family=edition['family'],edition=edition['name'],image_index=edition['index'],
            wim_sha256=wim_sha,efi_assets=own,experimental_rom_table=efi_enabled and model!='MacBookPro11,5')
        if model=='MacBookPro11,5':record['graphics_helper_binding']=self.accepted_gpu['target_binding']
        if sector_route is not None:record['sector_compatibility']=sector_route
        overlay=self.session/'ntfs-overlay';(overlay/'WinDeploy').mkdir(parents=True)
        import report_metadata
        (overlay/'WinDeploy/deployment.json').write_text(json.dumps(report_metadata.deployment(ROOT,edition,model,gpt['guid'],win['guid'],
            keyboard_preparation=getattr(self,'keyboard_preparation_enabled',None)),indent=2)+'\n')
        (overlay/'WinDeploy/installation.json').write_bytes(canonical(make_marker(record))+b'\n')
        overlay_wim=self.session/'ntfs-overlay.wim'
        run([self.wim,'capture',overlay,overlay_wim,'WinDeploy installation record','--compress=none'])
        entry=dict(disk_guid=gpt['guid'],partition_guid=win['guid'],esp_guid=esp['guid'],
            boot_partition_guid=esp['guid'],title='Windows 7',loader_path=loader,
            bcd_path='EFI/Microsoft/Boot/BCD',single_os_bcd_verified=True,available=True,
            requires_ntfs=False,hash_locked=True,loader_sha256=lifecycle.sha(esp_files[loader]),
            ownership='verified-windeploy')
        return dict(record=record,canonical_bcd=canonical_bcd,entry=entry,esp_files=esp_files,
            wim=overlay_wim,wim_sha256=sha256(overlay_wim),files=tree_manifest(overlay))

    def prepare_installation_selection(self,early_scan=False):
        """Read and approve one image; no downloads, formatting or EFI writes.

        The unified dispatcher retains this in memory for its chosen engine.
        No GUI request or persisted receipt can supply a preapproved selection.
        """
        require(platform.system()=='Darwin' and platform.machine()=='x86_64' and os.geteuid()==0
                and self.interaction is not None,'Use the authorised Intel macOS app')
        iso=Path(self.args.iso).expanduser().resolve()
        require(iso.is_file() and iso.suffix.lower()=='.iso','Select a Windows ISO')
        self.begin()
        # Identify actual ISO contents before the FIRST preparation dialog.
        # Reuse this single mount/metadata read for the later edition picker.
        phase(self.note,'Reading the selected ISO for early sector-size guidance')
        media=self.mount_iso(iso);wim=ci_child(ci_child(media,'sources'),'install.wim')
        require(wim.is_file(),'This build requires sources/install.wim in the ISO')
        xml=self.session/'editions.xml';run([self.wim,'info',wim,'--extract-xml='+str(xml)])
        editions=inspect_editions(xml.read_bytes())
        from sector_guidance import inform
        inform(editions,self.interaction)
        detected_model=run(['/usr/sbin/sysctl','-n','hw.model']).decode().strip()
        model=mac_compatibility.confirm_model(self.interaction,detected_model)
        self.confirmed_model=model
        self.note('GoAwayBCA Beta 1: Windows 8.1 / 10 / 11. OpenCore is recommended.')
        self.note('No partition resizing, default-boot changes, automatic restart or automatic driver installation.')
        initial_scan=None
        if early_scan:
            phase(self.note,'Checking existing Windows before installation preparation')
            initial_scan=self.scan()
            write_receipt(self.session/'initial-installation-scan.json',initial_scan)
            require(initial_scan['scan_complete'],'Installation scan incomplete; resolve the reported unreadable partition first')
            from destination_availability import check
            check(inventory(),initial_scan,self.interaction)
            from existing_windows_consent import confirm
            confirm(initial_scan,self.interaction)
        phase(self.note,'Choose the Windows edition from the selected ISO')
        edition=editions[self.choose('Choose the Windows edition:',
            ['%d. %s [%s; WIM index %d]'%(i+1,e['name'],e['family'] or e['refusal'],e['index']) for i,e in enumerate(editions)])]
        require(edition['family'] in FAMILIES,'This build installs Windows 7, 8, 8.1, 10 and 11')
        firmware_evidence=firmware_compatibility.configure(edition,self.interaction)
        model_guidance=mac_compatibility.configure(model,edition,self.interaction)
        cpu_guidance=cpu_compatibility.configure(edition,self.interaction)
        from keyboard_policy import configure as configure_keyboard
        self.keyboard_preparation_enabled=configure_keyboard(model,edition['family'],self.interaction,self.note)
        return dict(detected_model=detected_model,model=model,iso=iso,wim=wim,edition=edition,
                    firmware_evidence=firmware_evidence,model_guidance=model_guidance,
                    cpu_guidance=cpu_guidance,initial_scan=initial_scan)

    def execute(self):
        # Only the in-process unified dispatcher sets this after real preflight.
        selection=self.__dict__.pop('_unified_selection',None)
        if selection is None:selection=self.prepare_installation_selection()
        detected_model,model,iso,wim,edition=(selection[k] for k in
            ('detected_model','model','iso','wim','edition'))
        firmware_evidence,model_guidance,cpu_guidance=(selection[k] for k in
            ('firmware_evidence','model_guidance','cpu_guidance'))
        entry_title=prompt_entry_name(self.interaction,edition['family'])
        efi_enabled=choose_efi(self.interaction,model,edition['family'],self.note)
        driver_choice=driver_preparation.configure(self.interaction,model,edition['family'],model_guidance)
        self.driver_delivery=driver_preparation.prepare(driver_choice,ROOT,self.session,self.wim,self.interaction,self.note)
        if edition['family']=='Windows 7':
            phase(self.note,'Verifying and preparing the Windows 7 source')
            wim,self.prepared_profile=win7_iso_source.prepare(ROOT,wim,self.session,self.wim,self.note)
            # The selected stock and prepared image must expose the same edition.
            prepared_xml=self.session/'prepared-editions.xml'
            run([self.wim,'info',wim,'--extract-xml='+str(prepared_xml)])
            matching=[e for e in inspect_editions(prepared_xml.read_bytes()) if e['index']==edition['index']]
            require(len(matching)==1 and matching[0]['family']=='Windows 7' and
                    matching[0]['name']==edition['name'],'Prepared image edition does not match selected ISO edition')
            edition=matching[0]
        phase(self.note,'Checking existing Windows installations and boot ownership')
        scan=self.scan();write_receipt(self.session/'installation-scan.json',scan)
        targets=eligible_targets(inventory(),scan)
        require(targets,'No eligible empty exFAT/NTFS partition. Erase an existing Windows installation through Manage Installations first.')
        target=targets[self.choose(destination_prompt(edition['family']),
            [partition_summary(t['partition'],t['disk']) for t in targets])]
        from disk_layout import require_windows_sector_support
        import win7_sector
        self.prepared_4kn=win7_sector.prepare_target(self,target,edition,wim,ROOT,model)
        require_windows_sector_support(target,edition['family'],self.prepared_4kn)
        import win8_nvme
        optional_nvme=win8_nvme.prepare(self,target,edition,wim,ROOT) if edition['family']=='Windows 8' else None
        from installation_destination import check as check_destination
        check_destination(target,Path(self.ntfscat).with_name('ntfsls'))
        p=target['partition'];device=p['DeviceIdentifier']
        driver_bytes=self.driver_delivery['bytes'] if self.driver_delivery else 0
        require(p['TotalSize']>=max(32*GIB,edition['bytes']+driver_bytes+4*GIB),'Partition too small for selected edition and drivers')
        protected=[iso,ROOT,self.session,wim]
        require_sources_off_target(protected,device)
        assert_same_target(target,info(device),info(target['disk']['DeviceIdentifier']))
        gpt=gpt_for(target);win,esp=match_gpt(target,gpt)
        if self.esp_owned and self.esp_device!=target['esp']:require(not self.close_esp(),'Could not detach scan ESP')
        phase(self.note,'Backing up and verifying existing EFI files')
        mount=self.mount_esp(target['esp'],readonly=True);before=tree_manifest(mount,self.note,'Reading current EFI file hashes')
        backup=self.session/'efi-before';shutil.copytree(mount,backup)
        require(tree_manifest(backup,self.note,'Verifying EFI backup files')==before,'EFI backup verification failed')
        if getattr(self,'unified_menu',False):
            import backtoseven_menu as component_menu
            from backtoseven_menu_actions import read_efi,assemble,verify_sources
            files=read_efi(backup,before)
            old=component_menu.previous(files,gpt['guid'],esp['guid'])
        else:
            files=lifecycle.snapshot(backup,before)
            old=lifecycle.inspect(files,gpt['guid'],esp['guid'])
        import menu_presence
        removed=menu_presence.collect(self,old['catalog']['entries'],scan['installations'],target)
        removed_ids={identity(p) for p in removed}
        require(not any(e['partition_guid']==win['guid'] and identity(e) not in removed_ids for e in old['catalog']['entries']),
                'This partition has an existing chooser entry. Use Manage Installations before reinstalling.')
        from optional_verification import verify_wim
        image_verification=verify_wim(self.wim,wim,self.note,self.interaction)
        phase(self.note,'Testing bundled NTFS helpers on disposable files')
        self.smoke_tools();wim_sha=hash_file(wim,self.note,'Hashing selected Windows image')
        phase(self.note,'Preparing independent Windows boot files and menu transaction')
        if edition['family']=='Windows 7':
            overlay=self.prepare_win7(wim,edition,gpt,win,esp,wim_sha,model,efi_enabled)
        else:
            template=ROOT/('BCD81.template' if edition['family'] in ('Windows 8','Windows 8.1') else 'BCD10.template')
            overlay=windows10_boot.stage(self.wim,wim,edition,self.session,gpt,win,esp,wim_sha,template,self.note)
            overlay['entry']=stock_entry(overlay['record']);overlay['esp_files']={}
            if efi_enabled:experimental_efi.wrap_stock(overlay,ROOT,self.wim,self.session)
        overlay['entry']['title']=entry_title
        if getattr(self,'unified_menu',False):
            plan=assemble(self,backup,files,gpt['guid'],esp['guid'],scan['installations'],
                addition=overlay,preserve_addition_paths=True,confirmed_absent=removed)
        else:
            plan=lifecycle.transition(files,before,gpt['guid'],esp['guid'],scan['installations'],
                addition=overlay['entry'],new_store=overlay['canonical_bcd'],
                chooser=(ROOT/'efi/Windows.efi').read_bytes(),ntfs_driver=(ROOT/'efi/OpenNtfsDxe.efi').read_bytes(),ntfs_4kn_driver=(ROOT/'efi/Ntfs4Kn.efi').read_bytes(),confirmed_absent=removed)
        originals=lifecycle.indexed(before)
        dependencies=boot_dependencies.plan(before,overlay['esp_files'],
            (asset['path'] for asset in overlay['record']['efi_assets']))
        plan['changes'][0:0]=dependencies['changes']
        for kept in dependencies['preserved']:
            self.note('Preserving existing shared Windows memory-diagnostic file: '+kept['path']+
                '. It differs from the ISO copy and will not be overwritten; Windows installation can continue.')
        record_path='EFI/WinDeploy/Records/'+win['guid']+'.json'
        record_before=originals.get(record_path.casefold(),(record_path,None))
        if record_before[1] is not None:
            require(gpt['guid']+':'+win['guid'] in removed_ids,
                    'Existing installation ownership record must be reviewed in Manage Installations')
            stale_record=json.loads((backup/record_before[0]).read_text())
            require(identity(stale_record)==gpt['guid']+':'+win['guid'],
                    'Existing ownership record belongs to another partition')
        plan['changes'].insert(0,dict(path=record_before[0],expected=record_before[1],data=canonical(overlay['record'])+b'\n'))
        require(shutil.disk_usage(mount).free>sum(len(c['data'] or b'') for c in plan['changes'])+8*1024**2,
                'Insufficient EFI free space; no target formatted')
        receipt=dict(status='preflight-only',edition=edition,target=target,record=overlay['record'],
            catalog=plan['catalog'],efi_before=before,efi_fix_enabled=efi_enabled,image_verification=image_verification,
            windows7_fixes=edition['family']=='Windows 7' and model=='MacBookPro11,5',
            keyboard_preparation_enabled=getattr(self,'keyboard_preparation_enabled',None),
            menu_title=entry_title,driver_choice=driver_choice,driver_preparation=self.driver_delivery['receipt'] if self.driver_delivery else None,
            preserved_boot_dependencies=dependencies['preserved'],externally_removed_entries=removed,
            firmware_compatibility=firmware_evidence,
            mac_compatibility=model_guidance,cpu_compatibility=cpu_guidance,
            confirmed_model=model,detected_model=detected_model,
            accepted_gpu_staging=self.accepted_gpu)
        receipt['optional_windows8_nvme']=optional_nvme['receipt'] if optional_nvme else None
        receipt_path=self.session/'receipt.json';write_receipt(receipt_path,receipt)
        if self.args.dry_run:self.note('Preflight complete. No target or EFI writes.');return
        summary=partition_summary(info(device),target['disk'],multiline=True)
        warning=('Double-check the label, capacity, free space and partition identifier. ALL files on this '
                 'selected partition will be erased. Its data is NOT backed up by this tool. '
                 'The tool and its authors are not responsible for data loss caused by selecting the wrong partition.')
        require(self.approve('format_ready','Install '+edition['name']+' into this partition as NTFS.\n\n'+summary+
            '\n\nClick Format selected partition and install to begin the two confirmations. This click does not erase data.'),'Not requested')
        require(self.approve('review','Confirmation 1 of 2.\n\n'+summary+'\n\n'+warning),'First confirmation declined')
        require(self.confirm_erase('Confirmation 2 of 2.\n\n'+summary+'\n\n'+warning+'\n\nType confirm to continue.','confirm'),'Final confirmation declined')
        phase(self.note,'Rechecking selected partition and existing installations before formatting')
        firmware_compatibility.revalidate(edition,firmware_evidence)
        fresh=self.scan()
        require(fresh['scan_complete'],'Windows discovery could not be completed; no partition formatted')
        menu_presence.recheck(self,old['catalog']['entries'],fresh['installations'],target,removed)
        require(any(t['partition']['DeviceIdentifier']==device for t in eligible_targets(inventory(),fresh)),
                'Selected target now contains Windows or is no longer eligible')
        if getattr(self,'unified_menu',False):
            require(fresh['installations']==scan['installations'],'Existing Windows installations changed; start again')
            verify_sources(self,plan['windows_sources'])
        if self.esp_owned and self.esp_device!=target['esp']:require(not self.close_esp(),'Could not detach scan ESP')
        mount=self.mount_esp(target['esp'],readonly=False)
        def guard():
            state=info(target['esp'])
            require(state.get('DiskUUID','').lower()==esp['guid'] and state.get('MountPoint')==str(mount)
                    and state.get('ReadOnlyVolume') is False,'EFI mount identity changed')
            require(gpt_for(target)['digest']==gpt['digest'],'GPT layout changed')
        guard();assert_same_target(target,info(device),info(target['disk']['DeviceIdentifier']))
        require_efi_unchanged(tree_manifest(mount),before,'EFI changed during confirmation')
        require(hash_file(wim,self.note,'Rechecking Windows source before formatting')==wim_sha and
                hash_file(overlay['wim'],self.note,'Rechecking boot-file image')==overlay['wim_sha256'],'Staged image changed')
        if self.driver_delivery:
            require(hash_file(self.driver_delivery['wim'],self.note,'Rechecking driver image before formatting')==
                    self.driver_delivery['wim_sha256'],'Prepared driver image changed before format')
        require_sources_off_target(protected,device)
        tx=EFITransaction(mount,self.session/'efi-transaction',guard);tx.prepare(plan['changes'])
        check_destination(target,Path(self.ntfscat).with_name('ntfsls'))
        ensure_target_unmounted(target,self.note);raw='/dev/'+device
        require(stat.S_ISBLK(os.stat(raw).st_mode),'Expected selected partition block device')
        receipt.update(status='format-started',confirmations=2);write_receipt(receipt_path,receipt);self.formatted=True
        run_phase([self.mkntfs,'-Q','-s',str(gpt.get('sector_size',512)),'-p',str(win['first']),'-H','255','-S','63',
            '-L',volume_label(edition['family']),raw,str(win['last']-win['first']+1)],self.note,'Formatting selected partition as NTFS')
        require(not info(device).get('MountPoint'),'Target auto-mounted before image application')
        guard();assert_postformat_target(target,info(device),info(target['disk']['DeviceIdentifier']))
        run_phase([self.wim,'apply',wim,str(edition['index']),raw,'--check','--strict-acls'],self.note,'Installing Windows image')
        meter=Meter(self.note,'Verifying installed Windows boot and registry files',3,'files verified')
        for number,member in enumerate(('/Windows/System32/winload.efi','/Windows/System32/config/SYSTEM','/Windows/System32/config/SOFTWARE'),1):
            require(run([self.ntfscat,raw,member])==run([self.wim,'extract',wim,str(edition['index']),member,'--to-stdout']),
                    'Installed Windows file differs from selected image: '+member)
            meter.update(number,member,force=True)
        prepared_meter=Meter(self.note,'Verifying Windows 7 prepared components',len(self.prepared_readback),'files verified') if self.prepared_readback else None
        for number,(member,expected) in enumerate(self.prepared_readback.items(),1):
            require(hashlib.sha256(run([self.ntfscat,raw,member])).hexdigest()==expected,'Prepared Windows 7 component mismatch')
            prepared_meter.update(number,member)
        self.apply_accepted_additions(raw,target,gpt)
        def driver_guard():
            guard();current=info(device)
            assert_postformat_target(target,current,info(target['disk']['DeviceIdentifier']))
            require(not current.get('MountPoint'),'Target mounted during driver copy')
        receipt['optional_windows8_nvme_applied']=win8_nvme.apply(optional_nvme,self,raw,driver_guard)
        receipt['driver_verification']=driver_preparation.apply(self.driver_delivery,self.wim,self.ntfscat,raw,driver_guard,self.note,
                                                               interaction=self.interaction)
        write_receipt(receipt_path,receipt)
        guard();require(not info(device).get('MountPoint'),'Target auto-mounted before boot overlay')
        run_phase([self.wim,'apply',overlay['wim'],'1',raw,'--check'],self.note,'Copying Windows boot files')
        meter=Meter(self.note,'Verifying copied Windows boot files',len(overlay['files']),'files verified')
        for number,(member,expected) in enumerate(overlay['files'].items(),1):
            require(hashlib.sha256(run([self.ntfscat,raw,'/'+member])).hexdigest()==expected,'Boot/marker readback failed: '+member)
            meter.update(number,member)
        phase(self.note,'Checking existing EFI before publishing the updated Windows menu')
        guard();require_efi_unchanged(tree_manifest(mount),before,'EFI changed before menu publication')
        tx.commit(after_each=lambda index: self.note(Message(
            'Publishing Windows menu: %d of %d files (%d%%)'%(index+1,len(tx.journal['files']),100*(index+1)//len(tx.journal['files'])),
            'Publishing Windows menu',100*(index+1)/len(tx.journal['files']))))
        phase(self.note,'Flushing completed writes to disk');run(['/bin/sync'])
        phase(self.note,'Saving deployment receipt and final EFI hashes')
        receipt.update(status='deployed-awaiting-first-boot',efi_after=tree_manifest(mount));write_receipt(receipt_path,receipt)
        self.note('DEPLOYMENT COMPLETE. The Windows menu was regenerated with this installation.')
        self.note('Restart when ready. OpenCore > Windows > '+entry_title+'. Complete normal Windows setup.')
        self.note('Other installations and OpenCore were preserved. No automatic restart or default-boot change.')

        from completion_guidance import message as completion_message
        self.interaction.request('notice',completion_message(edition['family'],entry_title,drivers=bool(self.driver_delivery)),button_title='Done')
