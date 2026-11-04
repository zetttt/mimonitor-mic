import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import guardian
import manage
from settings import APP_ID, ADB_PORT, Layout, MODEL


class GuardianTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.layout = Layout(root / 'home')
        self.layout.runtime.mkdir(parents=True)
        tool = root / 'tool'
        tool.touch()
        config = {'app_id': APP_ID, 'version': 1, 'serial': 'TEST_MONITOR', 'gain': 1,
                  'port': ADB_PORT, **{key: str(tool) for key in ('adb', 'scrcpy', 'server', 'python')}}
        (self.layout.runtime / 'config.json').write_text(json.dumps(config))
        self.apk = root / 'guard.apk'
        self.apk.touch()
        self.apk.with_suffix('.apk.sha256').write_text(hashlib.sha256(b'').hexdigest())
        self.calls = []
        self.present = False
        self.running = False
        self.preexisting_idle_exemption = False
        self.fail_grant = False
        devices = [{'serial': 'TEST_MONITOR', 'state': 'device', 'model': MODEL}]
        for item in (patch.object(guardian, 'usb_devices', return_value=devices),
                     patch.object(guardian, 'adb', side_effect=self.adb),
                     patch('sys.stdout', new=io.StringIO())):
            item.start()
            self.addCleanup(item.stop)

    def adb(self, config, *args, **kwargs):
        self.calls.append(args)
        if args[:4] == ('shell', 'pm', 'list', 'packages'):
            return 'package:' + guardian.PACKAGE + '\n' if self.present else ''
        if args[:3] == ('shell', 'getprop', 'ro.build.version.sdk'):
            return '34\n'
        if args == ('shell', 'cmd', 'deviceidle', 'whitelist'):
            return 'user,' + guardian.PACKAGE + ',10123\n' if self.preexisting_idle_exemption else ''
        if args[0] == 'install':
            self.present = True
        elif args[0] == 'uninstall':
            self.present = self.running = False
        elif args[:3] == ('shell', 'pm', 'grant') and self.fail_grant:
            raise ValueError('Permission grant rejected')
        elif args[:3] == ('shell', 'am', 'start-foreground-service'):
            self.running = args[-1].endswith('.ENABLE')
        elif args[:4] == ('shell', 'dumpsys', 'activity', 'service'):
            return 'guard_enabled=true\nadb_enabled=1\nrestores=0\n' if self.running else ''
        return ''

    def test_install_stop_start_uninstall_preserves_microphone_configuration(self):
        config_path = self.layout.runtime / 'config.json'
        before = config_path.read_bytes()
        guardian.control('install', self.layout, self.apk)
        self.assertTrue(self.running)
        self.assertIn(('install', '-r', '--pkg', guardian.PACKAGE, self.apk), self.calls)
        guardian.control('stop', self.layout)
        self.assertFalse(self.running)
        self.assertIn(('shell', 'cmd', 'deviceidle', 'whitelist', '-' + guardian.PACKAGE), self.calls)
        guardian.control('start', self.layout)
        self.assertTrue(self.running)
        guardian.control('uninstall', self.layout)
        self.assertFalse(self.present)
        self.assertFalse((self.layout.runtime / 'guardian.json').exists())
        self.assertEqual(config_path.read_bytes(), before)

    def test_preexisting_idle_exemption_is_not_removed(self):
        self.preexisting_idle_exemption = True
        guardian.control('install', self.layout, self.apk)
        guardian.control('stop', self.layout)
        guardian.control('uninstall', self.layout)
        self.assertNotIn(('shell', 'cmd', 'deviceidle', 'whitelist', '-' + guardian.PACKAGE), self.calls)

    def test_rejected_permission_rolls_back_fresh_installation(self):
        self.fail_grant = True
        with self.assertRaisesRegex(ValueError, 'Permission grant rejected'):
            guardian.control('install', self.layout, self.apk)
        self.assertFalse(self.present)
        self.assertFalse((self.layout.runtime / 'guardian.json').exists())

    def test_foreign_guardian_is_not_changed(self):
        self.present = True
        with self.assertRaisesRegex(ValueError, 'not managed'):
            guardian.control('uninstall', self.layout)
        self.assertTrue(self.present)
        self.assertEqual(self.calls, [('shell', 'pm', 'list', 'packages', guardian.PACKAGE)])

    def test_missing_usb_is_rejected_before_installation(self):
        guardian.usb_devices.return_value = []
        with self.assertRaisesRegex(ValueError, 'No supported USB monitor'):
            guardian.control('install', self.layout, self.apk)
        self.assertEqual(self.calls, [])

    def test_mac_uninstall_removes_guard_before_connection_configuration(self):
        guardian.control('install', self.layout, self.apk)
        with patch.object(manage, 'stop') as stop, patch.object(manage, 'cleanup'):
            manage.uninstall(self.layout)
            stop.assert_called_once()
        self.assertFalse(self.present)
        self.assertFalse(self.layout.runtime.exists())

    def test_mac_uninstall_uses_replacement_adb_when_saved_tool_is_missing(self):
        guardian.control('install', self.layout, self.apk)
        config_path = self.layout.runtime / 'config.json'
        config = json.loads(config_path.read_text())
        config['adb'] = str(self.layout.home / 'removed-adb')
        config_path.write_text(json.dumps(config))
        guardian.adb.reset_mock()
        with patch.object(manage, 'stop') as stop, patch.object(manage, 'cleanup'):
            manage.uninstall(self.layout, adb_path='/usr/bin/true')
            stop.assert_called_once()
        guardian.usb_devices.assert_called_with('/usr/bin/true', ADB_PORT)
        for call in guardian.adb.call_args_list:
            self.assertEqual(call.args[0], dict(config, adb='/usr/bin/true'))
        self.assertFalse(self.present or self.layout.runtime.exists())

    def test_replacement_adb_does_not_bypass_guard_ownership(self):
        guardian.control('install', self.layout, self.apk)
        state_path = self.layout.runtime / 'guardian.json'
        state = json.loads(state_path.read_text())
        state['serial'] = 'OTHER_MONITOR'
        state_path.write_text(json.dumps(state))
        before = state_path.read_bytes()
        guardian.adb.reset_mock()
        with patch.object(manage, 'stop') as stop, patch.object(manage, 'cleanup'):
            with self.assertRaisesRegex(ValueError, 'ownership'):
                manage.uninstall(self.layout, adb_path='/usr/bin/true')
            stop.assert_not_called()
        guardian.adb.assert_not_called()
        self.assertTrue(self.present)
        self.assertEqual(state_path.read_bytes(), before)

    def test_manifest_limits_permissions_and_protects_service_control(self):
        root = ET.parse(Path(__file__).resolve().parents[1] / 'guardian/AndroidManifest.xml').getroot()
        android = '{http://schemas.android.com/apk/res/android}'
        permissions = {node.attrib[android + 'name'] for node in root.findall('uses-permission')}
        self.assertEqual(permissions, {'android.permission.' + name for name in (
            'WRITE_SECURE_SETTINGS', 'FOREGROUND_SERVICE', 'FOREGROUND_SERVICE_SPECIAL_USE',
            'POST_NOTIFICATIONS', 'RECEIVE_BOOT_COMPLETED')})
        service = root.find('application/service')
        self.assertEqual(service.attrib[android + 'permission'], 'android.permission.WRITE_SECURE_SETTINGS')


if __name__ == '__main__':
    unittest.main()
