"""Streaming container for an ntfsclone used-cluster image, not a file copy.

No disk access is performed here. The caller supplies ntfsclone --save-image
output from an unmounted, identity-checked partition. That format preserves
NTFS metadata/data while encoding free-space gaps instead of copying them.
Checksums detect damage; they are not a signature or proof of app ownership.
"""
import hashlib
import json
import struct
import zlib

import backup_metadata

MAGIC = b'GoAwayBCA-Backup\x00\x01'
BLOCK = 4 * 1024 * 1024
MAX_METADATA = 16 * 1024 * 1024
RECORD = struct.Struct('<II32s')
END = struct.Struct('<QQ32s')
MAX_STREAM = 64 * 1024**4


def require(value, message):
    if not value:
        raise ValueError(message)


def exact(stream, length):
    parts = []
    while length:
        part = stream.read(length)
        require(part, 'The Windows backup is incomplete or truncated.')
        require(len(part) <= length, 'Invalid backup input stream.')
        parts.append(part)
        length -= len(part)
    return b''.join(parts)


def write_all(stream, data):
    view = memoryview(data)
    while view:
        count = stream.write(view)
        require(type(count) is int and 0 < count <= len(view), 'Backup output write did not complete.')
        view = view[count:]


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate field in Windows backup metadata.')
        result[key] = value
    return result


def header(stream):
    require(exact(stream, len(MAGIC)) == MAGIC, 'Not a supported GoAwayBCA Windows backup.')
    length, = struct.unpack('<I', exact(stream, 4))
    require(0 < length <= MAX_METADATA, 'Windows backup metadata is too large.')
    encoded = exact(stream, length)
    require(exact(stream, 32) == hashlib.sha256(encoded).digest(), 'Windows backup metadata checksum failed.')
    value = json.loads(encoded.decode('utf-8'), object_pairs_hook=unique_object)
    require(type(value) is dict and set(value) == {'image_format', 'metadata'}, 'Invalid Windows backup header.')
    require(value['image_format'] == 'ntfsclone-save-image', 'Unknown Windows backup image format.')
    backup_metadata.validate(value['metadata'])
    return value['metadata'], hashlib.sha256(encoded)


def capture(source, output, metadata, progress=lambda amount: None, finish=lambda: None):
    """Write the wrapper; finish must verify the producer before the footer.

    A subprocess producer uses finish to check its exit status. A producer
    failure leaves an incomplete archive, never a valid completion record.
    """
    backup_metadata.validate(metadata)
    encoded = backup_metadata.canonical(dict(image_format='ntfsclone-save-image', metadata=metadata))
    require(len(encoded) <= MAX_METADATA, 'Windows backup history is too large.')
    digest = hashlib.sha256(encoded)
    write_all(output, MAGIC + struct.pack('<I', len(encoded)) + encoded + digest.digest())
    count = total = 0
    while True:
        raw = source.read(BLOCK)
        if not raw:
            break
        require(0 < len(raw) <= BLOCK and total + len(raw) <= MAX_STREAM, 'Invalid backup image stream size.')
        packed = zlib.compress(raw, 6)
        write_all(output, RECORD.pack(len(raw), len(packed), hashlib.sha256(raw).digest()))
        write_all(output, packed)
        digest.update(raw)
        total += len(raw)
        count += 1
        progress(total)
    require(total > 0, 'The filesystem backup helper produced no image.')
    finish()
    write_all(output, RECORD.pack(0, 0, bytes(32)))
    write_all(output, END.pack(count, total, digest.digest()))
    return dict(image_bytes=total, chunks=count, content_sha256=digest.hexdigest())


class ImageReader:
    """Bounded, checked image stream for filesystem preflight without a raw copy.

    Only bytes from a checksum-verified chunk are returned. The whole archive
    is verified only at EOF, when result becomes available. Callers must do a
    complete read-only preflight before using a second pass to restore.
    """
    def __init__(self, source, progress=lambda amount: None):
        self.source=source
        self.metadata,self.digest=header(source)
        self.progress=progress
        self.count=self.total=0
        self.buffer=b''
        self.result=None

    def _next(self):
        raw_size,packed_size,expected=RECORD.unpack(exact(self.source,RECORD.size))
        if raw_size==0:
            require(packed_size==0 and expected==bytes(32),'Invalid backup end marker.')
            end_count,end_total,end_digest=END.unpack(exact(self.source,END.size))
            require(self.total>0 and (end_count,end_total,end_digest)==
                    (self.count,self.total,self.digest.digest()),'Windows backup completion checksum failed.')
            require(not self.source.read(1),'Unexpected trailing data in Windows backup.')
            self.result=dict(metadata=self.metadata,image_bytes=self.total,chunks=self.count,
                             content_sha256=self.digest.hexdigest())
            return b''
        require(0<raw_size<=BLOCK and 0<packed_size<=BLOCK+4096,'Invalid Windows backup chunk size.')
        require(self.total+raw_size<=MAX_STREAM,'Windows backup image is too large.')
        decoder=zlib.decompressobj()
        raw=decoder.decompress(exact(self.source,packed_size),raw_size+1)
        require(len(raw)==raw_size and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,
                'Windows backup compressed data is invalid.')
        require(hashlib.sha256(raw).digest()==expected,'Windows backup data checksum failed.')
        self.digest.update(raw);self.total+=len(raw);self.count+=1
        self.progress(self.total)
        return raw

    def read(self, size):
        # The NTFS parser requests at most 2 MiB. Explicit bounds also prevent
        # an accidental read-all of a very large backup into the root worker.
        require(type(size) is int and 0<=size<=BLOCK,'Backup reads must use bounded chunks.')
        parts=[];left=size
        while left:
            if not self.buffer:
                if self.result is not None:break
                self.buffer=self._next()
                if not self.buffer:break
            part=self.buffer[:left];self.buffer=self.buffer[len(part):]
            parts.append(part);left-=len(part)
        return b''.join(parts)


def verify(source, output=None, progress=lambda amount: None):
    """Verify before restore. output, if supplied, receives the image stream.

    Verification with output is intended for a temporary image or an already
    approved ntfsclone process, never direct raw-device writes. The original
    backup must be fully verified before any destination is erased.
    """
    metadata, digest = header(source)
    count = total = 0
    while True:
        raw_size, packed_size, expected = RECORD.unpack(exact(source, RECORD.size))
        if raw_size == 0:
            require(packed_size == 0 and expected == bytes(32), 'Invalid backup end marker.')
            break
        require(0 < raw_size <= BLOCK and 0 < packed_size <= BLOCK + 4096,
                'Invalid Windows backup chunk size.')
        require(total + raw_size <= MAX_STREAM, 'Windows backup image is too large.')
        packed = exact(source, packed_size)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(packed, raw_size + 1)
        require(len(raw) == raw_size and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,
                'Windows backup compressed data is invalid.')
        require(hashlib.sha256(raw).digest() == expected, 'Windows backup data checksum failed.')
        if output is not None:
            write_all(output, raw)
        digest.update(raw)
        total += raw_size
        count += 1
        progress(total)
    end_count, end_total, end_digest = END.unpack(exact(source, END.size))
    require(total > 0 and (end_count, end_total, end_digest) == (count, total, digest.digest()),
            'Windows backup completion checksum failed.')
    require(not source.read(1), 'Unexpected trailing data in Windows backup.')
    return dict(metadata=metadata, image_bytes=total, chunks=count, content_sha256=digest.hexdigest())
