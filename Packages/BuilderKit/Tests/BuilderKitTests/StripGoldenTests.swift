import BuilderModel
import Foundation
import Testing

/// The Swift half of the cross-language gate.
///
/// `mobile/__tests__/strip.test.ts` reads these same JSON files and asserts the same
/// expected values. Neither suite alone proves anything about the other; together they
/// mean the Mac and the phone cannot silently disagree about what a session looked like.
///
/// The failure this prevents is specific and nasty: two independent designs of this
/// format had classes 1 and 2 swapped. A renderer fed the other spec's ordinals paints
/// every agent run in the prompt colour, producing a plausible strip showing a human who
/// typed for three hours. It does not crash, it does not look empty, and it survives code
/// review — only a decoded-value assertion catches it.
@Suite("Strip golden fixtures")
struct StripGoldenTests {

    struct Fixture: Decodable {
        let name: String
        let specVersion: Int
        let spanMs: Int
        let colsB64: String
        let marks: [[Int]]
        let expectedClassPerColumn: [Int]
        let expectedDensityPerColumn: [Int]
        let expectedResampled: [String: [Int]]

        enum CodingKeys: String, CodingKey {
            case name
            case specVersion = "spec_version"
            case spanMs = "span_ms"
            case colsB64 = "cols_b64"
            case marks
            case expectedClassPerColumn = "expected_class_per_column"
            case expectedDensityPerColumn = "expected_density_per_column"
            case expectedResampled = "expected_resampled"
        }
    }

    /// Walk up from this file to the repo root. The fixtures are shared with the
    /// TypeScript suite, so they live in `spec/`, not in either target's resources —
    /// duplicating them would defeat the entire point.
    static var fixtureDirectory: URL? {
        var dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            let candidate = dir.appendingPathComponent("spec/fixtures")
            if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
            dir = dir.deletingLastPathComponent()
        }
        return nil
    }

    static func load() throws -> [Fixture] {
        guard let dir = fixtureDirectory else { return [] }
        let files = try FileManager.default.contentsOfDirectory(atPath: dir.path)
            .filter { $0.hasPrefix("strip_") && $0.hasSuffix(".json") }
            .sorted()
        return try files.map { name in
            let data = try Data(contentsOf: dir.appendingPathComponent(name))
            return try JSONDecoder().decode(Fixture.self, from: data)
        }
    }

    @Test func fixturesExist() throws {
        let fixtures = try Self.load()
        #expect(!fixtures.isEmpty, "no golden fixtures found — run `make gen`")
    }

    @Test func decodesToExpectedClassAndDensity() throws {
        for f in try Self.load() {
            guard let bytes = Data(base64Encoded: f.colsB64) else {
                Issue.record("fixture \(f.name) has invalid base64")
                continue
            }
            #expect(bytes.count == StripSpec.columns, "fixture \(f.name) wrong length")
            #expect(f.specVersion == StripSpec.version)

            let decoded = bytes.map { StripSpec.unpack($0) }
            #expect(
                decoded.map { Int($0.klass.rawValue) } == f.expectedClassPerColumn,
                "class ordinals disagree in \(f.name)")
            #expect(
                decoded.map { Int($0.density) } == f.expectedDensityPerColumn,
                "density disagrees in \(f.name)")
        }
    }

    @Test func resamplesIdenticallyToTypeScript() throws {
        for f in try Self.load() {
            guard let bytes = Data(base64Encoded: f.colsB64) else { continue }
            let cols = [UInt8](bytes)
            for (widthKey, expected) in f.expectedResampled {
                guard let width = Int(widthKey) else { continue }
                let actual = StripSpec.resample(cols, to: width).map(Int.init)
                #expect(
                    actual == expected,
                    "resample to \(width) disagrees in \(f.name)")
            }
        }
    }

    @Test func reservedBitsAreAlwaysZero() throws {
        for f in try Self.load() {
            guard let bytes = Data(base64Encoded: f.colsB64) else { continue }
            // Bits 4-7 are the only expansion room the format has. If a fixture ever
            // carries one, the generator has started writing a field nothing decodes.
            #expect(bytes.allSatisfy { $0 >> 4 == 0 }, "reserved bits set in \(f.name)")
        }
    }

    @Test func markKindsAreInRange() throws {
        for f in try Self.load() {
            for m in f.marks {
                #expect(m.count == 2)
                #expect(StripMarkKind(rawValue: UInt8(m[1])) != nil, "unknown mark kind in \(f.name)")
            }
        }
    }
}
