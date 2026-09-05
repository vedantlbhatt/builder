import BuilderModel
import Foundation

/// Why a session ended. Only `idleGap` means *the work stopped*; the two cuts mean the
/// human's sitting changed while the agent kept going. See docs/session-boundaries.md.
public enum EndReason: String, Sendable, CaseIterable {
    /// Nothing happened for `Tuning.tauSessionSec`. The only end that fires a notification.
    case idleGap = "idle_gap"
    /// A presence signal arrived after >= `Tuning.tauReturnSplitSec` of autonomy. The human
    /// came back to a still-running agent: a new sitting begins with that signal.
    case humanReturned = "human_returned"
    /// The 04:00 local day boundary fell inside a gap while the run was autonomous. An
    /// attended late night is never split.
    case dayBoundary = "day_boundary"
    /// The last session in its pool. This IS the live session.
    case stillRunning = "still_running"
}

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
    ///
    /// Always `attendedSeconds + autonomousSeconds`.
    public var activeSeconds: Double

    /// Active time while a human was evidently present: within `Tuning.tauAutonomousSec`
    /// of the last presence signal. This, never `activeSeconds`, decides records.
    public var attendedSeconds: Double

    /// Active time after the human had been quiet for longer than `Tuning.tauAutonomousSec`.
    /// The agent was working; nobody was steering it.
    public var autonomousSeconds: Double

    public var idleSeconds: Double { max(0, wallSeconds - activeSeconds) }

    /// Indices into the source array, in time order.
    public var eventIndices: [Int]

    public var eventCount: Int { eventIndices.count }
    public var meaningfulEventCount: Int
    public var promptCount: Int

    /// Presence signals: prompts (typed or remote-human), interrupts, human file edits.
    public var presenceCount: Int
    public var endReason: EndReason
    public var firstEventUID: String

    public init(
        clientSessionID: String, harness: Harness, poolKey: String,
        startedAt: Double, endedAt: Double,
        activeSeconds: Double, attendedSeconds: Double, autonomousSeconds: Double,
        eventIndices: [Int], meaningfulEventCount: Int, promptCount: Int, presenceCount: Int,
        endReason: EndReason, firstEventUID: String
    ) {
        self.clientSessionID = clientSessionID
        self.harness = harness
        self.poolKey = poolKey
        self.startedAt = startedAt
        self.endedAt = endedAt
        self.activeSeconds = activeSeconds
        self.attendedSeconds = attendedSeconds
        self.autonomousSeconds = autonomousSeconds
        self.eventIndices = eventIndices
        self.meaningfulEventCount = meaningfulEventCount
        self.promptCount = promptCount
        self.presenceCount = presenceCount
        self.endReason = endReason
        self.firstEventUID = firstEventUID
    }

    /// Counts toward hours, contribution graph and streaks.
    public var counted: Bool {
        activeSeconds >= Tuning.countedMinActiveSec
            || meaningfulEventCount >= Tuning.countedMinMeaningfulEvents
    }

    /// Nobody was at the keyboard: ZERO presence signals over a span long enough to be
    /// notable.
    ///
    /// FOUND BY RUNNING THIS ON REAL DATA. The longest session in the corpus — 5h 40m
    /// active — had **zero typed prompts**: a long autonomous run in an automation repo.
    /// It would have become the headline "longest session" personal record, which is a
    /// record for the machine, not for the person.
    ///
    /// Redefined from "zero prompts" to "zero presence signals" (typed or remote-human
    /// prompt, interrupt, human file edit). A session with one kickoff prompt and eight
    /// autonomous hours is NOT unattended — the person did start it — but its record
    /// eligibility uses `attendedSeconds`, never `activeSeconds`. An unattended session
    /// still counts toward hours — the work did happen — but it cannot win a record,
    /// extend a streak, or fire a "session finished" alert; it fires "agent run finished".
    public var unattended: Bool {
        presenceCount == 0 && activeSeconds >= Tuning.notableMinActiveSec
    }

    /// Eligible to become a card, a personal record, or a "session finished" notification.
    /// Deliberately a higher bar than `counted`: a four-minute question should add its
    /// minutes to your week without generating an artifact. Judged on ATTENDED time, so a
    /// kickoff prompt followed by eight autonomous hours scores its attended minutes.
    public var notable: Bool {
        counted && attendedSeconds >= Tuning.notableMinActiveSec && !unattended
    }

    /// An unattended run that stopped on its own: the "Agent run finished" notification.
    /// It is the moment you want to look at what happened. Not a record, not a streak.
    public var runFinished: Bool {
        unattended && endReason == .idleGap && activeSeconds >= Tuning.notableMinActiveSec
    }

    /// Ended by a boundary rule rather than by silence. Final the moment it is derived,
    /// and never announced: for `humanReturned` you are already here, for `dayBoundary`
    /// the work continues.
    public var isCut: Bool {
        endReason == .humanReturned || endReason == .dayBoundary
    }
}

