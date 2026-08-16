import Foundation

/// Every tunable constant in Builder, in one file, each with the measurement it came from.
///
/// The rule: if a number here changes, the comment above it says what evidence would
/// justify the change. A constant without provenance is a guess wearing a decimal point.
///
/// REFERENCE CORPUS, measured 2026-08-15 on the author's machine. Every "MEASURED" note
/// below refers to it unless stated otherwise:
///   ~/.claude/projects  — 1.2 GB, 312 .jsonl files, 108,504 records, 89 root sessions
///   project RideGT      — 48,096 timestamped events pooled across 68 files, 309.5 h span
///   Cursor globalStorage — 482 composer headers, 14,565 message rows
///
/// CALIBRATION WARNING. This corpus is the single heaviest plausible user: ~99 active
/// hours in 13.2 days. A typical user will produce roughly a fifth of that. Every display
/// floor and bucket edge below therefore needs re-derivation from cohort percentiles at
/// ~1,000 users. They live here, together, precisely so that recalibration is a one-line
/// change plus a cache rebuild rather than a migration.
public enum Tuning {

    /// Bumping this invalidates cache.sqlite and forces a full re-derive. Any change to a
    /// constant in this file must bump it, or users keep numbers computed under the old
    /// rules with no way to notice.
    public static let version = "2026-08-16.1"

    // MARK: - Sessionization

    /// Idle gap that ends a session, in seconds.
    ///
    /// MEASURED gap distribution over 48,095 consecutive pairs in the reference project:
    ///   p50 1.0s · p75 5s · p90 13s · p95 22s · p98 63s · p99 171s · p99.5 258s
    ///   p99.9 1,951s (32.5 min) · max 52,386s (14.55 h) · mean 23.2s
    /// Only 997 of 48,095 gaps exceed 60s — 2.07%. The distribution is extremely bimodal:
    /// a dense sub-minute mass, then a thin tail. So the threshold is not fitting a valley,
    /// it is choosing a story length.
    ///
    /// Sessions produced at each candidate:
    ///     300s -> 217 sessions,  80.05 h active, 22.1 min mean   (shatters one afternoon)
    ///     900s ->  84 sessions,  98.98 h active, 70.7 min mean   <-- CHOSEN
    ///    1800s ->  52 sessions, 110.76 h active, 127.8 min mean  (nobody's "activity" is 2.1 h)
    ///    3600s ->  30 sessions, 125.46 h active, 250.9 min mean
    /// The count swings 2.8x between 900 and 3600, so this is the highest-leverage number
    /// in the product. 900s cuts at roughly the 99.83rd gap percentile and yields a mean
    /// session of 71 minutes, which is run-shaped.
    ///
    /// NOT user-configurable, on purpose. "Longest session" and any future comparison
    /// between two people are meaningless if two machines disagree about what a session is.
    public static let tauSessionSec: Double = 900

    /// Bumped whenever the sessionization ALGORITHM changes, not just the threshold.
    public static let sessionizerVersion = 1

    /// Maximum credit, in seconds, that a single inter-event gap contributes to active time.
    ///
    /// MEASURED: 120s sits between p98 (63s) and p99 (171s), so 98.7% of gaps are counted
    /// at their full length and only the genuine pauses get truncated.
    ///
    /// Deliberately INVARIANT to `tauSessionSec`: active time is computed from event
    /// spacing, not from session boundaries, so retuning the boundary does not move the
    /// number on the card. This is the elapsed-vs-moving distinction, which is exactly
    /// how a running app reports a run that included a traffic light.
    public static let activeGapCapSec: Double = 120

    public static let activeCalcVersion = 1

    /// Minimum gap that becomes a visible idle band on the timeline strip.
    ///
    /// MEASURED: only 2.07% of gaps exceed 60s, so an idle block at this threshold reads
    /// as a real event in the session rather than as visual noise.
    ///
    /// This is a DIFFERENT JOB from `activeGapCapSec` — one decides what the strip draws,
    /// the other decides what the clock counts. Two independent designs conflated them.
    public static let tauIdleSegSec: Double = 60

    /// How far before a commit we look for agent edits when attributing it to a session.
    public static let tauCommitAttributionSec: Double = 1800

    // MARK: - Display floors
    //
    // `counted` and `notable` are different questions. A four-minute question-and-answer
    // should still add its minutes to your week; it should not become a card.

    /// Below this a session still counts toward hours, graph and streaks but is not carded.
    public static let countedMinActiveSec: Double = 300

    /// "Meaningful" excludes system-injected turns: `promptSource == "typed"` gives 1,456
    /// real prompts where a naive `type == "user"` count gives 18,836 — a 13x inflation.
    public static let countedMinMeaningfulEvents = 3

