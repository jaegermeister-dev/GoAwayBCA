"""One authenticated, local administrator session, ending with its GUI.

No password storage or persistent service. Each operation runs in an isolated
child and completes mount/lock cleanup before the next operation is accepted.
"""
import os
import socket
from gui_protocol import receive, send
from runtime_profiles import validate_payload
from install_windows7 import require

OPERATIONS = ('install','manage','rebuild','diagnostics','efi-access','reset-menu',
              'experimental','saved-files','startup-check')


def run_child(execute, connection, ui, resources, config, profile, version, request):
    # Relay the child's done only AFTER it has exited. This avoids the UI
    # submitting the next operation while the prior process still owns state.
    parent, child = socket.socketpair()
    pid = os.fork()
    if pid == 0:
        parent.close()
        from gui_protocol import GUIInteraction
        child_ui = GUIInteraction(connection)
        child_ui.user_uid = ui.user_uid
        # Only completion goes through the private parent pipe; interactive
        # requests still use the authenticated GUI socket owned by this child.
        original_send = execute.__globals__['send']
        def child_send(stream, value):
            original_send(child if value.get('type') == 'done' else stream, value)
        execute.__globals__['send'] = child_send
        try:
            status = execute(connection, child_ui, resources, config, profile, version,
                             request['operation'], request, True, False)
        except BaseException:
            status = 1
        finally:
            child.close()
            connection.close()
        os._exit(status)
    child.close()
    try:
        try:
            done = receive(parent)
        except (OSError, ValueError):
            done = dict(type='done',success=False,session_alive=False,text='The operation process ended unexpectedly. Inspect the saved logs before retrying.')
    finally:
        parent.close()
        _, status = os.waitpid(pid, 0)
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) not in (0,1):
        done = dict(type='done',success=False,text='The operation process stopped unexpectedly. Inspect the saved logs before retrying.')
        done['session_alive'] = False
    return done


def serve(connection, ui, resources, config, profile, version, execute,
          run_operation=run_child):
    cache = {}
    startup_passed = False
    geometry = None
    while True:
        try:
            request = receive(connection)
        except (OSError, ValueError):
            return 0
        if request.get('type') == 'skip_verification':
            continue  # Late click from a phase which has already finished.
        operation = request.get('operation')
        try:
            require(request.get('type') == 'configuration','Missing explicit GUI configuration')
            require(operation in OPERATIONS,'Unknown GUI operation')
            require(startup_passed or operation == 'startup-check',
                    'The launch disk check must pass before using GoAwayBCA')
            require(not startup_passed or operation != 'startup-check',
                    'The launch check has already completed in this app session')
            validate_payload(resources, profile, operation, ui.log, cache)
            operation_config = dict(config, drive_geometry_session=geometry)
            done = run_operation(execute, connection, ui, resources, operation_config, profile, version, request)
        except Exception as error:
            done = dict(type='done',success=False,text=str(error),session_alive=False)
        if operation == 'startup-check':
            startup_passed = done.get('success') is True
            geometry = done.pop('drive_geometry', None)
            if startup_passed and geometry is None:
                startup_passed = False
                done.update(success=False,text='Launch drive geometry was not returned. Close and reopen GoAwayBCA.')
        alive = startup_passed and done.get('session_alive',True)
        done['session_alive'] = alive
        try:
            send(connection, done)
        except OSError:
            return 0
        if not alive:
            return 1
