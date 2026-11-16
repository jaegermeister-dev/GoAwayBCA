"""Next private GUI workflow: Win10 alongside the recorded working Win7.

No standalone execution and no unattended destructive mode. The caller's
signed-off runtime profile must enable this workflow. Preparation/testing does
not invoke execute on hardware. Existing Win7 and OpenCore remain untouched
apart from replacing the two already-verified Windows *launch aliases* last.
"""
import hashlib
import os
from pathlib import Path
import platform
import shutil
import stat
import sys
from install_windows7 import (ROOT,GIB,require,run,run_logged,info,inventory,filesystem,
    partition_summary,require_sources_off_target,assert_same_target,gpt_for,match_gpt,
    tree_manifest,require_efi_unchanged,sha256,write_receipt,ensure_target_unmounted,ci_child)
from manage_installations import InstallationManager
from windows_images import inspect_editions
from windows10_boot import stage
from coexistence_plan import plan_first_system_stores,WIN10
from efi_transaction import EFITransaction

BOOT_ARCHITECTURE_VALIDATED = False


class CoexistenceInstaller(InstallationManager):
    def execute(self):
        require(BOOT_ARCHITECTURE_VALIDATED,
                'Windows 10 coexistence is locked: canonical BCD activation and recovery are still being validated. No device changes were started.')
        require(sys.platform=='darwin' and platform.machine()=='x86_64' and os.geteuid()==0
                and self.interaction is not None, 'Use the authorised Intel macOS GUI')
        model=run(['/usr/sbin/sysctl','-n','hw.model']).decode().strip()
        require(model=='MacBookPro11,5','This private hardware test is MacBookPro11,5 only')
        require(not self.args.prepared_media and not self.args.boot_manager,
                'Windows 10 uses its own ISO, not prepared Windows 7 boot files')
        iso=Path(self.args.iso).expanduser().resolve()
        require(iso.is_file() and iso.suffix.lower()=='.iso','Select a Windows ISO file')
        self.begin()
        self.note('Private Windows 10 coexistence build. Fresh physical boot acceptance is pending.')
        self.note('No partition creation/resizing, activation changes, automatic restart or default-boot changes.')
        media=self.mount_iso(iso)
        wim=ci_child(ci_child(media,'sources'),'install.wim')
        require(wim.is_file(),'This installation method requires sources/install.wim')
        xml=self.session/'editions.xml'
        run([self.wim,'info',wim,'--extract-xml='+str(xml)])
        editions=inspect_editions(xml.read_bytes())
        options=['%s [WIM index %d; %s]'%(e['name'],e['index'],e['family'] or e['refusal']) for e in editions]
        edition=editions[self.choose('Choose the Windows edition:',options)]
        require(edition['family']=='Windows 10','This coexistence test accepts Windows 10 x64 only')
        scan=self.scan()
        write_receipt(self.session/'installation-scan.json',scan)
        require(scan['scan_complete'],'Windows scan incomplete; existing installations cannot yet be preserved reliably')
        targets=inventory()
        require(targets,'No eligible existing internal GPT exFAT/NTFS partition')
        target=targets[self.choose('Choose the existing partition to REPLACE:',
                    [partition_summary(t['partition'],t['disk']) for t in targets])]
        p=target['partition'];device=p['DeviceIdentifier']
        require(p.get('DiskUUID','').lower()==WIN10,
                'This private coexistence test is limited to your designated 250 GB partition; Windows 7 is preserved')
        require(p['TotalSize']>=max(32*GIB,edition['bytes']+4*GIB),'Selected partition is too small')
        protected=[iso,ROOT,self.session]
        require_sources_off_target(protected,device)
        assert_same_target(target,info(device),info(target['disk']['DeviceIdentifier']))
        gpt=gpt_for(target);win,esp=match_gpt(target,gpt)
        mount=self.mount_esp(target['esp'],readonly=True)
        before=tree_manifest(mount)
        require(before==scan['installations'][0]['efi_manifest'],'EFI changed since discovery')
        backup=self.session/'efi-before';shutil.copytree(mount,backup)
        require(tree_manifest(backup)==before,'EFI backup verification failed')
        self.note('Existing EFI backup verified. Verifying the complete selected Windows image...')
        run_logged([self.wim,'verify',wim],self.note)
        self.smoke_tools()
        wim_sha=sha256(wim)
        overlay=stage(self.wim,wim,edition,self.session,gpt,win,esp,wim_sha,ROOT/'BCD10.template')
        chooser=(ROOT/'efi/Windows.efi').read_bytes()
        driver=(ROOT/'efi/OpenNtfsDxe.efi').read_bytes()
        saved_bcd=ci_child(ci_child(ci_child(backup,'EFI'),'Microsoft'),'Boot')
        canonical_win7=ci_child(saved_bcd,'BCD').read_bytes()
        plan=plan_first_system_stores(scan,overlay['record'],chooser,driver,model=model,
            canonical_win7=canonical_win7,canonical_win10=overlay['canonical_bcd'])
        require(shutil.disk_usage(mount).free>sum(len(c['data']) for c in plan['changes'])+8*1024**2,
                'Insufficient existing EFI free space')
        receipt=dict(status='preflight-only',iso=str(iso),wim_sha256=wim_sha,edition=edition,
            target=target,gpt=gpt,efi_before=before,record=overlay['record'],
            menu=plan['catalog'],store_activation=plan['store_activation'],
            preserved_efi=plan['preserved'],hardware_boot_acceptance_pending=True)
        path=self.session/'receipt.json';write_receipt(path,receipt)
        self.note('Windows 7 retains its working graphics helper and its own separate BCD.')
        self.note('Windows 10 receives its own loader and BCD; the menu activates each OS\'s separate system boot store when selected.')
        self.note('The existing active Windows 7 boot store stays unchanged during installation; no Windows entries are merged.')
        self.note('The shared Windows launch aliases will open the chooser; OpenCore will not be modified.')
        if self.args.dry_run:
            self.note('Preflight complete; no target or EFI writes. Physical installation is not validated by a dry run.')
            return
        current=info(device);assert_same_target(target,current,info(target['disk']['DeviceIdentifier']))
        summary=partition_summary(current,target['disk'],multiline=True)
        warning=('Double-check the label, capacity, free space and partition identifier. ALL files on this '
                 'selected partition will be erased and are NOT backed up by this tool. Selecting the wrong '
                 'partition causes data loss. The tool and its authors are not responsible for data loss '
                 'caused by selecting the wrong partition.')
        require(self.approve('format_ready','Ready to format as NTFS and install '+edition['name']+
                '.\n\n'+summary+'\n\nClick Format selected partition and install to begin two confirmations. '
                'This click does not erase anything.'),'Formatting was not requested')
        require(self.approve('review','Confirmation 1 of 2.\n\n'+summary+'\n\n'+warning),'First confirmation declined')
        require(self.confirm_erase('Confirmation 2 of 2.\n\n'+summary+'\n\n'+warning+
                '\n\nErase ONLY this partition and install '+edition['name']+'?','confirm'),'Final confirmation declined')
        mount=self.mount_esp(target['esp'],readonly=False)
        def check_efi():
            d=info(target['esp'])
            require(d.get('DiskUUID','').lower()==esp['guid'] and d.get('MountPoint')==str(mount)
                    and d.get('ReadOnlyVolume') is False,'EFI mount identity/access changed')
            require(gpt_for(target)['digest']==gpt['digest'],'GPT layout changed')
        check_efi()
        assert_same_target(target,info(device),info(target['disk']['DeviceIdentifier']))
        require_efi_unchanged(tree_manifest(mount),before,'Existing EFI changed during preflight')
        require(sha256(wim)==wim_sha and sha256(overlay['wim'])==overlay['wim_sha256'],'Installation source changed')
        require_sources_off_target(protected,device)
        tx=EFITransaction(mount,self.session/'efi-transaction',check_efi)
        tx.prepare(plan['changes'])  # Backups/staging ONLY, before erasing NTFS.
        ensure_target_unmounted(target,self.note)
        raw='/dev/'+device
        require(stat.S_ISBLK(os.stat(raw).st_mode),'Expected a partition block device')
        receipt['status']='format-started';receipt['confirmations']=2;write_receipt(path,receipt)
        self.formatted=True
        run_logged([self.mkntfs,'-Q','-s',str(gpt.get('sector_size',512)),'-p',str(win['first']),'-H','255','-S','63',
                    '-L','WINDOWS10',raw,str(win['last']-win['first']+1)],self.note)
        require(not info(device).get('MountPoint'),'Target auto-mounted; stopped before image application')
        check_efi()
        receipt['status']='image-apply-started';write_receipt(path,receipt)
        run_logged([self.wim,'apply',wim,str(edition['index']),raw,'--check','--strict-acls'],self.note)
        verified={}
        for member in ('/Windows/System32/winload.efi','/Windows/System32/config/SYSTEM',
                       '/Windows/System32/config/SOFTWARE'):
            data=run([self.ntfscat,raw,member])
            expected=run([self.wim,'extract',wim,str(edition['index']),member,'--to-stdout'])
            require(data==expected,'Installed system-file readback differs from selected WIM: '+member)
            verified[member]=hashlib.sha256(data).hexdigest()
        require(not info(device).get('MountPoint'),'Target auto-mounted before boot-file application')
        check_efi()
        run_logged([self.wim,'apply',overlay['wim'],'1',raw,'--check'],self.note)
        for member,expected in overlay['files'].items():
            require(hashlib.sha256(run([self.ntfscat,raw,'/'+member])).hexdigest()==expected,
                    'Independent boot-file/marker readback failed: '+member)
        for member,expected in verified.items():
            require(hashlib.sha256(run([self.ntfscat,raw,member])).hexdigest()==expected,
                    'Boot overlay altered an original Windows system file')
        receipt['status']='image-and-independent-boot-verified';write_receipt(path,receipt)
        check_efi();require_efi_unchanged(tree_manifest(mount),before,'EFI changed before publication')
        tx.commit()
        run(['/bin/sync'])
        receipt['status']='deployed-awaiting-first-boot';receipt['efi_after']=tree_manifest(mount)
        write_receipt(path,receipt)
        self.note('DEPLOYMENT COMPLETE. First physical Windows 10 boot remains unverified.')
        self.note('Restart when ready. Select Windows in OpenCore, then Windows 10 in the new list.')
        self.note('Windows 7 remains in that list with its existing graphics fix. No automatic restart or default change was made.')
