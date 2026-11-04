import hashlib
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'src'))
import bundle


class BundleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        for name in bundle.ENTRIES:
            path = self.project / name
            if name in ('src', 'guardian', 'scripts', 'docs', 'tests'):
                path.mkdir()
                (path / 'example.py').write_text('# source\n')
            else:
                path.write_text('#!/bin/bash\n' if name.endswith(('.sh', '.command')) else 'source\n')
        (self.project / 'Install.command').chmod(0o755)
        (self.project / 'guardian/private.p12').write_bytes(b'private key')
        (self.project / 'src/config.json').write_text('private config')
        cache = self.project / 'src/__pycache__'
        cache.mkdir()
        (cache / 'private.py').write_text('private cache')
        self.apk = self.root / 'custom-name.apk'
        self.apk.write_bytes(b'test APK')
        self.apk.with_suffix('.apk.sha256').write_text(hashlib.sha256(self.apk.read_bytes()).hexdigest())
        self.output = self.root / 'bundle.zip'

    def test_bundle_contains_guard_checksum_executable_entry_and_no_private_files(self):
        with patch.object(bundle, 'ROOT', self.project):
            bundle.make_bundle(self.apk, self.output)
        with zipfile.ZipFile(self.output) as archive:
            names = archive.namelist()
            self.assertIn('MiMonitorMic/src/example.py', names)
            self.assertIn('MiMonitorMic/guardian/example.py', names)
            self.assertEqual(archive.read('MiMonitorMic/assets/mi-monitor-mic-guard.apk'), self.apk.read_bytes())
            checksum = archive.read('MiMonitorMic/assets/mi-monitor-mic-guard.apk.sha256').decode()
            self.assertEqual(checksum.split(), [hashlib.sha256(self.apk.read_bytes()).hexdigest(), 'mi-monitor-mic-guard.apk'])
            mode = archive.getinfo('MiMonitorMic/Install.command').external_attr >> 16
            self.assertTrue(mode & stat.S_IXUSR)
            self.assertFalse(any(name.endswith('.p12') or 'config.json' in name or '__pycache__' in name for name in names))

    def test_corrupt_apk_is_rejected_before_overwriting_bundle(self):
        self.output.write_bytes(b'existing bundle')
        self.apk.write_bytes(b'changed APK')
        with patch.object(bundle, 'ROOT', self.project):
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                bundle.make_bundle(self.apk, self.output)
        self.assertEqual(self.output.read_bytes(), b'existing bundle')


if __name__ == '__main__':
    unittest.main()
