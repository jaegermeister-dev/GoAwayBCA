from pathlib import Path
import subprocess,xml.etree.ElementTree as E,json,hashlib,shutil
import argparse
parser=argparse.ArgumentParser(description='Reproduce the EFI1.x-compatible x64 NTFS reader from the bundled pinned source archives.')
parser.add_argument('--source',required=True,help='Extract EfiFs and its pinned grub/gnu-efi submodules here first')
parser.add_argument('--output',required=True,help='New build output directory')
parser.add_argument('--clang',required=True)
parser.add_argument('--lld',required=True,help='LLVM ld.lld executable, with COFF link flavor')
args=parser.parse_args()
S=Path(args.source).resolve();R=Path(args.output).resolve();R.mkdir(parents=True,exist_ok=True)
O=R/'coff';O.mkdir(exist_ok=True)
CL=Path(args.clang);LD=Path(args.lld)
ns={'m':'http://schemas.microsoft.com/developer/msbuild/2003'}
def files(proj):
 return [(proj.parent/x.attrib['Include'].replace('\\','/')).resolve() for x in E.parse(proj).findall('.//m:ClCompile[@Include]',ns)]
commands=[]
def run(cmd):
 commands.append([str(x) for x in cmd]);r=subprocess.run(cmd,capture_output=True,text=True)
 if r.returncode:print(json.dumps(dict(command=[str(x) for x in cmd[:4]],returncode=r.returncode,stdout=r.stdout,stderr=r.stderr)));raise SystemExit(1)
 return r
if not (R/'grub-patch-applied.txt').exists():
 run(['git','-C',str(S/'grub'),'apply','--unsafe-paths',str(S/'0001-GRUB-fixes.patch')]);(R/'grub-patch-applied.txt').write_text('Applied upstream bundled patch to matching GRUB submodule\n')
base=[str(CL),'--target=x86_64-pc-windows-msvc','-ffreestanding','-fshort-wchar','-fno-builtin','-fno-stack-protector','-mno-red-zone','-mno-stack-arg-probe','-ffunction-sections','-fdata-sections','-Os','-std=gnu11','-Wno-incompatible-pointer-types','-Wno-visibility','-Wno-inconsistent-dllimport','-Wno-pragma-pack', '-D_UNICODE','-DUNICODE','-D__MAKEWITH_GNUEFI','-DHAVE_USE_MS_ABI','-DGRUB_FILE=__FILE__']
shutil.copytree(S/'grub/include/grub/x86_64',S/'grub/include/grub/cpu',dirs_exist_ok=True)
for inc in ['.','gnu-efi/inc','gnu-efi/inc/x86_64','gnu-efi/inc/protocol','grub/include','grub/grub-core/lib/minilzo','grub/grub-core/lib/zstd']:base+=['-I',str(S/inc)]
base+=['-ffile-prefix-map='+str(S)+'=efifs']
objs=[]
for group,proj in [('gnu',S/'gnu-efi/.vs/efilib.vcxproj'),('grub',S/'.vs/grub.vcxproj'),('efifs',S/'.vs/efifs.vcxproj'),('ntfs',S/'.vs/ntfs.vcxproj')]:
 for source in files(proj):
  if group=='gnu' and any(x in source.parts for x in ['ia32','arm','aarch64']):continue
  if group=='grub' and any(x in source.parts for x in ['minilzo','zstd']):continue
  obj=O/(group+'-'+source.relative_to(S).as_posix().replace('/','_')+'.obj')
  defs=[]
  if group=='grub':defs=['-DGRUB','-DLZO_CFG_FREESTANDING']
  if group=='ntfs':defs=['-DDRIVERNAME=ntfs','-DDRIVERNAME_STR="NTFS"','-DCOMPRESSED_DRIVERNAME=ntfscomp']
  run(base+defs+['-c',str(source),'-o',str(obj)]);objs.append(obj)
out=R/'Ntfs4Kn.efi'
support=R/'compiler_support.c';support.write_text('/* COFF floating-point-use marker; no runtime dependency. */\nint _fltused = 0;\n')
support_obj=O/'compiler_support.obj';run(base+['-c',str(support),'-o',str(support_obj)]);objs.append(support_obj)
run([str(LD),'-flavor','link','/nodefaultlib','/entry:FSDriverInstall','/subsystem:efi_boot_service_driver','/machine:x64','/opt:ref','/opt:icf','/dynamicbase','/timestamp:0','/out:'+str(out)]+[str(x) for x in objs])
(R/'build-commands.json').write_text(json.dumps(commands,indent=2))
print(json.dumps(dict(output=str(out),bytes=out.stat().st_size,sha256=hashlib.sha256(out.read_bytes()).hexdigest())))


