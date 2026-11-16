"""Early sector-size information; never select or modify a destination."""
import drive_geometry


def message(editions, geometry):
    families=[f for f in ('Windows 7','Windows 8')
              if any(e.get('family')==f for e in editions)]
    if not families:
        return None
    lines=['Before preparing '+ ' / '.join(families),
           '', 'Internal drive information recorded at app launch:']
    drives=(geometry or {}).get('drives',{})
    for key,row in sorted(drives.items()):
        lines.append('%s: %s-byte logical sectors (%s GB).'%(
            row.get('device_at_launch','Internal drive'),row['sector_size'],
            '%.1f'%(row['bytes']/1000000000.0)))
    if not drives:
        lines.append('No internal GPT drive sector-size result is available. '
                     'Do not assume 512 or 4096 bytes; the selected destination must be checked.')
    lines += ['', '512-byte and 4096-byte (4Kn) logical sectors require different boot handling. '
              'The app uses the measured drive information, not its SATA/NVMe label or your Mac model.']
    if 'Windows 7' in families:
        lines += ['', 'Windows 7 does not natively support 4096-byte logical sectors. '
                  'GoAwayBCA has experimental compatibility handling for its supported Windows 7 image. '
                  'It is selected automatically on MacBookPro11,4 and 11,5 when the destination is 4Kn; '
                  'other models require an additional experimental confirmation. '
                  'This notice is shown on every model, even when compatibility handling is automatic. '
                  'The current fix has passed VM tests; physical-Mac acceptance, kernel updates and '
                  'hibernation are not confirmed. A 512-byte destination does not use this 4Kn fix.']
    if 'Windows 8' in families:
        lines += ['', 'Windows 8 includes native 4Kn support and does not use the Windows 7 sector fix. '
                  'Its EFI boot files still need the correct destination geometry. NVMe driver '
                  'availability is a separate matter; the optional Windows 8 NVMe driver preparation '
                  'is experimental and untested.']
    lines += ['', 'If you intend to install to USB, the internal-drive result does not describe '
              'your USB destination and can be disregarded for that destination. A USB drive or '
              'adapter can report a different logical sector size, even if the internal drive is 512 bytes. '
              'When USB installation support is added, the app will check the selected USB device '
              'after partition selection and report its actual size, whether 512 or 4096 bytes, '
              'with the applicable guidance. USB installation destinations are not available in this build.',
              '', 'This is information only. No partition has been selected or formatted.']
    return '\n'.join(lines)


def inform(editions, interaction):
    text=message(editions,drive_geometry.report())
    if text is not None:
        interaction.request('notice',text,button_title='Done')
