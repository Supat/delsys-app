import Foundation
import Network

/// Minimal async wrapper around an NWConnection TCP stream.
final class TCPConnection: Sendable {
    private let connection: NWConnection
    private let queue: DispatchQueue

    init(host: String, port: UInt16) {
        let tcp = NWProtocolTCP.Options()
        tcp.noDelay = true
        let params = NWParameters(tls: nil, tcp: tcp)
        connection = NWConnection(host: NWEndpoint.Host(host), port: NWEndpoint.Port(rawValue: port)!, using: params)
        queue = DispatchQueue(label: "trigno.tcp.\(port)")
    }

    func start(timeout: Duration = .seconds(10)) async throws {
        let once = ResumeOnce()
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    once.run { cont.resume() }
                case .failed(let error):
                    once.run { cont.resume(throwing: TrignoError.connectionFailed(error.localizedDescription)) }
                case .waiting(let error):
                    // e.g. connection refused: NWConnection would retry forever; fail fast instead.
                    once.run { cont.resume(throwing: TrignoError.connectionFailed(error.localizedDescription)) }
                case .cancelled:
                    once.run { cont.resume(throwing: TrignoError.disconnected) }
                default:
                    break
                }
            }
            connection.start(queue: queue)
            queue.asyncAfter(deadline: .now() + timeout.timeInterval) {
                once.run { cont.resume(throwing: TrignoError.connectionFailed("timed out")) }
            }
        }
        connection.stateUpdateHandler = nil
    }

    func send(_ data: Data) async throws {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            connection.send(content: data, completion: .contentProcessed { error in
                if let error { cont.resume(throwing: error) } else { cont.resume() }
            })
        }
    }

    /// Next chunk of bytes, or nil once the peer has closed the stream.
    func receive() async throws -> Data? {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Data?, Error>) in
            connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { data, _, isComplete, error in
                if let data, !data.isEmpty {
                    cont.resume(returning: data)
                } else if let error {
                    cont.resume(throwing: error)
                } else if isComplete {
                    cont.resume(returning: nil)
                } else {
                    cont.resume(returning: Data())
                }
            }
        }
    }

    func cancel() {
        connection.cancel()
    }
}

/// Guards a continuation that several callbacks race to resume.
final class ResumeOnce: @unchecked Sendable {
    private let lock = NSLock()
    private var done = false

    func run(_ body: () -> Void) {
        lock.lock()
        let first = !done
        done = true
        lock.unlock()
        if first { body() }
    }
}

extension Duration {
    var timeInterval: TimeInterval {
        let (seconds, attoseconds) = components
        return TimeInterval(seconds) + TimeInterval(attoseconds) / 1e18
    }
}
