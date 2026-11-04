import AppKit
import Foundation
import Darwin

@MainActor
final class MicrophoneService: NSObject, NSApplicationDelegate {
    private var capture: Process?
    private var stopping = false
    private var signals: [DispatchSourceSignal] = []
    private var runtime: URL {
        guard let path = Bundle.main.object(forInfoDictionaryKey: "RMMRuntimeDirectory") as? String else {
            fatalError("Missing runtime directory in app bundle")
        }
        return URL(fileURLWithPath: path)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        for number in [SIGTERM, SIGINT, SIGHUP] {
            signal(number, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
            source.setEventHandler { NSApp.terminate(nil) }
            source.resume()
            signals.append(source)
        }
        startCapture()
    }

    private func startCapture() {
        guard !stopping else { return }
        let process = Process()
        let configURL = runtime.appendingPathComponent("config.json")
        do {
            let data = try Data(contentsOf: configURL)
            guard let config = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let python = config["python"] as? String else {
                throw CocoaError(.fileReadCorruptFile)
            }
            process.executableURL = URL(fileURLWithPath: python)
        } catch {
            FileHandle.standardError.write(Data("Cannot load runtime configuration: \(error)\n".utf8))
            retry()
            return
        }
        process.arguments = [runtime.appendingPathComponent("bridge.py").path]
        process.currentDirectoryURL = runtime
        process.standardOutput = FileHandle.standardOutput
        process.standardError = FileHandle.standardError
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self else { return }
                self.capture = nil
                if self.stopping {
                    NSApp.reply(toApplicationShouldTerminate: true)
                } else {
                    self.retry()
                }
            }
        }
        capture = process
        do {
            try process.run()
        } catch {
            capture = nil
            FileHandle.standardError.write(Data("无法启动采音程序：\(error)\n".utf8))
            retry()
        }
    }

    private func retry() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [weak self] in
            self?.startCapture()
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        stopping = true
        guard let capture, capture.isRunning else { return .terminateNow }
        capture.terminate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 35) {
            if capture.isRunning { kill(capture.processIdentifier, SIGKILL) }
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}

@main
enum Main {
    @MainActor
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let service = MicrophoneService()
        app.delegate = service
        app.run()
    }
}
