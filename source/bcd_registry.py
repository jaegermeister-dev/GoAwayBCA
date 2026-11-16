"""Bounded, read-only REGF reader for small CLEAN BCD hives.

No registry APIs, log recovery, writing, or implicit activation authority.
Record field definitions cross-checked against hivex 1.3.24's format structs.
This intentionally rejects volatile keys, symbolic links and large-value lists.
"""
import hashlib
import struct

from bcd_store_sanity import clean_hive, require


class Registry:
    def __init__(self, data):
        self.info = clean_hive(data)
        self.data = data
        self.cells = {}
        offset = 4096
        while offset < len(data):
            end = offset + struct.unpack_from('<I', data, offset + 8)[0]
            cell = offset + 32
            while cell < end:
                length = struct.unpack_from('<i', data, cell)[0]
                if length < 0:
                    self.cells[cell - 4096] = data[cell + 4:cell - length]
                cell += abs(length)
            offset = end
        self.keys = {}
        self._seen = set()
        self._walk(struct.unpack_from('<I', data, 0x24)[0], (), None)

    def cell(self, offset, minimum=0):
        require(offset in self.cells, 'Registry reference is not an allocated cell')
        result = self.cells[offset]
        require(len(result) >= minimum, 'Registry cell is too short')
        return result

    @staticmethod
    def number(data, offset):
        require(offset + 4 <= len(data), 'Registry field is out of bounds')
        return struct.unpack_from('<I', data, offset)[0]

    @staticmethod
    def name(data, compressed):
        name = data.decode('latin1' if compressed else 'utf-16le', 'strict')
        require(len(name) <= 255 and not any(ord(x) < 32 or x == '\\' for x in name),
                'Invalid registry name')
        return name

    def indices(self, offset, expected):
        require(0 < expected <= 4096, 'Unexpected subkey count')
        seen = set()
        def expand(index, depth):
            require(index not in seen and depth < 16, 'Cyclic registry subkey list')
            seen.add(index)
            cell = self.cell(index, 4)
            magic, count = cell[:2], struct.unpack_from('<H', cell, 2)[0]
            require(0 < count <= 4096, 'Unexpected index count')
            require(magic in (b'lf', b'lh', b'li', b'ri'), 'Unknown registry subkey list')
            stride = 8 if magic in (b'lf', b'lh') else 4
            require(4 + count * stride <= len(cell), 'Subkey list exceeds its cell')
            result = []
            for i in range(count):
                target = self.number(cell, 4 + i * stride)
                result.extend(expand(target, depth + 1) if magic == b'ri' else [target])
                require(len(result) <= expected, 'Subkey list is larger than declared')
            return result
        result = expand(offset, 0)
        require(len(result) == expected and len(set(result)) == expected, 'Inconsistent subkey list')
        return result

    def _walk(self, offset, path, parent):
        require(offset not in self._seen and len(self._seen) < 8192 and len(path) < 32,
                'Cyclic or oversized registry key tree')
        self._seen.add(offset)
        cell = self.cell(offset, 76)
        require(cell[:2] == b'nk', 'Expected registry key cell')
        flags = struct.unpack_from('<H', cell, 2)[0]
        require(not flags & 0x10, 'Registry link keys are not accepted')
        if parent is not None:
            require(self.number(cell, 16) == parent, 'Incorrect registry key parent')
        name_size = struct.unpack_from('<H', cell, 72)[0]
        require(76 + name_size <= len(cell), 'Registry key name exceeds its cell')
        name = self.name(cell[76:76 + name_size], flags & 0x20)
        if parent is not None:
            require(name, 'Empty registry subkey name')
            path += (name,)
        canonical = tuple(x.casefold() for x in path)
        require(canonical not in self.keys, 'Case-insensitive duplicate registry key')
        count = self.number(cell, 36)
        require(count <= 4096, 'Oversized registry value list')
        values = {}
        if count:
            indices = self.cell(self.number(cell, 40), count * 4)
            seen_values = set()
            for i in range(count):
                pointer = self.number(indices, i * 4)
                require(pointer not in seen_values, 'Duplicate registry value pointer')
                seen_values.add(pointer)
                value = self.cell(pointer, 20)
                require(value[:2] == b'vk', 'Expected registry value cell')
                length = struct.unpack_from('<H', value, 2)[0]
                require(20 + length <= len(value), 'Registry value name exceeds its cell')
                value_flags = struct.unpack_from('<H', value, 16)[0]
                require(value_flags & ~1 == 0, 'Unexpected registry value flags')
                value_name = self.name(value[20:20 + length], value_flags & 1)
                require(value_name.casefold() not in values, 'Duplicate registry value name')
                raw_size = self.number(value, 4)
                size = raw_size & 0x7fffffff
                require(size <= 16344, 'Large registry values are not accepted in this BCD profile')
                if raw_size & 0x80000000:
                    require(size <= 4, 'Invalid inline registry value length')
                    content = value[8:8 + size]
                elif size:
                    content = self.cell(self.number(value, 8), size)[:size]
                else:
                    content = b''
                values[value_name.casefold()] = (self.number(value, 12), content)
        self.keys[canonical] = values
        require(self.number(cell, 24) == 0, 'Volatile registry subkeys are not accepted')
        children = self.number(cell, 20)
        if children:
            for child in self.indices(self.number(cell, 28), children):
                self._walk(child, path, offset)

    def value(self, path, name):
        key = tuple(x.casefold() for x in path)
        require(key in self.keys and name.casefold() in self.keys[key], 'Required BCD value is absent')
        return self.keys[key][name.casefold()]

    def snapshot(self):
        return {'\\'.join(path): {name: (kind, len(data), hashlib.sha256(data).hexdigest())
                for name, (kind, data) in values.items()} for path, values in self.keys.items()}
