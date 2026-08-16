import BuilderModel
import BuilderParse
import BuilderSQLite
import Foundation

/// The only thing that writes to `state.sqlite`.
///
/// One connection, one writer. Everything else in the system reads.
public final class StoreWriter {

    private let db: SQLiteDB

    public init(db: SQLiteDB) {
        self.db = db
    }

    // MARK: - Events

    /// Write one source's parsed events and its watermark.
    ///
    /// **The batch and the watermark commit in the SAME transaction.** If they could
    /// diverge, a crash between them leaves the store in one of two states, both bad:
    /// the watermark advanced past events that were never written, so those events are
    /// lost forever; or events written without the watermark advancing, so the next run
    /// re-inserts them — which is survivable for `raw_event` thanks to `INSERT OR IGNORE`,
    /// but not for the partial unique index on token usage, which would abort the
    /// transaction and wedge that source permanently.
    @discardableResult
    public func write(
        events: [NormalizedEvent],
        watermark: Watermark,
        source: SourceRef,
        restart: Bool = false
    ) throws -> Int {
        try db.transaction {
            // A parser version bump means our reading of the same bytes changed, so the
            // stored rows are stale even though the file is untouched. Delete and replay.
            if restart {
                try db.run("DELETE FROM raw_event WHERE source_id = ?", [.text(source.sourceID)])
            }

            let insert = try db.prepare(
                """
                INSERT OR IGNORE INTO raw_event (
                  event_uid, harness, source_id, ordinal,
                  native_session_id, native_event_id, native_parent_id, agent_id,
                  is_sidechain, on_live_path,
                  ts, day, hour, dow, tz_offset_min,
                  kind, role, cwd, repo_id, harness_version, model, effort, service_tier,
                  dedupe_key, usage_authoritative,
                  tok_in, tok_out, tok_cache_read, tok_cache_w5m, tok_cache_w1h,
                  tool_name, tool_id, target_path, lines_added, lines_removed,
                  duration_ms, segment_source, title, leaf_uuid, extra, ingested_at
                ) VALUES (?,?,?,?, ?,?,?,?, ?,?, ?,?,?,?,?, ?,?,?,?,?,?,?,?, ?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?)
                """
            )
            defer { insert.finalize() }

            let now = Date().timeIntervalSince1970
            var written = 0
            let cal = Calendar(identifier: .gregorian)

            for e in events {
                // Denormalized local-time columns, computed once at write time so the
                // contribution graph is a plain GROUP BY rather than a per-row conversion.
                var day: String?
                var hour: Int?
                var dow: Int?
                var tzOffset: Int?
                if let ts = e.ts {
                    let date = Date(timeIntervalSince1970: ts)
                    let c = cal.dateComponents([.year, .month, .day, .hour, .weekday], from: date)
                    // Honours Tuning.dayBoundaryHour: work at 00:20 belongs to the night
                    // that started it, not to a fresh calendar date.
                    day = Tuning.localDay(for: date, calendar: cal)
                    hour = c.hour
                    dow = (c.weekday ?? 1) - 1
                    tzOffset = TimeZone.current.secondsFromGMT(for: date) / 60
                }

                do {
                    try insert.execute([
                        .text(e.eventUID), .text(e.harness.rawValue), .text(e.sourceID), .int(e.ordinal),
                        .optionalText(e.nativeSessionID), .optionalText(e.nativeEventID),
                        .optionalText(e.nativeParentID), .optionalText(e.agentID),
                        .bool(e.isSidechain), e.onLivePath.map { SQLValue.bool($0) } ?? .null,
                        .optionalDouble(e.ts), .optionalText(day), .optionalInt(hour),
                        .optionalInt(dow), .optionalInt(tzOffset),
                        .text(e.kind.rawValue), .optionalText(e.role), .optionalText(e.cwd),
                        .null, .optionalText(e.harnessVersion), .optionalText(e.model),
                        .optionalText(e.effort), .null,
                        .optionalText(e.dedupeKey), .bool(e.usageAuthoritative),
                        .optionalInt(e.tokIn), .optionalInt(e.tokOut), .optionalInt(e.tokCacheRead),
                        .optionalInt(e.tokCacheW5m), .optionalInt(e.tokCacheW1h),
                        .optionalText(e.toolName), .optionalText(e.toolID), .optionalText(e.targetPath),
                        .optionalInt(e.linesAdded), .optionalInt(e.linesRemoved),
                        .optionalInt(e.durationMs), .null,
                        .optionalText(e.title), .optionalText(e.leafUUID), .null, .double(now),
                    ])
                    written += 1
                } catch let err as SQLiteError where err.isConstraintViolation {
                    // The partial unique index on (dedupe_key WHERE usage_authoritative=1)
                    // fired: this message's usage is already recorded. That is the index
                    // doing exactly its job — a resumed read cannot see keys claimed before
                    // its watermark — so re-insert the row without the usage claim rather
                    // than aborting the source's whole transaction.
                    var demoted = e
                    demoted.usageAuthoritative = false
                    try insertDemoted(demoted, day: day, hour: hour, dow: dow, tz: tzOffset, now: now)
                    written += 1
                }
            }

            try upsert(watermark: watermark, source: source)
            return written
        }
    }

