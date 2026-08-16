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

    static func session(
        id: String, endedAt: Double, activeSeconds: Double = 1800, prompts: Int = 5
    ) -> DetectedSession {
        DetectedSession(
            clientSessionID: id,
            harness: .claudeCode,
            poolKey: "test",
            startedAt: endedAt - activeSeconds,
            endedAt: endedAt,
            activeSeconds: activeSeconds,
            eventIndices: Array(0..<20),
            meaningfulEventCount: 20,
            promptCount: prompts,
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
        #expect(pending1.count == 1)
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

        let t = try lifecycle.tick(sessions: [lastWeek, justNow], now: now)
        let pending = try lifecycle.pendingNotifications(
            for: [lastWeek, justNow], transitions: t, now: now)

        #expect(pending.map(\.clientSessionID) == ["new"])

        // And the suppressed one is recorded, so a later tick cannot resurface it.
        let again = try lifecycle.pendingNotifications(
            for: [lastWeek, justNow], transitions: t, now: now)
        #expect(again.isEmpty || again.map(\.clientSessionID) == ["new"])
        #expect(try db.scalarInt(
            "SELECT COUNT(*) FROM notification_log WHERE channel = 'suppressed_stale'") == 1)
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

    /// Short sessions and unattended runs finalize silently.
    @Test func onlyNotableSessionsInterrupt() throws {
        let (db, dir) = try Self.scratchDB()
        defer { try? FileManager.default.removeItem(atPath: dir) }
        let lifecycle = SessionLifecycle(db: db)

        let now = 1_800_000_000.0
        // Four minutes of work: counts toward hours, does not deserve an interruption.
        let brief = Self.session(id: "brief", endedAt: now, activeSeconds: 240)
        // Long, but nobody typed anything — an autonomous run. Nobody to congratulate.
        let robot = Self.session(id: "robot", endedAt: now, activeSeconds: 7200, prompts: 0)
        let real = Self.session(id: "real", endedAt: now, activeSeconds: 3600, prompts: 12)

        let t = try lifecycle.tick(
            sessions: [brief, robot, real], now: now + Tuning.tauSessionSec + 5)
        let pending = try lifecycle.pendingNotifications(
            for: [brief, robot, real], transitions: t)

        #expect(pending.map(\.clientSessionID) == ["real"])
        #expect(brief.notable == false)
        #expect(robot.unattended == true)
        #expect(robot.notable == false)
    }

    /// The alert copy leads with duration, because it is the only number every harness
    /// always has. Cursor never reports tokens and most sessions have no commits.
    @Test func alertCopyLeadsWithDuration() {
        let s = Self.session(id: "a", endedAt: 1_800_000_000, activeSeconds: 6120, prompts: 9)
        let alert = SessionAlert(session: s, repoName: "gt-transit", agentLines: 2140, prompts: 9)
        #expect(alert.title == "Session finished: 1h 42m in gt-transit")
        #expect(alert.body.contains("+2,140 lines"))
        #expect(alert.body.contains("9 prompts"))

        // No repo resolved: still a complete sentence, never "in (null)".
        let anon = SessionAlert(session: s, repoName: nil, agentLines: 0, prompts: 0)
        #expect(anon.title == "Session finished: 1h 42m")
    }
}
