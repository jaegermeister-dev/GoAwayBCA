"""Plan additive boot dependencies without replacing shared or protected EFI.

Windows memory diagnostics are not the loader used to start the installation.
An existing diagnostic may belong to another Windows version; leave it in
place, rather than fail deployment or downgrade/replace it. This exception
does NOT apply to loaders, graphics helpers, BCD, fonts or arbitrary files.
This module only plans changes; the existing transaction owns actual writes.
"""
import re
from efi_transaction import safe_relative
from install_windows7 import require
from menu_lifecycle import indexed, sha, ALIASES, CANON


def optional_memory_diagnostic(path):
    path = path.casefold()
    return path == 'efi/microsoft/boot/memtest.efi' or bool(re.fullmatch(
        r'efi/microsoft/boot/[a-z]{2,3}-[a-z0-9]{2,8}/memtest\.efi\.mui', path))


def plan(manifest, dependencies, owned_paths=()):
    originals = indexed(manifest)
    indexed(dependencies)  # FAT path case collisions must not be ambiguous.
    owned = {safe_relative(p).casefold() for p in owned_paths}
    reserved = {p.casefold() for p in ALIASES + (CANON,)}
    changes, preserved = [], []
    for path, data in dependencies.items():
        path = safe_relative(path)  # Explicitly excludes OpenCore and Apple.
        require(path.casefold() not in reserved,
                'Menu-owned boot files must use the menu transaction: ' + path)
        existing = originals.get(path.casefold())
        digest = sha(data)
        if existing is None:
            changes.append(dict(path=path, expected=None, data=data))
        elif existing[1] != digest:
            if optional_memory_diagnostic(path) and path.casefold() not in owned:
                preserved.append(dict(path=existing[0], existing_sha256=existing[1],
                    supplied_sha256=digest, reason='existing-shared-memory-diagnostic'))
            else:
                require(False, 'An existing Windows boot dependency differs and will be preserved: ' + path)
    return dict(changes=changes, preserved=preserved)
