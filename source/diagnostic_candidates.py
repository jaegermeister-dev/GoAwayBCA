"""Partition selection for diagnostics, independent of Windows readability."""
from install_windows7 import filesystem, partition_summary


def candidates(targets):
    rows = []
    for target in targets:
        p = target['partition']
        # diskutil can retain the pre-install exFAT label after NTFS deployment.
        # These are candidates, never a claim that Windows exists or boots.
        if filesystem(p) not in ('ntfs', 'exfat'):
            continue
        rows.append(dict(target=target,
            title=p.get('VolumeName') or 'Unlabelled partition',
            summary=partition_summary(p, target['disk'], multiline=True)+
                '\nWindows contents have not been inspected. Select the partition containing the Windows installation you want to test.'))
    return rows
