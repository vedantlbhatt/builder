import BuilderIngest
import BuilderModel
import BuilderUI
import Foundation
import Testing

/// The strip format, asserted on decoded values rather than pixels.
///
/// A class-ordinal swap is the failure this suite exists for: a renderer fed the wrong
/// ordinals paints every agent run in the prompt colour and produces a plausible-looking
/// strip showing a human who typed for three hours. It does not crash, it does not look
/// empty, and it survives code review. With a Swift renderer and a TypeScript renderer
/// reading the same bytes, only a decoded-value assertion catches it.
@Suite("Strip format")
struct StripTests {

    @Test func packRoundTrips() {
        for k in StripClass.allCases {
            for d in UInt8(0)...UInt8(3) {
                let byte = StripSpec.pack(k, density: d)
                let out = StripSpec.unpack(byte)
                #expect(out.klass == k)
                #expect(out.density == d)
                // Bits 4-7 are reserved and MUST be zero, or a future field cannot be
                // added without invalidating every stored strip.
                #expect(byte >> 4 == 0)
            }
        }
    }

    /// The ordinals are wire format. If these change, every stored strip and every
    /// already-shared card silently re-colours.
    @Test func ordinalsAreStable() {
        #expect(StripClass.idle.rawValue == 0)
        #expect(StripClass.prompting.rawValue == 1)
        #expect(StripClass.agent.rawValue == 2)
        #expect(StripClass.human_edit.rawValue == 3)
        #expect(StripMarkKind.prompt.rawValue == 0)
        #expect(StripMarkKind.commit.rawValue == 1)
        #expect(StripMarkKind.compact.rawValue == 2)
        #expect(StripSpec.columns == 1024)
    }

    /// Nearest-neighbour on column centres, never a box filter — averaging categorical
    /// ordinals is meaningless, and the TypeScript renderer must agree exactly.
    @Test func resampleIsNearestNeighbour() {
        let cols: [UInt8] = [0, 1, 2, 3]
        #expect(StripSpec.resample(cols, to: 4) == cols)

        // Downsampling samples the SOURCE at each output column's centre:
        // centres are 0.25 and 0.75, so floor(0.25 * 4) = 1 and floor(0.75 * 4) = 3.
        //
        // When an output column spans an even number of source columns the centre falls
        // exactly on a boundary, and this floors to the LATER one. The tie-break is
        // arbitrary but it must be identical in Swift and TypeScript, or the phone and
        // the Mac render subtly different strips from the same bytes — so it is pinned
        // here rather than left to whichever rounding each language happens to do.
        #expect(StripSpec.resample(cols, to: 2) == [1, 3])
        #expect(StripSpec.resample(cols, to: 8) == [0, 0, 1, 1, 2, 2, 3, 3])
        // Never produces a value that was not in the source.
        let out = StripSpec.resample(cols, to: 37)
        #expect(out.allSatisfy { cols.contains($0) })
        #expect(StripSpec.resample([], to: 10).isEmpty)
        #expect(StripSpec.resample(cols, to: 0).isEmpty)
    }

    /// Priority weighting is what keeps the human visible.
    ///
    /// Prompts are ~6% of events. Under raw argmax the agent — which is always the
    /// longest-running thing — takes every mixed column, and the person vanishes from
    /// their own timeline.
    @Test func promptingOutweighsAgentInAMixedColumn() {
        #expect(StripSpec.weight(.prompting) > StripSpec.weight(.agent))
        #expect(StripSpec.weight(.human_edit) > StripSpec.weight(.agent))
        // Six seconds of agent should still lose to two seconds of prompting.
        #expect(2.0 * StripSpec.weight(.prompting) > 6.0 * StripSpec.weight(.agent))
    }

    @Test func buildProducesExactlyOneKilobyte() {
        let t0 = 1_800_000_000.0
        let events: [NormalizedEvent] = (0..<50).map { i in
            NormalizedEvent(
                eventUID: "e\(i)", harness: .claudeCode, sourceID: "s", ordinal: i,
                nativeEventID: "e\(i)", ts: t0 + Double(i) * 30,
                kind: i % 10 == 0 ? .prompt : .toolUse)
        }
        let strip = StripBuilder.build(events: events, startedAt: t0, endedAt: t0 + 1500)

        #expect(strip.cols.count == StripSpec.columns)
        #expect(strip.cols.allSatisfy { $0 >> 4 == 0 })
        // Prompts became marks, and marks live outside the columns so downsampling can
        // never erase them.
        #expect(!strip.marks.isEmpty)
        #expect(strip.marks.allSatisfy { $0.kind == .prompt })
        #expect(strip.t1Ms > strip.t0Ms)
    }

