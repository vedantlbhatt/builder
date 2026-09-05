import AppKit
import BuilderAnalysis
import BuilderIngest
import BuilderModel
import BuilderSchema
import BuilderSQLite
import BuilderStore
import BuilderUI
import Foundation
import Observation

/// Everything the menu bar UI reads, and the one place the daemon writes.
///
/// `@Observable` rather than `ObservableObject`. It matters concretely here: the popover
/// redraws a session list and a 371-cell contribution grid, and field-level invalidation
/// means a changing live-session timer does not re-render the graph every second.
@Observable
@MainActor
final class AppStore {

    struct SessionRow: Identifiable, Equatable {
        var id: String
        var repo: String
        var headline: String
        var activeSeconds: Double
        var wallSeconds: Double
        var startedAt: Double
        var prompts: Int
        var strip: [UInt8]
        var marks: [(ms: Int, kind: StripMarkKind)]
        var notable: Bool

        static func == (a: SessionRow, b: SessionRow) -> Bool { a.id == b.id }
    }

    // MARK: Observable state

    var todayActiveSeconds: Double = 0
    var streakDays: Int = 0
    var liveSession: SessionRow?
    var recent: [SessionRow] = []
    var graph: [Analysis.GraphDay] = []
    var totalSessions: Int = 0
    var lastScanAt: Date?
    var scanning = false
    var selectedSessionID: String?
    var isPaused = false {
        didSet { if !isPaused { refresh(force: false) } }
    }

    var onSummaryChange: ((String) -> Void)?
    var notifier: (any Notifier)?

    // MARK: Private

    private var state: SQLiteDB?
    private var cache: SQLiteDB?
    private var lifecycle: SessionLifecycle?
    private var coordinator: IngestCoordinator?
    private var daemon: Daemon?
    private var repoNames: [Int: String] = [:]
    private let work = DispatchQueue(label: "dev.builder.mac.work", qos: .utility)

    func start() {
        work.async { [weak self] in self?.openStores() }
    }

    private nonisolated func openStores() {
        do {
            let state = try SchemaManager.openState()
            let cache = try SchemaManager.openCache(tuningVersion: Tuning.version).db
            let lifecycle = SessionLifecycle(db: state)
            let coordinator = IngestCoordinator(db: state)

            Task { @MainActor [weak self] in
                guard let self else { return }
                self.state = state
                self.cache = cache
                self.lifecycle = lifecycle
                self.coordinator = coordinator
                self.startDaemon()
                self.refresh(force: false)
            }
        } catch {
            NSLog("builder: could not open store: \(error)")
        }
    }

    @MainActor
    private func startDaemon() {
        let daemon = Daemon(
            onEvent: { _ in },
            pass: { [weak self] in
                Task { @MainActor in self?.runPass() }
            })
        daemon.start()
        self.daemon = daemon
    }

    func refresh(force: Bool) {
        Task { @MainActor in runPass(force: force) }
    }

    // MARK: The pass

    @MainActor
    private func runPass(force: Bool = false) {
        guard !isPaused || force else { return }
        guard let state, let cache, let lifecycle, let coordinator, !scanning else { return }

        scanning = true
        let notifier = self.notifier

        work.async { [weak self] in
            var rows: [SessionRow] = []
            var live: SessionRow?
            var graphDays: [Analysis.GraphDay] = []
            var today: Double = 0
            var streak = 0
            var total = 0
            var names: [Int: String] = [:]

            do {
                _ = try coordinator.run()
                let sessions = try SessionDeriver.run(db: state, verbose: false)

                let transitions = try lifecycle.tick(sessions: sessions)
                let pending = try lifecycle.pendingNotifications(
                    for: sessions, transitions: transitions)

                names = (try? IngestCoordinator.repoNames(db: state)) ?? [:]

                var queue: [(session: DetectedSession, kind: SessionAlert.Kind)] = []
                for s in pending.sessionFinished { queue.append((session: s, kind: .sessionFinished)) }
                for s in pending.runFinished { queue.append((session: s, kind: .runFinished)) }

                for (s, kind) in queue {
                    let alert = SessionAlert(
                        session: s,
                        kind: kind,
                        repoName: try? Self.repoName(for: s.clientSessionID, cache: cache, names: names),
                        agentLines: (try? cache.scalarInt(
                            "SELECT agent_lines_added FROM session WHERE client_session_id = ?",
                            [.text(s.clientSessionID)])).flatMap { $0 } ?? 0,
                        prompts: s.promptCount)
                    // Recorded before delivery: at-most-once is the right side to err on.
                    try lifecycle.markNotified(s.clientSessionID, channel: notifier?.channel ?? "local")
                    try? notifier?.deliver(alert)
                }

                let analysis = Analysis(cache: cache, state: state)
                graphDays = try analysis.contributionGraph(days: 119)
                streak = try analysis.longestStreak().length
                total = try cache.scalarInt("SELECT COUNT(*) FROM session") ?? 0
                today = graphDays.last?.activeSeconds ?? 0

                rows = try Self.loadRows(cache: cache, names: names, limit: 12)
                if let openSession = try lifecycle.openSession(among: sessions) {
                    live = rows.first { $0.id == openSession.clientSessionID }
                        ?? Self.row(from: openSession, names: names)
                }
            } catch {
                NSLog("builder: pass failed: \(error)")
            }

            let capturedRows = rows
            let capturedLive = live
            let capturedGraph = graphDays
            let capturedToday = today
            let capturedStreak = streak
            let capturedTotal = total
            let capturedNames = names

            Task { @MainActor [weak self] in
                guard let self else { return }
                self.repoNames = capturedNames
                self.recent = capturedRows
                self.liveSession = capturedLive
                self.graph = capturedGraph
                self.todayActiveSeconds = capturedToday
                self.streakDays = capturedStreak
                self.totalSessions = capturedTotal
                self.lastScanAt = Date()
                self.scanning = false
                self.onSummaryChange?(Self.shortDuration(capturedToday))
            }
        }
    }

