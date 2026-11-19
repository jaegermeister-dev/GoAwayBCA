"""Validate the pinned ntfsclone save-image stream before a restore writes.

Only full 10.1 save images are accepted, not metadata-only, rescue or old
host-endian images. No raw-device access or restore is performed here.
Format reference: ntfs-3g 2026.9.28 ntfsprogs/ntfsclone.c.
"""
import struct

from backup_archive import exact, require

HEADER = struct.Struct('<16sBBIQqQI')
MAGIC = b'\0ntfsclone-image'


def inspect(stream, expected_bytes=None, expected_sector=None, readback=None):
    magic, major, minor, cluster, size, clusters, used, offset = HEADER.unpack(exact(stream, HEADER.size))
    require(magic == MAGIC and (major,minor) == (10,1), 'Unsupported NTFS backup image format.')
    require(512 <= cluster <= 2*1024**2 and cluster & (cluster-1) == 0, 'Invalid NTFS cluster size.')
    require(512 <= size <= 64*1024**4 and size % 512 == 0, 'Invalid NTFS source partition size.')
    require(0 < clusters and 0 < size-clusters*cluster <= cluster, 'NTFS image geometry is inconsistent.')
    require(0 < used <= clusters and HEADER.size <= offset <= 4096, 'Invalid NTFS image header.')
    require(expected_bytes is None or size == expected_bytes, 'NTFS image and backup partition sizes disagree.')
    exact(stream, offset-HEADER.size)
    position = count = 0
    sector = None
    first_boot = None
    while position <= clusters:
        command = exact(stream,1)
        if command == b'\0':
            gap, = struct.unpack('<q', exact(stream,8))
            require(position > 0 and gap > 0 and position+gap <= clusters,
                    'NTFS backup contains an invalid free-space gap.')
            position += gap
        elif command == b'\1':
            length = min(cluster, size-position*cluster)
            data = exact(stream,length)
            if position == 0:
                require(data[3:11] == b'NTFS    ' and data[510:512] == b'\x55\xaa',
                        'NTFS backup boot sector is invalid.')
                sector, = struct.unpack_from('<H',data,11)
                require(sector in (512,4096) and cluster >= sector and cluster % sector == 0,
                        'Unsupported NTFS backup sector size.')
                sectors_per_cluster = data[13]
                actual_cluster = (sectors_per_cluster if sectors_per_cluster <= 128 else 1 << (256-sectors_per_cluster))*sector
                require(actual_cluster == cluster, 'NTFS backup cluster size disagrees with its boot sector.')
                require(expected_sector is None or sector == expected_sector,
                        'NTFS image and backup logical sector sizes disagree.')
                first_boot=data[:512]
            if position == clusters:
                require(first_boot is not None and length >= sector and data[-sector:][:512] == first_boot,
                        'NTFS backup alternate boot sector is missing or inconsistent.')
            if readback is not None:
                readback(position*cluster,data)
            position += 1
            count += 1
        else:
            raise ValueError('Invalid command in NTFS backup image.')
    require(count == used+1, 'NTFS backup allocated-cluster count does not match its header.')
    require(not stream.read(1), 'Unexpected trailing NTFS image data.')
    return dict(bytes=size,logical_sector_size=sector,cluster_size=cluster,
                allocated_clusters=used,allocated_bytes=used*cluster)
