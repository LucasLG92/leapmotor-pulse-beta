// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "PulseCore",
    platforms: [.iOS("17.4")],
    products: [
        .library(name: "PulseCore", targets: ["PulseCore"]),
    ],
    targets: [
        .target(name: "PulseCore"),
        .testTarget(name: "PulseCoreTests", dependencies: ["PulseCore"]),
    ],
    swiftLanguageModes: [.v5]
)
