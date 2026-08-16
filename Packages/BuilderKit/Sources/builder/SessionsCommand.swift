import BuilderIngest
import BuilderModel
import BuilderSchema
import BuilderSQLite
import Foundation

/// `builder sessions` — what the feed will show, in a terminal.
///
/// Deliberately the same shape as the recap row in the app: a title, a duration, and the
/// strip. Rendering it here first means the strip spec gets exercised by the cheapest
/// possible renderer before any SwiftUI or SVG exists.
enum SessionsCommand {

    static func run() throws {
        let limit = Int(CLIArgs.value("limit") ?? "20") ?? 20
        let showAll = CLIArgs.flag("all")

        let state = try SchemaManager.openState()
        let (cache, _) = try SchemaManager.openCache(tuningVersion: Tuning.version)

        let count = try cache.scalarInt("SELECT COUNT(*) FROM session") ?? 0
        if count == 0 {
            print("No sessions yet. Run `builder scan` first.")
            return
        }

        var repoNames: [Int: String] = [:]
        try state.query("SELECT repo_id, COALESCE(display_name, origin_url_norm) FROM repo") { s in
            if let id = s.int(0), let n = s.text(1) { repoNames[id] = n }
        }

        var strips: [String: (cols: [UInt8], marks: String, span: Int)] = [:]
        try cache.query("SELECT client_session_id, cols, marks, t0_ms, t1_ms FROM strip") { s in
            if let id = s.text(0), let blob = s.blob(1) {
                strips[id] = (blob, s.text(2) ?? "[]", (s.int(4) ?? 0) - (s.int(3) ?? 0))
            }
        }

        let filter = showAll ? "" : "WHERE notable = 1"
        var rows = 0

        print("")
        try cache.query(
            """
            SELECT client_session_id, harness, started_at, active_seconds, wall_seconds,
                   title, chore_title, repo_id_primary, n_prompts, n_tool_calls,
                   agent_lines_added, tok_out, tokens_reported, unattended, notable,
                   agent_line_bucket
            FROM session \(filter)
            ORDER BY started_at DESC LIMIT ?
            """,
            [.int(limit)]
        ) { s in
            rows += 1
            let id = s.text(0) ?? ""
            let repo = s.int(7).flatMap { repoNames[$0] } ?? "—"
            let title = s.text(5)
            let chore = s.bool(6)
            let active = s.double(3) ?? 0
            let wall = s.double(4) ?? 0

            // The superlative ladder in miniature: a chore-log title is worse than no
            // title, because it makes the session look trivial when it was not.
            let headline: String
            if let title, !title.isEmpty, !chore {
                headline = title
            } else {
                let agentLines = s.int(10) ?? 0
                headline = agentLines > 0 ? "+\(Fmt.int(agentLines)) lines" : Fmt.duration(active)
            }

            let flags =
                (s.bool(13) ? " [unattended]" : "") + (s.bool(14) ? "" : " [minor]")

            print("  \(Fmt.date(s.double(2) ?? 0))  \(Fmt.pad(repo, 20)) \(headline)\(flags)")
            print(
                "  \(Fmt.pad("", 17))\(Fmt.pad(Fmt.duration(active) + " active", 14))"
                    + "\(Fmt.pad("of " + Fmt.duration(wall), 14))"
                    + "\(Fmt.rpad(Fmt.int(s.int(8) ?? 0), 4)) prompts  "
                    + "\(Fmt.rpad(Fmt.int(s.int(9) ?? 0), 5)) tools  "
                    + (s.bool(12) ? "\(Fmt.int(s.int(11) ?? 0)) out-tokens" : "tokens n/a"))

            if let strip = strips[id] {
                print(
                    "  \(Fmt.pad("", 17))"
                        + AnsiStrip.render(
                            cols: strip.cols, width: 60,
                            marks: AnsiStrip.decodeMarks(strip.marks), spanMs: strip.span))
            }
            print("")
        }

        if rows == 0 {
            print("  No notable sessions. Try --all to include short and unattended ones.")
        }
    }
}
