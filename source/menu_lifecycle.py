"""Verified independent-store migration for installation additions/removals.

Pure planning except bounded snapshot reads. Executor owns confirmation, data
formatting, backups and the journalled EFI transaction. Never merges BCDs.
"""
import hashlib
import json
from pathlib import Path
import re
import struct
import menu_assets
from boot_catalog import rebuild, identity, guid
from chooser_config import encode as menu_encode
from installation_records import canonical
from store_configuration import encode as config_encode, verify_stores
from bcd_semantics import identity as verify_bcd

BASE = 'EFI/WinDeploy/'
PREFIX = BASE + 'SystemStores/'
CATALOG = BASE + 'catalog.json'
MENU = BASE + 'menu.bin'
CANON = 'EFI/Microsoft/Boot/BCD'
CONFIG = PREFIX + 'Configuration.bin'
STATE = PREFIX + 'Active.bin'
ALIASES = (BASE + 'Windows.efi', 'EFI/Microsoft/Boot/bootmgfw.efi', 'EFI/Boot/BOOTX64.EFI')
CHOOSER_SHA = 'f581308710fbf74d260496f41300581fb4179c289021e0938e3aef276171247b'
OLD_CHOOSER_SHA = '6e09756631c7cb94914723f84403e902892b5a16fc8784023d96c9e1ab29bee7'
PRIOR_CHOOSER_SHAS = frozenset((OLD_CHOOSER_SHA,
    'f00a1114201cca0e23f5e751789a1671215101d56d6701d3fc37e4dd0c90b21d',
    '3138e9c51d291be0ad25e8e06ae6ed05b85295cb2c83f12b7239c506acaca49c',
    '96c358eed054e5c2a12307e47add7fd5b4f091fdf48be26950bc2cad671f4528',
    'f77bc2f4d73bed564fdeb423c8707aeea383e674f88a9621a482901c40bfdaa9',
    '9b60ae395259a0171f0d78b3b2e8efd4a59e5aac36e1c67be615ea1f48a2485c'))
NTFS_4KN_SHA = 'be98c861c709fa5db0819337ea1e40e4e11ed9037f8f37497a6e4274973c2ed7'
PRIOR_NTFS_4KN_SHAS = frozenset(('202aeb2cc078693e9c03f3c0a64e8c6c44f2c75eded8a32ada5e1589753b3317',))
NTFS_SHA = '463bf0b24d58f21d3a9d594a7a4fa68310e1b59e376de0bb5b2dcfb1948d1734'

def require(ok, text):
    if not ok: raise ValueError(text)

def sha(data): return hashlib.sha256(data).hexdigest()

def snapshot(root, manifest):
    """Read only lifecycle files from a previously fingerprinted ESP/backup."""
    root = Path(root).resolve()
    result = {}
    for name, expected in manifest.items():
        folded = name.casefold()
        if (folded in {p.casefold() for p in (CATALOG,MENU,CANON)} or
                folded.startswith(PREFIX.casefold()) or
                folded.startswith(menu_assets.BASE.casefold()) or
                re.fullmatch(r'efi/backtoseven/children/[0-9a-f-]{36}/bcd', folded)):
            from efi_transaction import node
            path = node(root, name)
            require(not path.is_symlink() and path.is_file() and path.stat().st_size <= 16*1024**2,
                    'Invalid lifecycle file: ' + name)
            data = path.read_bytes()
            require(sha(data) == expected, 'Lifecycle file changed: ' + name)
            result[name] = data
    return result

def indexed(values):
    result = {}
    for path, value in values.items():
        key = path.casefold()
        require(key not in result, 'Case-colliding EFI paths')
        result[key] = (path, value)
    return result

