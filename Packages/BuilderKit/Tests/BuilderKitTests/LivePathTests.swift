import BuilderModel
import BuilderParse
import Foundation
import Testing

/// The Swift half of the live-path conformance gate.
///
/// `scripts/measure_live_path.py` is the reference: it ports `LivePathResolver`'s rule,
/// measures it over a real corpus, and writes three synthetic transcripts under
/// `spec/fixtures/live_path/` whose classification both implementations must agree on.
///
/// `genuine_rewind` holds a rewind — the human resubmitted from `u1`, abandoning
/// `a1 -> r1 -> a2`, an Edit that added 3 lines and was undone — plus one parallel tool
/// batch whose dead-end result `r3` the old single-chain walk also filed as rewound.
/// `harness_forks` holds every harness fork shape the corpus showed — a dead-end result, a
/// dead-end block, a stop hook, a second root — and nothing a human undid. `queued_message`
/// ends in the two parentless, uuid-less `queue-operation` records a queued message writes,
/// which the old rule elected as the live leaf, abandoning the whole session.
@Suite("Live path — reference fixtures")
struct LivePathTests {

    /// Walk up from this file to the repo root, exactly as `BoundaryFixtureTests` does.
    static var fixtureDirectory: URL? {
        var dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            let candidate = dir.appendingPathComponent("spec/fixtures/live_path")
            if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
            dir = dir.deletingLastPathComponent()
        }
        return nil
    }

    /// Parse a fixture through the real parser and resolve it, exactly as ingest does.
    static func resolved(_ name: String) throws -> [NormalizedEvent] {
        let dir = try #require(
            Self.fixtureDirectory,
            "spec/fixtures/live_path missing — run scripts/measure_live_path.py --write-fixture")
        let data = try Data(contentsOf: dir.appendingPathComponent(name + ".jsonl"))
        var events = try BoundaryFixtureTests.parse(jsonl: data)
        let live = LivePathResolver.liveEventIDs(in: events)
        for i in events.indices {
            if let id = events[i].nativeEventID { events[i].onLivePath = live.contains(id) }
        }
        return events
    }

    /// Record uuids (block suffix removed) of every event off the live path.
    static func rewoundRecords(_ events: [NormalizedEvent]) -> Set<String> {
        var out = Set<String>()
        for e in events where e.onLivePath == false {
            guard let id = e.nativeEventID else { continue }
            out.insert(id.split(separator: "#", maxSplits: 1).first.map(String.init) ?? id)
        }
        return out
    }

    @Test func genuineRewindLosesExactlyItsAbandonedBranch() throws {
        let events = try Self.resolved("genuine_rewind")
        #expect(Self.rewoundRecords(events) == ["a1", "r1", "a2"])

        // `r3` — the dead-end result of the parallel batch — is live, so its 5 lines count;
        // the rewound Edit's 3 lines do not. The old rule reported 0 here.
        let lines = TokenAccountant.agentLines(events)
        #expect(lines.added == 5 && lines.removed == 0)

        // Tokens are summed over EVERY branch (the API charged for the abandoned one) and
        // the abandoned share is reported apart: msg_A 110 + msg_B 125. The repeated usage
        // objects on `a3b` and `a4b` are the content-block duplication and count once.
        let ledger = TokenAccountant.ledger(events, harness: .claudeCode)
        #expect(ledger.buckets.input == 720 && ledger.buckets.output == 65)
        #expect(ledger.abandonedBranchTokens == 235)
    }

    @Test func harnessForksLoseNothing() throws {
        let events = try Self.resolved("harness_forks")
        #expect(Self.rewoundRecords(events).isEmpty)

        // Both Edits count (4 + 2), including the dead-end result `rA`; every one of the six
        // tool calls survives, including the stop-hook continuation's commit and the second
        // root's Read; nothing is reported as abandoned.
        let lines = TokenAccountant.agentLines(events)
        #expect(lines.added == 6 && lines.removed == 0)
        #expect(events.filter { $0.kind == .toolUse && $0.onLivePath != false }.count == 6)
        #expect(TokenAccountant.ledger(events, harness: .claudeCode).abandonedBranchTokens == 0)
    }

    @Test func queuedMessageDoesNotAbandonTheSession() throws {
        let events = try Self.resolved("queued_message")
        #expect(Self.rewoundRecords(events).isEmpty)
        #expect(events.filter { $0.kind == .toolUse && $0.onLivePath != false }.count == 1)
        #expect(TokenAccountant.ledger(events, harness: .claudeCode).abandonedBranchTokens == 0)
    }
}
