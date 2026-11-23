"""Prepare a target-bound OpenShell collector without changing boot settings."""
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import uuid

import boot_diagnostic_binding as binding
from manage_installations import InstallationManager
from install_windows7 import (ROOT,require,info,run,gpt_for,match_gpt,tree_manifest,
                              require_efi_unchanged,write_receipt,assert_same_target)
from efi_transaction import EFITransaction
from user_driver_store import Store
from operation_progress import phase

PREFIX='EFI/WinDeploy/Diagnostics/'

VARIANTS = (
    ('Test1.efi', 'WindowsDiagnostics.template.efi', 'Direct Windows loader from memory'),
    ('Test2.efi', 'WindowsDiagnostics-path.template.efi', 'Direct Windows loader through firmware file path'),
    ('Test3.efi', 'WindowsDiagnostics-esp.template.efi', 'Direct Windows loader with experimental EFI-partition context'),
)

def bound_tests(resources, config):
    return {name: binding.bind((resources/'diagnostic-efi'/template).read_bytes(), config)
            for name, template, description in VARIANTS}

def instructions(folder,title,esp_guid):
    path='\\'+folder.replace('/','\\')
    return ('GoAwayBCA Windows EFI boot comparisons\n\nSelected Windows entry: '+title+
        '\nEFI partition GUID: '+esp_guid+'\nDiagnostic folder: '+path+'\n\n'
        '1. Restart into OpenShell. This feature does not install or change OpenCore.\n'
        '2. Enter: map -r\n'
        '3. Find the fsN: mapping containing the exact folder above. Do not assume fs0:.\n'
        '4. On that mapping enter: cd '+path+'\n'
        '5. Run Test1.efi. It immediately collects reports and tries the selected Windows installation.\n'
        '6. Restart before the next test, return to the same folder, then run Test2.efi. Repeat for Test3.efi. '
        'Do not run the tests one after another without restarting, even if a failed test returns to OpenShell.\n\n'
        'Test1.efi: verified original Windows loader from memory.\n'
        'Test2.efi: load that verified original Windows loader through its firmware file path.\n'
        'Test3.efi: use the EFI partition as the original Windows loader\'s boot context. This experimental comparison may fail even when Test1 and Test2 work.\n\n'
        'These are comparisons, not automatic fixes. Each confirms the selected disk and partition, collects '
        'PCI/ACPI reports, and saves a numbered test1-*, test2-* or test3-* log before handing over to Windows. '
        'If a test hangs, photograph the screen and report which test it was. The last saved stage remains useful; '
        'this is not a Windows kernel debugger. Returned EFI status and any exit text are logged.\n\n'
        'All three explicitly bypass the recognised experimental Fix.efi helper and verify its saved Original.efi against Entry.bin before loading it. A direct bootmgfw.efi entry stays direct. The log identifies the configured entry, actual executable and its SHA256. Missing or changed original files stop the test; it never falls back to running the helper. All three use the normal Windows menu per-Windows BCD selection. They do not replace the boot menu, '
        'patch Windows, alter OpenCore, change the default startup disk or repartition. Windows itself may make '
        'normal boot/setup writes. A missing or ambiguous menu entry stops that attempt.\n\n'
        '7. Return to macOS and mount the indicated EFI partition. ZIP this entire Test folder, including '
        'all logs and capture folders. Include the outcome of each test and whether OpenShell was started '
        'through OpenCore or another route. Review reports before sharing; nothing uploads automatically.\n\n'
        'Creating this set again uses a new numbered folder. Existing tools and logs are preserved.\n')

