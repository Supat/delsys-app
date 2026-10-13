import CLSL
import Foundation
import XCTest
import TrignoKit
@testable import TrignoLSL

/// Streams mock-server data through LSL and reads it back with a real LSL inlet.
final class LSLStreamerTests: XCTestCase {
    static let host = "127.0.0.1"
    var server: Process?

    override func setUp() async throws {
        let repo = URL(filePath: #filePath).deletingLastPathComponent().appending(path: "../../../..").standardized
        let python = repo.appending(path: ".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw XCTSkip("Python venv not found at \(python.path)")
        }
        let process = Process()
        process.executableURL = python
        process.arguments = ["-m", "trigno.mock_server", "--sensors", "2"]
        process.currentDirectoryURL = repo
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        server = process
        for _ in 0..<50 {
            let probe = CommandClient(host: Self.host, timeout: .milliseconds(300))
            if (try? await probe.connect()) != nil {
                await probe.close()
                return
            }
            try await Task.sleep(for: .milliseconds(100))
        }
        XCTFail("Mock server did not start")
    }

    override func tearDown() async throws {
        server?.terminate()
        server?.waitUntilExit()
    }

    func testDefaultsMatchDaqApplication() {
        let config = LSLConfiguration()
        XCTAssertEqual(config.name, "Delsys")
        XCTAssertEqual(config.type, "EMG Signal")
        XCTAssertEqual(config.sourceID, "EMG_Signal")
        XCTAssertEqual(config.layout, .daqCompatible)
    }

    func testDaqCompatibleStreamRoundTrip() async throws {
        let session = TrignoSession(host: Self.host)
        let system = try await session.open()

        var config = LSLConfiguration()
        // Unique identity so the test never collides with a real stream on the network.
        let tag = UUID().uuidString.prefix(8)
        config.name = "TrignoTest-\(tag)"
        config.sourceID = "TrignoTest-\(tag)"
        config.includeAux = true
        let streamer = LSLStreamer(system: system, configuration: config)
        XCTAssertEqual(streamer.emgChannelLabels.count, 16)
        XCTAssertEqual(Array(streamer.emgChannelLabels.prefix(3)), ["S1_emg1_V", "S2_emg1_V", "EMG3_unused"])
        XCTAssertEqual(streamer.auxChannelLabels.count, 12)

        let pushed = Recorder()
        try await session.start { block in
            streamer.push(block)
            pushed.add(block)
        }

        // Resolve and read the EMG stream like LabRecorder would.
        let info = try resolve(sourceID: config.sourceID)
        XCTAssertEqual(String(cString: lsl_get_name(info)), config.name)
        XCTAssertEqual(String(cString: lsl_get_type(info)), "EMG Signal")
        XCTAssertEqual(lsl_get_channel_count(info), 16)
        XCTAssertEqual(lsl_get_nominal_srate(info), system.emgRate, accuracy: 0.01)

        let inlet = lsl_create_inlet(info, 360, 0, 1)!
        defer { lsl_destroy_inlet(inlet) }
        var ec: Int32 = 0
        lsl_open_stream(inlet, 5, &ec)
        XCTAssertEqual(ec, 0)
        let full = lsl_get_fullinfo(inlet, 5, &ec)!
        let xml = String(cString: lsl_get_xml(full))
        XCTAssertTrue(xml.contains("<label>S2_emg1_V</label>"))
        XCTAssertTrue(xml.contains("<manufacturer>Delsys</manufacturer>"))
        lsl_destroy_streaminfo(full)

        var received: [Float] = []
        let deadline = Date.now.addingTimeInterval(1.5)
        var buffer = [Float](repeating: 0, count: 16 * 4096)
        var stamps = [Double](repeating: 0, count: 4096)
        while Date.now < deadline {
            let n = lsl_pull_chunk_f(inlet, &buffer, &stamps, UInt(buffer.count), UInt(stamps.count), 0.2, &ec)
            received.append(contentsOf: buffer.prefix(Int(n)))
        }
        await session.stop()
        await session.close()
        lsl_destroy_streaminfo(info)

        let rows = received.count / 16
        XCTAssertGreaterThan(rows, 1500, "expected ~1.5 s of 2 kHz EMG")

        // The received rows must be an exact, gap-free run of what was pushed.
        let sent = pushed.emgRows()
        let first = Array(received[0..<16])
        guard let start = sent.firstIndex(of: first) else {
            return XCTFail("first received sample not found among pushed samples")
        }
        for r in 0..<rows {
            XCTAssertEqual(Array(received[(r * 16)..<(r * 16 + 16)]), sent[start + r], "row \(r)")
            if r > 5 { break }
        }
        let receivedRows = (0..<rows).map { Array(received[($0 * 16)..<($0 * 16 + 16)]) }
        XCTAssertEqual(receivedRows, Array(sent[start..<(start + rows)]))
        // Unused slots stream zeros; active slots carry signal.
        XCTAssertTrue(receivedRows.allSatisfy { $0[2] == 0 && $0[15] == 0 })
        XCTAssertTrue(receivedRows.contains { $0[0] != 0 })
    }

    func testActiveChannelLayoutAndScale() async throws {
        let session = TrignoSession(host: Self.host)
        let system = try await session.open()
        await session.close()

        var config = LSLConfiguration()
        config.name = "TrignoTest-\(UUID().uuidString.prefix(8))"
        config.sourceID = config.name
        config.layout = .activeChannels
        config.emgScale = 1000
        let streamer = LSLStreamer(system: system, configuration: config)
        XCTAssertEqual(streamer.emgChannelLabels, ["S1_emg1_V", "S2_emg1_V"])
        XCTAssertEqual(streamer.auxChannelLabels, [])

        let info = try resolve(sourceID: config.sourceID)
        XCTAssertEqual(lsl_get_channel_count(info), 2)
        let inlet = lsl_create_inlet(info, 360, 0, 1)!
        defer { lsl_destroy_inlet(inlet) }
        var ec: Int32 = 0
        lsl_open_stream(inlet, 5, &ec)
        lsl_destroy_streaminfo(info)
        // LSL only delivers samples pushed after the outlet has registered the inlet.
        for _ in 0..<50 where !streamer.hasConsumers {
            try await Task.sleep(for: .milliseconds(100))
        }
        XCTAssertTrue(streamer.hasConsumers)

        // A synthetic EMG frame: columns 0 and 1 carry the two sensors.
        var frame = [Float](repeating: 0, count: 16)
        frame[0] = 0.001
        frame[1] = -0.002
        streamer.push(DataBlock(port: .emg, channelCount: 16, samples: frame, startIndex: 0, hostTime: .now))
        var sample = [Float](repeating: 0, count: 2)
        let stamp = lsl_pull_sample_f(inlet, &sample, 2, 5, &ec)
        XCTAssertGreaterThan(stamp, 0)
        XCTAssertEqual(sample[0], 1, accuracy: 1e-6)
        XCTAssertEqual(sample[1], -2, accuracy: 1e-6)
    }

    private func resolve(sourceID: String) throws -> lsl_streaminfo {
        var results = [lsl_streaminfo?](repeating: nil, count: 1)
        let found = lsl_resolve_byprop(&results, 1, "source_id", sourceID, 1, 5)
        guard found > 0, let info = results[0] else {
            throw XCTSkip("LSL stream not discoverable on this machine (multicast blocked?)")
        }
        return info
    }
}

final class Recorder: @unchecked Sendable {
    private let lock = NSLock()
    private var emg: [[Float]] = []

    func add(_ block: DataBlock) {
        guard block.port == .emg else { return }
        let rows = (0..<block.rowCount).map { r in (0..<16).map { block.value(row: r, column: $0) } }
        lock.withLock { emg.append(contentsOf: rows) }
    }

    func emgRows() -> [[Float]] { lock.withLock { emg } }
}
