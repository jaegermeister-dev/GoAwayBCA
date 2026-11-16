"""User-confirmed repair of our menu only. Never formats a Windows partition."""
import hashlib
import shutil
from boot_catalog import identity
from efi_transaction import EFITransaction
from manage_installations import InstallationManager
from install_windows7 import (ROOT,require,info,gpt_for,match_gpt,tree_manifest,
    require_efi_unchanged,write_receipt,run)
import menu_lifecycle as lifecycle
import backtoseven_menu as component_menu
from operation_progress import phase,Message


class RebuildMenu(InstallationManager):
    def execute(self):
        self.begin();phase(self.note,'Verifying Windows installations and current boot records')
        scan=self.scan();require(scan['scan_complete'],'Some Windows partitions could not be verified; no menu changed')
        write_receipt(self.session/'rebuild-discovery.json',scan)
        targets={}
        for row in scan['installations']:targets.setdefault(row['target']['esp'],row)
        import menu_presence
        # A different disk may still contain Windows while every installation
        # on this menu's disk has been removed externally. Offer both disks.
        targets.update(menu_presence.empty_menu_targets(self,excluded_esps=set(targets)))
        require(targets,'No Windows installations or existing Windows boot menu found; no files changed')
        choices=list(targets.values())
        selected=choices[0] if len(choices)==1 else choices[self.choose(
            'Choose which disk\'s Windows menu to rebuild. No Windows partition will be formatted.',
            [r['target']['esp']+'\n'+r['summary'] for r in choices])]
        target=selected['target'];gpt=gpt_for(target)
        if selected.get('boot_menu_only'):
            esp=next(p for p in gpt['partitions'].values() if p['guid']==selected['esp_guid'])
        else:_,esp=match_gpt(target,gpt)
        if self.esp_owned and self.esp_device!=target['esp']:
            require(not self.close_esp(),'Could not detach prior scan EFI partition')
        mount=self.mount_esp(target['esp'],readonly=True);before=tree_manifest(mount)
        require_efi_unchanged(before,selected['efi_manifest'],'EFI changed since menu discovery')
        missing_catalog=not any(p.casefold()==lifecycle.CATALOG.casefold() for p in before)
        files=lifecycle.snapshot(mount,before)
        old=component_menu.previous(files,gpt['guid'],esp['guid'])
        removed=menu_presence.collect(self,old['catalog']['entries'],scan['installations'],target)
        registered={identity(e) for e in old['catalog']['entries']}
        additions=[r['title'] for r in scan['installations'] if r['target']['esp']==target['esp']
                   and identity(r['observed']) not in registered]
        indexed=lifecycle.indexed(before)
        replaced_public_loader=any(indexed.get(path.casefold(),(None,None))[1] not in
            (None,*lifecycle.PRIOR_CHOOSER_SHAS,lifecycle.CHOOSER_SHA)
            for path in lifecycle.ALIASES if path!='EFI/Boot/BOOTX64.EFI')
        recovering=missing_catalog or bool(additions) or replaced_public_loader
        if recovering:
            from backtoseven_menu_actions import read_efi,assemble,verify_sources
            files=read_efi(mount,before)
        chooser=(ROOT/'efi/Windows.efi').read_bytes();ntfs=(ROOT/'efi/OpenNtfsDxe.efi').read_bytes()
        def plan(rows):
            if recovering:
                return assemble(self,mount,files,gpt['guid'],esp['guid'],rows,confirmed_absent=removed)
            return lifecycle.transition(files,before,gpt['guid'],esp['guid'],rows,
                chooser=chooser,ntfs_driver=ntfs,rebuild_menu=True,ntfs_4kn_driver=(ROOT/'efi/Ntfs4Kn.efi').read_bytes(),confirmed_absent=removed)
        proposed=plan(scan['installations'])
        require(recovering or bool(removed) or not proposed['canonical_changed'],'Rebuilding must preserve the active Windows boot store')
        registered={identity(e) for e in proposed['catalog']['entries']}
        unregistered=[r['title'] for r in scan['installations'] if r['target']['esp']==target['esp']
                      and identity(r['observed']) not in registered]
        backup=self.session/'rebuild-efi-before';shutil.copytree(mount,backup)
        require(tree_manifest(backup,self.note,'Verifying boot-menu backup')==before,'EFI backup verification failed')
        report=dict(status='rebuild-planned',esp=target['esp'],efi_before=before,recovered_missing_catalog=missing_catalog,
            added_installations=additions,externally_removed_entries=removed,reconciled_public_loader=replaced_public_loader,
            entries=proposed['catalog']['entries'],unregistered_installations=unregistered,
            partition_formatted=False,windows_files_changed=False,default_changed=False,
            changes=[dict(path=c['path'],before=c['expected'],
                after=lifecycle.sha(c['data']) if c['data'] is not None else None) for c in proposed['changes']])
        receipt=self.session/'rebuild-receipt.json';write_receipt(receipt,report)
        text=('Rebuild the GoAwayBCA Windows menu on '+target['esp']+'.\n\n'+
            '\n'.join(e['title']+(' (currently unavailable)' if not e['available'] else '')
                      for e in proposed['catalog']['entries'])+
            '\n\nThis restores our verified selector and regenerates its list from existing verified boot records. '
            'It does not format partitions, replace Windows, change OpenCore or change the default boot choice. '+
            (('The deleted or absent menu catalog will be recreated. ' if missing_catalog else
              'Discovered Windows installations and changed Windows boot entries will be reconciled. ')+
             'The proposed menu uses discovered Windows loaders and verified boot-store copies. '
             'Each recovered store is checked against its Windows partition. '
             'The menu may initially select a different Windows entry; no firmware startup default changes.'
             if recovering else
             'Windows-serviced BCD data is preserved. A corrupt/missing identity record or boot store requires '
             'separate recovery; this operation will not invent replacements.'))
        if additions and not missing_catalog:
            text+='\n\nNew installations to add: '+', '.join(additions)
        if removed:
            gone={identity(p) for p in removed}
            text+='\n\nConfirmed removed outside this app; removing from the menu: '+', '.join(
                e['title'] for e in old['catalog']['entries'] if identity(e) in gone)
        if unregistered:
            text+='\n\nThese installations lack our complete boot records and will remain unchanged: '+', '.join(unregistered)
        if self.args.dry_run:self.note(text);self.note('Rebuild preview complete; no physical writes.');return
        require(self.approve('rebuild_review',text),'Menu rebuild cancelled')
        phase(self.note,'Rechecking disk and boot-menu identity before publication')
        fresh=self.scan();require(fresh['scan_complete'],'Installation scan changed; no menu changed')
        menu_presence.recheck(self,old['catalog']['entries'],fresh['installations'],target,removed)
        if recovering:
            require(fresh['installations']==scan['installations'],'Available installations changed; start again')
            verify_sources(self,proposed['windows_sources'])
        else:require(plan(fresh['installations'])==proposed,'Available installations changed; start again')
        if self.esp_owned and self.esp_device!=target['esp']:
            require(not self.close_esp(),'Could not detach prior scan EFI partition')
        mount=self.mount_esp(target['esp'],readonly=False)
        def guard():
            current=info(target['esp'])
            require(current.get('DiskUUID','').lower()==esp['guid'] and current.get('MountPoint')==str(mount)
                and current.get('ReadOnlyVolume') is False,'EFI identity/access changed before rebuilding')
            require(gpt_for(target)['digest']==gpt['digest'],'Disk layout changed before rebuilding')
        guard();require_efi_unchanged(tree_manifest(mount),before,'EFI changed before rebuilding')
        tx=EFITransaction(mount,self.session/'rebuild-efi-transaction',guard)
        tx.prepare(proposed['changes']);tx.commit(after_each=lambda index:self.note(Message(
            'Rebuilding Windows menu: %d of %d files'%(index+1,len(tx.journal['files'])),
            'Rebuilding Windows menu',100*(index+1)/len(tx.journal['files']))))
        phase(self.note,'Flushing boot-menu update');run(['/bin/sync'])
        report.update(status='rebuild-complete',efi_after=tree_manifest(mount));write_receipt(receipt,report)
        self.note('Windows boot menu rebuilt. No Windows partition, OpenCore or default startup choice changed.')
        if self.interaction is not None:
            self.interaction.request('notice',
                'The Windows boot menu has been rebuilt. Restart when ready to try your Windows installations. '
                'No Windows partition was formatted, and OpenCore and your default startup choice were not changed.',
                button_title='Done')
