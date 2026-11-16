"""Pinned original-publisher driver reconstruction; never runs an installer.

Recipe JSON is DATA, not a downloaded program. The caller must pin its digest
before loading it. Output paths, URLs, sizes, hashes and models are validated
again here. Package/executable trust checks are mandatory, not optional copy
verification. Everything finishes before any Windows partition is formatted.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

from community_payload import MODELS, SHARED, AMD, INTEL, require, relative, digest
from operation_progress import Meter, phase, hash_file

HOSTS = frozenset(('swcdn.apple.com', 'www2.ati.com', 'drivers.amd.com',
    'download.amd.com', 'download.lenovo.com', 'download.microsoft.com',
    'github.com', 'release-assets.githubusercontent.com'))
MIB = 1024**2
AMD_DOWNLOAD_HOSTS = frozenset(('www2.ati.com','drivers.amd.com','download.amd.com'))


def checked_url(value):
    require(type(value) is str and not any(ord(c)<33 for c in value), 'Invalid publisher URL')
    p = urlsplit(value)
    require(p.scheme == 'https' and p.hostname in HOSTS and not p.username and not p.password
            and p.port in (None, 443) and not p.fragment and p.path.startswith('/'),
            'Download is not an approved HTTPS publisher')
    return value


def file_spec(row):
    require(type(row) is dict, 'Invalid pinned file record')
    require(type(row.get('bytes')) is int and 0 < row['bytes'] <= 8*1024**3, 'Invalid pinned file size')
    require(type(row.get('sha256')) is str and re.fullmatch('[0-9a-f]{64}', row['sha256']),
            'Invalid pinned SHA256')


def member_name(value):
    relative(value)
    require(not any(c in value for c in '*?\n\r') and not value.startswith(('-', '@')),
            'Unsafe archive member selector')
    return value


def load_recipe(path, expected_sha256):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 2*MIB,
            'Invalid recipe file')
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == expected_sha256, 'Driver recipe integrity mismatch')
    return json.loads(data.decode('utf-8'))


def validate(recipe, model):
    require(type(recipe) is dict and recipe.get('schema') == 1 and recipe.get('family') == 'Windows 7'
            and recipe.get('models') == list(MODELS) and model in MODELS, 'Unsupported community driver recipe/model')
    sources, containers, files = recipe.get('sources'), recipe.get('containers'), recipe.get('files')
    require(type(sources) is dict and 0 < len(sources) <= 16 and type(containers) is dict
            and len(containers) <= 64 and type(files) is list and 0 < len(files) <= 4096,
            'Invalid driver recipe structure')
    require(not set(sources).intersection(containers), 'Duplicate recipe object identifier')
    objects = dict(sources, **containers)
    for key, row in objects.items():
        require(re.fullmatch('[a-z][a-z0-9-]{0,63}', key) and type(row) is dict, 'Invalid recipe object')
        file_spec(row)
        require(row.get('kind') in ('7zip', 'inno', 'file', 'apple-pkg'), 'Unknown extraction operation')
        if key in sources:
            checked_url(row.get('url'))
            if row['kind'] == 'apple-pkg':
                require(re.fullmatch('[0-9a-f]{40}', row.get('toc_sha1', '')), 'Missing Apple TOC pin')
        else:
            member_name(row.get('member'))
            if 'byte_offset' in row:
                offset=row['byte_offset']; parent=objects.get(row.get('parent'),{})
                require(type(offset) is int and offset>=0 and offset+row['bytes']<=parent.get('bytes',0) and parent.get('kind')=='7zip', 'Invalid pinned archive slice')
            require(row.get('parent') in objects and row['kind'] != 'apple-pkg', 'Invalid archive parent')
            seen = {key}; parent = row['parent']
            while parent in containers:
                require(parent not in seen, 'Cyclic archive recipe'); seen.add(parent)
                parent = containers[parent].get('parent')
                require(parent in objects, 'Unknown archive ancestor')
    seen = set(); selected = []; components = set()
    for row in files:
        file_spec(row)
        path = relative(row.get('path')); parts = path.split('/')
        require(len(parts) >= 3 and parts[0] == 'Payload' and parts[1] in SHARED+AMD+INTEL
                and row.get('component') == parts[1], 'Unexpected driver output path')
        require(path.casefold() not in seen, 'Duplicate/case-colliding driver output'); seen.add(path.casefold())
        expected_models = (['MacBookPro11,5'] if parts[1] in AMD else ['MacBookPro11,4'] if parts[1] in INTEL else list(MODELS))
        require(row.get('models') == expected_models, 'Driver model policy mismatch')
        parent = row.get('parent'); require(parent in objects, 'Unknown driver parent')
        if objects[parent]['kind'] == 'file':
            require(row.get('member') == '' and all(row[x] == objects[parent][x] for x in ('bytes', 'sha256')),
                    'Direct-file pin differs from its source')
        else:member_name(row.get('member'))
        if model in expected_models:
            selected.append(row); components.add(parts[1])
    require(components == set(SHARED + (AMD if model == 'MacBookPro11,5' else INTEL)),
            'Incomplete community components')
    return selected


def regular(path, spec):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path and stat.S_ISREG(path.lstat().st_mode)
            and path.stat().st_size == spec['bytes'] and digest(path) == spec['sha256'],
            'Original driver/package integrity mismatch: ' + path.name)
    return path


def fetch(spec, destination, note):
    """No automatic redirect trust, insecure fallback, overwrite, or execution."""
    file_spec(spec); url = checked_url(spec['url']); destination = Path(destination)
    require(not destination.exists(), 'Publisher destination already exists')
    for attempt in range(5):
        part = destination.with_name(destination.name + '.part-' + str(attempt))
        require(not part.exists(), 'Publisher scratch path already exists')
        meter = Meter(note, 'Downloading ' + destination.name, spec['bytes'])
        # AMD's public download CDN expects a publisher-page reference. This
        # contains no user/session information, is sent only to AMD's download
        # hosts and does not replace HTTPS, size or digest authentication.
        reference = ['--referer','https://www.amd.com/'] if urlsplit(url).hostname in AMD_DOWNLOAD_HOSTS else []
        cmd = ['/usr/bin/curl', '--silent', '--show-error', '--proto', '=https',
            '--connect-timeout', '20', '--max-time', '3600', '--max-filesize', str(spec['bytes']),
            '-o', str(part), '--write-out', '%{http_code}\n%{redirect_url}\n'] + reference + [url]
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors)
            try:
                start = time.monotonic()
                while process.poll() is None:
                    size = part.stat().st_size if part.exists() else 0
                    require(size <= spec['bytes'] and time.monotonic()-start < 3630,
                            'Publisher response exceeded bounds')
                    if size < spec['bytes']:meter.update(size)
                    time.sleep(.1)
                stdout = process.communicate()[0].decode('utf-8').splitlines()
                errors.seek(0); error = errors.read(1500).decode('utf-8', 'replace')
                require(process.returncode == 0 and stdout, 'Publisher download failed: ' + error)
                code = int(stdout[0])
                if code in (301, 302, 303, 307, 308):
                    require(len(stdout) == 2, 'Missing redirect location'); url = checked_url(stdout[1]); continue
                require(code == 200, 'Publisher returned HTTP ' + str(code))
                require(part.stat().st_size == spec['bytes'], 'Publisher package length mismatch')
                meter.update(spec['bytes'], force=True); note('Download complete: ' + destination.name)
                require(hash_file(part, note, 'Authenticating publisher package') == spec['sha256'],
                        'Publisher package SHA256 mismatch')
                part.rename(destination)
                return dict(url=url, bytes=spec['bytes'], sha256=spec['sha256'])
            finally:
                if process.poll() is None:process.terminate()
                process.communicate(timeout=10)
    raise ValueError('Too many publisher redirects')


def listing(text):
    """7-Zip -ba -slt output: order retained for a bounded stdout extraction."""
    require(len(text) <= 8*MIB, 'Oversized archive directory')
    records = []; seen = set()
    for block in re.split(r'\r?\n\r?\n', text.strip('\r\n')):
        if not block.strip():continue
        row = {}
        for line in block.splitlines():
            if ' = ' not in line:continue
            key, value = line.split(' = ', 1)
            require(key not in row, 'Ambiguous archive record'); row[key] = value
        if 'Path' not in row:continue
        name = row['Path'].replace('\\', '/')
        member_name(name)
        require(name.casefold() not in seen, 'Ambiguous/case-colliding archive member'); seen.add(name.casefold())
        require('Symbolic Link' not in row and 'Hard Link' not in row, 'Linked archive entry refused')
        if row.get('Folder') == '+' or row.get('Attributes', '').startswith('D'):continue
        # NSIS archives omit unpacked lengths in their directory. Their exact
        # lengths still come from our pinned recipe; the bounded stream and
        # SHA256 checks below remain mandatory. A malformed length is refused.
        size = row.get('Size')
        require(size == '' or (type(size) is str and size.isdigit()), 'Invalid archive member length')
        records.append((name, int(size) if size else None, row['Path']))
    require(0 < len(records) <= 100000, 'Invalid archive directory')
    return records


def extract_seven(tool, archive, requests, note):
    """One decode pass. Vendor archive paths NEVER become filesystem writes.

