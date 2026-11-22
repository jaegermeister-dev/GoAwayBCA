import hashlib,struct,unittest,uuid
import boot_diagnostic_binding as b

class FolderTests(unittest.TestCase):
 def test_repeated_creation_preserves_existing_names(self):
  names=['0123456789abcdef0123456789abcdef','test-001','TEST-002','notes.txt']
  before=list(names)
  self.assertEqual(b.next_folder(names),'Test-003')
  self.assertEqual(names,before)
  self.assertEqual(b.next_folder([]),'Test-001')
 def test_name_exhaustion_refuses_instead_of_overwriting(self):
  with self.assertRaises(ValueError):b.next_folder(['Test-%03d'%i for i in range(1,1000)])
 def test_short_and_legacy_bindings(self):
  args=[str(uuid.uuid4()) for _ in range(4)]+[4096,'11'*32,'22'*32,'MacBookPro11,5']
  for folder,version in ((None,1),('Test-001',2),('Test-999',2)):
   data=b.configuration(*args,folder_name=folder)
   self.assertEqual(len(data),512)
   self.assertEqual(struct.unpack_from('<I',data,32)[0],version)
   self.assertEqual(data[168:200],hashlib.sha256(data[:168]+data[200:]).digest())
   if folder:self.assertEqual(data[264:280],folder.encode()+bytes(8))
  for folder in ('Test-000','Test-01','../Test-001','Test-001/x','test-001','Test-001\\x'):
   with self.assertRaises(ValueError):b.configuration(*args,folder_name=folder)

if __name__=='__main__':unittest.main()
