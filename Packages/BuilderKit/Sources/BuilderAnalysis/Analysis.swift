import BuilderModel
import BuilderSQLite
import Foundation

/// The derivation layer: records, contribution graph, project arcs, attribution.
///
/// Everything here is a query over `cache.sqlite`, which is a pure function of the durable
/// store plus `Tuning`. Nothing in this file may write, and nothing may accumulate — a
/// record is always recomputed from scratch, because an accumulated record survives a
/// re-derive and would let a bug become permanent.
public struct Analysis {

    let cache: SQLiteDB
    let state: SQLiteDB

    public init(cache: SQLiteDB, state: SQLiteDB) {
        self.cache = cache
        self.state = state
    }

    // MARK: - Records

    public struct Record {
        public let label: String
        public let formatted: String
        public let context: String
    }

    /// Personal records. Self-referential by design — the product has no leaderboard, so
    /// the only thing to beat is you.
    ///
    /// Every record excludes `unattended` sessions. The longest session in the reference
    /// corpus was a 5h40m autonomous run with zero typed prompts; a record for that is a
    /// record for the machine.
    public func records() throws -> [Record] {
        var out: [Record] = []

        try cache.query(
            """
            SELECT active_seconds, day, repo_id_primary, started_at
            FROM session
            WHERE notable = 1 AND unattended = 0
            ORDER BY active_seconds DESC LIMIT 1
            """
        ) { s in
            out.append(
                Record(
                    label: "longest session",
                    formatted: fmtDuration(s.double(0) ?? 0),
                    context: dateString(s.double(3) ?? 0)))
        }

        try cache.query(
            """
            SELECT day, SUM(active_seconds) FROM session
            WHERE visible = 1 AND unattended = 0
            GROUP BY day ORDER BY 2 DESC LIMIT 1
            """
        ) { s in
            out.append(
                Record(
                    label: "biggest day",
                    formatted: fmtDuration(s.double(1) ?? 0),
                    context: s.text(0) ?? ""))
        }

        let streak = try longestStreak()
        out.append(
            Record(
                label: "longest streak",
                formatted: "\(streak.length) days",
                context: streak.length > 0 ? "\(streak.start) → \(streak.end)" : ""))

        try cache.query(
            """
            SELECT day, SUM(tok_out) FROM session
            WHERE tokens_reported = 1 GROUP BY day ORDER BY 2 DESC LIMIT 1
            """
        ) { s in
            out.append(
                Record(
                    label: "most output tokens",
                    formatted: fmtInt(s.int(1) ?? 0),
                    context: s.text(0) ?? ""))
        }

        try cache.query(
            "SELECT COUNT(*), SUM(active_seconds) FROM session WHERE visible = 1"
        ) { s in
            out.append(
                Record(
                    label: "all time",
                    formatted: fmtDuration(s.double(1) ?? 0),
                    context: "\(fmtInt(s.int(0) ?? 0)) sessions"))
        }

        return out
    }

    /// Consecutive local days with at least one counted session.
    ///
    /// A session that crosses midnight belongs entirely to the day it STARTED, which is
    /// already denormalized into `session.day`. Splitting it would manufacture a two-day
    /// streak out of one sitting, and this audience skews nocturnal.
    public func longestStreak() throws -> (length: Int, start: String, end: String) {
        var days: [String] = []
        try cache.query(
            "SELECT DISTINCT day FROM session WHERE visible = 1 AND day IS NOT NULL ORDER BY day"
        ) { s in
            if let d = s.text(0) { days.append(d) }
        }
        guard !days.isEmpty else { return (0, "", "") }

        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd"
        df.timeZone = TimeZone.current

        var best = 1
        var bestStart = days[0]
        var bestEnd = days[0]
        var runStart = days[0]
        var run = 1

        for i in 1..<days.count {
            guard let prev = df.date(from: days[i - 1]), let cur = df.date(from: days[i]) else {
                continue
            }
            let gapDays = Int((cur.timeIntervalSince(prev) / 86400).rounded())
            if gapDays == 1 {
                run += 1
            } else {
                run = 1
                runStart = days[i]
            }
            if run > best {
                best = run
                bestStart = runStart
                bestEnd = days[i]
            }
        }
        return (best, bestStart, bestEnd)
    }

    // MARK: - Contribution graph

    public struct GraphDay {
        public let day: String
        public let activeSeconds: Double
        public let level: Int
    }

    /// Coloured by ACTIVE HOURS, not tokens.
    ///
    /// Hours are the honest metric — every harness has them, including the ones that never
    /// write a token count. Tokens are the flex metric, shown but never ranked by. The
    /// bucket edges are absolute rather than per-user quantiles, so two people's graphs
    /// mean the same thing.
    public func contributionGraph(days: Int) throws -> [GraphDay] {
        var byDay: [String: Double] = [:]
        try cache.query(
            """
            SELECT day, SUM(active_seconds) FROM session
            WHERE visible = 1 AND day IS NOT NULL GROUP BY day
            """
        ) { s in
            if let d = s.text(0) { byDay[d] = s.double(1) ?? 0 }
        }

        var out: [GraphDay] = []
        for offset in stride(from: days - 1, through: 0, by: -1) {
            let date = Calendar.current.date(byAdding: .day, value: -offset, to: Date()) ?? Date()
            let key = Tuning.localDay(for: date)
            let seconds = byDay[key] ?? 0
            out.append(GraphDay(day: key, activeSeconds: seconds, level: level(forHours: seconds / 3600)))
        }
        return out
    }

