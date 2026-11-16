"""Conservative read-only reference validation for the observed BCD profile.

Never repairs or rewrites a store. Unknown references need explicit validation
before this profile can accept them. Firmware-export display order is not an
OS boot-manager menu and is preserved separately.
"""
import re
from bcd_store_sanity import require

GUID=re.compile(r'\{[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}')
# Observed standard inherited settings absent from the saved Win7 BCD export:
# resume-loader settings and boot-loader settings, respectively. These may be
# absent, but a present object with either identifier MUST be an inherited group.
ABSENT_INHERIT=frozenset(('{1afa9c49-16ab-4a5c-901b-212802da9460}',
                         '{6efb52bf-1766-41db-a6b3-0ee5eff72bd7}'))
NULL_GUID='{00000000-0000-0000-0000-000000000000}'
FIRMWARE_MANAGER='{a5a30fa2-3d06-4e9f-b5f4-a01df9d1fcba}'


def names(value,number,allow_duplicate_firmware_order=False):
    kind,data=value;fmt=(number>>24)&15
    require(fmt in (3,4) and kind==(1 if fmt==3 else 7) and len(data)%2==0
            and len(data)<=64*78+2,'Invalid BCD reference type/size')
    decoded=data.decode('utf-16le','strict').lower()
    suffix='\0' if fmt==3 else '\0\0'
    require(decoded.endswith(suffix),'Unterminated BCD references')
    result=decoded[:-len(suffix)].split('\0')
    require((len(result)==1 if fmt==3 else 1<=len(result)<=64)
            and (len(set(result))==len(result) or allow_duplicate_firmware_order)
            and all(GUID.fullmatch(v) for v in result),
            'Malformed/duplicate BCD references')
    return result


def validate(objects,fields,bootmgr,loader):
    """fields maps (object GUID, eight-digit element string) to (type, bytes)."""
    require(1<=len(objects)<=512,'BCD object limit exceeded')
    inheritance={};count=0
    for (owner,number_text),value in fields.items():
        require(owner in objects and re.fullmatch('[0-9a-fA-F]{8}',number_text),
                'Invalid BCD reference owner/element')
        number=int(number_text,16)
        if objects[owner] in (0x10100001,0x101fffff):
            # Do not interpret another OS/firmware menu or stale firmware references.
            # References FROM Windows still undergo the strict checks below.
            continue
        if (number>>24)&15 not in (3,4):continue
        kind=objects[owner]
        # Real Windows10 serviced BCD mirrors a repeated BootCamp entry in
        # {fwbootmgr}. This is NOT the Windows OS menu. Preserve its exact
        # bytes; still validate every target and reject duplicates elsewhere.
        firmware_order=(number==0x24000001 and owner==FIRMWARE_MANAGER and kind==0x10100001)
        refs=names(value,number,allow_duplicate_firmware_order=firmware_order);count+=len(refs)
        if number==0x14000006:
            for target in refs:
                require((target in objects and objects[target]>>28==2)
                        or (target not in objects and target in ABSENT_INHERIT),
                        'Inheritance points to an application or unknown missing object')
            inheritance[owner]=refs
        elif number==0x14000008:
            # Win10's observed serviced store uses a single NULL GUID when
            # no Windows RE application is configured. It is not another OS.
            require(kind in (0x10200003,0x10200004) and ((refs==[NULL_GUID] and NULL_GUID not in objects) or all(
                target!=loader and objects.get(target)==0x10200003 for target in refs)),
                'Recovery sequence points outside the validated recovery applications')
        elif number==0x23000003:
            require(len(refs)==1 and ((owner==bootmgr and refs[0]==loader) or
                (kind==0x10200003 and objects.get(refs[0])==0x10200004)),
                'Default/resume reference selects an unexpected application')
        elif number==0x24000001:
            require((owner==bootmgr and refs==[loader]) or
                (kind==0x10100001 and all(objects.get(target) in (0x10100002,0x101fffff) for target in refs)),
                'Display-order reference selects an unexpected application')
        elif number==0x24000002:
            require(owner==bootmgr and all(objects.get(target)==0x10200003 for target in refs),
                    'Boot sequence points outside this installation')
        else:
            raise ValueError('Unvalidated BCD reference element: '+number_text)
    done=set()
    def walk(owner,trail):
        require(owner not in trail and len(trail)<32,'Cyclic/overdeep BCD inheritance')
        if owner in done:return
        for target in inheritance.get(owner,[]):walk(target,trail+(owner,))
        done.add(owner)
    for owner in inheritance:walk(owner,())
    return count
