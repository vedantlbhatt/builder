import BuilderModel
import Foundation

/// The only place in Builder where token arithmetic happens.
///
/// Concentrating it here is the point: every published number about AI coding usage is
/// wrong in at least one of the three ways below, and keeping the arithmetic in one file
/// means there is exactly one place to get it right and one place to test.
public enum TokenAccountant {

    /// Sum the way a naive implementation would — every record that carries usage.
    ///
    /// Kept, and exported, purely so the regression suite can assert the ratio against the
    /// deduplicated figure. MEASURED on the reference corpus: 10,922,288,007 naive versus
    /// 5,815,701,063 deduplicated, a factor of 1.878.
    public static func naiveTotal(_ events: [NormalizedEvent]) -> TokenBuckets {
        var b = TokenBuckets.zero
        // Every row that carries usage, authoritative or not — which is precisely what a
        // parser summing `.message.usage` across the file would produce.
        for e in events where e.tokIn != nil || e.tokOut != nil || e.tokCacheRead != nil {
            b.input += e.tokIn ?? 0
            b.output += e.tokOut ?? 0
            b.cacheRead += e.tokCacheRead ?? 0
            b.cacheWrite5m += e.tokCacheW5m ?? 0
            b.cacheWrite1h += e.tokCacheW1h ?? 0
        }
        return b
    }

    /// The correct total.
    ///
    /// Three independent corrections, each of which alone produces a wrong headline:
    ///
    /// 1. **Content-block duplication (1.878x).** Claude Code writes one JSONL record per
    ///    content block and repeats the identical `usage` object on each. Only the record
    ///    flagged `usageAuthoritative` contributes.
    ///
    /// 2. **Subagent double counting (~3x on top).** Subagent transcripts live in sidecar
    ///    files and the parent's `Agent` tool result already carries their aggregated
    ///    usage. Sidecars are parsed in full for tools and timeline but never contribute
    ///    usage — enforced at parse time by the path-shape allowlist, and again here.
    ///
    /// 3. **Synthetic turns.** `<synthetic>` is a literal model string on 15 records,
    ///    marking locally-generated error and interrupt placeholders. Dropped before any
    ///    price lookup, and it does not degrade `cost_state`.
    public static func ledger(_ events: [NormalizedEvent], harness: Harness) -> TokenLedger {
        guard harness.reportsTokens else {
            // Not zero — ABSENT. Cursor writes `{inputTokens: 0, outputTokens: 0}` on all
            // 14,565 of its message rows because usage is accounted server-side. Rendering
            // that as "0 tokens" would look like a parsing bug on every Cursor session.
            return .unreported
        }

        var b = TokenBuckets.zero
        var abandoned = TokenBuckets.zero
        var sawUsage = false
        var sawUnkeyed = false

        for e in events where e.usageAuthoritative {
            sawUsage = true
            if e.isSidechain { continue }  // belt and braces; parse-time already excluded it
            if e.dedupeKey == nil { sawUnkeyed = true }

            let one = TokenBuckets(
                input: e.tokIn ?? 0,
                output: e.tokOut ?? 0,
                cacheRead: e.tokCacheRead ?? 0,
                cacheWrite5m: e.tokCacheW5m ?? 0,
                cacheWrite1h: e.tokCacheW1h ?? 0
            )
            b += one
            // Rewound branches still cost money. They are counted, and reported apart.
            if e.onLivePath == false { abandoned += one }
        }

        guard sawUsage else {
            return TokenLedger(
                buckets: .zero,
                dedupe: .messageID,
                scope: .parentAggregated,
                coverage: .partial,
                reported: false
            )
        }

        return TokenLedger(
            buckets: b,
            dedupe: sawUnkeyed ? .none : .messageID,
            scope: .parentAggregated,
            coverage: sawUnkeyed ? .partial : .complete,
            abandonedBranchTokens: abandoned.displayTotal,
            reported: true
        )
    }

    /// Lines the agent wrote, over the SURVIVING branch only.
    ///
    /// The live-path filter is not a nicety. Edits on rewound branches were applied and
    /// then undone; their `+` lines are not in the file. Including them inflates the
    /// denominator of the human-vs-agent statement on precisely the sessions where the
    /// user iterated most.
    public static func agentLines(_ events: [NormalizedEvent]) -> (added: Int, removed: Int) {
        var added = 0
        var removed = 0
        for e in events where e.onLivePath != false {
            added += e.linesAdded ?? 0
            removed += e.linesRemoved ?? 0
        }
        return (added, removed)
    }

    /// A LOWER BOUND on the model's share of the code, plus how much to trust it.
    ///
    /// Deliberately does NOT compute `human = gitInsertions - agentAdded`. That expression
    /// subtracts a per-edit-event count from a per-commit count; the difference has no
    /// defined meaning and fails in four directions, all biased the same way. See the
    /// discussion on `AgentLineBucket`.
    ///
    /// What is actually measurable: how much the agent wrote, whether any commits landed,
    /// and whether the human edited outside the agent at all. `humanEditEvents` carries no
    /// magnitude — MEASURED, 296 `edited_text_file` records with no line counts on any of
    /// them — so the result is a bucket, and the copy says "at least".
    public static func attribution(
        agentAdded: Int,
        gitInsertions: Int,
        gitCommits: Int,
        humanEditEvents: Int
    ) -> (bucket: AgentLineBucket, confidence: AttributionConfidence) {

        // No commits in the window is the COMMON case, not the edge case: MEASURED, only
        // 4 of 44 repositories on the reference machine have any commits in a 13-day span.
        guard gitCommits > 0, gitInsertions > 0 else {
            if agentAdded == 0 { return (.unknown, .none) }
            // The agent demonstrably wrote code and nothing contradicts it, but there is
            // no independent measurement of what the human typed.
            return (humanEditEvents == 0 ? .almostAllAgent : .nineInTen, .low)
        }

        // Agent rewrite churn routinely exceeds net committed insertions, so this ratio
        // is not a share — it is evidence that the agent did most of the writing.
        let ratio = Double(agentAdded) / Double(max(gitInsertions, 1))
        let clampFired = agentAdded > gitInsertions
        let confidence: AttributionConfidence = clampFired ? .medium : .high

        let bucket: AgentLineBucket
        switch ratio {
        case ..<0.5: bucket = .mostlyYou
        case ..<0.75: bucket = .aboutHalf
        case ..<0.9: bucket = .threeInFour
        case ..<0.97: bucket = .nineInTen
        default: bucket = .almostAllAgent
        }
        return (bucket, confidence)
    }
}
