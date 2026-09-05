import BuilderModel
import Foundation

/// Why a session ended. `idleGap`, `cleared` and `switchedRepo` mean *the work stopped*
/// and are announced; the two cuts mean the human's sitting changed while the agent kept
/// going, and are not. See docs/session-boundaries.md (v3).
public enum EndReason: String, Sendable, CaseIterable {
    /// Nothing happened for the tau in force (`SessionThresholds.tau`, fallback
    /// `Tuning.tauSessionSec`). Announced.
    case idleGap = "idle_gap"
    /// A presence signal arrived after >= `Tuning.tauReturnSplitSec` of autonomy. The human
    /// came back to a still-running agent: a new sitting begins with that signal.
    case humanReturned = "human_returned"
    /// The 04:00 local day boundary fell inside a gap while the run was autonomous. An
    /// attended late night is never split.
    case dayBoundary = "day_boundary"
    /// The human typed `/clear` (v3). The conversation was ended on purpose, so the
    /// session ends at that record whatever the gap after it. Final where it stands,
    /// announced like silence. UNTESTED ON REAL DATA: zero `/clear` records in the
    /// container corpus; the `cleared_twice` fixture pins the shape.
    case cleared
    /// A human opened a NEW native session in a DIFFERENT pool at least
    /// `Tuning.switchedRepoMinGapSec` after this session's last record and before its next
    /// (v3). The sitting moved; this session ends, credited as an idle end would be.
    /// Final where it stands, announced like silence.
    case switchedRepo = "switched_repo"
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
        unattended && !isCut && activeSeconds >= Tuning.notableMinActiveSec
    }

    /// Ended by a boundary rule rather than by silence. Final the moment it is derived,
    /// and never announced: for `humanReturned` you are already here, for `dayBoundary`
    /// the work continues.
    public var isCut: Bool {
        endReason == .humanReturned || endReason == .dayBoundary
    }

    /// Ended by something the human did on purpose (v3): `/clear`, or opening a new
    /// session in another repo. Final the moment it is derived, like a cut — and
    /// announced, like silence, because the work in this session stopped.
    public var isStructuralEnd: Bool {
        endReason == .cleared || endReason == .switchedRepo
    }

    /// Nothing more can ever be added: a cut or a structural end. The lifecycle marks these
    /// final without waiting `tauSessionSec` for silence.
    public var isFinalOnDerivation: Bool { isCut || isStructuralEnd }
}

