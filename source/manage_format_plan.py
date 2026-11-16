"""Pure post-format EFI/menu plan. No disk writes or user consent implied.

The executor must wait for verified completion of the confirmed NTFS format.
Uninstall requires paired creation records; acknowledgements never override that
requirement or authorise guessed EFI cleanup. OpenCore and other BCD stores are preserved. Public aliases can be
removed only for a sole, verified standalone installation that recorded them.
"""
from boot_catalog import rebuild, identity, guid
from chooser_config import encode, decode
from installation_records import format_plan, canonical, paired_record
from efi_transaction import digest
import menu_assets


def require(value,message):
    if not value:raise ValueError(message)


def plan(selected, rows, *, scan_complete, catalog_bytes=None, menu_bytes=None,
         unknown_acknowledged=False, lifecycle_files=None, menu_update=None):
    observed=selected['observed'];target=identity(observed)
    require(scan_complete is True,'Incomplete scan; no format/menu plan')
    require(sum(identity(r['observed'])==target for r in rows)==1,'Ambiguous selected partition')
    records=[r['record'] for r in rows if r['ownership']['managed']]
    assets={(r['partition_guid'],r['path']):r['sha256'] for r in selected['asset_readbacks']}
    operation=format_plan(observed,selected['marker'],selected['record'],assets,records,
        scan_complete,unknown_ownership_acknowledged=unknown_acknowledged,bcd_files=lifecycle_files)
    changes=[];removed=[];manifest=selected['efi_manifest'];catalog=None
    require((catalog_bytes is None)==(menu_bytes is None),'Incomplete existing chooser metadata')
    # The JSON ledger and firmware data must agree before either is regenerated.
    if catalog_bytes is not None and lifecycle_files is not None:
        from menu_lifecycle import transition
        import json
        catalog=json.loads(catalog_bytes.decode('utf-8'))
        require(encode(catalog['entries'])==menu_bytes,'Chooser catalog/menu disagree')
        # The system-store generation must change with the menu, especially
        # when the erased installation was the last booted (active) one.
        esp=selected.get('esp_guid') or selected['target'].get('esp_guid')
        if not esp and catalog['entries']:esp=catalog['entries'][0]['esp_guid']
        require(esp,'Missing verified ESP identity')
        migration=menu_update or transition(lifecycle_files,manifest,observed['disk_guid'],esp,rows,remove=target)
        changes.extend(migration['changes'])
        if menu_update:catalog=menu_update['catalog']
    elif catalog_bytes is not None:
        import json
        catalog=json.loads(catalog_bytes.decode('utf-8'))
        require(encode(catalog['entries'])==menu_bytes,'Chooser catalog/menu disagree')
        require(len(decode(menu_bytes))==len(catalog['entries']),'Invalid chooser menu')
        found={identity(r['observed']) for r in rows if identity(r['observed'])!=target}
        fresh=[e for e in catalog['entries'] if identity(e) in found]
        completed=dict(observed,status='format-complete') if any(identity(e)==target for e in catalog['entries']) else None
        new=rebuild(catalog,fresh,scan_complete=True,completed_format=completed)
        for path,before,data in [('EFI/WinDeploy/catalog.json',catalog_bytes,canonical(new)+b'\n'),
                                 ('EFI/WinDeploy/menu.bin',menu_bytes,encode(new['entries']))]:
            require(manifest.get(path)==digest(before),'Chooser metadata changed since scan')
            changes.append(dict(path=path,expected=digest(before),data=data))
    unknown_others=[r for r in rows if identity(r['observed'])!=target and not r['ownership']['managed']]
    protected={p.casefold() for p in ('EFI/Microsoft/Boot/bootmgfw.efi','EFI/Boot/BOOTX64.EFI',
        'EFI/WinDeploy/Windows.efi','EFI/WinDeploy/OpenNtfsDxe.efi','EFI/WinDeploy/Ntfs4Kn.efi','EFI/Microsoft/Boot/BCD')}
    references=set()
    if catalog:
        for row in catalog['entries']:
            if identity(row)!=target:
                for name in ('loader_path','bcd_path'):
                    references.add((guid(row['boot_partition_guid']),row[name].casefold()))
    for record in records:
        if identity(record)!=target:
            references.update((guid(a['partition_guid']),a['path'].casefold())
                              for a in record['efi_assets'])
    retained=[]
    standalone_only=(selected['ownership']['managed'] and catalog_bytes is None and len(rows)==1
        and selected['record'].get('boot_layout')=='standalone-canonical-esp-v1')
    for asset in operation['efi_cleanup']:
        key=(guid(asset['partition_guid']),asset['path'].casefold())
        # Unknown neighbouring installations mean exclusivity is not established.
        # Still permit formatting after warnings, but retain all their potential
        # shared boot files. Never blanket-delete Microsoft's folder.
        if unknown_others or key in references or (key[1] in protected and not standalone_only):
            retained.append(asset['path']);continue
        require(manifest.get(asset['path'])==asset['sha256'],'Cleanup asset changed')
        removed.append(dict(path=asset['path'],expected=asset['sha256'],data=None))
    if standalone_only and any(c['path'].casefold()=='efi/microsoft/boot/bcd' for c in removed):
        # No other installation references this verified standalone BCD.
        for suffix in ('.LOG','.LOG1','.LOG2'):
            path='EFI/Microsoft/Boot/BCD'+suffix
            if path in manifest:
                removed.append(dict(path=path,expected=manifest[path],data=None))
    try:
        paired=paired_record(observed,selected['marker'],selected['record'])
    except (ValueError,KeyError,TypeError):
        paired=None
    if paired is not None:
        record_path='EFI/WinDeploy/Records/'+guid(observed['partition_guid'])+'.json'
        record_data=canonical(paired)+b'\n'
        # This paired metadata record is distinct from arbitrary boot assets.
        # Once its exact Windows partition is erased, leaving a stale receipt
        # would block reinstall. Changed boot files are still retained/reported.
        if manifest.get(record_path)==digest(record_data):
            removed.append(dict(path=record_path,expected=digest(record_data),data=None))
    esp=selected.get('esp_guid') or (selected.get('record') or {}).get('esp_guid')
    if esp:
        imported=menu_assets.cleanup(lifecycle_files or {},manifest,observed['disk_guid'],esp,
            observed['partition_guid'],references,unknown_neighbours=bool(unknown_others))
        existing_removed={c['path'].casefold():c for c in removed}
        for change in imported['changes']:
            previous=existing_removed.get(change['path'].casefold())
            require(previous is None or previous==change,'Conflicting private boot cleanup records')
            if previous is None:removed.append(change)
        retained.extend(imported['retained'])
    require(not {c['path'].casefold() for c in changes}&{c['path'].casefold() for c in removed},
        'Menu and cleanup paths overlap')
    modified={c['path'].casefold() for c in changes+removed}
    return dict(operation=operation,changes=changes+removed,
        preserved_efi_paths=sorted(p for p in set(operation.get('preserved_efi_paths',[])+retained) if p.casefold() not in modified),
        cleanup_warning=bool(retained or operation.get('preserved_efi_paths') or not selected['ownership']['managed']),
        apply_only_after_verified_format=True,modify_opencore=False,set_default_boot=False)
