import Foundation

/// Everything a recap card needs, already resolved.
///
/// The card never queries. Building this struct is where the judgement calls happen —
/// which title to trust, whether tokens exist, what the headline should be — so they can
/// be tested without rendering anything.
public struct RecapModel: Sendable, Equatable {
    public var clientSessionID: String
    public var harness: Harness
    public var repoName: String?
    public var startedAt: Double
    public var activeSeconds: Double
    public var wallSeconds: Double
    public var title: String?
    public var choreTitle: Bool
    public var prompts: Int
    public var toolCalls: Int
    public var filesTouched: Int
    public var agentLinesAdded: Int
    public var agentLinesRemoved: Int
    public var commits: Int
    public var tokensReported: Bool
    public var outputTokens: Int
    public var totalTokens: Int
    public var models: [String]
    public var agentLineBucket: AgentLineBucket
    public var attribConfidence: AttributionConfidence
    public var stripColumns: [UInt8]
    public var stripMarks: [(ms: Int, kind: StripMarkKind)]
    public var isPersonalRecord: Bool
    public var recordKind: String?
    public var previousRecord: Double?
    /// The model-written reading of the session (`SessionAnalysis`), when one has been
    /// stored. All nil/empty when analysis is off, has not run yet, or failed — the card
    /// must render identically to before in that case.
    public var analysisHeadline: String?
    /// Raw enum value, e.g. `shipped`. Display copy comes from `analysisLine`.
    public var analysisOutcome: String?
    /// Raw enum value, e.g. `velocity_machine`. nil for sessions too short to type.
    public var analysisArchetype: String?
    /// One bar per dimension, in spec order, each already clamped to 0...100. Empty
    /// when there is no analysis.
    public var dimensionScores: [Dimension]

    /// One dimension score as the card draws it: a short label and a 0...100 bar.
    ///
    /// Clamped on construction rather than at draw time. The schema says 0-100, but a
    /// bar drawn from a value the model got wrong would overflow its track and look
    /// like a rendering bug rather than a data one.
    public struct Dimension: Sendable, Equatable {
        public let label: String
        public let score: Int

        public init(label: String, score: Int) {
            self.label = label
            self.score = min(100, max(0, score))
        }

        /// The card's five short labels, in spec order. Five columns under 30pt each is
        /// all the lower-right quadrant has room for.
        public static func shortLabel(_ d: SessionAnalysis.Dimension) -> String {
            switch d {
            case .steering: return "steer"
            case .execution: return "exec"
            case .engineering: return "eng"
            case .productInstinct: return "product"
            case .planning: return "plan"
            }
        }
    }

    public init(
        clientSessionID: String, harness: Harness, repoName: String?, startedAt: Double,
        activeSeconds: Double, wallSeconds: Double, title: String?, choreTitle: Bool,
        prompts: Int, toolCalls: Int, filesTouched: Int, agentLinesAdded: Int,
        agentLinesRemoved: Int, commits: Int, tokensReported: Bool, outputTokens: Int,
        totalTokens: Int, models: [String], agentLineBucket: AgentLineBucket,
        attribConfidence: AttributionConfidence, stripColumns: [UInt8],
        stripMarks: [(ms: Int, kind: StripMarkKind)], isPersonalRecord: Bool = false,
        recordKind: String? = nil, previousRecord: Double? = nil,
        analysisHeadline: String? = nil, analysisOutcome: String? = nil,
        analysisArchetype: String? = nil, dimensionScores: [Dimension] = []
    ) {
        self.clientSessionID = clientSessionID
        self.harness = harness
        self.repoName = repoName
        self.startedAt = startedAt
        self.activeSeconds = activeSeconds
        self.wallSeconds = wallSeconds
        self.title = title
        self.choreTitle = choreTitle
        self.prompts = prompts
        self.toolCalls = toolCalls
        self.filesTouched = filesTouched
        self.agentLinesAdded = agentLinesAdded
        self.agentLinesRemoved = agentLinesRemoved
        self.commits = commits
        self.tokensReported = tokensReported
        self.outputTokens = outputTokens
        self.totalTokens = totalTokens
        self.models = models
        self.agentLineBucket = agentLineBucket
        self.attribConfidence = attribConfidence
        self.stripColumns = stripColumns
        self.stripMarks = stripMarks
        self.isPersonalRecord = isPersonalRecord
        self.recordKind = recordKind
        self.previousRecord = previousRecord
        self.analysisHeadline = analysisHeadline
        self.analysisOutcome = analysisOutcome
        self.analysisArchetype = analysisArchetype
        self.dimensionScores = dimensionScores
    }

