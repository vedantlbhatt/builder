import BuilderModel
import BuilderSQLite
import Foundation

/// Builds a fully-resolved `RecapModel` from the derived store.
///
/// Shared by the CLI and the menu bar app deliberately: two code paths producing "the
/// card" would drift, and the first symptom would be a shared image that disagrees with
/// what the app showed the person who shared it.
public enum RecapLoader {

    /// Load one session as a fully-resolved card model.
    ///
    /// Defaults to the most recent NOTABLE session rather than the most recent session
    /// outright — the latest row is frequently a two-minute question, and offering that as
    /// "your session" would make the feature look broken the first time anyone tries it.
    public static func model(state: SQLiteDB, cache: SQLiteDB, sessionID: String?) throws -> RecapModel? {
        var repoNames: [Int: String] = [:]
        try state.query("SELECT repo_id, COALESCE(display_name, origin_url_norm) FROM repo") { s in
            if let id = s.int(0), let n = s.text(1) { repoNames[id] = n }
        }

        // The longest notable session is the record holder; used to decide whether this
        // session is a personal best, which outranks every other headline. Judged on
        // ATTENDED seconds, the same measure `Analysis.records()` ranks by — a kickoff
        // prompt plus eight autonomous hours competes on its attended minutes.
        let bestSeconds =
            try cache.scalarDouble(
                "SELECT MAX(attended_seconds) FROM session WHERE notable = 1 AND unattended = 0") ?? 0

        let sql = """
            SELECT client_session_id, harness, started_at, active_seconds, wall_seconds,
                   title, chore_title, repo_id_primary, n_prompts, n_tool_calls,
                   n_files_touched, agent_lines_added, agent_lines_removed, git_commits,
                   tokens_reported, tok_out,
                   tok_in + tok_out + tok_cache_read + tok_cache_w5m + tok_cache_w1h,
                   models_json, agent_line_bucket, attrib_confidence, attended_seconds
            FROM session
            WHERE \(sessionID != nil ? "client_session_id = ?" : "notable = 1")
            ORDER BY started_at DESC LIMIT 1
            """

        var model: RecapModel?
        try cache.query(sql, sessionID.map { [.text($0)] } ?? []) { s in
            guard let id = s.text(0), let harness = Harness(rawValue: s.text(1) ?? "") else { return }
            let active = s.double(3) ?? 0
            let attended = s.double(20) ?? 0

            var models: [String] = []
            if let json = s.text(17), let data = json.data(using: .utf8),
               let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] {
                models = arr.compactMap { $0["model_id"] as? String }
            }

            model = RecapModel(
                clientSessionID: id,
                harness: harness,
                repoName: s.int(7).flatMap { repoNames[$0] },
                startedAt: s.double(2) ?? 0,
                activeSeconds: active,
                wallSeconds: s.double(4) ?? 0,
                title: s.text(5),
                choreTitle: s.bool(6),
                prompts: s.int(8) ?? 0,
                toolCalls: s.int(9) ?? 0,
                filesTouched: s.int(10) ?? 0,
                agentLinesAdded: s.int(11) ?? 0,
                agentLinesRemoved: s.int(12) ?? 0,
                commits: s.int(13) ?? 0,
                tokensReported: s.bool(14),
                outputTokens: s.int(15) ?? 0,
                totalTokens: s.int(16) ?? 0,
                models: models,
                agentLineBucket: AgentLineBucket(rawValue: s.text(18) ?? "") ?? .unknown,
                attribConfidence: AttributionConfidence(rawValue: s.text(19) ?? "") ?? .none,
                stripColumns: [],
                stripMarks: [],
                isPersonalRecord: attended >= bestSeconds && bestSeconds > 0,
                recordKind: "session")
        }

        guard var m = model else { return nil }

        try cache.query(
            "SELECT cols, marks, t0_ms, t1_ms FROM strip WHERE client_session_id = ?",
            [.text(m.clientSessionID)]
        ) { s in
            m.stripColumns = s.blob(0) ?? []
            m.stripMarks = RecapLoader.decodeMarks(s.text(1))
        }

        // A card with no strip is not a card. Fall back to a flat idle track rather than
        // rendering an empty rectangle.
        if m.stripColumns.isEmpty {
            m.stripColumns = [UInt8](repeating: StripSpec.pack(.idle, density: 0), count: StripSpec.columns)
        }
        return m
    }

    public static func decodeMarks(_ json: String?) -> [(ms: Int, kind: StripMarkKind)] {
        guard let json, let data = json.data(using: .utf8),
              let arr = try? JSONSerialization.jsonObject(with: data) as? [[Int]]
        else { return [] }
        return arr.compactMap {
            guard $0.count == 2, let k = StripMarkKind(rawValue: UInt8($0[1])) else { return nil }
            return (ms: $0[0], kind: k)
        }
    }
}
