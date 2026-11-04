import argparse
import hashlib
import io
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import guardian
import manage
from settings import APP_ID, Layout, MODEL, load_config


class InstallFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.layout = Layout(self.root / 'home')
        self.args = argparse.Namespace(adb='/usr/bin/true', scrcpy=None, server=None,
                                       serial=None, gain=None, no_start=False,
                                       bridge_only=False, guard_only=False, apk=None)
        self.apk = self.root / 'guard.apk'
        self.apk.write_bytes(b'test artifact')
        self.apk.with_suffix('.apk.sha256').write_text(hashlib.sha256(self.apk.read_bytes()).hexdigest())
        self.calls = []
        self.loaded = self.guard_present = self.guard_running = False
        self.fail_guard = self.fail_bridge = self.fail_guard_cleanup = False
        patches = [patch.object(manage.platform, 'system', return_value='Darwin'),
                   patch.object(manage.platform, 'mac_ver', return_value=('13.0', (), '')),
                   patch.object(manage, 'loaded', side_effect=lambda: self.loaded),
                   patch.object(manage, 'cleanup'),
                   patch.object(manage, 'usb_devices', return_value=[{
                       'serial': 'TEST_MONITOR', 'state': 'device', 'model': MODEL}]),
                   patch.object(manage, 'build_bridge', side_effect=self.build),
                   patch.object(manage, 'start', side_effect=self.start),
                   patch.object(manage, 'stop_and_wait', side_effect=self.stop),
                   patch.object(guardian, 'installed', side_effect=lambda config: self.guard_present),
                   patch.object(guardian, 'service_state', side_effect=lambda config: 'guard_enabled=true' if self.guard_running else ''),
                   patch.object(guardian, 'require_android'),
                   patch.object(guardian, 'control', side_effect=self.guard),
                   patch.object(guardian, 'PROJECT', self.root / 'source'),
                   patch.object(guardian, 'DEFAULT_APK', self.apk),
                   patch('sys.stdout', new=io.StringIO()), patch('sys.stderr', new=io.StringIO())]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def build(self, staging, args, config, layout):
        self.calls.append('build')
        config.update(gain=args.gain if args.gain is not None else 1,
                      scrcpy='/usr/bin/true', server='/usr/bin/true', python='/usr/bin/true')
        runtime = staging / 'runtime'
        runtime.mkdir()
        for name in ('bridge.py', 'settings.py', 'audio-output'):
            (runtime / name).write_text('test artifact')
        app = staging / 'app'
        (app / 'Contents').mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': APP_ID}))
        return runtime, app, {'Label': APP_ID, 'Disabled': args.no_start}

    def start(self, layout):
        self.calls.append('start bridge')
        self.loaded = True
        if self.fail_bridge:
            raise ValueError('bridge start rejected')

    def stop(self, layout):
        self.calls.append('stop bridge')
        self.loaded = False

    def guard(self, action, layout, apk=None, activate=True):
        self.calls.append('guard ' + action)
        path = layout.runtime / 'guardian.json'
        if action == 'install':
            config = load_config(layout.runtime, bridge=False)
            path.write_text(json.dumps({'app_id': APP_ID, 'serial': config['serial'], 'idle_exempt_before': False}))
            self.guard_present = True
            if self.fail_guard:
                raise ValueError('guard grant rejected')
            self.guard_running = activate
        elif action == 'start':
            self.guard_running = True
        elif action == 'stop':
            self.guard_running = False
        elif action == 'uninstall':
            if self.fail_guard_cleanup:
                raise ValueError('device disconnected')
            path.unlink(missing_ok=True)
            self.guard_present = self.guard_running = False

    def test_default_installs_both_and_starts_guard_before_capture(self):
        manage.install(self.args, self.layout)
        self.assertTrue(self.loaded and self.guard_running)
        self.assertEqual(self.calls, ['build', 'guard install', 'start bridge'])
        self.assertEqual(load_config(self.layout.runtime)['gain'], 1)

    def test_bridge_only_does_not_require_apk_or_guard(self):
        self.args.bridge_only = True
        self.apk.unlink()
        manage.install(self.args, self.layout)
        self.assertTrue(self.loaded)
        self.assertFalse(self.guard_present)
        self.assertEqual(self.calls, ['build', 'start bridge'])

    def test_guard_only_does_not_build_or_install_bridge(self):
        self.args.guard_only = True
        manage.install(self.args, self.layout)
        self.assertTrue(self.guard_running)
        self.assertFalse(self.layout.app.exists() or self.layout.plist.exists())
        self.assertEqual(self.calls, ['guard install'])
        self.assertNotIn('gain', load_config(self.layout.runtime, bridge=False))

    def test_guard_only_does_not_require_existing_bridge_dependencies(self):
        manage.install(self.args, self.layout)
        config = load_config(self.layout.runtime)
        config['scrcpy'] = str(self.root / 'removed-scrcpy')
        (self.layout.runtime / 'config.json').write_text(json.dumps(config))
        self.args.guard_only = True
        self.guard_running = False
        manage.install(self.args, self.layout)
        self.assertTrue(self.guard_running)
        self.assertEqual(self.calls.count('build'), 1)

    def test_default_completes_guard_only_without_reinstalling_guard(self):
        self.args.guard_only = True
        manage.install(self.args, self.layout)
        before = (self.layout.runtime / 'guardian.json').read_bytes()
        self.args.guard_only = False
        manage.install(self.args, self.layout)
        self.assertEqual(self.calls.count('guard install'), 1)
        self.assertTrue(self.loaded and self.guard_running)
        self.assertEqual((self.layout.runtime / 'guardian.json').read_bytes(), before)

    def test_repeat_preserves_gain_binding_and_running_components(self):
        self.args.gain = 20
        manage.install(self.args, self.layout)
        config_path = self.layout.runtime / 'config.json'
        config_path.write_text(json.dumps(load_config(self.layout.runtime), separators=(',', ':')))
        config = (self.layout.runtime / 'config.json').read_bytes()
        before_calls = list(self.calls)
        self.args.gain = None
        self.apk.unlink()
        manage.install(self.args, self.layout)
        self.assertEqual(self.calls, before_calls)
        self.assertEqual((self.layout.runtime / 'config.json').read_bytes(), config)

    def test_replacement_adb_repairs_existing_bridge_without_restarting_components(self):
        self.args.gain = 20
        manage.install(self.args, self.layout)
        config = load_config(self.layout.runtime)
        config['adb'] = str(self.root / 'removed-adb')
        (self.layout.runtime / 'config.json').write_text(json.dumps(config))
        guard_state = (self.layout.runtime / 'guardian.json').read_bytes()
        before_calls = list(self.calls)
        self.args.gain = None
        manage.install(self.args, self.layout)
        self.assertEqual(load_config(self.layout.runtime), dict(config, adb=self.args.adb))
        self.assertEqual(self.calls, before_calls)
        self.assertEqual((self.layout.runtime / 'guardian.json').read_bytes(), guard_state)
        self.assertTrue(self.loaded and self.guard_running)

    def test_replacement_adb_repairs_standalone_guard(self):
        self.args.guard_only = True
        manage.install(self.args, self.layout)
        config = load_config(self.layout.runtime, bridge=False)
        config['adb'] = str(self.root / 'removed-adb')
        (self.layout.runtime / 'config.json').write_text(json.dumps(config))
        manage.install(self.args, self.layout)
        self.assertEqual(load_config(self.layout.runtime, bridge=False), dict(config, adb=self.args.adb))
        self.assertFalse(self.layout.app.exists() or self.layout.plist.exists())

    def test_invalid_replacement_adb_leaves_existing_installation_unchanged(self):
        manage.install(self.args, self.layout)
        before = (self.layout.runtime / 'config.json').read_bytes()
        before_calls = list(self.calls)
        nonexecutable = self.root / 'nonexecutable-adb'
        nonexecutable.touch()
        for path in (self.root / 'missing-adb', nonexecutable):
            with self.subTest(path=path):
                self.args.adb = str(path)
                with self.assertRaisesRegex(ValueError, 'adb'):
                    manage.install(self.args, self.layout)
                self.assertEqual((self.layout.runtime / 'config.json').read_bytes(), before)
                self.assertEqual(self.calls, before_calls)

    def test_failed_guard_addition_restores_configuration_before_adb_repair(self):
        self.args.bridge_only = True
        manage.install(self.args, self.layout)
        config = load_config(self.layout.runtime)
        config['adb'] = str(self.root / 'removed-adb')
        before = json.dumps(config, separators=(',', ':')).encode()
        (self.layout.runtime / 'config.json').write_bytes(before)
        self.args.bridge_only = False
        self.fail_guard = True
        with self.assertRaisesRegex(ValueError, 'guard grant rejected'):
            manage.install(self.args, self.layout)
        self.assertEqual((self.layout.runtime / 'config.json').read_bytes(), before)
        self.assertTrue(self.loaded)
        self.assertFalse(self.guard_present)
        self.assertNotIn('stop bridge', self.calls)

    def test_checksum_failure_leaves_no_installation(self):
        self.apk.write_bytes(b'changed artifact')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            manage.install(self.args, self.layout)
        self.assertFalse(self.layout.runtime.exists())
        self.assertEqual(self.calls, [])

    def test_guard_failure_rolls_back_new_bridge_and_guard(self):
        self.fail_guard = True
        with self.assertRaisesRegex(ValueError, 'guard grant rejected'):
            manage.install(self.args, self.layout)
        self.assertFalse(self.layout.app.exists() or self.layout.plist.exists() or self.layout.runtime.exists())
        self.assertFalse(self.loaded or self.guard_present)

    def test_capture_failure_removes_new_components(self):
        self.fail_bridge = True
        with self.assertRaisesRegex(ValueError, 'bridge start rejected'):
            manage.install(self.args, self.layout)
        self.assertIn('stop bridge', self.calls)
        self.assertFalse(self.loaded or self.guard_present)
        self.assertFalse(self.layout.runtime.exists() or self.layout.app.exists() or self.layout.plist.exists())

    def test_failed_guard_addition_preserves_existing_bridge(self):
        self.args.bridge_only = True
        manage.install(self.args, self.layout)
        config = load_config(self.layout.runtime)
        (self.layout.runtime / 'config.json').write_text(json.dumps(config, separators=(',', ':')))
        before = (self.layout.runtime / 'config.json').read_bytes()
        self.args.bridge_only = False
        self.fail_guard = True
        with self.assertRaisesRegex(ValueError, 'guard grant rejected'):
            manage.install(self.args, self.layout)
        self.assertTrue(self.loaded and self.layout.app.is_dir())
        self.assertNotIn('stop bridge', self.calls)
        self.assertEqual((self.layout.runtime / 'config.json').read_bytes(), before)

    def test_no_start_applies_to_both_components(self):
        self.args.no_start = True
        manage.install(self.args, self.layout)
        self.assertTrue(self.guard_present)
        self.assertFalse(self.loaded or self.guard_running)
        self.assertTrue(plistlib.loads(self.layout.plist.read_bytes())['Disabled'])

    def test_existing_explicit_settings_cannot_be_overwritten(self):
        self.args.gain = 20
        manage.install(self.args, self.layout)
        self.args.gain = 1
        with self.assertRaisesRegex(ValueError, 'Existing gain differs'):
            manage.install(self.args, self.layout)
        self.assertEqual(load_config(self.layout.runtime)['gain'], 20)

    def test_failed_device_rollback_retains_connection_record(self):
        self.fail_guard = self.fail_guard_cleanup = True
        with self.assertRaisesRegex(ValueError, 'guard grant rejected'):
            manage.install(self.args, self.layout)
        self.assertEqual(load_config(self.layout.runtime, bridge=False)['serial'], 'TEST_MONITOR')
        self.assertTrue((self.layout.runtime / 'guardian.json').is_file())
        self.assertFalse(self.layout.app.exists() or self.layout.plist.exists())

    def test_failed_bridge_addition_restores_disabled_existing_guard(self):
        self.args.guard_only = True
        self.args.no_start = True
        manage.install(self.args, self.layout)
        before = (self.layout.runtime / 'config.json').read_bytes()
        self.args.guard_only = self.args.no_start = False
        self.fail_bridge = True
        with self.assertRaisesRegex(ValueError, 'bridge start rejected'):
            manage.install(self.args, self.layout)
        self.assertTrue(self.guard_present)
        self.assertFalse(self.guard_running or self.loaded)
        self.assertEqual((self.layout.runtime / 'config.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
