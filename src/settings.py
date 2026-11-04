"""Local paths, configuration and USB device discovery shared by the tools."""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess

APP_NAME = 'Mi Monitor Mic'
APP_ID = 'local.mimonitor-mic'
ADB_PORT = 15038
MODEL = 'MiTV_MFFU1'
TARGET = 'BlackHole 2ch'


@dataclass(frozen=True)
class Layout:
    home: Path

    @property
    def runtime(self):
        return self.home / 'Library/Application Support' / APP_NAME

    @property
    def app(self):
        return self.home / 'Applications' / (APP_NAME + '.app')

    @property
    def plist(self):
        return self.home / 'Library/LaunchAgents' / (APP_ID + '.plist')

    @property
    def log(self):
        return self.home / 'Library/Logs' / APP_NAME / 'service.log'


def load_config(runtime, bridge=True, adb_path=None):
    config = json.loads((runtime / 'config.json').read_text())
    if not isinstance(config, dict) or config.get('app_id') != APP_ID or config.get('version') != 1:
        raise ValueError('Unrecognized configuration; reinstall this project.')
    serial = config.get('serial', '')
    if not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', serial):
        raise ValueError('Invalid USB serial; network addresses are not accepted.')
    if bridge:
        gain = config.get('gain')
        if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not math.isfinite(gain) or not 0 < gain <= 100:
            raise ValueError('Gain must be finite and greater than 0, up to 100.')
    if config.get('port') != ADB_PORT:
        raise ValueError('Unexpected private ADB port; reinstall this project.')
    if adb_path is not None:
        config['adb'] = os.path.abspath(adb_path)
    for key in (('adb', 'scrcpy', 'server', 'python') if bridge else ('adb',)):
        value = config.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).is_file():
            raise ValueError('Missing dependency: ' + key + '. Reinstall after updating dependencies.')
    return config


def usb_devices(adb, port=ADB_PORT):
    result = subprocess.run([str(adb), '-P', str(port), 'devices', '-l'],
                            capture_output=True, text=True, timeout=12, check=True)
    devices = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 2 or not any(field.startswith('usb:') for field in fields[2:]):
            continue
        metadata = dict(field.split(':', 1) for field in fields[2:] if ':' in field)
        devices.append({'serial': fields[0], 'state': fields[1], 'model': metadata.get('model', '')})
    return devices


def choose_device(devices, serial=None):
    matches = [device for device in devices if device['model'] == MODEL and
               (serial is None or device['serial'] == serial)]
    if not matches:
        raise ValueError('No supported USB monitor found. Check USB debugging, its data cable and authorization.')
    if len(matches) != 1:
        raise ValueError('Multiple monitors found. Run ./mic.sh devices, then install with --serial SERIAL.')
    if matches[0]['state'] != 'device':
        raise ValueError('Allow USB debugging on the monitor, then try again.')
    return matches[0]
