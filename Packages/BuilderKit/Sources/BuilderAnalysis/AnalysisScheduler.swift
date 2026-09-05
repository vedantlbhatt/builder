import BuilderIngest
import BuilderModel
import BuilderParse
import BuilderSQLite
import Foundation

/// Everything needed to digest and analyse one session, resolved from the durable store
/// while the caller still holds the `DetectedSession`.
///
/// The session's events are NOT enough on their own: `raw_event` never stores prompt
/// text, so the digest re-reads the transcript files. Sources are found the way the
/// deriver pooled them — same harness, same repo (or cwd), timestamps inside the session
/// — and mapped to paths through `ingest_watermark`.
public struct AnalysisJob: Sendable {
    public let clientSessionID: String
    public let harness: Harness
    public let startedAt: Double
    public let endedAt: Double
    /// A live mid-run reading rather than the final one.
    public let checkpoint: Bool
    public let meta: SessionDigest.Meta
    public let transcripts: [String]

    public init(
        clientSessionID: String, harness: Harness, startedAt: Double, endedAt: Double,
        checkpoint: Bool, meta: SessionDigest.Meta, transcripts: [String]
    ) {
        self.clientSessionID = clientSessionID
        self.harness = harness
        self.startedAt = startedAt
        self.endedAt = endedAt
        self.checkpoint = checkpoint
        self.meta = meta
        self.transcripts = transcripts
    }

    /// Resolve a session's transcripts and header metadata from `state.sqlite`.
    ///
    /// `poolKey` is `"<harness>|<repo_id or cwd or unknown>"` (SessionDeriver's pooling),
    /// which is exactly `COALESCE(CAST(repo_id AS TEXT), cwd, 'unknown')` on `raw_event`,
    /// so the same predicate finds the same files. The first event's own source is added
    /// unconditionally as a floor.
    ///
    /// Root transcripts ONLY. Subagent sidecars (`<projectdir>/<uuid>/subagents/*.jsonl`)
    /// are ingested with the parent's cwd, repo and timestamps, so the pool predicate
    /// alone would merge them into the digest — and the Python reference
    /// (`analysis/digest.py`) digests exactly one root file. The two must render the same
    /// bytes, because `digest_hash` is how the server tells a fresh analysis from a
    /// replayed one. Two layers, because they fail independently: `is_sidechain = 0` on
    /// the event query (the record's own flag, or the path shape when the record has
    /// none), then `isRootTranscript(path:sourceID:)` on every resolved file — the same
    /// allowlist the parser applies at discovery, so a sidecar whose records claim
    /// `isSidechain: false` still cannot get in through the first-event floor.
    public static func make(
        for s: DetectedSession, checkpoint: Bool, state: SQLiteDB, repoNames: [Int: String],
        calendar: Calendar = .current
    ) throws -> AnalysisJob {
        let prefix = s.harness.rawValue + "|"
        let pool = s.poolKey.hasPrefix(prefix) ? String(s.poolKey.dropFirst(prefix.count)) : s.poolKey

        var sourceIDs = Set<String>()
        try state.query(
            """
            SELECT DISTINCT source_id FROM raw_event
            WHERE harness = ? AND ts >= ? AND ts <= ? AND is_sidechain = 0
              AND COALESCE(CAST(repo_id AS TEXT), cwd, 'unknown') = ?
            """,
            [.text(s.harness.rawValue), .double(s.startedAt), .double(s.endedAt), .text(pool)]
        ) { st in
            if let id = st.text(0) { sourceIDs.insert(id) }
        }
        try state.query(
            "SELECT source_id FROM raw_event WHERE event_uid = ?", [.text(s.firstEventUID)]
        ) { st in
            if let id = st.text(0) { sourceIDs.insert(id) }
        }

        var paths: [String] = []
        for id in sourceIDs.sorted() {
            if let p = try state.scalarText(
                "SELECT path FROM ingest_watermark WHERE source_id = ? AND kind = 'jsonl'", [.text(id)]),
                FileManager.default.fileExists(atPath: p)
            {
                if s.harness == .claudeCode && !isRootTranscript(path: p, sourceID: id) { continue }
                paths.append(p)
            }
        }
        paths.sort()

        // A session finalized by silence after being the last in its pool ended on an
        // idle gap; `still_running` is reserved for the checkpoint (and the live upload).
        let endReason: String
        if checkpoint {
            endReason = EndReason.stillRunning.rawValue
        } else if s.endReason == .stillRunning {
            endReason = EndReason.idleGap.rawValue
        } else {
            endReason = s.endReason.rawValue
        }

        let f = DateFormatter()
        f.calendar = calendar
        f.timeZone = calendar.timeZone
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ssxxx"

        let meta = SessionDigest.Meta(
            repo: Int(pool).flatMap { repoNames[$0] },
            harness: s.harness.rawValue,
            startedAtLocal: f.string(from: Date(timeIntervalSince1970: s.startedAt)),
            endReason: endReason,
            attendedSeconds: Int(s.attendedSeconds.rounded()),
            autonomousSeconds: Int(s.autonomousSeconds.rounded()))

        return AnalysisJob(
            clientSessionID: s.clientSessionID, harness: s.harness,
            startedAt: s.startedAt, endedAt: s.endedAt, checkpoint: checkpoint,
            meta: meta, transcripts: paths)
    }

