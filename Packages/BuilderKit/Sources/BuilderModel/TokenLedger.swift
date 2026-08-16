import Foundation

/// How duplicate usage records were collapsed.
///
/// Carried on every session and uploaded, because a token total whose deduplication basis
/// is unknown is not a measurement. The server rejects `none` together with
/// `tokensReported == true` — that combination is precisely the 1.878x overcount arriving
/// with a straight face.
public enum TokenDedupe: String, Sendable, Codable {
    /// Preferred. `sourceID | message.id`.
    case messageID = "message_id"
    /// Fallback when `message.id` is absent but `requestId` is present.
    case requestID = "request_id"
    /// No usable key. Forces `coverage == .partial`, and the session reports no tokens.
    case none
}

/// Which records were allowed to contribute usage.
///
/// There is exactly ONE legal accounting basis and it is `parentAggregated`: root
/// transcripts plus each `Agent` tool result's own pre-aggregated `usage`. All 224
/// subagent sidecar transcripts on the reference machine are parsed in full for tools and
/// timeline but written non-authoritative UNCONDITIONALLY.
///
/// There is deliberately no `flat` case. Globbing `**/*.jsonl` and summing everything
/// triple-counts subagent tokens on top of the parent's already-aggregated total; adding a
/// second legal value would let that number into the database wearing a legitimate label,
/// which is worse than a crash.
public enum TokenScope: String, Sendable, Codable {
    case parentAggregated = "parent_aggregated"
    /// Some sources in this session could not be attributed to one basis.
    case mixed
}

/// Whether the number is trustworthy, incomplete, or simply does not exist.
public enum TokenCoverage: String, Sendable, Codable {
    case complete
    /// Some usage was unrecoverable — e.g. a `Workflow` result, which carries
    /// `{status: "async_launched", transcriptDir}` and no usage at all. MEASURED: 23
    /// Workflow calls on the reference corpus.
    case partial
    /// The harness never writes token counts. Cursor, always. Not missing — absent.
    case structurallyAbsent = "structurally_absent"
}

/// Five buckets and no total.
///
/// `billableTotal` was considered and deleted. `cacheRead` IS billed, at a reduced rate,
/// and on a cache-heavy agentic workload it dominates the input side — MEASURED, the
/// reference corpus reports `input_tokens` values as low as 2 on requests whose real cost
/// is entirely cache reads. A single summed field would silently pick one definition and
/// every downstream consumer would inherit it without knowing.
///
/// The card's headline figure is `displayTotal` below, and its definition is pinned by a
/// regression test against `ccusage` output for the same transcript. The target audience
/// already screenshots `ccusage`; a headline differing from it by a multiple looks broken
/// on day one regardless of which number is more defensible.
public struct TokenBuckets: Sendable, Equatable, Codable {
    public var input: Int
    public var output: Int
    public var cacheRead: Int
    public var cacheWrite5m: Int
    public var cacheWrite1h: Int

    public static let zero = TokenBuckets(input: 0, output: 0, cacheRead: 0, cacheWrite5m: 0, cacheWrite1h: 0)

    public init(input: Int = 0, output: Int = 0, cacheRead: Int = 0, cacheWrite5m: Int = 0, cacheWrite1h: Int = 0) {
        self.input = input
        self.output = output
        self.cacheRead = cacheRead
        self.cacheWrite5m = cacheWrite5m
        self.cacheWrite1h = cacheWrite1h
    }

    /// What the card prints. Matches `ccusage`'s notion of total tokens.
    public var displayTotal: Int { input + output + cacheRead + cacheWrite5m + cacheWrite1h }

    public static func + (a: TokenBuckets, b: TokenBuckets) -> TokenBuckets {
        TokenBuckets(
            input: a.input + b.input,
            output: a.output + b.output,
            cacheRead: a.cacheRead + b.cacheRead,
            cacheWrite5m: a.cacheWrite5m + b.cacheWrite5m,
            cacheWrite1h: a.cacheWrite1h + b.cacheWrite1h
        )
    }

    public static func += (a: inout TokenBuckets, b: TokenBuckets) { a = a + b }
}

/// Token buckets plus the provenance that makes them meaningful.
///
/// Two ledgers may only be added when their basis agrees. This is a `precondition`, not a
/// coalescing rule, because a silent merge of differently-deduplicated numbers produces a
/// total that is wrong in a way no test downstream can detect.
public struct TokenLedger: Sendable, Equatable {
    public var buckets: TokenBuckets
    public var dedupe: TokenDedupe
    public var scope: TokenScope
    public var coverage: TokenCoverage

