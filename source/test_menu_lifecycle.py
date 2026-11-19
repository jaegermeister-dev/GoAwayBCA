import copy
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from bcd import personalise
from chooser_config import decode
from efi_transaction import EFITransaction
from install_windows7 import tree_manifest
import menu_lifecycle as m

D='aaaaaaaa-1111-4111-8111-111111111111'
E='cccccccc-3333-4333-8333-333333333333'
PARTS=['bbbbbbbb-2222-4222-8222-222222222222','dddddddd-4444-4444-8444-444444444444','eeeeeeee-5555-4555-8555-555555555555']

def entry(index):
    return dict(disk_guid=D,partition_guid=PARTS[index],esp_guid=E,boot_partition_guid=PARTS[index],
        title=('Windows 7','Windows 8.1','Windows 10')[index],loader_path='EFI/Microsoft/Boot/bootmgfw.efi',
        bcd_path='EFI/Microsoft/Boot/BCD',single_os_bcd_verified=True,available=True,
        requires_ntfs=True,hash_locked=False,loader_sha256='a'*64)

def store(index):
    template=(Path(__file__).parent/('BCD.template','BCD81.template','BCD10.template')[index]).read_bytes()
    return personalise(template,m.sha(template),D,E,PARTS[index])

def rows(indices):
    return [dict(observed=entry(i)) for i in indices]

def plan(files,indices,add=None,remove=None):
    return m.transition(files,{k:m.sha(v) for k,v in files.items()},D,E,rows(indices),
        addition=entry(add) if add is not None else None,new_store=store(add) if add is not None else None,
        remove=m.identity(entry(remove)) if remove is not None else None)

def apply(files,transaction):
    result=dict(files)
    for c in transaction['changes']:
        assert (m.sha(result[c['path']]) if c['path'] in result else None)==c['expected']
        if c['data'] is None:del result[c['path']]
        else:result[c['path']]=c['data']
    return result