    /// A Claude Code transcript's path relative to its project directory, recovered from
    /// the source identity rather than guessed from depth.
    ///
    /// `ingest_watermark.path` is absolute and the projects root is not stored, so depth
    /// alone cannot say whether `.../a/b/c.jsonl` is `<root>/<projectdir>/<uuid>.jsonl` or
    /// `<root>/<projectdir>/<uuid>/subagents/c.jsonl` with a shorter root. The source id
    /// settles it: `ClaudeCodeParser.discover` derives it from
    /// `"<projectdir>/<path relative to projectdir>"`, so exactly one split of the path
    /// reproduces the stored id, and the remainder after that split is the relative path
    /// the parser's allowlist was written for. No split matching means the path is not
    /// the file the id names (it moved between project directories, or the row was
    /// written by hand) and the caller treats it as not a root.
    public static func claudeCodeRelativePath(path: String, sourceID: String) -> String? {
        let comps = path.split(separator: "/", omittingEmptySubsequences: true).map(String.init)
        guard comps.count >= 2 else { return nil }
        // Shallowest relative path first: `<projectdir>/<file>` is the common case.
        for i in stride(from: comps.count - 2, through: 0, by: -1) {
            let rel = comps[(i + 1)...].joined(separator: "/")
            if Hashing.sourceID(harness: .claudeCode, descriptor: comps[i] + "/" + rel) == sourceID {
                return rel
            }
        }
        return nil
    }

    /// `ClaudeCodeParser.isRootTranscript(relativePath:)` — the ALLOWLIST on path shape,
    /// never a denylist on `subagents/` — applied to a stored watermark path.
    public static func isRootTranscript(path: String, sourceID: String) -> Bool {
        guard let rel = claudeCodeRelativePath(path: path, sourceID: sourceID) else { return false }
        return ClaudeCodeParser.isRootTranscript(relativePath: rel)
    }

    public func digest(budget: Int = SessionDigest.defaultBudget) throws -> SessionDigest.Output {
        try SessionDigest.build(
            harness: harness, transcripts: transcripts, start: startedAt, end: endedAt,
            meta: meta, budget: budget)
    }

    /// Worth a model call at all? Below `Tuning.countedMinActiveSec` or with fewer than
    /// `Tuning.countedMinMeaningfulEvents` there is nothing for the analyst to read, and
    /// only Claude Code transcripts have a digest loader today.
    public static func isWorthAnalysing(_ s: DetectedSession) -> Bool {
        s.harness == .claudeCode
            && s.activeSeconds >= Tuning.countedMinActiveSec
            && s.meaningfulEventCount >= Tuning.countedMinMeaningfulEvents
    }

    /// Run this job to completion on the calling thread and store the row. What the CLI
    /// does directly and what the scheduler does on its queue.
    @discardableResult
    public func perform(in db: SQLiteDB, model: String = AnalysisSettings.model()) throws
        -> (record: AnalysisRecord, result: Analyzer.Result, digest: SessionDigest.Output)
    {
        let digest = try digest()
        let result = try Analyzer.run(digest: digest, model: model)
        let record = AnalysisRecord(
            clientSessionID: clientSessionID,
            analysisVersion: result.analysis.analysisVersion,
            digestHash: digest.hash,
            digestCoverage: digest.coverage,
            model: result.analysis.model,
            generatedAt: result.analysis.generatedAt.timeIntervalSince1970,
            costUSD: result.costUSD,
            body: try AnalysisStore.encode(result.analysis),
            checkpoint: checkpoint,
            createdAt: Date().timeIntervalSince1970)
        try AnalysisStore.upsert(record, in: db)
        return (record, result, digest)
    }
}

