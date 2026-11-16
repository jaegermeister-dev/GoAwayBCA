"""BackToSeven's narrow product boundary; independent of driver download choices."""
MODELS = ('MacBookPro11,4', 'MacBookPro11,5')


def validate_model(value):
    if not isinstance(value, str) or value.strip() not in MODELS:
        raise ValueError('This Mac model is not supported by BackToSeven. '
                         'Only MacBookPro11,4 and MacBookPro11,5 are supported.')
    return value.strip()


def confirm_model(interaction, detected):
    value = interaction.request('driver_model',
        'Confirm your Mac model identifier. Make sure the identifier you confirm '
        'is accurate for your machine. The detected identifier is already filled in; '
        'correct it only if necessary. BackToSeven supports only MacBookPro11,4 '
        'and MacBookPro11,5.', model=detected)
    return validate_model(value)


def validate_edition(edition):
    if edition.get('family') != 'Windows 7':
        raise ValueError('BackToSeven installs Windows 7 x64 only. Select a Windows 7 ISO.')
    return edition


def configure_drivers(interaction, model):
    from driver_preparation import WORKING_115
    validate_model(model)
    message = ('Apple did not officially support Windows 7 on ' + model + '. '
        'BackToSeven can prepare our community driver support. It downloads our '
        'support recipe and installer from GitHub, retrieves original drivers '
        'from their publishers, and assembles them locally. The installer will '
        'be ready at C:\\BackToSevenSupport\\BackToSeven Drivers Installer.exe '
        'after normal Windows setup.\n\n' + WORKING_115)
    if model == 'MacBookPro11,4':
        message += '\n\n11,4 receives its original Intel Iris and HDMI audio drivers, with no AMD/M370X packages or 11,5 graphics EFI patch.'
    answer = interaction.choose(message, ['Prepare community drivers (recommended)',
                                         'Skip drivers'])
    return dict(source='community' if answer == 0 else 'skip', model=model,
                detected_model=model, family='Windows 7')
