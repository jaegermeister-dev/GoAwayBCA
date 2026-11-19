import copy
import unittest
from unittest.mock import Mock,patch
import menu_presence as p
import menu_lifecycle as m
import backtoseven_menu as imported
from test_menu_lifecycle import D,E,PARTS,entry,rows,store,apply,plan
from test_backtoseven_menu import child,ROOT


def proof(i, reason='windows-directory-absent'):
    return dict(disk_guid=D,partition_guid=PARTS[i],status='confirmed-absent',
                reason=reason,layout_digest='a'*64)


class PresenceTests(unittest.TestCase):
    def test_runtime_collection_requires_readable_identity_matched_partition(self):
        target=dict(disk=dict(DeviceIdentifier='disk7'))
        geometry=dict(guid=PARTS[0],first=2048,last=4095)
        layout=dict(guid=D,digest='a'*64,sector_size=512,partition_scan_complete=True,
            partitions={1:geometry},partition_devices={PARTS[0]:'disk7s2'})
        volume=dict(DiskUUID=PARTS[0],ParentWholeDisk='disk7',Size=1048576,
                    PartitionMapPartitionOffset=1048576)
        owner=Mock(ntfscat='/verified/tools/ntfscat')
        with patch('install_windows7.gpt_for',return_value=layout), \
             patch('install_windows7.info',return_value=volume) as info, \
             patch('install_windows7.filesystem',return_value='ntfs'), \
             patch('ntfs_read.root_names',return_value=['Users','Program Files']) as root_names:
            self.assertEqual(p.collect(owner,[entry(0)],[],target),[proof(0)])
            root_names.return_value=['wInDoWs','Users']
            self.assertEqual(p.collect(owner,[entry(0)],[],target),[])
            root_names.side_effect=OSError('volume unreadable')
            self.assertEqual(p.collect(owner,[entry(0)],[],target),[])
            root_names.reset_mock(side_effect=True)
            info.return_value=dict(volume,DiskUUID=PARTS[1])
            self.assertEqual(p.collect(owner,[entry(0)],[],target),[])
            root_names.assert_not_called()

    def test_runtime_collection_refuses_layout_change(self):
        layout=dict(guid=D,digest='a'*64,partition_scan_complete=True,partitions={})
        with patch('install_windows7.gpt_for',side_effect=[layout,dict(layout,digest='b'*64)]):
            with self.assertRaisesRegex(RuntimeError,'layout changed'):
                p.collect(Mock(),[entry(0)],[],dict(disk=dict(DeviceIdentifier='disk7')))

    def test_only_positive_same_disk_evidence_removes(self):
        layout=dict(guid=D,digest='a'*64,partition_scan_complete=True,
                    partitions={i:dict(guid=PARTS[i]) for i in (0,1)})
        entries=[entry(i) for i in range(3)]
        for state,expected in (('unknown',[2]),('windows-present',[2]),
                               ('no-windows-directory',[1,2]),('non-ntfs-filesystem',[1,2])):
            reader=Mock(return_value=state)
            result=p.inspect_presence(entries,rows([0]),layout,reader)
            self.assertEqual([x['partition_guid'] for x in result],[PARTS[i] for i in expected])
            reader.assert_called_once_with(PARTS[1],layout['partitions'][1])
        self.assertEqual(p.inspect_presence(entries,[],dict(layout,guid=E),Mock()),[])

    def test_incomplete_enumeration_is_not_deletion(self):
        layout=dict(guid=D,digest='a'*64,partition_scan_complete=False,partitions={})
        self.assertEqual(p.inspect_presence([entry(0)],[],layout,Mock()),[])

    def test_remove_active_external_then_last_preserves_unrelated_efi(self):
        files={}
        for i in range(3):files=apply(files,plan(files,list(range(i)),add=i))
        files['EFI/OC/OpenCore.efi']=b'unchanged'
        files['EFI/ubuntu/grubx64.efi']=b'unchanged linux'
        original=copy.deepcopy(files)
        change=m.transition(files,{k:m.sha(v) for k,v in files.items()},D,E,rows([1,2]),
                            rebuild_menu=True,confirmed_absent=[proof(0)])
        result=apply(files,change)
        remaining=m.inspect(result,D,E)
        self.assertEqual(result[m.CANON],remaining['stores'][remaining['active']])
        self.assertEqual(len(m.inspect(result,D,E)['stores']),2)
        change=m.transition(result,{k:m.sha(v) for k,v in result.items()},D,E,[],
                            rebuild_menu=True,confirmed_absent=[proof(1),proof(2)])
        result=apply(result,change)
        self.assertEqual(m.inspect(result,D,E)['catalog']['entries'],[])
        self.assertIn(m.MENU,result)
        self.assertNotIn(m.CANON,result)
        self.assertEqual(result['EFI/OC/OpenCore.efi'],original['EFI/OC/OpenCore.efi'])
        self.assertEqual(result['EFI/ubuntu/grubx64.efi'],original['EFI/ubuntu/grubx64.efi'])
        self.assertEqual(files,original)

    def test_removal_contradicting_current_windows_refuses(self):
        files=apply({},plan({},[],add=0))
        with self.assertRaisesRegex(ValueError,'also observed'):
            m.transition(files,{k:m.sha(v) for k,v in files.items()},D,E,rows([0]),confirmed_absent=[proof(0)])
        with self.assertRaisesRegex(ValueError,'positive'):
            m.transition(files,{k:m.sha(v) for k,v in files.items()},D,E,[],
                         confirmed_absent=[dict(proof(0),reason='scan-missed-it')])

    def test_new_install_can_reuse_confirmed_empty_partition(self):
        files=apply({},plan({},[],add=0))
        updated=dict(entry(0),title='Replacement Windows 7')
        result=apply(files,m.transition(files,{k:m.sha(v) for k,v in files.items()},D,E,[],
            addition=updated,new_store=store(0),confirmed_absent=[proof(0)]))
        self.assertEqual(m.inspect(result,D,E)['catalog']['entries'][0]['title'],'Replacement Windows 7')

    def test_import_new_windows_and_prune_removed_in_one_plan(self):
        def reconcile(files,indices,removed=()):
            children={m.identity(child(i)):child(i) for i in indices}
            return imported.reconcile(files,D,E,[dict(observed=c) for c in children.values()],children,
                {m.identity(child(i)):store(i) for i in indices},
                (ROOT/'efi/Windows.efi').read_bytes(),(ROOT/'efi/OpenNtfsDxe.efi').read_bytes(),
                confirmed_absent=removed)
        files=apply({},reconcile({},[0,1]))
        result=apply(files,reconcile(files,[1,2],[proof(0)]))
        self.assertEqual({e['partition_guid'] for e in m.inspect(result,D,E)['catalog']['entries']},set(PARTS[1:]))


if __name__=='__main__':unittest.main()