    public func level(forHours h: Double) -> Int {
        var lvl = 0
        for edge in Tuning.graphHourBuckets where h > edge { lvl += 1 }
        return min(lvl, 5)
    }

    /// Terminal rendering of the graph, laid out the way the app will: weeks as columns,
    /// weekdays as rows.
    public func renderGraph(_ days: [GraphDay]) -> String {
        let glyphs = [" ", "░", "▒", "▓", "█", "█"]
        let colours = [237, 94, 136, 178, 214, 220]

        var byWeekday: [[String]] = Array(repeating: [], count: 7)
        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd"

        for d in days {
            guard let date = df.date(from: d.day) else { continue }
            let weekday = (Calendar.current.component(.weekday, from: date) + 5) % 7
            let cell = "\u{1B}[38;5;\(colours[min(d.level, 5)])m\(glyphs[min(d.level, 5)])\u{1B}[0m"
            byWeekday[weekday].append(cell)
        }

        let labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        var out: [String] = []
        for (i, row) in byWeekday.enumerated() {
            out.append("  \(labels[i])  " + row.joined())
        }
        let total = days.reduce(0.0) { $0 + $1.activeSeconds }
        let activeDays = days.filter { $0.activeSeconds > 0 }.count
        out.append(
            "  \(fmtDuration(total)) across \(activeDays) of \(days.count) days")
        return out.joined(separator: "\n")
    }

    // MARK: - Project arcs

    public struct ProjectArc {
        public let repoID: Int
        public let name: String
        public let sessions: Int
        public let activeSeconds: Double
        public let firstSession: Double
        public let lastSession: Double
    }

    /// Sessions grouped by repository, first to latest. The portfolio view.
    public func projectArcs() throws -> [ProjectArc] {
        var names: [Int: String] = [:]
        try state.query("SELECT repo_id, COALESCE(display_name, origin_url_norm) FROM repo") { s in
            if let id = s.int(0), let n = s.text(1) { names[id] = n }
        }

        var out: [ProjectArc] = []
        try cache.query(
            """
            SELECT repo_id_primary, COUNT(*), SUM(active_seconds), MIN(started_at), MAX(started_at)
            FROM session WHERE visible = 1 AND repo_id_primary IS NOT NULL
            GROUP BY repo_id_primary ORDER BY 3 DESC
            """
        ) { s in
            guard let id = s.int(0) else { return }
            out.append(
                ProjectArc(
                    repoID: id,
                    name: names[id] ?? "repo \(id)",
                    sessions: s.int(1) ?? 0,
                    activeSeconds: s.double(2) ?? 0,
                    firstSession: s.double(3) ?? 0,
                    lastSession: s.double(4) ?? 0))
        }
        return out
    }

    // MARK: - Attribution

    public struct AttributionSummary {
        public let agentLines: Int
        public let humanEditEvents: Int
        public let prompts: Int
    }

    /// Three separately measured numbers, never combined.
    ///
    /// `edited_text_file` measures EXISTENCE of human editing and carries no line count —
    /// 296 records against 2,808 agent edits on the reference machine. And most sessions
    /// have no commit in their window, so `gitInsertions - agentAdded` reads zero human
    /// regardless of how much was typed. A percentage here would be fiction with a
    /// decimal point.
    public func attributionSummary() throws -> AttributionSummary {
        var agent = 0
        var human = 0
        var prompts = 0
        try cache.query(
            "SELECT SUM(agent_lines_added), SUM(n_human_edit_events), SUM(n_prompts) FROM session"
        ) { s in
            agent = s.int(0) ?? 0
            human = s.int(1) ?? 0
            prompts = s.int(2) ?? 0
        }
        return AttributionSummary(agentLines: agent, humanEditEvents: human, prompts: prompts)
    }

    /// Model labels by share of output tokens. Labels only, never a price.
    public func modelShare() throws -> [(String, Double)] {
        var byModel: [String: Int] = [:]
        try state.query(
            """
            SELECT model, SUM(tok_out) FROM raw_event
            WHERE usage_authoritative = 1 AND model IS NOT NULL GROUP BY model
            """
        ) { s in
            if let m = s.text(0) { byModel[m] = s.int(1) ?? 0 }
        }
        let total = max(byModel.values.reduce(0, +), 1)
        return byModel.sorted { $0.value > $1.value }.map { ($0.key, Double($0.value) / Double(total)) }
    }

    // MARK: - Formatting helpers

    private func fmtDuration(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        if s < 60 { return "\(s)s" }
        let h = s / 3600
        let m = (s % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }

    private func fmtInt(_ n: Int) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: n)) ?? "\(n)"
    }

    private func dateString(_ ts: Double) -> String {
        let df = DateFormatter()
        df.dateFormat = "EEE d MMM"
        return df.string(from: Date(timeIntervalSince1970: ts))
    }
}
