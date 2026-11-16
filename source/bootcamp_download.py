"""Optional Apple Boot Camp retrieval; no package scripts or drivers are run.

Catalog/model-distribution discovery follows the approach documented by
timsutton/brigadier 0.2.6 (https://github.com/timsutton/brigadier). This is an
independent implementation for our bundled Python 3 runtimes. Native macOS curl
provides verified HTTPS even in the legacy runtime without an ssl extension.
"""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import time
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET
import zlib
import zipfile
from operation_progress import Meter, phase, hash_file

CATALOG='https://swscan.apple.com/content/catalogs/others/index-11-10.15-10.14-10.13-10.12-10.11-10.10-10.9-mountainlion-lion-snowleopard-leopard.merged-1.sucatalog'
HOSTS=('swscan.apple.com','swcdn.apple.com','swdist.apple.com','download.info.apple.com')
MODEL=re.compile(r'(?:MacBookPro|MacBookAir|MacBook|MacPro|Macmini|iMacPro|iMac|Xserve)[1-9][0-9]*,[1-9][0-9]*\Z')
MIB=1024**2

def require(ok,text):
    if not ok:raise ValueError(text)

def model_identifier(value):
    require(type(value) is str and MODEL.fullmatch(value.strip()),'Enter a Mac model identifier such as MacBookPro11,5')
    return value.strip()

def apple_url(value):
    p=urlsplit(value)
    require(p.scheme in ('https','http') and p.hostname in HOSTS and not p.username and not p.password
            and p.port in (None,443) and not p.fragment,'Unexpected Apple download URL')
    return urlunsplit(('https',p.netloc,p.path,p.query,''))

def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(MIB),b''):h.update(block)
    return h.hexdigest()

def fetch(url,destination,limit,timeout=90,expected_size=None,expected_toc_sha1=None,note=lambda text:None):
    """No insecure fallback, unchecked redirects, overwrite, or shell parsing."""
    path=Path(destination);require(not path.exists(),'Download destination already exists')
    url=apple_url(url);started=time.monotonic()
    for redirect in range(5):
        meter=Meter(note,'Downloading '+path.name,expected_size) if expected_size is not None else None
        part=path.with_name(path.name+'.part-'+str(redirect))
        require(not part.exists(),'Download scratch file already exists')
        # No -L: validate each redirect host before following it ourselves.
        cmd=['/usr/bin/curl','--silent','--show-error','--proto','=https','--connect-timeout','20',
             '--max-time',str(timeout),'--max-filesize',str(limit),'-o',str(part),
             '--write-out','%{http_code}\n%{redirect_url}\n',url]
        process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            while process.poll() is None:
                size=part.stat().st_size if part.exists() else 0
                require(size<=limit,'Download exceeds its permitted size')
                require(time.monotonic()-started<timeout+30,'Download timed out')
                if meter and size<expected_size:meter.update(size)
                time.sleep(.1)
            out,err=process.communicate()
            require(process.returncode==0,'Verified HTTPS download failed: '+err.decode('utf-8','replace')[-1500:])
            fields=out.decode('utf-8').splitlines();require(fields,'Missing download response')
            code=int(fields[0]);require(part.stat().st_size<=limit,'Oversized response')
            if code in (301,302,303,307,308):
                require(len(fields)>1,'Missing redirect location');url=apple_url(fields[1]);continue
            require(code==200,'Apple download returned HTTP '+str(code))
            if expected_size is not None:require(part.stat().st_size==expected_size,'Package size does not match Apple catalog')
            if meter:meter.update(part.stat().st_size,force=True)
            if expected_size is not None:note('Download complete: %d bytes received.'%part.stat().st_size)
            if expected_toc_sha1 is not None:verify_package(part,expected_toc_sha1,note)
            part.rename(path)
            digest=(hash_file(path,note,'Hashing downloaded package') if expected_size is not None else sha256(path))
            return dict(url=url,bytes=path.stat().st_size,sha256=digest)
        finally:
            if process.poll() is None:process.terminate();process.communicate(timeout=10)
    raise ValueError('Too many Apple download redirects')

