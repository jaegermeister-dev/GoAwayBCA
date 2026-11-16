"""Privileged, user-started GUI backend. No standalone/unattended install mode."""
import argparse
import ctypes
import os
from pathlib import Path
import plistlib
import re
import socket
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_protocol import GUIInteraction, receive, send
from install_windows7 import Installer, require
from runtime_profiles import select, validate_payload


def connect_gui(path, token):
    require(re.fullmatch(r'[0-9a-f]{64}', token), 'Invalid GUI handshake token')
    path = Path(path)
    require(path.is_absolute() and path.name == 'control.sock', 'Unexpected GUI socket')
    require(path.parent.parent == Path('/private/tmp'), 'Unexpected GUI session parent')
    require(path.parent.name.startswith('windows-deployer-gui-'), 'Unexpected GUI session name')
    parent, node = path.parent.lstat(), path.lstat()
    require(stat.S_ISDIR(parent.st_mode) and stat.S_IMODE(parent.st_mode) == 0o700
            and parent.st_uid != 0 and not path.parent.is_symlink(), 'Unsafe GUI directory')
    require(stat.S_ISSOCK(node.st_mode) and node.st_uid == parent.st_uid
            and stat.S_IMODE(node.st_mode) == 0o600, 'Unsafe GUI socket permissions')
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(path))
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    lib.getpeereid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    lib.getpeereid.restype = ctypes.c_int
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    require(lib.getpeereid(s.fileno(), ctypes.byref(uid), ctypes.byref(gid)) == 0
            and uid.value == parent.st_uid, 'GUI peer identity mismatch')
    send(s, dict(type='hello', token=token, protocol=1))
    return s, uid.value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--token', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--operation',choices=('install','manage','rebuild','diagnostics','efi-access','reset-menu','experimental','saved-files','startup-check'),default='install')
    parser.add_argument('--session', action='store_true')
    args = parser.parse_args()
    require(sys.platform == 'darwin' and os.geteuid() == 0, 'Authorised macOS GUI worker required')
    resources = Path(__file__).resolve().parent
    config = plistlib.loads((resources / 'runtime-profiles.plist').read_bytes())
    version = plistlib.loads(Path('/System/Library/CoreServices/SystemVersion.plist').read_bytes())['ProductVersion']
    arch = subprocess.check_output(['/usr/bin/uname', '-m'], text=True).strip()
    profile = select(config['profiles'], version, arch)
    require(profile['id'] == args.profile, 'GUI/runtime profile disagreement')
    require(config.get('installation_enabled') is True or config.get('management_enabled') is True,
            'Installation is unavailable in this configuration')
    # No model-specific modifications merely to make the 11,3 pass a baseline.
    # Individual payloads still enforce their verified hardware/identity guards.
    launch_operation=getattr(args,'operation','install')
    connection, gui_uid = connect_gui(args.socket, args.token)
    ui = GUIInteraction(connection)
    ui.user_uid = gui_uid  # Kernel-authenticated peer, never GUI-supplied UID/home.
    if getattr(args, 'session', False):
        from gui_session import serve
        try:
            return serve(connection, ui, resources, config, profile, version, execute_request)
        finally:
            connection.close()
    return execute_request(connection, ui, resources, config, profile, version, launch_operation)


