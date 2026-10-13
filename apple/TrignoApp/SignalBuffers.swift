import Foundation
import TrignoKit

/// Thread-safe ring buffers holding the last few seconds of every active channel,
/// written from the network readers and read by the plot at display rate.
final class SignalBuffers: @unchecked Sendable {
    struct Envelope {
        var mins: [Float]
        var maxs: [Float]
        var lo: Float
        var hi: Float
        var isEmpty: Bool { mins.isEmpty }
    }

    private final class Ring {
        let columns: [Int]
        let capacity: Int
        var data: [Float]  // capacity rows x columns.count, row-major
        var writeRow = 0
        var filled = 0

        init(columns: [Int], capacity: Int) {
            self.columns = columns
            self.capacity = capacity
            data = Array(repeating: 0, count: columns.count * capacity)
        }
    }

    private let lock = NSLock()
    private var rings: [DataPort: Ring] = [:]
    private var columnSlot: [DataPort: [Int: Int]] = [:]

    func configure(system: SystemInfo, window: TimeInterval) {
        lock.withLock {
            rings.removeAll()
            columnSlot.removeAll()
            for port in [DataPort.emg, .aux] {
                let columns = system.channels(on: port).map(\.column)
                guard !columns.isEmpty else { continue }
                let capacity = max(1, Int(window * system.rate(of: port)))
                rings[port] = Ring(columns: columns, capacity: capacity)
                columnSlot[port] = Dictionary(uniqueKeysWithValues: columns.enumerated().map { ($1, $0) })
            }
        }
    }

    func push(_ block: DataBlock) {
        lock.withLock {
            guard let ring = rings[block.port] else { return }
            let width = ring.columns.count
            for row in 0..<block.rowCount {
                let base = ring.writeRow * width
                for (i, column) in ring.columns.enumerated() {
                    ring.data[base + i] = block.value(row: row, column: column)
                }
                ring.writeRow = (ring.writeRow + 1) % ring.capacity
                ring.filled = min(ring.filled + 1, ring.capacity)
            }
        }
    }

    /// Min/max per bin over the buffered window, oldest first, for one channel.
    func envelope(port: DataPort, column: Int, bins: Int) -> Envelope {
        lock.withLock {
            guard let ring = rings[port], let slot = columnSlot[port]?[column], ring.filled > 0, bins > 0 else {
                return Envelope(mins: [], maxs: [], lo: 0, hi: 0)
            }
            let width = ring.columns.count
            // Always span the full window so the time axis stays fixed; unfilled bins stay empty.
            let binCount = min(bins, ring.capacity)
            var mins = [Float](repeating: .nan, count: binCount)
            var maxs = [Float](repeating: .nan, count: binCount)
            var lo = Float.infinity, hi = -Float.infinity
            let oldest = (ring.writeRow - ring.filled + ring.capacity) % ring.capacity
            let offset = ring.capacity - ring.filled
            for k in 0..<ring.filled {
                let value = ring.data[((oldest + k) % ring.capacity) * width + slot]
                let bin = (offset + k) * binCount / ring.capacity
                if mins[bin].isNaN || value < mins[bin] { mins[bin] = value }
                if maxs[bin].isNaN || value > maxs[bin] { maxs[bin] = value }
                lo = min(lo, value)
                hi = max(hi, value)
            }
            return Envelope(mins: mins, maxs: maxs, lo: lo, hi: hi)
        }
    }
}
