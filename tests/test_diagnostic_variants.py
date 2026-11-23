import unittest, uuid, hashlib, struct, tempfile
from pathlib import Path
import boot_diagnostics as d
import boot_diagnostic_binding as b

class VariantsTests(unittest.TestCase):
    def test_all_variants_bind_to_identical_selected_target(self):
        root=Path(d.__file__).parent
        config=b.configuration(*[str(uuid.uuid4()) for _ in range(4)],4096,'11'*32,'22'*32,'MacBook5,1',folder_name='Test-002')
        programs=d.bound_tests(root,config)
        self.assertEqual(set(programs),{'Test1.efi','Test2.efi','Test3.efi'})
        self.assertEqual(len({hashlib.sha256(x).digest() for x in programs.values()}),3)
        for data in programs.values():
            self.assertEqual(data.count(b.MAGIC),1)
            offset=data.index(b.MAGIC)
            self.assertEqual(data[offset:offset+512],config)
            self.assertEqual(struct.unpack_from('<H',data,struct.unpack_from('<I',data,60)[0]+4)[0],0x8664)

    def test_guide_identifies_all_tests_and_restart_boundary(self):
        text=d.instructions('EFI/WinDeploy/Diagnostics/Test-002','Named Windows','esp-guid')
        for text_needed in ('Test1.efi','Test2.efi','Test3.efi','Restart before the next test','Named Windows','esp-guid','Test-002','all logs','Existing tools and logs are preserved','bypass the recognised experimental Fix.efi','Entry.bin'):
            self.assertIn(text_needed,text)
        self.assertNotIn('WindowsDiagnostics.efi',text)
        self.assertNotIn('press Enter',text)

    def test_missing_template_refuses_instead_of_creating_partial_set(self):
        config=b.configuration(*[str(uuid.uuid4()) for _ in range(4)],512,'11'*32,'22'*32,'MacBook5,1',folder_name='Test-001')
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):d.bound_tests(Path(temp),config)

if __name__=='__main__':unittest.main()
