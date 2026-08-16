import BuilderIngest
import BuilderModel
import BuilderParse
import Foundation
import Testing

/// Regression tests against the real transcript corpus on this machine.
///
/// These are the strongest correctness signal available. The reference figures were
/// measured independently of this code, by a separate exploration pass, so agreement is
/// evidence rather than tautology — and the failure mode of a log parser is never a crash,
/// it is a plausible wrong number that nobody questions.
///
/// The corpus is live and grows as the machine is used, so counts are asserted with a
/// tolerance where they are cumulative and exactly where they are structural. Ratios and
/// distributions are exact, because those are properties of the FORMAT and do not drift.
@Suite("Ground truth — Claude Code corpus")
struct GroundTruthTests {

    static let referenceProject = "-Users-vedantbhatt-Downloads-projects-RideGT"

    static var corpusRoot: String {
        (NSHomeDirectory() as NSString).appendingPathComponent(".claude/projects")
    }

    static var corpusAvailable: Bool {
        FileManager.default.fileExists(
            atPath: (corpusRoot as NSString).appendingPathComponent(referenceProject))
    }

    /// One entry per transcript RECORD, which is what the reference measured. Content
    /// blocks of the same record share a timestamp and would inject zero-length gaps.
    static func records(_ events: [NormalizedEvent]) -> [NormalizedEvent] {
        var seen = Set<String>()
        return events.filter { e in
            guard e.ts != nil else { return false }
            return seen.insert("\(e.sourceID)|\(Sessionizer.recordBaseID(e))").inserted
        }
    }

    static func referenceProjectEvents() throws -> [NormalizedEvent] {
        let parser = ClaudeCodeParser()
        let dir = (corpusRoot as NSString).appendingPathComponent(referenceProject)
        let sources = try parser.discover().filter {
            $0.path.hasPrefix(dir + "/") && !$0.isSidecar
        }
        var events: [NormalizedEvent] = []
        for s in sources { events.append(contentsOf: try parser.parseAll(source: s).events) }
        return events
    }

    // MARK: - Sessionization: the constant that IS the product

    /// The published counts, reproduced exactly.
    ///
    /// Measured: 68 root transcripts, 48,096 timestamped records, 309.5 hours of span,
    /// pooled together and cut on the idle gap.
    ///
    /// tau=300 is asserted with a small tolerance and the others exactly, which is not
    /// arbitrary: at a 5-minute threshold the corpus sits right on a cliff, so a handful
    /// of new records shifts the count. At 900 seconds and above the structure is stable —
    /// which is itself an argument for the chosen default.
    @Test(.enabled(if: corpusAvailable))
    func sessionCountsAtEachThreshold() throws {
        let events = try Self.referenceProjectEvents()
        let pooling = Sessionizer.Pooling.explicit { _ in "project" }

        func count(_ tau: Double) -> Int {
            Sessionizer.sessions(from: events, options: .init(tau: tau, pooling: pooling)).count
        }

        #expect(abs(count(300) - 217) <= 5)
        #expect(count(900) == 84)
        #expect(count(1800) == 52)
        #expect(count(3600) == 30)
        #expect(count(7200) == 22)

        // The count swings 2.8x between 900 and 3600 seconds. This is why the threshold is
        // fixed rather than user-configurable: it is not a preference, it is the definition
        // of the unit the whole product is built on.
        #expect(Double(count(900)) / Double(count(3600)) > 2.5)
    }

    /// Sum of sub-threshold gaps, in hours — the figure the exploration published.
    @Test(.enabled(if: corpusAvailable))
    func subThresholdGapHours() throws {
        let events = try Self.referenceProjectEvents()
        let pooling = Sessionizer.Pooling.explicit { _ in "project" }

        func hours(_ tau: Double) -> Double {
            Sessionizer.sumOfSubThresholdGapsHours(from: events, tau: tau, pooling: pooling)
        }

        #expect(abs(hours(300) - 80.05) < 0.5)
        #expect(abs(hours(900) - 98.98) < 0.5)
        #expect(abs(hours(1800) - 110.76) < 0.5)
        #expect(abs(hours(3600) - 125.46) < 0.5)
        #expect(abs(hours(7200) - 137.79) < 0.5)
    }

