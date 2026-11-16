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
    tree_manifest,require_efi_unchanged,sha256,write_receipt,ensure_target_unmounted,ci_child,media_size)
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
            'Partitions that already contain Windows are not listed. This standalone build '
            'requires an empty exFAT or NTFS partition.')

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

from install_multiboot import MultiBootInstaller
import firmware_compatibility
import mac_compatibility
import cpu_compatibility
from backtoseven_policy import confirm_model, validate_model, validate_edition, configure_drivers
from backtoseven_boot import publication, cleanup

class BackToSevenInstaller(MultiBootInstaller):
    def standalone_files(self,model,efi_enabled):
        stage=self.session/'staged-efi'
        # The accepted 11,5 stage already contains a direct helper alias plus
        # original loader. The helper reads this ESP's canonical, personalised BCD.
        if model=='MacBookPro11,4' and efi_enabled:
            for directory,loader in ((stage/'Microsoft/Boot','bootmgfw.efi'),
                                     (stage/'Boot','BOOTX64.EFI')):
                additions=experimental_efi.files(ROOT,(directory/loader).read_bytes())
                for name,data in additions.items():(directory/name).write_bytes(data)
                (directory/loader).write_bytes(additions['Fix.efi'])
        if self.accepted_gpu:
            self.accepted_gpu['target_binding']['selector_activates_independent_bcd']=False
            self.accepted_gpu['target_binding']['standalone_canonical_bcd']=True
        return {'EFI/'+p.relative_to(stage).as_posix():p.read_bytes()
                for p in stage.rglob('*') if p.is_file()}

    def finish_overlay(self,overlay):
        root=self.session/'ntfs-overlay'
        (root/'WinDeploy/installation.json').write_bytes(canonical(make_marker(overlay['record']))+b'\n')
        (root/'WinDeploy/BackToSeven-installation.json').write_bytes(canonical(overlay['record'])+b'\n')
        # Optional connection tools are never run during
        # deployment or normal OOBE. Remote access needs the owner's UAC approval.
        kit=ROOT/'private-test-tools'
        require(kit.is_dir() and (kit/'Setup-SSH.cmd').is_file(),'Windows 7 installation is not supported in GoAwayBCA Beta 1.')
        shutil.copytree(kit,root/'BackToSeven-Test-Tools')
        import xml.etree.ElementTree as ET
        binding=ET.Element('BackToSeven')
        record=overlay['record']
        for key,value in [('Model',record['confirmed_model']),('DiskGuid',record['disk_guid']),('WindowsGuid',record['partition_guid']),('EspGuid',record['esp_guid'])]:
            ET.SubElement(binding,key).text=value
        if record.get('sector_compatibility'):
            route=record['sector_compatibility']
            for key,value in [('SectorLoader',route['loader_path']),('SectorHelperSha256',route['helper_sha256']),
                              ('SectorNextSha256',route['next_sha256'])]:
                ET.SubElement(binding,key).text=value
        (root/'BackToSeven').mkdir(exist_ok=True)
        # ElementTree.tostring(xml_declaration=...) requires Python 3.8.
        # ElementTree.write supports the same output on both bundled runtimes.
        ET.ElementTree(binding).write(str(root/'BackToSeven/Boot-Binding.xml'),
                                      encoding='utf-8',xml_declaration=True)
        wim=self.session/'backtoseven-overlay.wim'
        run([self.wim,'capture',root,wim,'BackToSeven installation records','--compress=none','--check'])
        overlay.update(wim=wim,wim_sha256=sha256(wim),files=tree_manifest(root))

    def execute(self):
        require(platform.system()=='Darwin' and platform.machine()=='x86_64' and os.geteuid()==0
                and self.interaction is not None,'Use the authorised Intel macOS app')
        selection=self.__dict__.pop('_unified_selection',None)
        if selection is None:
            detected=run(['/usr/sbin/sysctl','-n','hw.model']).decode().strip()
            model=confirm_model(self.interaction,detected)
            iso=Path(self.args.iso).expanduser().resolve()
            require(iso.is_file() and iso.suffix.lower()=='.iso','Select a Windows ISO')
            self.begin()
            self.note('BackToSeven: Windows 7 on '+model+'.')
            self.note('No partition resizing, default-boot changes, automatic restart or automatic driver installation.')
            phase(self.note,'Checking for existing Windows before installation preparation')
            scan=self.scan();write_receipt(self.session/'initial-installation-scan.json',scan)
        else:
            detected,model,iso,wim,edition=(selection[k] for k in
                ('detected_model','model','iso','wim','edition'))
            validate_model(model);validate_edition(edition)
            firmware_evidence,model_guidance,cpu_guidance=(selection[k] for k in
                ('firmware_evidence','model_guidance','cpu_guidance'))
            scan=selection['initial_scan']
            require(scan is not None and scan['scan_complete'],'Missing completed initial Windows scan')
            self.note('Using the BackToSeven Windows 7 component for '+model+'.')
        self.confirmed_model=model
        from backtoseven_experimental_policy import installation_choice
        self.experimental_menu=True if getattr(self,'unified_menu',False) else installation_choice(self.interaction,scan)
        if selection is None:
            phase(self.note,'Opening Windows ISO and reading its editions')
            media=self.mount_iso(iso);wim=ci_child(ci_child(media,'sources'),'install.wim')
            require(wim.is_file(),'This build requires sources/install.wim in the ISO')
            xml=self.session/'editions.xml';run([self.wim,'info',wim,'--extract-xml='+str(xml)])
            editions=inspect_editions(xml.read_bytes())
            edition=editions[self.choose('Choose the Windows edition:',
                ['%d. %s [%s; WIM index %d]'%(i+1,e['name'],e['family'] or e['refusal'],e['index']) for i,e in enumerate(editions)])]
            validate_edition(edition)
            firmware_evidence=firmware_compatibility.configure(edition,self.interaction)
            model_guidance=mac_compatibility.configure(model,edition,self.interaction)
            cpu_guidance=cpu_compatibility.configure(edition,self.interaction)
        import install_multiboot
        entry_title=install_multiboot.prompt_entry_name(self.interaction,edition['family'])
        require(eligible_targets(inventory(),scan),
            'No eligible empty exFAT/NTFS partition. Prepare an empty destination before trying again.')
        efi_enabled=choose_efi(self.interaction,model,edition['family'],self.note)
        driver_choice=configure_drivers(self.interaction,model)
        self.driver_delivery=driver_preparation.prepare(driver_choice,ROOT,self.session,self.wim,self.interaction,self.note)
        if edition['family']=='Windows 7':
            phase(self.note,'Verifying and preparing the private Windows 7 source')
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
        require(targets,'No eligible empty exFAT/NTFS partition. Prepare an empty destination before trying again.')
        target=targets[self.choose(destination_prompt(edition['family']),
            [partition_summary(t['partition'],t['disk']) for t in targets])]
        from disk_layout import require_windows_sector_support
        import win7_sector
        self.prepared_4kn=win7_sector.prepare_target(self,target,edition,wim,ROOT,model)
        require_windows_sector_support(target,edition['family'],self.prepared_4kn)
        require(self.prepared_4kn is None or self.experimental_menu,
                'Windows 7 4Kn compatibility currently requires the unified Windows boot menu.')
        from installation_destination import check as check_destination
        check_destination(target,Path(self.ntfscat).with_name('ntfsls'))
        p=target['partition'];device=p['DeviceIdentifier']
        driver_bytes=self.driver_delivery['bytes'] if self.driver_delivery else 0
        require(p['TotalSize']>=max(32*GIB,edition['bytes']+driver_bytes+4*GIB+64*1024**2),'Partition too small for selected edition and drivers')
        protected=[iso,ROOT,self.session,wim]
        require_sources_off_target(protected,device)
        assert_same_target(target,info(device),info(target['disk']['DeviceIdentifier']))
        gpt=gpt_for(target);win,esp=match_gpt(target,gpt)
        if self.esp_owned and self.esp_device!=target['esp']:require(not self.close_esp(),'Could not detach scan ESP')
        phase(self.note,'Backing up and verifying existing EFI files')
        mount=self.mount_esp(target['esp'],readonly=True);before=tree_manifest(mount,self.note,'Reading current EFI file hashes')
        backup=self.session/'efi-before';shutil.copytree(mount,backup)
        require(tree_manifest(backup,self.note,'Verifying EFI backup files')==before,'EFI backup verification failed')
        recovered=dict(changes=[],evidence=None)
        if not self.experimental_menu and not scan['installations']:
            from standalone_ownership import recover_completed_format
            from menu_lifecycle import snapshot
            recovered=recover_completed_format(dict(disk_guid=gpt['guid'],esp_guid=esp['guid'],
                partition_guid=win['guid'],partition_bytes=media_size(p),partition_offset=win['first']*gpt.get('sector_size',512)),
                before,snapshot(backup,before))
            if recovered['evidence']:
                self.note('Verified boot files retained by a completed uninstall. They will be replaced during this installation.')
                write_receipt(self.session/'recovered-uninstall-evidence.json',recovered)
        cleanup_changes=[] if self.experimental_menu else cleanup(backup,before,
            dict(disk_guid=gpt['guid'],esp_guid=esp['guid']),scan['installations'],recovered['changes'])
        for change in cleanup_changes:
            self.note('A verified obsolete Windows boot file is backed up and scheduled for cleanup.')
        if cleanup_changes:
            self.note('Cleanup is part of the confirmed installation transaction. OpenCore and Apple boot files stay intact.')

        from optional_verification import verify_wim
        image_verification=verify_wim(self.wim,wim,self.note,self.interaction)
        phase(self.note,'Testing bundled NTFS helpers on disposable files')
        self.smoke_tools();wim_sha=hash_file(wim,self.note,'Hashing selected Windows image')
        phase(self.note,'Preparing Windows 7 boot files')
        overlay=self.prepare_win7(wim,edition,gpt,win,esp,wim_sha,model,efi_enabled)
        overlay['entry']['title']=entry_title
        overlay['record']['confirmed_model']=model
        overlay['record']['detected_model']=detected
        from backtoseven_menu_actions import assemble, read_efi, save_backup
        all_files=read_efi(backup,before)
        removed=[]
        if self.experimental_menu:
            from backtoseven_menu import previous
            import menu_presence
            old=previous(all_files,gpt['guid'],esp['guid'])
            removed=menu_presence.collect(self,old['catalog']['entries'],scan['installations'],target)
            plan=assemble(self,backup,all_files,gpt['guid'],esp['guid'],scan['installations'],addition=overlay,
                preserve_addition_paths=self.prepared_4kn is not None,confirmed_absent=removed)
            dependencies=boot_dependencies.plan(before,overlay['esp_files'],
                (a['path'] for a in overlay['record']['efi_assets']))
            already={c['path'].casefold() for c in plan['changes']}
            plan['changes'][:0]=[c for c in dependencies['changes'] if c['path'].casefold() not in already]
            overlay['record'].update(product='BackToSeven',product_version='0.1.0-test.4',
                boot_layout='experimental-independent-stores-v1')
            ledger='EFI/WinDeploy/Records/'+win['guid']+'.json'
            prior=lifecycle.indexed(before).get(ledger.casefold(),(ledger,None))
            if prior[1] is not None:
                require(gpt['guid']+':'+win['guid'] in {identity(p) for p in removed},'Existing ownership record needs review')
                stale=json.loads((backup/prior[0]).read_text())
                require(identity(stale)==gpt['guid']+':'+win['guid'],'Existing ownership record belongs to another partition')
            plan['changes'].append(dict(path=prior[0],expected=prior[1],data=canonical(overlay['record'])+b'\n'))
        else:
            standalone=self.standalone_files(model,efi_enabled)
            # Keep a separately named, per-install store for later experimental
            # recovery if a different Windows installer replaces canonical BCD.
            standalone['EFI/BackToSeven/Children/'+win['guid']+'/BCD']=overlay['canonical_bcd']
            plan=publication(before,standalone,overlay['record'],scan['installations'],cleanup_changes)
        # Refresh ledger after adding the identity evidence, and bind the paired
        # NTFS marker to exactly that final ledger including product/layout.
        plan['changes'][-1]['data']=canonical(overlay['record'])+b'\n'
        self.finish_overlay(overlay)
        for kept in plan['preserved']:
            self.note('Preserving existing EFI file: '+kept)
        require(shutil.disk_usage(mount).free>sum(len(c['data'] or b'') for c in plan['changes'])+8*1024**2,
                'Insufficient EFI free space; no target formatted')
        receipt=dict(status='preflight-only',edition=edition,target=target,record=overlay['record'],
            boot_layout=overlay['record']['boot_layout'],efi_before=before,efi_fix_enabled=efi_enabled,image_verification=image_verification,
            windows7_fixes=edition['family']=='Windows 7' and model=='MacBookPro11,5',
            menu_title=entry_title,driver_choice=driver_choice,driver_preparation=self.driver_delivery['receipt'] if self.driver_delivery else None,
            preserved_boot_dependencies=plan['preserved'],legacy_boot_cleanup=plan['stale_files'],externally_removed_entries=removed,
            firmware_compatibility=firmware_evidence,
            mac_compatibility=model_guidance,cpu_compatibility=cpu_guidance,
            accepted_gpu_staging=self.accepted_gpu,product='BackToSeven',confirmed_model=model,detected_model=detected)
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
        require(any(t['partition']['DeviceIdentifier']==device for t in eligible_targets(inventory(),fresh)),
                'Selected target now contains Windows or is no longer eligible')
        if self.experimental_menu:
            from backtoseven_menu_actions import verify_sources
            require(fresh['installations']==scan['installations'],'Existing Windows installations changed; start again')
            menu_presence.recheck(self,old['catalog']['entries'],fresh['installations'],target,removed)
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
        backup_links,boot_snapshot=save_backup(mount,all_files,gpt['guid'],esp['guid'],plan['changes'],
            kind='pre-install',windows=win['guid'])
        plan['changes'].extend(backup_links)
        receipt['boot_snapshot']=boot_snapshot
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
        receipt['driver_verification']=driver_preparation.apply(self.driver_delivery,self.wim,self.ntfscat,raw,driver_guard,self.note,
                                                               interaction=self.interaction)
        write_receipt(receipt_path,receipt)
        guard();require(not info(device).get('MountPoint'),'Target auto-mounted before boot overlay')
        run_phase([self.wim,'apply',overlay['wim'],'1',raw,'--check'],self.note,'Copying Windows boot files')
        meter=Meter(self.note,'Verifying copied Windows boot files',len(overlay['files']),'files verified')
        for number,(member,expected) in enumerate(overlay['files'].items(),1):
            require(hashlib.sha256(run([self.ntfscat,raw,'/'+member])).hexdigest()==expected,'Boot/marker readback failed: '+member)
            meter.update(number,member)
        phase(self.note,'Checking existing EFI before publishing Windows 7 boot files')
        guard();require_efi_unchanged(tree_manifest(mount),before,'EFI changed before menu publication')
        tx.commit(after_each=lambda index: self.note(Message(
            'Publishing Windows 7 boot files: %d of %d files (%d%%)'%(index+1,len(tx.journal['files']),100*(index+1)//len(tx.journal['files'])),
            'Publishing Windows 7 boot files',100*(index+1)/len(tx.journal['files']))))
        phase(self.note,'Flushing completed writes to disk');run(['/bin/sync'])
        phase(self.note,'Saving deployment receipt and final EFI hashes')
        receipt.update(status='deployed-awaiting-first-boot',efi_after=tree_manifest(mount));write_receipt(receipt_path,receipt)
        self.note('Windows 7 deployment is complete.')
        self.note('Restart when ready. Select Windows in OpenCore and complete normal Windows setup.')
        self.note('Run C:/BackToSevenSupport/BackToSeven Drivers Installer.exe.')
        self.note('Other installations and OpenCore were preserved. No automatic restart or default-boot change.')
        from completion_guidance import message as completion_message
        self.interaction.request('notice',completion_message(edition['family'],entry_title,community=True,drivers=bool(self.driver_delivery)),button_title='Done')
