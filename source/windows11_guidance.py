"""Selected-WIM build guidance; no setup, activation or CPU modifications.

Microsoft release history identifies 24H2/IoT LTSC 2024 as build 26100.
Enterprise/IoT names alone do not identify a release: older editions exist.
https://learn.microsoft.com/windows/iot/iot-enterprise/whats-new/release-history
"""
import subprocess

def read_features():
    try:
        result=subprocess.run(['/usr/sbin/sysctl','-n','machdep.cpu.features'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5,check=True)
        text=result.stdout.decode('ascii').strip()
        return frozenset(text.upper().split()) if text else None
    except (OSError,subprocess.SubprocessError,UnicodeError):return None


def advisory(edition,features):
    if edition.get('family')!='Windows 11':return None
    version=edition.get('version')
    if not isinstance(version,(tuple,list)) or len(version)!=3 or not all(type(v) is int for v in version):
        return ('The selected Windows 11 build could not be established. CPU compatibility with '
                '24H2 or newer cannot be confirmed. Choose a verified ISO or try anyway at your own risk.')
    if version[2]<26100:return None
    if features is not None and {'POPCNT','SSE4.2'}.issubset(features):return None
    missing=('POPCNT/SSE4.2 support could not be determined' if features is None else
             'the CPU does not report '+', '.join(f for f in ('POPCNT','SSE4.2') if f not in features))
    return ('This selected Windows 11 image is build %d (24H2 or newer), but %s. '
            'It may be unable to boot. Consider a genuine Windows 11 23H2 or earlier ISO instead; '
            'older releases may be out of support. You may still try this image if you accept that risk.'
            %(version[2],missing))


def configure(edition,interaction,read=read_features):
    if edition.get('family')!='Windows 11':return None
    version=edition.get('version')
    needs_check=not isinstance(version,(tuple,list)) or len(version)!=3 or type(version[2]) is not int or version[2]>=26100
    features=read() if needs_check else None
    warning=advisory(edition,features)
    if warning and interaction.choose(warning,['Choose another ISO / cancel','Try anyway'])!=1:
        raise ValueError('Cancelled after Windows 11 CPU guidance; no partition was formatted')
    # Successful CPU checks stay out of dialogs/logs as requested; retain only
    # the machine-readable receipt, not a claim of full Microsoft eligibility.
    return dict(cpu_check_required=needs_check,cpu_warning_shown=bool(warning),
                continued_despite_cpu_warning=bool(warning))
