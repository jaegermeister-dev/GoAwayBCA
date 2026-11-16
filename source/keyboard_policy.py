"""Windows 7 inbox keyboard discovery, independent of graphics/EFI policy."""
MODELS = ('MacBookPro11,3', 'MacBookPro11,4', 'MacBookPro11,5')
GUIDANCE = (
    'We recommend preparing Windows 7 keyboard drivers before its first boot. '
    'Windows 7 setup reached the desktop without internal or USB keyboard input on '
    'our MacBookPro11,3 and 11,5, including the officially Windows 7-supported 11,3. '
    'The same preparation is used on our 11,4. It helps Windows find its own original '
    'Microsoft keyboard drivers; it is not an EFI patch and does not disable driver '
    'signatures or change UAC. These observations do not prove that EFI caused the '
    'problem or that every Mac needs this preparation.')

def enabled_for(model, selected=None):
    if model in MODELS:
        if selected is False:
            raise ValueError('Windows 7 keyboard preparation is required on '+model)
        return True
    if type(selected) is not bool:
        raise ValueError('Confirm the Windows 7 keyboard preparation choice')
    return selected

def configure(model, family, interaction, note):
    if family != 'Windows 7':
        return False
    if model in MODELS:
        note('Original Windows 7 keyboard discovery preparation will be applied automatically on '+model+'.')
        return True
    choice = interaction.keyboard_choice(GUIDANCE, True)
    selected = enabled_for(model, choice)
    note('Windows 7 keyboard preparation: '+('enabled' if selected else 'disabled by user')+'.')
    return selected