def exact_models(distribution):
    # Read the literal allow-list, never execute Apple's JavaScript. Matching
    # whole tokens avoids MacBookPro11,1 accidentally matching 11,10.
    root=ET.fromstring(distribution);models=set()
    for script in root.findall('script'):
        for group in re.findall(r'\b(?:var|let|const)\s+(?:models|supportedModels)\s*=\s*\[([^\]]*)\]',script.text or ''):
            for value in re.findall(r'[\'"]([^\'"]+)[\'"]',group):
                if MODEL.fullmatch(value):models.add(value)
    return models

def discover(model,work,note=lambda text:None,fetcher=fetch):
    model=model_identifier(model);work=Path(work);work.mkdir(mode=0o700)
    phase(note,'Reading official Apple driver catalog')
    catalog=work/'catalog.plist';fetcher(CATALOG,catalog,128*MIB)
    products=plistlib.loads(catalog.read_bytes())['Products']
    candidates=[(key,value) for key,value in products.items()
        if 'BootCamp' in value.get('ServerMetadataURL','')]
    require(len(candidates)<=512,'Unexpectedly large Boot Camp catalog')
    note('Checking Apple Boot Camp catalog for '+model+'...')
    def inspect(item):
        key,value=item
        require(re.fullmatch(r'[0-9]{3}-[0-9]{4,8}',key),'Invalid Apple product identifier')
        distribution=value.get('Distributions',{}).get('English') or value.get('Distributions',{}).get('en')
        if not distribution:return None
        path=work/(key+'.dist');fetcher(distribution,path,4*MIB)
        if model not in exact_models(path.read_bytes()):return None
        packages=value.get('Packages',[])
        require(len(packages)==1,'Unrecognised multi-package Boot Camp product')
        pkg=packages[0];size=pkg.get('Size');digest=pkg.get('Digest')
        if type(digest) is bytes:digest=digest.hex()
        require(type(size) is int and 0<size<=8*1024**3 and type(digest) is str
                and re.fullmatch('[0-9a-fA-F]{40}',digest),'Missing usable Apple package size/digest')
        return dict(id=key,model=model,url=apple_url(pkg['URL']),size=size,toc_sha1=digest.lower(),
            post_date=str(value.get('PostDate','')),distribution_sha256=sha256(path))
    # Failure of any distribution is not silently interpreted as "no match".
    meter=Meter(note,'Checking official Apple model matches',len(candidates),'packages')
    matches=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for number,row in enumerate(pool.map(inspect,candidates),1):
            if row:matches.append(row)
            meter.update(number)
    return sorted(matches,key=lambda r:(r['post_date'],r['id']),reverse=True)

