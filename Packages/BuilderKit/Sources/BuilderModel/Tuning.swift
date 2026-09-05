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
    public static let version = "2026-09-05.2-boundaries-v3"

    // MARK: - Sessionization

    /// The FALLBACK idle gap that ends a session, in seconds.
    ///
    /// v3 (docs/session-boundaries.md): the threshold the sessionizer actually cuts with is
    /// `SessionThresholds.tau` — fitted to the user's own presence-to-presence intervals by
    /// `ThresholdFitter` (a two-Gaussian mixture on log10 seconds, the valley between the
    /// modes, Halfaker et al. WWW 2015) and clamped to [`tauSessionMinSec`,
    /// `tauSessionMaxSec`]. This constant is what it falls back to while the sample has
    /// fewer than `tauFitMinGaps` intervals or is not bimodal by the rule below, and it is
    /// what the ground-truth table in CLAUDE.md is stated at. It was chosen like this:
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
    /// Fitting it to each person's own data is the opposite of a preference: it is the
    /// same rule, read off the same kind of evidence, on every machine.
    public static let tauSessionSec: Double = 900

    /// Clamp on a FITTED tau. Below 300 s a valley sits inside the agent's own tool-cadence
    /// tail (p99 of the reference corpus is 171 s, and 300 s shattered one afternoon into
    /// 217 sessions); above 3600 s the reference corpus yields 30 sessions with a 4.2 h
    /// mean, which is a day, not a sitting.
    public static let tauSessionMinSec: Double = 300
    public static let tauSessionMaxSec: Double = 3600

    /// Fewer presence intervals than this and no fit is attempted. A two-Gaussian EM has
    /// five free parameters; 200 points gives the minor component (>= 5% by weight, so
    /// >= 10 intervals) something to stand on. UNMEASURED JUDGEMENT CALL below that: the
    /// container corpus has 23 intervals and one sitting, which is not evidence of anything.
    public static let tauFitMinGaps = 200

    /// The two modes must be at least this far apart on log10 seconds — a factor of 6.3,
    /// the smallest separation at which two unit-variance log-normal modes still show a dip
    /// between them at 0.1-decade bins. Halfaker et al. report 2–3 decades on every system
    /// they studied; the synthetic bimodal fixture has 1.5.
    public static let tauFitMinSeparationDecades: Double = 0.8

    /// The minor component must hold at least this share of the sample, or "the second
    /// mode" is a handful of outliers the EM wrapped a Gaussian around. On the reference
    /// corpus the between-sitting share of presence intervals is ~84/1,456 = 5.8% — this
    /// floor is the number to watch when that corpus is refitted.
    public static let tauFitMinComponentWeight: Double = 0.05

    /// The valley must land within half a decade of the clamp range — [95 s, 11,400 s] — or
    /// the fit found a valley between two MACHINE modes, not between activity and absence,
    /// and the clamp would turn nonsense into a confident number. MEASURED on the container
    /// corpus: a fit on raw record gaps found modes at 7 ms (one turn's records flushed
    /// together) and 3.4 s (the tool cadence), a valley at 0.1 s, and would have clamped it
    /// to 300 s. That is why the sample is presence intervals, and why this window exists.
    public static let tauFitValleyMinLog10: Double = log10(300.0) - 0.5
    public static let tauFitValleyMaxLog10: Double = log10(3600.0) + 0.5

    /// Floor on a component's variance (decades²) so a sample of identical intervals cannot
    /// drive a sigma to 0 and the responsibilities to NaN. The EM's iteration cap and stop.
    public static let tauFitVarianceFloor: Double = 1e-4
    public static let tauFitMaxIterations = 500
    public static let tauFitTolerance: Double = 1e-10

    /// When to refit: the sample grew by a tenth, or a day passed. UNMEASURED JUDGEMENT
    /// CALL: a tenth is the smallest growth that can move a 200-point fit's valley by more
    /// than the 0.1-decade bin it is reported in; daily is one tick of "today".
    public static let tauRefitGrowthFraction: Double = 0.10
    public static let tauRefitIntervalSec: Double = 86400

    /// A human opening a NEW native session in a DIFFERENT pool at least this long after
    /// a session's last record ends that session (`switched_repo`). Equal to
    /// `activeGapCapSec` on purpose: a gap under the cap is credited in full as continuous
    /// work, so hopping between two repos inside two minutes is one sitting on two repos.
    /// MEASURED on the container corpus: the seven `claude -p` runs that started in another
    /// directory during the sitting all begin with a non-human `sdk` prompt and do not fire
    /// this; a sibling agent's Codex run did, once, which the rule cannot tell from a person.
    public static let switchedRepoMinGapSec: Double = activeGapCapSec

    /// How a slash command is written into a user record. `/clear` is the one slash command
    /// that is a session boundary (`cleared`) and a presence signal. UNTESTED ON REAL DATA:
    /// zero such records in the container corpus; the `cleared_twice` fixture pins the shape.
    public static let clearCommandMarker = "<command-name>/clear</command-name>"

    /// Bumped whenever the sessionization ALGORITHM changes, not just the threshold.
    ///
    /// 2: attended/autonomous split, `human_returned` and `day_boundary` cuts, presence
    ///    signals (docs/session-boundaries.md). The idle-gap rule itself is unchanged.
    /// 3: pools are folded by session lineage (a native session id lives in ONE pool, its
    ///    dominant one — MEASURED: one sitting whose shell `cd`d between home and the repo
    ///    uploaded as two overlapping sessions, the prompts in one and the commits in the
    ///    other); the idle gap is a fitted `SessionThresholds` with this file's fallback;
    ///    `cleared` and `switched_repo` end a session regardless of the gap.
    public static let sessionizerVersion = 3

    /// Seconds without a presence signal after which the agent is on its own.
    ///
    /// Two clocks run inside every session: `attended` while a human is evidently present,
    /// `autonomous` once the human has been quiet for longer than this. The agent is
    /// working; nobody is steering it. `active = attended + autonomous`, exactly as before
    /// — the split is what makes an overnight run describable instead of either a lie
    /// ("you worked 9 hours") or an omission ("nothing happened").
    ///
    /// UNMEASURED JUDGEMENT CALL. The one remote transcript this was developed against
    /// (30 min, n = 10 presence-to-presence intervals, p50 1m 33s, max 10m 53s) is the
    /// wrong shape to calibrate it: it has no overnight run in it. Recipe:
    /// `scripts/measure_boundaries.py ~/.claude/projects` prints the presence-interval
    /// distribution and a sensitivity grid; if a run you remember walking away from is
    /// still counted as attended, this is too high.
    public static let tauAutonomousSec: Double = 1800

    /// A presence signal after at least this much autonomy starts a NEW session.
    ///
    /// You kick off a long task at 23:00 and go to bed; if the agent is a loop that never
    /// stops and you sit down at 09:00 and type, that prompt is not the ninth hour of last
    /// night's sitting. The run is finalized at the instant of your prompt (`human_returned`)
    /// and a new session begins with it.
    ///
    /// Why 2 h and not `tauAutonomousSec`: a 45-minute autonomous stretch inside an
    /// afternoon — you asked for a refactor and went to lunch — is still your sitting when
    /// you come back to check it. Two hours of absence is a different thing from a long
    /// lunch. UNMEASURED JUDGEMENT CALL; the sensitivity grid in
    /// `scripts/measure_boundaries.py` shows how many sessions this rule creates at each
    /// candidate, and if it is creating them inside afternoons this is too low.
    public static let tauReturnSplitSec: Double = 7200

    /// The harness sentinel that begins the text of a user record written when the human
    /// pressed Escape or "stop". A record-shape check, never a heuristic on prompt text.
    public static let interruptPrefix = "[Request interrupted by user"

    /// Minimum interval between two uploads of the same live (open/idle) session, in
    /// seconds. A live snapshot is uploaded on any pass where its payload hash changed, at
    /// most this often; it is replaced in place when the session finalizes. UNMEASURED
    /// JUDGEMENT CALL: one tick of the daemon, which is the finest cadence anything
    /// upstream changes at.
    public static let liveUploadMinIntervalSec: Double = 60

    /// For a live session in an autonomous run, a checkpoint analysis runs every this many
    /// seconds so the phone can answer "what has it done so far" at 3 a.m. without waking
    /// anyone. UNMEASURED JUDGEMENT CALL: matched to `tauReturnSplitSec` so a run that is
    /// about to be cut by `human_returned` has a fresh checkpoint behind it.
    public static let analysisCheckpointSec: Double = 7200

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

    // `unattendedBgFraction` (0.80 over `sessionKind: "bg"`) was RETIRED, never wired up.
    // `bg` marks background TURNS, not absent PEOPLE — 2,508 records in the reference
    // corpus, too coarse and pointing at the wrong thing. `unattended` now means "zero
    // presence signals" (typed or remote-human prompt, interrupt, human file edit); see
    // `EventKind.isPresence` and docs/session-boundaries.md.

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

    /// The hour at which a new "day" begins, locally.
    ///
    /// FOUND BY RUNNING THE APP. At 00:20, mid-session, the menu bar read "0s active
    /// today" — because calendar midnight had rolled over and the running session was
    /// attributed to the previous date. Technically correct and completely wrong: nobody
    /// working at half past midnight thinks they have started a new day.
    ///
    /// A 4am boundary matches how the audience actually talks about their time ("I was up
    /// until 2 finishing it"), keeps a session that crosses midnight in one bucket, and
    /// stops a late-night sitting from silently breaking a streak by landing alone on a
    /// date with nothing else in it.
    ///
    /// This is a display-and-grouping rule only. Timestamps are never altered.
    public static let dayBoundaryHour = 4

    /// The local day a moment belongs to, honouring `dayBoundaryHour`.
    ///
    /// One implementation, used by ingest, derivation and the contribution graph alike —
    /// three different definitions of "day" would disagree about streaks in ways that are
    /// very hard to see and impossible to explain.
    public static func localDay(for date: Date, calendar: Calendar = .current) -> String {
        let shifted = date.addingTimeInterval(-Double(dayBoundaryHour) * 3600)
        let c = calendar.dateComponents([.year, .month, .day], from: shifted)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    public static func localDay(forTimestamp ts: Double, calendar: Calendar = .current) -> String {
        localDay(for: Date(timeIntervalSince1970: ts), calendar: calendar)
    }
}
