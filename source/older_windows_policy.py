"""Explicit older-OS consent and driver routing. Never invent a historical minimum."""
FAMILIES=('Windows 7','Windows 8','Windows 8.1','Windows 10','Windows 11')
EXCEPTIONS={'MacBookPro11,4','MacBookPro11,5'}


def assess(model,family,database):
    if family not in FAMILIES:raise ValueError('Unknown Windows family')
    if family=='Windows 7' and model in EXCEPTIONS:return None
    record=database['models'].get(model)
    if not record:return dict(model=model,family=family,minimum=None,driver_family=None,uncertain=True)
    # A minimum is definitive only when explicitly sourced in the database.
    # Earliest table coverage is not proof of an historical minimum: Windows 8
    # evidence is incomplete, and marketing variants can share a model ID.
    exact=record.get('official_minimum')
    minimum=exact.get('family') if isinstance(exact,dict) and exact.get('sources') else None
    if minimum not in FAMILIES:minimum=None
    confirmed=[]
    selected_supported=False
    for candidate in FAMILIES:
        variants=record.get('windows',{}).get(candidate,{}).get('variants',[])
        covered=bool(variants) and all(any(e.get('status')=='supported' for e in v.get('evidence',[])) for v in variants)
        if covered:confirmed.append(candidate)
        if candidate==family:selected_supported=covered
    if minimum:
        if FAMILIES.index(family)>=FAMILIES.index(minimum):return None
        return dict(model=model,family=family,minimum=minimum,driver_family=minimum,uncertain=False)
    if selected_supported:return None
    later=[f for f in confirmed if FAMILIES.index(f)>FAMILIES.index(family)]
    earlier=[f for f in confirmed if FAMILIES.index(f)<=FAMILIES.index(family)]
    if earlier:return None
    return dict(model=model,family=family,minimum=None,driver_family=later[0] if later else None,uncertain=True)


def configure(model,edition,interaction,database):
    result=assess(model,edition['family'],database)
    if result is None:return None
    text='Selected Windows: '+result['family']+'\nConfirmed Mac model: '+model+'\n'
    if result['minimum']:
        text+='Official minimum supported Windows version: '+result['minimum']+'.\n\nYour selected version is older than this Mac\'s official minimum.'
    else:
        text+='Official minimum supported Windows version: not confirmed by our records.'
        if result['driver_family']:
            text+='\nEarliest Apple-supported version confirmed for all recorded variants of this model: '+result['driver_family']+'.'
        text+='\n\nWe cannot confirm official support for your selected Windows version. The historical minimum may be earlier than the available records.'
    text+='\n\nWindows may fail to install or boot, and some hardware may not work. We do not provide support for versions older than your Mac\'s official minimum. You may acknowledge these risks and continue.'
    choices=['Cancel and choose another Windows ISO','Proceed without downloading drivers']
    if result['driver_family']:
        text+='\n\nYou can try the original Apple drivers for '+result['driver_family']+', but they are NOT confirmed compatible with '+result['family']+'. Their installers may refuse to run. No drivers are installed automatically.'
        choices.append('Proceed with Apple drivers for '+result['driver_family'])
    else:
        text+='\n\nNo reliable fallback driver version is recorded, so the app will not guess which package to download.'
    selected=interaction.choose(text,choices)
    if type(selected) is not int or not 0<=selected<len(choices):raise ValueError('Invalid older Windows decision')
    if selected==0:raise ValueError('Cancelled after older Windows guidance; no partition was formatted')
    return dict(result,action='skip' if selected==1 else 'fallback',acknowledged=True)


def driver_plan(guidance,model,family):
    choice=(guidance or {}).get('older_windows_driver_choice')
    if not choice:return None
    if choice.get('model')!=model or choice.get('family')!=family or choice.get('acknowledged') is not True:
        raise ValueError('Older Windows consent does not match this installation')
    if choice['action']=='skip':return dict(source='skip',model=model,detected_model=model,family=family)
    if choice['action']!='fallback' or choice.get('driver_family') not in FAMILIES:raise ValueError('Invalid fallback driver selection')
    return dict(source='apple',model=model,detected_model=model,family=family,
                driver_family=choice['driver_family'],older_windows_fallback=True)