def verify_package(package,expected_toc_sha1,note=lambda text:None):
    """Apple's catalog Digest hashes the compressed XAR TOC, NOT the file.

    The HTTPS-authenticated TOC in turn binds every archived member's checksum.
    Retain our separate whole-file SHA256 for reproducibility.
    """
    package=Path(package);length=package.stat().st_size
    phase(note,'Verifying Apple package and authenticated checksums')
    with package.open('rb') as f:
        magic,header,version,packed,expanded,algorithm=struct.unpack('>4sHHQQI',f.read(28))
        require(magic==b'xar!' and version==1 and algorithm==1 and 28<=header<=4096
            and 0<packed<=16*MIB and 0<expanded<=64*MIB,'Unsupported Apple XAR package')
        f.seek(header);toc=f.read(packed)
        require(hashlib.sha1(toc).hexdigest()==expected_toc_sha1,'Package TOC does not match the HTTPS Apple catalog')
        decoder=zlib.decompressobj();xml=decoder.decompress(toc,64*MIB+1)
        require(decoder.eof and len(xml)==expanded,'Invalid authenticated XAR table')
        root=ET.fromstring(xml);heap=header+packed;check=root.find('toc/checksum')
        require(check is not None and check.get('style')=='sha1' and int(check.findtext('size'))==20,
                'Invalid XAR TOC checksum record')
        offset=int(check.findtext('offset'));require(0<=offset<=length-heap-20,'Invalid TOC checksum offset')
        f.seek(heap+offset);require(f.read(20).hex()==expected_toc_sha1,'Stored XAR checksum differs')
        members=root.findall('.//file');require(len(members)<=100000,'Oversized XAR directory')
        total=sum(int(n.findtext('data/length')) for n in members if n.find('data') is not None)
        require(0<=total<=length,'Invalid XAR total member length')
        meter=Meter(note,'Verifying Apple package',total);done=0
        for node in members:
            data=node.find('data')
            if data is None:continue
            offset=int(data.findtext('offset'));left=int(data.findtext('length'))
            require(0<=offset and 0<=left<=length-heap-offset,'Invalid XAR member bounds')
            digest=data.find('archived-checksum')
            require(digest is not None and digest.get('style') in ('sha1','sha256'),'Missing XAR member digest')
            h=hashlib.new(digest.get('style'));f.seek(heap+offset)
            while left:
                block=f.read(min(left,MIB));require(block,'Truncated XAR member');left-=len(block);h.update(block)
                done+=len(block)
                if done<total:meter.update(done,node.findtext('name'))
            require(h.hexdigest()==digest.text.strip().lower(),'Apple package member checksum mismatch: '+node.findtext('name'))
        meter.update(done,force=True);note('Apple package checksum verification complete.')

def extract_payload(package,output,note=lambda text:None):
    """Extract one regular XAR member only; never expand package paths/scripts."""
    package=Path(package);output=Path(output);length=package.stat().st_size
    with package.open('rb') as f:
        magic,header,version,packed,expanded,algorithm=struct.unpack('>4sHHQQI',f.read(28))
        require(magic==b'xar!' and version==1 and 28<=header<=4096 and 0<packed<=16*MIB
                and 0<expanded<=64*MIB,'Unsupported package archive')
        f.seek(header);decoder=zlib.decompressobj();xml=decoder.decompress(f.read(packed),64*MIB+1)
        require(decoder.eof and len(xml)==expanded,'Invalid package table')
        root=ET.fromstring(xml)
        candidates=[n for n in root.findall('toc/file') if n.findtext('name')=='Payload']
        require(len(candidates)==1 and candidates[0].findtext('type')=='file','Package has no single regular Payload')
        data=candidates[0].find('data');offset=int(data.findtext('offset'));count=int(data.findtext('length'))
        size=int(data.findtext('size'));require(0<=offset and 0<count<=length-header-packed-offset
             and 0<size<=8*1024**3,'Invalid package member bounds')
        encoding=data.find('encoding').get('style');require(encoding in ('application/octet-stream','application/x-gzip'),
             'Unsupported package Payload encoding')
        checksum=data.find('archived-checksum');require(checksum is not None and checksum.get('style') in ('sha1','sha256'),
             'Missing package member checksum')
        h=hashlib.new(checksum.get('style'));d=zlib.decompressobj(47) if encoding=='application/x-gzip' else None
        written=0;f.seek(header+packed+offset)
        meter=Meter(note,'Extracting Apple package payload',size)
        with output.open('xb') as out:
            while count:
                block=f.read(min(MIB,count));require(block,'Truncated package');count-=len(block);h.update(block)
                if d:
                    while block:
                        decoded=d.decompress(block,MIB);block=d.unconsumed_tail
                        written+=len(decoded);require(written<=size,'Oversized decoded Payload');out.write(decoded)
                else:written+=len(block);out.write(block)
                if written<size:meter.update(written)
        require(written==size and (d is None or d.eof) and h.hexdigest()==checksum.text.strip().lower(),
                'Payload checksum/size mismatch')
        meter.update(written,force=True)

