import XCTest
@testable import TrignoKit

final class LocalNetworkTests: XCTestCase {
    func testAddressesAreWellFormedAndNotLoopback() {
        for item in LocalNetwork.ipv4Addresses() {
            XCTAssertTrue(item.interface.hasPrefix("en"), item.interface)
            let octets = item.address.split(separator: ".")
            XCTAssertEqual(octets.count, 4, item.address)
            XCTAssertTrue(octets.allSatisfy { UInt8($0) != nil }, item.address)
            XCTAssertFalse(item.address.hasPrefix("127."), item.address)
        }
    }

    func testKnownPeersConfig() {
        let addresses = [LocalIPv4Address(interface: "en0", address: "192.168.11.23"),
                         LocalIPv4Address(interface: "en2", address: "10.0.0.5")]
        XCTAssertEqual(LocalNetwork.knownPeersConfig(for: addresses),
                       "[lab]\nKnownPeers = {192.168.11.23, 10.0.0.5}")
    }
}