    /// Copy the parts of a stored analysis the card shows. One place, so the CLI, the
    /// app and the tests derive the same four things from the same document.
    ///
    /// The headline is dropped when blank: an empty string must never win the
    /// `Superlative` ladder and render a card with no headline at all. Dimensions are
    /// emitted in spec order regardless of the order the model wrote them, and a
    /// dimension the model omitted is omitted here too — never invented as zero.
    public mutating func apply(_ analysis: SessionAnalysis) {
        let h = analysis.headline.trimmingCharacters(in: .whitespacesAndNewlines)
        analysisHeadline = h.isEmpty ? nil : h
        analysisOutcome = analysis.outcome.rawValue
        analysisArchetype = analysis.archetype?.rawValue
        dimensionScores = SessionAnalysis.Dimension.allCases.compactMap { d in
            analysis.dimensions.first(where: { $0.dimension == d }).map {
                Dimension(label: Dimension.shortLabel(d), score: $0.score)
            }
        }
    }

    /// "shipped · velocity machine" — the card's second line. nil without an analysis.
    /// Enum values are shown with underscores as spaces, the same `labelize` the phone
    /// applies, so the two surfaces read the same word.
    public var analysisLine: String? {
        let parts = [analysisOutcome, analysisArchetype].compactMap { $0 }
            .map { $0.replacingOccurrences(of: "_", with: " ") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    public static func == (a: RecapModel, b: RecapModel) -> Bool {
        a.clientSessionID == b.clientSessionID && a.stripColumns == b.stripColumns
    }

    /// The short model name for copy: "Opus 5", not "claude-opus-5[1m]".
    public var primaryModelName: String? {
        guard let raw = models.first else { return nil }
        var s = raw
        if let bracket = s.firstIndex(of: "[") { s = String(s[s.startIndex..<bracket]) }
        s = s.replacingOccurrences(of: "claude-", with: "")
        let parts = s.split(separator: "-").map(String.init)
        guard !parts.isEmpty else { return nil }
        return parts.map { $0.prefix(1).uppercased() + $0.dropFirst() }.joined(separator: " ")
    }
}

/// Picks the one thing the card says loudest.
///
/// Neither of the obvious choices works. A DURATION is evaluable but not remarkable — a
/// stranger scrolling past learns that someone worked for a while. And the harness's own
/// TITLE is a chore-log entry: reading all 82 on-disk titles on the reference machine
/// turned up "Check backend service running on port 5001", "Add file to chat in terminal",
/// "Say hi in three words". Leading with either produces a card that reads like a Jira
/// ticket someone screenshotted.
///
/// So the headline is the most remarkable TRUE fact available, in a fixed order of
/// interest. Every branch is a measurement, never a grade and never an interpretation —
/// the card describes what happened and stops there.
public enum Superlative: Sendable, Equatable {
    case personalRecord(kind: String, value: String, previous: String?)
    /// The model's one-line reading of what the session WAS. Below a record — a record
    /// is rarer news, and it is about the person rather than the session — but above
    /// every measured rung, because "Wired Stripe webhooks end to end" says more than
    /// "9 of every 10 lines".
    case analysis(String)
    case agentShare(bucket: AgentLineBucket, model: String)
    case commits(Int)
    case netLines(Int)
    case longRun(Double)
    case titled(String)
    case duration(Double)

    public static func choose(_ m: RecapModel) -> Superlative {
        if m.isPersonalRecord, let kind = m.recordKind {
            return .personalRecord(
                kind: kind,
                value: format(duration: m.activeSeconds),
                previous: m.previousRecord.map { format(duration: $0) })
        }
        if let h = m.analysisHeadline, !h.isEmpty { return .analysis(h) }
        // The agent share is the one number nobody else displays, and everyone is
        // privately curious about theirs. It outranks raw output when it is known.
        if m.attribConfidence != .none, m.agentLineBucket != .unknown, m.agentLinesAdded >= 200,
           let model = m.primaryModelName {
            return .agentShare(bucket: m.agentLineBucket, model: model)
        }
        if m.commits >= 5 { return .commits(m.commits) }
        if m.agentLinesAdded >= 1000 { return .netLines(m.agentLinesAdded) }
        if m.activeSeconds >= 2700 { return .longRun(m.activeSeconds) }
        // A real title beats a bare duration, but a chore-log title does not.
        if let t = m.title, !t.isEmpty, !m.choreTitle, t.count <= 60 { return .titled(t) }
        return .duration(m.activeSeconds)
    }

    public var headline: String {
        switch self {
        case .personalRecord(let kind, let value, _):
            return "\(value) — longest \(kind) yet"
        case .analysis(let h):
            return h
        case .agentShare(let bucket, let model):
            return bucket.headline(modelName: model)
        case .commits(let n):
            return "\(n) commits"
        case .netLines(let n):
            return "+\(Superlative.format(int: n)) lines"
        case .longRun(let s):
            return "\(Superlative.format(duration: s)) in one sitting"
        case .titled(let t):
            return t
        case .duration(let s):
            return Superlative.format(duration: s)
        }
    }

    /// A second line only where it adds a fact the headline does not already carry.
    public var subline: String? {
        switch self {
        case .personalRecord(_, _, let previous):
            return previous.map { "previous best \($0)" }
        default:
            return nil
        }
    }

    static func format(duration seconds: Double) -> String {
        let s = Int(seconds.rounded())
        let h = s / 3600
        let m = (s % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }

    static func format(int n: Int) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: n)) ?? "\(n)"
    }
}
