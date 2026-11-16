"""User-started hardware reports. All hardware reads are passive.

Only explicitly confirmed collector staging writes to EFI, into a fresh session
directory. No boot paths/defaults are changed and no patch is applied.
"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import uuid
from manage_installations import InstallationManager
from install_windows7 import ROOT,Refusal,require,info,run,gpt_for,match_gpt,tree_manifest,write_receipt
from ntfs_read import Probe
from user_driver_store import Store
from operation_progress import phase,Meter
from efi_transaction import EFITransaction
import hardware_reports as reports
import menu_lifecycle

def capture(argv,plist=False,limit=16*1024**2):
    try:
        result=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
        require(result.returncode==0,'Command unavailable or failed')
        require(len(result.stdout)<=limit,'Hardware output exceeds report limit')
        value=plistlib.loads(result.stdout) if plist else result.stdout.decode('utf-8','replace').strip()
        return dict(status='observed',data=reports.clean(value))
    except (OSError,ValueError,Refusal,subprocess.SubprocessError) as error:
        return dict(status='unavailable',reason=str(error)[:300])

def software_evidence(data,session):
    """Open a private COPY read-only; export only non-personal version values."""
    from offline_keyboard import Hive
    temporary=Path(session)/('software-version-'+uuid.uuid4().hex)
    with temporary.open('xb') as out:out.write(data)
    hive=None
    try:
        hive=Hive(ROOT/'lib/libhivex.0.dylib',temporary,False)
        result=dict(status='observed',source='Installed SOFTWARE / Microsoft / Windows NT / CurrentVersion')
        for name in ('ProductName','EditionID','InstallationType','CurrentVersion','CurrentBuildNumber','DisplayVersion','ReleaseId','UBR'):
            try:
                kind,raw=hive.value(('Microsoft','Windows NT','CurrentVersion'),name)
                if kind in (1,2):
                    value=raw.decode('utf-16le').rstrip('\0')
                    require(len(value)<256 and not any(ord(c)<32 for c in value),'Invalid version value')
                elif kind==4 and len(raw)==4:value=int.from_bytes(raw,'little')
                else:continue
                result[name]=value
            except (ValueError,Refusal,UnicodeError):pass
        return result
    finally:
        if hive is not None:hive.close()
        temporary.unlink()  # Only this fresh private copy; never the Windows hive.

def hardware(note):
    output={}
    commands=[('cpu',['/usr/sbin/sysctl','machdep.cpu.brand_string','machdep.cpu.features','machdep.cpu.leaf7_features','hw.ncpu','hw.memsize'],False),
        ('macos',['/usr/bin/sw_vers'],False),
        ('firmware',['/usr/sbin/ioreg','-p','IODeviceTree','-n','efi','-d','1','-a'],True),
        ('power_configuration',['/usr/bin/pmset','-g','custom'],False),
        ('battery_status',['/usr/bin/pmset','-g','batt'],False)]
    # Individual categories have independent limits/failures. No activation of
    # camera, microphone, radios or fans; no network probes or throughput tests.
    for category in ('SPHardwareDataType','SPDisplaysDataType','SPPCIDataType','SPUSBDataType',
            'SPThunderboltDataType','SPAudioDataType','SPCameraDataType','SPCardReaderDataType',
            'SPBluetoothDataType','SPAirPortDataType','SPEthernetDataType','SPSerialATADataType','SPNVMeDataType','SPPowerDataType'):
        commands.append((category,['/usr/sbin/system_profiler','-xml','-detailLevel','mini','-timeout','25',category],True))
    commands.append(('HID_devices',['/usr/sbin/ioreg','-r','-c','IOHIDDevice','-a'],True))
    meter=Meter(note,'Collecting read-only hardware inventory',len(commands),'sections checked')
    for index,(key,argv,plist) in enumerate(commands,1):
        output[key]=capture(argv,plist);meter.update(index,key)
    output['limitations']='macOS observations describe this boot, not Windows driver bindings. Firmware spoofing may affect identity.'
    return output

def collector_files(report,resources):
    context=reports.context_bytes(report);session=report['session_id']
    folder=reports.PREFIX+session+'/'
    result={folder+'context.txt':context,
        folder+'macos-report.json':(json.dumps(report,indent=2)+'\n').encode('utf-8'),
        folder+'ALLOW-ACPI.txt':b'WinDeploy readonly ACPI collection 20261008 v1\n',
        folder+'ALLOW-PCI.txt':b'WinDeploy readonly PCI inventory 20261009 v1\n'}
    for name in ('ACPI.efi','PCI.efi'):
        path=Path(resources)/'diagnostic-efi'/name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size<2*1024**2,'Missing verified diagnostic collector')
        result[folder+name]=path.read_bytes()
    prefix='\\'+folder.replace('/','\\')
    result[folder+'Collect.nsh']=('@echo -off\r\necho WinDeploy read-only hardware collection. No patches or reboot.\r\n'+
        prefix+'ACPI.efi\r\n'+prefix+'PCI.efi\r\necho Collection commands finished. Check both completion messages.\r\n'+
        'echo Type exit to return to OpenCore, then boot normally.\r\n').encode('ascii')
    return result

def bounded_read(path,limit):
    require(not any(p.is_symlink() for p in (path,)+tuple(path.parents)) and path.is_file()
        and path.stat().st_size<=limit,'Unsafe or oversized report file: '+path.name)
    with path.open('rb') as source:data=source.read(limit+1)
    require(len(data)<=limit,'Report grew beyond its limit')
    return data

def windows_collector(output,report,resources):
    """Export for the user to run manually in Windows; never execute it here."""
    folder=output/'Optional-Windows-Collector';folder.mkdir()
    for name in ('Collect-Windows-Hardware.ps1','Collect-Windows-Hardware.cmd'):
        data=bounded_read(Path(resources)/'windows-report'/name,64*1024)
        (folder/name).write_bytes(data)
    (folder/'context.txt').write_bytes(reports.context_bytes(report))
    (folder/'READ-ME.txt').write_text('Optional: copy this entire folder to the affected Windows installation, then double-click '
        'Collect-Windows-Hardware.cmd. It uses Windows built-in PowerShell; no installation or elevation is requested. '
        'Some restricted evidence may be unavailable. Read-only inventory is saved to a NEW Desktop folder. '
        'The inherited target context is not automatically verified as the running Windows partition. Include your actual boot route '
        'and review the output before sharing. Device IDs/events may identify peripherals. No automatic upload or hardware fixes.\n')

def inspect_captures(folder,report):
    """Checks report/session and content hashes; does not infer unknown fixes."""
    require(bounded_read(folder/'context.txt',2048)==reports.context_bytes(report),'EFI context no longer matches this report')
    results=[]
    for directory in sorted(folder.iterdir()):
        if re.fullmatch('capture-[0-9]{3}',directory.name.casefold()) and directory.is_dir() and not directory.is_symlink():
            inventory=directory/'inventory.txt'
            text=bounded_read(inventory,1024**2).decode('utf-8','replace')
            require('session_id='+report['session_id']+'\n' in text,'ACPI capture belongs to another session')
            checked=0
            for line in text.splitlines():
                if not line.startswith('saved\t'):continue
                _,name,size,digest=line.split('\t')
                require(re.fullmatch('[A-Za-z0-9_.-]{1,96}',name) and name not in ('.','..'),'Unsafe ACPI file name')
                path=directory/name
                data=bounded_read(path,16*1024**2)
                require(len(data)==int(size),'ACPI file length differs')
                require(hashlib.sha256(data).hexdigest()==digest,'ACPI file hash differs: '+name)
                checked+=1
            results.append(dict(capture=directory.name,kind='ACPI',files_verified=checked,
                complete=bool(checked and any(line.startswith('COMPLETE\t') for line in text.splitlines()))))
        elif re.fullmatch('pci-[0-9]{3}\.txt',directory.name.casefold()):
            text=bounded_read(directory,1024**2).decode('utf-8','replace')
            require('session_id='+report['session_id']+'\n' in text,'PCI capture belongs to another session')
            result=re.search(r'END handles=(\d+) read=(\d+) failed=(\d+)\.',text)
            results.append(dict(capture=directory.name,kind='PCI',complete=bool(result and result[1]==result[2] and result[3]=='0')))
    return dict(captures=results,both_collectors_complete=all(any(r['kind']==kind and r['complete'] for r in results) for kind in ('ACPI','PCI')),
        new_fix_generated=False,limitations='Completion is collector coverage, not proof that hardware works.')

class Diagnostics(InstallationManager):
    def execute(self):
        self.begin();phase(self.note,'Identifying WinDeploy Windows installations')
        scan=self.discover();rows=[r for r in scan['installations'] if reports.eligible(r)]
        require(rows,'No present Windows installations with matching WinDeploy creation records were found. Nothing adopted or changed.')
        selected=rows[self.choose('Collect hardware report — which WinDeploy Windows installation has a problem? '
            'Other or unverified installations are not included.',[r['title']+'\n'+r['summary'] for r in rows])]
        from boot_catalog import identity
        require(sum(identity(r['observed'])==identity(selected['observed']) for r in scan['installations'])==1,
            'Duplicate Windows disk/partition identity found. Resolve the ambiguity before targeted diagnostics.')
        action=self.choose('Reports are for manual diagnosis, not automatic repair. They include hardware, Windows version '
            'and the GUIDs needed to target this installation. Review them before sharing; nothing is uploaded.',
            ['Collect macOS report and prepare OpenShell collectors','Import and check this installation\'s EFI reports','Cancel'])
        if action==2:return
        target=selected['target'];gpt=gpt_for(target);_,esp=match_gpt(target,gpt)
        if self.esp_owned and self.esp_device!=target['esp']:require(not self.close_esp(),'Cannot close earlier EFI mount')
        mount=self.mount_esp(target['esp'],readonly=True)
        if action==1:return self.import_reports(mount,selected)
        symptoms=self.interaction.request('diagnostic_notes','What hardware fails, what works, and how can the issue be reproduced? '
            'Mention connected docks/ports, Windows versions and the boot route used. Unknown is fine. '
            'Do not enter passwords, product keys or personal information. You may leave this blank.')
        require(type(symptoms) is str and len(symptoms)<=4000 and '\0' not in symptoms,'Invalid symptom notes')
        phase(self.note,'Reading installed Windows version without changing it')
        software=dict(status='unavailable');deployment_raw=None
        with Probe(target['partition'],self.ntfscat) as probe:
            loader=probe.read('/Windows/System32/winload.efi',8*1024**2)
            try:deployment_raw=probe.read('/WinDeploy/deployment.json',32768)
            except (OSError,ValueError,Refusal):pass
            try:software=software_evidence(probe.read('/Windows/System32/config/SOFTWARE',256*1024**2),self.session)
            except (OSError,ValueError,Refusal) as error:software=dict(status='unavailable',reason=str(error)[:400])
        catalog=self.discovery_json(mount/'EFI/WinDeploy/catalog.json') or {}
        entries=[e for e in catalog.get('entries',[]) if e.get('partition_guid')==selected['observed']['partition_guid'] and e.get('disk_guid')==gpt['guid']]
        version=plistlib.loads((ROOT.parent/'Info.plist').read_bytes()).get('CFBundleShortVersionString','unknown')
        model=run(['/usr/sbin/sysctl','-n','hw.model']).decode().strip()
        report=reports.target_report(selected,gpt,entries[0] if len(entries)==1 else None,
            reports.windows_evidence(loader,software),version,uuid.uuid4().hex,model)
        import report_metadata
        report['current_report_host']=report_metadata.host(ROOT)
        report['original_deployment']=report_metadata.original_deployment(deployment_raw,gpt['guid'],selected['observed']['partition_guid'])
        report['boot_environment']=report_metadata.boot_environment(mount,report_metadata.nvram_version())
        report['issue_guidance']=report_metadata.BOOT_HELP
        report['user_reported_symptoms']=dict(text=symptoms,source='User statement; not a measured hardware result')
        phase(self.note,'Identifying the running macOS volume and backing storage')
        import report_storage
        report['macos_storage']=report_storage.collect()
        report['discovered_windows']=[dict(target=r['observed'],esp_guid=r['esp_guid'],
            windows_version=r.get('windows_version'),created_by_windeploy=reports.eligible(r)) for r in scan['installations']]
        report['discovery_failures']=scan['scan_failures']
        phase(self.note,'Checking selected boot-store mapping read-only')
        before=tree_manifest(mount)
        shells=[dict(path=name,sha256=digest) for name,digest in before.items()
            if name.casefold().endswith(('/openshell.efi','/shell.efi'))]
        report['openshell']=dict(files=shells,status='file found; menu availability not established' if shells else
            'not found on the selected EFI; OpenShell must be available through your boot environment')
        try:
            state=menu_lifecycle.inspect(menu_lifecycle.snapshot(mount,before),gpt['guid'],esp['guid'])
            key=selected['observed']['disk_guid']+':'+selected['observed']['partition_guid']
            store=state['stores'][key]
            from bcd_semantics import identity
            report['boot_store']=dict(sha256=hashlib.sha256(store).hexdigest(),
                identity=identity(store,gpt['guid'],esp['guid'],gpt['guid'],selected['observed']['partition_guid']),
                canonical_path=menu_lifecycle.CANON,selected_store='canonical' if state['active']==key else 'private snapshot',
                active_installation=state['active'],menu_entry=entries[0] if len(entries)==1 else None)
            if not report['conflicts']:report['boot_mapping_status']='verified snapshot; revalidate before any future test'
        except (OSError,ValueError,Refusal,KeyError) as error:
            report['conflicts'].append('Boot mapping could not be fully verified: '+str(error))
            report['boot_mapping_status']='incomplete'
        report['hardware']=hardware(self.note)
        report['known_issue_notes']=(['Windows 7 on 11,4/11,5: cameras, sleep/resume repair and Thunderbolt dock Ethernet remain deferred. Bluetooth discovery was tested, pairing was not. This report does not test these functions.']
            if model in ('MacBookPro11,4','MacBookPro11,5') and report['windows']['family_label'].startswith('Windows 7 ') else [])
        output=self.session/'report-export';output.mkdir()
        write_receipt(output/'macos-report.json',report)
        (output/'READ-ME-FIRST.txt').write_text(reports.summary(report))
        windows_collector(output,report,ROOT)
        instruction=('OpenShell check: '+report['openshell']['status']+'.\n'+
            'To collect firmware evidence: reboot manually into OpenCore OpenShell. Use map -r and inspect fs mappings '
            'to find the EFI partition containing this exact directory:\n'+reports.PREFIX+report['session_id']+
            '\nChange to that directory on the matching fsN: volume, then run Collect.nsh. Do not assume fs0. '
            'Both collectors are read-only apart from saving new reports in their own directory. No fixes, Windows boot or reboot are performed. '
            'Return to macOS and choose Collect Hardware Report > Import and check EFI reports for this same installation.\n')
        (output/'OPEN-SHELL-INSTRUCTIONS.txt').write_text(instruction)
        choice=self.choose('The macOS report is ready. Prepare the two read-only EFI collectors on '+target['esp']+'? '
            'This adds ONLY a fresh diagnostic folder. It does not replace OpenCore, Windows boot files or defaults. '
            'You reboot and run them yourself; no automatic fixes.', ['Prepare EFI collectors','Save macOS report only','Cancel'])
        if choice==2:raise ValueError('Cancelled by user')
        if choice==0:
            fresh=self.discover();matches=[r for r in fresh['installations'] if reports.eligible(r) and r['marker']==selected['marker']]
            require(len(matches)==1,'Selected installation changed during collection')
            if self.esp_owned and self.esp_device!=target['esp']:require(not self.close_esp(),'Cannot close other EFI mount')
            mount=self.mount_esp(target['esp'],readonly=False)
            def guard():
                current=info(target['esp'])
                require(current.get('DiskUUID','').lower()==esp['guid'] and current.get('MountPoint')==str(mount)
                    and current.get('ReadOnlyVolume') is False,'Diagnostic EFI target changed')
                require(gpt_for(target)['digest']==gpt['digest'],'Disk layout changed')
            guard();files=collector_files(report,ROOT)
            require(shutil.disk_usage(mount).free>sum(len(v) for v in files.values())+32*1024**2,
                'Not enough free EFI space for collectors and new reports (32 MiB reserve). No other files will be deleted.')
            require(not (mount/reports.PREFIX/report['session_id']).exists(),'Diagnostic session already exists')
            tx=EFITransaction(mount,self.session/'collector-transaction',guard)
            tx.prepare([dict(path=name,expected=None,data=data) for name,data in files.items()]);tx.commit()
            self.note('Read-only collectors staged in '+reports.PREFIX+report['session_id']+'. No boot files or defaults changed.')
        else:(output/'OPEN-SHELL-INSTRUCTIONS.txt').write_text('EFI collectors were not staged. Run a new collection when you want them.\n')
        self.publish(output)

    def publish(self,folder):
        phase(self.note,'Saving report for manual privacy review')
        result=Store(self.interaction.user_uid).report(folder,self.note)
        self.note('Report saved on your Desktop: '+result['path']+'/Files. Review it before sharing. Nothing uploaded.')
        self.interaction.request('open_folder','Report saved. Review the files before sharing them.',path=result['path']+'/Files')

    def import_reports(self,mount,selected):
        base=mount/reports.PREFIX;require(base.is_dir() and not base.is_symlink(),'No WinDeploy diagnostic sessions found on this EFI')
        candidates=[]
        for folder in sorted(base.iterdir()):
            if not re.fullmatch('[0-9a-f]{32}',folder.name) or folder.is_symlink() or not folder.is_dir():continue
            record=self.discovery_json(folder/'macos-report.json')
            if record and record.get('schema')==1 and record.get('session_id')==folder.name:
                target=record.get('selected_installation',{})
                if (target.get('installation_uuid')==selected['record']['installation_uuid'] and
                    target.get('partition_guid')==selected['observed']['partition_guid'] and
                    target.get('disk_guid')==selected['observed']['disk_guid'] and record.get('esp_guid')==selected['esp_guid']):
                    reports.context_bytes(record)  # Validate bounded fields before presenting or exporting.
                    candidates.append((folder,record))
            require(len(candidates)<=100,'Too many diagnostic sessions; inspect the EFI diagnostic folder')
        require(candidates,'No matching diagnostic report for this current installation. Old/reinstalled targets are not substituted.')
        folder,report=candidates[self.choose('Choose the report session to import and check:',[r['created_utc']+' | '+p.name for p,r in candidates])]
        check=inspect_captures(folder,report)
        output=self.session/'efi-report-export';output.mkdir();total=0;count=0
        for path in folder.rglob('*'):
            require(not path.is_symlink(),'Linked diagnostic file refused')
            if path.is_dir():continue
            require(path.is_file() and path.stat().st_size<=16*1024**2,'Unsafe diagnostic file')
            if path.suffix.lower() not in ('.txt','.json','.dat'):continue
            count+=1;total+=path.stat().st_size
            require(count<=2048 and total<=64*1024**2,'Diagnostic export exceeds limits')
            dest=output/path.relative_to(folder);dest.parent.mkdir(parents=True,exist_ok=True)
            data=bounded_read(path,16*1024**2);dest.write_bytes(data)
            require(hashlib.sha256(dest.read_bytes()).digest()==hashlib.sha256(data).digest(),'Report export readback mismatch')
        write_receipt(output/'REPORT-CHECK.json',check)
        (output/'READ-ME-FIRST.txt').write_text(reports.summary(report)+'\nEFI collectors complete: '+str(check['both_collectors_complete'])+
            '\nThis is report verification only, not hardware validation or an automatic fix.\n')
        windows_collector(output,report,ROOT)
        self.publish(output)
