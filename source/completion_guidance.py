"""End-user installation completion guidance shared by all supported versions."""
def message(family, title, community=False, drivers=False, custom_menu=True):
    text = (family+' has been installed. You can restart when ready.\n\n'
            'With OpenCore, select Windows. Without OpenCore, hold Option while starting '
            'your Mac and select Windows or EFI Boot. '+
            ('In the Windows menu, select '+title+'. ' if custom_menu else 'The standard Microsoft loader starts Windows directly. ')+
            'Complete the normal Windows account setup.')
    if family == 'Windows 7':
        text += ('\n\nThe first or second boot may briefly be blank, show a few pixels at the top, '
                 'or show off-centre preparation text. Account setup may respond slowly before '
                 'graphics drivers are installed.')
    if community and drivers:
        text += ('\n\nAt the desktop, run C:\\BackToSevenSupport\\BackToSeven Drivers Installer.exe. '
                 'Use Restart now when offered. The installer reopens after sign-in with a normal '
                 'UAC prompt; do not start another copy manually. Cameras, sleep/resume repair '
                 'and dock Ethernet remain deferred.')
    elif drivers:
        text += ('\n\nAt the desktop, open C:\\Apple-WindowsSupport and run Boot Camp Setup.exe '
                 'from its BootCamp folder. Restart when the driver installer asks.')
    else:
        text += '\n\nNo driver package was included. Install your Mac\'s Windows drivers after account setup.'
    text += ('\n\nFiles saved during installation remain on your Mac. Open Saved Files in GoAwayBCA '
             'to review what you want to delete. Keep downloaded drivers if you want to reuse them.')
    return text