/// Turns a normalized event stream into build sessions.
///
/// The algorithm is: pool, sort by time, walk the gaps. Rule 1 cuts on silence exactly as
/// it always has; rules 2 and 3 cut a run the human is not part of. The reference
/// implementation is `scripts/measure_boundaries.py`, and `BoundaryFixtureTests` holds
/// this code to it on the fixtures under `spec/fixtures/boundaries/`.
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
        /// Quiet after the last presence signal before the agent is "on its own".
        public var tauAutonomous: Double
        /// Autonomy after which a presence signal starts a NEW session.
        public var tauReturnSplit: Double
        /// Supplies the local time zone for the `Tuning.dayBoundaryHour` rule.
        public var calendar: Calendar

        public init(
            tau: Double = Tuning.tauSessionSec,
            activeGapCap: Double = Tuning.activeGapCapSec,
            pooling: Pooling = .repository,
            machineID: String = "local",
            tauAutonomous: Double = Tuning.tauAutonomousSec,
            tauReturnSplit: Double = Tuning.tauReturnSplitSec,
            calendar: Calendar = .current
        ) {
            self.tau = tau
            self.activeGapCap = activeGapCap
            self.pooling = pooling
            self.machineID = machineID
            self.tauAutonomous = tauAutonomous
            self.tauReturnSplit = tauReturnSplit
            self.calendar = calendar
        }
    }

    /// The session being accumulated while walking one pool's gaps.
    private struct Accumulator {
        /// Index into the sorted pool of the first record. The session's identity.
        var firstIndex: Int
        /// A record's timestamp, or the 04:00 boundary after a `dayBoundary` cut.
        var startedAt: Double
        var endedAt: Double
        var active: Double = 0
        var attended: Double = 0
        var autonomous: Double = 0

        mutating func credit(_ seconds: Double, autonomous isAutonomous: Bool) {
            active += seconds
            if isAutonomous {
                autonomous += seconds
            } else {
                attended += seconds
            }
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
        // arithmetic is per record. The record's kind — prompt, interrupt, presence — is
        // read from its first event, which is the prompt or interrupt event itself on
        // every user record that is one.
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
            guard let firstEntry = sorted.first, let firstTS = firstEntry.1.ts else { continue }

            func cut(_ acc: Accumulator, endIndex: Int, reason: EndReason) {
                let slice = Array(sorted[acc.firstIndex..<endIndex])
                guard var s = build(slice: slice, key: key, accumulator: acc,
                                    endReason: reason, options: options) else { return }
                // Fold each record's remaining content blocks back in, so counts
                // downstream see every event even though boundaries used records.
                for (_, e) in slice {
                    let rk = "\(e.sourceID)|\(recordBaseID(e))"
                    if let more = extras[rk] { s.eventIndices.append(contentsOf: more) }
                }
                s.eventIndices.sort()
                out.append(s)
            }

            // Credit for every gap is computed over the POOL, not within the session.
            //
            // The gap after a session's final record is capped and credited to that
            // session, exactly like any other gap, and `endedAt` is extended by the same
            // amount. Without this, the boundary gap is silently discarded, and merging
            // two sessions recovers up to `activeGapCap` seconds — which makes total
            // active time depend on where sessions are cut. Measured before this fix:
            // total active moved by up to 4.5 hours across the threshold range, quietly
            // rewriting history whenever the threshold was tuned.
            //
            // Extending `endedAt` by the trailing credit is what keeps active <= elapsed:
            // "2h 5m active of 2h 0m elapsed" is nonsense on a card, and the server's
            // sanity gate rejects it. The extension is at most `activeGapCap`, and the
            // next session begins at least the gap later, so sessions never overlap.
            var cur = Accumulator(firstIndex: 0, startedAt: firstTS, endedAt: firstTS)
            var lastPresence: Double? = firstEntry.1.kind.isPresence ? firstTS : nil

            for i in 1..<max(sorted.count, 1) where sorted.count > 1 {
                let prevTS = sorted[i - 1].1.ts!
                let rec = sorted[i].1
                let ts = rec.ts!
                let gap = ts - prevTS
                let credit = min(gap, options.activeGapCap)

                // Is the human absent at the START of this gap?
                let sincePresence: Double? = lastPresence.map { prevTS - $0 }
                let autonomous: Bool
                if let since = sincePresence {
                    autonomous = since > options.tauAutonomous
                } else {
                    autonomous = true
                }

                // Rule 1: idle gap. Unchanged, and still the boundary that ends 99.8% of
                // sessions (MEASURED: 997 of 48,095 gaps exceed 60 s; p99.9 is 32.5 min).
                if gap > options.tau {
                    cur.credit(credit, autonomous: autonomous)
                    cur.endedAt = prevTS + credit
                    cut(cur, endIndex: i, reason: .idleGap)
                    cur = Accumulator(firstIndex: i, startedAt: ts, endedAt: ts)
                    lastPresence = rec.kind.isPresence ? ts : nil
                    continue
                }

                // Rule 3: the day boundary falls in this gap while autonomous. `<=`: a
                // record stamped exactly 04:00:00 begins the new day. MEASURED on the
                // robot fixture: a 60-second cadence from a round hour lands a record on
                // the boundary itself, and a strict comparison never split a 30-hour run.
                let boundary = nextDayBoundary(after: prevTS, calendar: options.calendar)
                if autonomous && boundary <= ts {
                    // Credit up to the boundary to the old session, the remainder of the
                    // capped gap to the new one. The old session ends AT the boundary.
                    let before = min(boundary - prevTS, options.activeGapCap)
                    cur.credit(before, autonomous: true)
                    cur.endedAt = boundary
                    cut(cur, endIndex: i, reason: .dayBoundary)
                    cur = Accumulator(firstIndex: i, startedAt: boundary, endedAt: ts)
                    let after: Double =
                        gap <= options.activeGapCap
                        ? max(0, min(ts - boundary, options.activeGapCap - before))
                        : 0
                    cur.credit(after, autonomous: true)
                    // `lastPresence` carries over: the human is still absent.
                    if rec.kind.isPresence { lastPresence = ts }
                    continue
                }

                // Rule 2: the human returned after a long autonomous run. The run is
                // finalized at the instant of the presence signal, credited for the gap
                // like any other end, and a new sitting begins with the signal.
                if rec.kind.isPresence && autonomous,
                   let since = sincePresence, since >= options.tauReturnSplit {
                    cur.credit(credit, autonomous: true)
                    cur.endedAt = prevTS + credit
                    cut(cur, endIndex: i, reason: .humanReturned)
                    cur = Accumulator(firstIndex: i, startedAt: ts, endedAt: ts)
                    lastPresence = ts
                    continue
                }

                // Ordinary continuation: credit the gap to whichever clock is running.
                cur.credit(credit, autonomous: autonomous)
                cur.endedAt = ts
                if rec.kind.isPresence { lastPresence = ts }
            }

            // The last session in the pool is the live one. No trailing credit: there is
            // no next record to measure a gap against.
            cut(cur, endIndex: sorted.count, reason: .stillRunning)
        }

        return out.sorted { $0.startedAt < $1.startedAt }
    }

    /// The first `Tuning.dayBoundaryHour`:00 local STRICTLY after `ts`, as unix seconds.
    ///
    /// Built from year/month/day components plus the hour, never by adding 24 h: across
    /// the spring-forward night 04:00 arrives 23 real hours after the previous 04:00, and
    /// on the fall-back night 25. Same definition as `Tuning.localDay`, read from the same
    /// constant — three different definitions of "day" would disagree about streaks in
    /// ways that are very hard to see and impossible to explain.
    public static func nextDayBoundary(after ts: Double, calendar: Calendar) -> Double {
        func boundary(onDayOf d: Date) -> Date? {
            var c = calendar.dateComponents([.year, .month, .day], from: d)
            c.hour = Tuning.dayBoundaryHour
            c.minute = 0
            c.second = 0
            c.nanosecond = 0
            return calendar.date(from: c)
        }
        let date = Date(timeIntervalSince1970: ts)
        guard let today = boundary(onDayOf: date) else { return ts + 86400 }
        if today.timeIntervalSince1970 > ts { return today.timeIntervalSince1970 }
        guard let tomorrow = calendar.date(byAdding: .day, value: 1, to: today),
              let next = boundary(onDayOf: tomorrow)
        else { return today.timeIntervalSince1970 + 86400 }
        return next.timeIntervalSince1970
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
        accumulator acc: Accumulator,
        endReason: EndReason,
        options: Options
    ) -> DetectedSession? {
        guard let first = slice.first else { return nil }

        let meaningful = slice.filter { $0.1.kind.isMeaningful }.count
        let prompts = slice.filter { $0.1.kind == .prompt }.count
        let presence = slice.filter { $0.1.kind.isPresence }.count

        return DetectedSession(
            clientSessionID: Hashing.clientSessionID(
                harness: first.1.harness,
                machineID: options.machineID,
                firstEventUID: first.1.eventUID
            ),
            harness: first.1.harness,
            poolKey: key,
            startedAt: acc.startedAt,
            endedAt: acc.endedAt,
            activeSeconds: acc.active,
            attendedSeconds: acc.attended,
            autonomousSeconds: acc.autonomous,
            eventIndices: slice.map(\.0),
            meaningfulEventCount: meaningful,
            promptCount: prompts,
            presenceCount: presence,
            endReason: endReason,
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
