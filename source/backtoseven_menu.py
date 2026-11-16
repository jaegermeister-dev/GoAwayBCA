"""Experimental menu reconciliation. Pure planning; caller backs up and commits.

Existing independent stores retain their settings. Later Windows installers can
replace the public alias/BCD; only a loader identified from an actual discovered
Windows installation, or our exact old selector/helper, may replace that alias.
"""
import json
import struct
from boot_catalog import identity, rebuild
from installation_records import canonical
from chooser_config import encode as menu_encode
from store_configuration import encode as config_encode, verify_stores
from bcd_semantics import identity as verify_bcd
import menu_lifecycle as m
import menu_assets

BASE = 'EFI/BackToSeven/Experimental/'
BASELINE = BASE + 'PreInstall.json'
LATEST = BASE + 'Latest.json'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def previous(files, disk, esp):
    """Validate saved metadata without assuming the public alias stayed ours."""
    values = {k.casefold(): v for k, v in files.items()}
    if m.CATALOG.casefold() not in values:
        require(not any(p.casefold() in values for p in (m.CONFIG, m.STATE, m.MENU)),
                'Incomplete earlier menu; preserve it for recovery')
        return dict(catalog=dict(schema=1, generation=0, entries=[]), stores={}, active=None, generation=0)
    catalog = json.loads(values[m.CATALOG.casefold()].decode('utf-8'))
    if type(catalog) is dict and catalog.get('entries') == []:
        # Removing the final Windows installation intentionally leaves an empty
        # catalog with no active/private stores. It remains valid for reuse.
        return m.inspect(values, disk, esp, repair_menu=True)
    state = values.get(m.STATE.casefold(), b'')
    require(len(state) == 88, 'Missing/invalid earlier active-store record')
    active = struct.unpack_from('<I', state, 16)[0]
    private = values.get((m.PREFIX + 'Private-%d.BCD' % active).casefold())
    require(private is not None, 'Missing earlier active BCD snapshot')
    # inspect checks the configuration, entry ordering, active-record checksum,
    # GPT identity and every private BCD. The current public BCD is assessed
    # separately as potential newer evidence, never discarded before backup.
    values[m.CANON.casefold()] = private
    return m.inspect(values, disk, esp, repair_menu=True)