/// Decides which sessions get an analysis and runs them one at a time, off the pass.
///
/// Two triggers (docs/session-boundaries.md, "Analysis timing"): a session the lifecycle
/// holds `final`, for every end reason — so after a `human_returned` end the first thing
/// you see when you sit down is what happened while you were away — and a live session in
/// an autonomous run, every `Tuning.analysisCheckpointSec`, so the phone can answer "what
/// has it done so far" at 3 a.m.
///
/// The final trigger is a STATE, not the transition into it. An earlier version offered
/// a final job only on the tick where `justFinalized` fired, so a `claude -p` that failed
/// on that one tick — the CLI missing from a Finder-launched app's PATH, the 480 s
/// timeout, a locked database, a transient exit 1 — was never offered again:
/// `retryAfterFailureSec` never applied to finals, and a `checkpoint = 1` row from the
/// run's lifetime survived as the session's only analysis. Now every tick considers every
/// `final` session inside `backfillHorizonSec` that has no final row, and the failure
/// backoff is the only thing holding a retry.
///
/// The pass never waits on a model. `consider` only reads the store and enqueues; the
/// serial queue guarantees at most one `claude -p` in flight, and the scheduler opens its
/// OWN state connection on that queue rather than borrowing the pass's.
public final class AnalysisScheduler: @unchecked Sendable {

    /// Finalizations older than this are not analysed automatically.
    ///
    /// The first launch finalizes the entire history at once — 71 sessions on the
    /// reference machine — and MEASURED at $0.33 and 150 s each that is ~$23 and three
    /// hours of serial model calls nobody asked for. A day is long enough that a laptop
    /// woken the next morning still gets last night's run read; anything older is
    /// `builder analyze --all-missing`, an explicit decision. UNMEASURED JUDGEMENT CALL.
    ///
    /// Since finals are re-offered on every tick, this is also the bound on retries: a
    /// final that keeps failing is attempted at most `86_400 / retryAfterFailureSec` = 48
    /// times and then left to `builder analyze`.
    public static let backfillHorizonSec: Double = 86_400

    /// After a failed attempt, leave the session alone for this long. A missing CLI
    /// fails in milliseconds; a timeout costs eight minutes, and a job that failed would
    /// otherwise be retried every 30-second tick. Applies to finals and checkpoints alike.
    /// UNMEASURED JUDGEMENT CALL: a quarter of `analysisCheckpointSec`, so a checkpoint
    /// that fails still gets several tries inside its own cadence, and well over the
    /// 480 s timeout, so two timeouts cannot overlap.
    public static let retryAfterFailureSec: Double = 1_800

    /// Runs one job on the scheduler's queue against its own connection and returns the
    /// one-line summary to log. The default is `AnalysisJob.perform`; tests inject a
    /// runner that fails or succeeds on cue, because the real one spawns `claude -p`.
    public typealias Runner = @Sendable (AnalysisJob, SQLiteDB) throws -> String

    public var log: (@Sendable (String) -> Void)?

    private let openState: @Sendable () throws -> SQLiteDB
    private let clock: @Sendable () -> Double
    private let runner: Runner
    private let queue = DispatchQueue(label: "dev.builder.analysis", qos: .utility)
    private let lock = NSLock()
    private var queued = Set<String>()  // "<id>|checkpoint" / "<id>|final"
    private var lastFailure: [String: Double] = [:]
    private var db: SQLiteDB?  // opened lazily, touched only on `queue`

    /// `clock` stamps failures and is what `consider`'s `now` is compared against; the two
    /// must read the same clock, which is why both are injectable together.
    public init(
        openState: @escaping @Sendable () throws -> SQLiteDB,
        log: (@Sendable (String) -> Void)? = nil,
        clock: @escaping @Sendable () -> Double = { Date().timeIntervalSince1970 },
        runner: Runner? = nil
    ) {
        self.openState = openState
        self.log = log
        self.clock = clock
        self.runner = runner ?? Self.defaultRunner
    }

