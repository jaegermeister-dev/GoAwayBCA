"""Recognise old boot records by schema, GUIDs and hashes, never their branding.

Only called after a complete scan found NO Windows installations. Produces an
exact-file deletion plan for the normal confirmed/backed-up transaction.
"""
import copy,json,re,hashlib,struct
from pathlib import Path
import menu_lifecycle as m
import chooser_config as chooser
import store_configuration as stores
import reset_menu
from installation_records import validate_record

OLD_SELECTORS=frozenset(('0c445aa9645969a37141960a89e5fee56f033b51bc3043479d76915d46f45dae',
    '6e09756631c7cb94914723f84403e902892b5a16fc8784023d96c9e1ab29bee7',
    'f77bc2f4d73bed564fdeb423c8707aeea383e674f88a9621a482901c40bfdaa9',
    '9b60ae395259a0171f0d78b3b2e8efd4a59e5aac36e1c67be615ea1f48a2485c'))
OLD_HELPER='a98ce3567342c893bf4f6e7d9872344d8e5e39c9db3b677d9ac5a6b64c9efda9'
ORIGINAL='6470d503fc884f23b515457a03018ece8bf6f6c5bd358f3b5518711ebadb5161'

def plan(root,manifest,disk,esp):
    root=Path(root);index=m.indexed(manifest);changes={}
    def read(path,limit=2*1024**2):
        actual,digest=index[path.casefold()];p=root/actual
        if p.is_symlink() or not p.is_file() or p.stat().st_size>limit:raise ValueError('Unsafe legacy boot evidence')
        data=p.read_bytes()
        if m.sha(data)!=digest:raise ValueError('Legacy boot evidence changed')
        return data
    def remove(path):
        actual,digest=index[path.casefold()]
        changes[actual.casefold()]=dict(path=actual,expected=digest,data=None)
    for path in manifest:
        if not re.fullmatch(r'EFI/[^/]+/catalog.json',path,re.I) or path.casefold()==m.CATALOG.casefold():continue
        base=path[:-len('catalog.json')];prefix=base+'SystemStores/'
        alias=index.get((base+'Windows.efi').casefold())
        if not alias or alias[1] not in OLD_SELECTORS:continue
        config=read(prefix+'Configuration.bin');menu=read(base+'menu.bin');state=read(prefix+'Active.bin')
        if not (len(state)==88 and state[20:24]==bytes(4) and m.sha(state[:56])==state[56:].hex()
                and hashlib.sha256(config).digest()==state[24:56]):raise ValueError('Invalid legacy active state')
        if not (menu[:5]==config[:5]==state[:5] and menu[5:8]==b'001' and config[5:8]==b'C01' and state[5:8]==b'S01'):
            raise ValueError('Unknown legacy store format')
        names=dict(BASE=base,PREFIX=prefix,CATALOG=path,MENU=base+'menu.bin',CONFIG=prefix+'Configuration.bin',
            STATE=prefix+'Active.bin',ALIASES=(base+'Windows.efi','EFI/Microsoft/Boot/bootmgfw.efi','EFI/Boot/BOOTX64.EFI'),
            PRIOR_CHOOSER_SHAS=OLD_SELECTORS)
        saved={name:getattr(m,name) for name in names};cm,sm=chooser.MAGIC,stores.MAGIC
        try:
            for name,value in names.items():setattr(m,name,value)
            chooser.MAGIC=menu[:8];stores.MAGIC=config[:8]
            files=reset_menu.snapshot(root,manifest)
            # The prior signature was verified above. Normalize only the copied
            # state header for the current read-only ownership parser.
            actual=index[(prefix+'Active.bin').casefold()][0]
            normalized=b'W7DEPS01'+state[8:56];normalized+=hashlib.sha256(normalized).digest()
            files[actual]=normalized;virtual=dict(manifest);virtual[actual]=m.sha(normalized)
            proposed=reset_menu.plan(files,virtual,disk,esp)
            if proposed['needs_review']:raise ValueError('Older boot files need review; no target was formatted')
            for item in proposed['changes']:remove(item['path'])
        finally:
            chooser.MAGIC,stores.MAGIC=cm,sm
            for name,value in saved.items():setattr(m,name,value)
    for path in manifest:
        if not re.fullmatch(r'EFI/[^/]+/Records/[0-9a-f-]{36}\.json',path,re.I) or path.casefold().startswith(m.BASE.casefold()):continue
        raw=read(path);record=json.loads(raw)
        if record.get('product')!='BackToSeven':continue
        checked=copy.deepcopy(record);checked['creator']='WinDeploy'
        try:validate_record(checked)
        except (ValueError,KeyError,TypeError):continue
        if record['disk_guid'].lower()!=disk or record['esp_guid'].lower()!=esp or Path(path).stem.lower()!=record['partition_guid'].lower():continue
        for asset in record['efi_assets']:
            if asset['partition_guid'].lower()!=esp:continue
            found=index.get(asset['path'].casefold())
            if found and found[1]==asset['sha256']:remove(found[0])
        # A serviced canonical store is eligible only when it still contains
        # the exact one recorded Windows partition, not a shared/foreign OS.
        canonical=index.get(m.CANON.casefold())
        if canonical and canonical[0].casefold() not in changes:
            try:m.verify_bcd(read(m.CANON),disk,esp,disk,record['partition_guid'],for_removal=True)
            except (ValueError,KeyError,TypeError):pass
            else:remove(m.CANON)
        public=index.get('efi/microsoft/boot/bootmgfw.efi')
        if public and public[1] in (OLD_HELPER,ORIGINAL) and m.CANON.casefold() in changes:remove(public[0])
        remove(path)
    return list(changes.values())
