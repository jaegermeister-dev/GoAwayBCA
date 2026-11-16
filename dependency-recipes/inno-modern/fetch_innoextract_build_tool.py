"""Fetch only the pinned official CMake developer tool, without installing it."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

NAME = 'cmake-3.31.8-macos-universal.tar.gz'
URL = 'https://github.com/Kitware/CMake/releases/download/v3.31.8/' + NAME
SHA256 = 'd1449f969c54d5c00886d5b643340d493dfb3c81cb39ee29b35453395c11ebf7'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    folder = args.directory.resolve(strict=True)
    assert folder.is_dir()
    destination = folder / NAME
    if destination.exists() or destination.is_symlink():
        assert destination.is_file() and not destination.is_symlink() and digest(destination) == SHA256
        print('Pinned CMake archive already present and verified.')
        return
    part = folder / (NAME + '.part')
    # Preserve any interrupted download for inspection rather than overwriting it.
    with part.open('xb') as output, urllib.request.urlopen(URL, timeout=60) as response:
        assert response.geturl().startswith('https://')
        total = 0
        while True:
            data = response.read(1024*1024)
            if not data:
                break
            total += len(data)
            assert total <= 180000000
            output.write(data)
    assert digest(part) == SHA256, 'Checksum mismatch; untrusted download retained as .part'
    assert not destination.exists() and not destination.is_symlink()
    part.rename(destination)
    print(json.dumps(dict(file=NAME, url=URL, bytes=total, sha256=SHA256,
                          installed=False, developer_tool_only=True)))


if __name__ == '__main__':
    main()
