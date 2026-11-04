#!/usr/bin/env python3
"""Package the installer and built guard without development tools or local state."""
import hashlib
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import guardian

ENTRIES = ('Install.command', 'install.sh', 'mic.sh', 'uninstall.sh', 'Makefile', 'LICENSE',
           'README.md', 'README.en.md', 'THIRD_PARTY.md', 'CONTRIBUTING.md',
           'src', 'guardian', 'scripts', 'docs', 'tests')
SUFFIXES = {'.py', '.java', '.xml', '.swift', '.md', '.sh', '.command'}


def make_bundle(apk, output):
    apk = guardian.resolve_apk(apk)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.bundle-', dir=output.parent) as directory:
        temporary = Path(directory) / 'bundle.zip'
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in ENTRIES:
                entry = ROOT / name
                if not entry.exists():
                    raise ValueError('Missing bundle input: ' + name)
                paths = sorted(entry.rglob('*')) if entry.is_dir() else [entry]
                for path in paths:
                    relative = path.relative_to(ROOT)
                    if (not path.is_file() or path.is_symlink() or
                            any(part.startswith('.') or part == '__pycache__' for part in relative.parts)):
                        continue
                    if entry.is_file() or path.suffix in SUFFIXES:
                        archive.write(path, 'MiMonitorMic/' + str(relative))
            archive.write(apk, 'MiMonitorMic/assets/mi-monitor-mic-guard.apk')
            archive.writestr('MiMonitorMic/assets/mi-monitor-mic-guard.apk.sha256',
                             hashlib.sha256(apk.read_bytes()).hexdigest() + '  mi-monitor-mic-guard.apk\n')
        temporary.replace(output)
    return output


if __name__ == '__main__':
    print(make_bundle(guardian.DEFAULT_APK, ROOT / 'dist/mi-monitor-mic.zip'))