    /// Eligible for a recap card, a personal record, or a notification.
    ///
    /// UNMEASURED JUDGEMENT CALL. On the reference corpus at tau=900 the session length
    /// median is 39.3 min (p25 8.1 min, p10 3.4 min); 28% of sessions are under 10 minutes
    /// while the top 12% hold 44% of all hours. 20 minutes keeps the tail out of the feed
    /// without hiding a productive half hour. Recalibrate against cohort data.
    public static let notableMinActiveSec: Double = 1200
    public static let notableMinNetLines = 300

    /// Harness-written titles are frequently chore-log entries — a reviewer read all 82
    /// on-disk titles on the reference machine and found "Check backend service running on
    /// port 5001", "Add file to chat in terminal", "Say hi in three words". A title
    /// matching this pattern is marked `chore_title` and the card falls through to the
    /// superlative ladder instead of printing it as a headline.
    public static let choreTitlePattern =
        #"^(Check|Run|Debug the|Disable|Enable|List|Add file|Say|Clarify|Analyze|Toggle)\b"#

    // MARK: - Parsing limits

    /// MEASURED: average JSONL line is 12.1 KB, but individual records reach megabytes and
    /// the largest single transcript is 78 MB across only 2,635 records. A line over this
    /// is skipped with a `oversized_line_skipped` diagnostic rather than read into memory.
    public static let maxLineBytes = 32 << 20

    public static let readBufferBytes = 1 << 20

    /// Hash of the first 64 KiB of a source file, stored in the watermark. Catches an
    /// in-place rewrite that happens to land on the same byte length, which `size + mtime`
    /// alone would miss and which would silently resume mid-file at the wrong offset.
    public static let headHashBytes = 64 << 10

    public static let insertBatchRows = 4096

    /// Vendored and generated files inflate BOTH sides of any line comparison, so they are
    /// excluded from git enrichment. `--` is always passed before pathspecs because Claude
    /// Code project directory names literally begin with `-`.
    public static let gitExcludePathspecs = [
        ":(exclude)*.lock",
        ":(exclude)package-lock.json",
        ":(exclude)bun.lockb",
        ":(exclude)yarn.lock",
        ":(exclude)Pods/**",
        ":(exclude)node_modules/**",
        ":(exclude)*.pbxproj",
        ":(exclude)*.xcworkspacedata",
    ]

    /// MEASURED: appears literally, as this string, in `.message.model` on 15 records.
    /// These are locally-generated placeholder turns for errors and interrupts — not real
    /// API calls. Dropped from cost AND from token totals before any price lookup, and it
    /// does not degrade the session's `cost_state`.
    public static let syntheticModelSentinel = "<synthetic>"

    /// Sessions where more than this fraction of active time is background work are marked
    /// `unattended`: they still count toward hours but are excluded from streaks and from
    /// the "longest session" record, because nobody was at the keyboard.
    ///
    /// UNMEASURED JUDGEMENT CALL. `sessionKind: "bg"` appears on 2,508 records.
    public static let unattendedBgFraction = 0.80

    // MARK: - Repo identity

    /// GLOBAL, not per-user, and NOT SECRET. Matching the same repository across two
    /// machines requires both to derive the same hash, so this ships inside an open-source
    /// binary and anyone can read it. It defeats casual exposure and makes a database dump
    /// unreadable; it does not survive a dictionary attack against public repo names.
    /// PRIVACY.md states exactly this. `excluded` is the answer for anything sensitive.
    ///
    /// TODO(WP-5): replace with 32 real bytes and never rotate them without bumping
    /// `repoPepperVersion`, which is carried on every uploaded session for exactly this.
    public static let repoPepper: [UInt8] = Array("builder-dev-pepper-not-final".utf8)
    public static let repoPepperVersion = 1
    public static let repoHashPrefix = "builder-repo-v1|"

    // MARK: - Contribution graph

    /// Bucket edges in ACTIVE HOURS per day, giving levels 0...5.
    ///
    /// ABSOLUTE, not per-user quantiles. Self-relative buckets would make any two profiles
    /// non-comparable, and a screenshot of a graph whose scale is private to its owner
    /// communicates nothing. 8 h is a real human ceiling, which is what makes a full-tone
    /// square mean something specific.
    ///
    /// MEASURED for sanity: the reference corpus is 98.98 active hours over 13.2 days,
    /// i.e. ~7.5 h/day for the heaviest plausible user, which lands at the top of this
    /// ramp — as intended. A typical user will sit in levels 1-3.
    public static let graphHourBuckets: [Double] = [0.0, 0.5, 2.0, 4.0, 8.0]
    public static let graphFullScaleHours: Double = 8.0

    /// A session that crosses local midnight is attributed entirely to its START date.
    /// Splitting it manufactures a two-day streak out of one sitting, and the target
    /// audience skews nocturnal, so this materially changes streak records.
    public static let attributeSessionToStartDate = true
}
