"""Reconcile newly discovered Windows before an existing menu is maintained.

Only reads installed Windows/EFI here. Composition produces a single transaction
against the original snapshot; the caller keeps confirmation and commit ordering.
"""
from boot_catalog import identity
import menu_lifecycle as m


def compose(files, manifest, reconciliation, disk, esp, rows, *, remove=None, rename=None):
    final = dict(files)
    for change in reconciliation['changes']:
        path = change['path']
        old = final.get(path)
        m.require((m.sha(old) if old is not None else None) == change['expected'],
                  'Menu reconciliation snapshot changed')
        if change['data'] is None: final.pop(path, None)
        else: final[path] = change['data']
    virtual_manifest = dict(manifest)
    for change in reconciliation['changes']:
        if change['data'] is None: virtual_manifest.pop(change['path'], None)
        else: virtual_manifest[change['path']] = m.sha(change['data'])
    mutation = m.transition(final, virtual_manifest, disk, esp, rows, remove=remove, rename=rename)
    for change in mutation['changes']:
        if change['data'] is None: final.pop(change['path'], None)
        else: final[change['path']] = change['data']
    before = m.indexed(manifest)
    touched = {c['path'].casefold():c['path'] for c in reconciliation['changes']+mutation['changes']}
    values = m.indexed(final)
    changes = []
    for folded, path in touched.items():
        actual, expected = before.get(folded, (path, None))
        data = values.get(folded, (None, None))[1]
        if (m.sha(data) if data is not None else None) != expected:
            changes.append(dict(path=actual, expected=expected, data=data))
    changes.sort(key=lambda c:(c['path'].casefold()==m.MENU.casefold(),
                              c['path'].casefold() in {p.casefold() for p in m.ALIASES}))
    m.inspect(final, disk, esp, for_removal=remove is not None)
    result = dict(mutation, changes=changes)
    result['windows_sources'] = reconciliation['windows_sources']
    result['added_installations'] = reconciliation['added_installations']
    result['confirmed_absent'] = reconciliation.get('confirmed_absent', [])
    result['removed_installations'] = reconciliation.get('removed_installations', [])
    return result


def prepare(owner, root, manifest, disk, esp, rows, *, remove=None, rename=None,target=None):
    # No existing menu means this operation has no menu to refresh. Normal
    # installation/rebuild creates one using the same complete discovery path.
    if m.CATALOG.casefold() not in m.indexed(manifest): return None
    from backtoseven_menu_actions import read_efi, assemble
    from backtoseven_menu import previous
    files = read_efi(root, manifest)
    old = previous(files, disk, esp)
    registered = {identity(e) for e in old['catalog']['entries']}
    same_drive = [r for r in rows if r['observed']['disk_guid']==disk and r.get('esp_guid')==esp
                  and identity(r['observed']) != remove]
    additions = [r for r in same_drive if identity(r['observed']) not in registered]
    from menu_presence import collect
    absent = collect(owner,old['catalog']['entries'],rows,target) if target is not None else []
    if not additions and not absent: return None
    if additions:
        reconciliation = assemble(owner, root, files, disk, esp, same_drive,confirmed_absent=absent)
    else:
        reconciliation = m.transition(files,manifest,disk,esp,rows,confirmed_absent=absent)
        reconciliation['windows_sources'] = []
    reconciliation['confirmed_absent'] = absent
    gone = {identity(p) for p in absent}
    reconciliation['removed_installations'] = [e['title'] for e in old['catalog']['entries'] if identity(e) in gone]
    reconciliation['added_installations'] = [r['title'] for r in additions]
    return compose(files, manifest, reconciliation, disk, esp, rows, remove=remove, rename=rename)


def review(plan):
    if not plan: return ''
    text = ''
    if plan.get('added_installations'):
        text += ('\n\nOther Windows installations found on this same drive will also be added to the boot menu: '+
                 ', '.join(plan['added_installations'])+'. Their Windows partitions will not be changed.')
    if plan.get('removed_installations'):
        text += ('\n\nWindows installations confirmed removed outside this app will be removed from the menu: '+
                 ', '.join(plan['removed_installations'])+'.')
    return text+' The EFI configuration is backed up before publication.'


def recheck(owner, files, disk, esp, rows, target, plan):
    from backtoseven_menu import previous
    from menu_presence import recheck as verify
    verify(owner,previous(files,disk,esp)['catalog']['entries'],rows,target,plan.get('confirmed_absent',[]))
