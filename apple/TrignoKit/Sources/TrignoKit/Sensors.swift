import Foundation

public enum DataPort: String, Codable, Sendable {
    case emg, aux

    public var number: UInt16 { self == .emg ? TrignoProtocol.emgPort : TrignoProtocol.auxPort }
}

public struct ChannelInfo: Codable, Sendable, Hashable, Identifiable {
    /// 1-based channel number on the sensor ("SENSOR n CHANNEL m").
    public var number: Int
    public var port: DataPort
    /// 0-based position in that port's multiplexed frame.
    public var column: Int
    public var rate: Double?
    public var units: String
    public var gain: Double?
    public var label: String

    public var id: String { label }
}

public struct SensorInfo: Codable, Sendable, Hashable, Identifiable {
    public var slot: Int
    public var type: String
    public var serial: String
    public var firmware: String
    public var mode: String
    public var emgChannelCount: Int
    public var auxChannelCount: Int
    /// 0-based, normalised.
    public var startIndex: Int
    public var channels: [ChannelInfo]

    public var id: Int { slot }

    public func channels(on port: DataPort) -> [ChannelInfo] {
        channels.filter { $0.port == port }
    }
}

public struct SystemInfo: Codable, Sendable, Hashable {
    public var version: String
    public var baseSerial: String
    public var baseFirmware: String
    public var frameInterval: Double
    public var maxSamplesEmg: Int
    public var maxSamplesAux: Int
    public var backwardsCompatibility: String
    public var upsampling: String
    public var endianness: String
    public var trigger: String
    public var sensors: [SensorInfo]

    public var emgRate: Double { Double(maxSamplesEmg) / frameInterval }
    public var auxRate: Double { Double(maxSamplesAux) / frameInterval }

    public func rate(of port: DataPort) -> Double { port == .emg ? emgRate : auxRate }

    public var isBigEndian: Bool { endianness.uppercased().contains("BIG") }

    public func channels(on port: DataPort) -> [ChannelInfo] {
        sensors.flatMap { $0.channels(on: port) }
    }
}

func parseDouble(_ text: String) -> Double? {
    text.split(separator: " ").last.flatMap { Double($0) }
}

func parseInt(_ text: String) -> Int {
    parseDouble(text).map { Int($0) } ?? 0
}

func isYes(_ text: String) -> Bool {
    text.trimmingCharacters(in: .whitespaces).uppercased() == "YES"
}

/// Decide whether STARTINDEX replies are 0- or 1-based.
///
/// The manual contradicts itself: section 6.1.3 shows slot 1 -> "0", while the
/// appendix example shows slot 1 -> "1". Resolve it from the replies themselves.
public func detectStartIndexBase(_ rawIndices: [Int: Int]) -> Int {
    guard let min = rawIndices.values.min(), let max = rawIndices.values.max() else { return 0 }
    if min == 0 { return 0 }
    if max >= TrignoProtocol.channels(onPort: TrignoProtocol.emgPort) { return 1 }
    // Without multi-channel sensors the index tracks the slot: slot n -> n-1 or n.
    if rawIndices.allSatisfy({ $0.key == $0.value }) { return 1 }
    if rawIndices.allSatisfy({ $0.key - 1 == $0.value }) { return 0 }
    return 1
}

public func querySystem(_ client: CommandClient, startIndexBase: Int? = nil) async throws -> SystemInfo {
    func q(_ command: String) async throws -> String { try await client.send(command) }

    var info = SystemInfo(
        version: await client.version,
        baseSerial: try await q("BASE SERIAL?"),
        baseFirmware: try await q("BASE FIRMWARE?"),
        frameInterval: parseDouble(try await q("FRAME INTERVAL?")) ?? 0.0135,
        maxSamplesEmg: parseInt(try await q("MAX SAMPLES EMG?")),
        maxSamplesAux: parseInt(try await q("MAX SAMPLES AUX?")),
        backwardsCompatibility: try await q("BACKWARDS COMPATIBILITY?"),
        upsampling: try await q("UPSAMPLING?"),
        endianness: try await q("ENDIANNESS?"),
        trigger: try await q("TRIGGER?"),
        sensors: []
    )

    struct Raw { var type, serial, firmware, mode: String; var emg, aux, start: Int }
    var raw: [Int: Raw] = [:]
    for slot in 1...TrignoProtocol.maxSensors {
        guard isYes(try await q("SENSOR \(slot) PAIRED?")) else { continue }
        // Paired but switched off sensors are not streaming; skip them.
        guard isYes(try await q("SENSOR \(slot) ACTIVE?")) else { continue }
        raw[slot] = Raw(
            type: try await q("SENSOR \(slot) TYPE?"),
            serial: try await q("SENSOR \(slot) SERIAL?"),
            firmware: try await q("SENSOR \(slot) FIRMWARE?"),
            mode: try await q("SENSOR \(slot) MODE?"),
            emg: parseInt(try await q("SENSOR \(slot) EMGCHANNELCOUNT?")),
            aux: parseInt(try await q("SENSOR \(slot) AUXCHANNELCOUNT?")),
            start: parseInt(try await q("SENSOR \(slot) STARTINDEX?"))
        )
    }

    let base = startIndexBase ?? detectStartIndexBase(raw.mapValues(\.start))

    for slot in raw.keys.sorted() {
        let r = raw[slot]!
        let start = r.start - base
        let auxStart = start * TrignoProtocol.auxChannelsPerSlot
        var channels: [ChannelInfo] = []
        for m in stride(from: 1, through: r.emg + r.aux, by: 1) {
            let isEMG = m <= r.emg
            let units = try await q("SENSOR \(slot) CHANNEL \(m) UNITS?")
            let port: DataPort = isEMG ? .emg : .aux
            let index = isEMG ? m : m - r.emg
            channels.append(ChannelInfo(
                number: m,
                port: port,
                column: isEMG ? start + m - 1 : auxStart + (m - r.emg - 1),
                rate: parseDouble(try await q("SENSOR \(slot) CHANNEL \(m) RATE?")),
                units: units,
                gain: parseDouble(try await q("SENSOR \(slot) CHANNEL \(m) GAIN?")),
                label: "S\(slot)_\(port.rawValue)\(index)_\(units.replacingOccurrences(of: " ", with: ""))"
            ))
        }
        info.sensors.append(SensorInfo(
            slot: slot, type: r.type, serial: r.serial, firmware: r.firmware, mode: r.mode,
            emgChannelCount: r.emg, auxChannelCount: r.aux, startIndex: start, channels: channels
        ))
    }
    return info
}
