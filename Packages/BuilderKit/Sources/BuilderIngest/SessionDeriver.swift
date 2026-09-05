import BuilderGit
import BuilderModel
import BuilderParse
import BuilderSchema
import BuilderSQLite
import Foundation

/// Turns the durable event store into the derived session index.
///
/// Split out from ingest because it is a pure function of `state.sqlite` plus `Tuning`:
/// it can be re-run at any time, and it MUST be re-run whenever a constant changes, or a
/// user keeps a contribution graph bucketed under thresholds that no longer exist.
public enum SessionDeriver {

    @discardableResult
    public static func run(db: SQLiteDB, verbose: Bool = false) throws -> [DetectedSession] {
        let (cache, didRebuild) = try SchemaManager.openCache(tuningVersion: Tuning.version)
        if verbose && didRebuild {
            print("\nDERIVE")
            print("  cache rebuilt (tuning \(Tuning.version))")
        } else if verbose {
            print("\nDERIVE")
        }

        let events = try IngestCoordinator.loadEvents(db: db)
        let repoNames = try IngestCoordinator.repoNames(db: db)

        // Pool by resolved repository. `repo_id` was attached at ingest, so this needs no
        // git calls and works even for repositories that have since been deleted.
        let pooling = Sessionizer.Pooling.explicit { e in
            let repo = e.extra?["repo_id"] ?? e.cwd ?? "unknown"
            return "\(e.harness.rawValue)|\(repo)"
        }

        let sessions = Sessionizer.sessions(from: events, options: .init(pooling: pooling))

        try writeSessions(sessions, events: events, repoNames: repoNames, state: db, to: cache)

        if verbose {
            let counted = sessions.filter(\.counted)
            let notable = sessions.filter(\.notable)
            let unattended = sessions.filter(\.unattended)
            let active = sessions.reduce(0.0) { $0 + $1.activeSeconds }
            print("  sessions              \(SessionDeriver.int(sessions.count))")
            print("  counted               \(SessionDeriver.int(counted.count))   (toward hours, graph, streaks)")
            print("  notable               \(SessionDeriver.int(notable.count))   (card, record, notification)")
            print("  unattended            \(SessionDeriver.int(unattended.count))   (no presence signal; hours only)")
            print("  active time           \(SessionDeriver.duration(active))")
        }
        return sessions
    }

    /// Commits and line deltas for a session's window, memoised in Tier A.
    ///
    /// Cached in `git_cache` rather than recomputed each derive for two reasons: `git log`
    /// over 558 windows is slow enough to be felt, and — the real one — a repository can be
    /// DELETED. Once the working copy is gone the numbers are unrecoverable, so they live in
    /// the durable store next to the events rather than in the disposable index.
    private static func gitStats(
        session: DetectedSession, repoID: Int?, roots: [Int: String], state: SQLiteDB,
        enricher: GitEnricher
    ) -> GitEnricher.WindowStats {
        guard let repoID, let root = roots[repoID] else { return .zero }

        let winStart = session.startedAt
        // Look back a little before the session: a commit is made at the END of a stretch
        // of work, and the edits that produced it often start before the window does.
        let lookback = winStart - Tuning.tauCommitAttributionSec
        let winEnd = session.endedAt

        if let cached = try? state.prepare(
            "SELECT commits, insertions, deletions, files_changed FROM git_cache "
                + "WHERE repo_id = ? AND win_start = ? AND win_end = ?") {
            defer { cached.finalize() }
            try? cached.bind([.int(repoID), .double(lookback), .double(winEnd)])
            if (try? cached.step()) == true {
                return GitEnricher.WindowStats(
                    commits: cached.int(0) ?? 0, insertions: cached.int(1) ?? 0,
                    deletions: cached.int(2) ?? 0, filesChanged: cached.int(3) ?? 0)
            }
        }

        let stats = enricher.stats(cwd: root, from: lookback, to: winEnd)
        try? state.run(
            "INSERT OR REPLACE INTO git_cache "
                + "(repo_id, win_start, win_end, commits, insertions, deletions, files_changed, "
                + "author_filtered, computed_at) VALUES (?,?,?,?,?,?,?,0,?)",
            [
                .int(repoID), .double(lookback), .double(winEnd),
                .int(stats.commits), .int(stats.insertions), .int(stats.deletions),
                .int(stats.filesChanged), .double(Date().timeIntervalSince1970),
            ])
        return stats
    }

