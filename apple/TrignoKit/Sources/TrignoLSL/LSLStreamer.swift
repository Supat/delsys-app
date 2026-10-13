import CLSL
import Foundation
import TrignoKit

/// How EMG channels are laid out in the LSL stream.
public enum LSLChannelLayout: String, Codable, Sendable, CaseIterable {
    /// All 16 EMG port columns, like the NI-DAQ setup's ai0–7/ai16–23 → 16 channels.
    /// Unused slots stream zeros, so channel n stays sensor slot n.
    case daqCompatible
    /// Only the EMG channels of active sensors, in slot order (same as emg.csv).
    case activeChannels
}

public struct LSLConfiguration: Codable, Sendable, Hashable {
    /// Defaults match the lab's Daq_revised.py outlet so existing consumers find it unchanged.
    public var name = "Delsys"
    public var type = "EMG Signal"
    public var sourceID = "EMG_Signal"
    public var layout = LSLChannelLayout.daqCompatible
    /// Also publish accelerometer/gyro/etc. as a second stream ("<name> AUX").
    public var includeAux = false
    /// Multiplier applied to EMG values. The SDK reports volts at the electrode; the DAQ
    /// sampled the base station's amplified analog outputs, so match that gain here if
    /// consumers depend on the old magnitudes.
    public var emgScale: Float = 1

    public init() {}
}

/// Publishes Trigno data blocks as Lab Streaming Layer outlets.
///
/// Create it when acquisition starts and drop it when acquisition stops,
/// matching the outlet lifetime of the NI-DAQ application it replaces.
public final class LSLStreamer: @unchecked Sendable {
    public let configuration: LSLConfiguration
    public let emgChannelLabels: [String]
    public let auxChannelLabels: [String]

    private let lock = NSLock()
    private let emg: Outlet
    private let aux: Outlet?

    private struct Outlet {
        let handle: lsl_outlet
        let columns: [Int]
        let scale: Float
    }

    public static var libraryVersion: String {
        let v = lsl_library_version()
        return "\(v / 100).\(v % 100)"
    }

    public init(system: SystemInfo, configuration: LSLConfiguration) {
        self.configuration = configuration

        let emgChannels: [(column: Int, label: String, units: String)]
        switch configuration.layout {
        case .daqCompatible:
            let byColumn = Dictionary(uniqueKeysWithValues: system.channels(on: .emg).map { ($0.column, $0) })
            let count = TrignoProtocol.channels(onPort: TrignoProtocol.emgPort)
            emgChannels = (0..<count).map { column in
                if let channel = byColumn[column] { return (column, channel.label, channel.units) }
                return (column, "EMG\(column + 1)_unused", "V")
            }
        case .activeChannels:
            emgChannels = system.channels(on: .emg).map { ($0.column, $0.label, $0.units) }
        }
        emgChannelLabels = emgChannels.map(\.label)
        emg = Self.makeOutlet(
            name: configuration.name, type: configuration.type, sourceID: configuration.sourceID,
            rate: system.emgRate, channels: emgChannels, channelType: "EMG",
            scale: configuration.emgScale, system: system
        )

        let auxChannels = system.channels(on: .aux).map { (column: $0.column, label: $0.label, units: $0.units) }
        if configuration.includeAux && !auxChannels.isEmpty {
            aux = Self.makeOutlet(
                name: "\(configuration.name) AUX", type: "AUX", sourceID: "\(configuration.sourceID)_AUX",
                rate: system.auxRate, channels: auxChannels, channelType: "AUX", scale: 1, system: system
            )
            auxChannelLabels = auxChannels.map(\.label)
        } else {
            aux = nil
            auxChannelLabels = []
        }
    }

    deinit {
        lsl_destroy_outlet(emg.handle)
        if let aux { lsl_destroy_outlet(aux.handle) }
    }

    /// Push one data block; safe to call from the network reader threads.
    public func push(_ block: DataBlock) {
        let outlet: Outlet
        switch block.port {
        case .emg: outlet = emg
        case .aux:
            guard let aux else { return }
            outlet = aux
        }
        let width = outlet.columns.count
        var chunk = [Float](repeating: 0, count: block.rowCount * width)
        for row in 0..<block.rowCount {
            for (i, column) in outlet.columns.enumerated() {
                chunk[row * width + i] = block.value(row: row, column: column) * outlet.scale
            }
        }
        lock.withLock {
            // Timestamped with the LSL clock on arrival, like the DAQ app's push_chunk;
            // earlier samples in the chunk are back-dated by the nominal rate.
            _ = chunk.withUnsafeBufferPointer { lsl_push_chunk_f(outlet.handle, $0.baseAddress, UInt(chunk.count)) }
        }
    }

    /// Whether any inlet (LabRecorder, analysis script, …) is connected to the EMG stream.
    public var hasConsumers: Bool {
        lsl_have_consumers(emg.handle) != 0
    }

    private static func makeOutlet(
        name: String, type: String, sourceID: String, rate: Double,
        channels: [(column: Int, label: String, units: String)], channelType: String,
        scale: Float, system: SystemInfo
    ) -> Outlet {
        let info = lsl_create_streaminfo(name, type, Int32(channels.count), rate, cft_float32, sourceID)
        let desc = lsl_get_desc(info)
        let xmlChannels = lsl_append_child(desc, "channels")
        for channel in channels {
            let node = lsl_append_child(xmlChannels, "channel")
            lsl_append_child_value(node, "label", channel.label)
            lsl_append_child_value(node, "unit", channel.units)
            lsl_append_child_value(node, "type", channelType)
        }
        let acquisition = lsl_append_child(desc, "acquisition")
        lsl_append_child_value(acquisition, "manufacturer", "Delsys")
        lsl_append_child_value(acquisition, "model", "Trigno")
        lsl_append_child_value(acquisition, "transport", "Trigno SDK (TCP)")
        lsl_append_child_value(acquisition, "base_serial", system.baseSerial)
        lsl_append_child_value(acquisition, "scale", String(scale))

        let handle = lsl_create_outlet(info, 0, 360)!
        lsl_destroy_streaminfo(info)
        return Outlet(handle: handle, columns: channels.map(\.column), scale: scale)
    }
}
