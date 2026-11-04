#!/usr/bin/env python3
"""Build the small Android app with the standard SDK, without Gradle dependencies."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent


def run(args):
    subprocess.run([str(arg) for arg in args], check=True)


def main():
    sdk = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or
               Path.home() / 'Library/Android/sdk')
    tools = sdk / 'build-tools/35.0.0'
    android = sdk / 'platforms/android-34/android.jar'
    java = Path(os.environ['JAVA_HOME']) / 'bin' if os.environ.get('JAVA_HOME') else None
    javac = java / 'javac' if java else shutil.which('javac')
    keytool = java / 'keytool' if java else shutil.which('keytool')
    for path in (android, tools / 'aapt2', tools / 'd8', tools / 'zipalign', tools / 'apksigner'):
        if not path.is_file():
            raise SystemExit('Missing Android SDK component: ' + str(path) + '. See docs/guardian.md.')
    if not javac or not keytool:
        raise SystemExit('JDK 17 is required. Set JAVA_HOME; see docs/guardian.md.')
    output = PROJECT / '.build'
    output.mkdir(exist_ok=True)
    signing = Path.home() / '.local/share/mimonitor-mic'
    signing.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = signing / 'guardian.p12'
    if not key.exists():
        previous = os.umask(0o077)
        try:
            run([keytool, '-genkeypair', '-alias', 'guard', '-keyalg', 'RSA', '-keysize', '2048',
                 '-validity', '10000', '-keystore', key, '-storetype', 'PKCS12',
                 '-storepass', 'android', '-dname', 'CN=Mi Monitor Mic local build'])
        finally:
            os.umask(previous)
    key.chmod(0o600)
    with tempfile.TemporaryDirectory(prefix='guardian-', dir=output) as directory:
        work = Path(directory)
        classes = work / 'classes'
        classes.mkdir()
        run([javac, '--release', '8', '-classpath', android, '-d', classes, *sorted((ROOT / 'src').rglob('*.java'))])
        dex = work / 'dex'
        dex.mkdir()
        run([tools / 'd8', '--lib', android, '--min-api', '34', '--output', dex, *sorted(classes.rglob('*.class'))])
        unsigned = work / 'unsigned.apk'
        run([tools / 'aapt2', 'link', '-I', android, '--manifest', ROOT / 'AndroidManifest.xml', '-o', unsigned])
        with zipfile.ZipFile(unsigned, 'a', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(dex / 'classes.dex', 'classes.dex')
        aligned = work / 'aligned.apk'
        run([tools / 'zipalign', '-f', '4', unsigned, aligned])
        apk = output / 'mi-monitor-mic-guard.apk'
        run([tools / 'apksigner', 'sign', '--ks', key, '--ks-key-alias', 'guard',
             '--ks-pass', 'pass:android', '--out', apk, aligned])
        run([tools / 'apksigner', 'verify', apk])
    apk.with_suffix('.apk.sha256').write_text(hashlib.sha256(apk.read_bytes()).hexdigest() + '  ' + apk.name + '\n')
    print('Built ' + str(apk))


if __name__ == '__main__':
    main()
