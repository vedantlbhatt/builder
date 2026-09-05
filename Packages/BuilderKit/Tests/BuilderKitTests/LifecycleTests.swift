import BuilderIngest
import BuilderModel
import BuilderSchema
import BuilderSQLite
import Foundation
import Testing

/// The completion loop, tested with an injected clock.
///
/// Every one of these would otherwise take fifteen minutes to observe, which is exactly
/// why the failure mode here is so easy to ship: nobody sits and waits for a notification
/// that never comes, they just quietly stop trusting the app.
@Suite("Session lifecycle")
struct LifecycleTests {

    /// A scratch state database. Never touches the user's real store.
    static func scratchDB() throws -> (SQLiteDB, String) {
        let dir = NSTemporaryDirectory() + "builder-tests-\(UUID().uuidString)"
        try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        let path = dir + "/state.sqlite"
        return (try SchemaManager.openState(path: path), dir)
    }

    /// By default a fully attended sitting that ended on silence: every second attended,
    /// one presence signal per prompt.
    static func session(
        id: String, endedAt: Double, activeSeconds: Double = 1800, prompts: Int = 5,
        attendedSeconds: Double? = nil, presence: Int? = nil, endReason: EndReason = .idleGap
    ) -> DetectedSession {
        let attended = attendedSeconds ?? activeSeconds
        return DetectedSession(
            clientSessionID: id,
            harness: .claudeCode,
            poolKey: "test",
            startedAt: endedAt - activeSeconds,
            endedAt: endedAt,
            activeSeconds: activeSeconds,
            attendedSeconds: attended,
            autonomousSeconds: activeSeconds - attended,
            eventIndices: Array(0..<20),
            meaningfulEventCount: 20,
            promptCount: prompts,
            presenceCount: presence ?? prompts,
            endReason: endReason,
            firstEventUID: id)
    }

    @Test func statesFollowTheClock() throws {
        let (db, dir) = try Self.scratchDB()
        defer { try? FileManager.default.removeItem(atPath: dir) }
        let lifecycle = SessionLifecycle(db: db)

        let now = 1_800_000_000.0
        let s = Self.session(id: "a", endedAt: now)

        // Just finished an event: still working.
        _ = try lifecycle.tick(sessions: [s], now: now + 5)
        #expect(try lifecycle.state(of: "a") == .open)

        // Quiet past the idle threshold but not the session threshold: same session,
        // clock stopped, strip paints an idle band.
        _ = try lifecycle.tick(sessions: [s], now: now + Tuning.tauIdleSegSec + 5)
        #expect(try lifecycle.state(of: "a") == .idle)

        // Quiet past the session threshold: done.
        _ = try lifecycle.tick(sessions: [s], now: now + Tuning.tauSessionSec + 5)
        #expect(try lifecycle.state(of: "a") == .final)
    }

    /// The guarantee that matters most: a session announces itself exactly once, even if
    /// the process restarts mid-finalization.
    @Test func notifiesExactlyOnce() throws {
        let (db, dir) = try Self.scratchDB()
        defer { try? FileManager.default.removeItem(atPath: dir) }

        let now = 1_800_000_000.0
        let s = Self.session(id: "a", endedAt: now)

        let firedAt = now + Tuning.tauSessionSec + 5
        let first = SessionLifecycle(db: db)
        let t1 = try first.tick(sessions: [s], now: firedAt)
        let pending1 = try first.pendingNotifications(for: [s], transitions: t1, now: firedAt)
        #expect(pending1.sessionFinished.count == 1)
        #expect(pending1.runFinished.isEmpty)
        try first.markNotified("a", channel: "test")

        // A brand new lifecycle object, as after a relaunch. The state is in the database,
        // not in memory, so it must not announce the same session again.
        let second = SessionLifecycle(db: db)
        let t2 = try second.tick(sessions: [s], now: firedAt + 600)
        let pending2 = try second.pendingNotifications(for: [s], transitions: t2, now: firedAt + 600)
        #expect(pending2.isEmpty)
    }

