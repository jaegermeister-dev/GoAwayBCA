"""Image-specific CPU advisories from actual CPUID/instruction observations.

No model/marketing-name inference, system changes or eligibility certification.
Users can acknowledge missing/unknown CPU capabilities and try anyway; the
separate EFI architecture and partition guards cannot be overridden here.
Sources and scope are recorded in CPU-COMPATIBILITY-STATUS-20261108.txt.
"""
import json
from pathlib import Path
import subprocess

HELPER = Path(__file__).resolve().parent / 'bin/cpu-probe'
FEATURES = ('X64', 'PAE', 'NX', 'SSE2', 'SSE4.1', 'SSE4.2', 'POPCNT',
            'CMPXCHG16B', 'LAHF/SAHF', 'PREFETCHW')
FAMILIES = ('Windows 7', 'Windows 8', 'Windows 8.1', 'Windows 10', 'Windows 11')
MAX_OUTPUT = 8192


def unknown(reason):
    return dict(status='unknown', reason=reason, features={name: None for name in FEATURES},
                source='bundled native CPUID probe', evidence={})


def parse(raw, prefetch_returncode=None):
    try:
        if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT:
            raise ValueError('Invalid probe output size')
        value = json.loads(raw.decode('ascii'))
        if not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1:
            raise ValueError('Invalid probe schema')
        for key in ('vendor', 'brand'):
            if not isinstance(value.get(key), str) or len(value[key]) > 100:
                raise ValueError('Invalid CPU identity')
        for key in ('max_basic', 'max_extended'):
            if type(value.get(key)) is not int or not 0 <= value[key] <= 0xffffffff:
                raise ValueError('Invalid CPUID maximum')
        for key in ('leaf1', 'extended1'):
            if (not isinstance(value.get(key), list) or len(value[key]) != 4 or
                    any(type(x) is not int or not 0 <= x <= 0xffffffff for x in value[key])):
                raise ValueError('Invalid CPUID registers')
    except (ValueError, UnicodeError, TypeError):
        return unknown('CPU probe returned malformed evidence')
    # A missing leaf is unknown, never evidence of zero feature bits.
    def bit(leaf, register, position):
        available = value['max_basic'] >= 1 if leaf == 'leaf1' else value['max_extended'] >= 0x80000001
        return bool(value[leaf][register] & (1 << position)) if available else None
    features = dict(zip(FEATURES, (
        bit('extended1', 3, 29), bit('leaf1', 3, 6), bit('extended1', 3, 20),
        bit('leaf1', 3, 26), bit('leaf1', 2, 19), bit('leaf1', 2, 20), bit('leaf1', 2, 23),
        bit('leaf1', 2, 13), bit('extended1', 2, 0),
        True if type(prefetch_returncode) is int and prefetch_returncode == 0 else
        False if type(prefetch_returncode) is int and prefetch_returncode == 77 else None)))
    value['prefetchw_advertised'] = bit('extended1', 2, 8)
    value['prefetchw_probe_exit'] = prefetch_returncode if type(prefetch_returncode) is int else None
    return dict(status='observed', reason='', features=features,
                source='bundled native CPUID and isolated PREFETCHW execution', evidence=value)


def read_cpu(helper=HELPER, runner=None):
    runner = subprocess.run if runner is None else runner
    common = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
    try:
        result = runner([str(helper)], **common)
        if result.returncode != 0:
            return unknown('CPU probe could not run')
    except (OSError, subprocess.SubprocessError):
        return unknown('CPU probe is unavailable or timed out')
    prefetch_code = None
    try:
        prefetch_code = runner([str(helper), '--prefetchw'], **common).returncode
    except (OSError, subprocess.SubprocessError):
        pass
    return parse(result.stdout, prefetch_code)


def requirements(edition):
    family, arch = edition.get('family'), edition.get('architecture')
    if family not in FAMILIES or type(arch) is not int or arch not in (0, 9):
        raise ValueError('Unsupported Windows image for CPU assessment')
    needed = ['X64'] if arch == 9 else []
    if family != 'Windows 7':
        needed += ['PAE', 'NX', 'SSE2']
    if arch == 9 and family in ('Windows 8.1', 'Windows 10', 'Windows 11'):
        needed += ['CMPXCHG16B', 'LAHF/SAHF', 'PREFETCHW']
    version = edition.get('version')
    build = version[2] if (isinstance(version, (tuple, list)) and len(version) == 3 and
                          all(type(n) is int and n >= 0 for n in version)) else None
    release_unknown = family == 'Windows 11' and (build is None or
                        (build < 26100 and build not in (22000, 22621, 22631)))
    if family == 'Windows 11' and build is not None and build >= 26100:
        # Actual stock 24H2+ execution requirements, not Microsoft's supported
        # CPU model list. Do not backdate these to 21H2/22H2/23H2. SSE4.1 is
        # used by newer setup/user-mode components; SSE4.2/POPCNT by boot paths.
        needed += ['SSE4.1', 'POPCNT', 'SSE4.2']
    return dict(family=family, architecture=arch, build=build, required=needed,
                build_unknown=family == 'Windows 11' and build is None,
                release_requirements_unknown=release_unknown)


def assess(edition, observation):
    rule = requirements(edition)
    features = observation.get('features', {}) if isinstance(observation, dict) else {}
    if not isinstance(features, dict):
        features = {}
    missing = [name for name in rule['required'] if features.get(name) is False]
    uncertain = [name for name in rule['required'] if type(features.get(name)) is not bool]
    messages = []
    if missing:
        messages.append('This processor does not meet the checked CPU capability requirements for '+
                        rule['family']+': '+', '.join(missing)+'. Windows may fail to boot or run correctly.')
    if uncertain:
        messages.append('The app could not verify these CPU capabilities for the selected image: '+
                        ', '.join(uncertain)+'. Unknown results do not mean the features are absent.')
    if rule['release_requirements_unknown']:
        reason=('The selected Windows 11 build could not be established' if rule['build_unknown'] else
                'The selected Windows 11 build '+str(rule['build'])+' is not a recognized released baseline')
        messages.append(reason+', so its additional instruction requirements could not be evaluated. '
                        'Preview builds can introduce requirements before the final release.')
    if rule['family'] == 'Windows 11' and (rule['release_requirements_unknown'] or
            any(name in ('SSE4.1', 'POPCNT', 'SSE4.2') for name in missing + uncertain)):
        messages.append('For the 24H2-or-newer instruction requirements, consider a genuine Windows 11 '
                        '23H2 or earlier image instead. Earlier releases still have their own requirements.')
    if messages:
        messages.append('You may choose another image or try anyway if you accept that Windows may not boot. '
                        'This does not override the separate EFI or partition checks.')
    return dict(rule=rule, missing=missing, unknown=uncertain, warning='\n\n'.join(messages),
                observation=observation, full_windows_eligibility_claimed=False)


def configure(edition, interaction, reader=None):
    observation = (reader or read_cpu)()
    result = assess(edition, observation)
    if result['warning']:
        answer = interaction.choose(result['warning'], ['Choose another image / cancel', 'Try anyway'])
        if type(answer) is not int or answer != 1:
            raise ValueError('Cancelled after Windows CPU compatibility guidance; no partition was formatted')
    result['continued_despite_cpu_warning'] = bool(result['warning'])
    return result
