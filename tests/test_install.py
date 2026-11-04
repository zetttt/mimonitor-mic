import argparse
import contextlib
import io
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import manage
from settings import APP_ID, ADB_PORT, Layout, load_config


class ConfigurationTests(unittest.TestCase):
    def test_authorized_monitor_does_not_wait_for_an_unrelated_phone(self):
        ready = {'serial': 'TEST_MONITOR', 'state': 'device', 'model': 'MiTV_MFFU1'}
        pending = {'serial': 'PHONE', 'state': 'unauthorized', 'model': ''}
        with patch.object(manage, 'usb_devices', return_value=[ready, pending]), \
                patch.object(manage.time, 'sleep') as sleep:
            self.assertEqual(manage.select_device('/fake/adb', None), ready)
            sleep.assert_not_called()

    def test_first_usb_authorization_is_waited_for_before_selecting_model(self):
        pending = [{'serial': 'TEST_MONITOR', 'state': 'unauthorized', 'model': ''}]
        ready = [{'serial': 'TEST_MONITOR', 'state': 'device', 'model': 'MiTV_MFFU1'}]
        with patch.object(manage, 'usb_devices', side_effect=[pending, ready]), \
                patch.object(manage.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(manage.select_device('/fake/adb', None), ready[0])

    def test_usb_authorization_wait_has_a_deadline(self):
        pending = [{'serial': 'TEST_MONITOR', 'state': 'unauthorized', 'model': 'MiTV_MFFU1'}]
        with patch.object(manage, 'usb_devices', return_value=pending), \
                patch.object(manage.time, 'monotonic', side_effect=[0, 61]), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'Allow USB debugging'):
                manage.select_device('/fake/adb', 'TEST_MONITOR')

    def test_non_object_configuration_is_rejected_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout(Path(directory))
            layout.runtime.mkdir(parents=True)
            for value in ([], None):
                (layout.runtime / 'config.json').write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    load_config(layout.runtime)
                with self.assertRaises(ValueError):
                    manage.validate_owned(layout)
            (layout.runtime / 'config.json').write_text(json.dumps({'app_id': APP_ID}))
            info = layout.app / 'Contents/Info.plist'
            info.parent.mkdir(parents=True)
            info.write_bytes(plistlib.dumps([]))
            with self.assertRaises(ValueError):
                manage.validate_owned(layout)
            info.write_bytes(plistlib.dumps({'CFBundleIdentifier': APP_ID}))
            layout.plist.parent.mkdir(parents=True)
            layout.plist.write_bytes(plistlib.dumps([]))
            with self.assertRaises(ValueError):
                manage.validate_owned(layout)

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS installer')
    def test_discovery_error_survives_cleanup_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout(Path(directory) / 'home')
            server = Path(directory) / 'scrcpy-server'
            server.touch()
            args = argparse.Namespace(adb='/usr/bin/true', scrcpy='/usr/bin/true',
                                      server=str(server), serial=None, gain=1, no_start=True,
                                      bridge_only=True, guard_only=False, apk=None)

            def run(args, **kwargs):
                if 'kill-server' in args:
                    raise subprocess.TimeoutExpired(args, kwargs['timeout'])
                return subprocess.CompletedProcess(args, 0, stdout='/usr/bin/true\n')

            with patch.object(manage, 'usb_devices', return_value=[]), \
                    patch.object(manage, 'loaded', return_value=False), \
                    patch.object(manage.subprocess, 'run', side_effect=run), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(ValueError, 'No supported USB monitor'):
                    manage.install(args, layout)

    def test_static_scrcpy_server_does_not_require_homebrew_package(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'scrcpy'
            server = Path(directory) / 'scrcpy-server'
            binary.touch()
            server.touch()
            with patch.object(manage.subprocess, 'run') as run:
                self.assertEqual(manage.find_server(str(binary)), str(server))
                run.assert_not_called()

    def test_ip_addresses_and_nonfinite_gain_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = root / 'dependency'
            tool.touch()
            base = {'app_id': APP_ID, 'version': 1, 'port': ADB_PORT, 'serial': 'TEST_MONITOR',
                    'gain': 1, 'adb': str(tool), 'scrcpy': str(tool), 'server': str(tool), 'python': str(tool)}
            for key, value in [('serial', '192.0.2.1:5555'), ('gain', float('nan')),
                               ('gain', float('inf')), ('gain', True), ('port', 5037)]:
                config = dict(base, **{key: value})
                (root / 'config.json').write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    load_config(root)
                with self.assertRaises(ValueError):
                    load_config(root, adb_path=str(tool))
            (root / 'config.json').write_text(json.dumps(base))
            self.assertEqual(load_config(root)['serial'], 'TEST_MONITOR')

    def test_foreign_installation_is_not_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout(Path(directory))
            layout.runtime.mkdir(parents=True)
            config = layout.runtime / 'config.json'
            config.write_text(json.dumps({'app_id': 'another.project'}))
            with patch.object(manage, 'stop') as stop:
                with self.assertRaisesRegex(ValueError, 'ownership'):
                    manage.uninstall(layout)
                stop.assert_not_called()
            self.assertTrue(config.exists())

    def test_symlinked_runtime_is_not_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            layout = Layout(home)
            victim = home / 'unrelated'
            victim.mkdir()
            layout.runtime.parent.mkdir(parents=True)
            layout.runtime.symlink_to(victim)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                manage.validate_owned(layout)
            self.assertTrue(victim.exists())


@unittest.skipUnless(sys.platform == 'darwin', 'macOS source build smoke test')
class InstallSmokeTest(unittest.TestCase):
    def test_default_full_install_builds_without_starting_either_service(self):
        with tempfile.TemporaryDirectory(prefix='full installer ') as directory:
            layout = Layout(Path(directory) / 'home')
            server = Path(directory) / 'scrcpy-server'
            server.touch()
            args = argparse.Namespace(adb='/usr/bin/true', scrcpy='/usr/bin/true',
                                      server=str(server), serial=None, gain=None, no_start=True,
                                      bridge_only=False, guard_only=False, apk=None)
            devices = [{'serial': 'TEST_MONITOR', 'state': 'device', 'model': 'MiTV_MFFU1'}]
            with patch.object(manage, 'usb_devices', return_value=devices), \
                    patch.object(manage, 'loaded', return_value=False), \
                    patch.object(manage, 'start') as start, \
                    patch.object(manage.guardian, 'installed', return_value=False), \
                    patch.object(manage.guardian, 'resolve_apk', return_value=server), \
                    patch.object(manage.guardian, 'require_android'), \
                    patch.object(manage.guardian, 'control') as control:
                manage.install(args, layout)
                start.assert_not_called()
                control.assert_called_once_with('install', layout, server, activate=False)
            self.assertTrue((layout.runtime / 'audio-output').is_file())
            self.assertTrue((layout.app / 'Contents/MacOS/mimonitor-mic').is_file())
            self.assertEqual(load_config(layout.runtime)['gain'], 1)
            self.assertTrue(plistlib.loads(layout.plist.read_bytes())['Disabled'])

    def test_build_install_without_start_and_scoped_uninstall(self):
        with tempfile.TemporaryDirectory(prefix='rmm home with spaces ') as directory:
            layout = Layout(Path(directory) / 'home')
            server = Path(directory) / 'scrcpy-server'
            server.touch()
            args = argparse.Namespace(adb='/usr/bin/true', scrcpy='/usr/bin/true',
                                      server=str(server), serial=None, gain=1, no_start=True,
                                      bridge_only=True, guard_only=False, apk=None)
            devices = [{'serial': 'TEST_MONITOR', 'state': 'device', 'model': 'MiTV_MFFU1'}]
            with patch.object(manage, 'usb_devices', return_value=devices), \
                    patch.object(manage, 'loaded', return_value=False), \
                    patch.object(manage, 'start') as start:
                manage.install(args, layout)
                start.assert_not_called()
            self.assertTrue((layout.app / 'Contents/MacOS/mimonitor-mic').is_file())
            self.assertEqual(load_config(layout.runtime)['serial'], 'TEST_MONITOR')
            plist = plistlib.loads(layout.plist.read_bytes())
            self.assertEqual(plist['Label'], APP_ID)
            self.assertTrue(plist['Disabled'])
            self.assertEqual(plist['StandardOutPath'], str(layout.log))
            before = (layout.runtime / 'config.json').read_bytes()
            with patch.object(manage, 'usb_devices', return_value=devices), \
                    patch.object(manage, 'loaded', return_value=False), \
                    patch.object(manage, 'build_bridge') as build:
                manage.install(args, layout)
                build.assert_not_called()
            self.assertEqual((layout.runtime / 'config.json').read_bytes(), before)
            layout.log.write_text('synthetic log\n')
            with patch.object(manage, 'stop') as stop:
                manage.uninstall(layout)
                stop.assert_called_once()
            self.assertTrue(server.exists())
            self.assertTrue(layout.log.exists())
            self.assertFalse(layout.runtime.exists())
            self.assertFalse(layout.app.exists())
            self.assertFalse(layout.plist.exists())
            manage.uninstall(layout)


if __name__ == '__main__':
    unittest.main()
