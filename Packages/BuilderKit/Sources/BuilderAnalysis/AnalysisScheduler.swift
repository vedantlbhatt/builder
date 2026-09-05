import BuilderIngest
import BuilderModel
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
            WHERE harness = ? AND ts >= ? AND ts <= ?
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
/// Two triggers (docs/session-boundaries.md, "Analysis timing"): a session that just
/// finalized, for every end reason — so after a `human_returned` end the first thing you
/// see when you sit down is what happened while you were away — and a live session in an
/// autonomous run, every `Tuning.analysisCheckpointSec`, so the phone can answer "what
/// has it done so far" at 3 a.m.
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
    public static let backfillHorizonSec: Double = 86_400

    /// After a failed attempt, leave the session alone for this long. A missing CLI
    /// fails in milliseconds; a timeout costs eight minutes, and a checkpoint that failed
    /// would otherwise be retried every 30-second tick.
    public static let retryAfterFailureSec: Double = 1_800

    public var log: (@Sendable (String) -> Void)?

    private let openState: @Sendable () throws -> SQLiteDB
    private let queue = DispatchQueue(label: "dev.builder.analysis", qos: .utility)
    private let lock = NSLock()
    private var queued = Set<String>()  // "<id>|checkpoint" / "<id>|final"
    private var lastFailure: [String: Double] = [:]
    private var db: SQLiteDB?  // opened lazily, touched only on `queue`

    public init(openState: @escaping @Sendable () throws -> SQLiteDB, log: (@Sendable (String) -> Void)? = nil) {
        self.openState = openState
        self.log = log
    }

    /// Called from the pass after lifecycle transitions. Reads, decides, enqueues; returns
    /// the number of jobs queued.
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
        let index = try AnalysisStore.index(in: state)
        let finalized = Set(transitions.filter(\.justFinalized).map(\.clientSessionID))
        var jobs: [AnalysisJob] = []

        for s in sessions where finalized.contains(s.clientSessionID) {
            guard AnalysisJob.isWorthAnalysing(s) else { continue }
            if let row = index[s.clientSessionID], !row.checkpoint { continue }
            if now - s.endedAt > Self.backfillHorizonSec { continue }
            if recentlyFailed(s.clientSessionID, now: now) { continue }
            jobs.append(try AnalysisJob.make(for: s, checkpoint: false, state: state, repoNames: repoNames))
        }

        if let live, !finalized.contains(live.clientSessionID), AnalysisJob.isWorthAnalysing(live),
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
            let (record, result, digest) = try job.perform(in: db)
            let cost = record.costUSD.map { String(format: "$%.2f", $0) } ?? "cost n/a"
            log?(
                "analysis \(short) \(job.checkpoint ? "checkpoint" : "final"): "
                    + "\"\(result.analysis.headline)\" — \(result.analysis.outcome.rawValue), "
                    + "confidence \(String(format: "%.2f", result.analysis.confidence)), \(cost), "
                    + "\(digest.events) events, coverage \(digest.coverage), "
                    + "\(Int(Date().timeIntervalSince(started)))s")
        } catch {
            lock.lock()
            lastFailure[job.clientSessionID] = Date().timeIntervalSince1970
            lock.unlock()
            log?("analysis \(short) failed: \(error)")
        }
    }
}
