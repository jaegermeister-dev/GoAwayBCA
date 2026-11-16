"""Only explicitly read-only, optional content checks may use these controls.

Authentication, partition identity, confirmations, EFI snapshots and writing
helpers deliberately do not call this module. Losing the UI means keep checking.
"""
from contextlib import contextmanager
import queue
import re
import subprocess
import threading
from operation_progress import Message, phase


@contextmanager
def control(interaction, label):
    if interaction is None or not hasattr(interaction, 'verification'):
        yield lambda: False
    else:
        with interaction.verification(label) as skipped:
            yield skipped


def stop_owned_reader(process):
    """The caller owns this read-only subprocess, never an arbitrary PID."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    else:
        process.wait()


def verify_wim(tool, image, note, interaction=None):
    # The command is constructed here, not accepted from an arbitrary caller:
    # it must remain a read-only WIM verify, never apply/update/format.
    label = 'Verifying Windows image'
    last = dict(phase=label, percent=None)
    output = queue.Queue()
    lines = []
    with control(interaction, label) as skipped:
        phase(note, label)
        with subprocess.Popen([str(tool), 'verify', str(image)], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT) as process:
            def reader():
                try:
                    # WIM progress can use CR rather than newline.
                    pending = b''
                    for block in iter(lambda: process.stdout.read1(4096), b''):
                        pending += block
                        parts = re.split(b'[\r\n]', pending)
                        pending = parts.pop()
                        for line in parts:
                            if line: output.put(line.decode('utf-8', 'replace'))
                        if len(pending) > 65536:
                            output.put(pending[:65536].decode('utf-8', 'replace'))
                            pending = pending[65536:]
                    if pending: output.put(pending.decode('utf-8', 'replace'))
                finally:
                    output.put(None)
            thread = threading.Thread(target=reader)
            thread.start()
            ended = False
            requested = False
            try:
                while not ended:
                    try:
                        text = output.get(timeout=.1)
                    except queue.Empty:
                        text = ''
                    if text is None:
                        ended = True
                    elif text:
                        lines.append(text)
                        lines[:] = lines[-20:]
                        found = re.search(r'(?<![\d.])(\d+(?:\.\d+)?)%', text)
                        percent = float(found.group(1)) if found else None
                        if percent is not None and not 0 <= percent <= 100: percent = None
                        last = dict(phase=label + (' — '+text.split(':', 1)[0] if ':' in text else ''),
                                    percent=percent)
                        note(Message(label+': '+text, last['phase'], percent))
                        # Never turn an already reported verification failure
                        # into success because a skip arrives moments later.
                        if re.search(r'(?i)(\[error\]|error\s*\d+|corrupt|verification failed)', text):
                            raise ValueError('Windows image verification reported an error: '+text)
                    code = process.poll()
                    if code is not None and code != 0:
                        raise ValueError('Windows image verification failed (%d): %s' % (code, '\n'.join(lines)))
                    if not ended and skipped():
                        requested = True
                        stop_owned_reader(process)
                        break
                if not requested:
                    code = process.wait()
                    if code != 0:
                        raise ValueError('Windows image verification failed (%d): %s' % (code, '\n'.join(lines)))
            finally:
                stop_owned_reader(process)
                thread.join()
        if requested:
            note(Message('Windows image verification skipped by user. Last reported phase: '+last['phase']+
                (('; %.1f%% reported.' % last['percent']) if last['percent'] is not None else '; no percentage reported.')+
                ' This is not a successful full verification.', label))
        else:
            note(Message('Windows image verification complete.', label, 100))
    return dict(status='skipped-by-user' if requested else 'verified', last_reported=last)
