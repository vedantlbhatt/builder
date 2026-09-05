import BuilderModel
import BuilderSQLite
import Foundation

/// Tracks whether a session is still running, and fires exactly once when it ends.
///
/// **A session ends precisely because nothing is happening.** That is the whole difficulty:
/// there is no event that means "done", so an event-driven design can never detect
/// completion. The lifecycle therefore runs on a timer that ticks whether or not the file
/// system said anything.
///
/// State is persisted rather than held in memory, because the interesting failures all
/// involve the process not being alive: quitting mid-finalization must not lose the
/// session, and relaunching must not re-announce a session the user was already told about.
public enum SessionState: String, Sendable, CaseIterable {
    /// An event arrived recently. Work is in progress.
    case open
    /// Quiet for longer than `tauIdleSegSec` but not yet `tauSessionSec`. Still one
    /// session — the strip paints an idle band and the clock stops counting.
    case idle
    /// Quiet past the session threshold. Derivation is running.
    case finalizing
    /// Derived and committed. This is the moment the notification fires.
    case final
}

public struct LifecycleTransition: Sendable {
    public let clientSessionID: String
    public let from: SessionState?
    public let to: SessionState
    public let lastEventAt: Double

    /// True when this transition is the moment a session became complete.
    public var justFinalized: Bool { to == .final && from != .final }
}

/// What `pendingNotifications` found: two classes, because they say different things.
/// "Session finished" congratulates a person; "Agent run finished" tells them a machine
/// they walked away from has stopped, which is the moment to go and look.
public struct PendingNotifications: Sendable {
    public var sessionFinished: [DetectedSession]
    public var runFinished: [DetectedSession]

    public init(sessionFinished: [DetectedSession] = [], runFinished: [DetectedSession] = []) {
        self.sessionFinished = sessionFinished
        self.runFinished = runFinished
    }

    public var isEmpty: Bool { sessionFinished.isEmpty && runFinished.isEmpty }
    public var count: Int { sessionFinished.count + runFinished.count }
}

public final class SessionLifecycle {

    private let db: SQLiteDB

    public init(db: SQLiteDB) {
        self.db = db
    }

    /// Evaluate every known session against the clock and record transitions.
    ///
    /// `now` is injectable so the state machine is testable without waiting fifteen
    /// minutes, which is otherwise the only way to observe a finalization.
    @discardableResult
    public func tick(sessions: [DetectedSession], now: Double = Date().timeIntervalSince1970)
        throws -> [LifecycleTransition]
    {
        var stored: [String: (state: SessionState, lastEvent: Double)] = [:]
        try db.query("SELECT client_session_id, state, last_event_ts FROM session_lifecycle") { s in
            guard let id = s.text(0), let st = SessionState(rawValue: s.text(1) ?? "") else { return }
            stored[id] = (st, s.double(2) ?? 0)
        }

        var transitions: [LifecycleTransition] = []

        try db.transaction {
            let upsert = try db.prepare(
                """
                INSERT INTO session_lifecycle
                  (client_session_id, state, last_event_ts, entered_state_at, finalized_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(client_session_id) DO UPDATE SET
                  state = excluded.state,
                  last_event_ts = excluded.last_event_ts,
                  entered_state_at = CASE
                    WHEN session_lifecycle.state != excluded.state THEN excluded.entered_state_at
                    ELSE session_lifecycle.entered_state_at END,
                  finalized_at = COALESCE(session_lifecycle.finalized_at, excluded.finalized_at)
                """
            )
            defer { upsert.finalize() }

            for s in sessions {
                let quiet = now - s.endedAt
                let next: SessionState
                if s.isCut {
                    // Ended by a boundary rule, not by silence: the human came back to a
                    // running agent, or 04:00 passed during an autonomous run. Nothing
                    // more can ever be added to it, so it is final the moment it exists.
                    next = .final
                } else if quiet < Tuning.tauIdleSegSec {
                    next = .open
                } else if quiet < Tuning.tauSessionSec {
                    next = .idle
                } else {
                    next = .final
                }

                let previous = stored[s.clientSessionID]?.state

                // A session never moves backwards. Once final, a late-arriving event
                // belongs to a NEW session by definition — more than tau has passed —
                // so reopening one would resurrect something the user was already told
                // had finished.
                if previous == .final { continue }
                if previous == next { continue }

                try upsert.execute([
                    .text(s.clientSessionID), .text(next.rawValue), .double(s.endedAt),
                    .double(now), next == .final ? .double(now) : .null,
                ])

                transitions.append(
                    LifecycleTransition(
                        clientSessionID: s.clientSessionID, from: previous, to: next,
                        lastEventAt: s.endedAt))
            }
        }

        return transitions
    }

