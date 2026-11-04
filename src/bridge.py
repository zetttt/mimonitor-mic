#!/usr/bin/env python3
"""Forward the display's PCM stream to BlackHole without saving audio."""
import fcntl
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time
from settings import APP_NAME, TARGET, load_config, usb_devices

ROOT = Path(__file__).resolve().parent


def stop(process):
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
    except ProcessLookupError:
        pass


def adb(config, *args):
    return subprocess.run([config['adb'], '-P', str(config['port']), *args],
                          text=True, capture_output=True, timeout=12)


def interrupted(_signum, _frame):
    raise KeyboardInterrupt


def write_output(process, data):
    fd = process.stdin.fileno()
    pending = memoryview(data)
    deadline = time.monotonic() + 8
    # A stalled output queue must not block the capture loop's reconnect checks.
    while pending:
        if process.poll() is not None:
            raise RuntimeError('本机音频输出已停止。')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('BlackHole 音频输出连续 8 秒未响应，正在重新连接。')
        _, ready, _ = select.select([], [fd], [], min(0.5, remaining))
        if not ready:
            continue
        try:
            written = os.write(fd, pending)
        except BlockingIOError:
            continue
        if written == 0:
            raise RuntimeError('本机音频输出连接已关闭。')
        pending = pending[written:]


def read_exact(fd, count, process, deadline):
    data = bytearray()
    while len(data) < count:
        if time.monotonic() > deadline:
            raise RuntimeError('显示器没有及时送来音频。请确认显示器已开机、USB 连接可用。')
        ready, _, _ = select.select([fd], [], [], 0.2)
        if not ready:
            continue
        try:
            chunk = os.read(fd, count - len(data))
        except BlockingIOError:
            continue
        if chunk:
            data.extend(chunk)
        elif process.poll() is not None:
            raise RuntimeError('显示器的音频连接已结束。')
        else:
            time.sleep(0.02)
    return bytes(data)


def skip_wave_header(fd, process):
    deadline = time.monotonic() + 20
    header = read_exact(fd, 12, process, deadline)
    if header[:4] not in (b'RIFF', b'RF64') or header[8:] != b'WAVE':
        raise RuntimeError('收到的音频格式不是 WAV。')
    checked_format = False
    while True:
        chunk = read_exact(fd, 8, process, deadline)
        kind, size = chunk[:4], struct.unpack('<I', chunk[4:])[0]
        if kind == b'data':
            if not checked_format:
                raise RuntimeError('音频缺少格式信息。')
            return
        if size > 1024 * 1024:
            raise RuntimeError('音频头格式异常。')
        body = read_exact(fd, size + size % 2, process, deadline)
        if kind == b'fmt ':
            if len(body) < 16:
                raise RuntimeError('音频格式信息不完整。')
            codec, channels, rate, _, _, bits = struct.unpack('<HHIIHH', body[:16])
            if (codec, channels, rate, bits) != (1, 2, 48000, 16):
                raise RuntimeError('显示器送来的音频格式与接收程序不匹配。')
            checked_format = True


def main():
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    config = load_config(ROOT)
    lock_path = ROOT / 'capture.lock'
    with lock_path.open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(APP_NAME + ' is already running.')
        run(config)


def run(config):
    serial = config['serial']
    scrcpy = config['scrcpy']
    pcm = ROOT / 'audio-output'
    devices = subprocess.run([str(pcm), '--list'], text=True, capture_output=True, timeout=8)
    if not any(line.split('\t', 1)[0] == TARGET for line in devices.stdout.splitlines()):
        raise RuntimeError('还未识别到 BlackHole 2ch。请先安装驱动；若已安装，请重启 Mac。')
    environment = os.environ.copy()
    environment.update(ADB=config['adb'], ADB_SERVER_SOCKET='tcp:127.0.0.1:' + str(config['port']),
                       SCRCPY_SERVER_PATH=config['server'])
    capture = output = None
    try:
        result = adb(config, 'start-server')
        if result.returncode:
            raise RuntimeError('无法启动显示器连接：' + result.stderr.strip())
        connected = [device for device in usb_devices(config['adb'], config['port'])
                     if device['serial'] == serial and device['state'] == 'device']
        if len(connected) != 1:
            raise RuntimeError('Configured USB monitor is unavailable; waiting for its USB connection.')
        result = adb(config, '-s', serial, 'get-state')
        if result.returncode or result.stdout.strip() != 'device':
            raise RuntimeError('显示器 USB 连接不可用，请检查连接线、USB 调试及显示器上的授权提示：' + (result.stdout + result.stderr).strip())
        with tempfile.TemporaryDirectory(prefix='xiaomi-mic-') as scratch:
            fifo = Path(scratch) / 'audio.wav'
            os.mkfifo(fifo, 0o600)
            fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            try:
                args = [str(scrcpy), '-s', serial, '--audio-source=mic', '--audio-codec=raw', '--no-video', '--no-window', '--no-control', '--no-playback', '--require-audio', '--record-format=wav', '--record=' + str(fifo)]
                capture = subprocess.Popen(args, env=environment, stdout=sys.stderr, stderr=sys.stderr, start_new_session=True)
                skip_wave_header(fd, capture)
                output = subprocess.Popen([str(pcm), TARGET, str(config['gain'])], stdin=subprocess.PIPE, bufsize=0, start_new_session=True)
                os.set_blocking(output.stdin.fileno(), False)
                print(APP_NAME + ': USB connected. Select BlackHole 2ch in the receiving app.', flush=True)
                print('本程序不保存录音。', flush=True)
                last_data = time.monotonic()
                while True:
                    if time.monotonic() - last_data > 8:
                        raise RuntimeError('已连续 8 秒收不到显示器音频，连接已停止。')
                    if output.poll() is not None:
                        raise RuntimeError('本机音频输出已停止。')
                    ready, _, _ = select.select([fd], [], [], 0.5)
                    if not ready:
                        if capture.poll() is not None:
                            break
                        continue
                    try:
                        chunk = os.read(fd, 16384)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        if capture.poll() is not None:
                            break
                        time.sleep(0.02)
                        continue
                    last_data = time.monotonic()
                    write_output(output, chunk)
                if capture.wait(timeout=3) != 0:
                    raise RuntimeError('显示器的音频采集退出，请检查上方错误信息。')
            finally:
                os.close(fd)
    finally:
        stop(capture)
        stop(output)
        if output is not None and output.stdin is not None:
            try:
                output.stdin.close()
            except BrokenPipeError:
                pass
        try:
            adb(config, 'kill-server')
        except (OSError, subprocess.TimeoutExpired):
            pass


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(APP_NAME + ' stopped.', flush=True)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('无法启动：' + str(error), file=sys.stderr, flush=True)
        sys.exit(1)