    private func insertDemoted(
        _ e: NormalizedEvent, day: String?, hour: Int?, dow: Int?, tz: Int?, now: Double
    ) throws {
        try db.run(
            """
            INSERT OR IGNORE INTO raw_event (
              event_uid, harness, source_id, ordinal,
              native_session_id, native_event_id, native_parent_id, agent_id,
              is_sidechain, on_live_path, ts, day, hour, dow, tz_offset_min,
              kind, role, cwd, harness_version, model, effort,
              dedupe_key, usage_authoritative,
              tok_in, tok_out, tok_cache_read, tok_cache_w5m, tok_cache_w1h,
              tool_name, tool_id, target_path, lines_added, lines_removed,
              duration_ms, title, leaf_uuid, ingested_at
            ) VALUES (?,?,?,?, ?,?,?,?, ?,?,?,?,?,?,?, ?,?,?,?,?,?, ?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?,?)
            """,
            [
                .text(e.eventUID), .text(e.harness.rawValue), .text(e.sourceID), .int(e.ordinal),
                .optionalText(e.nativeSessionID), .optionalText(e.nativeEventID),
                .optionalText(e.nativeParentID), .optionalText(e.agentID),
                .bool(e.isSidechain), e.onLivePath.map { SQLValue.bool($0) } ?? .null,
                .optionalDouble(e.ts), .optionalText(day), .optionalInt(hour),
                .optionalInt(dow), .optionalInt(tz),
                .text(e.kind.rawValue), .optionalText(e.role), .optionalText(e.cwd),
                .optionalText(e.harnessVersion), .optionalText(e.model), .optionalText(e.effort),
                .optionalText(e.dedupeKey), .int(0),
                .optionalInt(e.tokIn), .optionalInt(e.tokOut), .optionalInt(e.tokCacheRead),
                .optionalInt(e.tokCacheW5m), .optionalInt(e.tokCacheW1h),
                .optionalText(e.toolName), .optionalText(e.toolID), .optionalText(e.targetPath),
                .optionalInt(e.linesAdded), .optionalInt(e.linesRemoved),
                .optionalInt(e.durationMs), .optionalText(e.title), .optionalText(e.leafUUID),
                .double(now),
            ]
        )
    }

    // MARK: - Watermarks

    public func upsert(watermark w: Watermark, source: SourceRef) throws {
        try db.run(
            """
            INSERT INTO ingest_watermark (
              source_id, harness, path, kind, byte_offset, line_count,
              st_dev, st_ino, size_bytes, mtime, head_sha256, last_row_key,
              parser_version, bodies_missing_first_seen_at, completed_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_id) DO UPDATE SET
              byte_offset = excluded.byte_offset,
              line_count  = excluded.line_count,
              st_dev      = excluded.st_dev,
              st_ino      = excluded.st_ino,
              size_bytes  = excluded.size_bytes,
              mtime       = excluded.mtime,
              head_sha256 = excluded.head_sha256,
              last_row_key = excluded.last_row_key,
              parser_version = excluded.parser_version,
              bodies_missing_first_seen_at =
                COALESCE(ingest_watermark.bodies_missing_first_seen_at,
                         excluded.bodies_missing_first_seen_at),
              completed_at = excluded.completed_at
            """,
            [
                .text(w.sourceID), .text(source.harness.rawValue), .text(source.path),
                .text(source.kind.rawValue), .int(w.byteOffset), .int(w.lineCount),
                .optionalInt(w.stDev), .optionalInt(w.stIno), .optionalInt(w.sizeBytes),
                .optionalDouble(w.mtime), .optionalText(w.headSHA256), .optionalText(w.lastRowKey),
                .int(w.parserVersion), .optionalDouble(w.bodiesMissingFirstSeenAt),
                .double(Date().timeIntervalSince1970),
            ]
        )
    }