    /// Tokens spent on DAG branches that were rewound and never landed.
    ///
    /// MEASURED: 225 fork points on the reference corpus. Those branches carry valid
    /// timestamps and *distinct* `message.id` values, so message-id deduplication does not
    /// touch them. They stay in the token total — you paid for them — and are reported
    /// separately so the card can say so. Lines and file counts, by contrast, exclude them
    /// entirely: an edit that was rewound never reached the file.
    public var abandonedBranchTokens: Int

    /// `false` for every Cursor session, forever.
    public var reported: Bool

    public static let unreported = TokenLedger(
        buckets: .zero,
        dedupe: .none,
        scope: .parentAggregated,
        coverage: .structurallyAbsent,
        abandonedBranchTokens: 0,
        reported: false
    )

    public init(
        buckets: TokenBuckets,
        dedupe: TokenDedupe,
        scope: TokenScope,
        coverage: TokenCoverage,
        abandonedBranchTokens: Int = 0,
        reported: Bool = true
    ) {
        self.buckets = buckets
        self.dedupe = dedupe
        self.scope = scope
        self.coverage = coverage
        self.abandonedBranchTokens = abandonedBranchTokens
        self.reported = reported
    }

    /// Combine two ledgers from the same session.
    ///
    /// Preconditions rather than coalescing: adding a `messageID`-deduplicated ledger to a
    /// `none`-deduplicated one yields a number that is neither, and nothing downstream
    /// could ever notice.
    public static func + (a: TokenLedger, b: TokenLedger) -> TokenLedger {
        if !a.reported { return b }
        if !b.reported { return a }
        precondition(
            a.dedupe == b.dedupe,
            "refusing to add token ledgers with different dedupe bases (\(a.dedupe) vs \(b.dedupe))"
        )
        precondition(
            a.scope == b.scope,
            "refusing to add token ledgers with different scopes (\(a.scope) vs \(b.scope))"
        )
        return TokenLedger(
            buckets: a.buckets + b.buckets,
            dedupe: a.dedupe,
            scope: a.scope,
            coverage: (a.coverage == .complete && b.coverage == .complete) ? .complete : .partial,
            abandonedBranchTokens: a.abandonedBranchTokens + b.abandonedBranchTokens,
            reported: true
        )
    }
}

/// How confident the human-vs-agent attribution is for a session.
public enum AttributionConfidence: String, Sendable, Codable {
    /// Commits exist in the window and the agent's own line counts are complete.
    case high
    /// Commits exist but coverage is partial.
    case medium
    /// No commits in the window — the overwhelmingly common case. MEASURED: only 4 of 44
    /// repositories on the reference machine have any commits in the 13-day window.
    case low
    case none
}

/// A LOWER BOUND on how much of the code came from the model, phrased as one.
///
/// The obvious formula, `human = max(0, gitInsertions - agentAdded)`, is deleted. It
/// subtracts a per-edit-event count from a per-commit count and the difference has no
/// defined semantics. It fails in four directions, all biased the same way:
///
///   1. Agent rewrite churn makes `agentAdded > gitInsertions`, the clamp fires, and human
///      reads 0 when it is positive — the COMMON case in long agent runs.
///   2. A session with no commit has `gitInsertions == 0`, so human reads 0 regardless of
///      how much was typed. Most sessions have no commit.
///   3. The one true-positive human signal, `edited_text_file`, is 296 records against
///      2,808 agent edits and carries no line count at all.
///   4. Cursor's `humanChanges` array was asserted by two designs to be a line source; it
///      was never established that it carries line counts.
///
/// So the card says "9 of every 10 lines came from Opus 5 — at least". The words *at
/// least* make it honest AND make it the more impressive framing. An exact percentage
/// appears only on the profile trend, only at `.medium` confidence or better.
public enum AgentLineBucket: String, Sendable, Codable, CaseIterable {
    case almostAllAgent = "almost_all_agent"
    case nineInTen = "nine_in_ten"
    case threeInFour = "three_in_four"
    case aboutHalf = "about_half"
    case mostlyYou = "mostly_you"
    case unknown

    /// Copy for the recap card. Always a lower bound, never a point estimate.
    public func headline(modelName: String) -> String {
        switch self {
        case .almostAllAgent: return "Nearly every line came from \(modelName)"
        case .nineInTen: return "9 of every 10 lines came from \(modelName) — at least"
        case .threeInFour: return "3 of every 4 lines came from \(modelName) — at least"
        case .aboutHalf: return "About half the lines came from \(modelName)"
        case .mostlyYou: return "Most of these lines are yours"
        case .unknown: return ""
        }
    }
}
