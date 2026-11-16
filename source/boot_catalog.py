"""Pure planning for independent Windows chooser entries. No disk/EFI I/O.

Not wired to the first-Windows-7 installer. A scanner must supply verified
observations; a future transaction layer must journal and verify publication.
No BCD merging, partition operations, defaults or OpenCore changes live here.
"""
import copy
import re
import uuid


class CatalogRefusal(ValueError):
    pass


def require(value, message):
    if not value:
        raise CatalogRefusal(message)


def guid(value):
    require(isinstance(value, str), 'Expected a GUID string')
    try:
        identifier = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise CatalogRefusal('Invalid GUID')
    require(identifier.int != 0, 'Zero GUID is not an installation identity')
    return str(identifier)


def identity(record):
    return guid(record['disk_guid']) + ':' + guid(record['partition_guid'])


def efi_path(path):
    require(isinstance(path, str) and path and '\\' not in path and ':' not in path,
            'Expected a relative EFI path')
    parts = path.split('/')
    require(len(parts) >= 3 and parts[0] == 'EFI' and
            all(p and p not in ('.', '..') and not p.endswith((' ', '.')) and
                not any(ord(c) < 32 or c in '*?<>|"' for c in p) for p in parts),
            'Unsafe EFI path')
    return path


def entry(record):
    value = copy.deepcopy(record)
    value['id'] = identity(value)
    value['disk_guid'], value['partition_guid'] = value['id'].split(':')
    value['esp_guid'] = guid(value['esp_guid'])
    value['boot_partition_guid'] = guid(value.get('boot_partition_guid', value['esp_guid']))
    require(value['boot_partition_guid'] in (value['esp_guid'], value['partition_guid']),
            'Child boot files must belong to its ESP or Windows partition')
    require(isinstance(value.get('title'), str) and 0 < len(value['title']) <= 128
            and all(ord(c) >= 32 for c in value['title']), 'Invalid menu title')
    require(value.get('single_os_bcd_verified') is True,
            'Each child must have a verified independent single-OS BCD')
    for field in ('loader_path', 'bcd_path'):
        value[field] = efi_path(value[field])
    require(value['loader_path'].casefold().endswith('.efi') and
            value['bcd_path'].casefold().endswith('/bcd'), 'Invalid loader/BCD paths')
    require(type(value.get('available')) is bool, 'Availability must be explicit')
    return value


def rebuild(previous, observations, *, scan_complete, completed_format=None, confirmed_absent=()):
    """Return a new generation, never publish it or infer deletion from absence.

    completed_format identifies a target already confirmed erased by a future
    transaction. Before-format intent alone is not accepted as a completion.
    Unobserved previous entries are retained, disabled, and explicitly labelled
    unavailable until discovery can resolve them; other entries are not deleted.
    """
    require(scan_complete is True, 'Incomplete scan: keep the previous menu unchanged')
    require(previous.get('schema') == 1 and type(previous.get('generation')) is int
            and previous['generation'] >= 0, 'Invalid catalog schema/generation')
    old = {}
    for record in previous['entries']:
        item = entry(record)
        require(item['id'] not in old, 'Duplicate previous installation identity')
        old[item['id']] = item
    fresh = {}
    for record in observations:
        item = entry(record)
        require(item['id'] not in fresh, 'Ambiguous duplicate discovered GUIDs')
        fresh[item['id']] = item
    removed = None
    absent = set()
    for proof in confirmed_absent:
        key = identity(proof)
        require(proof.get('status') == 'confirmed-absent' and
                proof.get('reason') in ('partition-removed', 'windows-directory-absent', 'partition-reformatted') and
                re.fullmatch('[0-9a-f]{64}', proof.get('layout_digest', '')),
                'External removal needs positive same-disk evidence')
        require(key in old and key not in fresh and key not in absent,
                'External removal conflicts with current Windows discovery')
        absent.add(key)
    if completed_format is not None:
        require(completed_format.get('status') == 'format-complete',
                'Do not remove an entry based on an uncompleted format request')
        removed = identity(completed_format)
        require(removed in old, 'Formatting receipt does not identify a known installation')
        require(removed not in fresh, 'Stale scan still advertises the formatted installation')
    merged = {}
    for key, item in old.items():
        if key == removed or key in absent:
            continue
        if key not in fresh:
            item['available'] = False
            item['discovery_status'] = 'unavailable-not-deleted'
        merged[key] = item
    merged.update(fresh)
    occupied = {}
    for item in merged.values():
        for field in ('loader_path', 'bcd_path'):
            location = (item['boot_partition_guid'], item[field].casefold())
            require(location not in occupied, 'Shared loader/BCD needs explicit resolution')
            occupied[location] = item['id']
    return dict(schema=1, generation=previous['generation'] + 1,
                public_name='Windows', set_default_boot=False,
                entries=sorted(merged.values(), key=lambda item: (item['title'].casefold(), item['id'])))


def cleanup_plan(selected, ownership, observed_hashes, backup_hashes, referenced_paths):
    """Return only individually owned, unchanged, backed-up paths. Never delete.

    Maps use (ESP GUID, relative path). The future publisher must reread identity
    and hashes immediately before each operation; this plan is not authorisation.
    """
    selected_id = identity(selected)
    disk, part = selected_id.split(':')
    esp = guid(selected['esp_guid'])
    prefix = 'EFI/WindowsDeployer/installs/' + disk + '/' + part + '/'
    references = {(guid(device), efi_path(path).casefold()) for device,path in referenced_paths}
    result, seen = [], set()
    for asset in ownership:
        if asset.get('owner') != selected_id:
            continue
        path = efi_path(asset['path'])
        device = guid(asset['esp_guid'])
        key = (device, path)
        canonical = (device, path.casefold())
        require(device == esp and path.startswith(prefix), 'Asset is outside selected owned namespace')
        require(canonical not in seen, 'Duplicate/case-colliding asset')
        seen.add(canonical)
        require(canonical not in references, 'Asset is still referenced by another installation')
        expected = asset.get('sha256')
        require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected), 'Invalid asset hash')
        require(observed_hashes.get(key) == expected, 'Owned asset changed or could not be read')
        require(backup_hashes.get(key) == expected, 'No verified backup of the owned asset')
        result.append(dict(esp_guid=device, path=path, sha256=expected))
    return result
