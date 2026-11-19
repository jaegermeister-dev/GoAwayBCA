"""Display actual saved/custom names, not inferred Windows-version labels."""
from boot_catalog import identity


def describe(drive_label, entries):
    return '%s — %d saved menu entries\n%s' % (
        drive_label, len(entries), '\n'.join(e['title'] for e in entries) or '(empty menu)')


def comparison(before, after):
    old={identity(e): e for e in before};new={identity(e): e for e in after}
    def listing(entries):
        return '\n'.join(e['title']+(' (currently unavailable)' if e.get('available') is False else '')
                         for e in entries) or '(empty menu)'
    lines=['Current saved menu:',listing(before),'','Proposed updated menu:',listing(after)]
    added=[e['title'] for e in after if identity(e) not in old]
    removed=[e['title'] for e in before if identity(e) not in new]
    if added:lines+=['','Entries to add:','\n'.join(added)]
    if removed:lines+=['','Entries to remove:','\n'.join(removed)]
    lines+=['','The current list is read from this drive\'s saved boot-menu configuration. '
            'It is not proof that those Windows installations still exist. Rebuilding checks '
            'for Windows added or removed outside GoAwayBCA and updates this drive\'s list. '
            'Unavailable or unreadable installations are not assumed deleted. Other drives\' menus are unchanged.']
    return '\n'.join(lines)


def choices(owner, rows):
    from install_windows7 import require, gpt_for, tree_manifest, require_efi_unchanged
    from menu_lifecycle import snapshot
    from backtoseven_menu import previous
    labels=[]
    for row in rows:
        target=row['target'];layout=gpt_for(target)
        if owner.esp_owned and owner.esp_device!=target['esp']:
            require(not owner.close_esp(),'Could not detach prior scan EFI partition')
        root=owner.mount_esp(target['esp'],readonly=True)
        manifest=tree_manifest(root,owner.note,'Reading saved menu on '+target['disk']['DeviceIdentifier'])
        require_efi_unchanged(manifest,row['efi_manifest'],'EFI changed before drive selection; retry')
        old=previous(snapshot(root,manifest),layout['guid'],row['esp_guid'])
        label='%s / %s / %s' % (target['disk']['DeviceIdentifier'],
            target['disk'].get('MediaName') or 'Drive',
            'Internal' if target['disk'].get('Internal') is True else 'USB')
        labels.append(describe(label,old['catalog']['entries']))
    return labels