def extract_dmg(payload,destination,note=lambda text:None):
    """Read cpio metadata and save ONLY WindowsSupport.dmg, never archive paths."""
    import gzip
    import bz2
    raw=Path(payload).open('rb');prefix=raw.read(2);raw.seek(0)
    stream=gzip.GzipFile(fileobj=raw) if prefix==b'\x1f\x8b' else (bz2.BZ2File(raw) if prefix==b'BZ' else raw)
    found=False;total=0;entries=0
    phase(note,'Locating WindowsSupport image in Apple payload')
    def read(n):
        nonlocal total
        require(0<=n<=8*1024**3,'Invalid cpio length');chunks=[]
        while n:
            block=stream.read(min(n,MIB));require(block,'Truncated cpio payload');chunks.append(block);n-=len(block);total+=len(block)
            require(total<=12*1024**3,'Oversized cpio payload')
        return b''.join(chunks)
    try:
        while True:
            entries+=1;require(entries<=100000,'Too many cpio members')
            magic=read(6)
            if magic in (b'070701',b'070702'):
                fields=[int(read(8),16) for _ in range(13)]
                mode,links,size,namesize=fields[1],fields[4],fields[6],fields[11];alignment=4;header=110
            elif magic==b'070707':
                values=[int(read(n),8) for n in (6,6,6,6,6,6,6,11,6,11)]
                mode,links,namesize,size=values[2],values[5],values[8],values[9];alignment=1;header=76
            else:raise ValueError('Unsupported Apple cpio format')
            require(1<=namesize<=4096,'Invalid cpio name');name=read(namesize)
            require(name.endswith(b'\0'),'Unterminated cpio name');name=name[:-1].decode('utf-8')
            read(-(header+namesize)%alignment)
            if name=='TRAILER!!!':break
            parts=name.split('/');require(not name.startswith('/') and '..' not in parts,'Unsafe cpio path')
            match=name.removeprefix('./') if hasattr(name,'removeprefix') else (name[2:] if name.startswith('./') else name)
            selected=match=='Library/Application Support/BootCamp/WindowsSupport.dmg'
            if selected:
                require(not found and stat.S_ISREG(mode) and links==1 and 0<size<=8*1024**3,'Invalid WindowsSupport image')
                out=Path(destination).open('xb');found=True
                meter=Meter(note,'Extracting WindowsSupport image',size)
            else:out=None
            try:
                left=size
                while left:
                    data=read(min(left,MIB));left-=len(data)
                    if out:
                        out.write(data);meter.update(size-left)
            finally:
                if out:out.close()
            read(-size%alignment)
        require(found,'WindowsSupport.dmg not found in Apple package')
    finally:
        stream.close();raw.close()

def unpack_drivers(package,work,destination,note=lambda text:None):
    """Mount only our downloaded image read-only, copy regular files, detach it."""
    work=Path(work);destination=Path(destination)
    payload=work/'Payload';dmg=work/'WindowsSupport.dmg'
    extract_payload(package,payload,note);extract_dmg(payload,dmg,note)
    return copy_driver_image(dmg,work/'mounted',destination,note)

