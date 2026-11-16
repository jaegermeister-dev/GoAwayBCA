"""Shared wording and choices for the optional boot-menu workflow."""
from report_metadata import MULTI_WINDOWS
WARNING = (
    'Experimental multi-Windows boot menu\n\n'
    'This feature may not work with your boot configuration and unintended side effects '
    'are possible, including one or more Windows installations becoming unbootable. '
    'Back up your data and existing Windows installations before proceeding.\n\n'
    'Install any other Windows versions BEFORE installing Windows 7 with BackToSeven. '
    'Preferably do not install another Windows version yourself afterward: its installer '
    'can change boot files during setup and subsequent restarts. Rescanning and rebuilding '
    'this menu afterward may not recover a working boot configuration.\n\n'
    'Menu operations change files on the selected EFI partition, not Windows partition '
    'contents, partition sizes, OpenCore, or the default startup choice. A verified backup '
    'of the current EFI files is required before changes. That backup is not a backup of '
    'your Windows installation or personal files.\n\n' + MULTI_WINDOWS
)
NO_EXISTING = (
    'No existing Windows installation was found. A custom boot menu is not needed for '
    'a single Windows 7 installation and is not recommended unless you understand why '
    'you need it. Normal installation does not offer or enable it. It remains available '
    'here as an explicit experimental option.'
)
RESTORE = (
    'To return to the configuration from before this Windows 7 installation, first back '
    'up anything you need from Windows 7, then erase only its Windows partition yourself. '
    'Return to Experimental settings and select Restore pre-install boot configuration. '
    'Do not resize other partitions. Restoration checks for later boot-file changes and '
    'will not silently overwrite another installation. If Windows was installed later, '
    'the earlier backup cannot contain its boot configuration.'
)


def installation_choice(ui, scan):
    if not scan['scan_complete']:
        raise ValueError('Windows discovery is incomplete; resolve unreadable partitions first.')
    if not scan['installations']:
        return False
    choice = ui.choose(
        'An existing Windows installation was found. The experimental menu is recommended '
        'if you want to keep it bootable alongside Windows 7.\n\n' + WARNING + '\n\n' + RESTORE,
        ['Use experimental menu', 'Continue without the menu', 'Cancel'])
    if choice == 2:
        raise ValueError('Installation cancelled before Windows preparation.')
    if choice == 1:
        ui.choose('Continuing without the experimental menu does not guarantee that both '
                  'Windows installations will boot. Existing unowned boot files will not '
                  'be overwritten automatically; an incompatible configuration can stop '
                  'installation before formatting. You can return to Experimental settings later, '
                  'but recovery after another installer changes the files may not work.', ['Continue'])
    return choice == 0

# Keep Linux/other-OS coexistence guidance visible during menu review.
from report_metadata import LINUX_GUIDANCE
WARNING += "\n\n" + LINUX_GUIDANCE
