import Foundation

/// Writes streamed data to CSV (one file per port) plus a JSON metadata sidecar.
/// Same layout as the Python `trigno record` output.
public final class CSVRecorder: @unchecked Sendable {
    public let directory: URL
    public let system: SystemInfo
    public let startedAt = Date()

    private let queue = DispatchQueue(label: "trigno.recorder")
    private var handles: [DataPort: FileHandle] = [:]
    private var columns: [DataPort: [Int]] = [:]
    private var counts: [DataPort: Int] = [.emg: 0, .aux: 0]
    private var firstHostTime: [DataPort: Date] = [:]

    public init(directory: URL, system: SystemInfo) throws {
        self.directory = directory
        self.system = system
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        for port in [DataPort.emg, .aux] {
            let channels = system.channels(on: port)
            guard !channels.isEmpty else { continue }
            let url = directory.appending(path: "\(port.rawValue).csv")
            let header = (["time_s"] + channels.map(\.label)).joined(separator: ",") + "\n"
            try Data(header.utf8).write(to: url)
            let handle = try FileHandle(forWritingTo: url)
            handle.seekToEndOfFile()
            handles[port] = handle
            columns[port] = channels.map(\.column)
        }
    }

    public func write(_ block: DataBlock) {
        queue.async { [self] in
            guard let handle = handles[block.port], let cols = columns[block.port] else { return }
            let rate = system.rate(of: block.port)
            if firstHostTime[block.port] == nil { firstHostTime[block.port] = block.hostTime }
            var text = ""
            text.reserveCapacity(block.rowCount * (cols.count + 1) * 14)
            for row in 0..<block.rowCount {
                text += format(Double(block.startIndex + row) / rate)
                for col in cols {
                    text += ","
                    text += format(Double(block.value(row: row, column: col)))
                }
                text += "\n"
            }
            handle.write(Data(text.utf8))
            counts[block.port] = block.startIndex + block.rowCount
        }
    }

    /// Flush, close the CSV files and write metadata.json.
    @discardableResult
    public func finish() throws -> URL {
        try queue.sync {
            for handle in handles.values { try handle.close() }
            handles.removeAll()
            let meta = Metadata(
                recordingStartedUnix: startedAt.timeIntervalSince1970,
                firstDataReceivedUnix: Dictionary(uniqueKeysWithValues: firstHostTime.map { ($0.key.rawValue, $0.value.timeIntervalSince1970) }),
                samples: Dictionary(uniqueKeysWithValues: counts.map { ($0.key.rawValue, $0.value) }),
                note: "time_s = sample_index / port_rate, counted from the first sample after START/trigger.",
                system: system,
                emgRate: system.emgRate,
                auxRate: system.auxRate
            )
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let url = directory.appending(path: "metadata.json")
            try encoder.encode(meta).write(to: url)
            return url
        }
    }

    private struct Metadata: Encodable {
        var recordingStartedUnix: Double
        var firstDataReceivedUnix: [String: Double]
        var samples: [String: Int]
        var note: String
        var system: SystemInfo
        var emgRate: Double
        var auxRate: Double
    }

    private func format(_ value: Double) -> String {
        String(format: "%.9g", value)
    }
}
