import Foundation
import AudioToolbox
import CoreAudio
import Darwin

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

func stringProperty(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String {
    var address = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var value: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(id, &address, 0, nil, &size, &value) == noErr else { return "" }
    return value?.takeRetainedValue() as String? ?? ""
}

func outputDevices() -> [(AudioObjectID, String, String)] {
    var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr else { return [] }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    let result = ids.withUnsafeMutableBytes { AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, $0.baseAddress!) }
    guard result == noErr else { return [] }
    return ids.compactMap { id in
        var streams = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreams, mScope: kAudioDevicePropertyScopeOutput, mElement: kAudioObjectPropertyElementMain)
        var streamSize: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(id, &streams, 0, nil, &streamSize) == noErr, streamSize > 0 else { return nil }
        return (id, stringProperty(id, kAudioObjectPropertyName), stringProperty(id, kAudioDevicePropertyDeviceUID))
    }
}

struct AudioBufferMetrics {
    let queuedFrames: Int
    let droppedFrames: Int64
    let underrunFrames: Int64
    let correctionPPM: Int
    let peak: Int
}

final class AudioBuffer {
    private let lock = NSLock()
    private let capacity: Int
    private let target: Int
    private let startup: Int
    private let gain: Double
    private let maximumCorrection: Double
    private var samples: [Int16]
    private var head = 0
    private var count = 0
    private var fraction = 0.0
    private var average: Double
    private var correction = 0.0
    private var dropped: Int64 = 0
    private var underruns: Int64 = 0
    private var peak = 0
    private var closed = false
    private var failure: String?
    private var signalled = false
    let ready = DispatchSemaphore(value: 0)

    init(gain: Double, targetFrames: Int = 6720, capacityFrames: Int = 24000,
         maximumCorrection: Double = 0.005, startupFrames: Int? = nil) {
        precondition(targetFrames > 0 && capacityFrames > targetFrames)
        precondition((startupFrames ?? targetFrames) > 0 && (startupFrames ?? targetFrames) <= capacityFrames)
        self.gain = gain
        target = targetFrames
        startup = startupFrames ?? targetFrames
        capacity = capacityFrames
        self.maximumCorrection = maximumCorrection
        average = Double(targetFrames)
        samples = [Int16](repeating: 0, count: capacityFrames * 2)
    }

    func append(_ input: [Int16]) {
        precondition(input.count % 2 == 0)
        lock.lock()
        defer { lock.unlock() }
        guard !closed else { return }
        let incoming = input.count / 2
        var skip = 0
        if count + incoming > capacity {
            // A delayed burst must not turn this live input into a replay queue.
            let discard = count + incoming - target
            let buffered = min(discard, count)
            head = (head + buffered) % capacity
            count -= buffered
            skip = discard - buffered
            dropped += Int64(discard)
            fraction = 0
            average = Double(target)
        }
        for frame in skip..<incoming {
            let index = (head + count) % capacity
            samples[index * 2] = input[frame * 2]
            samples[index * 2 + 1] = input[frame * 2 + 1]
            count += 1
        }
        if !signalled && count >= startup {
            signalled = true
            ready.signal()
        }
    }

    func close(error: String? = nil) {
        lock.lock()
        defer { lock.unlock() }
        closed = true
        if failure == nil { failure = error }
        if !signalled {
            signalled = true
            ready.signal()
        }
    }

    var error: String? {
        lock.lock()
        defer { lock.unlock() }
        return failure
    }

    func render(_ output: UnsafeMutableBufferPointer<Int16>) -> Bool {
        precondition(output.count % 2 == 0)
        lock.lock()
        defer { lock.unlock() }
        guard !closed || count > 0 else { return false }
        average += (Double(count) - average) * 0.02
        // Smooth the independent capture/playback clocks; do not chase each USB burst.
        correction = closed ? 0 : max(-maximumCorrection, min(maximumCorrection,
                                    (average - Double(target)) / Double(target) * 0.01))
        let rate = 1 + correction
        for frame in 0..<(output.count / 2) {
            guard count > 0 else {
                for index in frame * 2..<output.count { output[index] = 0 }
                if !closed { underruns += Int64(output.count / 2 - frame) }
                fraction = 0
                break
            }
            let next = count > 1 ? (head + 1) % capacity : head
            for channel in 0..<2 {
                let first = Double(samples[head * 2 + channel])
                let second = Double(samples[next * 2 + channel])
                let value = Int16(max(-32767, min(32767, (first + (second - first) * fraction) * gain)))
                output[frame * 2 + channel] = value
                peak = max(peak, abs(Int(value)))
            }
            fraction += rate
            let consumed = min(Int(fraction), count)
            fraction -= Double(consumed)
            head = (head + consumed) % capacity
            count -= consumed
            if count == 0 { fraction = 0 }
        }
        return true
    }

    func metrics(resetPeak: Bool = false) -> AudioBufferMetrics {
        lock.lock()
        defer { lock.unlock() }
        let value = AudioBufferMetrics(queuedFrames: count, droppedFrames: dropped,
                                      underrunFrames: underruns, correctionPPM: Int(correction * 1_000_000), peak: peak)
        if resetPeak { peak = 0 }
        return value
    }
}

final class State {
    let audio: AudioBuffer
    let ended = DispatchSemaphore(value: 0)
    init(gain: Double) {
        // Prime the three AudioQueue buffers without using up the jitter reserve.
        audio = AudioBuffer(gain: gain, startupFrames: 9600)
    }

