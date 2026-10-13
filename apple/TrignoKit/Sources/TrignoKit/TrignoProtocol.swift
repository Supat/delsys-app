import Foundation

/// Constants and framing for the Trigno SDK TCP protocol (MAN-025-3-5, SDK v3.5).
public enum TrignoProtocol {
    public static let commandPort: UInt16 = 50040
    public static let legacyEMGPort: UInt16 = 50041
    public static let legacyAUXPort: UInt16 = 50042
    public static let emgPort: UInt16 = 50043
    public static let auxPort: UInt16 = 50044

    /// Number of multiplexed float32 channels carried on each data port (section 6.1).
    public static func channels(onPort port: UInt16) -> Int {
        switch port {
        case emgPort, legacyEMGPort: 16
        case auxPort: 144
        case legacyAUXPort: 48
        default: 0
        }
    }

    public static let maxSensors = 16
    /// AUX buffer position of a sensor's first aux channel = EMG start index * 9 (section 6.1.3).
    public static let auxChannelsPerSlot = 9
    public static let bytesPerSample = 4

    /// Each command ends with <CR><LF>; a packet ends with two consecutive pairs (section 6.2).
    public static let terminator = Data("\r\n\r\n".utf8)

    public static let replyInvalid = "INVALID COMMAND"
    public static let replyCannot = "CANNOT COMPLETE"

    static func encode(_ command: String) -> Data {
        Data(command.trimmingCharacters(in: .whitespacesAndNewlines).utf8) + terminator
    }

    /// Messages the server pushes on the command port without being asked.
    static func isAsyncMessage(_ message: String) -> Bool {
        message.hasPrefix("STOPPED") || message.hasSuffix("PAIR COMPLETE")
    }
}

public enum TrignoError: LocalizedError, Sendable {
    case connectionFailed(String)
    case notConnected
    case timeout
    case disconnected
    case rejected(command: String, reply: String)

    public var errorDescription: String? {
        switch self {
        case .connectionFailed(let detail):
            "Cannot reach the Trigno SDK server (\(detail)). Is the Trigno Control Utility running and reachable from this device?"
        case .notConnected: "Not connected to the Trigno SDK server."
        case .timeout: "Timed out waiting for the Trigno SDK server."
        case .disconnected: "The Trigno SDK server closed the connection."
        case .rejected(let command, let reply): "\(command) → \(reply)"
        }
    }
}