    /// Sessions that just completed and have never been announced.
    ///
    /// The `notification_log` check is the exactly-once guard, and it is a row rather than
    /// an in-memory flag for a specific reason: finalization and notification are separate
    /// steps, and a crash between them must not produce either a silent drop or a repeat.
    /// A row committed before the alert is sent means at-most-once; the alert is cheap to
    /// lose and expensive to duplicate, so that is the right side to err on.
    ///
    /// Two classes come back. `sessionFinished` is the existing rule — `notable`, i.e. a
    /// person was present for at least `Tuning.notableMinActiveSec` — and `runFinished`
    /// is an unattended run that stopped on its own. Both require `endReason == .idleGap`:
    /// it is the only end that means the work stopped. A `human_returned` end is not
    /// announced because you are already here, and a `day_boundary` end is not announced
    /// because the work continues (docs/session-boundaries.md).
    public func pendingNotifications(
        for sessions: [DetectedSession],
        transitions: [LifecycleTransition],
        now: Double = Date().timeIntervalSince1970
    ) throws -> PendingNotifications {
        let finalized = Set(transitions.filter(\.justFinalized).map(\.clientSessionID))
        guard !finalized.isEmpty else { return PendingNotifications() }

        var alreadyTold = Set<String>()
        try db.query("SELECT client_session_id FROM notification_log") { s in
            if let id = s.text(0) { alreadyTold.insert(id) }
        }

        var deliver = PendingNotifications()
        var suppress: [String] = []

        for s in sessions {
            guard finalized.contains(s.clientSessionID) else { continue }
            guard !alreadyTold.contains(s.clientSessionID) else { continue }

            // Only silence means the work stopped. A session the clock finalized is the
            // last one in its pool, so the sessionizer labels it `stillRunning` — silence
            // ended it all the same. Requiring `.idleGap` alone here silenced every
            // notification on the normal path (found by review, not by the tests, which
            // built sessions with `.idleGap` by hand).
            guard !s.isCut else { continue }

            // A four-minute question should not interrupt anyone. An autonomous run with
            // nobody at the keyboard has no one to congratulate — but it IS news that it
            // stopped, so it goes out under the other headline.
            let isRun = s.runFinished
            guard s.notable || isRun else { continue }

            // FOUND BY RUNNING THE DAEMON. The first launch backfills the entire history,
            // so every historical session transitions to `final` at once — 71 of them on
            // this machine — and the user is buried in notifications about work they did
            // last week. An alert is only meaningful if it is news.
            //
            // Anything older than the staleness horizon is recorded as notified WITHOUT
            // being delivered, so it can never resurface later either.
            if now - s.endedAt > Self.staleNotificationSeconds {
                suppress.append(s.clientSessionID)
                continue
            }
            if isRun {
                deliver.runFinished.append(s)
            } else {
                deliver.sessionFinished.append(s)
            }
        }

        for id in suppress { try markNotified(id, channel: "suppressed_stale") }
        return deliver
    }

    /// How recently a session must have ended for its completion to be worth announcing.
    ///
    /// Twice the session threshold: long enough that a tick landing late still delivers,
    /// short enough that a backfill or a laptop woken after a weekend stays silent.
    public static let staleNotificationSeconds: Double = Tuning.tauSessionSec * 2

    public func markNotified(_ clientSessionID: String, channel: String) throws {
        try db.run(
            "INSERT OR IGNORE INTO notification_log (client_session_id, notified_at, channel) "
                + "VALUES (?,?,?)",
            [.text(clientSessionID), .double(Date().timeIntervalSince1970), .text(channel)])
    }

    public func state(of clientSessionID: String) throws -> SessionState? {
        try db.scalarText(
            "SELECT state FROM session_lifecycle WHERE client_session_id = ?",
            [.text(clientSessionID)]
        ).flatMap(SessionState.init(rawValue:))
    }

    /// The session currently in progress, if any.
    public func openSession(among sessions: [DetectedSession]) throws -> DetectedSession? {
        var openIDs = Set<String>()
        try db.query("SELECT client_session_id FROM session_lifecycle WHERE state IN ('open','idle')") { s in
            if let id = s.text(0) { openIDs.insert(id) }
        }
        return sessions.filter { openIDs.contains($0.clientSessionID) }
            .max { $0.endedAt < $1.endedAt }
    }
}
