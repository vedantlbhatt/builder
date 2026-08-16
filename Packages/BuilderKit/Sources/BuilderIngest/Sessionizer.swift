import BuilderModel
import Foundation

/// A detected build session: a contiguous stretch of work in one place.
public struct DetectedSession: Sendable, Equatable {
    public var clientSessionID: String
    public var harness: Harness
    public var poolKey: String
    public var startedAt: Double
    public var endedAt: Double

    /// Wall clock, first event to last.
    public var wallSeconds: Double { endedAt - startedAt }

    /// Time you were actually working: the sum of inter-event gaps, each capped at
    /// `Tuning.activeGapCapSec`.
    ///
    /// This is "moving time" to `wallSeconds`'s "elapsed time", and it is the number the
    /// card leads with. It is INVARIANT to `tauSessionSec` by construction — the cap is
    /// 120s and the session threshold is 900s, so changing where sessions are cut cannot
    /// move this figure. That matters because otherwise retuning the boundary silently
    /// rewrites every historical total.
    public var activeSeconds: Double

    public var idleSeconds: Double { max(0, wallSeconds - activeSeconds) }

    /// Indices into the source array, in time order.
    public var eventIndices: [Int]

    public var eventCount: Int { eventIndices.count }
    public var meaningfulEventCount: Int
    public var promptCount: Int
    public var firstEventUID: String

    public init(
        clientSessionID: String, harness: Harness, poolKey: String,
        startedAt: Double, endedAt: Double, activeSeconds: Double,
        eventIndices: [Int], meaningfulEventCount: Int, promptCount: Int, firstEventUID: String
    ) {
        self.clientSessionID = clientSessionID
        self.harness = harness
        self.poolKey = poolKey
        self.startedAt = startedAt
        self.endedAt = endedAt
        self.activeSeconds = activeSeconds
        self.eventIndices = eventIndices
        self.meaningfulEventCount = meaningfulEventCount
        self.promptCount = promptCount
        self.firstEventUID = firstEventUID
    }

    /// Counts toward hours, contribution graph and streaks.
    public var counted: Bool {
        activeSeconds >= Tuning.countedMinActiveSec
            || meaningfulEventCount >= Tuning.countedMinMeaningfulEvents
    }

    /// Nobody was at the keyboard.
    ///
    /// FOUND BY RUNNING THIS ON REAL DATA. The longest session in the corpus — 5h 40m
    /// active — had **zero typed prompts**: a long autonomous run in an automation repo.
    /// It would have become the headline "longest session" personal record, which is a
    /// record for the machine, not for the person.
    ///
    /// A session with no typed prompt at all, over a span long enough to be notable, is
    /// unattended by definition. It still counts toward hours — the work did happen — but
    /// it cannot win a record, extend a streak, or trigger a "you just finished a session"
    /// notification, because there is nobody to congratulate.
    public var unattended: Bool {
        promptCount == 0 && activeSeconds >= Tuning.notableMinActiveSec
    }

    /// Eligible to become a card, a personal record, or a "session finished" notification.
    /// Deliberately a higher bar than `counted`: a four-minute question should add its
    /// minutes to your week without generating an artifact.
    public var notable: Bool {
        counted && activeSeconds >= Tuning.notableMinActiveSec && !unattended
    }
}

/// Turns a normalized event stream into build sessions.
///
/// The whole algorithm is: pool, sort by time, cut on gaps. Every subtlety is in what
/// "pool" means and in what is allowed to count as an event.
public enum Sessionizer {

    /// How events are grouped before cutting.
    public enum Pooling: Sendable {
        /// Group by the repository worked in. THE PRODUCT DEFAULT.
        ///
        /// Not by file: MEASURED, resume appends to the SAME transcript file, so 43 files
        /// contain an internal gap over an hour, 22 contain one over twelve hours, and the
        /// longest single file spans 121.3 hours. A file is not a session.
        ///
        /// Not by `sessionId` either: two Claude Code sessions run back to back in the same
        /// repo are one sitting from the user's point of view, and treating them as two
        /// halves the length of every session on the card.
        case repository

        /// Group by the harness's own session id. Used for sources where no repo can be
        /// resolved at all.
        case nativeSession

        /// Group by an explicit key. Used by the regression suite to reproduce exactly the
        /// pooling that produced the published ground-truth counts (all events under one
        /// Claude Code project directory).
        case explicit(@Sendable (NormalizedEvent) -> String)
    }