    func readInput() {
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            var bytes = [UInt8](repeating: 0, count: 16384)
            var pending: [UInt8] = []
            while true {
                let count = bytes.withUnsafeMutableBytes { Darwin.read(STDIN_FILENO, $0.baseAddress!, $0.count) }
                if count < 0 {
                    if errno == EINTR { continue }
                    audio.close(error: "读取音频失败：\(String(cString: strerror(errno)))")
                    return
                }
                if count == 0 {
                    audio.close(error: pending.isEmpty ? nil : "音频在不完整的立体声采样帧处结束。")
                    return
                }
                pending.append(contentsOf: bytes.prefix(count))
                let aligned = pending.count / 4 * 4
                var input = [Int16](repeating: 0, count: aligned / 2)
                for index in 0..<input.count {
                    input[index] = Int16(bitPattern: UInt16(pending[index * 2]) | UInt16(pending[index * 2 + 1]) << 8)
                }
                pending.removeFirst(aligned)
                audio.append(input)
            }
        }
    }

    func fill(_ buffer: AudioQueueBufferRef) -> Bool {
        let count = Int(buffer.pointee.mAudioDataBytesCapacity) / 2
        let output = UnsafeMutableBufferPointer(start: buffer.pointee.mAudioData.assumingMemoryBound(to: Int16.self), count: count)
        guard audio.render(output) else { return false }
        buffer.pointee.mAudioDataByteSize = UInt32(count * 2)
        return true
    }
}

#if !AUDIO_BUFFER_TESTS
@main
enum AudioOutput {
    static func main() {
        let devices = outputDevices()
        if CommandLine.arguments.dropFirst().first == "--list" {
            for (_, name, uid) in devices { print("\(name)\t\(uid)") }
            return
        }
        guard CommandLine.arguments.count == 3, let gain = Double(CommandLine.arguments[2]),
              gain.isFinite, gain >= 0, gain <= 100 else {
            fail("用法：pcm-output 输出设备名称 音量倍数；输入为 48000 Hz 双声道 16 位 PCM。")
        }
        let deviceName = CommandLine.arguments[1]
        guard let device = devices.first(where: { $0.1 == deviceName }) else { fail("找不到音频设备：\(deviceName)。请先完成驱动安装。") }
        let state = State(gain: gain)
        state.readInput()
        guard state.audio.ready.wait(timeout: .now() + 20) == .success else { fail("等待麦克风音频超时。") }
        var format = AudioStreamBasicDescription(mSampleRate: 48000, mFormatID: kAudioFormatLinearPCM, mFormatFlags: kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked, mBytesPerPacket: 4, mFramesPerPacket: 1, mBytesPerFrame: 4, mChannelsPerFrame: 2, mBitsPerChannel: 16, mReserved: 0)
        var queue: AudioQueueRef?
        let callback: AudioQueueOutputCallback = { opaque, queue, buffer in
            guard let opaque else { return }
            let state = Unmanaged<State>.fromOpaque(opaque).takeUnretainedValue()
            if state.fill(buffer) {
                let status = AudioQueueEnqueueBuffer(queue, buffer, 0, nil)
                if status != noErr {
                    state.audio.close(error: "音频排队失败：\(status)")
                    state.ended.signal()
                }
            } else {
                state.ended.signal()
            }
        }
        let status = AudioQueueNewOutput(&format, callback, Unmanaged.passUnretained(state).toOpaque(), nil, nil, 0, &queue)
        guard status == noErr, let queue else { fail("无法建立音频输出：\(status)") }
        let uidString = device.2 as CFString
        var uid = Unmanaged.passUnretained(uidString)
        let selected = withExtendedLifetime(uidString) {
            AudioQueueSetProperty(queue, kAudioQueueProperty_CurrentDevice, &uid, UInt32(MemoryLayout<Unmanaged<CFString>>.size))
        }
        guard selected == noErr else { AudioQueueDispose(queue, true); fail("无法选择输出设备：\(selected)") }
        var queued = 0
        for _ in 0..<3 {
            var buffer: AudioQueueBufferRef?
            guard AudioQueueAllocateBuffer(queue, 3840, &buffer) == noErr, let buffer else { fail("无法建立音频缓冲区") }
            if state.fill(buffer) {
                guard AudioQueueEnqueueBuffer(queue, buffer, 0, nil) == noErr else { fail("音频排队失败") }
                queued += 1
            } else { break }
        }
        guard queued > 0 else { AudioQueueDispose(queue, true); fail(state.audio.error ?? "没有收到麦克风音频") }
        guard AudioQueueStart(queue, nil) == noErr else { AudioQueueDispose(queue, true); fail("无法启动音频输出") }
        FileHandle.standardError.write(Data("已将小米麦克风送入 \(deviceName)，音量放大 \(gain) 倍；已启用低延迟缓冲保护。\n".utf8))
        let reporter = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .utility))
        reporter.schedule(deadline: .now() + 1, repeating: 60)
        reporter.setEventHandler {
            let metrics = state.audio.metrics(resetPeak: true)
            let line = "音频状态：缓冲 \(metrics.queuedFrames * 1000 / 48000)ms，时钟校正 \(metrics.correctionPPM)ppm，跳过旧音频 \(metrics.droppedFrames * 1000 / 48000)ms，缺流补静音 \(metrics.underrunFrames * 1000 / 48000)ms，峰值 \(metrics.peak * 100 / 32767)%\n"
            FileHandle.standardError.write(Data(line.utf8))
        }
        reporter.resume()
        state.ended.wait()
        reporter.cancel()
        AudioQueueStop(queue, true)
        AudioQueueDispose(queue, true)
        if let error = state.audio.error { fail(error) }
    }
}
#endif