    /// Backfill must be silent.
    ///
    /// On first launch every historical session finalizes at once — 71 of them on the
    /// reference machine. Announcing them would bury the user in alerts about work they
    /// did last week, and an alert that is not news trains people to ignore all of them.
    @Test func staleSessionsFinalizeSilently() throws {
        let (db, dir) = try Self.scratchDB()
        defer { try? FileManager.default.removeItem(atPath: dir) }
        let lifecycle = SessionLifecycle(db: db)

        let now = 1_800_000_000.0
        let lastWeek = Self.session(id: "old", endedAt: now - 7 * 86400, activeSeconds: 3600)
        let justNow = Self.session(id: "new", endedAt: now - Tuning.tauSessionSec - 10,
                                   activeSeconds: 3600)
        // An unattended run from last week is just as stale as a sitting from last week.
        let oldRobot = Self.session(id: "old-robot", endedAt: now - 7 * 86400,
                                    activeSeconds: 7200, prompts: 0)

        let all = [lastWeek, justNow, oldRobot]
        let t = try lifecycle.tick(sessions: all, now: now)
        let pending = try lifecycle.pendingNotifications(for: all, transitions: t, now: now)

        #expect(pending.sessionFinished.map(\.clientSessionID) == ["new"])
        #expect(pending.runFinished.isEmpty)

        // And the suppressed ones are recorded, so a later tick cannot resurface them.
        let again = try lifecycle.pendingNotifications(for: all, transitions: t, now: now)
        #expect(again.runFinished.isEmpty)
        #expect(again.sessionFinished.isEmpty || again.sessionFinished.map(\.clientSessionID) == ["new"])
        #expect(try db.scalarInt(
            "SELECT COUNT(*) FROM notification_log WHERE channel = 'suppressed_stale'") == 2)
    }

    /// A session never reopens. Once more than tau has passed, a later event belongs to a
    /// NEW session by definition — resurrecting the old one would contradict an alert the
    /// user has already seen.
    @Test func finalIsTerminal() throws {
        let (db, dir) = try Self.scratchDB()
        defer { try? FileManager.default.removeItem(atPath: dir) }
        let lifecycle = SessionLifecycle(db: db)

        let now = 1_800_000_000.0
        let s = Self.session(id: "a", endedAt: now)
        _ = try lifecycle.tick(sessions: [s], now: now + Tuning.tauSessionSec + 5)
        #expect(try lifecycle.state(of: "a") == .final)

        _ = try lifecycle.tick(sessions: [s], now: now + 10)
        #expect(try lifecycle.state(of: "a") == .final)
    }

    /// Short sessions finalize silently. An unattended run that stopped is announced —
    /// under its own headline, never as a "session finished".
    @Test func onlyNotableSessionsInterrupt() throws {
        let (db, dir) = try Self.scratchDB()
        defer { try? FileManager.default.removeItem(atPath: dir) }
        let lifecycle = SessionLifecycle(db: db)

        let now = 1_800_000_000.0
        // Four minutes of work: counts toward hours, does not deserve an interruption.
        let brief = Self.session(id: "brief", endedAt: now, activeSeconds: 240)
        // Long, but nobody typed anything — an autonomous run. Nobody to congratulate,
        // but the moment it stops is exactly when you want to go and look.
        let robot = Self.session(id: "robot", endedAt: now, activeSeconds: 7200, prompts: 0)
        let real = Self.session(id: "real", endedAt: now, activeSeconds: 3600, prompts: 12)

        let t = try lifecycle.tick(
            sessions: [brief, robot, real], now: now + Tuning.tauSessionSec + 5)
        let pending = try lifecycle.pendingNotifications(
            for: [brief, robot, real], transitions: t)

        #expect(pending.sessionFinished.map(\.clientSessionID) == ["real"])
        #expect(pending.runFinished.map(\.clientSessionID) == ["robot"])
        #expect(brief.notable == false)
        #expect(brief.runFinished == false)
        #expect(robot.unattended == true)
        #expect(robot.notable == false)
        #expect(robot.runFinished == true)
    }

    /// A kickoff prompt followed by hours of autonomy is NOT unattended — the person did
    /// start it — but it is judged on attended time. Twenty attended minutes is a
    /// session; five is not, however long the machine ran afterwards.
    @Test func notableIsJudgedOnAttendedTime() {
        let now = 1_800_000_000.0
        let kickoff = Self.session(id: "k", endedAt: now, activeSeconds: 8 * 3600, prompts: 1,
                                   attendedSeconds: 300)
        #expect(kickoff.unattended == false)
        #expect(kickoff.notable == false)
        #expect(kickoff.runFinished == false)

        let steered = Self.session(id: "s", endedAt: now, activeSeconds: 8 * 3600, prompts: 1,
                                   attendedSeconds: 1500)
        #expect(steered.notable == true)
        #expect(abs(steered.activeSeconds - steered.attendedSeconds - steered.autonomousSeconds) < 0.001)
    }

    /// A session cut by a boundary rule is final the moment it exists, however recent its
    /// last event, and it is never announced: for `humanReturned` you are already here,
    /// for `dayBoundary` the work continues.
    @Test func cutSessionsFinalizeImmediatelyAndSilently() throws {
        let (db, dir) = try Self.scratchDB()
        defer { try? FileManager.default.removeItem(atPath: dir) }
        let lifecycle = SessionLifecycle(db: db)

        let now = 1_800_000_000.0
        // Both would qualify on every other count: long, prompted, just ended.
        let night = Self.session(id: "night", endedAt: now - 1, activeSeconds: 5 * 3600,
                                 prompts: 1, endReason: .dayBoundary)
        let returned = Self.session(id: "returned", endedAt: now - 1, activeSeconds: 4 * 3600,
                                    prompts: 3, endReason: .humanReturned)
        #expect(night.isCut && returned.isCut)

        let t = try lifecycle.tick(sessions: [night, returned], now: now)
        #expect(try lifecycle.state(of: "night") == .final)
        #expect(try lifecycle.state(of: "returned") == .final)
        #expect(t.filter(\.justFinalized).count == 2)

        let pending = try lifecycle.pendingNotifications(
            for: [night, returned], transitions: t, now: now)
        #expect(pending.isEmpty)

        // The same span ended by silence WOULD have been announced — the cut is the
        // only thing keeping these quiet.
        let robotRun = Self.session(id: "robot", endedAt: now - 1, activeSeconds: 5 * 3600,
                                    prompts: 0, endReason: .dayBoundary)
        #expect(robotRun.unattended == true)
        #expect(robotRun.runFinished == false)
    }

    /// The alert copy leads with duration, because it is the only number every harness
    /// always has. Cursor never reports tokens and most sessions have no commits.
    @Test func alertCopyLeadsWithDuration() {
        let s = Self.session(id: "a", endedAt: 1_800_000_000, activeSeconds: 6120, prompts: 9)
        let alert = SessionAlert(
            session: s, kind: .sessionFinished, repoName: "gt-transit", agentLines: 2140, prompts: 9)
        #expect(alert.kind == .sessionFinished)
        #expect(alert.title == "Session finished: 1h 42m in gt-transit")
        #expect(alert.body.contains("+2,140 lines"))
        #expect(alert.body.contains("9 prompts"))

        // No repo resolved: still a complete sentence, never "in (null)".
        let anon = SessionAlert(session: s, repoName: nil, agentLines: 0, prompts: 0)
        #expect(anon.title == "Session finished: 1h 42m")
    }

    /// "Agent run finished: 6h 12m in gt-transit" / "+2,101 lines · ran unattended · started 23:04"
    @Test func runFinishedHasItsOwnHeadline() {
        let startedAt = 1_800_000_000.0
        let run = Self.session(id: "r", endedAt: startedAt + 6 * 3600 + 12 * 60,
                               activeSeconds: 6 * 3600 + 12 * 60, prompts: 0)
        #expect(run.runFinished == true)

        let alert = SessionAlert(
            session: run, kind: .runFinished, repoName: "gt-transit", agentLines: 2101, prompts: 0)
        #expect(alert.kind == .runFinished)
        #expect(alert.title == "Agent run finished: 6h 12m in gt-transit")
        #expect(alert.body.hasPrefix("+2,101 lines · ran unattended · started "))
        #expect(!alert.body.contains("prompt"))

        // The start time is local wall clock; assert the shape rather than the zone.
        let clock = alert.body.split(separator: " ").last.map(String.init) ?? ""
        #expect(clock.count == 5 && clock.dropFirst(2).first == ":")

        let anon = SessionAlert(session: run, kind: .runFinished, repoName: nil, agentLines: 0, prompts: 0)
        #expect(anon.title == "Agent run finished: 6h 12m")
        #expect(anon.body.hasPrefix("ran unattended · started "))
    }
}
