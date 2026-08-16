import BuilderGit
import BuilderIngest
import BuilderModel
import BuilderParse
import BuilderSchema
import BuilderSQLite
import Foundation

/// `builder scan` — parse everything new on disk into the durable store.
///
/// This is the operation the menu bar app runs on launch and on every file-system event.
/// Running it twice in a row must do almost nothing the second time; if it does not, the
/// watermarking is broken and the steady-state daemon would re-read 1.2 GB every tick.
enum ScanCommand {

    static func run() throws {
        let force = CLIArgs.flag("rebuild")
        let db = try SchemaManager.openState()

        if force {
            print("--rebuild: clearing the derived index and re-reading every source.")
            print("(state.sqlite keeps its events — they may be the only copy left.)")
            try db.exec("DELETE FROM ingest_watermark")
        }

        let before = try db.scalarInt("SELECT COUNT(*) FROM raw_event") ?? 0

        let coordinator = IngestCoordinator(db: db)
        var lastPrinted = -1

        let result = try coordinator.run { p in
            let pct = Int(p.fraction * 100)
            guard pct != lastPrinted else { return }
            lastPrinted = pct
            let name = p.currentPath.map { ($0 as NSString).lastPathComponent } ?? "done"
            let line = "  \(Fmt.bar(p.fraction)) \(Fmt.rpad("\(pct)", 3))%  "
                + "\(Fmt.rpad(Fmt.int(p.sourcesDone), 4))/\(p.sourcesTotal) sources  "
                + String(name.prefix(28))
            print("\u{1B}[2K\r" + line, terminator: "")
            fflush(stdout)
        }
        print("\u{1B}[2K\r", terminator: "")

        let after = try db.scalarInt("SELECT COUNT(*) FROM raw_event") ?? 0

        Fmt.heading("INGEST")
        print("  sources parsed        \(Fmt.int(result.sourcesScanned))")
        print("  sources unchanged     \(Fmt.int(result.sourcesSkipped))   (watermark already current)")
        print("  events written        \(Fmt.int(result.eventsWritten))")
        print("  events in store       \(Fmt.int(after))   (+\(Fmt.int(after - before)))")
        print("  repos resolved        \(Fmt.int(result.reposResolved))")
        print("  elapsed               \(String(format: "%.2f", result.elapsed))s")

        if !result.diagnostics.isEmpty {
            Fmt.heading("DIAGNOSTICS")
            for (code, n) in result.diagnostics.sorted(by: { $0.value > $1.value }) {
                print("  \(Fmt.pad(code, 26))\(Fmt.int(n))")
            }
        }

        try Derive.run(db: db, verbose: true)
    }
}

/// Turns the durable event store into the derived session index.
///
/// Split out from ingest because it is a pure function of `state.sqlite` plus `Tuning`:
/// it can be re-run at any time, and it MUST be re-run whenever a constant changes, or a
/// user keeps a contribution graph bucketed under thresholds that no longer exist.
enum Derive {

    @discardableResult
    static func run(db: SQLiteDB, verbose: Bool = false) throws -> [DetectedSession] {
        let (cache, didRebuild) = try SchemaManager.openCache(tuningVersion: Tuning.version)
        if verbose && didRebuild {
            Fmt.heading("DERIVE")
            print("  cache rebuilt (tuning \(Tuning.version))")
        } else if verbose {
            Fmt.heading("DERIVE")
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

        try writeSessions(sessions, events: events, repoNames: repoNames, to: cache)

        if verbose {
            let counted = sessions.filter(\.counted)
            let notable = sessions.filter(\.notable)
            let unattended = sessions.filter(\.unattended)
            let active = sessions.reduce(0.0) { $0 + $1.activeSeconds }
            print("  sessions              \(Fmt.int(sessions.count))")
            print("  counted               \(Fmt.int(counted.count))   (toward hours, graph, streaks)")
            print("  notable               \(Fmt.int(notable.count))   (card, record, notification)")
            print("  unattended            \(Fmt.int(unattended.count))   (no typed prompt; hours only)")
            print("  active time           \(Fmt.duration(active))")
        }
        return sessions
    }

    private static func writeSessions(
        _ sessions: [DetectedSession],
        events: [NormalizedEvent],
        repoNames: [Int: String],
        to cache: SQLiteDB
    ) throws {
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
              is_background, unattended, time_quality, merge_group_id, visibility
            ) VALUES (?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?,?,
                      ?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?,?, ?,?,?,?,?)
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
                let day = String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)

                let repoID = evs.compactMap { $0.extra?["repo_id"] }.first.flatMap(Int.init)
                let attrib = TokenAccountant.attribution(
                    agentAdded: lines.added, gitInsertions: 0, gitCommits: 0,
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
                    .text(TimelineFidelity.full.rawValue), .text("final"),
                    .bool(s.counted), .bool(s.notable),
                    .int(s.promptCount), .text("typed_promptsource"),
                    .int(count(.toolUse)), .int(tool("Read")), .int(tool("Edit")),
                    .int(tool("Write")), .int(tool("Bash")),
                    .int(Set(evs.compactMap(\.targetPath)).count), .int(0),
                    .int(Set(evs.compactMap(\.agentID)).count), .int(count(.compaction)),
                    .int(count(.humanEdit)),
                    .int(lines.added), .int(lines.removed),
                    .int(0), .int(0), .int(0),
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
