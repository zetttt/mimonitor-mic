import Foundation

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else { fatalError(message) }
}

func render(_ audio: AudioBuffer, frames: Int) -> (Bool, [Int16]) {
    var output = [Int16](repeating: -1, count: frames * 2)
    let active = output.withUnsafeMutableBufferPointer { audio.render($0) }
    return (active, output)
}

func frames(_ range: Range<Int>) -> [Int16] {
    range.flatMap { [Int16($0), -Int16($0)] }
}

func simulateClock(_ drift: Double, correction: Double) -> AudioBufferMetrics {
    let audio = AudioBuffer(gain: 1, maximumCorrection: correction)
    let burst = [Int16](repeating: 1200, count: 8192 * 2)
    audio.append(burst)
    audio.append(burst)
    var credit = 0.0
    var output = [Int16](repeating: 0, count: 960 * 2)
    for _ in 0..<3 { _ = output.withUnsafeMutableBufferPointer { audio.render($0) } }
    // Twenty minutes of independent clocks, delivered in realistic ~171 ms bursts.
    for _ in 0..<60000 {
        credit += 960 * (1 + drift)
        while credit >= 8192 {
            audio.append(burst)
            credit -= 8192
        }
        _ = output.withUnsafeMutableBufferPointer { audio.render($0) }
    }
    return audio.metrics()
}

@main
enum AudioOutputTests {
    static func main() {
        let mode = CommandLine.arguments[1]
        switch mode {
        case "order":
            let audio = AudioBuffer(gain: 1, targetFrames: 4, capacityFrames: 12, maximumCorrection: 0)
            audio.append(frames(1..<9))
            require(render(audio, frames: 6).1 == frames(1..<7), "Initial stereo order changed")
            audio.append(frames(9..<15))
            audio.close()
            require(render(audio, frames: 8).1 == frames(7..<15), "Wrapped stereo order changed")
            require(!render(audio, frames: 1).0, "EOF did not drain")
        case "burst":
            let audio = AudioBuffer(gain: 1, targetFrames: 4, capacityFrames: 12, maximumCorrection: 0)
            audio.append(frames(1..<5))
            audio.append(frames(5..<105))
            require(audio.metrics().queuedFrames == 4, "Delayed burst was not bounded")
            require(audio.metrics().droppedFrames == 100, "Discarded duration was not counted")
            audio.close()
            require(render(audio, frames: 4).1 == frames(101..<105), "Burst retained stale audio")
        case "starvation":
            let audio = AudioBuffer(gain: 1, targetFrames: 4, capacityFrames: 12, maximumCorrection: 0)
            let empty = render(audio, frames: 8)
            require(empty.0 && empty.1.allSatisfy { $0 == 0 }, "Starvation did not return silence")
            require(audio.metrics().underrunFrames == 8, "Missing audio was not counted")
            audio.append(frames(1..<5))
            require(render(audio, frames: 4).1 == frames(1..<5), "Capture did not recover after starvation")
            audio.close()
            require(!render(audio, frames: 1).0, "Closed empty input stayed alive")
        case "gain":
            let audio = AudioBuffer(gain: 20, targetFrames: 2, capacityFrames: 4, maximumCorrection: 0)
            audio.append([10000, -10000, 1, -1])
            audio.close()
            require(render(audio, frames: 2).1 == [32767, -32767, 20, -20], "Gain or clipping changed")
        case "fast-clock":
            let uncorrected = simulateClock(0.001, correction: 0)
            require(uncorrected.droppedFrames > 0, "Regression scenario did not accumulate delay")
            let corrected = simulateClock(0.001, correction: 0.005)
            require(corrected.droppedFrames == 0, "Fast clock caused stale-audio drops")
            require(corrected.underrunFrames == 0, "Fast clock caused gaps")
            require(corrected.queuedFrames < 16000, "Clock correction did not control delay")
        case "slow-clock":
            let corrected = simulateClock(-0.001, correction: 0.005)
            require(corrected.droppedFrames == 0, "Slow clock overflowed")
            require(corrected.underrunFrames == 0, "Slow clock exhausted audio")
            require(corrected.queuedFrames < 16000, "Slow clock accumulated delay")
        case "reader":
            let state = State(gain: 1)
            state.readInput()
            print("READER_READY")
            fflush(stdout)
            require(state.audio.ready.wait(timeout: .now() + 3) == .success, "Input reader stalled")
            require(state.audio.error == nil, "Valid fragmented PCM was rejected")
            require(render(state.audio, frames: 3).1 == [1, -1, 2, -2, 3, -3], "Partial reads corrupted PCM")
            require(!render(state.audio, frames: 1).0, "EOF did not stop the input")
        case "truncated-reader":
            let state = State(gain: 1)
            state.readInput()
            require(state.audio.ready.wait(timeout: .now() + 3) == .success, "Incomplete input stalled")
            require(state.audio.error != nil, "Truncated PCM was silently accepted")
        default:
            fatalError("Unknown test")
        }
        print("PASS \(mode)")
    }
}