class LifecycleTests(unittest.TestCase):
    def populated(self):
        files={}
        for i in range(3):files=apply(files,plan(files,list(range(i)),add=i))
        return files

    def test_add_three_remove_every_order_then_reinstall(self):
        import itertools
        for order in itertools.permutations(range(3)):
            files=self.populated();remaining=[0,1,2]
            initial=m.inspect(files,D,E)
            self.assertEqual(len(initial['stores']),3)
            for index in order:
                before=m.inspect(files,D,E)
                files=apply(files,plan(files,remaining,remove=index));remaining.remove(index)
                after=m.inspect(files,D,E)
                self.assertEqual({e['partition_guid'] for e in after['catalog']['entries']},{PARTS[i] for i in remaining})
                for i in remaining:self.assertEqual(after['stores'][m.identity(entry(i))],before['stores'][m.identity(entry(i))])
                if after['active'] is not None:
                    self.assertEqual(files[m.CANON],after['stores'][after['active']])
            self.assertNotIn(m.CANON,files)
            self.assertEqual(decode(files[m.MENU]),[])
            files=apply(files,plan(files,[],add=0))
            self.assertEqual(m.inspect(files,D,E)['active'],m.identity(entry(0)))

    def test_preserves_windows_serviced_current_store_and_logs_when_active_survives(self):
        files=self.populated()
        # A later clean store for the same identity must supersede the snapshot.
        # Use a distinct valid template but personalise it for the active Win7.
        template=(Path(__file__).parent/'BCD10.template').read_bytes()
        newer=personalise(template,m.sha(template),D,E,PARTS[0])
        files[m.CANON]=newer;files[m.CANON+'.LOG1']=b'Windows log'
        updated=apply(files,plan(files,[0,1,2],remove=2))
        self.assertEqual(updated[m.CANON],newer)
        self.assertEqual(updated[m.CANON+'.LOG1'],b'Windows log')
        self.assertEqual(m.inspect(updated,D,E)['stores'][m.identity(entry(0))],newer)
        updated=apply(updated,plan(updated,[0,1],remove=0))
        self.assertNotIn(m.CANON+'.LOG1',updated)
        self.assertEqual(updated[m.CANON],store(1))

    def test_absent_other_installation_not_deleted(self):
        files=self.populated();updated=apply(files,plan(files,[0,1],remove=0))
        entries=m.inspect(updated,D,E)['catalog']['entries']
        self.assertEqual(len(entries),2)
        self.assertFalse(next(e for e in entries if e['partition_guid']==PARTS[2])['available'])

    def test_corrupt_or_pending_state_and_foreign_canonical_refused(self):
        files=self.populated()
        for name,value in ((m.PREFIX+'Pending.bin',b'pending'),(m.STATE,b'bad'),
                           (m.CANON,store(1)),(m.CONFIG,b'bad')):
            with self.subTest(name=name),self.assertRaises((ValueError,KeyError)):
                plan(dict(files,**{name:value}),[0,1,2],remove=0)
        with self.assertRaises(ValueError):plan(files,[0,1,2],add=0)

    def test_foreign_public_loader_not_overwritten(self):
        from unittest.mock import patch
        payload=b'MZ synthetic chooser';driver=b'MZ synthetic driver'
        with patch.object(m,'CHOOSER_SHA',m.sha(payload)),patch.object(m,'NTFS_SHA',m.sha(driver)):
            with self.assertRaisesRegex(ValueError,'another loader'):
                m.transition({}, {m.ALIASES[1]:'f'*64},D,E,[],addition=entry(0),new_store=store(0),chooser=payload,ntfs_driver=driver)

    def test_rename_reorders_menu_without_swapping_installations(self):
        files=self.populated();old=m.inspect(files,D,E);key=m.identity(entry(0))
        files[m.CANON+'.LOG1']=b'preserved live log'
        renamed=m.transition(files,{k:m.sha(v) for k,v in files.items()},D,E,rows([0,1,2]),
            rename=(key,'ZZZ Legacy Gaming'))
        updated=apply(files,renamed);after=m.inspect(updated,D,E)
        self.assertEqual(after['active'],old['active'])
        self.assertEqual(after['stores'],old['stores'])
        self.assertEqual(updated[m.CANON],files[m.CANON])
        self.assertEqual(updated[m.CANON+'.LOG1'],b'preserved live log')
        self.assertEqual(after['catalog']['entries'][-1]['title'],'ZZZ Legacy Gaming')
        self.assertEqual(after['catalog']['entries'][-1]['partition_guid'],PARTS[0])
        self.assertFalse(renamed['canonical_changed'])

    def test_duplicate_display_titles_keep_distinct_identity(self):
        files=self.populated()
        for index in (0,1,2):
            files=apply(files,m.transition(files,{k:m.sha(v) for k,v in files.items()},D,E,rows([0,1,2]),
                rename=(m.identity(entry(index)),'Windows 10')))
        result=m.inspect(files,D,E)
        self.assertEqual(len(result['stores']),3)
        self.assertEqual({e['title'] for e in result['catalog']['entries']},{'Windows 10'})

    def test_renaming_nonexistent_or_invalid_title_is_refused(self):
        from entry_names import validate
        for title in ('','   ','a'*96,'name\nother','snow 雪',None):
            with self.assertRaises(ValueError):validate(title)
        self.assertEqual(validate('  Games - Win7  '),'Games - Win7')
        files=self.populated()
        with self.assertRaises(ValueError):m.transition(files,{k:m.sha(v) for k,v in files.items()},D,E,rows([0,1,2]),
            rename=(D+':'+E,'Unknown'))

    def test_rebuild_repairs_only_redundant_menu_preserving_serviced_store(self):
        for missing in (False,True):
            files=self.populated();before=m.inspect(files,D,E)
            if missing:del files[m.MENU]
            else:files[m.MENU]=b'corrupted menu only'
            files[m.CANON+'.LOG1']=b'Windows servicing log'
            with self.assertRaises(ValueError):m.inspect(files,D,E)
            changes=m.transition(files,{k:m.sha(v) for k,v in files.items()},D,E,rows([0,1,2]),rebuild_menu=True)
            after=apply(files,changes)
            state=m.inspect(after,D,E)
            self.assertEqual(before['stores'],state['stores'])
            self.assertEqual(before['active'],state['active'])
            self.assertEqual(after[m.CANON],files[m.CANON])
            self.assertEqual(after[m.CANON+'.LOG1'],files[m.CANON+'.LOG1'])
            self.assertFalse(changes['canonical_changed'])

    def test_rebuild_does_not_bypass_invalid_configuration_or_foreign_bcd(self):
        for key,value in ((m.CONFIG,b'bad'),(m.STATE,b'bad'),(m.CANON,store(1))):
            files=self.populated();files[m.MENU]=b'bad';files[key]=value
            with self.assertRaises((ValueError,KeyError)):
                m.transition(files,{k:m.sha(v) for k,v in files.items()},D,E,rows([0,1,2]),rebuild_menu=True)
        with self.assertRaises(ValueError):m.transition({}, {},D,E,[],rebuild_menu=True)

    def test_rebuild_migrates_known_selectors_and_preserves_foreign_alias(self):
        from unittest.mock import patch
        payload=b'MZ new fixture';driver=b'MZ ntfs fixture'
        for prior in m.PRIOR_CHOOSER_SHAS:
            files=self.populated();manifest={k:m.sha(v) for k,v in files.items()}
            manifest.update({alias:prior for alias in m.ALIASES})
            with patch.object(m,'CHOOSER_SHA',m.sha(payload)),patch.object(m,'NTFS_SHA',m.sha(driver)):
                changed=m.transition(files,manifest,D,E,rows([0,1,2]),rebuild_menu=True,chooser=payload,ntfs_driver=driver)
                self.assertEqual({c['path'] for c in changed['changes'] if c['data']==payload},set(m.ALIASES))
                manifest[m.ALIASES[0]]='f'*64
                with self.assertRaisesRegex(ValueError,'another loader'):
                    m.transition(files,manifest,D,E,rows([0,1,2]),rebuild_menu=True,chooser=payload,ntfs_driver=driver)

    def test_publication_failure_restores_exact_previous_generation(self):
        before=self.populated();before['EFI/OC/OpenCore.efi']=b'protected OpenCore'
        migration=plan(before,[0,1,2],remove=0)
        for fail_at in range(len(migration['changes'])):
            with tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary).resolve();esp=root/'esp';esp.mkdir()
                for path,data in before.items():
                    target=esp/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
                original=tree_manifest(esp)
                tx=EFITransaction(esp,root/'journal',lambda:None);tx.prepare(migration['changes'])
                def fail(index):
                    if index==fail_at:raise RuntimeError('injected failure')
                with self.assertRaises(RuntimeError):tx.commit(fail)
                self.assertEqual(tree_manifest(esp),original)

if __name__=='__main__':unittest.main()
