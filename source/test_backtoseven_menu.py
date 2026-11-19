import copy
import unittest
from pathlib import Path
import backtoseven_menu as menu
import menu_lifecycle as m
from bcd import personalise
from backtoseven_experimental_policy import installation_choice, WARNING

D='aaaaaaaa-1111-4111-8111-111111111111'
E='cccccccc-3333-4333-8333-333333333333'
P=['bbbbbbbb-2222-4222-8222-222222222222','dddddddd-4444-4444-8444-444444444444','eeeeeeee-5555-4555-8555-555555555555']
ROOT=Path(__file__).parent

def store(i):
    raw=(ROOT/('BCD.template','BCD81.template','BCD10.template')[i]).read_bytes()
    return personalise(raw,m.sha(raw),D,E,P[i])

def child(i):
    path='EFI/BackToSeven/Children/'+P[i]+'/'
    loader=('original loader %d'%i).encode()
    return dict(disk_guid=D,partition_guid=P[i],esp_guid=E,boot_partition_guid=E,
        title=('Windows 7','Windows 8.1','Windows 10')[i],loader_path=path+'bootmgfw.efi',bcd_path=path+'BCD',
        single_os_bcd_verified=True,available=True,requires_ntfs=False,hash_locked=True,
        loader_sha256=m.sha(loader),_files={path+'bootmgfw.efi':loader,path+'BCD':store(i)},
        public_loader_hashes=[m.sha(loader)])

def apply(files,plan):
    result=dict(files)
    for c in plan['changes']:
        old=result.get(c['path'])
        assert (m.sha(old) if old is not None else None)==c['expected']
        if c['data'] is None:result.pop(c['path'],None)
        else:result[c['path']]=c['data']
    return result

class ReconcileTests(unittest.TestCase):
    def plan(self,files,indices):
        entries=[child(i) for i in indices]
        return menu.reconcile(files,D,E,[dict(observed=e) for e in entries],
            {m.identity(e):e for e in entries},{m.identity(child(i)):store(i) for i in indices},
            (ROOT/'efi/Windows.efi').read_bytes(),(ROOT/'efi/OpenNtfsDxe.efi').read_bytes())

    def test_later_installer_overwrites_alias_and_bcd_then_adds_third_os(self):
        files={m.CANON:store(0),'EFI/Microsoft/Boot/bootmgfw.efi':b'original loader 0',
               'EFI/Boot/BOOTX64.EFI':b'OpenCore fallback', 'EFI/OC/config.plist':b'OpenCore config'}
        first=self.plan(files,[0]);files=apply(files,first)
        # Later independent Windows setup replaces canonical loader/store.
        for i in (1,2):
            files[m.CANON]=store(i)
            files['EFI/Microsoft/Boot/bootmgfw.efi']=('original loader %d'%i).encode()
            original=dict(files)
            plan=self.plan(files,list(range(i+1)));files=apply(files,plan)
            self.assertEqual(files['EFI/Boot/BOOTX64.EFI'],b'OpenCore fallback')
            self.assertEqual(files['EFI/OC/config.plist'],b'OpenCore config')
            self.assertEqual(len(m.inspect(files,D,E)['catalog']['entries']),i+1)
            self.assertEqual(original[m.CANON],store(i))
        self.assertEqual({m.identity(e) for e in m.inspect(files,D,E)['catalog']['entries']},
                         {m.identity(child(i)) for i in range(3)})

    def test_missing_installation_kept_unavailable_and_its_store_unchanged(self):
        files=apply({},self.plan({},[0,1,2]))
        old=m.inspect(files,D,E)
        final=apply(files,self.plan(files,[0,2]));new=m.inspect(final,D,E)
        unavailable=next(e for e in new['catalog']['entries'] if e['partition_guid']==P[1])
        self.assertFalse(unavailable['available'])
        self.assertEqual(new['stores'][m.identity(child(1))],old['stores'][m.identity(child(1))])

    def test_unknown_public_loader_is_retained(self):
        files={'EFI/Microsoft/Boot/bootmgfw.efi':b'unknown custom bootloader'}
        before=dict(files)
        with self.assertRaisesRegex(ValueError,'Unrecognised public boot loader'):
            self.plan(files,[0])
        self.assertEqual(files,before)

    def test_corrupt_saved_configuration_refuses_instead_of_guessing(self):
        files=apply({},self.plan({},[0,1]))
        files[m.CONFIG]=files[m.CONFIG][:-1]+bytes([files[m.CONFIG][-1]^1])
        with self.assertRaises(ValueError):self.plan(files,[0,1,2])

    def test_no_menu_prompt_without_existing_windows(self):
        class UI:
            def choose(self,*args):raise AssertionError('No menu offer expected')
        self.assertFalse(installation_choice(UI(),dict(scan_complete=True,installations=[])))
        self.assertIn('unintended side effects',WARNING)
        self.assertIn('may not recover',WARNING)

if __name__=='__main__':unittest.main()
