#!/usr/bin/env python3
"""Install and control the USB bridge and its monitor-side ADB guard."""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

import guardian
from settings import ADB_PORT, APP_ID, APP_NAME, MODEL, TARGET, Layout, choose_device, load_config, usb_devices

SOURCE = Path(__file__).resolve().parent


def checked(args, timeout=60):
    return subprocess.run([str(arg) for arg in args], check=True, capture_output=True,
                          text=True, timeout=timeout)


def cleanup(args, timeout=12):
    try:
        checked(args, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        print('Cleanup warning: ' + str(error), file=sys.stderr)


def require_audio_output(executable):
    outputs = checked([executable, '--list']).stdout
    if not any(line.split('\t', 1)[0] == TARGET for line in outputs.splitlines()):
        raise ValueError(TARGET + ' is not available. Install it and follow its restart instructions.')


def executable(name, override=None):
    path = override or shutil.which(name)
    if not path or not Path(path).is_file() or not os.access(path, os.X_OK):
        raise ValueError('Missing executable: ' + name + '. See README prerequisites.')
    return os.path.abspath(path)


def find_server(scrcpy, override=None):
    if override:
        candidates = [Path(override)]
    else:
        binary = Path(scrcpy)
        candidates = [binary.parent / 'scrcpy-server']
        if candidates[0].is_file():
            return str(candidates[0].absolute())
        brew = shutil.which('brew')
        if brew:
            result = subprocess.run([brew, '--prefix', 'scrcpy'], capture_output=True,
                                    text=True, timeout=12, check=False)
            if result.returncode == 0:
                candidates.append(Path(result.stdout.strip()) / 'share/scrcpy/scrcpy-server')
        candidates.append(binary.resolve().parent.parent / 'share/scrcpy/scrcpy-server')
    for path in candidates:
        if path.is_file():
            return str(path.absolute())
    raise ValueError('scrcpy-server not found; provide --server /absolute/path/to/scrcpy-server.')


def service_target():
    return 'gui/' + str(os.getuid()) + '/' + APP_ID


def loaded():
    return subprocess.run(['/bin/launchctl', 'print', service_target()],
                          capture_output=True, text=True, timeout=8).returncode == 0


def start(layout):
    if not layout.app.is_dir() or not layout.plist.is_file():
        raise ValueError('USB bridge is not installed. Run ./install.sh or ./install.sh --bridge-only.')
    load_config(layout.runtime)
    checked(['/bin/launchctl', 'enable', service_target()])
    if not loaded():
        checked(['/bin/launchctl', 'bootstrap', 'gui/' + str(os.getuid()), layout.plist])
    checked(['/bin/launchctl', 'kickstart', service_target()])


def stop():
    checked(['/bin/launchctl', 'disable', service_target()])
    if loaded():
        checked(['/bin/launchctl', 'bootout', service_target()], timeout=45)


def bridge_installed(layout, adb_path=None):
    files = [layout.runtime / name for name in ('bridge.py', 'settings.py', 'audio-output')]
    if not any(path.exists() for path in (layout.app, layout.plist, *files)):
        return False
    if not layout.app.is_dir() or not layout.plist.is_file() or not all(path.is_file() for path in files):
        raise ValueError('Bridge installation is incomplete. Uninstall it before reinstalling.')
    load_config(layout.runtime, adb_path=adb_path)
    return True


def write_config(runtime, data):
    with tempfile.NamedTemporaryFile(dir=runtime, delete=False) as stream:
        temp = Path(stream.name)
        stream.write(data)
    try:
        temp.replace(runtime / 'config.json')
    finally:
        temp.unlink(missing_ok=True)


def build_bridge(staging, args, config, layout):
    config['gain'] = args.gain if args.gain is not None else config.get('gain', 1.0)
    scrcpy = executable('scrcpy', args.scrcpy or config.get('scrcpy'))
    config.update(scrcpy=scrcpy, server=find_server(scrcpy, args.server or config.get('server')),
                  python=os.path.abspath(sys.executable))
    swiftc = checked(['/usr/bin/xcrun', '--find', 'swiftc']).stdout.strip()
    if not Path(swiftc).is_file():
        raise ValueError('Install Xcode Command Line Tools: xcode-select --install')
    runtime = staging / 'runtime'
    runtime.mkdir(mode=0o700)
    for name in ('bridge.py', 'settings.py'):
        shutil.copy2(SOURCE / name, runtime / name)
    target = platform.machine() + '-apple-macos13.0'
    checked([swiftc, '-swift-version', '5', '-target', target, '-O',
             SOURCE / 'audio-output.swift', '-o', runtime / 'audio-output'], timeout=120)
    if not args.no_start:
        require_audio_output(runtime / 'audio-output')
    app = staging / (APP_NAME + '.app')
    contents = app / 'Contents'
    macos = contents / 'MacOS'
    macos.mkdir(parents=True)
    checked([swiftc, '-swift-version', '5', '-target', target, '-O', '-parse-as-library',
             SOURCE / 'service.swift', '-o', macos / 'mimonitor-mic'], timeout=120)
    info = {'CFBundleIdentifier': APP_ID, 'CFBundleName': APP_NAME,
            'CFBundleDisplayName': APP_NAME, 'CFBundleExecutable': 'mimonitor-mic',
            'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': '0.1.0',
            'CFBundleVersion': '1', 'LSMinimumSystemVersion': '13.0', 'LSUIElement': True,
            'RMMRuntimeDirectory': str(layout.runtime)}
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    checked(['/usr/bin/codesign', '--force', '--sign', '-', app])
    plist = {'Label': APP_ID, 'ProgramArguments': [str(layout.app / 'Contents/MacOS/mimonitor-mic')],
             'WorkingDirectory': str(layout.runtime), 'RunAtLoad': True, 'KeepAlive': True,
             'Disabled': args.no_start, 'LimitLoadToSessionType': 'Aqua',
             'ThrottleInterval': 20, 'ExitTimeOut': 40,
             'EnvironmentVariables': {'PYTHONUNBUFFERED': '1'},
             'StandardOutPath': str(layout.log), 'StandardErrorPath': str(layout.log),
             'AssociatedBundleIdentifiers': [APP_ID]}
    return runtime, app, plist


def stop_and_wait(layout):
    stop()
    path = layout.runtime / 'capture.lock'
    if not path.exists():
        return
    deadline = time.monotonic() + 45
    with path.open('r') as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError('Capture has not stopped; installation files retained for recovery.')
                time.sleep(0.2)


def select_device(adb, serial):
    deadline = None
    while True:
        devices = usb_devices(adb)
        ready = any(device['state'] == 'device' and device['model'] == MODEL and
                    (serial is None or device['serial'] == serial) for device in devices)
        pending = any(device['state'] == 'unauthorized' and
                      (serial is None or device['serial'] == serial) for device in devices)
        if ready or not pending:
            return choose_device(devices, serial)
        if deadline is None:
            print('Allow USB debugging on the monitor. Waiting up to 60 seconds for authorization.', flush=True)
            deadline = time.monotonic() + 60
        if time.monotonic() >= deadline:
            raise ValueError('Allow USB debugging on the monitor, then retry; the authorization wait timed out.')
        time.sleep(1)


def install(args, layout):
    if platform.system() != 'Darwin' or int(platform.mac_ver()[0].split('.')[0]) < 13:
        raise ValueError('macOS 13 or later is required.')
    want_bridge, want_guard = not args.guard_only, not args.bridge_only
    if args.gain is not None and (not math.isfinite(args.gain) or not 0 < args.gain <= 100):
        raise ValueError('--gain must be finite and greater than 0, up to 100.')
    if args.guard_only and any(value is not None for value in (args.gain, args.scrcpy, args.server)):
        raise ValueError('--gain, --scrcpy and --server are bridge options.')
    if args.bridge_only and args.apk:
        raise ValueError('--apk requires an installation that includes the guard.')
    validate_owned(layout)
    config_path = layout.runtime / 'config.json'
    old_config = config_path.read_bytes() if config_path.exists() else None
    config = load_config(layout.runtime, bridge=False, adb_path=args.adb) if old_config else {
        'app_id': APP_ID, 'version': 1, 'port': ADB_PORT}
    had_bridge = bridge_installed(layout, adb_path=config.get('adb')) if want_bridge else False
    was_loaded = loaded()
    for key in ('serial', 'gain', 'scrcpy', 'server'):
        value = getattr(args, key)
        if value is not None and key in ('scrcpy', 'server'):
            value = os.path.abspath(value)
        if value is not None and key in config and value != config[key]:
            raise ValueError('Existing ' + key + ' differs. Uninstall before changing saved settings.')
    config['adb'] = executable('adb', args.adb or config.get('adb'))
    try:
        device = select_device(config['adb'], args.serial or config.get('serial'))
        config['serial'] = device['serial']
        guard_path = layout.runtime / 'guardian.json'
        old_guard = guard_path.read_bytes() if guard_path.exists() else None
        saved_guard = guardian.read_state(guard_path, config) if want_guard else None
        had_guard = guardian.installed(config) if want_guard else False
        if want_guard and had_guard and saved_guard is None:
            raise ValueError('Existing guardian is not managed by this installation; nothing changed.')
        guard_running = had_guard and 'guard_enabled=true' in guardian.service_state(config)
        apk = None
        install_guard = want_guard and (not had_guard or args.apk is not None)
        if install_guard:
            apk = guardian.resolve_apk(args.apk)
            guardian.require_android(config)
        new_bridge = want_bridge and not had_bridge
        layout.runtime.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.mimonitor-mic-', dir=layout.runtime.parent) as temp:
            built = build_bridge(Path(temp), args, config, layout) if new_bridge else None
            created = []
            new_runtime = not layout.runtime.exists()
            bridge_start_attempted = guard_start_attempted = False
            try:
                layout.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
                if old_config is None or config != json.loads(old_config):
                    write_config(layout.runtime, (json.dumps(config, indent=2) + '\n').encode())
                if built:
                    runtime, app, plist = built
                    layout.app.parent.mkdir(parents=True, exist_ok=True)
                    layout.plist.parent.mkdir(parents=True, exist_ok=True)
                    layout.log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    for source in runtime.iterdir():
                        target = layout.runtime / source.name
                        source.rename(target)
                        created.append(target)
                    app.rename(layout.app)
                    created.append(layout.app)
                    layout.plist.write_bytes(plistlib.dumps(plist))
                    created.append(layout.plist)
                if install_guard:
                    guard_start_attempted = had_guard and not guard_running and not args.no_start
                    print('Installing the monitor ADB guard; enabling it grants settings/notification permission and a standby exemption.')
                    guardian.control('install', layout, apk, activate=not args.no_start)
                elif want_guard and not args.no_start and not guard_running:
                    guard_start_attempted = True
                    guardian.control('start', layout)
                if want_bridge and not args.no_start and not was_loaded:
                    bridge_start_attempted = True
                    start(layout)
            except BaseException:
                can_remove_bridge = True
                if bridge_start_attempted:
                    try:
                        stop_and_wait(layout)
                    except (OSError, ValueError, subprocess.SubprocessError) as error:
                        can_remove_bridge = False
                        print('Rollback warning: ' + str(error), file=sys.stderr)
                guard_cleanup_ok = True
                if want_guard and ((not had_guard and guard_path.exists()) or guard_start_attempted):
                    try:
                        guardian.control('stop' if had_guard else 'uninstall', layout)
                        if old_guard is not None and not had_guard:
                            guard_path.write_bytes(old_guard)
                    except (OSError, ValueError, subprocess.SubprocessError) as error:
                        guard_cleanup_ok = False
                        print('Guard rollback warning: ' + str(error), file=sys.stderr)
                if can_remove_bridge:
                    try:
                        for path in reversed(created):
                            if path.is_dir():
                                shutil.rmtree(path)
                            else:
                                path.unlink(missing_ok=True)
                        if old_config is not None:
                            write_config(layout.runtime, old_config)
                        elif new_runtime and guard_cleanup_ok:
                            shutil.rmtree(layout.runtime)
                    except OSError as error:
                        print('File rollback warning: ' + str(error), file=sys.stderr)
                # A failed device cleanup keeps its connection/ownership record for a later retry.
                raise
        selected = 'USB bridge + ADB guard' if want_bridge and want_guard else 'USB bridge' if want_bridge else 'ADB guard'
        print('Ready: ' + selected + ('. No new services started.' if args.no_start else '.'))
        if want_bridge:
            print('Choose ' + TARGET + ' in the receiving app. Log: ' + str(layout.log))
    finally:
        if not loaded():
            cleanup([config['adb'], '-P', str(ADB_PORT), 'kill-server'])


def validate_owned(layout):
    if layout.runtime.is_symlink() or layout.app.is_symlink() or layout.plist.is_symlink():
        raise ValueError('Refusing to remove a symlinked installation.')
    config_path = layout.runtime / 'config.json'
    if layout.runtime.exists():
        config = json.loads(config_path.read_text())
        if not isinstance(config, dict) or config.get('app_id') != APP_ID:
            raise ValueError('Runtime ownership marker does not match; nothing removed.')
    if layout.app.exists():
        info = plistlib.loads((layout.app / 'Contents/Info.plist').read_bytes())
        if not isinstance(info, dict) or info.get('CFBundleIdentifier') != APP_ID:
            raise ValueError('App ownership marker does not match; nothing removed.')
    if layout.plist.exists():
        plist = plistlib.loads(layout.plist.read_bytes())
        if not isinstance(plist, dict) or plist.get('Label') != APP_ID:
            raise ValueError('LaunchAgent ownership marker does not match; nothing removed.')


def uninstall(layout, adb_path=None):
    validate_owned(layout)
    if not any(path.exists() for path in (layout.runtime, layout.app, layout.plist)):
        print('Not installed.')
        return
    if adb_path is not None:
        adb_path = executable('adb', adb_path)
    config = None
    try:
        config = load_config(layout.runtime, bridge=False, adb_path=adb_path)
    except (OSError, ValueError):
        pass
    if (layout.runtime / 'guardian.json').exists():
        guardian.control('uninstall', layout, adb_path=adb_path)
    stop()
    if config:
        cleanup([config['adb'], '-P', str(ADB_PORT), 'kill-server'])
    for path in (layout.plist, layout.app, layout.runtime):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    print('Uninstalled. Logs retained at ' + str(layout.log.parent))
    print('BlackHole, scrcpy, ADB and monitor settings were not changed.')


def doctor(layout):
    if not layout.app.is_dir() or not layout.plist.is_file():
        raise ValueError('USB bridge is not installed. Use ./mic.sh guard status to check a standalone guard.')
    config = load_config(layout.runtime)
    device = choose_device(usb_devices(config['adb']), config['serial'])
    require_audio_output(layout.runtime / 'audio-output')
    print('USB monitor authorized; BlackHole 2ch available; configuration valid.')
    print('Device: ' + device['model'] + ' / ****' + device['serial'][-4:])
    print('This checks prerequisites, not speech quality or application compatibility.')


def main():
    if sys.version_info < (3, 9):
        print('Python 3.9 or later is required.', file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description=APP_NAME + ': USB-only microphone bridge for macOS')
    commands = parser.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('install')
    setup.add_argument('--serial', help='Choose a USB monitor when more than one is connected')
    setup.add_argument('--gain', type=float, help='PCM gain; new bridges default to 1.0, existing settings are preserved')
    setup.add_argument('--adb', help='Path to an existing adb executable; also repairs a saved ADB path')
    setup.add_argument('--scrcpy', help='Path to an existing scrcpy executable')
    setup.add_argument('--server', help='Path to its matching scrcpy-server')
    setup.add_argument('--no-start', action='store_true', help='Install without starting new services')
    modes = setup.add_mutually_exclusive_group()
    modes.add_argument('--bridge-only', action='store_true', help='Install only the Mac USB bridge')
    modes.add_argument('--guard-only', action='store_true', help='Install only the monitor ADB guard')
    setup.add_argument('--apk', help='Guard APK with its .apk.sha256 file; defaults to bundled or locally built APK')
    for command in ('start', 'stop', 'status', 'doctor', 'devices'):
        commands.add_parser(command)
    removal = commands.add_parser('uninstall')
    removal.add_argument('--adb', help='Use this adb executable if the saved path is unavailable')
    guard = commands.add_parser('guard', help='Control the optional monitor-side USB ADB guard')
    guard.add_argument('action', choices=('install', 'start', 'stop', 'status', 'uninstall'))
    guard.add_argument('--apk', help='Locally built guard APK (install only)')
    args = parser.parse_args()
    layout = Layout(Path.home())
    try:
        if args.command == 'install':
            install(args, layout)
        elif args.command == 'start':
            start(layout)
            print('Background capture enabled, including future logins.')
        elif args.command == 'stop':
            stop()
            print('Background capture and login autostart disabled.')
        elif args.command == 'status':
            print('USB bridge: ' + ('loaded' if loaded() else 'stopped / not installed'))
            if (layout.runtime / 'guardian.json').exists():
                try:
                    guardian.control('status', layout)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    print('ADB guard: unable to query: ' + str(error))
            else:
                print('ADB guard: not configured')
            print('Log: ' + str(layout.log))
        elif args.command == 'doctor':
            doctor(layout)
        elif args.command == 'uninstall':
            uninstall(layout, adb_path=args.adb)
        elif args.command == 'guard':
            if args.apk and args.action != 'install':
                raise ValueError('--apk is only accepted with guard install.')
            guardian.control(args.action, layout, args.apk)
        elif args.command == 'devices':
            adb = executable('adb')
            try:
                for device in usb_devices(adb):
                    print(device['serial'], device['state'], device['model'])
            finally:
                if not loaded():
                    cleanup([adb, '-P', str(ADB_PORT), 'kill-server'])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr.strip(), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
