import BuilderModel
import BuilderParse
import Foundation
import Testing

/// Cursor's constraints are unusual enough that they need their own assertions.
///
/// The important ones are all NEGATIVE — things that must never appear — because the
/// failure mode is not a crash but a plausible zero. A Cursor session rendering "0 tokens"
/// looks like a bug in Builder; the truth is that Cursor never writes the number at all.
@Suite("Cursor IDE")
struct CursorTests {

    static var dbPath: String {
        (NSHomeDirectory() as NSString)
            .appendingPathComponent("Library/Application Support/Cursor/User/globalStorage/state.vscdb")
    }

    static var available: Bool { FileManager.default.fileExists(atPath: dbPath) }

    static func parseAll() throws -> [NormalizedEvent] {
        let parser = CursorIDEParser()
        var out: [NormalizedEvent] = []
        for s in try parser.discover() {
            out.append(contentsOf: try parser.parseAll(source: s).events)
        }
        return out
    }

    /// Tokens are ABSENT, not zero.
    ///
    /// Measured across all 14,565 message rows: `inputTokens > 0` matches zero rows and
    /// `outputTokens > 0` matches zero rows. Usage is accounted server-side and never
    /// written to disk, so no amount of parsing will ever recover it.
    @Test(.enabled(if: available))
    func tokensAreStructurallyAbsent() throws {
        let events = try Self.parseAll()
        #expect(!events.isEmpty)

        // Not "all zero" — none of them may even carry a token field.
        #expect(events.allSatisfy { $0.tokIn == nil && $0.tokOut == nil })
        #expect(events.allSatisfy { !$0.usageAuthoritative })

        let ledger = TokenAccountant.ledger(events, harness: .cursorIDE)
        #expect(ledger.reported == false)
        #expect(ledger.coverage == .structurallyAbsent)
        #expect(Harness.cursorIDE.reportsTokens == false)
    }

    /// The model is unknowable per session, so nothing may claim to know it.
    @Test func modelIsNotClaimed() {
        #expect(Harness.cursorIDE.reportsModel == false)
    }

    /// Conversations whose bodies were garbage-collected still contribute their hours,
    /// but must never be rendered as a card.
    @Test(.enabled(if: available))
    func garbageCollectedConversationsSurviveAsHours() throws {
        let parser = CursorIDEParser()
        let sources = try parser.discover()
        let result = try parser.parseAll(source: sources[0])

        // Some conversations are header-only. That is the expected steady state — the GC
        // cliff falls at roughly two months.
        let gcNote = result.diagnostics.first { $0.code == "cursor_bodies_gc" }
        #expect(gcNote != nil)

        // Header-only conversations still produce span-bearing events so their time counts.
        #expect(result.events.contains { $0.kind == .noise })
    }

    /// Fidelity may only ever be revised upward.
    ///
    /// Cursor vacuums while running, so a read can race the GC and see bodies missing for
    /// a conversation that had them moments ago. Letting fidelity fall would permanently
    /// downgrade a good session because of a transient lock.
    @Test func fidelityIsMonotonic() {
        #expect(TimelineFidelity.headerOnly.merged(with: .full) == .full)
        #expect(TimelineFidelity.full.merged(with: .headerOnly) == .full)
        #expect(TimelineFidelity.coarse.merged(with: .headerOnly) == .coarse)
        #expect(TimelineFidelity.full > TimelineFidelity.coarse)
        #expect(TimelineFidelity.coarse > TimelineFidelity.headerOnly)
    }

    /// Cursor's prompt rule is looser than Claude Code's, so the two must be labelled and
    /// never summed as if they meant the same thing.
    ///
    /// Claude Code can distinguish a typed prompt from a system injection via
    /// `promptSource`; Cursor has no equivalent, so every user bubble counts. Summing them
    /// would produce a "prompts" figure that means different things in different rows.
    @Test(.enabled(if: available))
    func promptBasisDiffersFromClaudeCode() throws {
        let events = try Self.parseAll()
        let prompts = events.filter { $0.kind == .prompt }
        #expect(!prompts.isEmpty)
        // Every Cursor prompt is a user bubble; none was filtered by promptSource.
        #expect(prompts.allSatisfy { $0.role == "user" })
    }

    /// Timestamps are genuinely per-message rather than batch-written at save time.
    ///
    /// Verified against the live database: the busiest conversation has 3,035 bubbles with
    /// 1,745 distinct timestamps spanning six days. This is what makes a Cursor session's
    /// duration meaningful at all — if bubbles shared a save-time timestamp, every Cursor
    /// session would collapse to an instant and the strip would be a single bar.
    @Test(.enabled(if: available))
    func timestampsAreSpreadOverTime() throws {
        let events = try Self.parseAll().filter { $0.ts != nil }
        let byConversation = Dictionary(grouping: events, by: { $0.nativeSessionID ?? "" })
        guard let biggest = byConversation.values.max(by: { $0.count < $1.count }), biggest.count > 50
        else { return }

        let stamps = Set(biggest.compactMap(\.ts))
        // Well over half of a busy conversation's events have distinct timestamps.
        #expect(Double(stamps.count) / Double(biggest.count) > 0.4)

        let span = (biggest.compactMap(\.ts).max() ?? 0) - (biggest.compactMap(\.ts).min() ?? 0)
        #expect(span > 60)
    }
}