def copy_driver_image(dmg,mount,destination,note=lambda text:None):
    """A CD9660 driver image can mount as a whole disk, not just a slice."""
    dmg=Path(dmg);mount=Path(mount);destination=Path(destination)
    mount.mkdir();attached=False
    try:
        phase(note,'Opening downloaded driver image read-only')
        result=plistlib.loads(subprocess.check_output(['/usr/bin/hdiutil','attach','-readonly','-nobrowse',
            '-noautoopen','-plist','-mountpoint',str(mount),str(dmg)]))
        attached=True
        entities=result.get('system-entities',[])
        matched=[e for e in entities if e.get('mount-point')==str(mount)]
        require(len(matched)==1 and re.fullmatch(r'/dev/disk[0-9]+(?:s[0-9]+)?',matched[0].get('dev-entry','')),
                'Downloaded image mount identity is ambiguous')
        destination.mkdir(mode=0o700);files={};source_hashes={}
        paths=sorted(mount.rglob('*'))
        meter=Meter(note,'Staging original Apple drivers',sum(p.stat().st_size for p in paths if p.is_file()))
        done=0
        for path in paths:
            mode=path.lstat().st_mode
            require(stat.S_ISDIR(mode) or stat.S_ISREG(mode),'Unexpected linked/special driver file')
            if not stat.S_ISREG(mode):continue
            rel=path.relative_to(mount).as_posix();require(rel.casefold() not in files,'Case-colliding driver path')
            target=destination/rel;target.parent.mkdir(parents=True,exist_ok=True)
            h=hashlib.sha256()
            with path.open('rb') as src,target.open('xb') as dst:
                for data in iter(lambda:src.read(MIB),b''):
                    dst.write(data);h.update(data);done+=len(data);meter.update(done,rel)
            source_hashes[rel]=h.hexdigest()
            files[rel.casefold()]=rel
        require(any(p.lower().endswith('/bootcamp.msi') or p.lower().endswith('/bootcamp64.msi') for p in files),
                'No Boot Camp driver installer in downloaded image')
        meter=Meter(note,'Verifying staged Apple drivers',len(files),'files verified')
        for number,rel in enumerate(files.values(),1):
            require(sha256(destination/rel)==source_hashes[rel],'Copied Boot Camp driver differs from Apple image')
            meter.update(number,rel)
        return source_hashes
    finally:
        # Detach the exact private mount we requested, including when a later
        # validation fails. Never infer another disk from its numeric index.
        if attached:
            phase(note,'Closing downloaded driver image')
            subprocess.run(['/usr/bin/hdiutil','detach',str(mount)],check=True)

