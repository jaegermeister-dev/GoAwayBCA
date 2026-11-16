"""Ownership of private copies created by menu import, separate from Windows.

Only two exact files beneath the selected partition's private child directory
can be owned here. Existing unrecorded files are never adopted just because
their names or bytes match. Reset preserves these receipts for later cleanup.
"""
import hashlib
import json
import re
from boot_catalog import guid
from installation_records import canonical

BASE = 'EFI/WinDeploy/MenuAssets/'
CHILDREN = 'EFI/BackToSeven/Children/'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def indexed(values):
    result = {}
    for path, value in values.items():
        if path.casefold() in result:
            raise ValueError('Case-colliding menu asset paths')
        result[path.casefold()] = (path, value)
    return result


def receipt_path(partition):
    return BASE + guid(partition) + '.json'


def allowed_paths(partition):
    prefix = CHILDREN + guid(partition) + '/'
    return {prefix + 'BCD', prefix + 'bootmgfw.efi'}


def decode(data, disk, esp, partition):
    if len(data) > 16*1024:
        raise ValueError('Oversized private menu asset receipt')
    value = json.loads(data.decode('utf-8'))
    if (type(value) is not dict or value.get('schema') != 1 or
            value.get('creator') != 'WinDeployMenu' or
            value.get('disk_guid') != guid(disk) or
            value.get('esp_guid') != guid(esp) or
            value.get('partition_guid') != guid(partition)):
        raise ValueError('Private menu asset receipt identity mismatch')
    assets = value.get('assets')
    if type(assets) is not dict or not assets or not set(assets) <= allowed_paths(partition):
        raise ValueError('Private menu asset receipt escapes selected child namespace')
    if any(not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h) for h in assets.values()):
        raise ValueError('Invalid private menu asset digest')
    return value


def record_copies(files, disk, esp, children):
    """Return receipts only for new copies or changes to previously owned copies."""
    before = indexed(files)
    desired = {}
    for child in children.values():
        partition = guid(child['partition_guid'])
        if guid(child['disk_guid']) != guid(disk) or guid(child['esp_guid']) != guid(esp):
            raise ValueError('Menu child belongs to a different disk or EFI partition')
        path = receipt_path(partition)
        prior = before.get(path.casefold())
        if prior:
            try:
                record = decode(prior[1], disk, esp, partition)
            except (ValueError, TypeError, UnicodeError):
                # A damaged receipt is recovery evidence, not permission to
                # overwrite it or adopt existing files. Rebuild can still work.
                continue
        else:
            record = dict(schema=1, creator='WinDeployMenu', disk_guid=guid(disk),
                          esp_guid=guid(esp), partition_guid=partition, assets={})
        assets = dict(record['assets'])
        for name, data in child.get('_files', {}).items():
            if name not in allowed_paths(partition):
                continue
            current = before.get(name.casefold())
            if current is None or assets.get(name) == sha(current[1]):
                assets[name] = sha(data)
        if assets and (prior is None or assets != record['assets']):
            desired[path] = canonical(dict(record, assets=assets)) + b'\n'
    return desired


def cleanup(files, manifest, disk, esp, partition, references, unknown_neighbours=False):
    """Plan exact hash-checked removals; uncertain ownership remains a warning."""
    existing = indexed(manifest)
    contents = indexed(files)
    prefix = (CHILDREN + guid(partition) + '/').casefold()
    path = receipt_path(partition)
    receipt = existing.get(path.casefold())
    retained = {name for name in manifest if name.casefold().startswith(prefix)}
    if receipt:
        retained.add(receipt[0])
    raw = contents.get(path.casefold())
    if not receipt or not raw or sha(raw[1]) != receipt[1] or unknown_neighbours:
        return dict(changes=[], retained=sorted(retained))
    try:
        record = decode(raw[1], disk, esp, partition)
    except (ValueError, TypeError, UnicodeError):
        return dict(changes=[], retained=sorted(retained))
    changes = []
    references = {(guid(device), name.casefold()) for device, name in references}
    for name, digest in record['assets'].items():
        current = existing.get(name.casefold())
        if current and current[1] == digest and (guid(esp), name.casefold()) not in references:
            changes.append(dict(path=current[0], expected=digest, data=None))
            retained.discard(current[0])
    # Keep the receipt with any remaining/changed/shared private files so that
    # the report and backup still explain what was created by the application.
    if retained == {receipt[0]}:
        changes.append(dict(path=receipt[0], expected=receipt[1], data=None))
        retained.clear()
    return dict(changes=changes, retained=sorted(retained))
