"""Official download routing: exact model AND Windows family, not date alone.

No driver binaries/INFs are patched. Unknown combinations require an explicit
fallback choice and are never labelled supported. Evidence is recorded below.
"""
PUBLIC = (
    ('5.1.5621','106412','031-3384.20140211.Xcc3e/BootCamp5.1.5621.zip',
     'MacBookAir4,1 MacBookAir4,2 MacBookAir5,1 MacBookAir5,2 MacBookPro6,1 MacBookPro6,2 '
     'MacBookPro8,1 MacBookPro8,2 MacBookPro8,3 MacBookPro9,1 MacBookPro9,2 MacBookPro10,1 '
     'MacBookPro10,2 MacPro4,1 MacPro5,1 Macmini5,1 Macmini5,2 Macmini5,3 Macmini6,1 Macmini6,2 '
     'iMac11,3 iMac12,1 iMac12,2 iMac13,1 iMac13,2 iMac13,3'),
    ('5.1.5640','106432','031-3442.20140211.Nnju7/BootCamp5.1.5640.zip',
     'MacBookAir6,1 MacBookAir6,2 MacBookPro11,1 MacBookPro11,2 MacBookPro11,3 MacPro6,1 iMac14,1 iMac14,2 iMac14,3'),
    ('5.1.5722','106409','031-30883-20150812-ea1d13dc-4130-11e5-ba25-920911ba098f/bootcamp5.1.5722.zip','iMac14,4'),
    ('5.1.5769','106378','031-30890-20150812-ea191174-4130-11e5-a125-930911ba098f/bootcamp5.1.5769.zip','Macmini7,1 iMac15,1'),
)
# This exact catalog product's original BootCamp.xml is 6.0.6136. Its MSI
# rejects Windows 7; its original graphics folders include separate 6.3/10.0
# INFs. https://support.apple.com/en-us/101201 documents Boot Camp 6 + Win8.1.
KNOWN_6 = '041-98143'
KNOWN_6_TOC = '5d773499c67d9a5ca2bc136c41849fe4f73d599c'

DRIVER_TROUBLESHOOTING = (
    'If the full Boot Camp driver installer causes blue-screen crashes, boot loops or instability, '
    'older Apple drivers may be incompatible with your newer Windows version. Back up your files '
    'before reinstalling Windows. On the fresh installation, let Windows Update install the available '
    'drivers, then use Device Manager to identify remaining missing devices. Manually install only '
    'those missing drivers from the appropriate Apple package; do not replace drivers that already '
    'work. This is troubleshooting guidance, not an automatic driver-selection feature or a guarantee. '
    'Windows Update availability depends on the Windows version. If this Mac/Windows combination '
    'normally used legacy BIOS/hybrid MBR, pure-EFI hardware issues are a separate possibility; '
    'check the compatibility warning and include diagnostics when reporting an issue.')

def rank(model,family,products):
    from bootcamp_download import require
    from mac_compatibility import assess,FAMILIES
    require(family in FAMILIES,'Unknown Windows family for Apple driver selection')
    # Windows 11 intentionally uses unchanged Windows 10 drivers. Windows 8
    # must not silently inherit an 8.1/10-only package's compatibility claim.
    target='Windows 10' if family=='Windows 11' else family
    support=assess(model,dict(family=target,architecture=9))['official_status']
    confirmed=support=='supported'
    rows=[]
    if target in ('Windows 7','Windows 8','Windows 8.1'):
        for version,page,url,models in PUBLIC:
            if model in models.split():
                rows.append(dict(id='Apple-'+version,version=version,model=model,kind='zip',
                    url='https://download.info.apple.com/Mac_OS_X/'+url,
                    evidence='https://support.apple.com/en-us/'+page,
                    os_match=confirmed,rank=90,apple_support_status=support,
                    requested_family=family,driver_family_basis=target,
                    reason=('Original Apple package advertised for '+target+' x64 and listing this model. '+
                        ('The separate Apple model/version support record matches.' if confirmed else
                         'The separate Apple model/version support record is '+support+'; this is not confirmed official support.'))))
    for product in products:
        require(product.get('model',model)==model,'Apple catalog model match belongs to another Mac')
        row=dict(product,kind='pkg',os_match=False,rank=0,apple_support_status=support)
        known=(row['id']==KNOWN_6 and row.get('toc_sha1')==KNOWN_6_TOC)
        if target in ('Windows 8.1','Windows 10') and known:
            row.update(os_match=confirmed,rank=100,version='6.0.6136',
                reason='Original Boot Camp 6 package for '+target+'; exact model match.',
                evidence='https://support.apple.com/en-us/'+('101201' if target=='Windows 8.1' else '102622'))
            if not confirmed:row['reason']+=' Separate Apple model/version support is '+support+'.'
            if family=='Windows 11':row['reason']+=' Windows 11 uses this unchanged Windows 10 driver package.'
        else:
            row['reason']=('Exact Apple model match; Windows-version support is not established for this package.'
                if not (known and target=='Windows 7') else
                'Apple did not supply Windows 7 support in this Boot Camp 6 package; its main installer rejects Windows 7.')
            if known and target=='Windows 8':
                row['reason']='Exact Apple model match, but this known package is for Windows 8.1/10; Windows 8 compatibility is not established.'
        row['requested_family']=family;row['driver_family_basis']=target
        rows.append(row)
    return sorted(rows,key=lambda r:(r['rank'],r.get('post_date',''),r['id']),reverse=True)

def choose(model,family,products,interaction,note):
    from bootcamp_download import require
    rows=rank(model,family,products)
    require(rows,'No official Apple driver download found for '+model)
    supported=[r for r in rows if r['os_match']]
    if supported:
        chosen=supported[0]
        note('Selected original Apple '+chosen['id']+' for '+model+' / '+family+'. '+chosen['reason'])
        return chosen
    # Never silently mistake a model-only match for an OS-compatible choice.
    index=interaction.choose('No verified official package covers both '+model+' and '+family+'. '
        'These are original Apple MODEL matches only; they may reject this Windows version. '
        'You can explicitly try one unchanged, or skip the official download. No drivers run automatically.',
        [r['id']+' — '+r['reason'] for r in rows]+['Skip official Apple drivers'])
    require(type(index) is int and 0<=index<=len(rows),'Invalid official driver fallback selection')
    return None if index==len(rows) else rows[index]
