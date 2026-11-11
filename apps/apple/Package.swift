// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "KyoseiApple",
    platforms: [.macOS("15.0")],
    products: [
        .executable(name: "KyoseiMac", targets: ["KyoseiMac"]),
        .library(name: "KyoseiClient", targets: ["KyoseiClient"]),
    ],
    targets: [
        // Everything that does not draw: requests to `kyo serve` and the event stream.
        .target(name: "KyoseiClient"),
        .executableTarget(
            name: "KyoseiMac",
            dependencies: ["KyoseiClient"],
            resources: [.copy("Resources/renderer")]
        ),
        .testTarget(name: "KyoseiClientTests", dependencies: ["KyoseiClient"]),
        .testTarget(name: "KyoseiMacTests", dependencies: ["KyoseiMac"]),
    ]
)
