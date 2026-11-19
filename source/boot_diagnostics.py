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

def instructions(folder,title,esp_guid):
    path='\\'+folder.replace('/','\\')
    return ('GoAwayBCA Windows EFI diagnostics\n\nSelected Windows entry: '+title+
        '\nEFI partition GUID: '+esp_guid+'\nDiagnostic folder: '+path+'\n\n'
        '1. Restart into OpenShell. In OpenCore this is normally under Tools (press Space if tools are hidden). '
        'OpenShell must already be available in your boot environment; this feature does not install or change OpenCore.\n'
        '2. Enter: map -r\n3. Inspect the fsN: mappings to find the EFI partition containing the exact folder above. '
        'Do not assume fs0:. You can enter fs0:, then dir, and try another mapping as needed.\n'
        '4. On the correct fsN: mapping, enter: cd '+path+'\n5. Enter: WindowsDiagnostics.efi\n'
        '6. Press 1 to collect logs only, or 2 to collect logs and attempt this selected Windows installation.\n\n'
        'Option 1 only creates diagnostic files. Option 2 also performs the existing menu\'s normal per-Windows '
        'BCD selection before handing over to that installation. It does not apply a repair, add a menu entry, '
        'change OpenCore, change the default startup disk or repartition anything. Windows itself may perform '
        'its normal boot and setup writes. If its menu entry is missing or ambiguous, booting stops with a log.\n\n'
        'The tool records firmware revision, device/sector information, hardware reports and each boot stage. '
        'It flushes logs before handoff. If Windows hangs after control is handed over, the last flushed stage '
        'is available; this is not a Windows kernel debugger. Returned EFI errors are recorded.\n\n'
        '7. Return to macOS. In GoAwayBCA, use Advanced > Mount / Unmount EFI and select the EFI partition above. '
        'Copy this entire diagnostic folder, including boot-*.log and PCI/ACPI reports. Each run uses a new log. '
        'Review the files before attaching them to your GitHub issue. Nothing is uploaded automatically.\n')

class BootDiagnostics(InstallationManager):
    def execute(self):
        self.begin();phase(self.note,'Finding Windows partitions for targeted EFI diagnostics')
        scan=self.discover();rows=scan['installations']
        require(rows,'No readable Windows installations found. No diagnostic tool was written.')
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
        # Folder bytes deliberately match the embedded UUID's little-endian wire representation.
        session_id=session.bytes_le.hex();folder=PREFIX+session_id
        version=plistlib.loads((ROOT.parent/'Info.plist').read_bytes()).get('CFBundleShortVersionString','unknown')
        report=dict(schema=1,kind='targeted-windows-boot',session_id=session_id,app_version=version,
            model=model,target=selected['observed'],esp_guid=esp['guid'],sector_size=layout['sector_size'],
            current_device_names=dict(windows=target['partition']['DeviceIdentifier'],esp=target['esp']),
            menu_title=selected['title'],scan_failures=scan.get('scan_failures',[]),
            automatic_fixes=False,automatic_upload=False)
        import report_metadata
        report['host']=report_metadata.host(ROOT)
        report['boot_environment']=report_metadata.boot_environment(root,report_metadata.nvram_version())
        collectors={name:(ROOT/'diagnostic-efi'/name).read_bytes() for name in ('PCI.efi','ACPI.efi')}
        config=binding.configuration(str(session),layout['guid'],esp['guid'],win['guid'],layout['sector_size'],
            hashlib.sha256(collectors['PCI.efi']).hexdigest(),hashlib.sha256(collectors['ACPI.efi']).hexdigest(),model)
        program=binding.bind((ROOT/'diagnostic-efi/WindowsDiagnostics.template.efi').read_bytes(),config)
        context=('WinDeploy read-only hardware report v1\nsession_id='+session_id+'\nesp_guid='+esp['guid']+
                 '\nmacos_model='+model+'\nwindows_disk_guid='+layout['guid']+'\nwindows_partition_guid='+win['guid']+'\n').encode('ascii')
        guide=instructions(folder,selected['title'],esp['guid'])
        files=dict(collectors,**{'WindowsDiagnostics.efi':program,'context.txt':context,
            'ALLOW-ACPI.txt':b'WinDeploy readonly ACPI collection 20261008 v1\n',
            'ALLOW-PCI.txt':b'WinDeploy readonly PCI inventory 20261009 v1\n',
            'OPEN-SHELL-INSTRUCTIONS.txt':guide.encode('utf8'),
            'macos-report.json':(json.dumps(report,indent=2)+'\n').encode('utf8')})
        require(self.approve('review','Create an OpenShell diagnostic tool for “'+selected['title']+'”?\n\n'
            'This adds a new diagnostic folder to '+target['esp']+'. It does not fix or replace boot files. '
            'You run it yourself in OpenShell. Option 1 collects reports only; option 2 also uses normal Windows '
            'BCD selection to attempt this Windows boot. Logs are saved on EFI before handoff. No automatic upload.',
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
        self.interaction.request('notice','The targeted EFI diagnostic tool is ready.\n\n'+guide,button_title='Done')
        self.interaction.request('open_folder','OpenShell instructions saved on your Desktop.',path=saved['path']+'/Files')
