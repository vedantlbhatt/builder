import BuilderAnalysis
import BuilderIngest
import BuilderModel
import BuilderSchema
import BuilderSQLite
import Foundation
import Testing

/// The scheduler's final trigger, with an injected clock and an injected runner.
///
/// The real runner spawns `claude -p`, which is exactly what fails in the ways these tests
/// stage: the CLI missing from a Finder-launched app's PATH, the 480 s timeout, a locked
/// database. An earlier scheduler offered a final job only on the tick where the
/// lifecycle transition fired, so one such failure left the session without a final
/// analysis for good — and a checkpoint row from the run's lifetime as its only reading.
///
/// These would otherwise take thirty minutes to observe, which is why nobody did.
@Suite("Analysis scheduler — finals are a state, not a transition")
struct AnalysisSchedulerTests {

    /// A settable clock shared by `consider(now:)` and the scheduler's failure stamps.
    final class Clock: @unchecked Sendable {
        private let lock = NSLock()
        private var value: Double
        init(_ value: Double) { self.value = value }
        var now: Double {
            get { lock.lock(); defer { lock.unlock() }; return value }
            set { lock.lock(); value = newValue; lock.unlock() }
        }
    }

    struct StagedFailure: Error {}

    /// Fails the first `failures` attempts, then writes the row `AnalysisJob.perform`
    /// would have written. Records every attempt as "final" or "checkpoint".
    final class Runner: @unchecked Sendable {
        private let lock = NSLock()
        private var failuresLeft: Int
        private var attemptKinds: [String] = []
        init(failures: Int) { self.failuresLeft = failures }

        var attempts: [String] {
            lock.lock()
            defer { lock.unlock() }
            return attemptKinds
        }

        func run(_ job: AnalysisJob, _ db: SQLiteDB) throws -> String {
            lock.lock()
            attemptKinds.append(job.checkpoint ? "checkpoint" : "final")
            let fail = failuresLeft > 0
            if fail { failuresLeft -= 1 }
            lock.unlock()
            if fail { throw StagedFailure() }
            try AnalysisStore.upsert(
                AnalysisRecord(
                    clientSessionID: job.clientSessionID, analysisVersion: 1, digestHash: "d",
                    digestCoverage: 1, model: "test", generatedAt: job.endedAt, costUSD: nil,
                    body: "{}", checkpoint: job.checkpoint, createdAt: job.endedAt),
                in: db)
            return "ok"
        }
    }

    /// A scratch store, a session the lifecycle holds `final`, and a scheduler wired to
    /// the clock and runner. `now` is the tick that finalized the session.
    static func finalized(
        endedAt: Double = 1_800_000_000, failures: Int
    ) throws -> (db: SQLiteDB, dir: String, session: DetectedSession, now: Double, clock: Clock, runner: Runner, scheduler: AnalysisScheduler) {
        let (db, dir) = try LifecycleTests.scratchDB()
        let s = LifecycleTests.session(id: "a", endedAt: endedAt)
        #expect(AnalysisJob.isWorthAnalysing(s))
        let now = endedAt + Tuning.tauSessionSec + 5
        _ = try SessionLifecycle(db: db).tick(sessions: [s], now: now)
        #expect(try SessionLifecycle(db: db).state(of: "a") == .final)

        let clock = Clock(now)
        let runner = Runner(failures: failures)
        let path = dir + "/state.sqlite"
        let scheduler = AnalysisScheduler(
            openState: { try SchemaManager.openState(path: path) },
            clock: { clock.now },
            runner: { job, db in try runner.run(job, db) })
        return (db, dir, s, now, clock, runner, scheduler)
    }