def reconcile(files, disk, esp, observations, children, stores, chooser, ntfs, *, active=None,ntfs_4kn_driver=None,confirmed_absent=()):
    """children/stores are authenticated by the read-only discovery executor.

    A missing installation stays listed as unavailable. Nothing formats NTFS.
    """
    index = m.indexed(files)
    before = {k.casefold(): (k, m.sha(v)) for k, v in files.items()}
    old = previous(files, disk, esp)
    absent = {identity(p) for p in confirmed_absent}
    # Validate removal evidence against the old generation first. A new
    # installation may legitimately reuse a manually emptied partition UUID.
    base = rebuild(old['catalog'], [], scan_complete=True, confirmed_absent=confirmed_absent) if absent else old['catalog']
    require(m.sha(chooser) == m.CHOOSER_SHA and m.sha(ntfs) == m.NTFS_SHA, 'Unverified menu executables')
    observed = {identity(r['observed']) for r in observations}
    require(set(children) <= observed, 'Menu addition was not found on a Windows partition')
    fresh = []
    kept = set()
    all_stores = dict(old['stores'])
    all_stores.update(stores)
    for entry in old['catalog']['entries']:
        key = identity(entry)
        if key in absent:
            continue
        selected = dict(children.get(key, entry), available=key in observed)
        fresh.append(selected)
        kept.add(key)
    fresh += [dict(e, available=True) for key, e in children.items() if key not in kept]
    catalog = rebuild(base, fresh, scan_complete=True)
    entries = catalog['entries']
    for entry in entries:
        entry.pop('_files', None)
        entry.pop('public_loader_hashes', None)
    require(entries and any(e['available'] for e in entries), 'No available Windows installation to boot')
    keys = [identity(e) for e in entries]
    require(set(keys) <= set(all_stores), 'Missing independent BCD; preserve current boot configuration')
    menu = menu_encode(entries)
    config = config_encode(menu, disk, esp, [dict(disk_guid=e['disk_guid'], partition_guid=e['partition_guid']) for e in entries])
    verify_stores(config, menu, [all_stores[k] for k in keys])
    available = {identity(e) for e in entries if e['available']}
    active = active or (old['active'] if old['active'] in available else next(k for k in keys if k in available))
    require(active in available, 'Requested active installation is unavailable')
    generation = old['generation'] + 1
    body = b'W7DEPS01' + struct.pack('<QI', generation, keys.index(active)) + bytes(4) + bytes.fromhex(m.sha(config))
    desired = {m.CATALOG: canonical(catalog)+b'\n', m.MENU: menu, m.CONFIG: config,
               m.STATE: body + bytes.fromhex(m.sha(body)), m.CANON: all_stores[active],
               m.BASE+'OpenNtfsDxe.efi': ntfs}
    if ntfs_4kn_driver is not None:
        require(m.sha(ntfs_4kn_driver)==m.NTFS_4KN_SHA,'Unverified 4Kn reader payload')
        path=m.BASE+'Ntfs4Kn.efi'
        require(before.get(path.casefold(),(None,m.NTFS_4KN_SHA))[1] in m.PRIOR_NTFS_4KN_SHAS | {m.NTFS_4KN_SHA},'Unexpected existing 4Kn EFI reader')
        desired[path]=ntfs_4kn_driver
    desired.update({m.PREFIX+'Private-%d.BCD'%i: all_stores[k] for i, k in enumerate(keys)})
    known_public = {m.CHOOSER_SHA, *m.PRIOR_CHOOSER_SHAS}
    for child in children.values():
        known_public.update(child.get('public_loader_hashes', []))
    for path in m.ALIASES:
        old_hash = before.get(path.casefold(), (None, None))[1]
        if path == 'EFI/Boot/BOOTX64.EFI' and old_hash is not None and old_hash not in known_public:
            continue  # OpenCore/another loader can own the fallback; keep it.
        require(old_hash is None or old_hash in known_public,
                'Unrecognised public boot loader retained: '+path)
        desired[path] = chooser
    for entry in entries:
        key = identity(entry)
        if key in children:
            for path, data in children[key].get('_files', {}).items():
                require(path.startswith(('EFI/BackToSeven/Children/', 'EFI/W7DEP-WIN7-AUTO/')) or
                        path == 'EFI/Microsoft/Boot/bootmgfw-w7dep-original.efi', 'Unexpected child boot path')
                prior = before.get(path.casefold(), (None, None))[1]
                if prior not in (None, m.sha(data)):
                    require(path.startswith('EFI/BackToSeven/Children/') and path.endswith('/BCD'),
                            'Existing child loader changed; preserve it: '+path)
                    verify_bcd(index[path.casefold()][1], disk, esp, entry['disk_guid'],entry['partition_guid'])
                desired[path] = data
    # Private copies created during import have their own ownership receipts.
    # They do not claim ownership of the imported Windows installation itself.
    desired.update(menu_assets.record_copies(files, disk, esp, children))
    # No runtime-only fields or embedded bytes in the persistent catalog.
    for e in catalog['entries']:
        e.pop('_files', None)
        e.pop('public_loader_hashes', None)
    desired[m.CATALOG] = canonical(catalog)+b'\n'
    if before.get(m.CANON.casefold(), (None, None))[1] != m.sha(desired[m.CANON]):
        for suffix in ('.LOG', '.LOG1', '.LOG2'):
            if (m.CANON+suffix).casefold() in before:
                desired[before[(m.CANON+suffix).casefold()][0]] = None
    # Stale numbered private stores must not survive a reordering/reduction.
    for path in files:
        if path.casefold().startswith(m.PREFIX.casefold()+'private-') and path.casefold().endswith('.bcd'):
            if path.casefold() not in {p.casefold() for p in desired}:
                desired[path] = None
    changes = []
    for path, data in desired.items():
        actual, old_hash = before.get(path.casefold(), (path, None))
        if (m.sha(data) if data is not None else None) == old_hash:
            continue
        changes.append(dict(path=actual, expected=old_hash, data=data))
    changes.sort(key=lambda c: (c['path'].casefold() == m.MENU.casefold(), c['path'] in m.ALIASES))
    # Verify the resulting on-disk view using the same consumer as normal menu
    # maintenance, before giving any writes to the transaction executor.
    final = {k.casefold(): v for k, v in files.items()}
    for change in changes:
        if change['data'] is None:
            final.pop(change['path'].casefold(), None)
        else:
            final[change['path'].casefold()] = change['data']
    m.inspect(final, disk, esp)
    return dict(changes=changes, catalog=catalog, active=active, preserved=[], stale_files=[])
