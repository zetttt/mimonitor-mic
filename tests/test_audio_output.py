from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'macOS audio helper')
class AudioOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='rmm-audio-tests-')
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.binary = Path(cls.scratch.name) / 'audio-tests'
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['xcrun', '--sdk', 'macosx', 'swiftc', '-swift-version', '5', '-O',
                        '-parse-as-library', '-D', 'AUDIO_BUFFER_TESTS',
                        str(root / 'src/audio-output.swift'), str(root / 'tests/audio-output-tests.swift'),
                        '-o', str(cls.binary)], check=True, capture_output=True, text=True, timeout=90)

    def check_mode(self, mode, data=b''):
        result = subprocess.run([str(self.binary), mode], input=data,
                                capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode(errors='replace'))
        self.assertIn(('PASS ' + mode).encode(), result.stdout)

    def test_stereo_order_wraparound_and_eof(self):
        self.check_mode('order')

    def test_delayed_burst_keeps_recent_audio_with_a_hard_limit(self):
        self.check_mode('burst')

    def test_starvation_returns_silence_without_blocking_and_recovers(self):
        self.check_mode('starvation')

    def test_gain_and_clipping_are_preserved(self):
        self.check_mode('gain')

    def test_faster_capture_clock_does_not_accumulate_delay(self):
        self.check_mode('fast-clock')

    def test_slower_capture_clock_does_not_run_dry(self):
        self.check_mode('slow-clock')

    def test_partial_pipe_reads_preserve_complete_stereo_frames(self):
        process = subprocess.Popen([str(self.binary), 'reader'], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(process.stdout.readline().strip(), b'READER_READY')
            data = struct.pack('<hhhhhh', 1, -1, 2, -2, 3, -3)
            for chunk in (data[:1], data[1:3], data[3:8], data[8:]):
                process.stdin.write(chunk)
                process.stdin.flush()
                time.sleep(0.02)
            process.stdin.close()
            process.stdin = None
            output, error = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, error.decode(errors='replace'))
            self.assertIn(b'PASS reader', output)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def test_truncated_pcm_reports_an_error(self):
        self.check_mode('truncated-reader', b'\x01\x00\x02')
