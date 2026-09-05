import BuilderModel
import BuilderUI
import Foundation
import Testing

/// How a stored `SessionAnalysis` becomes the four things the card and the panel show.
///
/// The document is model-written, so every value here is one the schema promises but a
/// prompt retune could break: a score outside 0-100, a dimension missing, an archetype
/// withheld for a short session. The card must degrade quietly on each.
@Suite("Recap analysis")
struct RecapAnalysisTests {

    static func analysis(
        headline: String = "Wired Stripe webhooks end to end",
        summary: String = "Built the webhook receiver and signature check. Tests were added for the retry path. Ended shipped.",
        outcome: SessionAnalysis.Outcome = .shipped,
        archetype: SessionAnalysis.Archetype? = .velocityMachine,
        dimensions: [SessionAnalysis.DimensionScore] = [
            .init(dimension: .steering, score: 70, rationale: "r"),
            .init(dimension: .execution, score: 85, rationale: "r"),
            .init(dimension: .engineering, score: 60, rationale: "r"),
            .init(dimension: .productInstinct, score: 55, rationale: "r"),
            .init(dimension: .planning, score: 40, rationale: "r"),
        ]
    ) -> SessionAnalysis {
        SessionAnalysis(
            analysisVersion: AnalysisSpec.version, model: "sonnet",
            generatedAt: Date(timeIntervalSince1970: 1_800_000_000),
            digestHash: String(repeating: "a", count: 64), digestCoverage: 1,
            headline: headline, summary: summary, outcome: outcome, features: [],
            workMix: ["feature": 1],
            buildStyle: .init(
                planning: .light, iteration: .iterative, steering: .guided,
                verification: .ranTests, scopeControl: .held),
            dimensions: dimensions, archetype: archetype, decisionPatterns: [], pivots: [],
            friction: [],
            prompting: .init(tone: .terse, specificity: 60, correctionShare: 0.1, questionShare: 0.1),
            growthEdge: [], tags: [], confidence: 0.8, containsSensitive: false)
    }

    @Test func applyCopiesTheFourThingsTheCardShows() {
        var m = CardTests.model()
        m.apply(Self.analysis())
        #expect(m.analysisHeadline == "Wired Stripe webhooks end to end")
        #expect(m.analysisOutcome == "shipped")
        #expect(m.analysisArchetype == "velocity_machine")
        #expect(m.analysisLine == "shipped · velocity machine")
        #expect(m.dimensionScores.map(\.label) == ["steer", "exec", "eng", "product", "plan"])
        #expect(m.dimensionScores.map(\.score) == [70, 85, 60, 55, 40])
        #expect(Superlative.choose(m).headline == "Wired Stripe webhooks end to end")
    }

    /// The schema says 0-100. A bar drawn from a value outside it would overflow its
    /// track and read as a rendering bug, so the clamp lives in the value, not the view.
    @Test func dimensionScoresClampToZeroThroughOneHundred() {
        #expect(RecapModel.Dimension(label: "steer", score: 140).score == 100)
        #expect(RecapModel.Dimension(label: "steer", score: -5).score == 0)
        #expect(RecapModel.Dimension(label: "steer", score: 0).score == 0)
        #expect(RecapModel.Dimension(label: "steer", score: 100).score == 100)
        #expect(RecapModel.Dimension(label: "steer", score: 42).score == 42)

        var m = CardTests.model()
        m.apply(Self.analysis(dimensions: [
            .init(dimension: .steering, score: 250, rationale: "r"),
            .init(dimension: .planning, score: -1, rationale: "r"),
        ]))
        #expect(m.dimensionScores.allSatisfy { (0...100).contains($0.score) })
        #expect(m.dimensionScores.map(\.score) == [100, 0])
    }

    /// Dimensions come out in spec order whatever order the model wrote them, and a
    /// missing one is missing — never invented as zero.
    @Test func dimensionsAreSpecOrderedAndNeverInvented() {
        var m = CardTests.model()
        m.apply(Self.analysis(dimensions: [
            .init(dimension: .planning, score: 40, rationale: "r"),
            .init(dimension: .steering, score: 70, rationale: "r"),
        ]))
        #expect(m.dimensionScores.map(\.label) == ["steer", "plan"])
        #expect(m.dimensionScores.map(\.score) == [70, 40])
    }

    /// A session under about fifteen minutes gets no archetype (docs/analysis.md); the
    /// second line then carries the outcome alone rather than a dangling separator.
    @Test func lineWithoutArchetypeHasNoSeparator() {
        var m = CardTests.model()
        m.apply(Self.analysis(archetype: nil))
        #expect(m.analysisArchetype == nil)
        #expect(m.analysisLine == "shipped")
    }

    /// Whitespace-only headline is treated as absent, so it cannot win the ladder.
    @Test func blankHeadlineIsAbsent() {
        var m = CardTests.model(lines: 3000, bucket: .nineInTen, confidence: .high)
        m.apply(Self.analysis(headline: "  \n"))
        #expect(m.analysisHeadline == nil)
        #expect(Superlative.choose(m).headline == "9 of every 10 lines came from Opus 5 — at least")
    }

    @Test func panelSummaryWordsEnumsLikeThePhone() {
        let s = MenuBarPanel.AnalysisSummary(analysis: Self.analysis(), checkpoint: true)
        #expect(s.outcome == "shipped")
        #expect(s.archetype == "velocity machine")
        #expect(s.checkpoint)
        #expect(s.headline == "Wired Stripe webhooks end to end")
    }

    /// The panel shows about one sentence. A short summary is returned whole; a long
    /// one is cut on a word boundary and ends in an ellipsis, never mid-word.
    @Test func panelSummaryTruncatesAtAboutOneSixty() {
        let short = MenuBarPanel.AnalysisSummary(analysis: Self.analysis(summary: "Short and done."))
        #expect(short.shortSummary() == "Short and done.")

        let firstSentence = MenuBarPanel.AnalysisSummary(
            analysis: Self.analysis(
                summary: "Spent 1.5 h on the webhook receiver and signature check. "
                    + String(repeating: "Then the retry path was tested again. ", count: 6)))
        #expect(firstSentence.shortSummary() == "Spent 1.5 h on the webhook receiver and signature check.")

        let long = MenuBarPanel.AnalysisSummary(
            analysis: Self.analysis(summary: String(repeating: "word ", count: 100)))
        let cut = long.shortSummary()
        #expect(cut.hasSuffix("…"))
        #expect(cut.count <= 161)
        #expect(!cut.contains("wor…"))
    }
}
