"""Installation availability for this standalone product.

Classification, discovery, driver handling and menu handling remain shared.
This gate controls installing NEW Windows images, not recognising existing ones.
"""
PRODUCT = 'GoAwayBCA'
INSTALL_FAMILIES = ('Windows 8.1', 'Windows 10', 'Windows 11')


def require_family(family):
    if family not in INSTALL_FAMILIES:
        raise ValueError(PRODUCT + ' installs ' + ', '.join(INSTALL_FAMILIES) +
                         ' only. ' + str(family or 'This Windows image') +
                         ' cannot be installed with this app. Nothing was formatted.')


def selectable_editions(editions):
    selected = [edition for edition in editions if edition.get('family') in INSTALL_FAMILIES]
    if not selected:
        families = ', '.join(dict.fromkeys(e.get('family') or e.get('refusal') or
                                          'Unrecognised Windows image' for e in editions))
        require_family(families or None)
    return selected