class BootDiagnostics(InstallationManager):
    def execute(self):
        self.begin();phase(self.note,'Finding Windows partitions for targeted EFI diagnostics')
        scan=self.discover(verify_filesystem=True);rows=scan['installations']
        scan_path=self.session/'diagnostic-scan.json'
        write_receipt(scan_path,scan)
        for failure in scan.get('scan_failures',[]):
            self.note('Could not inspect '+failure['partition']+': '+failure['error'])
        self.note('EFI diagnostic scan: %d Windows installation(s) found; %d partition(s) could not be inspected.' %
            (len(rows),len(scan.get('scan_failures',[]))))
        self.note('Diagnostic scan report: '+str(scan_path))
        require(rows,('Windows partitions could not be read. Check the scan errors in the log and include the saved diagnostic scan report when reporting the problem.'
            if scan.get('scan_failures') else
            'No readable Windows installations found. Check that the Windows drive is connected and accessible. If Windows was just installed, include the saved diagnostic scan report when reporting the problem.')+
            '\nNo diagnostic tool was written.\nScan report: '+str(scan_path))
        selected=rows[self.choose('Which Windows installation fails to boot? The diagnostic EFI will target '
            'this partition by its persistent identifier, including if its current disk number changes.',
            [r['title']+'\n'+r['summary'] for r in rows])]
        target=selected['target'];layout=gpt_for(target);win,esp=match_gpt(target,layout)
        require(sum(r['observed']['partition_guid']==win['guid'] for r in rows)==1,
                'The Windows partition identity is ambiguous. Resolve duplicate identifiers first.')
        model=run(['/usr/sbin/sysctl','-n','hw.model']).decode().strip()
        if self.esp_owned and self.esp_device!=target['esp']:
            require(not self.close_esp(),'Could not close the previous diagnostic scan mount.')
        root=self.mount_esp(target['esp'],readonly=True);before=tree_manifest(root)
        session=uuid.uuid4()
        session_id=session.bytes_le.hex()
        diagnostic_root=root/PREFIX
        existing=sorted(p.name for p in diagnostic_root.iterdir()) if diagnostic_root.exists() else []
        folder_name=binding.next_folder(existing);folder=PREFIX+folder_name
        version=plistlib.loads((ROOT.parent/'Info.plist').read_bytes()).get('CFBundleShortVersionString','unknown')
        report=dict(schema=1,kind='targeted-windows-boot',session_id=session_id,app_version=version,
            folder_name=folder_name,model=model,target=selected['observed'],esp_guid=esp['guid'],sector_size=layout['sector_size'],
            current_device_names=dict(windows=target['partition']['DeviceIdentifier'],esp=target['esp']),
            menu_title=selected['title'],scan_failures=scan.get('scan_failures',[]),
            automatic_fixes=False,automatic_upload=False)
        import report_metadata
        report['host']=report_metadata.host(ROOT)
        report['boot_environment']=report_metadata.boot_environment(root,report_metadata.nvram_version())
        collectors={name:(ROOT/'diagnostic-efi'/name).read_bytes() for name in ('PCI.efi','ACPI.efi')}
        config=binding.configuration(str(session),layout['guid'],esp['guid'],win['guid'],layout['sector_size'],
            hashlib.sha256(collectors['PCI.efi']).hexdigest(),hashlib.sha256(collectors['ACPI.efi']).hexdigest(),model,folder_name=folder_name)
        programs=bound_tests(ROOT,config)
        report['tests']=[dict(file=name,method=description) for name,template,description in VARIANTS]
        context=('WinDeploy read-only hardware report v1\nsession_id='+session_id+'\nesp_guid='+esp['guid']+
                 '\nfolder_name='+folder_name+'\nmacos_model='+model+'\nwindows_disk_guid='+layout['guid']+'\nwindows_partition_guid='+win['guid']+'\n').encode('ascii')
        guide=instructions(folder,selected['title'],esp['guid'])
        files=dict(collectors,**dict(programs,**{'context.txt':context,
            'ALLOW-ACPI.txt':b'WinDeploy readonly ACPI collection 20261008 v1\n',
            'ALLOW-PCI.txt':b'WinDeploy readonly PCI inventory 20261009 v1\n',
            'OPEN-SHELL-INSTRUCTIONS.txt':guide.encode('utf8'),
            'macos-report.json':(json.dumps(report,indent=2)+'\n').encode('utf8')}))
        require(self.approve('review','Create three OpenShell boot tests for “'+selected['title']+'”?\n\n'
            'New folder: '+folder_name+' on '+target['esp']+'.\n'
            'Existing diagnostic folders and logs will be kept. Creating a new tool does not update or remove earlier tools; use this new folder for the latest test.\n\n'
            'It does not fix or replace boot files. '
            'The tests bypass the recognised experimental EFI helper and start the verified original Windows loader. Test3 changes the boot context and may fail even when the other two work. Run Test1.efi, Test2.efi and Test3.efi separately in OpenShell, restarting between tests. Each collects reports and '
            'uses normal Windows BCD selection to attempt this Windows boot. Logs are saved on EFI before handoff. No automatic upload.',
            details=guide),'Diagnostic preparation cancelled.')
        assert_same_target(target,info(target['partition']['DeviceIdentifier']),info(target['disk']['DeviceIdentifier']))
        require(gpt_for(target)['digest']==layout['digest'],'The selected drive layout changed. Start again.')
        root=self.mount_esp(target['esp'],readonly=False)
        def guard():
            current=info(target['esp'])
            require(current.get('DiskUUID','').lower()==esp['guid'] and current.get('MountPoint')==str(root)
                    and current.get('ReadOnlyVolume') is False,'The diagnostic EFI mount changed.')
            require(gpt_for(target)['digest']==layout['digest'],'The diagnostic target layout changed.')
        guard();require_efi_unchanged(tree_manifest(root),before,'EFI changed during diagnostic review.')
        require(not (root/folder).exists(),'The diagnostic session folder already exists.')
        require(shutil.disk_usage(root).free>sum(map(len,files.values()))+32*1024**2,
                'EFI needs more free space for the tool and logs. No files will be deleted automatically.')
        tx=EFITransaction(root,self.session/'diagnostic-transaction',guard)
        tx.prepare([dict(path=folder+'/'+name,expected=None,data=data) for name,data in files.items()]);tx.commit()
        run(['/bin/sync'])
        output=self.session/'diagnostic-export';output.mkdir()
        for name in ('OPEN-SHELL-INSTRUCTIONS.txt','macos-report.json'):(output/name).write_bytes(files[name])
        write_receipt(self.session/'diagnostic-receipt.json',dict(report,folder=folder,
            files={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}))
        saved=Store(self.interaction.user_uid).report(output,self.note)
        self.note('Diagnostic EFI ready at '+folder+'. Instructions saved to '+saved['path']+'/Files.')
        self.interaction.request('notice','The three targeted EFI boot tests are ready.\n\n'+guide,button_title='Done')
        self.interaction.request('open_folder','OpenShell instructions saved on your Desktop.',path=saved['path']+'/Files')
