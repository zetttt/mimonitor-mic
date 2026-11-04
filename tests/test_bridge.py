import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import bridge
import settings


class USBSelectionTests(unittest.TestCase):
    def test_network_devices_are_not_usb_candidates(self):
        listing = ('List of devices attached\n'
                   '192.0.2.1:5555 device product:test model:MiTV_MFFU1 transport_id:2\n'
                   'TEST_MONITOR device usb:1-2 product:test model:MiTV_MFFU1 transport_id:1\n')
        with patch.object(settings.subprocess, 'run', return_value=types.SimpleNamespace(stdout=listing)):
            self.assertEqual(settings.usb_devices('/fake/adb'),
                             [{'serial': 'TEST_MONITOR', 'state': 'device', 'model': 'MiTV_MFFU1'}])

    def test_multiple_monitors_require_explicit_selection(self):
        devices = [{'serial': serial, 'state': 'device', 'model': settings.MODEL}
                   for serial in ('TEST_ONE', 'TEST_TWO')]
        with self.assertRaisesRegex(ValueError, 'Multiple'):
            settings.choose_device(devices)
        self.assertEqual(settings.choose_device(devices, 'TEST_TWO')['serial'], 'TEST_TWO')

    def test_phone_and_unauthorized_monitor_are_not_silently_used(self):
        with self.assertRaises(ValueError):
            settings.choose_device([{'serial': 'PHONE', 'state': 'device', 'model': 'Other'}])
        with self.assertRaisesRegex(ValueError, 'Allow USB debugging'):
            settings.choose_device([{'serial': 'MONITOR', 'state': 'unauthorized', 'model': settings.MODEL}])


class StreamTests(unittest.TestCase):
    def parse(self, payload):
        reader, writer = os.pipe()
        try:
            os.set_blocking(reader, False)
            os.write(writer, payload)
            os.close(writer)
            writer = None
            bridge.skip_wave_header(reader, types.SimpleNamespace(poll=lambda: 0))
            return os.read(reader, 16)
        finally:
            os.close(reader)
            if writer is not None:
                os.close(writer)

    def test_streaming_wav_accepts_metadata_and_preserves_first_samples(self):
        fmt = struct.pack('<HHIIHH', 1, 2, 48000, 192000, 4, 16)
        wav = b'RIFF' + struct.pack('<I', 0xffffffff) + b'WAVE'
        wav += b'JUNK' + struct.pack('<I', 3) + b'abc\0'
        wav += b'fmt ' + struct.pack('<I', len(fmt)) + fmt
        wav += b'data' + struct.pack('<I', 0xffffffff) + b'\x01\x00\x02\x00'
        self.assertEqual(self.parse(wav), b'\x01\x00\x02\x00')

    def test_wrong_format_and_oversized_chunks_are_rejected(self):
        with self.assertRaises(RuntimeError):
            self.parse(b'NOT A WAVE!!')
        with self.assertRaisesRegex(RuntimeError, '音频头'):
            self.parse(b'RIFF\0\0\0\0WAVEJUNK' + struct.pack('<I', 2 * 1024 * 1024))
        fmt = struct.pack('<HHIIHH', 1, 1, 44100, 88200, 2, 16)
        with self.assertRaisesRegex(RuntimeError, '不匹配'):
            self.parse(b'RIFF\0\0\0\0WAVEfmt ' + struct.pack('<I', 16) + fmt)

    def test_pipe_write_preserves_large_payload_with_partial_writes(self):
        data = bytes(range(256)) * 4096
        process = subprocess.Popen(
            [sys.executable, '-c', 'import sys,hashlib;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0, start_new_session=True)
        try:
            os.set_blocking(process.stdin.fileno(), False)
            bridge.write_output(process, data)
            process.stdin.close()
            self.assertEqual(process.stdout.read().decode().strip(), hashlib.sha256(data).hexdigest())
            self.assertEqual(process.wait(timeout=3), 0)
        finally:
            bridge.stop(process)
            process.stdout.close()

    def test_stalled_output_releases_control_instead_of_blocking_forever(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'],
                                   stdin=subprocess.PIPE, bufsize=0, start_new_session=True)
        try:
            os.set_blocking(process.stdin.fileno(), False)
            with self.assertRaisesRegex(RuntimeError, '8 秒未响应'):
                bridge.write_output(process, b'\0' * (1024 * 1024))
        finally:
            bridge.stop(process)
            process.stdin.close()

    def test_synthetic_capture_uses_fifo_only_and_cleans_up(self):
        with tempfile.TemporaryDirectory(prefix='rmm-stream-') as directory:
            runtime = Path(directory)
            producer = runtime / 'fake-scrcpy'
            producer.write_text('#!' + sys.executable + '\n'
                                'import os,sys,struct,json,stat,pathlib\n'
                                'path=next(x.split("=",1)[1] for x in sys.argv if x.startswith("--record="))\n'
                                'pathlib.Path(__file__).with_name("pipe-check.json").write_text(json.dumps({"path":path,"fifo":stat.S_ISFIFO(os.stat(path).st_mode)}))\n'
                                'fd=os.open(path,os.O_WRONLY)\n'
                                'fmt=struct.pack("<HHIIHH",1,2,48000,192000,4,16)\n'
                                'os.write(fd,b"RIFF"+struct.pack("<I",0xffffffff)+b"WAVEfmt "+struct.pack("<I",16)+fmt+b"data"+struct.pack("<I",0xffffffff))\n'
                                'for i in range(64): os.write(fd,b"\\x01\\x00\\x02\\x00"*960)\n'
                                'os.close(fd)\n')
            producer.chmod(0o700)
            sink = runtime / 'audio-output'
            sink.write_text('#!' + sys.executable + '\n'
                            'import sys\n'
                            'if "--list" in sys.argv: print("BlackHole 2ch\\tBlackHole2ch_UID")\n'
                            'else:\n'
                            ' while sys.stdin.buffer.read(4096): pass\n')
            sink.chmod(0o700)
            config = {'adb': '/fake/adb', 'scrcpy': str(producer), 'server': '/fake/server',
                      'serial': 'TEST_MONITOR', 'port': settings.ADB_PORT, 'gain': 1}
            calls = []

            def fake_adb(config, *args):
                calls.append(args)
                return types.SimpleNamespace(returncode=0, stdout='device\n', stderr='')

            devices = [{'serial': 'TEST_MONITOR', 'state': 'device', 'model': settings.MODEL}]
            with patch.object(bridge, 'ROOT', runtime), patch.object(bridge, 'adb', fake_adb), \
                    patch.object(bridge, 'usb_devices', return_value=devices), \
                    contextlib.redirect_stdout(io.StringIO()):
                bridge.run(config)
            self.assertNotIn('connect', [call[0] for call in calls])
            self.assertEqual(calls[-1], ('kill-server',))
            pipe = json.loads((runtime / 'pipe-check.json').read_text())
            self.assertTrue(pipe['fifo'])
            self.assertFalse(Path(pipe['path']).exists())
            self.assertFalse(list(runtime.rglob('*.wav')))


if __name__ == '__main__':
    unittest.main()
