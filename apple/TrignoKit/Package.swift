// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "TrignoKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "TrignoKit", targets: ["TrignoKit"]),
        .library(name: "TrignoLSL", targets: ["TrignoLSL"]),
    ],
    targets: [
        .target(name: "TrignoKit"),

        // Lab Streaming Layer: official liblsl release build (iOS, iOS Simulator, macOS).
        .binaryTarget(
            name: "lsl",
            url: "https://github.com/sccn/liblsl/releases/download/v1.17.7/lsl.xcframework.1.17.zip",
            checksum: "7886450aa2abe8545417f2c7eb11a9e1b224c9e33d716cfccb13a01706d5b593"
        ),
        .target(name: "CLSL", dependencies: ["lsl"]),
        .target(name: "TrignoLSL", dependencies: ["TrignoKit", "CLSL"]),

        .testTarget(name: "TrignoKitTests", dependencies: ["TrignoKit"]),
        .testTarget(name: "TrignoLSLTests", dependencies: ["TrignoLSL", "CLSL"]),
    ]
)
