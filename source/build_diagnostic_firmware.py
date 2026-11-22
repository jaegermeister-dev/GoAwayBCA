"""Reproduce the target-binding EFI template without installing it."""
import argparse
import hashlib
from pathlib import Path
import subprocess

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zig',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    assert subprocess.check_output([args.zig,'version'],universal_newlines=True).strip()=='0.17.0-dev.2251+1175a3e99'
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=False)
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    for name,source in (('PCI','diagnostic-efi/pci_collector.zig'),
                        ('ACPI','diagnostic-efi/acpi_collector.zig'),
                        ('WindowsDiagnostics.template','firmware/menu/boot_diagnostic.zig')):
        binary=output/(name+'.efi')
        subprocess.run([args.zig,'build-exe','-target','x86_64-uefi','-O','ReleaseSafe','-fstrip',
            '--name',name,'--cache-dir',str(output/'cache'),
            '--global-cache-dir',str(output/'cache'),'-femit-bin='+str(binary),str(root/source)],check=True)
        assert digest(binary)==digest(root/'diagnostic-efi'/(name+'.efi')), 'Rebuilt firmware differs: '+name
    print('PASS: all three diagnostic EFI binaries match; nothing installed.')

if __name__=='__main__':main()
