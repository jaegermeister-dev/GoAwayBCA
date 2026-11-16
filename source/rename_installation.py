"""Rename a verified picker entry without formatting or changing Windows data."""
import shutil
from boot_catalog import identity
from efi_transaction import EFITransaction
from install_windows7 import (ROOT,require,info,gpt_for,match_gpt,tree_manifest,
    require_efi_unchanged,write_receipt,run)
import menu_lifecycle as lifecycle
from entry_names import prompt
from operation_progress import Message,phase

def execute(manager,selected,scan):
    require(scan['scan_complete'],'Incomplete installation scan')
    target=selected['target'];key=identity(selected['observed'])
    gpt=gpt_for(target);win,esp=match_gpt(target,gpt)
    if manager.esp_owned and manager.esp_device!=target['esp']:
        require(not manager.close_esp(),'Could not detach prior scan ESP')
    mount=manager.mount_esp(target['esp'],readonly=True);before=tree_manifest(mount)
    require_efi_unchanged(before,selected['efi_manifest'],'EFI changed since discovery')
    files=lifecycle.snapshot(mount,before);old=lifecycle.inspect(files,gpt['guid'],esp['guid'])
    entries=[e for e in old['catalog']['entries'] if identity(e)==key]
    require(len(entries)==1,'This Windows installation has no entry in our picker to rename. Its files were not changed.')
    old_name=entries[0]['title'];new_name=prompt(manager.interaction,old_name,True)
    if new_name==old_name:manager.note('Name unchanged. No files changed.');return
    import menu_refresh
    refresh=menu_refresh.prepare(manager,mount,before,gpt['guid'],esp['guid'],scan['installations'],rename=(key,new_name),target=target)
    plan=refresh or lifecycle.transition(files,before,gpt['guid'],esp['guid'],scan['installations'],rename=(key,new_name))
    require(not plan['canonical_changed'] or bool((refresh or {}).get('confirmed_absent')),
            'Renaming must not change the active Windows boot store')
    backup=manager.session/'rename-efi-before';shutil.copytree(mount,backup)
    require(tree_manifest(backup,manager.note,'Verifying rename EFI backup')==before,'Rename backup verification failed')
    receipt=dict(status='rename-planned',identity=key,before=old_name,after=new_name,
        windows_files_changed=False,partition_formatted=False,default_changed=False,efi_before=before)
    receipt_path=manager.session/'rename-receipt.json';write_receipt(receipt_path,receipt)
    if manager.args.dry_run:manager.note('Rename preview complete. No physical writes.');return
    require(manager.approve('rename_review','Rename only this picker label:\n\n'+old_name+' → '+new_name+
        '\n\n'+selected['summary']+'\n\nNo formatting. Windows files, other installations and OpenCore are preserved.'+
        menu_refresh.review(refresh)),
        'Rename cancelled')
    fresh=manager.scan()
    require(fresh['scan_complete'] and sum(identity(r['observed'])==key for r in fresh['installations'])==1,
            'Installation identity changed before rename')
    require(fresh['installations']==scan['installations'],'Windows discovery changed; start again before renaming')
    if refresh:
        from backtoseven_menu_actions import verify_sources
        verify_sources(manager,refresh['windows_sources'])
        menu_refresh.recheck(manager,files,gpt['guid'],esp['guid'],fresh['installations'],target,refresh)
    if manager.esp_owned and manager.esp_device!=target['esp']:
        require(not manager.close_esp(),'Could not detach prior scan ESP')
    mount=manager.mount_esp(target['esp'],readonly=False)
    def guard():
        state=info(target['esp'])
        require(state.get('DiskUUID','').lower()==esp['guid'] and state.get('MountPoint')==str(mount)
            and state.get('ReadOnlyVolume') is False,'EFI identity/access changed before rename')
        require(gpt_for(target)['digest']==gpt['digest'],'Disk layout changed before rename')
    guard();require_efi_unchanged(tree_manifest(mount),before,'EFI changed before rename')
    tx=EFITransaction(mount,manager.session/'rename-efi-transaction',guard)
    tx.prepare(plan['changes']);tx.commit(after_each=lambda index:manager.note(Message(
        'Renaming menu entry: %d of %d files'%(index+1,len(tx.journal['files'])),
        'Renaming menu entry',100*(index+1)/len(tx.journal['files']))))
    phase(manager.note,'Flushing rename and saving receipt');run(['/bin/sync'])
    receipt.update(status='rename-complete',efi_after=tree_manifest(mount));write_receipt(receipt_path,receipt)
    manager.note('Picker entry renamed to '+new_name+'. No partition or Windows files were changed.')
