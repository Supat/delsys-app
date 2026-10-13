import Foundation
import XCTest
@testable import TrignoKit

/// End-to-end tests against the Python mock TCU (`trigno mock-server`) from the repo root.
final class MockServerTests: XCTestCase {
    static let host = "127.0.0.1"
    var server: Process?

    override func setUp() async throws {
        let repo = URL(filePath: #filePath).deletingLastPathComponent().appending(path: "../../../..").standardized
        let python = repo.appending(path: ".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw XCTSkip("Python venv not found at \(python.path); run `python3 -m venv .venv && .venv/bin/pip install -e .`")
        }
        let process = Process()
        process.executableURL = python
        process.arguments = ["-m", "trigno.mock_server", "--sensors", "2"]
        process.currentDirectoryURL = repo
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        server = process
        // Wait for the command port to accept connections.
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

    func testStartIndexBaseDetection() {
        XCTAssertEqual(detectStartIndexBase([1: 0, 2: 1]), 0)
        XCTAssertEqual(detectStartIndexBase([1: 1, 2: 2]), 1)
        XCTAssertEqual(detectStartIndexBase([3: 3]), 1)
        XCTAssertEqual(detectStartIndexBase([3: 2]), 0)
        XCTAssertEqual(detectStartIndexBase([:]), 0)
    }

    func testDiscovery() async throws {
        let session = TrignoSession(host: Self.host)
        let info = try await session.open()
        await session.close()

        XCTAssertEqual(info.emgRate, 2000, accuracy: 0.01)
        XCTAssertEqual(info.auxRate, 148.148, accuracy: 0.01)
        XCTAssertEqual(info.sensors.map(\.slot), [1, 2])
        let s2 = info.sensors[1]
        XCTAssertEqual(s2.startIndex, 1)
        XCTAssertEqual(s2.channels(on: .emg).map(\.column), [1])
        XCTAssertEqual(s2.channels(on: .aux).map(\.column), [9, 10, 11, 12, 13, 14])
        XCTAssertEqual(s2.channels(on: .aux).first?.label, "S2_aux1_g")
    }

    func testRejectedCommand() async throws {
        let client = CommandClient(host: Self.host)
        try await client.connect()
        let reply = try await client.send("NOT A COMMAND")
        XCTAssertEqual(reply, TrignoProtocol.replyInvalid)
        do {
            try await client.command("NOT A COMMAND")
            XCTFail("expected rejection")
        } catch TrignoError.rejected {}
        await client.close()
    }

    func testRecordMatchesMockSignal() async throws {
        let session = TrignoSession(host: Self.host)
        let info = try await session.open()
        let dir = FileManager.default.temporaryDirectory.appending(path: "trigno-test-\(UUID().uuidString)")
        let recorder = try CSVRecorder(directory: dir, system: info)

        try await session.armTriggers(start: false, stop: false)
        try await session.start { recorder.write($0) }
        try await Task.sleep(for: .seconds(1))
        await session.stop()
        try recorder.finish()
        await session.close()

        let lines = try String(contentsOf: dir.appending(path: "emg.csv"), encoding: .utf8)
            .split(separator: "\n")
        XCTAssertEqual(lines.first, "time_s,S1_emg1_V,S2_emg1_V")
        let rows = lines.dropFirst().map { $0.split(separator: ",").map { Double($0)! } }
        XCTAssertGreaterThan(rows.count, 1500)
        // The mock's EMG for slot n is 1e-4 * sin(2π·50n·t) * (1 + sin(2π·0.5·t)).
        for row in rows {
            let t = row[0]
            for slot in 1...2 {
                let expected = 1e-4 * sin(2 * .pi * 50 * Double(slot) * t) * (1 + sin(2 * .pi * 0.5 * t))
                XCTAssertEqual(row[slot], expected, accuracy: 1e-8)
            }
        }

        let aux = try String(contentsOf: dir.appending(path: "aux.csv"), encoding: .utf8).split(separator: "\n")
        XCTAssertEqual(aux.first?.split(separator: ",").count, 13)

        let meta = try JSONSerialization.jsonObject(with: Data(contentsOf: dir.appending(path: "metadata.json"))) as! [String: Any]
        XCTAssertEqual((meta["samples"] as! [String: Int])["emg"], rows.count)
    }

    func testHardwareTriggers() async throws {
        let session = TrignoSession(host: Self.host)
        try await session.open()
        let counter = Counter()
        try await session.armTriggers(start: true, stop: true)
        try await session.start(waitForTrigger: true) { block in
            if block.port == .emg { counter.add(block.rowCount) }
        }

        // The mock fires the start trigger after 1 s and the stop trigger 2 s later.
        let stopped = await withTaskGroup(of: Bool.self) { group in
            group.addTask {
                for await event in session.events where event.hasPrefix("STOPPED") { return true }
                return false
            }
            group.addTask {
                try? await Task.sleep(for: .seconds(6))
                return false
            }
            let first = await group.next()!
            group.cancelAll()
            return first
        }
        XCTAssertTrue(stopped)
        await session.stop(sendStop: false)
        try await session.armTriggers(start: false, stop: false)
        await session.close()
        XCTAssertEqual(Double(counter.value), 4000, accuracy: 1000)
    }
}

final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var total = 0
    func add(_ n: Int) { lock.withLock { total += n } }
    var value: Int { lock.withLock { total } }
}
