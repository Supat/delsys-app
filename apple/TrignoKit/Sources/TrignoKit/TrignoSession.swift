import Foundation

/// High-level acquisition session: discover, configure, start (command or trigger), stream, stop.
public actor TrignoSession {
    public let host: String
    public let client: CommandClient
    public private(set) var system: SystemInfo?
    public private(set) var isRunning = false
    private var streams: [DataStream] = []
    private var finalCounts: [DataPort: Int] = [:]
    private let startIndexBase: Int?

    public init(host: String, startIndexBase: Int? = nil) {
        self.host = host
        self.startIndexBase = startIndexBase
        client = CommandClient(host: host)
    }

    /// Server events: "STOPPED" (stop trigger), "SENSOR n PAIR COMPLETE", "DISCONNECTED".
    public nonisolated var events: AsyncStream<String> { client.events }

    @discardableResult
    public func open() async throws -> SystemInfo {
        try await client.connect()
        return try await refresh()
    }

    @discardableResult
    public func refresh() async throws -> SystemInfo {
        let info = try await querySystem(client, startIndexBase: startIndexBase)
        system = info
        return info
    }

    public func close() async {
        if isRunning { await stop(sendStop: true) }
        await client.close()
    }

    public func armTriggers(start: Bool, stop: Bool) async throws {
        try await client.command("TRIGGER START \(start ? "ON" : "OFF")")
        try await client.command("TRIGGER STOP \(stop ? "ON" : "OFF")")
    }

    /// Open the data ports and begin acquisition.
    ///
    /// With `waitForTrigger`, START is not sent: the base station begins
    /// streaming when it receives the hardware start trigger (section 4).
    public func start(waitForTrigger: Bool = false, sink: @escaping @Sendable (DataBlock) -> Void) async throws {
        guard let system else { throw TrignoError.notConnected }
        let opened = [DataPort.emg, .aux].map { DataStream(host: host, port: $0, bigEndian: system.isBigEndian, sink: sink) }
        finalCounts = [:]
        for stream in opened { try await stream.connect() }
        for stream in opened { await stream.start() }
        streams = opened
        if !waitForTrigger {
            try await client.command("START", timeout: .seconds(20))
        }
        isRunning = true
    }

    /// Stop acquisition. Pass `sendStop: false` when the hardware stop trigger already ended it.
    public func stop(sendStop: Bool = true) async {
        guard isRunning else { return }
        isRunning = false
        if sendStop {
            _ = try? await client.command("STOP", timeout: .seconds(20))
        }
        // Let the readers flush the last frames before closing the sockets.
        try? await Task.sleep(for: .milliseconds(200))
        for stream in streams { await stream.stop() }
        finalCounts = await samplesReceived()
        streams.removeAll()
    }

    public func hasData() async -> Bool {
        for stream in streams where await stream.hasData { return true }
        return false
    }

    /// Samples received per port in the current (or, once stopped, the last) acquisition.
    public func samplesReceived() async -> [DataPort: Int] {
        if streams.isEmpty { return finalCounts }
        var counts: [DataPort: Int] = [:]
        for stream in streams { counts[stream.port] = await stream.samplesReceived }
        return counts
    }
}
