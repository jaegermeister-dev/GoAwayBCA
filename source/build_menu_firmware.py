"""Rebuild the included menu from source with the recorded Zig toolchain.

Only creates files in a user-selected build directory. Never installs firmware.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--zig',required=True)
    p.add_argument('--output',required=True)
    a=p.parse_args();root=Path(__file__).resolve().parent
    assert subprocess.check_output([a.zig,'version'],universal_newlines=True).strip()=='0.17.0-dev.2251+1175a3e99'
    out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=False)
    command=[a.zig,'build-exe','-target','x86_64-uefi','-O','ReleaseSafe','-fstrip','--name','Windows',
             '--cache-dir',str(out/'cache'),'--global-cache-dir',str(out/'cache'),
             '-femit-bin='+str(out/'Windows.efi'),str(root/'firmware/menu/system_store_chooser.zig')]
    subprocess.run(command,check=True)
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest(out/'Windows.efi')==digest(root/'efi/Windows.efi'),'Rebuilt menu differs from the included release payload'
    print('PASS: rebuilt Windows.efi matches the included payload; nothing installed.')

if __name__=='__main__':main()
