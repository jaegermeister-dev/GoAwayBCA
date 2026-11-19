import hashlib
from pathlib import Path
import struct
import unittest
import uuid
import boot_diagnostic_binding as binding

class BindingTests(unittest.TestCase):
    def config(self,sector=512):
        return binding.configuration(*[str(uuid.uuid4()) for _ in range(4)],sector,'11'*32,'22'*32,'MacBookPro11,4')

    def template(self):
        data=bytearray(1024);data[:2]=b'MZ';struct.pack_into('<I',data,60,128)
        data[128:132]=b'PE\0\0';struct.pack_into('<H',data,132,0x8664)
        struct.pack_into('<H',data,152,0x20b);struct.pack_into('<H',data,220,10)
        data[512:544]=binding.MAGIC
        return bytes(data)

    def test_geometry_and_binding_checksum(self):
        for sector in (512,4096):
            config=self.config(sector);result=binding.bind(self.template(),config)
            self.assertEqual(result[:512],self.template()[:512])
            self.assertEqual(result[512:],config)
            self.assertEqual(struct.unpack_from('<I',config,36)[0],sector)
            self.assertEqual(config[168:200],hashlib.sha256(config[:168]+config[200:]).digest())

    def test_reject_tamper_duplicate_and_rebinding(self):
        config=self.config();bad=bytearray(config);bad[88]^=1
        for template,data in ((self.template(),bytes(bad)),(self.template()+binding.MAGIC+bytes(480),config),
                              (binding.bind(self.template(),config),config)):
            with self.assertRaises(ValueError):binding.bind(template,data)

    def test_wrong_architecture_or_file_type(self):
        for offset,value in ((132,0x14c),(152,0x10b),(220,3)):
            template=bytearray(self.template());struct.pack_into('<H',template,offset,value)
            with self.assertRaises(ValueError):binding.bind(bytes(template),self.config())

    def test_real_compiled_template_has_one_binding(self):
        path=Path(__file__).parent/'diagnostic-efi/WindowsDiagnostics.template.efi'
        if path.is_file():self.assertEqual(len(binding.bind(path.read_bytes(),self.config(4096))),path.stat().st_size)

if __name__=='__main__':unittest.main()
