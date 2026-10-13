import Foundation
import Observation
import TrignoKit
import TrignoLSL
#if os(iOS)
import UIKit
#endif

struct LogEntry: Identifiable {
    let id = UUID()
    let time = Date()
    let text: String
    let isError: Bool
}

struct Recording: Identifiable, Hashable {
    let url: URL
    let date: Date
    var id: URL { url }
    var name: String { url.lastPathComponent }

    var files: [URL] {
        (try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: nil))?
            .sorted { $0.lastPathComponent < $1.lastPathComponent } ?? []
    }
}

@MainActor
@Observable
final class AppModel {
    enum Phase: Equatable {
        case disconnected, connecting, connected, armed, streaming
    }

    var host: String = UserDefaults.standard.string(forKey: "host") ?? "192.168.1.50" {
        didSet { UserDefaults.standard.set(host, forKey: "host") }
    }
    private(set) var phase: Phase = .disconnected
    private(set) var system: SystemInfo?
    private(set) var log: [LogEntry] = []
    var alert: String?

    // Acquisition options
    var saveToFile = true
    var triggerStart = false
    var triggerStop = false
    var durationLimit: Double = 0  // seconds, 0 = until stopped
    var plotWindow: Double = 5 {
        didSet { if let system { buffers.configure(system: system, window: plotWindow) } }
    }

    // Lab Streaming Layer
    var lslEnabled: Bool = UserDefaults.standard.bool(forKey: "lslEnabled") {
        didSet { UserDefaults.standard.set(lslEnabled, forKey: "lslEnabled") }
    }
    var lsl: LSLConfiguration = AppModel.loadLSLConfiguration() {
        didSet { UserDefaults.standard.set(try? JSONEncoder().encode(lsl), forKey: "lslConfiguration") }
    }
    private(set) var lslStreaming = false
    private(set) var lslHasConsumers = false

    private(set) var dataStartedAt: Date?
    private(set) var currentRecording: URL?
    private(set) var recordings: [Recording] = []
    private(set) var lastSampleCounts: [DataPort: Int] = [:]

    let buffers = SignalBuffers()
    private var session: TrignoSession?
    private var recorder: CSVRecorder?
    private var streamer: LSLStreamer?
    private var eventTask: Task<Void, Never>?
    private var monitorTask: Task<Void, Never>?

    var isConnected: Bool { phase != .disconnected && phase != .connecting }
    var isAcquiring: Bool { phase == .armed || phase == .streaming }

    static var recordingsDirectory: URL {
        URL.documentsDirectory.appending(path: "Recordings", directoryHint: .isDirectory)
    }

    init() {
        reloadRecordings()
    }

    // MARK: Connection

    func connect() async {
        guard phase == .disconnected else { return }
        phase = .connecting
        let session = TrignoSession(host: host.trimmingCharacters(in: .whitespaces))
        do {
            let discovered = try await session.open()
            self.session = session
            apply(discovered)
            phase = .connected
            info("Connected: \(await session.client.version)")
            listen(to: session)
        } catch {
            await session.close()
            phase = .disconnected
            fail(error)
        }
    }

    func disconnect() async {
        if isAcquiring { await stopAcquisition() }
        eventTask?.cancel()
        await session?.close()
        session = nil
        phase = .disconnected
        info("Disconnected")
    }

    func refresh() async {
        guard let session, !isAcquiring else { return }
        do { apply(try await session.refresh()) } catch { fail(error) }
    }

    private func apply(_ info: SystemInfo) {
        system = info
        buffers.configure(system: info, window: plotWindow)
    }

    private func listen(to session: TrignoSession) {
        eventTask?.cancel()
        eventTask = Task { [weak self] in
            for await event in session.events {
                guard let self else { return }
                self.info("Server: \(event)")
                if event.hasPrefix("STOPPED") {
                    await self.stopAcquisition(byTrigger: true)
                } else if event == "DISCONNECTED" {
                    await self.handleDisconnect()
                } else if event.hasSuffix("PAIR COMPLETE") {
                    await self.refresh()
                }
            }
        }
    }

    private func handleDisconnect() async {
        if isAcquiring { await finishRecording() }
        closeLSL()
        session = nil
        phase = .disconnected
        setIdleTimer(disabled: false)
        alert = "Lost connection to the Trigno SDK server."
    }

    // MARK: Acquisition

    func startAcquisition() async {
        guard let session, let system, phase == .connected else { return }
        guard !system.sensors.isEmpty else {
            alert = "No active sensors. Pair them and turn them on, then refresh."
            return
        }
        do {
            buffers.configure(system: system, window: plotWindow)
            // A previous client (e.g. this app killed while recording) may have left the
            // base streaming, which makes the server reject configuration commands.
            _ = try? await session.client.send("STOP", timeout: .seconds(20))
            try await session.armTriggers(start: triggerStart, stop: triggerStop)
            if saveToFile {
                let formatter = DateFormatter()
                formatter.locale = Locale(identifier: "en_US_POSIX")  // ignore the 12/24-hour setting
                formatter.dateFormat = "yyyyMMdd-HHmmss"
                let dir = Self.recordingsDirectory.appending(path: formatter.string(from: .now), directoryHint: .isDirectory)
                recorder = try CSVRecorder(directory: dir, system: system)
                currentRecording = dir
            }
            if lslEnabled {
                // Creating outlets binds sockets; keep it off the main thread.
                let config = lsl
                streamer = await Task.detached { LSLStreamer(system: system, configuration: config) }.value
                lslStreaming = true
                info("LSL: streaming \"\(lsl.name)\" (\(streamer?.emgChannelLabels.count ?? 0) ch)"
                     + (lsl.includeAux ? " + \"\(lsl.name) AUX\"" : ""))
            }
            let buffers = self.buffers
            let recorder = self.recorder
            let streamer = self.streamer
            try await session.start(waitForTrigger: triggerStart) { block in
                buffers.push(block)
                recorder?.write(block)
                streamer?.push(block)
            }
            dataStartedAt = nil
            phase = triggerStart ? .armed : .streaming
            info(triggerStart ? "Armed, waiting for start trigger" : "Streaming")
            setIdleTimer(disabled: true)
            monitor(session)
        } catch {
            fail(error)
            await session.stop()
            discardRecording()
            closeLSL()
        }
    }

