import Foundation

/// Sends commands/queries on port 50040 and returns the server's replies.
///
/// Replies arrive in the order commands were sent. Unsolicited messages
/// ("STOPPED" after a stop trigger, "SENSOR n PAIR COMPLETE") are published on
/// `events` instead of being taken as a reply.
public actor CommandClient {
    public let host: String
    public let port: UInt16
    public let timeout: Duration
    public private(set) var version = ""

    public nonisolated let events: AsyncStream<String>
    private let eventSink: AsyncStream<String>.Continuation

    private var connection: TCPConnection?
    private var readTask: Task<Void, Never>?
    private var buffer = Data()
    private var unclaimed: [String] = []
    private var waiters: [(id: UUID, continuation: CheckedContinuation<String, Error>)] = []

    public init(host: String, port: UInt16 = TrignoProtocol.commandPort, timeout: Duration = .seconds(10)) {
        self.host = host
        self.port = port
        self.timeout = timeout
        (events, eventSink) = AsyncStream.makeStream(of: String.self)
    }

    @discardableResult
    public func connect() async throws -> String {
        let conn = TCPConnection(host: host, port: port)
        try await conn.start(timeout: timeout)
        connection = conn
        readTask = Task { await self.readLoop(conn) }
        // The server announces its protocol version on connect (section 6.3.1).
        version = try await nextReply(timeout: timeout)
        return version
    }

    public func close() {
        readTask?.cancel()
        readTask = nil
        connection?.cancel()
        connection = nil
        failAll(TrignoError.disconnected)
        eventSink.finish()
    }

    public var isConnected: Bool { connection != nil }

    /// Send one command or query and return its reply text.
    public func send(_ command: String, timeout: Duration? = nil) async throws -> String {
        guard let connection else { throw TrignoError.notConnected }
        try await connection.send(TrignoProtocol.encode(command))
        return try await nextReply(timeout: timeout ?? self.timeout)
    }

    /// Send a command that must not be rejected; throws on INVALID/CANNOT replies.
    @discardableResult
    public func command(_ command: String, timeout: Duration? = nil) async throws -> String {
        let reply = try await send(command, timeout: timeout)
        if reply == TrignoProtocol.replyInvalid || reply == TrignoProtocol.replyCannot {
            throw TrignoError.rejected(command: command, reply: reply)
        }
        return reply
    }

    private func nextReply(timeout: Duration) async throws -> String {
        if !unclaimed.isEmpty { return unclaimed.removeFirst() }
        let id = UUID()
        let timer = Task {
            try? await Task.sleep(for: timeout)
            if !Task.isCancelled { self.expire(id) }
        }
        defer { timer.cancel() }
        return try await withCheckedThrowingContinuation { waiters.append((id, $0)) }
    }

    private func expire(_ id: UUID) {
        guard waiters.contains(where: { $0.id == id }) else { return }
        // A late reply would now be matched to the wrong command, so the session is unusable.
        connection?.cancel()
        connection = nil
        failAll(TrignoError.timeout)
    }

    private func failAll(_ error: Error) {
        let pending = waiters
        waiters.removeAll()
        for waiter in pending { waiter.continuation.resume(throwing: error) }
    }

    private func readLoop(_ conn: TCPConnection) async {
        while !Task.isCancelled {
            let chunk: Data?
            do { chunk = try await conn.receive() } catch { break }
            guard let chunk else { break }
            buffer.append(chunk)
            while let range = buffer.range(of: TrignoProtocol.terminator) {
                let raw = buffer[buffer.startIndex..<range.lowerBound]
                buffer.removeSubrange(buffer.startIndex..<range.upperBound)
                let message = String(decoding: raw, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
                if TrignoProtocol.isAsyncMessage(message) {
                    eventSink.yield(message)
                } else if !waiters.isEmpty {
                    waiters.removeFirst().continuation.resume(returning: message)
                } else {
                    unclaimed.append(message)
                }
            }
        }
        if connection === conn {
            connection = nil
            failAll(TrignoError.disconnected)
            eventSink.yield("DISCONNECTED")
        }
    }
}
