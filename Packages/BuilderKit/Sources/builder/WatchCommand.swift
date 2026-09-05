import BuilderAnalysis
import BuilderIngest
import BuilderModel
import BuilderSchema
import BuilderSQLite
import Foundation

/// `builder watch` — the completion loop, running for real.
///
/// This is what the menu bar app will host. Everything it does is here first, in a form
/// that can be watched in a terminal, because a bug in "tell me when I'm done" is
/// invisible until fifteen minutes after it happens.
enum WatchCommand {

    /// All mutable daemon state lives behind one lock. The FSEvents callback, the tick
    /// timer and the reconcile timer all land on the same serial queue, but the SQLite
    /// connections must not be touched from anywhere else.
    private final class Runner: @unchecked Sendable {
        let state: SQLiteDB
        let cache: SQLiteDB
        let lifecycle: SessionLifecycle
        let notifier: any Notifier
        let coordinator: IngestCoordinator
        /// Queues `claude -p` runs for sessions that just finalized and checkpoints for
        /// a live autonomous run; they execute on their own queue with their own state
        /// connection, so the pass never waits on a model.
        let analysis: AnalysisScheduler
        var repoNames: [Int: String] = [:]
        var lastLiveDescription = ""
        let lock = NSLock()

        init(quiet: Bool) throws {
            state = try SchemaManager.openState()
            cache = try SchemaManager.openCache(tuningVersion: Tuning.version).db
            lifecycle = SessionLifecycle(db: state)
            coordinator = IngestCoordinator(db: state)
            notifier =
                quiet
                ? ConsoleNotifier()
                : MultiNotifier([ConsoleNotifier(), MacOSNotifier()])
            analysis = AnalysisScheduler(
                openState: { try SchemaManager.openState() },
                log: { line in print("\u{1B}[2K\r  \(line)") })
            repoNames = (try? IngestCoordinator.repoNames(db: state)) ?? [:]
        }

        func pass() {
            lock.lock()
            defer { lock.unlock() }

            do {
                let ingest = try coordinator.run()
                let sessions = try SessionDeriver.run(db: state, verbose: false)

                let transitions = try lifecycle.tick(sessions: sessions)
                let pending = try lifecycle.pendingNotifications(
                    for: sessions, transitions: transitions)

                // Two headlines, one delivery path. A finished sitting congratulates
                // the person; a finished unattended run tells them to go and look.
                var queue: [(session: DetectedSession, kind: SessionAlert.Kind)] = []
                for s in pending.sessionFinished { queue.append((session: s, kind: .sessionFinished)) }
                for s in pending.runFinished { queue.append((session: s, kind: .runFinished)) }

                for (session, kind) in queue {
                    let repoID = try? cache.scalarInt(
                        "SELECT repo_id_primary FROM session WHERE client_session_id = ?",
                        [.text(session.clientSessionID)])
                    let lines = try? cache.scalarInt(
                        "SELECT agent_lines_added FROM session WHERE client_session_id = ?",
                        [.text(session.clientSessionID)])

                    let alert = SessionAlert(
                        session: session,
                        kind: kind,
                        repoName: repoID.flatMap { $0 }.flatMap { repoNames[$0] },
                        agentLines: lines.flatMap { $0 } ?? 0,
                        prompts: session.promptCount)

                    // Record BEFORE delivering. At-most-once is the right side to err on:
                    // a lost alert costs one notification, a repeated one erodes trust in
                    // every alert after it.
                    try lifecycle.markNotified(session.clientSessionID, channel: notifier.channel)
                    try notifier.deliver(alert)
                }

                if ingest.eventsWritten > 0 {
                    repoNames = (try? IngestCoordinator.repoNames(db: state)) ?? repoNames
                }

                let live = try lifecycle.openSession(among: sessions)

                // Analyses: every session that just finalized, and a checkpoint for a
                // live autonomous run. Queued here, run elsewhere; a failure to even
                // read the store is reported, never thrown into the pass.
                do {
                    let queued = try analysis.consider(
                        sessions: sessions, transitions: transitions, live: live,
                        state: state, repoNames: repoNames)
                    if queued > 0 { print("\u{1B}[2K\r  queued \(queued) analysis run(s)") }
                } catch {
                    print("\u{1B}[2K\r  analysis scheduling failed: \(error)")
                }

                // Live session line, redrawn only when it changes.
                if let live {
                    let repoID = try? cache.scalarInt(
                        "SELECT repo_id_primary FROM session WHERE client_session_id = ?",
                        [.text(live.clientSessionID)])
                    let name = repoID.flatMap { $0 }.flatMap { repoNames[$0] } ?? "—"
                    let desc =
                        "\(name)  \(Fmt.duration(live.activeSeconds)) active  "
                        + "\(live.promptCount) prompts"
                    if desc != lastLiveDescription {
                        lastLiveDescription = desc
                        let stamp = DateFormatter()
                        stamp.dateFormat = "HH:mm:ss"
                        print("\u{1B}[2K\r  \(stamp.string(from: Date()))  ● \(desc)", terminator: "")
                        fflush(stdout)
                    }
                } else if !lastLiveDescription.isEmpty {
                    lastLiveDescription = ""
                    print("\u{1B}[2K\r  idle", terminator: "")
                    fflush(stdout)
                }
            } catch {
                print("\u{1B}[2K\r  error: \(error)")
            }
        }
    }

    static func run() throws {
        let quiet = CLIArgs.flag("quiet")
        let runner = try Runner(quiet: quiet)

        print("builder watch")
        print("  roots        \(Daemon.defaultRoots().map { ($0 as NSString).lastPathComponent }.joined(separator: ", "))")
        print("  session ends after \(Int(Tuning.tauSessionSec / 60)) min of quiet")
        print("  tick         every 30s, independent of file events — a session ends")
        print("               because nothing happens, so nothing can be the trigger")
        print("  notify       \(quiet ? "console only" : "console + macOS notification")")
        print(
            "  analysis     "
                + (AnalysisSettings.runnerEnabled()
                    ? "claude -p (\(AnalysisSettings.model())) on finalization; BUILDER_ANALYSIS=0 disables"
                    : "off (BUILDER_ANALYSIS=0)"))
        print("")

        let daemon = Daemon(
            onEvent: { _ in },
            pass: { runner.pass() })
        daemon.start()

        // Run until interrupted.
        signal(SIGINT) { _ in
            print("\n  stopped")
            exit(0)
        }
        RunLoop.main.run()
    }
}