    /// The transition list is empty on every call here: the final job is offered because
    /// the session IS final, not because it just became so. After the staged failure the
    /// backoff holds it for exactly `retryAfterFailureSec`, then it is offered again, and
    /// once the final row lands it is never offered again.
    @Test func failedFinalIsReofferedAfterTheBackoffAndNotBefore() throws {
        let t = try Self.finalized(failures: 1)
        defer { try? FileManager.default.removeItem(atPath: t.dir) }
        let retry = AnalysisScheduler.retryAfterFailureSec

        #expect(try t.scheduler.consider(
            sessions: [t.session], transitions: [], live: nil, state: t.db, repoNames: [:], now: t.now) == 1)
        t.scheduler.waitUntilIdle()
        #expect(t.runner.attempts == ["final"])
        #expect(try AnalysisStore.index(in: t.db)["a"] == nil)

        // Held back for the whole backoff window.
        for later in [t.now + 1, t.now + retry / 2, t.now + retry - 1] {
            #expect(try t.scheduler.consider(
                sessions: [t.session], transitions: [], live: nil, state: t.db, repoNames: [:], now: later) == 0)
        }
        t.scheduler.waitUntilIdle()
        #expect(t.runner.attempts == ["final"])

        // Offered again the moment the backoff expires, and this time it lands.
        t.clock.now = t.now + retry
        #expect(try t.scheduler.consider(
            sessions: [t.session], transitions: [], live: nil, state: t.db, repoNames: [:], now: t.now + retry) == 1)
        t.scheduler.waitUntilIdle()
        #expect(t.runner.attempts == ["final", "final"])
        let index = try AnalysisStore.index(in: t.db)
        let row = try #require(index["a"])
        #expect(row.checkpoint == false)

        // A final row settles it.
        #expect(try t.scheduler.consider(
            sessions: [t.session], transitions: [], live: nil, state: t.db, repoNames: [:], now: t.now + retry + 1) == 0)
        t.scheduler.waitUntilIdle()
        #expect(t.runner.attempts.count == 2)
    }

    /// A checkpoint row left behind by the run does not count as the session's analysis:
    /// the final session is offered a FINAL job, and the final row replaces the checkpoint
    /// under the same key.
    @Test func survivingCheckpointOnAFinalSessionIsReplacedByAFinal() throws {
        let t = try Self.finalized(failures: 0)
        defer { try? FileManager.default.removeItem(atPath: t.dir) }
        try AnalysisStore.upsert(
            AnalysisRecord(
                clientSessionID: "a", analysisVersion: 1, digestHash: "c", digestCoverage: 1,
                model: "test", generatedAt: t.session.endedAt - 3600, costUSD: nil, body: "{}",
                checkpoint: true, createdAt: t.session.endedAt - 3600),
            in: t.db)
        #expect(try AnalysisStore.index(in: t.db)["a"]?.checkpoint == true)

        #expect(try t.scheduler.consider(
            sessions: [t.session], transitions: [], live: nil, state: t.db, repoNames: [:], now: t.now) == 1)
        t.scheduler.waitUntilIdle()
        #expect(t.runner.attempts == ["final"])
        #expect(try AnalysisStore.index(in: t.db)["a"]?.checkpoint == false)
    }

    /// Re-offering on every tick must not reopen the backfill: a final older than the
    /// horizon is left to `builder analyze --all-missing`, however many ticks pass.
    @Test func finalsOutsideTheHorizonAreNeverOffered() throws {
        let t = try Self.finalized(failures: 0)
        defer { try? FileManager.default.removeItem(atPath: t.dir) }
        let late = t.session.endedAt + AnalysisScheduler.backfillHorizonSec + 1
        t.clock.now = late
        #expect(try t.scheduler.consider(
            sessions: [t.session], transitions: [], live: nil, state: t.db, repoNames: [:], now: late) == 0)
        t.scheduler.waitUntilIdle()
        #expect(t.runner.attempts.isEmpty)
    }

    /// A session the lifecycle has not finalized is not offered a final job: the state
    /// table is the authority, and an open session inside the horizon that is worth
    /// analysing still waits for its finalization.
    @Test func openSessionsAreNotOfferedAFinal() throws {
        let (db, dir) = try LifecycleTests.scratchDB()
        defer { try? FileManager.default.removeItem(atPath: dir) }
        let endedAt = 1_800_000_000.0
        let s = LifecycleTests.session(id: "b", endedAt: endedAt)
        let now = endedAt + 5
        _ = try SessionLifecycle(db: db).tick(sessions: [s], now: now)
        #expect(try SessionLifecycle(db: db).state(of: "b") == .open)

        let runner = Runner(failures: 0)
        let path = dir + "/state.sqlite"
        let scheduler = AnalysisScheduler(
            openState: { try SchemaManager.openState(path: path) },
            clock: { now },
            runner: { job, db in try runner.run(job, db) })
        #expect(try scheduler.consider(
            sessions: [s], transitions: [], live: nil, state: db, repoNames: [:], now: now) == 0)
        scheduler.waitUntilIdle()
        #expect(runner.attempts.isEmpty)
    }
}
