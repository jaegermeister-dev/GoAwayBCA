"""GitHub-hosted Windows 7 support kit + direct original-publisher drivers.

The tiny support kit contains our installer, a data recipe and a guide. Never
executes downloaded code on macOS. Release pins live in the integrity-checked
app resources, not an unsigned mutable 'latest' remote manifest.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile

import community_driver_pack
import publisher_drivers as publisher
from community_payload import MODELS, require
from operation_progress import phase

PREFIX = 'https://github.com/jaegermeister-dev/component-store/releases/download/'
RECIPE = 'driver-recipe.json'
INSTALLER = community_driver_pack.INSTALLER_NAME
GUIDE = 'READ-ME-FIRST.txt'


def validate_release(release):
    require(type(release) is dict and release.get('schema') == 1, 'Invalid community release pin')
    publisher.file_spec(release)
    if release.get('delivery')=='bundled-private-test':
        require('url' not in release and re.fullmatch(r'private-support/Windows7-Driver-Support-r[1-9][0-9]*\.zip',release.get('asset','')),
                'Invalid bundled private support-kit location')
    else:
        require(release.get('delivery','github')=='github','Unknown support-kit delivery')
        url = publisher.checked_url(release.get('url'))
        require(url.startswith(PREFIX) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\.zip', url[len(PREFIX):]),
                'Support kit must come from the configured versioned support repository')
    files = release.get('files')
    require(type(files) is dict and set(files) == {RECIPE, INSTALLER, GUIDE}, 'Unexpected support-kit contents')
    for name, row in files.items():
        publisher.file_spec(row)
        require(row['bytes'] <= 8*1024**2, 'Oversized support-kit member')
    require(files[INSTALLER]['bytes'] == community_driver_pack.INSTALLER_BYTES
            and files[INSTALLER]['sha256'] == community_driver_pack.INSTALLER_SHA256,
            'Release installer is not the app-validated installer')
    return release


def fetch_kit(release,resources,destination,note):
    validate_release(release)
    if release.get('delivery')!='bundled-private-test':return publisher.fetch(release,destination,note)
    source=Path(resources)/release['asset']
    publisher.regular(source,release)
    destination=Path(destination)
    require(not destination.exists(),'Support-kit destination already exists')
    note('Preparing the verified driver installer bundled with GoAwayBCA.')
    with source.open('rb') as incoming,destination.open('xb') as out:shutil.copyfileobj(incoming,out)
    publisher.regular(destination,release)
    return dict(source='bundled-private-test',asset=release['asset'],bytes=release['bytes'],sha256=release['sha256'])


def unpack_kit(archive, destination, release):
    validate_release(release)
    publisher.regular(Path(archive), release)
    destination = Path(destination)
    require(destination.is_absolute() and destination.resolve() == destination and not destination.exists(),
            'Fresh support-kit directory required')
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        require(len(members) == 3 and {r.filename for r in members} == set(release['files']),
                'Missing/extra support-kit member')
        for member in members:
            mode = member.external_attr >> 16
            row = release['files'][member.filename]
            require(not member.is_dir() and not member.flag_bits & 1 and
                    (stat.S_IFMT(mode) in (0, stat.S_IFREG)) and member.file_size == row['bytes'],
                    'Non-regular or wrong-sized support member')
        destination.mkdir(mode=0o700)
        for member in members:
            row = release['files'][member.filename]
            with zipped.open(member) as stream:
                data = stream.read(row['bytes']+1)
            require(len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256'],
                    'Support-kit member integrity mismatch')
            with (destination/member.filename).open('xb') as out:out.write(data)
    return destination


def prepare(model, family, resources, session, extraction_tools, note):
    require(model in MODELS and family == 'Windows 7', 'Unsupported community driver request')
    resources = Path(resources)
    release = validate_release(json.loads((resources/'community-support-release.json').read_text()))
    work = Path(tempfile.mkdtemp(prefix='community-publisher-', dir=str(session)))
    phase(note, 'Preparing Windows 7 driver support')
    kit = work/'Windows7-Driver-Support.zip'; downloaded = fetch_kit(release,resources,kit,note)
    unpacked = unpack_kit(kit, work/'support', release)
    recipe = publisher.load_recipe(unpacked/RECIPE, release['files'][RECIPE]['sha256'])
    original = publisher.reconstruct(recipe, model, work/'original-drivers', extraction_tools, note)
    overlay = work/'overlay'; overlay.mkdir(mode=0o700)
    result = community_driver_pack.stage(original['inventory'], original['root'], unpacked/INSTALLER,
        overlay/'BackToSevenSupport', model, family, True)
    return dict(root=overlay, receipt=dict(source=('bundled-support-and-original-publishers' if release.get('delivery')=='bundled-private-test' else 'GitHub-support-and-original-publishers'),
        support=downloaded, recipe_id=recipe['id'], sources=original['inventory']['sources'],
        result=result, drivers_executed=False))


def refresh_installer(result, model, family, resources, session, note, extraction_tools=None):
    """Upgrade an old cached setup in a NEW session overlay, never in the cache.

    Reuse all publisher driver bytes only after checking the current recipe.
    Download just the small support kit, not gigabytes of unchanged originals.
    """
    require(model in MODELS and family == 'Windows 7', 'Unsupported community driver request')
    source = Path(result['root'])/'BackToSevenSupport'
    if not source.is_dir():
        legacy = Path(result['root'])/'Community-Windows7-Drivers'
        if legacy.is_dir() and not legacy.is_symlink():source = legacy
    old = source/INSTALLER
    expected = dict(bytes=community_driver_pack.INSTALLER_BYTES,
                    sha256=community_driver_pack.INSTALLER_SHA256)
    try:
        publisher.regular(old, expected)
        if source.name == 'BackToSevenSupport' and community_driver_pack.recovery_matches(source,model):return result
    except (ValueError, OSError):
        pass
    note('Saved community drivers contain an older or changed setup. Preparing the current verified installer; the saved cache is left untouched.')
    release = validate_release(json.loads((Path(resources)/'community-support-release.json').read_text()))
    work = Path(tempfile.mkdtemp(prefix='community-setup-refresh-', dir=str(session)))
    kit = work/'support.zip'; downloaded = fetch_kit(release,resources,kit,note)
    unpacked = unpack_kit(kit, work/'support', release)
    recipe = publisher.load_recipe(unpacked/RECIPE, release['files'][RECIPE]['sha256'])
    # Older caches predate the accepted Intel/HDMI and utility dependencies.
    # Never label an incomplete old set as this build's complete driver pack.
    from community_payload import select_files
    selected = select_files(dict(status='PASS',private_engineering_only=True,files=recipe['files']),model,family,True)
    missing = [row for row in selected if not (source/row['path']).exists()]
    if missing:
        require(extraction_tools is not None,
                'Saved drivers predate required components. Select Download community support; your saved cache is retained.')
        note('Saved drivers lack %d required files. Downloading the current original publisher components; the old cache is retained.'%len(missing))
        original=publisher.reconstruct(recipe,model,work/'original-drivers',extraction_tools,note)
        source=Path(original['root'])
    inventory = dict(status='PASS', private_engineering_only=True, files=recipe['files'])
    overlay = work/'overlay'; overlay.mkdir(mode=0o700)
    staged = community_driver_pack.stage(inventory, source, unpacked/INSTALLER,
        overlay/'BackToSevenSupport', model, family, True)
    receipt = dict(result['receipt'], installer_refreshed=True, support=downloaded,
                   result=staged, drivers_executed=False)
    note('Current driver installer assembled with verified publisher drivers.' if missing else
         'Current driver installer assembled with the verified cached publisher drivers. No vendor re-download was needed.')
    return dict(root=overlay, receipt=receipt)
