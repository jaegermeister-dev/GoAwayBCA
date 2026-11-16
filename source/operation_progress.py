"""Measured phase-local progress shared by text logs and the existing Cocoa bar."""
import hashlib
from pathlib import Path
import re
import time


class Message(str):
    def __new__(cls, text, phase, percent=None):
        value = str.__new__(cls, text)
        value.progress = dict(phase=phase, determinate=percent is not None)
        if percent is not None:
            if not 0 <= percent <= 100:
                raise ValueError('Progress outside phase bounds')
            value.progress['percent'] = float(percent)
        return value


def phase(note, label):
    note(Message(label + '...', label))


class Meter:
    def __init__(self, note, label, total, unit='bytes', clock=time.monotonic):
        if type(total) is not int or total < 0:
            raise ValueError('Invalid measurable total')
        self.note, self.label, self.total, self.unit = note, label, total, unit
        self.clock, self.last_time, self.last_value = clock, -1e30, -1
        self.seen_value = -1
        self.update(0, force=True)

    def update(self, done, detail='', force=False):
        if not 0 <= done <= self.total or done < self.seen_value:
            raise ValueError('Invalid/regressing phase progress')
        self.seen_value = done
        now = self.clock()
        if not force and done != self.total and now-self.last_time < .4:
            return
        percent = 100.0*done/self.total if self.total else 100.0
        counter = ('%d of %d files verified' % (done, self.total) if self.unit == 'files verified'
                   else '%d of %d %s' % (done, self.total, self.unit))
        self.note(Message('%s: %s (%.1f%%)%s' %
                          (self.label, counter, percent, ' — '+detail if detail else ''), self.label, percent))
        self.last_time, self.last_value = now, done


def hash_file(path, note, label):
    path = Path(path)
    before = path.stat()
    meter = Meter(note, label, before.st_size)
    done = 0
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024**2), b''):
            done += len(data)
            h.update(data)
            # The final 100% is published after the unchanged-size check.
            if done < before.st_size:
                meter.update(done)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or done != before.st_size:
        raise ValueError('Source changed while hashing: '+str(path))
    meter.update(done, force=True)
    return h.hexdigest()


def run_phase(argv, note, label):
    # Import lazily: the installer also uses these progress helpers.
    from install_windows7 import run_logged
    phase(note, label)
    def line(text):
        found = re.search(r'(?<![\d.])(\d+(?:\.\d+)?)%', text)
        subphase = text.split(':', 1)[0] if ':' in text else ''
        title = label + (' — '+subphase if subphase else '')
        value = float(found.group(1)) if found and 0 <= float(found.group(1)) <= 100 else None
        note(Message(label+': '+text, title, value))
    run_logged(argv, line)
    note(Message(label+' complete.', label, 100))
