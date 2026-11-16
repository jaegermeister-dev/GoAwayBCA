"""Pure image classification for the Windows 7-11 deployment expansion.

This module does not enable an OS in the destructive deployment entry point.
Recognition is distinct from tested boot/driver support.
"""
import xml.etree.ElementTree as ET


def classify(architecture, product_type, major, minor, build):
    if architecture != 9:
        return None, 'Only x64 client images are in scope.'
    if product_type != 'WinNT':
        return None, 'Server and Windows PE images are out of scope.'
    exact = {(6, 1, 7600): 'Windows 7', (6, 1, 7601): 'Windows 7',
             (6, 2, 9200): 'Windows 8', (6, 3, 9600): 'Windows 8.1'}
    family = exact.get((major, minor, build))
    if (major, minor) == (10, 0):
        if 10240 <= build <= 19045:
            family = 'Windows 10'
        elif 22000 <= build < 30000:
            family = 'Windows 11'
    return (family, '') if family else (None, 'Unrecognised Windows version/build; do not guess.')


def inspect_editions(xml_data):
    root = ET.fromstring(xml_data)
    if root.tag != 'WIM':
        raise ValueError('Expected WIM image metadata.')
    result, seen = [], set()
    for image in root.findall('IMAGE'):
        index = int(image.attrib['INDEX'])
        if index <= 0 or index in seen:
            raise ValueError('Invalid or duplicate WIM image index.')
        seen.add(index)
        try:
            version = tuple(int(image.findtext('WINDOWS/VERSION/' + key))
                            for key in ('MAJOR', 'MINOR', 'BUILD'))
            architecture = int(image.findtext('WINDOWS/ARCH'))
            size = int(image.findtext('TOTALBYTES'))
        except (TypeError, ValueError) as exc:
            raise ValueError('Incomplete image metadata.') from exc
        if min(version) < 0 or size <= 0:
            raise ValueError('Invalid version or expanded image size.')
        family, reason = classify(architecture, image.findtext('WINDOWS/PRODUCTTYPE'), *version)
        result.append(dict(index=index, name=image.findtext('DISPLAYNAME') or image.findtext('NAME')
                           or 'Image %d' % index, bytes=size, architecture=architecture,
                           version=version, family=family, refusal=reason))
    if not result:
        raise ValueError('No images in Windows install image.')
    return result
