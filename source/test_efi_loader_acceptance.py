"""Boot-manager versions never select acceptance or installed OS identity."""
import struct
import unittest
from test_fast_discovery import loader
from windows_discovery import validate_efi_application, loader_version


def application(version=(11,1,7601,26022)):
    data=bytearray(loader(version or (0,0,0,0)))
    struct.pack_into('<H',data,132,0x8664)
    struct.pack_into('<H',data,152,0x20b)
    struct.pack_into('<H',data,220,10)
    if version is None:
        data[1024:1536]=bytes(512)
    return bytes(data)


def installed_loader(index):
    return application(((11,1,7601,26022),(6,3,9600,17211),(10,0,19045,1))[index])


def os_loader(index):
    data=bytearray(application(((6,1,7601,17514),(6,3,9600,17238),(10,0,19045,1))[index]))
    struct.pack_into('<H',data,220,16)  # Windows boot application, not an EFI app.
    return bytes(data)


class Acceptance(unittest.TestCase):
    def test_no_release_whitelist_or_required_version_resource(self):
        for version in ((11,1,7601,26022),(6,1,7601,1),(6,3,9600,1),
                        (10,0,26100,1),(99,42,1234,0),None):
            with self.subTest(version=version):
                data=application(version)
                self.assertEqual(loader_version(data),version)
                self.assertEqual(validate_efi_application(data)['machine'],'x64')

    def test_actual_structure_still_matters(self):
        bad=[b'not an EFI executable',application()[:150],application()[:1300],os_loader(0)]
        for offset,value in ((132,0x14c),(132,0xaa64),(152,0x10b),(220,3),(220,11)):
            data=bytearray(application());struct.pack_into('<H',data,offset,value);bad.append(bytes(data))
        for data in bad:
            with self.subTest(header=data[:64]):
                with self.assertRaises(ValueError):validate_efi_application(data)


if __name__=='__main__':unittest.main()