    private static func writeSessions(
        _ sessions: [DetectedSession],
        events: [NormalizedEvent],
        repoNames: [Int: String],
        state: SQLiteDB,
        to cache: SQLiteDB
    ) throws {
        // Where to run git for each repo. `common_root` is the shared .git directory, so
        // all worktrees of one repository resolve to the same place.
        var repoRoots: [Int: String] = [:]
        try state.query(
            "SELECT repo_id, common_root FROM repo WHERE common_root IS NOT NULL"
        ) { s in
            if let id = s.int(0), let root = s.text(1) { repoRoots[id] = root }
        }
        let enricher = GitEnricher()
        try cache.exec("DELETE FROM session")
        try cache.exec("DELETE FROM session_repo")
        try cache.exec("DELETE FROM strip")

        let insertStrip = try cache.prepare(
            "INSERT OR REPLACE INTO strip (client_session_id, t0_ms, t1_ms, cols, marks, spec_version) "
                + "VALUES (?,?,?,?,?,?)")
        defer { insertStrip.finalize() }

        let insert = try cache.prepare(
            """
            INSERT OR REPLACE INTO session (
              client_session_id, harness, started_at, ended_at, wall_seconds, active_seconds,
              idle_seconds, active_calc_version, sessionizer_version, day, hour, dow,
              tz_offset_min, repo_id_primary, title, title_source, chore_title,
              timeline_fidelity, state, visible, notable, n_prompts, prompt_count_basis,
              n_tool_calls, n_reads, n_edits, n_writes, n_bash, n_files_touched,
              n_files_created, n_subagents, n_compactions, n_human_edit_events,
              agent_lines_added, agent_lines_removed, git_commits, git_insertions,
              git_deletions, agent_line_bucket, attrib_confidence,
              tok_in, tok_out, tok_cache_read, tok_cache_w5m, tok_cache_w1h,
              abandoned_branch_tokens, tokens_reported, token_dedupe, token_scope,
              token_coverage, cost_usd, cost_state, models_json, model_state,
              is_background, unattended, time_quality, merge_group_id, visibility,
              attended_seconds, autonomous_seconds, n_presence, end_reason, run_finished
            ) VALUES (?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?,?,
                      ?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,
                      ?,?,?,?,?)
            """
        )
        defer { insert.finalize() }

        let cal = Calendar(identifier: .gregorian)

        try cache.transaction {
            for s in sessions {
                let evs = s.eventIndices.map { events[$0] }
                let ledger = TokenAccountant.ledger(evs, harness: s.harness)
                let lines = TokenAccountant.agentLines(evs)

                // Title resolution: prefer the title the harness already wrote to disk,
                // and only accept one whose leafUuid points at a record inside THIS
                // session. One file routinely yields many sessions but carries a single
                // current title, so "last title in the file" would stamp today's title on
                // a card for three days ago.
                let ids = Set(evs.compactMap(\.nativeEventID))
                let title = evs.last(where: { e in
                    guard e.kind == .title, let t = e.title, !t.isEmpty else { return false }
                    guard let leaf = e.leafUUID else { return true }
                    return ids.contains(leaf)
                })?.title
                let chore =
                    title?.range(of: Tuning.choreTitlePattern, options: .regularExpression) != nil

                let date = Date(timeIntervalSince1970: s.startedAt)
                let c = cal.dateComponents([.year, .month, .day, .hour, .weekday], from: date)
                let day = Tuning.localDay(for: date, calendar: cal)

                let repoID = evs.compactMap { $0.extra?["repo_id"] }.first.flatMap(Int.init)
                let git = gitStats(
                    session: s, repoID: repoID, roots: repoRoots, state: state, enricher: enricher)
                let attrib = TokenAccountant.attribution(
                    agentAdded: lines.added,
                    gitInsertions: git.insertions,
                    gitCommits: git.commits,
                    humanEditEvents: evs.filter { $0.kind == .humanEdit }.count)

                func count(_ k: EventKind) -> Int { evs.filter { $0.kind == k }.count }
                func tool(_ n: String) -> Int {
                    evs.filter { $0.kind == .toolUse && $0.toolName == n && $0.onLivePath != false }.count
                }

                try insert.execute([
                    .text(s.clientSessionID), .text(s.harness.rawValue),
                    .double(s.startedAt), .double(s.endedAt),
                    .double(s.wallSeconds), .double(s.activeSeconds), .double(s.idleSeconds),
                    .int(Tuning.activeCalcVersion), .int(Tuning.sessionizerVersion),
                    .text(day), .optionalInt(c.hour), .int((c.weekday ?? 1) - 1),
                    .int(TimeZone.current.secondsFromGMT(for: date) / 60),
                    .optionalInt(repoID),
                    .optionalText(title), .text(title != nil ? "harness" : "template"),
                    .bool(chore),
                    // `state` here is the derived row's shape, not the live state: the
                    // lifecycle table decides open/idle/final for a session still capable
                    // of growing. A cut session (`isCut`) is final by construction.
                    .text(TimelineFidelity.full.rawValue), .text("final"),
                    .bool(s.counted), .bool(s.notable),
                    .int(s.promptCount), .text("typed_promptsource"),
                    .int(count(.toolUse)), .int(tool("Read")), .int(tool("Edit")),
                    .int(tool("Write")), .int(tool("Bash")),
                    .int(Set(evs.compactMap(\.targetPath)).count), .int(0),
                    .int(Set(evs.compactMap(\.agentID)).count), .int(count(.compaction)),
                    .int(count(.humanEdit)),
                    .int(lines.added), .int(lines.removed),
                    .int(git.commits), .int(git.insertions), .int(git.deletions),
                    .text(attrib.bucket.rawValue), .text(attrib.confidence.rawValue),
                    .int(ledger.buckets.input), .int(ledger.buckets.output),
                    .int(ledger.buckets.cacheRead), .int(ledger.buckets.cacheWrite5m),
                    .int(ledger.buckets.cacheWrite1h),
                    .int(ledger.abandonedBranchTokens), .bool(ledger.reported),
                    .text(ledger.dedupe.rawValue), .text(ledger.scope.rawValue),
                    .text(ledger.coverage.rawValue),
                    .null, .text(ledger.reported ? "computed" : "unavailable"),
                    .optionalText(modelsJSON(evs)),
                    .text(s.harness.reportsModel ? "known" : "unknown"),
                    .int(0), .bool(s.unattended), .text("ok"), .null, .text("anonymous"),
                    .double(s.attendedSeconds), .double(s.autonomousSeconds),
                    .int(s.presenceCount), .text(s.endReason.rawValue), .bool(s.runFinished),
                ])

                if let repoID {
                    try cache.run(
                        "INSERT OR REPLACE INTO session_repo VALUES (?,?,?,?)",
                        [.text(s.clientSessionID), .int(repoID), .int(evs.count),
                         .double(s.activeSeconds)])
                }

                // The strip is built from live-path events only: a rewound edit never
                // reached the file, so painting it would show work that does not exist.
                let strip = StripBuilder.build(
                    events: evs.filter { $0.onLivePath != false },
                    startedAt: s.startedAt, endedAt: s.endedAt)
                try insertStrip.execute([
                    .text(s.clientSessionID), .int(strip.t0Ms), .int(strip.t1Ms),
                    .blob(strip.cols), .text(strip.marksJSON), .int(StripSpec.version),
                ])
            }
        }
    }

    /// Model labels with their share of output tokens. Labels only — never a price.
    private static func modelsJSON(_ events: [NormalizedEvent]) -> String? {
        var byModel: [String: Int] = [:]
        for e in events where e.usageAuthoritative {
            guard let m = e.model, m != Tuning.syntheticModelSentinel else { continue }
            byModel[m, default: 0] += e.tokOut ?? 0
        }
        guard !byModel.isEmpty else { return nil }
        let total = max(byModel.values.reduce(0, +), 1)
        let arr = byModel.sorted { $0.value > $1.value }.map {
            ["model_id": $0.key, "output_token_share": String(format: "%.4f", Double($0.value) / Double(total))]
        }
        return (try? JSONSerialization.data(withJSONObject: arr)).map {
            String(decoding: $0, as: UTF8.self)
        }
    }
}

extension SessionDeriver {
    static func int(_ n: Int) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: n)) ?? "\(n)"
    }

    static func duration(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        let h = s / 3600
        let m = (s % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }
}