def inspect(files, esp_disk, esp_guid, *, repair_menu=False, for_removal=False):
    indexed(files)  # Reject case collisions before normalising.
    files = {k.casefold():v for k,v in files.items()}
    has_catalog = CATALOG.casefold() in files
    require(has_catalog == (MENU.casefold() in files) or
            (repair_menu and has_catalog), 'Incomplete chooser metadata')
    catalog = json.loads(files[CATALOG.casefold()].decode()) if has_catalog else dict(schema=1,generation=0,entries=[])
    entries = catalog['entries']
    if has_catalog:
        expected_menu = menu_encode(entries)
        if repair_menu:
            # Only the redundant rendered menu is recoverable here. The
            # configuration and active-record hashes must still authenticate
            # these exact entries; all BCD identity checks below stay intact.
            files[MENU.casefold()] = expected_menu
        else:require(expected_menu == files[MENU.casefold()], 'Catalog/menu mismatch')
    stores = {}
    for path in files:
        if path.startswith(PREFIX.casefold()):
            leaf = path[len(PREFIX):]
            require(leaf in ('configuration.bin','active.bin') or
                    re.fullmatch(r'private-([0-9]|1[0-5])\.bcd',leaf) or
                    re.fullmatch(r'log-[0-9a-f]{16}-[0-9]+-[012]\.bin',leaf),
                    'Pending or unknown store-switch files; inspect recovery journal before changing the menu')
    if not entries:
        require(not any(p in files for p in (CONFIG.casefold(),STATE.casefold())) and
                not any(p.startswith((PREFIX+'Private-').casefold()) for p in files),
                'Empty menu has unexpected active/private boot stores')
        return dict(catalog=catalog,stores=stores,active=None,generation=0,existing=has_catalog)
    require(has_catalog, 'Store configuration without menu')
    private = [files[(PREFIX+'Private-%d.BCD'%i).casefold()] for i in range(len(entries))]
    require({p for p in files if re.fullmatch(re.escape(PREFIX.casefold())+r'private-\d+\.bcd',p)} ==
            {(PREFIX+'Private-%d.BCD'%i).casefold() for i in range(len(entries))},'Unexpected extra private BCD files')
    verified = verify_stores(files[CONFIG.casefold()],files[MENU.casefold()],private,for_removal=for_removal)
    config = verified['configuration']
    require(config['esp_disk_guid'] == guid(esp_disk) and config['esp_partition_guid'] == guid(esp_guid),
            'Store configuration refers to another EFI partition')
    state = files[STATE.casefold()]
    require(len(state)==88 and state[:8]==b'W7DEPS01' and state[20:24]==bytes(4) and
            hashlib.sha256(state[:-32]).digest()==state[-32:] and
            state[24:56]==hashlib.sha256(files[CONFIG.casefold()]).digest(), 'Invalid active-store record')
    generation, active = struct.unpack_from('<QI',state,8)
    require(active < len(entries) and generation < 2**64-2, 'Invalid active-store position/generation')
    for entry, data in zip(entries,private): stores[identity(entry)] = data
    active_entry = entries[active]
    verify_bcd(files[CANON.casefold()],esp_disk,esp_guid,active_entry['disk_guid'],active_entry['partition_guid'],
               for_removal=for_removal)
    # Windows may have serviced its active BCD after the last chooser switch.
    # The live clean store supersedes its older private snapshot, not vice versa.
    stores[identity(active_entry)] = files[CANON.casefold()]
    return dict(catalog=catalog,stores=stores,active=identity(active_entry),generation=generation,existing=True)