    public func watermark(for sourceID: String) throws -> Watermark? {
        var found: Watermark?
        try db.query(
            """
            SELECT byte_offset, line_count, st_dev, st_ino, size_bytes, mtime,
                   head_sha256, last_row_key, parser_version, bodies_missing_first_seen_at
            FROM ingest_watermark WHERE source_id = ?
            """,
            [.text(sourceID)]
        ) { s in
            found = Watermark(
                sourceID: sourceID,
                byteOffset: s.int(0) ?? 0,
                lineCount: s.int(1) ?? 0,
                stDev: s.int(2),
                stIno: s.int(3),
                sizeBytes: s.int(4),
                mtime: s.double(5),
                headSHA256: s.text(6),
                lastRowKey: s.text(7),
                parserVersion: s.int(8) ?? 1,
                bodiesMissingFirstSeenAt: s.double(9)
            )
        }
        return found
    }

    // MARK: - Repos

    /// Insert or update a repository, returning its local id.
    ///
    /// Visibility is preserved on conflict: re-resolving a repo must never silently reset
    /// a user's choice to exclude it back to the default.
    @discardableResult
    public func upsertRepo(
        identity: String,
        basis: String,
        displayName: String?,
        commonRoot: String?,
        repoHash: String?,
        seenAt: Double
    ) throws -> Int {
        try db.run(
            """
            INSERT INTO repo (origin_url_norm, common_root, display_name, repo_hash,
                              repo_id_basis, pepper_version, visibility,
                              first_seen_ts, last_seen_ts)
            VALUES (?,?,?,?,?,?,'anonymous',?,?)
            ON CONFLICT(origin_url_norm) DO UPDATE SET
              common_root  = COALESCE(excluded.common_root, repo.common_root),
              display_name = COALESCE(excluded.display_name, repo.display_name),
              repo_hash    = COALESCE(excluded.repo_hash, repo.repo_hash),
              last_seen_ts = MAX(COALESCE(repo.last_seen_ts, 0), excluded.last_seen_ts)
            """,
            [
                .text(identity), .optionalText(commonRoot), .optionalText(displayName),
                .optionalText(repoHash), .text(basis), .int(Tuning.repoPepperVersion),
                .double(seenAt), .double(seenAt),
            ]
        )
        return try db.scalarInt(
            "SELECT repo_id FROM repo WHERE origin_url_norm = ?", [.text(identity)]) ?? 0
    }

    /// Cache a working directory's resolved repository, INCLUDING misses.
    ///
    /// Resolution happens at ingest rather than at derive time because worktrees get
    /// deleted: once the directory is gone, `git -C <cwd> rev-parse` fails and the session
    /// would lose its project forever.
    public func cachePathRepo(path: String, repoID: Int?, identity: RepoPathInfo) throws {
        try db.run(
            """
            INSERT INTO path_repo (path, common_root, origin_url_norm, root_commit, repo_id, resolved_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET
              common_root = excluded.common_root,
              origin_url_norm = excluded.origin_url_norm,
              repo_id = excluded.repo_id,
              resolved_at = excluded.resolved_at
            """,
            [
                .text(path), .optionalText(identity.commonRoot), .optionalText(identity.origin),
                .optionalText(identity.rootCommit), .optionalInt(repoID),
                .double(Date().timeIntervalSince1970),
            ]
        )
    }

    /// Attach resolved repositories to already-written events.
    public func assignRepo(repoID: Int, toEventsWithCwd cwd: String) throws {
        try db.run("UPDATE raw_event SET repo_id = ? WHERE cwd = ?", [.int(repoID), .text(cwd)])
    }

    // MARK: - Diagnostics

    /// Diagnostics are rows, not log lines. The entire correctness story of this product
    /// is "the numbers are right", and a parser that degrades quietly is indistinguishable
    /// from one that works.
    public func record(diagnostic code: String, detail: String?, harness: Harness?, sourceID: String?) throws {
        try db.run(
            "INSERT INTO diagnostics (ts, harness, source_id, code, detail, count) VALUES (?,?,?,?,?,1)",
            [
                .double(Date().timeIntervalSince1970), .optionalText(harness?.rawValue),
                .optionalText(sourceID), .text(code), .optionalText(detail),
            ]
        )
    }

    public func diagnosticCounts() throws -> [(code: String, count: Int)] {
        var out: [(String, Int)] = []
        try db.query("SELECT code, COUNT(*) FROM diagnostics GROUP BY code ORDER BY 2 DESC") { s in
            out.append((s.text(0) ?? "?", s.int(1) ?? 0))
        }
        return out
    }
}

public struct RepoPathInfo: Sendable {
    public var origin: String?
    public var rootCommit: String?
    public var commonRoot: String?
    public init(origin: String? = nil, rootCommit: String? = nil, commonRoot: String? = nil) {
        self.origin = origin
        self.rootCommit = rootCommit
        self.commonRoot = commonRoot
    }
}
