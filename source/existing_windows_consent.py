"""Explain importing pre-existing Windows before installation preparation."""
from installation_records import make_marker
from boot_catalog import identity


def paired(row):
    try:
        record=row.get('record')
        return row.get('marker')==make_marker(record) and identity(record)==identity(row['observed'])
    except (ValueError,KeyError,TypeError):return False


def confirm(scan,ui):
    rows=[r for r in scan['installations'] if not paired(r)]
    if not rows:return
    text=('Existing Windows installations were found without matching GoAwayBCA or BackToSeven creation records. '
        'A missing record does not prove that another tool created them.\n\n'
        'GoAwayBCA will try to add existing Windows installations on the destination disk to its Windows boot menu. '
        'This changes Windows boot files on that disk\'s EFI partition; it does not reinstall or erase the existing Windows partitions. '
        'The new installation will format only the destination you later select and confirm.\n\n'
        'Multi-Windows integration is experimental. Existing boot arrangements can differ, and Windows setup may change boot files. '
        'An existing installation might fail to appear or boot. Back up your data and preferably the entire existing Windows installation before continuing. '
        'GoAwayBCA saves an EFI backup before changing boot files, but this is not a backup of your Windows data. '
        'OpenCore, Apple and Linux boot files are not imported or intentionally changed.\n\n'
        'Continue to review and prepare the installation, or cancel now?')
    if ui.approve('import_review',text) is not True:
        raise ValueError('Cancelled before importing existing Windows; no downloads, preparation or formatting started')
