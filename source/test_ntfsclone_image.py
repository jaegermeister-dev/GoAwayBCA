import io
import struct
import unittest

import ntfsclone_image as n


def fixture(sector=512):
    boot=bytearray(512)
    boot[3:11]=b'NTFS    ';boot[510:512]=b'\x55\xaa'
    struct.pack_into('<H',boot,11,sector);boot[13]=4096//sector
    primary=bytes(boot)+bytes(4096-512)
    alternate=bytes(4096-sector)+bytes(boot)+bytes(sector-512)
    header=n.HEADER.pack(n.MAGIC,10,1,4096,8192,1,1,56)+bytes(6)
    return header+b'\1'+primary+b'\1'+alternate


class Tests(unittest.TestCase):
    def test_both_sector_sizes_and_allocated_readback_offsets(self):
        for sector in (512,4096):
            rows=[]
            result=n.inspect(io.BytesIO(fixture(sector)),8192,sector,lambda offset,data:rows.append((offset,len(data))))
            self.assertEqual(result['logical_sector_size'],sector)
            self.assertEqual(rows,[(0,4096),(4096,4096)])

    def test_conflicting_metadata_rejected_before_restore(self):
        with self.assertRaisesRegex(ValueError,'sizes disagree'):
            n.inspect(io.BytesIO(fixture()),16384,512)
        with self.assertRaisesRegex(ValueError,'sector sizes disagree'):
            n.inspect(io.BytesIO(fixture()),8192,4096)

    def test_truncation_trailing_data_and_bad_alternate(self):
        good=fixture()
        for invalid in (good[:-1],good+b'x',good[:56]+b'\2'+good[57:],good[:-512]+b'X'+good[-511:]):
            with self.assertRaises(ValueError):n.inspect(io.BytesIO(invalid))

    def test_negative_oversized_and_initial_gaps_rejected(self):
        good=fixture()
        for gap in (-1,0,2):
            with self.assertRaises(ValueError):
                n.inspect(io.BytesIO(good[:4153]+b'\0'+struct.pack('<q',gap)+good[4154:]))
        with self.assertRaises(ValueError):
            n.inspect(io.BytesIO(good[:56]+b'\0'+struct.pack('<q',1)+good[56:]))

    def test_header_allocation_count_and_dimensions(self):
        good=fixture()
        for offset,format,value in ((18,'<I',768),(22,'<Q',8193),(30,'<q',-1),(38,'<Q',99),(46,'<I',10)):
            damaged=bytearray(good);struct.pack_into(format,damaged,offset,value)
            with self.assertRaises(ValueError):n.inspect(io.BytesIO(damaged))


if __name__=='__main__':unittest.main()