    func stopAcquisition(byTrigger: Bool = false) async {
        guard let session, isAcquiring else { return }
        monitorTask?.cancel()
        await session.stop(sendStop: !byTrigger)
        lastSampleCounts = await session.samplesReceived()
        if triggerStart || triggerStop {
            // Leave the base station disarmed for whoever uses the TCU next.
            try? await session.armTriggers(start: false, stop: false)
        }
        await finishRecording()
        closeLSL()
        phase = .connected
        setIdleTimer(disabled: false)
        info(byTrigger ? "Stopped by trigger" : "Stopped")
    }

    private func finishRecording() async {
        guard let recorder else { return }
        do {
            try recorder.finish()
            info("Saved \(recorder.directory.lastPathComponent)")
        } catch {
            fail(error)
        }
        self.recorder = nil
        currentRecording = nil
        reloadRecordings()
    }

    private func closeLSL() {
        streamer = nil  // destroys the outlets; consumers see the stream end
        lslStreaming = false
        lslHasConsumers = false
    }

    private static func loadLSLConfiguration() -> LSLConfiguration {
        guard let data = UserDefaults.standard.data(forKey: "lslConfiguration"),
              let config = try? JSONDecoder().decode(LSLConfiguration.self, from: data) else { return LSLConfiguration() }
        return config
    }

    private func discardRecording() {
        guard let recorder else { return }
        _ = try? recorder.finish()
        try? FileManager.default.removeItem(at: recorder.directory)
        self.recorder = nil
        currentRecording = nil
        reloadRecordings()
    }

    private func monitor(_ session: TrignoSession) {
        monitorTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(100))
                guard let self, self.isAcquiring else { return }
                if self.dataStartedAt == nil, await session.hasData() {
                    self.dataStartedAt = .now
                    if self.phase == .armed {
                        self.phase = .streaming
                        self.info("Triggered")
                    }
                }
                self.lastSampleCounts = await session.samplesReceived()
                self.lslHasConsumers = self.streamer?.hasConsumers ?? false
                if let start = self.dataStartedAt, self.durationLimit > 0,
                   Date.now.timeIntervalSince(start) >= self.durationLimit {
                    await self.stopAcquisition()
                    return
                }
            }
        }
    }

    // MARK: Configuration

    func configureSensor(slot: Int, mode: Int?, range: String?, bandwidth: String?) async {
        guard let session, !isAcquiring else { return }
        do {
            if let mode { info(try await session.client.command("SENSOR \(slot) SETMODE \(mode)")) }
            if let range { try await session.client.command("SENSOR \(slot) SETRANGE \(range)") }
            if let bandwidth { try await session.client.command("SENSOR \(slot) SETBANDWIDTH \(bandwidth)") }
            await refresh()
        } catch {
            fail(error)
        }
    }

    func setBackwardsCompatibility(_ on: Bool) async {
        await simpleCommand("BACKWARDS COMPATIBILITY \(on ? "ON" : "OFF")")
    }

    func setUpsampling(_ on: Bool) async {
        await simpleCommand("UPSAMPLE \(on ? "ON" : "OFF")")
    }

    func pair(slot: Int) async {
        guard let session else { return }
        do {
            info(try await session.client.command("SENSOR \(slot) PAIR"))
            alert = "Turn sensor on and press and hold its button to pair it to slot \(slot)."
        } catch {
            fail(error)
        }
    }

    func sendRaw(_ command: String) async {
        guard let session, !command.isEmpty else { return }
        info("> \(command)")
        do { info(try await session.client.send(command)) } catch { fail(error) }
    }

    private func simpleCommand(_ command: String) async {
        guard let session, !isAcquiring else { return }
        do {
            try await session.client.command(command)
            await refresh()
        } catch {
            fail(error)
        }
    }

    // MARK: Recordings

    func reloadRecordings() {
        let dir = Self.recordingsDirectory
        let urls = (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.creationDateKey])) ?? []
        recordings = urls.compactMap { url in
            let date = (try? url.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? .distantPast
            return Recording(url: url, date: date)
        }
        .sorted { $0.date > $1.date }
    }

    func delete(_ recording: Recording) {
        try? FileManager.default.removeItem(at: recording.url)
        reloadRecordings()
    }

    // MARK: Helpers

    private func info(_ text: String) {
        log.append(LogEntry(text: text.trimmingCharacters(in: .whitespaces), isError: false))
        trimLog()
    }

    private func fail(_ error: Error) {
        let text = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        log.append(LogEntry(text: text, isError: true))
        trimLog()
        alert = text
    }

    private func trimLog() {
        if log.count > 500 { log.removeFirst(log.count - 500) }
    }

    private func setIdleTimer(disabled: Bool) {
        #if os(iOS)
        UIApplication.shared.isIdleTimerDisabled = disabled
        #endif
    }
}