7-Zip writes selected member bytes to stdout in archive order. Each member is
bounded and hashed into a caller-chosen exclusive output path. Missing, extra,
reordered or modified data fails before the staging result is accepted.
"""
    wanted = {r['member']: r for r in requests}
    require(len(wanted) == len(requests), 'Duplicate extraction request')
    data = subprocess.check_output([str(tool), 'l', '-ba', '-slt', '-sccUTF-8', '-spd', '--', str(archive)], timeout=120)
    entries = [r for r in listing(data.decode('utf-8')) if r[0] in wanted]
    require(len(entries) == len(wanted) and all(size in (None, wanted[name]['bytes']) for name, size, raw in entries),
            'Pinned archive members missing or wrong size')
    entries = [(name, wanted[name]['bytes'], raw) for name, size, raw in entries]
    total = sum(r['bytes'] for r in requests); done = 0
    meter = Meter(note, 'Extracting verified driver files', total)
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([str(tool), 'x', '-so', '-spd', '-y', '--', str(archive)] +
            [raw for name, size, raw in entries], stdout=subprocess.PIPE, stderr=errors)
        timed_out = threading.Event()
        def expire():
            timed_out.set()
            if process.poll() is None:process.kill()
        timer = threading.Timer(600, expire); timer.daemon = True; timer.start()
        try:
            for name, size, raw in entries:
                row = wanted[name]; path = Path(row['output']); path.parent.mkdir(parents=True, exist_ok=True)
                require(path.resolve() == path and not path.exists(), 'Unsafe/reused extraction destination')
                h = hashlib.sha256(); left = size
                with path.open('xb') as output:
                    while left:
                        block = process.stdout.read(min(left, MIB)); require(block, 'Truncated archive member: ' + name)
                        left -= len(block); h.update(block); output.write(block); done += len(block)
                        if done < total:meter.update(done, name)
                require(h.hexdigest() == row['sha256'], 'Extracted driver SHA256 mismatch: ' + name)
            require(process.stdout.read(1) == b'', 'Unexpected extra extracted data')
            code = process.wait(); errors.seek(0)
            require(code == 0 and not timed_out.is_set(), 'Extraction failed: '+errors.read(1500).decode('utf-8', 'replace'))
            meter.update(done, force=True)
        finally:
            timer.cancel()
            if process.poll() is None:process.kill()
            process.wait()


def extract_inno(tool, archive, requests, work, note):
    """Extract only exact selected files into a fresh, private scratch tree."""
    scratch = Path(tempfile.mkdtemp(prefix='inno-', dir=str(work)))
    args = [str(tool), '--extract', '--silent', '--output-dir', str(scratch)]
    for row in requests:args += ['--include', member_name(row['member'])]
    args.append(str(archive))
    phase(note, 'Extracting original Wi-Fi package without running its installer')
    subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    wanted = {r['member']: r for r in requests}; seen = set()
    for path in scratch.rglob('*'):
        require(not path.is_symlink() and path.resolve() == path, 'Linked Inno output')
        if path.is_dir():continue
        name = path.relative_to(scratch).as_posix()
        require(name in wanted and name not in seen, 'Unexpected Inno output')
        seen.add(name); row = wanted[name]; regular(path, row)
        target = Path(row['output']); target.parent.mkdir(parents=True, exist_ok=True)
        require(target.resolve() == target and not target.exists(), 'Unsafe/reused Inno destination')
        with path.open('rb') as source, target.open('xb') as out:shutil.copyfileobj(source, out, MIB)
        regular(target, row)
    require(seen == set(wanted), 'Incomplete Inno extraction')


def reconstruct(recipe, model, work, tools, note, original_sources=None, apple_folder=None):
    """Build a new payload tree. Audit-only optional inputs are fully rehashed.

