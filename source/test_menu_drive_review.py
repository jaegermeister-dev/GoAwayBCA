import unittest
from menu_drive_review import describe,comparison

D='aaaaaaaa-1111-4111-8111-111111111111'
P='bbbbbbbb-2222-4222-8222-222222222222'
Q='cccccccc-3333-4333-8333-333333333333'

class Tests(unittest.TestCase):
    def entry(self,p,title):return dict(disk_guid=D,partition_guid=p,title=title,available=True)
    def test_exact_names_and_diff(self):
        before=[self.entry(P,'My 7 for games')];after=[self.entry(Q,'Work system')]
        label=describe('USB disk2',before)
        self.assertIn('1 saved menu entries',label);self.assertIn('My 7 for games',label)
        text=comparison(before,after)
        self.assertIn('Entries to add:\nWork system',text)
        self.assertIn('Entries to remove:\nMy 7 for games',text)
        self.assertIn('not proof',text)
    def test_empty_or_unavailable_does_not_invent_titles(self):
        self.assertIn('(empty menu)',describe('disk1',[]))
        item=dict(self.entry(P,'Archive'),available=False)
        text=comparison([item],[item])
        self.assertIn('Archive (currently unavailable)',text)
        self.assertNotIn('Entries to remove:',text)

if __name__=='__main__':unittest.main()
