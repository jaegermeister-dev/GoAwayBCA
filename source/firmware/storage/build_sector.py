"""Reproduce the private x64 sector helper with the bundled pinned GNU-EFI source."""
from pathlib import Path
import argparse,hashlib,json,subprocess,xml.etree.ElementTree as E
p=argparse.ArgumentParser()
for name in ('gnu-efi','output','clang','lld'):p.add_argument('--'+name,required=True)
a=p.parse_args();gnu=Path(a.gnu_efi).resolve();out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=False)
source=Path(__file__).with_name('win7_sector_boot.c');commands=[]
base=[a.clang,'--target=x86_64-pc-windows-msvc','-ffreestanding','-fshort-wchar','-fno-builtin',
    '-fno-stack-protector','-mno-red-zone','-mno-stack-arg-probe','-ffunction-sections','-fdata-sections',
    '-Os','-std=gnu11','-DHAVE_USE_MS_ABI','-D__MAKEWITH_GNUEFI']
for name in ('inc','inc/x86_64','inc/protocol'):base+=['-I',str(gnu/name)]
def run(command):
    commands.append([str(x) for x in command]);subprocess.run(command,check=True)
own=out/'sector.obj'
run(base+['-Wall','-Wextra','-Werror','-c',str(source),'-o',str(own)])
ns={'m':'http://schemas.microsoft.com/developer/msbuild/2003'}
proj=gnu/'.vs/efilib.vcxproj';objects=[]
for item in E.parse(proj).findall('.//m:ClCompile[@Include]',ns):
    file=(proj.parent/item.attrib['Include'].replace('\\','/')).resolve()
    if any(x in file.parts for x in ('ia32','arm','aarch64')):continue
    obj=out/('gnu-'+file.relative_to(gnu.parent).as_posix().replace('/','_')+'.obj')
    run(base+['-Wno-incompatible-pointer-types','-Wno-visibility','-Wno-inconsistent-dllimport','-Wno-pragma-pack',
        '-D_UNICODE','-DUNICODE','-DGRUB_FILE=__FILE__','-ffile-prefix-map='+str(gnu.parent)+'=efifs',
        '-c',str(file),'-o',str(obj)])
    objects.append(obj)
# The original tested link used the directory's lexical object enumeration.
objects.sort(key=lambda x:x.name)
binary=out/'Win7SectorBoot.efi'
run([a.lld,'-flavor','link','/nodefaultlib','/entry:efi_main','/subsystem:efi_application,1.10',
    '/machine:x64','/opt:ref','/opt:icf','/dynamicbase','/timestamp:0','/out:'+str(binary),str(own),*[str(x) for x in objects]])
result=dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),bytes=binary.stat().st_size,commands=commands)
(out/'build.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='commands'}))