def prepare_apple(model,family,session,interaction,note,selected_family=None):
    """Everything here finishes before target formatting; caller offers retry/skip."""
    import tempfile
    work=Path(tempfile.mkdtemp(prefix='apple-drivers-',dir=str(session)))
    products=discover(model,work/'metadata',note)
    from apple_driver_policy import choose
    selected=choose(model,family,products,interaction,note)
    overlay=work/'overlay';overlay.mkdir();folder=overlay/'Apple-WindowsSupport'
    if selected is None:
        return dict(root=overlay,receipt=dict(source='Apple-download-skipped',model=model,family=family))
    if selected['kind']=='zip':
        phase(note,'Checking size of official Apple driver archive')
        headers=subprocess.check_output(['/usr/bin/curl','-fsS','--proto','=https','--max-time','30',
            '--head',apple_url(selected['url'])]).decode('iso-8859-1')
        lengths=re.findall(r'(?im)^content-length:\s*([0-9]+)\s*$',headers)
        require(lengths and 0<int(lengths[-1])<=8*1024**3,'Official archive has no usable length')
        selected['size']=int(lengths[-1])
    require(shutil.disk_usage(work).free>selected['size']*5+2*1024**3,'Insufficient macOS space to prepare drivers')
    note('Downloading original Apple Boot Camp '+selected['id']+'; no drivers will run automatically.')
    package=work/('BootCamp.zip' if selected['kind']=='zip' else 'BootCampESD.pkg')
    downloaded=fetch(selected['url'],package,selected['size'],timeout=3600,
        expected_size=selected['size'],expected_toc_sha1=selected.get('toc_sha1'),note=note)
    files=(unpack_official_zip(package,folder,note) if selected['kind']=='zip' else
           unpack_drivers(package,work,folder,note))
    guide=('Original Apple Boot Camp files for '+model+'\r\nSelected Windows: '+family+'\r\n'
        'Complete normal Windows setup first. Then open BootCamp/Setup.exe in this folder (or Setup.exe if at the root).\r\n'
        'Approve the normal Windows UAC prompt. These files have NOT been modified or automatically installed.\r\n'
        'Selection: '+selected['reason']+'\r\n'
        'Package: '+selected['id']+'\r\n'
        'An official driver package does not guarantee compatibility with every Windows version or boot configuration.\r\n')
    if selected_family is not None:
        guide=('UNSUPPORTED OLDER WINDOWS CONFIGURATION\r\nSelected Windows: '+selected_family+
            '\r\nDriver package selected for: '+family+' on '+model+'\r\n'
            'These original Apple drivers are NOT confirmed compatible with the selected older Windows. '
            'Their installers may refuse to run or leave hardware unavailable. No support is provided for this older configuration.\r\n\r\n')+guide
    from apple_driver_policy import DRIVER_TROUBLESHOOTING
    (folder/'Apple-Drivers-READ-ME.txt').write_text(guide+'\r\nDRIVER TROUBLESHOOTING\r\n'+DRIVER_TROUBLESHOOTING+'\r\n')
    receipt=dict(source='Apple',model=model,family=selected_family or family,driver_family_basis=family,
        older_windows_fallback=selected_family is not None,product=selected,download=downloaded,
        scripts_executed=False,drivers_installed=False,files=files)
    (work/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return dict(root=overlay,receipt=receipt)

def unpack_official_zip(package,destination,note):
    """Keep original regular files; validate paths, ZIP CRC and copied SHA256.
    No extractall, links, executable invocation or installer script execution.
    """
    destination=Path(destination);destination.mkdir(mode=0o700)
    with zipfile.ZipFile(package) as archive:
        members=archive.infolist();require(len(members)<=100000,'Excessive driver archive entries')
        picked=[];seen=set()
        for member in members:
            name=member.filename
            require(name and not name.startswith('/') and '\\' not in name and ':' not in name
                and '..' not in name.split('/'),'Unsafe Apple archive path')
            if name.startswith('__MACOSX/') or name.endswith('/.DS_Store'):continue
            parts=name.rstrip('/').split('/')
            if parts[0].lower().startswith('bootcamp5.'):
                parts=parts[1:]
            if not parts:continue
            mode=member.external_attr>>16
            require(not stat.S_ISLNK(mode) and (not stat.S_IFMT(mode) or stat.S_ISREG(mode) or stat.S_ISDIR(mode)),
                    'Linked/special file in Apple archive')
            if member.is_dir():continue
            rel='/'.join(parts)
            require(rel.casefold() not in seen,'Case-colliding Apple archive path');seen.add(rel.casefold())
            picked.append((member,rel))
        total=sum(m.file_size for m,_ in picked);require(0<total<=12*1024**3,'Invalid archive expanded size')
        meter=Meter(note,'Extracting original Apple drivers',total);done=0;files={}
        for member,rel in picked:
            path=destination/rel;path.parent.mkdir(parents=True,exist_ok=True);h=hashlib.sha256()
            with archive.open(member) as src,path.open('xb') as dst:
                for data in iter(lambda:src.read(MIB),b''):
                    dst.write(data);h.update(data);done+=len(data)
                    if done<total:meter.update(done,rel)
            files[rel]=h.hexdigest()
        meter.update(done,force=True)
    require(any(p.lower().endswith('/bootcamp.msi') or p.lower().endswith('/bootcamp64.msi') for p in files),
            'No original Boot Camp installer in archive')
    meter=Meter(note,'Verifying extracted Apple drivers',len(files),'files verified')
    for index,(rel,digest) in enumerate(files.items(),1):
        require(sha256(destination/rel)==digest,'Extracted Apple file differs: '+rel)
        meter.update(index,rel)
    return files