    private static let defaultRunner: Runner = { job, db in
        let (record, result, digest) = try job.perform(in: db)
        let cost = record.costUSD.map { String(format: "$%.2f", $0) } ?? "cost n/a"
        return "\"\(result.analysis.headline)\" — \(result.analysis.outcome.rawValue), "
            + "confidence \(String(format: "%.2f", result.analysis.confidence)), \(cost), "
            + "\(digest.events) events, coverage \(digest.coverage)"
    }

    /// Called from the pass after lifecycle transitions. Reads, decides, enqueues; returns
    /// the number of jobs queued.
    ///
    /// `transitions` is accepted for the callers that already pass it but is no longer
    /// what decides a final job: `session_lifecycle` is, on every tick (see the type
    /// comment). A session whose row is not yet `final` is never offered a final job, and
    /// a session that is can never be mistaken for the live one.
    @discardableResult
    public func consider(
        sessions: [DetectedSession],
        transitions: [LifecycleTransition],
        live: DetectedSession?,
        state: SQLiteDB,
        repoNames: [Int: String],
        now: Double = Date().timeIntervalSince1970
    ) throws -> Int {
        guard AnalysisSettings.runnerEnabled() else { return 0 }
        _ = transitions
        let index = try AnalysisStore.index(in: state)
        var finals = Set<String>()
        try state.query("SELECT client_session_id FROM session_lifecycle WHERE state = 'final'") { st in
            if let id = st.text(0) { finals.insert(id) }
        }
        var jobs: [AnalysisJob] = []

        for s in sessions where finals.contains(s.clientSessionID) {
            guard AnalysisJob.isWorthAnalysing(s) else { continue }
            // A final row settles it. A surviving checkpoint row does not: it is the
            // mid-run reading, and the final one replaces it under the same key.
            if let row = index[s.clientSessionID], !row.checkpoint { continue }
            if now - s.endedAt > Self.backfillHorizonSec { continue }
            if recentlyFailed(s.clientSessionID, now: now) { continue }
            jobs.append(try AnalysisJob.make(for: s, checkpoint: false, state: state, repoNames: repoNames))
        }

        if let live, !finals.contains(live.clientSessionID), AnalysisJob.isWorthAnalysing(live),
           live.autonomousSeconds >= Tuning.tauAutonomousSec, !recentlyFailed(live.clientSessionID, now: now)
        {
            // Absent, or a checkpoint older than the cadence. A final row for an open
            // session cannot happen (the lifecycle never reopens) and is left alone.
            let due: Bool
            if let row = index[live.clientSessionID] {
                due = row.checkpoint && now - row.generatedAt >= Tuning.analysisCheckpointSec
            } else {
                due = true
            }
            if due {
                jobs.append(try AnalysisJob.make(for: live, checkpoint: true, state: state, repoNames: repoNames))
            }
        }

        var queuedNow = 0
        for job in jobs where enqueue(job) { queuedNow += 1 }
        return queuedNow
    }

    /// Block until every job queued so far has finished. For tests; the pass never calls it.
    public func waitUntilIdle() {
        queue.sync {}
    }

    private func recentlyFailed(_ id: String, now: Double) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard let at = lastFailure[id] else { return false }
        return now - at < Self.retryAfterFailureSec
    }

    /// False if an identical job is already queued or running. A final job may queue
    /// behind a checkpoint for the same session; the serial queue runs them in order and
    /// the final row replaces the checkpoint.
    private func enqueue(_ job: AnalysisJob) -> Bool {
        let key = "\(job.clientSessionID)|\(job.checkpoint ? "checkpoint" : "final")"
        lock.lock()
        let inserted = queued.insert(key).inserted
        lock.unlock()
        guard inserted else { return false }
        queue.async { [self] in
            perform(job)
            lock.lock()
            queued.remove(key)
            lock.unlock()
        }
        return true
    }

    private func perform(_ job: AnalysisJob) {
        let short = String(job.clientSessionID.prefix(8))
        do {
            if db == nil { db = try openState() }
            guard let db else { return }
            let started = Date()
            let summary = try runner(job, db)
            log?(
                "analysis \(short) \(job.checkpoint ? "checkpoint" : "final"): \(summary), "
                    + "\(Int(Date().timeIntervalSince(started)))s")
        } catch {
            lock.lock()
            lastFailure[job.clientSessionID] = clock()
            lock.unlock()
            log?("analysis \(short) failed: \(error)")
        }
    }
}