    /// A long idle gap must be visible as idle, not painted as work.
    @Test func idleGapsAppearAsIdle() {
        let t0 = 1_800_000_000.0
        let events: [NormalizedEvent] = [
            NormalizedEvent(eventUID: "a", harness: .claudeCode, sourceID: "s", ordinal: 0,
                            nativeEventID: "a", ts: t0, kind: .toolUse),
            // Ten minutes of nothing.
            NormalizedEvent(eventUID: "b", harness: .claudeCode, sourceID: "s", ordinal: 1,
                            nativeEventID: "b", ts: t0 + 600, kind: .toolUse),
        ]
        let strip = StripBuilder.build(events: events, startedAt: t0, endedAt: t0 + 600)
        let idle = strip.cols.filter { StripSpec.unpack($0).klass == .idle }.count
        // Only the first 120s is credited as active, so most of the bar must read idle.
        #expect(Double(idle) / Double(strip.cols.count) > 0.6)
    }
}

@Suite("Recap card")
struct CardTests {

    static func model(
        active: Double = 3600, lines: Int = 0, commits: Int = 0, prompts: Int = 10,
        title: String? = nil, chore: Bool = false, record: Bool = false,
        bucket: AgentLineBucket = .unknown, confidence: AttributionConfidence = .none
    ) -> RecapModel {
        RecapModel(
            clientSessionID: "abc123def456", harness: .claudeCode, repoName: "gt-transit",
            startedAt: 1_800_000_000, activeSeconds: active, wallSeconds: active * 1.4,
            title: title, choreTitle: chore, prompts: prompts, toolCalls: 100,
            filesTouched: 12, agentLinesAdded: lines, agentLinesRemoved: 0, commits: commits,
            tokensReported: true, outputTokens: 50_000, totalTokens: 1_200_000,
            models: ["claude-opus-5[1m]"], agentLineBucket: bucket, attribConfidence: confidence,
            stripColumns: [UInt8](repeating: 0, count: 1024), stripMarks: [],
            isPersonalRecord: record, recordKind: record ? "session" : nil,
            previousRecord: record ? 3000 : nil)
    }

    /// A personal record outranks everything. It is the only fact on the ladder that is
    /// about the person rather than the session.
    @Test func recordWinsTheHeadline() {
        let s = Superlative.choose(Self.model(active: 7200, lines: 5000, commits: 20, record: true))
        #expect(s.headline.contains("longest session yet"))
        #expect(s.subline?.contains("previous best") == true)
    }

    /// The agent share is the number nobody else displays, so it outranks raw output.
    @Test func agentShareOutranksCommits() {
        let s = Superlative.choose(
            Self.model(lines: 3000, commits: 9, bucket: .nineInTen, confidence: .high))
        #expect(s.headline == "9 of every 10 lines came from Opus 5 — at least")
    }

    /// "at least" is not decoration. The measurement is a lower bound — human edits are
    /// counted as events with no line count — so a bare percentage would be false
    /// precision, and the hedge happens to be the more impressive phrasing anyway.
    @Test func agentShareIsPhrasedAsALowerBound() {
        for bucket in [AgentLineBucket.nineInTen, .threeInFour] {
            #expect(bucket.headline(modelName: "Opus 5").contains("at least"))
        }
    }

    /// A chore-log title must never become a headline. Reading all 82 on-disk titles on
    /// the reference machine turned up "Check backend service running on port 5001" and
    /// "Say hi in three words" — a card leading with that reads like a Jira ticket.
    @Test func choreTitlesAreRejected() {
        let s = Superlative.choose(
            Self.model(active: 1500, title: "Check backend service running on port 5001", chore: true))
        #expect(!s.headline.contains("Check backend"))

        let good = Superlative.choose(
            Self.model(active: 1500, title: "Wired up Stripe webhooks", chore: false))
        #expect(good.headline == "Wired up Stripe webhooks")
    }

    /// Always a headline, whatever the data. Sessions with no commits, no lines and no
    /// tokens are common — Cursor has no tokens at all — and an empty headline is worse
    /// than a boring one.
    @Test func alwaysProducesAHeadline() {
        let bare = Superlative.choose(Self.model(active: 900, lines: 0, commits: 0, prompts: 0))
        #expect(!bare.headline.isEmpty)
        #expect(bare.headline == "15m")
    }

    /// Model names are shortened for display but the raw label keeps its context suffix.
    @Test func modelNameIsHumanReadable() {
        #expect(Self.model().primaryModelName == "Opus 5")
        var m = Self.model()
        m.models = ["claude-fable-5"]
        #expect(m.primaryModelName == "Fable 5")
    }
}