    /// Active time must NOT move when the session threshold moves.
    ///
    /// This is the property that makes the number on the card trustworthy: retuning where
    /// sessions are cut cannot rewrite how many hours you worked last month. It holds
    /// because the gap cap (120s) is far below any candidate threshold (>= 300s), so
    /// changing the threshold only changes how gaps are GROUPED, never how they are
    /// CREDITED.
    @Test(.enabled(if: corpusAvailable))
    func activeTimeIsInvariantToThreshold() throws {
        let events = try Self.referenceProjectEvents()
        let pooling = Sessionizer.Pooling.explicit { _ in "project" }

        func totalActive(_ tau: Double) -> Double {
            Sessionizer.sessions(from: events, options: .init(tau: tau, pooling: pooling))
                .reduce(0) { $0 + $1.activeSeconds }
        }

        let baseline = totalActive(900)
        for tau in [300.0, 1800.0, 3600.0, 7200.0] {
            #expect(abs(totalActive(tau) - baseline) < 1.0)
        }
        // And it can never exceed wall clock.
        for s in Sessionizer.sessions(from: events, options: .init(pooling: pooling)) {
            #expect(s.activeSeconds <= s.wallSeconds + 1)
        }
    }

    /// The gap distribution is a property of how people work, not of the corpus size.
    @Test(.enabled(if: corpusAvailable))
    func gapDistribution() throws {
        let events = try Self.referenceProjectEvents()
        var times = Self.records(events).compactMap(\.ts).sorted()
        var gaps: [Double] = []
        for i in 1..<times.count { gaps.append(times[i] - times[i - 1]) }
        gaps.sort()

        func pct(_ p: Double) -> Double { gaps[min(gaps.count - 1, Int(p * Double(gaps.count)))] }

        #expect(abs(pct(0.50) - 1.0) < 1.0)
        #expect(abs(pct(0.75) - 5.0) < 1.5)
        #expect(abs(pct(0.90) - 13.0) < 2.0)
        #expect(abs(pct(0.95) - 22.0) < 3.0)
        #expect(abs(pct(0.98) - 63.0) < 8.0)
        #expect(abs(pct(0.99) - 171.0) < 20.0)

        // Extremely bimodal: a dense sub-minute mass, then a thin tail. Roughly 2% of
        // gaps exceed a minute, which is what makes an idle band on the strip meaningful.
        let overMinute = Double(gaps.filter { $0 > 60 }.count) / Double(gaps.count)
        #expect(overMinute > 0.015 && overMinute < 0.03)
    }

    // MARK: - The overcount

    /// The 1.878x content-block duplication, reproduced.
    ///
    /// Claude Code writes one JSONL record per content block and repeats the identical
    /// `usage` object on each. Every naive sum of `.message.usage` in the wild is inflated
    /// by this factor.
    @Test(.enabled(if: corpusAvailable))
    func contentBlockDuplicationRatio() throws {
        let parser = ClaudeCodeParser()
        var events: [NormalizedEvent] = []
        for s in try parser.discover() {
            events.append(contentsOf: try parser.parseAll(source: s).events)
        }

        let naive = TokenAccountant.naiveTotal(events)
        // Deduplicated but WITHOUT parent aggregation, which is how the reference figure
        // was computed — it deduped by (file, message.id) across every .jsonl including
        // subagent sidecars.
        let dedupedAllFiles = TokenAccountant.ledger(
            events.map { var e = $0; e.isSidechain = false; return e }, harness: .claudeCode)

        let ratio = Double(naive.displayTotal) / Double(dedupedAllFiles.buckets.displayTotal)
        #expect(abs(ratio - 1.878) < 0.02)

        // Parent aggregation removes a SECOND, independent overcount that deduplication
        // cannot touch: subagent transcripts carry tokens the parent's Agent tool result
        // already reports in aggregate, and their message ids genuinely differ.
        let honest = TokenAccountant.ledger(events, harness: .claudeCode)
        #expect(honest.buckets.displayTotal < dedupedAllFiles.buckets.displayTotal)
        #expect(honest.dedupe == .messageID)
        #expect(honest.scope == .parentAggregated)
    }

