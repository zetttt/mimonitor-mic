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

let devices = outputDevices()
if CommandLine.arguments.dropFirst().first == "--list" {
    for (_, name, uid) in devices { print("\(name)\t\(uid)") }
    exit(0)
}
guard CommandLine.arguments.count == 3, let gain = Double(CommandLine.arguments[2]), gain >= 0, gain <= 100 else {
    fail("用法：pcm-output 输出设备名称 音量倍数；输入为 48000 Hz 双声道 16 位 PCM。")
}
let deviceName = CommandLine.arguments[1]
guard let device = devices.first(where: { $0.1 == deviceName }) else { fail("找不到音频设备：\(deviceName)。请先完成驱动安装。") }

final class State {
    let gain: Double
    let ended = DispatchSemaphore(value: 0)
    var finished = false
    var samples: Int64 = 0
    var reportAfter: Int64 = 96000
    var peak = 0
    init(gain: Double) { self.gain = gain }

    func fill(_ buffer: AudioQueueBufferRef) -> Bool {
        let capacity = Int(buffer.pointee.mAudioDataBytesCapacity)
        var count = 0
        while count < capacity {
            let n = Darwin.read(STDIN_FILENO, buffer.pointee.mAudioData.advanced(by: count), capacity - count)
            if n == 0 { break }
            if n < 0 {
                if errno == EINTR { continue }
                break
            }
            count += n
        }
        count -= count % 4
        guard count > 0 else { return false }
        let values = buffer.pointee.mAudioData.assumingMemoryBound(to: Int16.self)
        for i in 0 ..< count / 2 {
            let sample = max(-32767.0, min(32767.0, Double(values[i]) * gain))
            values[i] = Int16(sample)
            peak = max(peak, abs(Int(values[i])))
        }
        samples += Int64(count / 2)
        if samples >= reportAfter {
            FileHandle.standardError.write(Data("拾音运行中，峰值 \(Int(Double(peak) / 32767 * 100))%\n".utf8))
            samples = 0
            peak = 0
            reportAfter = 5760000
        }
        buffer.pointee.mAudioDataByteSize = UInt32(count)
        return true
    }
}

let state = State(gain: gain)
var format = AudioStreamBasicDescription(mSampleRate: 48000, mFormatID: kAudioFormatLinearPCM, mFormatFlags: kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked, mBytesPerPacket: 4, mFramesPerPacket: 1, mBytesPerFrame: 4, mChannelsPerFrame: 2, mBitsPerChannel: 16, mReserved: 0)
var queue: AudioQueueRef?
let callback: AudioQueueOutputCallback = { opaque, queue, buffer in
    guard let opaque else { return }
    let state = Unmanaged<State>.fromOpaque(opaque).takeUnretainedValue()
    if state.finished { return }
    if state.fill(buffer) {
        if AudioQueueEnqueueBuffer(queue, buffer, 0, nil) != noErr {
            state.finished = true
            state.ended.signal()
        }
    } else {
        state.finished = true
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
for _ in 0 ..< 3 {
    var buffer: AudioQueueBufferRef?
    guard AudioQueueAllocateBuffer(queue, 3840, &buffer) == noErr, let buffer else { fail("无法建立音频缓冲区") }
    if state.fill(buffer) {
        guard AudioQueueEnqueueBuffer(queue, buffer, 0, nil) == noErr else { fail("音频排队失败") }
        queued += 1
    } else { break }
}
guard queued > 0 else { AudioQueueDispose(queue, true); fail("没有收到麦克风音频") }
guard AudioQueueStart(queue, nil) == noErr else { AudioQueueDispose(queue, true); fail("无法启动音频输出") }
FileHandle.standardError.write(Data("已将小米麦克风送入 \(deviceName)，音量放大 \(gain) 倍。\n".utf8))
state.ended.wait()
AudioQueueStop(queue, true)
AudioQueueDispose(queue, true)