App callers download sources normally. Existing original_sources / apple_folder
are developer audit inputs, never silently discovered or trusted by pathname.
"""
    selected = validate(recipe, model); work = Path(work)
    require(work.is_absolute() and work.resolve() == work and not work.exists()
            and work.parent.is_dir(), 'Use a new canonical reconstruction directory')
    work.mkdir(mode=0o700); destination = work/'drivers'; destination.mkdir(mode=0o700)
    source_dir = work/'sources'; source_dir.mkdir(mode=0o700)
    objects = dict(recipe['sources'], **recipe['containers'])
    required = {r['parent'] for r in selected}
    for key in list(required):
        while key in recipe['containers']:
            key = objects[key]['parent']; required.add(key)
    paths = {}; downloads = {}
    for key, spec in recipe['sources'].items():
        if key not in required:continue
        if original_sources and key in original_sources:
            path = regular(Path(original_sources[key]).resolve(), spec)
            downloads[key] = dict(source='explicit-audit-original', sha256=spec['sha256'])
        else:
            path = source_dir/(key+'.package'); downloads[key] = fetch(spec, path, note)
        paths[key] = path
        if spec['kind'] == 'apple-pkg':
            import bootcamp_download as apple
            apple.verify_package(path, spec['toc_sha1'], note)
            if apple_folder is None:
                unpack = work/'apple-unpack'; unpack.mkdir()
                apple_folder = work/'apple-originals'
                apple.unpack_drivers(path, unpack, apple_folder, note)
            paths[key] = Path(apple_folder).resolve()
    pending = {k for k in required if k in recipe['containers']}
    while pending:
        ready = sorted(k for k in pending if objects[k]['parent'] in paths)
        require(ready, 'Unresolved archive dependencies')
        for key in ready:
            spec = objects[key]; parent = spec['parent']; output = work/(key+'.archive')
            row = dict(spec, output=str(output))
            if 'byte_offset' in spec:
                regular(paths[parent],objects[parent])
                with paths[parent].open('rb') as inp, output.open('xb') as out:
                    inp.seek(spec['byte_offset']); data=inp.read(spec['bytes'])
                    require(len(data)==spec['bytes'], 'Truncated archive slice'); out.write(data)
                regular(output,spec)
            elif objects[parent]['kind'] == 'apple-pkg':
                source = regular(paths[parent]/spec['member'], spec)
                with source.open('rb') as inp, output.open('xb') as out:shutil.copyfileobj(inp, out, MIB)
                regular(output, spec)
            else:extract_seven(tools['7zip'], paths[parent], [row], note)
            paths[key] = output; pending.remove(key)
    groups = {}
    for row in selected:
        groups.setdefault(row['parent'], []).append(dict(row, output=str(destination/row['path'])))
    for key, requests in groups.items():
        kind = objects[key]['kind']; phase(note, 'Preparing ' + requests[0]['component'])
        if kind == '7zip':extract_seven(tools['7zip'], paths[key], requests, note)
        elif kind == 'inno':extract_inno(tools['inno'], paths[key], requests, work, note)
        elif kind in ('file', 'apple-pkg'):
            for row in requests:
                target = Path(row['output']); target.parent.mkdir(parents=True, exist_ok=True)
                source = paths[key]/row['member'] if kind == 'apple-pkg' else paths[key]
                regular(source, row)
                with source.open('rb') as inp, target.open('xb') as out:shutil.copyfileobj(inp, out, MIB)
                regular(target, row)
        else:raise ValueError('Unsupported final driver extraction')
    rows = [{key:r[key] for key in ('path', 'bytes', 'sha256', 'models')} for r in selected]
    for row in rows:regular(destination/row['path'], row)
    result = dict(status='PASS', schema=1, private_engineering_only=True, files=rows,
        recipe_id=recipe['id'], model=model, sources=downloads, drivers_executed=False,
        reconstructed_from_publishers=True, ready_for_release=False)
    (work/'reconstruction.json').write_text(json.dumps(result, indent=2)+'\n')
    note('Original driver reconstruction complete: %d files verified.' % len(rows))
    return dict(root=destination, inventory=result)