def transition(files, manifest, esp_disk, esp_guid, observations, *, remove=None,
               addition=None, new_store=None, chooser=None, ntfs_driver=None,rename=None,
               rebuild_menu=False,ntfs_4kn_driver=None,confirmed_absent=()):
    require(not rebuild_menu or (remove is None and addition is None and rename is None),
            'Rebuilding the menu is a separate, non-formatting operation')
    # Removing an installation does not execute its recovery image. Prove
    # same-volume ownership without demanding the firmware's path whitelist.
    # Addition, rename, rebuild and firmware boot retain the existing policy.
    for_removal = remove is not None
    old = inspect(files,esp_disk,esp_guid,repair_menu=rebuild_menu,for_removal=for_removal)
    require(not rebuild_menu or old['existing'], 'No existing WinDeploy catalog to rebuild')
    original = indexed(manifest)
    require(all(original.get(k.casefold(),(None,None))[1] == sha(v) for k,v in files.items()),
            'Snapshot/manifest mismatch')
    entries = old['catalog']['entries']
    found = {identity(row['observed']) for row in observations}
    absent = {identity(proof) for proof in confirmed_absent}
    require(not absent.intersection(found), 'Removed Windows was also observed as installed')
    fresh = [dict(entry,available=identity(entry) in found) for entry in entries
             if identity(entry)!=remove and identity(entry) not in absent]
    if rename is not None:
        from entry_names import validate
        require(remove is None and addition is None,'Rename is a separate operation')
        key,title=rename
        matches=[e for e in fresh if identity(e)==key]
        require(len(matches)==1,'Selected installation is not in this WinDeploy picker')
        matches[0]['title']=validate(title)
    completed = None
    if remove is not None and any(identity(e)==remove for e in entries):
        disk,part = remove.split(':')
        completed=dict(disk_guid=disk,partition_guid=part,status='format-complete')
    stores=dict(old['stores'])
    if addition is not None:
        key=identity(addition)
        require((key not in stores or key in absent) and key!=remove,'Installation already exists in the chooser; use Manage Installations first')
        verify_bcd(new_store,esp_disk,esp_guid,addition['disk_guid'],addition['partition_guid'])
        stores[key]=new_store;fresh.append(addition)
    # Reusing a positively empty partition creates a new installation with the
    # same partition identity; validate removal before adding its new entry.
    base = old['catalog']
    if addition is not None and identity(addition) in absent:
        base = rebuild(base, [], scan_complete=True, confirmed_absent=confirmed_absent)
        confirmed_absent = ()
    catalog=rebuild(base,fresh,scan_complete=True,completed_format=completed,confirmed_absent=confirmed_absent)
    desired={CATALOG:canonical(catalog)+b'\n',MENU:menu_encode(catalog['entries'])}
    keys=[identity(e) for e in catalog['entries']]
    old_active=old['active']
    active=old_active if old_active in keys else (keys[0] if keys else None)
    canonical_changed = active != old_active or (addition is not None and
        identity(addition) == old_active and identity(addition) in absent)
    if keys:
        config=config_encode(desired[MENU],esp_disk,esp_guid,
            [dict(disk_guid=e['disk_guid'],partition_guid=e['partition_guid']) for e in catalog['entries']])
        verify_stores(config,desired[MENU],[stores[k] for k in keys],for_removal=for_removal)
        desired.update({PREFIX+'Private-%d.BCD'%i:stores[k] for i,k in enumerate(keys)})
        desired[CONFIG]=config
        body=b'W7DEPS01'+struct.pack('<QI',old['generation']+1,keys.index(active))+bytes(4)+hashlib.sha256(config).digest()
        desired[STATE]=body+hashlib.sha256(body).digest()
        if canonical_changed:
            # Only an absent canonical store or one proven to be ours can be changed.
            require(old['active'] is not None or CANON.casefold() not in original,
                    'Existing non-WinDeploy system boot store must be preserved')
            desired[CANON]=stores[active]
    elif old_active is not None:
        desired[CANON]=None
    # Old-generation private/config files are no longer allowed to name an
    # erased installation. Retained log archives stay as diagnostic evidence.
    for name in files:
        leaf=name[len(PREFIX):] if name.casefold().startswith(PREFIX.casefold()) else ''
        if leaf.lower() in ('configuration.bin','active.bin') or re.fullmatch(r'Private-\d+\.BCD',leaf,re.I):
            if name.casefold() not in {p.casefold() for p in desired}: desired[name]=None
    if canonical_changed:
        # Live logs were backed up by the executor. Never replay an outgoing
        # Windows hive's transaction logs into the newly selected clean hive.
        for suffix in ('.LOG','.LOG1','.LOG2'):
            key=(CANON+suffix).casefold()
            if key in original: desired[original[key][0]]=None
    if chooser is not None:
        require(sha(chooser)==CHOOSER_SHA and sha(ntfs_driver)==NTFS_SHA,'Unverified selector payload')
        for path in ALIASES:
            current=original.get(path.casefold(),(None,None))[1]
            if (rebuild_menu and path=='EFI/Boot/BOOTX64.EFI' and current is not None
                    and current not in PRIOR_CHOOSER_SHAS | {CHOOSER_SHA}):
                # OpenCore or another loader may own the firmware fallback.
                # Repair our Microsoft/WinDeploy entry without replacing it.
                continue
            require(current is None or (old['existing'] and current in PRIOR_CHOOSER_SHAS | {CHOOSER_SHA}),
                    'A public boot path belongs to another loader; preserve it: '+path)
            desired[path]=chooser
        driver=BASE+'OpenNtfsDxe.efi'
        require(original.get(driver.casefold(),(None,NTFS_SHA))[1]==NTFS_SHA,'Unexpected existing NTFS EFI driver')
        desired[driver]=ntfs_driver
        if ntfs_4kn_driver is not None:
            require(sha(ntfs_4kn_driver)==NTFS_4KN_SHA,'Unverified 4Kn reader payload')
            driver=BASE+'Ntfs4Kn.efi'
            require(original.get(driver.casefold(),(None,NTFS_4KN_SHA))[1] in PRIOR_NTFS_4KN_SHAS | {NTFS_4KN_SHA},'Unexpected existing 4Kn EFI reader')
            desired[driver]=ntfs_4kn_driver
    changes=[]
    for path,data in desired.items():
        actual,expected=original.get(path.casefold(),(path,None))
        if (data is None and expected is None) or (data is not None and sha(data)==expected):continue
        changes.append(dict(path=actual,expected=expected,data=data))
    # Publish the menu last. An interrupted multi-file update fails closed on
    # its menu/configuration checksum instead of selecting the wrong OS.
    changes.sort(key=lambda c: (c['path'].casefold()==MENU.casefold(), c['path'].casefold() in {p.casefold() for p in ALIASES}))
    return dict(changes=changes,catalog=catalog,active=active,
                canonical_changed=canonical_changed,set_default_boot=False,modify_opencore=False)