    public struct Options: Sendable {
        public var tau: Double
        public var activeGapCap: Double
        public var pooling: Pooling
        public var machineID: String

        public init(
            tau: Double = Tuning.tauSessionSec,
            activeGapCap: Double = Tuning.activeGapCapSec,
            pooling: Pooling = .repository,
            machineID: String = "local"
        ) {
            self.tau = tau
            self.activeGapCap = activeGapCap
            self.pooling = pooling
            self.machineID = machineID
        }
    }

    /// Cut an event stream into sessions.
    ///
    /// - Events with no timestamp are EXCLUDED from boundary logic entirely. MEASURED:
    ///   ~24,151 of 108,504 records carry no timestamp, and every one of them is
    ///   bookkeeping — `mode`, `permission-mode`, `file-history-snapshot`, `ai-title` and
    ///   friends. None carries usage, a tool call, or a typed prompt. They are never
    ///   imputed: file order is not time order (2,472 adjacent inversions measured), so
    ///   interpolating between neighbours can run the clock backwards.
    ///
    /// - Sorting is by timestamp, never by line order, for the same reason.
    ///
    /// - Sidechain events do not open sessions. Subagent transcripts inherit the parent's
    ///   `sessionId`, so they belong to whatever session their parent is in.
    public static func sessions(
        from events: [NormalizedEvent],
        options: Options = Options()
    ) -> [DetectedSession] {

        // Gap analysis runs over transcript RECORDS, not over normalized events.
        //
        // Two reasons, and they point the same way. First, one record routinely becomes
        // several events — an assistant turn with thinking, text and two tool calls is
        // four events sharing one timestamp — and feeding those to a gap analysis injects
        // runs of zero-length gaps that drag every percentile down and make the idle
        // structure unreadable. Second, a record is genuinely one moment: the timestamp
        // describes when the record was written, not four separate instants.
        //
        // Every event still belongs to the resulting session; only the boundary
        // arithmetic is per record.
        var pools: [String: [(Int, NormalizedEvent)]] = [:]
        var seenRecords = Set<String>()
        var extras: [String: [Int]] = [:]  // record key -> other event indices

        for (i, e) in events.enumerated() {
            guard e.ts != nil else { continue }
            let recordKey = "\(e.sourceID)|\(recordBaseID(e))"
            if seenRecords.insert(recordKey).inserted {
                pools[poolKey(e, options), default: []].append((i, e))
            } else {
                extras[recordKey, default: []].append(i)
            }
        }

        var out: [DetectedSession] = []

        for (key, entries) in pools {
            let sorted = entries.sorted { a, b in
                let at = a.1.ts ?? 0
                let bt = b.1.ts ?? 0
                return at == bt ? a.1.ordinal < b.1.ordinal : at < bt
            }
            guard !sorted.isEmpty else { continue }

            // Credit for each record is computed over the POOL, not within the session.
            //
            // The gap after a session's final record is capped and credited to that
            // session, exactly like any other gap. Without this, the boundary gap is
            // silently discarded, and merging two sessions recovers up to `activeGapCap`
            // seconds — which makes total active time depend on where sessions are cut.
            // Measured before this fix: total active moved by up to 4.5 hours across the
            // threshold range, quietly rewriting history whenever the threshold was tuned.
            //
            // It is also the more honest reading: after your last recorded event you kept
            // working for a moment before stopping. That is what a cap is for.
            var creditPerIndex = [Double](repeating: 0, count: sorted.count)
            if sorted.count > 1 {
                for i in 0..<(sorted.count - 1) {
                    creditPerIndex[i] = min(sorted[i + 1].1.ts! - sorted[i].1.ts!, options.activeGapCap)
                }
            }

            var runStart = 0
            for i in 1...max(sorted.count, 1) {
                let isLast = i == sorted.count
                let gap = isLast ? Double.infinity : (sorted[i].1.ts! - sorted[i - 1].1.ts!)

                if gap > options.tau || isLast {
                    let end = min(i, sorted.count)
                    let slice = Array(sorted[runStart..<end])
                    let credit = (runStart..<end).reduce(0.0) { $0 + creditPerIndex[$1] }
                    // The session ends when activity ceased, not at the instant of its
                    // last log line — otherwise active time can exceed elapsed time, and
                    // "2h 5m active of 2h 0m elapsed" is nonsense on a card. The extension
                    // is at most `activeGapCap` and the next session begins at least `tau`
                    // later, so sessions can never overlap.
                    let trailing = end > 0 ? creditPerIndex[end - 1] : 0
                    if !slice.isEmpty,
                       var s = build(
                        slice: slice, key: key, activeSeconds: credit,
                        trailingSeconds: trailing, options: options) {
                        // Fold each record's remaining content blocks back in, so counts
                        // downstream see every event even though boundaries used records.
                        for (_, e) in slice {
                            let rk = "\(e.sourceID)|\(recordBaseID(e))"
                            if let more = extras[rk] { s.eventIndices.append(contentsOf: more) }
                        }
                        s.eventIndices.sort()
                        out.append(s)
                    }
                    runStart = i
                }
                if isLast { break }
            }
        }

        return out.sorted { $0.startedAt < $1.startedAt }
    }

