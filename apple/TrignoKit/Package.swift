// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "TrignoKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "TrignoKit", targets: ["TrignoKit"])],
    targets: [
        .target(name: "TrignoKit"),
        .testTarget(name: "TrignoKitTests", dependencies: ["TrignoKit"]),
    ]
)