/// Turns a normalized event stream into build sessions.
///
/// The algorithm is: pool, fold by lineage, sort by time, walk the gaps. Silence cuts
/// exactly as it always has (at a tau that may now be fitted); `/clear` and a switch to
/// another repo end a session the human ended; `human_returned` and `day_boundary` cut a
/// run the human is not part of. The reference implementation is
/// `scripts/measure_boundaries.py`, and `BoundaryFixtureTests` holds this code to it on
/// the fixtures under `spec/fixtures/boundaries/`.
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
        /// A human session start in another pool at least this long after a session's
        /// last record ends it (`switchedRepo`).
        public var switchMinGap: Double

        /// `tau` is the FALLBACK unless the caller fitted one: pass
        /// `SessionThresholds.fitted(gaps: Sessionizer.presenceGaps(from:options:)).tau`.
        /// The deriver still passes the default today, so the Mac cuts at 900 s until it
        /// is wired; the ground-truth table in CLAUDE.md is stated at fixed taus and uses
        /// this default on purpose.
        public init(
            tau: Double = Tuning.tauSessionSec,
            activeGapCap: Double = Tuning.activeGapCapSec,
            pooling: Pooling = .repository,
            machineID: String = "local",
            tauAutonomous: Double = Tuning.tauAutonomousSec,
            tauReturnSplit: Double = Tuning.tauReturnSplitSec,
            calendar: Calendar = .current,
            switchMinGap: Double = Tuning.switchedRepoMinGapSec
        ) {
            self.tau = tau
            self.activeGapCap = activeGapCap
            self.pooling = pooling
            self.machineID = machineID
            self.tauAutonomous = tauAutonomous
            self.tauReturnSplit = tauReturnSplit
            self.calendar = calendar
            self.switchMinGap = switchMinGap
        }
    }

    /// Pools after the lineage fold, each sorted by (ts, ordinal); the content-block
    /// events that share a record with a pooled one; and the instants a HUMAN opened a new
    /// native session in each pool.
    private struct Pooled {
        var pools: [String: [(Int, NormalizedEvent)]]
        var extras: [String: [Int]]
        var humanStarts: [String: [Double]]
    }

    /// Pool, then fold by session lineage, then find the human session starts.
    ///
    /// THE FOLD (v3). A session is one human's sitting, and the repository is an attribute
    /// of it, not a partition key. Claude Code stamps the shell's CURRENT cwd on every
    /// record, so a conversation whose shell `cd`s between home and a repo scatters across
    /// two keys under any per-event pooling — MEASURED on the container corpus: one
    /// 2,231-record session held 332 runs of alternating cwd, all 15 human prompts under
    /// `/home/user` and 833 of the assistant's records under the repository, with a median
    /// gap of 0.4 s at each change. Pooled per record that sitting became two overlapping
    /// sessions, one with the prompts and zero commits and one with the commits and
    /// "0 prompts typed" — the plausible wrong number. (The reference machine shows the
    /// same shape in miniature: 5 distinct cwds in one 30-minute transcript.) So every
    /// record of one native session id goes to that id's DOMINANT key: by record count,
    /// ties to the key of the id's earliest record, then the smaller key string — the
    /// same three steps as `measure_boundaries.fold_by_session_lineage`. Records with no
    /// session id keep their own key. Two conversations back to back under one key still
    /// share a pool: that rule is unchanged from v1.
    ///
    /// HUMAN STARTS. A native session id is new at its earliest record anywhere; the start
    /// counts only if that record is a presence signal — a `claude -p` run stamps its
    /// prompt `sdk` with no human origin (MEASURED: 7 of 7 headless runs in the container
    /// corpus), and a robot starting in another repo says nothing about where the person is.
    private static func pool(_ events: [NormalizedEvent], options: Options) -> Pooled {
        var seenRecords = Set<String>()
        var extras: [String: [Int]] = [:]
        var keyed: [(key: String, index: Int, event: NormalizedEvent)] = []
        for (i, e) in events.enumerated() {
            guard e.ts != nil else { continue }
            let recordKey = "\(e.sourceID)|\(recordBaseID(e))"
            if seenRecords.insert(recordKey).inserted {
                keyed.append((poolKey(e, options), i, e))
            } else {
                extras[recordKey, default: []].append(i)
            }
        }

        var counts: [String: [String: Int]] = [:]
        var earliest: [String: (ts: Double, key: String)] = [:]
        for k in keyed {
            guard let sid = k.event.nativeSessionID, let ts = k.event.ts else { continue }
            counts[sid, default: [:]][k.key, default: 0] += 1
            if let cur = earliest[sid] {
                if ts < cur.ts || (ts == cur.ts && k.key < cur.key) { earliest[sid] = (ts, k.key) }
            } else {
                earliest[sid] = (ts, k.key)
            }
        }
        var home: [String: String] = [:]
        for (sid, byKey) in counts {
            let best = byKey.values.max() ?? 0
            let candidates = byKey.filter { $0.value == best }.map { $0.key }.sorted()
            let firstKey = earliest[sid]?.key ?? candidates[0]
            home[sid] = candidates.contains(firstKey) ? firstKey : candidates[0]
        }

        var pools: [String: [(Int, NormalizedEvent)]] = [:]
        for k in keyed {
            let key = k.event.nativeSessionID.flatMap { home[$0] } ?? k.key
            pools[key, default: []].append((k.index, k.event))
        }
        for key in pools.keys {
            pools[key]!.sort { a, b in
                let at = a.1.ts ?? 0
                let bt = b.1.ts ?? 0
                return at == bt ? a.1.ordinal < b.1.ordinal : at < bt
            }
        }

        // Earliest record of each native session id, over the folded pools (sorted, so the
        // first hit per id is its earliest by (ts, ordinal)).
        var first: [String: (ts: Double, key: String, presence: Bool)] = [:]
        for key in pools.keys.sorted() {
            for (_, e) in pools[key]! {
                guard let sid = e.nativeSessionID, let ts = e.ts else { continue }
                if let cur = first[sid], cur.ts <= ts { continue }
                first[sid] = (ts, key, e.kind.isPresence)
            }
        }
        var starts: [String: [Double]] = [:]
        for key in pools.keys { starts[key] = [] }
        for (_, f) in first where f.presence {
            starts[f.key, default: []].append(f.ts)
        }
        for key in starts.keys { starts[key]!.sort() }

        return Pooled(pools: pools, extras: extras, humanStarts: starts)
    }

    /// The sample `SessionThresholds` is fitted on: every strictly positive interval between
    /// consecutive presence signals in a pool, across idle gaps too (that is where the
    /// between-sitting mode lives), over every pool. Mirrors `measure_boundaries.presence_gaps`.
    public static func presenceGaps(
        from events: [NormalizedEvent], options: Options = Options()
    ) -> [Double] {
        var out: [Double] = []
        for (_, sorted) in pool(events, options: options).pools {
            var ts: [Double] = []
            for (_, e) in sorted where e.kind.isPresence {
                if let t = e.ts { ts.append(t) }
            }
            ts.sort()
            if ts.count < 2 { continue }
            for i in 1..<ts.count {
                let g = ts[i] - ts[i - 1]
                if g > 0 { out.append(g) }
            }
        }
        return out
    }

    /// The first human session start in another pool at or after `lo` and before `hi`.
    private static func foreignStart(in starts: [Double], notBefore lo: Double, before hi: Double) -> Double? {
        guard let f = starts.first(where: { $0 >= lo }) else { return nil }
        return f < hi ? f : nil
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
        let pooled = pool(events, options: options)
        let extras = pooled.extras

        var out: [DetectedSession] = []

        for (key, sorted) in pooled.pools {
            // Rule 2 (v3) reads the human session starts of every OTHER pool.
            var foreign: [Double] = []
            for (otherKey, starts) in pooled.humanStarts where otherKey != key {
                foreign.append(contentsOf: starts)
            }
            foreign.sort()
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
            // When the current autonomous stretch began, for a pool that has had no
            // presence signal at all: rule 2 measures autonomy from here in that case.
            var runStart: Double? = firstEntry.1.kind.isPresence ? nil : firstTS

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

                // Rule 0 (v3): the previous record was a `/clear`. The human ended the
                // conversation on purpose, so the session ends there whatever the gap —
                // silence after it is silence AFTER the stop, not the stop itself.
                // Rule 1: idle gap. Unchanged, and still the boundary that ends 99.8% of
                // sessions (MEASURED: 997 of 48,095 gaps exceed 60 s; p99.9 is 32.5 min).
                // Rule 2 (v3): a human opened a new session in another pool at least
                // `switchMinGap` after our last record and before our next. The sitting
                // moved; this session ends, credited exactly as an idle end would be.
                // All three credit the boundary gap the same way and differ in name only,
                // so the arithmetic below is shared and cannot drift between them.
                let prevIsClear = sorted[i - 1].1.kind == .clear
                let switchAt = foreign.isEmpty
                    ? nil
                    : foreignStart(in: foreign, notBefore: prevTS + options.switchMinGap, before: ts)
                if prevIsClear || gap > options.tau || switchAt != nil {
                    let reason: EndReason
                    var boundaryCredit = credit
                    if prevIsClear {
                        reason = .cleared
                    } else if gap > options.tau {
                        reason = .idleGap
                    } else {
                        reason = .switchedRepo
                        boundaryCredit = min(switchAt! - prevTS, options.activeGapCap)
                    }
                    cur.credit(boundaryCredit, autonomous: autonomous)
                    cur.endedAt = prevTS + boundaryCredit
                    cut(cur, endIndex: i, reason: reason)
                    cur = Accumulator(firstIndex: i, startedAt: ts, endedAt: ts)
                    lastPresence = rec.kind.isPresence ? ts : nil
                    runStart = rec.kind.isPresence ? nil : ts
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
                    if runStart == nil { runStart = prevTS }
                    if rec.kind.isPresence {
                        lastPresence = ts
                        runStart = nil
                    }
                    continue
                }

                // Rule 2: the human returned after a long autonomous run. The run is
                // finalized at the instant of the presence signal, credited for the gap
                // like any other end, and a new sitting begins with the signal.
                // A run that never had a presence signal measures its autonomy from its
                // own start, so the first human to sit down at a robot opens a new session.
                let autonomyLen: Double? = sincePresence ?? runStart.map { prevTS - $0 }
                if rec.kind.isPresence && autonomous,
                   let since = autonomyLen, since >= options.tauReturnSplit {
                    cur.credit(credit, autonomous: true)
                    cur.endedAt = prevTS + credit
                    cut(cur, endIndex: i, reason: .humanReturned)
                    cur = Accumulator(firstIndex: i, startedAt: ts, endedAt: ts)
                    lastPresence = ts
                    runStart = nil
                    continue
                }

                // Ordinary continuation: credit the gap to whichever clock is running.
                cur.credit(credit, autonomous: autonomous)
                cur.endedAt = ts
                if autonomous && runStart == nil { runStart = prevTS }
                if rec.kind.isPresence {
                    lastPresence = ts
                    runStart = nil
                }
            }

            // The last session in the pool is the live one — unless its last record is a
            // `/clear`, in which case it ended there and is final. No trailing credit
            // either way: there is no next record to measure a gap against.
            let lastIsClear = sorted[sorted.count - 1].1.kind == .clear
            cut(cur, endIndex: sorted.count, reason: lastIsClear ? .cleared : .stillRunning)
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
