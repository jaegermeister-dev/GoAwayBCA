"""Length-prefixed XML plist IPC, usable by old Cocoa without JSON APIs."""
import plistlib
import struct
import select
import uuid
from contextlib import contextmanager

MAX_MESSAGE = 2 * 1024 * 1024


def read_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        chunk = stream.recv(size - len(result))
        if not chunk:
            raise ConnectionError('GUI connection closed')
        result.extend(chunk)
    return bytes(result)


def receive(stream):
    size = struct.unpack('!I', read_exact(stream, 4))[0]
    if not 0 < size <= MAX_MESSAGE:
        raise ValueError('Invalid GUI frame size')
    value = plistlib.loads(read_exact(stream, size))
    if not isinstance(value, dict):
        raise ValueError('GUI message must be a dictionary')
    return value


def send(stream, value):
    data = plistlib.dumps(value, fmt=plistlib.FMT_XML)
    if not 0 < len(data) <= MAX_MESSAGE:
        raise ValueError('Invalid GUI message size')
    stream.sendall(struct.pack('!I', len(data)) + data)


class GUIInteraction:
    def __init__(self, connection):
        self.connection = connection
        self.sequence = 0
        self.disconnected = False
        self.verification_token = None

    def log(self, message):
        # Losing the UI after erasure must not itself terminate WIM application.
        # Installer.note() continues to save the independent on-disk log.
        if not self.disconnected:
            try:
                event = {'type': 'log', 'text': str(message)}
                if hasattr(message, 'progress'):
                    event['progress'] = message.progress
                send(self.connection, event)
            except (ConnectionError, OSError):
                self.disconnected = True

    def request(self, kind, text, **fields):
        self.sequence += 1
        if self.disconnected:
            raise ConnectionError('GUI disconnected before confirmation')
        send(self.connection, dict(type=kind, text=text, sequence=self.sequence, **fields))
        while True:
            response = receive(self.connection)
            # A click already in transit when a verification ended cannot
            # become approval for a dialog or skip the following phase.
            if response.get('type') == 'skip_verification':
                continue
            break
        if response.get('sequence') != self.sequence or response.get('type') != 'reply':
            raise ValueError('Unexpected or stale GUI response')
        if response.get('cancelled') is True:
            raise ValueError('Cancelled by user before deployment')
        return response.get('value')

    def choose(self, text, options):
        selected = self.request('choose', text, options=options)
        if type(selected) is not int or not 0 <= selected < len(options):
            raise ValueError('Invalid GUI selection')
        return selected

    def confirm(self, text, phrase, **fields):
        result = self.request('confirm', text, phrase=phrase, **fields)
        return isinstance(result, str) and result.strip().lower() == phrase.lower()

    def approve(self, kind, text, **fields):
        if kind not in ('format_ready', 'review', 'rename_review', 'rebuild_review', 'reset_review', 'compatibility_review', 'import_review'):
            raise ValueError('Invalid approval stage')
        # Integers, strings, a previous selection, or an omitted value cannot
        # substitute for explicit consent at this sequence number.
        return self.request(kind, text, **fields) is True

    def efi_choice(self, text, default_enabled=True):
        result = self.request('efi_choice', text, default_enabled=default_enabled)
        if type(result) is not bool:
            raise ValueError('Invalid EFI checkbox response')
        return result

    def keyboard_choice(self, text, default_enabled=True):
        result = self.request('keyboard_choice', text, default_enabled=default_enabled)
        if type(result) is not bool:
            raise ValueError('Invalid keyboard preparation choice')
        return result

    @contextmanager
    def verification(self, label):
        if self.verification_token is not None:
            raise ValueError('Nested optional verification')
        token = uuid.uuid4().hex
        self.verification_token = token
        def state(enabled):
            if not self.disconnected:
                try:
                    send(self.connection, dict(type='verification_state', phase_token=token,
                        enabled=enabled, text=label))
                except (ConnectionError, OSError):
                    self.disconnected = True
        def skipped():
            if self.disconnected:
                return False  # A missing GUI never silently skips a check.
            accepted = False
            try:
                while select.select([self.connection], [], [], 0)[0]:
                    message = receive(self.connection)
                    if message.get('type') != 'skip_verification':
                        raise ValueError('Unexpected message during verification')
                    if message.get('phase_token') == token:
                        accepted = True
            except (ConnectionError, OSError):
                self.disconnected = True
            return accepted
        state(True)
        try:
            yield skipped
        finally:
            self.verification_token = None
            state(False)