def execute_request(connection, ui, resources, config, profile, version, launch_operation,
                    request=None, verified=False, close_connection=True):
    installer = None
    operation = launch_operation
    success, message = False, 'Deployment did not complete.'
    try:
        # The GUI verified the executable payload before elevation. Repeat the
        # check as root, now reporting its real progress on authenticated IPC.
        if not verified:
            validate_payload(resources, profile,launch_operation,ui.log)
        request = request if request is not None else receive(connection)
        require(request.get('type') == 'configuration', 'Missing explicit GUI configuration')
        operation = request.get('operation', 'install')
        require(operation in ('install', 'manage', 'rebuild','diagnostics','efi-access','reset-menu','experimental','saved-files','startup-check'), 'Unknown GUI operation')
        # A management-scoped integrity check must never authorise installing
        # the payloads it intentionally did not read.
        require(launch_operation==operation,'GUI operation changed after verification')
        import partition_detection
        import drive_geometry
        drive_geometry.install_session(config.get('drive_geometry_session'))
        partition_detection.configure(version, request.get('reverse_partition_detection', False), ui.log)
        iso, prepared = request.get('iso'), request.get('prepared_media')
        coexistence = config.get('workflow') == 'win10-coexistence-private'
        multiboot = config.get('workflow') == 'multiboot-lifecycle'
        backtoseven = config.get('workflow') == 'backtoseven-single'
        unified = config.get('workflow') == 'goawaybca'
        if backtoseven:
            require(operation in ('install','experimental'),'BackToSeven exposes Windows 7 installation and experimental boot settings')
        if operation == 'install':
            require(config.get('installation_enabled') is True, 'Installation is not enabled in this build')
            require(isinstance(iso, str) and Path(iso).is_absolute(), 'Select an absolute ISO path')
            if coexistence or multiboot or backtoseven or unified:
                require(prepared in ('', None), 'Select an ISO; this workflow does not accept prepared-media folders')
                prepared = None
            else:
                require(isinstance(prepared, str) and Path(prepared).is_absolute(), 'Select your prepared-media folder')
        else:
            require(config.get('management_enabled') is True, 'Management is not enabled in this build')
        # Root-owned logs; no arbitrary output/work directory supplied by GUI.
        options = argparse.Namespace(iso=iso, prepared_media=prepared, boot_manager=None,
            tools_dir=str(resources / Path(profile['wim']).parent),
            work_dir='/private/var/root/WindowsDeployer-Logs', list_targets=False, dry_run=False,allow_rename=multiboot or unified,
            win7_accepted_extras=config.get('win7_accepted_extras') is True and not coexistence)
        if operation == 'startup-check':
            from startup_storage import StartupCheck
            installer=StartupCheck(options,interaction=ui)
        elif operation == 'saved-files':
            from storage_cleanup import StorageCleanup
            installer=StorageCleanup(options,interaction=ui)
        elif operation == 'experimental':
            from backtoseven_menu_actions import ExperimentalMenu
            installer=ExperimentalMenu(options, interaction=ui)
        elif operation == 'reset-menu':
            from reset_menu import ResetMenu
            installer = ResetMenu(options, interaction=ui)
        elif operation == 'diagnostics':
            from diagnostics import Diagnostics
            installer = Diagnostics(options, interaction=ui)
        elif operation == 'efi-access':
            from efi_access import EFIAccess
            installer = EFIAccess(options, interaction=ui)
        elif operation == 'rebuild':
            from rebuild_menu import RebuildMenu
            installer = RebuildMenu(options, interaction=ui)
        elif operation == 'manage':
            from format_installation import FormatInstallation
            installer = FormatInstallation(options, interaction=ui)
        elif unified:
            from install_unified import UnifiedInstaller
            installer = UnifiedInstaller(options, interaction=ui)
        elif backtoseven:
            from install_backtoseven import BackToSevenInstaller
            installer = BackToSevenInstaller(options, interaction=ui)
        elif multiboot:
            from install_multiboot import MultiBootInstaller
            installer = MultiBootInstaller(options, interaction=ui)
        elif coexistence:
            from install_coexistence import CoexistenceInstaller
            installer = CoexistenceInstaller(options, interaction=ui)
        else:
            installer = Installer(options, interaction=ui)
        installer.execute()
        success = True
        message = ('Disk compatibility check completed.' if operation=='startup-check' else
                   'Saved-file cleanup finished. See the saved deletion receipt.' if operation=='saved-files' else
                   'Experimental boot settings finished. See the saved log and backup location.' if operation=='experimental' else
                   'Boot menu deletion finished. See the log for removed and retained files. No partition was formatted.' if operation=='reset-menu' else
                   'Hardware report operation finished. See the log; no fixes applied.' if operation=='diagnostics' else
                   'EFI access operation finished. No boot files changed.' if operation=='efi-access' else
                   'Windows boot menu rebuilt. See the saved report.' if operation == 'rebuild' else
                   'Installation management finished. See the saved report.' if operation == 'manage' else
                   'Windows installation finished. Follow the restart instructions in the completion dialog.')
    except Exception as error:
        message = str(error)
        if installer is not None:
            installer.note('STOPPED: ' + message)
            installer.note('Target may contain a partial installation.' if installer.formatted
                           else 'No target partition was formatted by this run.')
    except KeyboardInterrupt:
        message = 'Operation interrupted. Deployment did not complete.'
        if installer is not None:
            installer.note(message)
            installer.note('Target may contain a partial installation; inspect it before retrying.' if installer.formatted
                           else 'No target partition was formatted by this run.')
    finally:
        try:
            if installer is not None:
                from operation_progress import phase
                phase(installer.note,'Closing temporary mounts and releasing the deployment lock')
                cleanup_errors = installer.close()
                if cleanup_errors:
                    success = False
                    message += '\nCleanup needs attention: ' + '; '.join(cleanup_errors)
            if installer is not None and installer.session and Path(installer.session).is_dir():
                from install_windows7 import write_receipt
                import partition_detection
                write_receipt(installer.session/'partition-detection.json', partition_detection.report())
                from drive_geometry import report as geometry_report
                if geometry_report() is not None:
                    write_receipt(installer.session/'drive-geometry-session.json',geometry_report())
            # The GUI must remain busy until mount cleanup has completed.
            try:
                done = dict(type='done', success=success, text=message,
                            session=str(installer.session) if installer else '')
                if operation == 'startup-check' and success:
                    done['drive_geometry'] = installer.drive_geometry
                send(connection, done)
            except OSError:
                pass
        finally:
            import partition_detection
            partition_detection.configure()
            if close_connection:connection.close()
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
