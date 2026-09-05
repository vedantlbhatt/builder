import BuilderAnalysis
import BuilderIngest
import BuilderModel
import BuilderSchema
import BuilderSQLite
import BuilderStore
import BuilderUI
import Foundation

/// `builder preview` — render the app's own surfaces to PNGs, from real data.
///
/// Useful for looking at the product without launching it, and genuinely useful for
/// layout work: a menu bar popover is awkward to inspect, and on a crowded menu bar the
/// status item may be hidden in the overflow entirely.
enum PreviewCommand {

    static func run() throws {
        let outDir = CLIArgs.value("out") ?? "/tmp/builder-preview"
        try? FileManager.default.createDirectory(
            atPath: outDir, withIntermediateDirectories: true)

        let state = try SchemaManager.openState()
        let (cache, _) = try SchemaManager.openCache(tuningVersion: Tuning.version)

        let panel = try menuBarModel(state: state, cache: cache)

        try MainActor.assumeIsolated {
            // Menu bar popover
            let panelPath = "\(outDir)/menubar.png"
            let panelData = try ImageExport.png(of: MenuBarPanel(model: panel), scale: 2)
            try panelData.write(to: URL(fileURLWithPath: panelPath))
            print("  \(panelPath)  (420x560 @2x)")

            // The analysis block on its own, when the previewed session has one. The
            // full panel may push it below the 560pt fold; this frame keeps two recent
            // rows so the block is always in view.
            if let analysis = panel.analysis {
                let focused = MenuBarPanel.Model(
                    todayActiveSeconds: panel.todayActiveSeconds,
                    streakDays: panel.streakDays,
                    totalSessions: panel.totalSessions,
                    allTimeSeconds: panel.allTimeSeconds,
                    live: panel.live,
                    recent: Array(panel.recent.prefix(2)),
                    graph: panel.graph,
                    phone: panel.phone,
                    analysis: analysis)
                let path = "\(outDir)/panel-analysis.png"
                let data = try ImageExport.png(of: MenuBarPanel(model: focused), scale: 2)
                try data.write(to: URL(fileURLWithPath: path))
                print("  \(path)  (420x560 @2x)")
            }

            // Cards for the most interesting sessions
            for (label, id) in try topSessionIDs(cache: cache) {
                guard let model = try RecapLoader.model(state: state, cache: cache, sessionID: id)
                else { continue }
                let path = "\(outDir)/card-\(label).png"
                _ = try ImageExport.writeCard(model, to: path)
                print("  \(path)")
            }
        }
    }

    /// The two sessions worth looking at: the longest, and the most recent notable one.
    private static func topSessionIDs(cache: SQLiteDB) throws -> [(String, String)] {
        var out: [(String, String)] = []
        try cache.query(
            "SELECT client_session_id FROM session WHERE notable = 1 AND unattended = 0 "
                + "ORDER BY attended_seconds DESC LIMIT 1"
        ) { s in
            if let id = s.text(0) { out.append(("longest", id)) }
        }
        try cache.query(
            "SELECT client_session_id FROM session WHERE notable = 1 "
                + "ORDER BY started_at DESC LIMIT 1"
        ) { s in
            if let id = s.text(0) { out.append(("latest", id)) }
        }
        return out
    }

    static func menuBarModel(state: SQLiteDB, cache: SQLiteDB) throws -> MenuBarPanel.Model {
        var repoNames: [Int: String] = [:]
        try state.query("SELECT repo_id, COALESCE(display_name, origin_url_norm) FROM repo") { s in
            if let id = s.int(0), let n = s.text(1) { repoNames[id] = n }
        }

        var strips: [String: ([UInt8], [(ms: Int, kind: StripMarkKind)])] = [:]
        try cache.query("SELECT client_session_id, cols, marks FROM strip") { s in
            guard let id = s.text(0), let cols = s.blob(1) else { return }
            strips[id] = (cols, RecapLoader.decodeMarks(s.text(2)))
        }

        var rows: [MenuBarPanel.SessionRow] = []
        try cache.query(
            """
            SELECT client_session_id, started_at, active_seconds, wall_seconds, title,
                   chore_title, repo_id_primary, n_prompts, agent_lines_added, git_commits
            FROM session WHERE notable = 1 ORDER BY started_at DESC LIMIT 4
            """
        ) { s in
            guard let id = s.text(0) else { return }
            let title = s.text(4)
            let chore = s.bool(5)
            let lines = s.int(8) ?? 0
            let commits = s.int(9) ?? 0
            let active = s.double(2) ?? 0

            // The same ladder the card uses, in miniature.
            let headline: String
            if commits >= 5 {
                headline = "\(commits) commits"
            } else if lines >= 1000 {
                headline = "+\(lines) lines"
            } else if let title, !title.isEmpty, !chore {
                headline = title
            } else {
                headline = shortDuration(active)
            }

            let strip = strips[id]
            rows.append(
                MenuBarPanel.SessionRow(
                    id: id,
                    repo: s.int(6).flatMap { repoNames[$0] } ?? "—",
                    headline: headline,
                    activeSeconds: active,
                    wallSeconds: s.double(3) ?? 0,
                    startedAt: s.double(1) ?? 0,
                    prompts: s.int(7) ?? 0,
                    commits: commits,
                    strip: strip?.0 ?? [],
                    marks: strip?.1 ?? []))
        }

        let analysis = Analysis(cache: cache, state: state)
        let graph = try analysis.contributionGraph(days: 119)
        let streak = try analysis.longestStreak().length
        let today = graph.last?.activeSeconds ?? 0

        var total = 0
        var allTime = 0.0
        try cache.query(
            "SELECT COUNT(*), COALESCE(SUM(active_seconds), 0) FROM session WHERE visible = 1"
        ) { s in
            total = s.int(0) ?? 0
            allTime = s.double(1) ?? 0
        }

        // The previewed session is the latest notable one — the first row. Its stored
        // analysis, if any, feeds both the panel's block and the `panel-analysis` frame.
        var summary: MenuBarPanel.AnalysisSummary?
        if let id = rows.first?.id,
           let record = try AnalysisStore.record(for: id, in: state),
           let decoded = try? record.decoded() {
            summary = MenuBarPanel.AnalysisSummary(analysis: decoded, checkpoint: record.checkpoint)
        }

        return MenuBarPanel.Model(
            todayActiveSeconds: today,
            streakDays: streak,
            totalSessions: total,
            allTimeSeconds: allTime,
            live: nil,
            recent: rows,
            graph: graph,
            analysis: summary)
    }

    private static func shortDuration(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        let h = s / 3600
        let m = (s % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }
}
