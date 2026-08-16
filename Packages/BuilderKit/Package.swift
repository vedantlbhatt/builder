// swift-tools-version: 6.0
//
// BuilderKit — the whole engine. The agent is the `builder` executable target in here,
// which is the single highest-leverage structural decision in this project: `swift build`,
// `swift test` and `swift run` need no Xcode project, no signing, no provisioning profile
// and no simulator, so every work package up to the macOS app has zero signing surface
// and CI is one ~60-second job.
//
// ZERO EXTERNAL DEPENDENCIES, deliberately. This package is the open-source artifact that
// backs the privacy claim; every dependency added here is another thing a skeptic has to
// audit before believing that prompts never leave the machine. SQLite comes from the SDK.
//
// Swift 5 language mode for now. The migration to .v6 with full strict concurrency is
// WP-13, on purpose: get the parser measurably correct against the ground-truth corpus
// first, then fight the concurrency checker. Doing it in the other order means debugging
// two unfamiliar things at once.

import PackageDescription

let swift5 = SwiftSetting.swiftLanguageMode(.v5)

let package = Package(
    name: "BuilderKit",
    platforms: [.macOS(.v15)],
    products: [
        .library(name: "BuilderKit", targets: [
            "BuilderModel", "BuilderSQLite", "BuilderSchema", "BuilderParse",
            "BuilderGit", "BuilderIngest", "BuilderAnalysis", "BuilderStore",
            "BuilderUI", "BuilderSync",
        ]),
        .executable(name: "builder", targets: ["builder"]),
    ],
    targets: [
        // Value types, the generated spec constants, and Tuning. No I/O, no dependencies.
        .target(name: "BuilderModel", swiftSettings: [swift5]),

        // ~300 lines over the system SQLite3. The only place a database handle is opened.
        .target(name: "BuilderSQLite", swiftSettings: [swift5]),

        // Tier A migrations and the Tier B rebuild, with the .sql files as resources.
        .target(
            name: "BuilderSchema",
            dependencies: ["BuilderSQLite"],
            resources: [.process("Resources")],
            swiftSettings: [swift5]
        ),

        // One parser per harness behind HarnessParser. Every measured trap lives here.
        .target(
            name: "BuilderParse",
            dependencies: ["BuilderModel", "BuilderSQLite", "BuilderSchema"],
            swiftSettings: [swift5]
        ),

        // git invoked via Process with argument arrays. Repo identity and hashing.
        .target(name: "BuilderGit", dependencies: ["BuilderModel"], swiftSettings: [swift5]),

        // discover -> watermark -> parse -> write -> enrich -> derive, plus the
        // session lifecycle state machine that decides a session has ENDED.
        .target(
            name: "BuilderIngest",
            dependencies: ["BuilderParse", "BuilderGit", "BuilderSchema"],
            swiftSettings: [swift5]
        ),

        // Records, day rollups, project arcs, human-vs-agent trend. Pure SQL over cache.sqlite.
        .target(
            name: "BuilderAnalysis",
            dependencies: ["BuilderModel", "BuilderSQLite"],
            swiftSettings: [swift5]
        ),

        // The read side. Shared by the CLI and the macOS app.
        .target(
            name: "BuilderStore",
            dependencies: ["BuilderModel", "BuilderSQLite"],
            swiftSettings: [swift5]
        ),

        // SwiftUI renderers for macOS. The iOS client is Expo/React Native and renders
        // the same strip from the same spec file — see mobile/src/strip/.
        .target(
            name: "BuilderUI",
            dependencies: ["BuilderModel", "BuilderStore"],
            swiftSettings: [swift5]
        ),

        // The wire payload. Hand-written encode(to:) over the GENERATED UploadField enum,
        // and no synthesized Codable anywhere in this target.
        .target(name: "BuilderSync", dependencies: ["BuilderModel"], swiftSettings: [swift5]),

        .executableTarget(
            name: "builder",
            dependencies: [
                "BuilderIngest", "BuilderAnalysis", "BuilderStore", "BuilderSync", "BuilderUI",
            ],
            swiftSettings: [swift5]
        ),

        .testTarget(
            name: "BuilderKitTests",
            dependencies: [
                "BuilderModel", "BuilderSQLite", "BuilderSchema", "BuilderParse",
                "BuilderGit", "BuilderIngest", "BuilderAnalysis", "BuilderStore",
                "BuilderUI", "BuilderSync",
            ],
            resources: [.copy("Fixtures")],
            swiftSettings: [swift5]
        ),
    ]
)
