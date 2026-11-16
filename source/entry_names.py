"""Labels are presentation only; GUIDs remain installation identity."""
from chooser_config import field

def validate(value):
    if type(value) is not str:raise ValueError('Enter a name for this Windows menu entry')
    value=value.strip()
    field(value,96)
    return value

def prompt(interaction,default,rename=False,context=''):
    while True:
        value=interaction.request('entry_name',
            (context+'\n\n' if context else '')+
            ('Rename this Windows picker entry.' if rename else 'Choose the name shown in the Windows picker.')+
            '\nKeep the default or enter a custom name. This changes the menu label only, not the partition or Windows files.'+
            '\nUse 1–95 characters (letters, numbers, spaces and standard punctuation; ASCII in this build).',
            name=default)
        try:return validate(value)
        except ValueError as error:
            interaction.choose(str(error),['Try another name'])


def imported_name(owner,row,default):
    """Ask once for a newly imported installation, never claim its origin."""
    from boot_catalog import identity
    from installation_records import can_uninstall
    if can_uninstall(row['observed'],row.get('marker'),row.get('record')):
        return default
    interaction=getattr(owner,'interaction',None)
    if interaction is None:return default
    names=getattr(owner,'_imported_menu_names',None)
    if type(names) is not dict:
        names={};owner._imported_menu_names=names
    key=identity(row['observed'])
    if key not in names:
        context=('An existing Windows installation was found outside the current menu. '
            'Its creation by GoAwayBCA or BackToSeven is not verified. Adding it to the '
            'menu does not enable uninstalling it through this app.\n\n'+
            row.get('title',default)+'\n'+row.get('summary','')+'\n'+
            'Partition: '+row['target']['partition']['DeviceIdentifier'])
        names[key]=prompt(interaction,default,context=context)
    return validate(names[key])