    /// Sidecar detection must be a path-SHAPE allowlist, not a `subagents/` denylist.
    @Test
    func sidecarDetectionIsAnAllowlist() {
        #expect(ClaudeCodeParser.isRootTranscript(relativePath: "abc-123.jsonl"))
        #expect(!ClaudeCodeParser.isRootTranscript(relativePath: "abc-123/subagents/agent-x.jsonl"))
        #expect(!ClaudeCodeParser.isRootTranscript(relativePath: "abc-123/workflows/wf_1/a.jsonl"))
        // The case a denylist misses: a directory name nobody anticipated.
        #expect(!ClaudeCodeParser.isRootTranscript(relativePath: "abc-123/futuredir/x.jsonl"))
    }

    // MARK: - Prompt inflation

    /// Typed prompts vs raw `type: "user"` records.
    ///
    /// Measured: 1,456 typed against 18,836 user records — a 13x inflation if you count
    /// them all. "Prompts" is a number that appears on a card, so this is not academic.
    @Test(.enabled(if: corpusAvailable))
    func typedPromptsAreASmallFractionOfUserRecords() throws {
        let parser = ClaudeCodeParser()
        var events: [NormalizedEvent] = []
        for s in try parser.discover() {
            events.append(contentsOf: try parser.parseAll(source: s).events)
        }
        let typed = events.filter { $0.kind == .prompt }.count
        let toolResults = events.filter { $0.kind == .toolResult }.count

        #expect(typed > 1_000)
        // Tool results dominate the `user` record type by an order of magnitude.
        #expect(Double(toolResults) / Double(typed) > 8)
    }

    /// `tool_use` and `tool_result` are 1:1 by construction. A drift here catches the
    /// string-content case, the string-`toolUseResult` case, and any future shape change,
    /// all in one assertion.
    @Test(.enabled(if: corpusAvailable))
    func toolUseAndResultBalance() throws {
        let events = try Self.referenceProjectEvents()
        let uses = events.filter { $0.kind == .toolUse }.count
        let results = events.filter { $0.kind == .toolResult }.count
        let imbalance = Double(abs(uses - results)) / Double(max(uses, 1))
        #expect(imbalance < 0.02)
    }

    // MARK: - Structural facts

    @Test(.enabled(if: corpusAvailable))
    func untimestampedRecordsAreAllBookkeeping() throws {
        let events = try Self.referenceProjectEvents()
        let undated = events.filter { $0.ts == nil }
        // None of them carries usage, a tool call, or a typed prompt — which is what makes
        // it safe to exclude them from boundary logic rather than impute a time.
        #expect(undated.allSatisfy { !$0.usageAuthoritative })
        #expect(undated.allSatisfy { $0.kind != .prompt })
        #expect(undated.allSatisfy { $0.kind != .toolUse })
    }

    /// One file is not one session, in either direction.
    @Test(.enabled(if: corpusAvailable))
    func oneFileIsNotOneSession() throws {
        let events = try Self.referenceProjectEvents()
        let pooling = Sessionizer.Pooling.explicit { _ in "project" }
        let sessions = Sessionizer.sessions(from: events, options: .init(pooling: pooling))
        let fileCount = Set(events.map(\.sourceID)).count

        // 68 files produce 84 sessions: some files hold several sittings, and resume
        // appends to the same file rather than starting a new one.
        #expect(sessions.count != fileCount)

        // At least one file must span more time than the session threshold, or the whole
        // premise of gap-cutting within a file is untested.
        let spanByFile = Dictionary(grouping: events.filter { $0.ts != nil }, by: \.sourceID)
            .mapValues { evs -> Double in
                let ts = evs.compactMap(\.ts)
                return (ts.max() ?? 0) - (ts.min() ?? 0)
            }
        #expect(spanByFile.values.contains { $0 > Tuning.tauSessionSec * 4 })
    }
}
