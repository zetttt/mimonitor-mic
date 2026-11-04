"""Install and control this project's monitor-side USB ADB guard."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from settings import APP_ID, choose_device, load_config, usb_devices

PACKAGE = 'local.mimonitor.guard'
COMPONENT = PACKAGE + '/.GuardService'
PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_APK = PROJECT / '.build/mi-monitor-mic-guard.apk'


def resolve_apk(apk=None):
    bundled = PROJECT / 'assets/mi-monitor-mic-guard.apk'
    path = Path(apk) if apk else (bundled if bundled.is_file() else DEFAULT_APK)
    checksum = path.with_suffix('.apk.sha256')
    if not path.is_file() or not checksum.is_file():
        raise ValueError('Guard APK/checksum missing. Use the complete installation bundle, or run make guardian.')
    values = checksum.read_text().split()
    if not values or hashlib.sha256(path.read_bytes()).hexdigest() != values[0].lower():
        raise ValueError('Guardian APK checksum mismatch; nothing installed.')
    return path


def require_android(config):
    version = adb(config, 'shell', 'getprop', 'ro.build.version.sdk').strip()
    if not version.isdigit() or int(version) < 34:
        raise ValueError('The ADB guard requires Android 14 or later. Use --bridge-only for USB capture alone.')


def adb(config, *args, timeout=12):
    try:
        result = subprocess.run([config['adb'], '-P', str(config['port']), '-s', config['serial'],
                                 *map(str, args)], capture_output=True, text=True, check=True, timeout=timeout)
    except subprocess.SubprocessError as error:
        message = getattr(error, 'stderr', None) or str(error)
        raise ValueError(message.strip().replace(config['serial'], '[USB device]')) from error
    if any(line.startswith('Error:') for line in result.stdout.splitlines()):
        raise ValueError(result.stdout.strip())
    return result.stdout


def installed(config):
    return ('package:' + PACKAGE) in adb(config, 'shell', 'pm', 'list', 'packages', PACKAGE).splitlines()


def service_state(config):
    return adb(config, 'shell', 'dumpsys', 'activity', 'service', COMPONENT)


def read_state(path, config):
    if not path.exists():
        return None
    state = json.loads(path.read_text())
    if (not isinstance(state, dict) or state.get('app_id') != APP_ID or
            state.get('serial') != config['serial'] or not isinstance(state.get('idle_exempt_before'), bool)):
        raise ValueError('Guardian ownership marker does not match; nothing changed.')
    return state


def wait_service(config, enabled):
    deadline = time.monotonic() + 5
    while ('guard_enabled=true' in service_state(config)) != enabled:
        if time.monotonic() >= deadline:
            raise ValueError('Guard did not change state. Check the monitor log for MiMonitorGuard.')
        time.sleep(0.2)


def start(config):
    adb(config, 'shell', 'pm', 'enable', PACKAGE)
    for permission in ('WRITE_SECURE_SETTINGS', 'POST_NOTIFICATIONS'):
        adb(config, 'shell', 'pm', 'grant', PACKAGE, 'android.permission.' + permission)
    adb(config, 'shell', 'cmd', 'deviceidle', 'whitelist', '+' + PACKAGE)
    adb(config, 'shell', 'am', 'start-foreground-service', '-n', COMPONENT, '-a', PACKAGE + '.ENABLE')
    wait_service(config, True)


def restore_idle_policy(config, state):
    if not state['idle_exempt_before']:
        adb(config, 'shell', 'cmd', 'deviceidle', 'whitelist', '-' + PACKAGE)


def control(action, layout, apk=None, activate=True, adb_path=None):
    config = load_config(layout.runtime, bridge=False, adb_path=adb_path)
    choose_device(usb_devices(config['adb'], config['port']), config['serial'])
    path = layout.runtime / 'guardian.json'
    state = read_state(path, config)
    present = installed(config)
    if action == 'status':
        if not present:
            print('Guard not installed.')
            return
        text = service_state(config)
        print('Guard running.' if 'guard_enabled=true' in text else 'Guard stopped.')
        for line in text.splitlines():
            if line.strip().startswith(('guard_enabled=', 'adb_enabled=', 'restores=')):
                print(line.strip())
        return
    if present and state is None:
        raise ValueError('Existing guardian is not managed by this installation; nothing changed.')
    if action == 'install':
        source = resolve_apk(apk)
        require_android(config)
        if state is None:
            whitelist = adb(config, 'shell', 'cmd', 'deviceidle', 'whitelist').splitlines()
            state = {'app_id': APP_ID, 'serial': config['serial'],
                     'idle_exempt_before': any(PACKAGE in line.split(',') for line in whitelist)}
        try:
            path.write_text(json.dumps(state, indent=2) + '\n')
            path.chmod(0o600)
            adb(config, 'install', '-r', '--pkg', PACKAGE, source, timeout=90)
            if activate:
                start(config)
        except (OSError, ValueError):
            # Only undo a fresh installation; an existing signed app must survive failed updates.
            if not present:
                cleanup_failed = False
                for args in (('shell', 'cmd', 'deviceidle', 'whitelist', '-' + PACKAGE), ('uninstall', PACKAGE)):
                    try:
                        if args[0] == 'uninstall' and not installed(config):
                            continue
                        if args[0] != 'shell' or not state['idle_exempt_before']:
                            adb(config, *args)
                    except (OSError, ValueError) as error:
                        cleanup_failed = True
                        print('Guardian cleanup warning: ' + str(error), file=sys.stderr)
                if not cleanup_failed:
                    path.unlink(missing_ok=True)
            raise
        print('USB ADB guard installed' + (' and enabled.' if activate else ' without starting.') +
              ' Network debugging settings were not changed.')
        return
    if state is None:
        print('Guard not installed.')
        return
    if action == 'start':
        if not present:
            raise ValueError('Guard APK is missing. Run ./mic.sh guard install.')
        start(config)
        print('USB ADB guard enabled.')
    elif action in ('stop', 'uninstall'):
        if present and action == 'stop':
            adb(config, 'shell', 'am', 'start-foreground-service', '-n', COMPONENT, '-a', PACKAGE + '.DISABLE')
            wait_service(config, False)
        restore_idle_policy(config, state)
        if action == 'uninstall':
            if present:
                adb(config, 'uninstall', PACKAGE)
            path.unlink(missing_ok=True)
        print('USB ADB guard ' + ('uninstalled.' if action == 'uninstall' else 'disabled.') +
              ' The current ADB switch is unchanged.')