    // MARK: Sharing

    func share(sessionID: String) {
        guard let state, let cache else { return }
        Task { @MainActor in
            guard let model = try? RecapLoader.model(state: state, cache: cache, sessionID: sessionID)
            else { return }
            guard let data = try? ImageExport.png(of: RecapCardView(model: model)) else { return }
            ImageExport.copyToPasteboard(data)

            let path = (NSHomeDirectory() as NSString)
                .appendingPathComponent("Desktop/builder-\(String(sessionID.prefix(6))).png")
            try? data.write(to: URL(fileURLWithPath: path))
            NSWorkspace.shared.selectFile(path, inFileViewerRootedAtPath: NSHomeDirectory() + "/Desktop")
        }
    }

    // MARK: Loading

    private nonisolated static func repoName(
        for sessionID: String, cache: SQLiteDB, names: [Int: String]
    ) throws -> String? {
        try cache.scalarInt(
            "SELECT repo_id_primary FROM session WHERE client_session_id = ?", [.text(sessionID)]
        ).flatMap { names[$0] }
    }

    private nonisolated static func row(from s: DetectedSession, names: [Int: String]) -> SessionRow {
        SessionRow(
            id: s.clientSessionID, repo: "—",
            headline: shortDuration(s.activeSeconds),
            activeSeconds: s.activeSeconds, wallSeconds: s.wallSeconds,
            startedAt: s.startedAt, prompts: s.promptCount, strip: [], marks: [], notable: s.notable)
    }

    private nonisolated static func loadRows(
        cache: SQLiteDB, names: [Int: String], limit: Int
    ) throws -> [SessionRow] {
        var strips: [String: ([UInt8], [(ms: Int, kind: StripMarkKind)])] = [:]
        try cache.query("SELECT client_session_id, cols, marks FROM strip") { s in
            guard let id = s.text(0), let cols = s.blob(1) else { return }
            strips[id] = (cols, decodeMarks(s.text(2)))
        }

        var out: [SessionRow] = []
        try cache.query(
            """
            SELECT client_session_id, started_at, active_seconds, wall_seconds, title,
                   chore_title, repo_id_primary, n_prompts, agent_lines_added, notable
            FROM session WHERE notable = 1 ORDER BY started_at DESC LIMIT ?
            """,
            [.int(limit)]
        ) { s in
            guard let id = s.text(0) else { return }
            let title = s.text(4)
            let chore = s.bool(5)
            let lines = s.int(8) ?? 0
            let active = s.double(2) ?? 0
            let headline: String
            if let title, !title.isEmpty, !chore {
                headline = title
            } else if lines > 0 {
                headline = "+\(lines) lines"
            } else {
                headline = shortDuration(active)
            }
            let strip = strips[id]
            out.append(
                SessionRow(
                    id: id,
                    repo: s.int(6).flatMap { names[$0] } ?? "—",
                    headline: headline,
                    activeSeconds: active,
                    wallSeconds: s.double(3) ?? 0,
                    startedAt: s.double(1) ?? 0,
                    prompts: s.int(7) ?? 0,
                    strip: strip?.0 ?? [],
                    marks: strip?.1 ?? [],
                    notable: s.bool(9)))
        }
        return out
    }

    nonisolated static func decodeMarks(_ json: String?) -> [(ms: Int, kind: StripMarkKind)] {
        guard let json, let data = json.data(using: .utf8),
              let arr = try? JSONSerialization.jsonObject(with: data) as? [[Int]]
        else { return [] }
        return arr.compactMap {
            guard $0.count == 2, let k = StripMarkKind(rawValue: UInt8($0[1])) else { return nil }
            return (ms: $0[0], kind: k)
        }
    }

    nonisolated static func shortDuration(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        if s < 60 { return "\(s)s" }
        let h = s / 3600
        let m = (s % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }
}
