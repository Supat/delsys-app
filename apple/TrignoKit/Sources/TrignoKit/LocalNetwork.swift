import Foundation

/// An IPv4 address of one of this device's Wi-Fi or Ethernet interfaces.
///
/// Used to tell LSL receivers where to look for this device's streams when
/// multicast discovery is unavailable (iPadOS), via `KnownPeers` in `lsl_api.cfg`.
public struct LocalIPv4Address: Hashable, Sendable, Identifiable {
    /// BSD interface name, for example `en0`.
    public let interface: String
    /// Dotted-quad address, for example `192.168.11.23`.
    public let address: String

    public var id: String { "\(interface) \(address)" }

    public init(interface: String, address: String) {
        self.interface = interface
        self.address = address
    }

    /// A short user-facing name for the interface.
    public var displayName: String {
        #if os(iOS)
        // On iPadOS en0 is always Wi-Fi; USB-C Ethernet adapters come up as en1, en2, …
        return interface == "en0" ? "Wi-Fi" : "Ethernet (\(interface))"
        #else
        return interface
        #endif
    }
}

public enum LocalNetwork {
    /// IPv4 addresses of the active Wi-Fi/Ethernet (`en*`) interfaces, `en0` first.
    ///
    /// Loopback, cellular (`pdp_ip*`), VPN (`utun*`), AWDL and bridge interfaces are
    /// skipped, because LSL receivers on the lab network cannot reach those addresses.
    public static func ipv4Addresses() -> [LocalIPv4Address] {
        var list: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&list) == 0, let first = list else { return [] }
        defer { freeifaddrs(list) }

        var found: [LocalIPv4Address] = []
        for entry in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let ifa = entry.pointee
            guard let sockaddr = ifa.ifa_addr, sockaddr.pointee.sa_family == sa_family_t(AF_INET) else { continue }
            let flags = Int32(bitPattern: ifa.ifa_flags)
            guard flags & IFF_UP != 0, flags & IFF_LOOPBACK == 0 else { continue }
            let name = String(cString: ifa.ifa_name)
            guard name.hasPrefix("en") else { continue }

            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            let status = getnameinfo(sockaddr, socklen_t(sockaddr.pointee.sa_len),
                                     &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST)
            guard status == 0 else { continue }
            let address = host.withUnsafeBufferPointer { String(cString: $0.baseAddress!) }
            let item = LocalIPv4Address(interface: name, address: address)
            if !found.contains(item) { found.append(item) }
        }
        return found.sorted { a, b in
            if a.interface != b.interface {
                return a.interface.localizedStandardCompare(b.interface) == .orderedAscending
            }
            return a.address < b.address
        }
    }

    /// The `lsl_api.cfg` lines that make liblsl on another computer query these
    /// addresses directly (unicast), bypassing multicast discovery.
    public static func knownPeersConfig(for addresses: [LocalIPv4Address]) -> String {
        let peers = addresses.map(\.address).joined(separator: ", ")
        return "[lab]\nKnownPeers = {\(peers)}"
    }
}
