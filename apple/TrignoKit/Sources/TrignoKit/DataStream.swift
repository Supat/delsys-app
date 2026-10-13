import Foundation

/// A block of demultiplexed samples from one data port.
public struct DataBlock: Sendable {
    public let port: DataPort
    public let channelCount: Int
    /// Row-major: samples[row * channelCount + channel].
    public let samples: [Float]
    /// Index of the first row since acquisition started.
    public let startIndex: Int
    public let hostTime: Date

    public var rowCount: Int { samples.count / channelCount }

    public func value(row: Int, column: Int) -> Float {
        samples[row * channelCount + column]
    }
}

/// Reads one multiplexed float32 data port (section 6.1.1).
///
/// Bytes are only consumed in whole multiples of channels * 4 so the channel
/// alignment is never lost, as the manual requires.
public actor DataStream {
    public let port: DataPort
    public let channelCount: Int
    public private(set) var samplesReceived = 0
    public private(set) var firstDataTime: Date?

    private let host: String
    private let bigEndian: Bool
    private let sink: @Sendable (DataBlock) -> Void
    private var connection: TCPConnection?
    private var task: Task<Void, Never>?

    public init(host: String, port: DataPort, bigEndian: Bool, sink: @escaping @Sendable (DataBlock) -> Void) {
        self.host = host
        self.port = port
        self.channelCount = TrignoProtocol.channels(onPort: port.number)
        self.bigEndian = bigEndian
        self.sink = sink
    }

    public func connect() async throws {
        let conn = TCPConnection(host: host, port: port.number)
        try await conn.start()
        connection = conn
    }

    public func start() {
        guard let connection else { return }
        task = Task { await self.run(connection) }
    }

    public func stop() {
        task?.cancel()
        task = nil
        connection?.cancel()
        connection = nil
    }

    public var hasData: Bool { firstDataTime != nil }

    private func run(_ conn: TCPConnection) async {
        let frameBytes = channelCount * TrignoProtocol.bytesPerSample
        var buffer = Data()
        while !Task.isCancelled {
            let chunk: Data?
            do { chunk = try await conn.receive() } catch { break }
            guard let chunk else { break }
            let now = Date()
            buffer.append(chunk)
            let usable = buffer.count - buffer.count % frameBytes
            guard usable > 0 else { continue }

            let values = decodeFloats(buffer.prefix(usable))
            buffer.removeFirst(usable)
            if firstDataTime == nil { firstDataTime = now }
            let block = DataBlock(port: port, channelCount: channelCount, samples: values,
                                  startIndex: samplesReceived, hostTime: now)
            samplesReceived += block.rowCount
            sink(block)
        }
    }

    private func decodeFloats(_ data: Data) -> [Float] {
        let count = data.count / TrignoProtocol.bytesPerSample
        return data.withUnsafeBytes { raw in
            (0..<count).map { i in
                let bits = raw.loadUnaligned(fromByteOffset: i * 4, as: UInt32.self)
                return Float(bitPattern: bigEndian ? UInt32(bigEndian: bits) : UInt32(littleEndian: bits))
            }
        }
    }
}