    /// A record's identity with any content-block suffix (`#tu0`, `#th1`) removed.
    public static func recordBaseID(_ e: NormalizedEvent) -> String {
        guard let id = e.nativeEventID else { return "ord\(e.ordinal)" }
        guard let hash = id.firstIndex(of: "#") else { return id }
        return String(id[id.startIndex..<hash])
    }

    private static func poolKey(_ e: NormalizedEvent, _ o: Options) -> String {
        switch o.pooling {
        case .repository:
            // `.cwd` varies within a file, so this is per-event, not per-file. Repo
            // identity proper (normalized origin URL, worktrees folded together) is
            // resolved during ingest and supplied via `.explicit` there; this is the
            // fallback when nothing has resolved yet.
            return "\(e.harness.rawValue)|\(e.cwd ?? e.nativeSessionID ?? "unknown")"
        case .nativeSession:
            return "\(e.harness.rawValue)|\(e.nativeSessionID ?? "unknown")"
        case .explicit(let f):
            return f(e)
        }
    }

    private static func build(
        slice: [(Int, NormalizedEvent)],
        key: String,
        activeSeconds active: Double,
        trailingSeconds trailing: Double,
        options: Options
    ) -> DetectedSession? {
        guard let first = slice.first, let last = slice.last else { return nil }
        let start = first.1.ts!
        let end = last.1.ts! + trailing

        let meaningful = slice.filter { $0.1.kind.isMeaningful }.count
        let prompts = slice.filter { $0.1.kind == .prompt }.count

        return DetectedSession(
            clientSessionID: Hashing.clientSessionID(
                harness: first.1.harness,
                machineID: options.machineID,
                firstEventUID: first.1.eventUID
            ),
            harness: first.1.harness,
            poolKey: key,
            startedAt: start,
            endedAt: end,
            activeSeconds: active,
            eventIndices: slice.map(\.0),
            meaningfulEventCount: meaningful,
            promptCount: prompts,
            firstEventUID: first.1.eventUID
        )
    }

    // MARK: - Measurement helper

    /// Total of every inter-event gap strictly below `tau`, in hours.
    ///
    /// This is a DIFFERENT QUANTITY from `DetectedSession.activeSeconds` and exists only
    /// to reproduce the published exploration figures (80.05 h at tau=300, 98.98 h at 900,
    /// 110.76 h at 1800, 125.46 h at 3600). Because it has no cap, it grows with `tau` —
    /// a 14-minute coffee break counts in full at tau=900. That is precisely why the
    /// product does not use it: the number on your card would change every time the
    /// session threshold was retuned.
    public static func sumOfSubThresholdGapsHours(
        from events: [NormalizedEvent],
        tau: Double,
        pooling: Pooling
    ) -> Double {
        var pools: [String: [Double]] = [:]
        var seen = Set<String>()
        let opts = Options(tau: tau, pooling: pooling)
        for e in events {
            guard let ts = e.ts else { continue }
            guard seen.insert("\(e.sourceID)|\(recordBaseID(e))").inserted else { continue }
            pools[poolKey(e, opts), default: []].append(ts)
        }
        var total: Double = 0
        for (_, var times) in pools {
            times.sort()
            for i in 1..<max(times.count, 1) where times.count > 1 {
                let gap = times[i] - times[i - 1]
                if gap <= tau { total += gap }
            }
        }
        return total / 3600.0
    }
}
