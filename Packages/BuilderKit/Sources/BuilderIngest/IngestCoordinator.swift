import BuilderGit
import BuilderModel
import BuilderParse
import BuilderSchema
import BuilderSQLite
import Foundation

/// Runs the pipeline: discover → watermark → parse → live-path → write → resolve repos.
///
/// Serial, on purpose. The measured baseline for a naive full parse of the entire 1.2 GB
/// corpus is under three seconds, which leaves roughly 20x headroom against the 60-second
/// backfill budget. Parallel ingest buys nothing here and costs the ability to reason
/// about a single write connection, so it is deferred until a measurement demands it.
public final class IngestCoordinator {

    public struct Progress: Sendable {
        public var sourcesTotal: Int
        public var sourcesDone: Int
        public var bytesTotal: Int
        public var bytesDone: Int
        public var eventsWritten: Int
        public var currentPath: String?

        /// Byte-weighted, never source-weighted: transcripts differ in size by four orders
        /// of magnitude (a few KB to 78 MB), so counting files makes the bar leap and then
        /// freeze. Byte-weighted progress moves at a believable rate.
        public var fraction: Double {
            bytesTotal > 0 ? Double(bytesDone) / Double(bytesTotal) : 0
        }
    }

    public struct Result: Sendable {
        public var sourcesScanned = 0
        public var sourcesSkipped = 0
        public var eventsWritten = 0
        public var reposResolved = 0
        public var diagnostics: [String: Int] = [:]
        public var elapsed: Double = 0
    }

    private let db: SQLiteDB
    private let writer: StoreWriter
    private let parsers: [any HarnessParser]
    private let resolver: RepoResolverCache

    public init(
        db: SQLiteDB,
        parsers: [any HarnessParser] = [ClaudeCodeParser(), CursorIDEParser(), CodexParser()],
        resolver: RepoResolverCache = RepoResolverCache()
    ) {
        self.db = db
        self.writer = StoreWriter(db: db)
        self.parsers = parsers
        self.resolver = resolver
    }

    /// One full pass over every source.
    ///
    /// Sources already at their watermark are skipped without opening the file, which is
    /// what makes the steady-state tick cheap enough to run every fifteen seconds.
    @discardableResult
    public func run(onProgress: ((Progress) -> Void)? = nil) throws -> Result {
        let started = Date()
        var result = Result()

        var work: [(parser: any HarnessParser, source: SourceRef, size: Int)] = []
        for parser in parsers {
            guard parser.harness.isImplemented else { continue }
            for src in (try? parser.discover()) ?? [] {
                let size = (try? FileManager.default.attributesOfItem(atPath: src.path))
                    .flatMap { ($0[.size] as? NSNumber)?.intValue } ?? 0
                work.append((parser, src, size))
            }
        }

        // Newest first. The first thing a user sees on a cold start should be this week,
        // not January — a progress bar that fills with stale data reads as a stalled app.
        work.sort { a, b in
            let am = (try? FileManager.default.attributesOfItem(atPath: a.source.path))
                .flatMap { ($0[.modificationDate] as? Date)?.timeIntervalSince1970 } ?? 0
            let bm = (try? FileManager.default.attributesOfItem(atPath: b.source.path))
                .flatMap { ($0[.modificationDate] as? Date)?.timeIntervalSince1970 } ?? 0
            return am > bm
        }

        let bytesTotal = work.reduce(0) { $0 + $1.size }
        var bytesDone = 0
        var cwdsSeen = Set<String>()

        for (i, item) in work.enumerated() {
            onProgress?(
                Progress(
                    sourcesTotal: work.count, sourcesDone: i,
                    bytesTotal: bytesTotal, bytesDone: bytesDone,
                    eventsWritten: result.eventsWritten, currentPath: item.source.path))

            let stored =
                (try? writer.watermark(for: item.source.sourceID))
                ?? Watermark(sourceID: item.source.sourceID, parserVersion: item.parser.parserVersion)

            let parsed: ParseResult
            do {
                parsed = try item.parser.parse(source: item.source, from: stored)
            } catch {
                try? writer.record(
                    diagnostic: "parse_failed", detail: "\(error)",
                    harness: item.parser.harness, sourceID: item.source.sourceID)
                result.diagnostics["parse_failed", default: 0] += 1
                bytesDone += item.size
                continue
            }

            if parsed.events.isEmpty && parsed.watermark.byteOffset == stored.byteOffset {
                result.sourcesSkipped += 1
                bytesDone += item.size
                continue
            }

            // The DAG never spans files, so live-path resolution is per source and can run
            // before the write — which means `on_live_path` is correct on first insert
            // rather than patched afterwards.
            var events = parsed.events
            let restart = parsed.diagnostics.contains { $0.code == "source_restart" }
            if restart || stored.byteOffset == 0 {
                let live = LivePathResolver.liveEventIDs(in: events)
                for j in events.indices {
                    if let id = events[j].nativeEventID { events[j].onLivePath = live.contains(id) }
                }
            }

            result.eventsWritten += try writer.write(
                events: events, watermark: parsed.watermark, source: item.source, restart: restart)
            result.sourcesScanned += 1
            bytesDone += item.size

            for d in parsed.diagnostics {
                result.diagnostics[d.code, default: 0] += 1
                try? writer.record(
                    diagnostic: d.code, detail: d.detail,
                    harness: item.parser.harness, sourceID: item.source.sourceID)
            }

            for e in events { if let c = e.cwd { cwdsSeen.insert(c) } }
        }

        result.reposResolved = try resolveRepos(for: cwdsSeen)

        onProgress?(
            Progress(
                sourcesTotal: work.count, sourcesDone: work.count,
                bytesTotal: bytesTotal, bytesDone: bytesTotal,
                eventsWritten: result.eventsWritten, currentPath: nil))

        result.elapsed = Date().timeIntervalSince(started)
        return result
    }

    /// Map every working directory seen to a repository, and write the mapping through.
    private func resolveRepos(for cwds: Set<String>) throws -> Int {
        var repoIDByIdentity: [String: Int] = [:]
        let now = Date().timeIntervalSince1970

        for cwd in cwds.sorted() {
            guard let ident = resolver.identity(for: cwd) else {
                try? writer.cachePathRepo(path: cwd, repoID: nil, identity: RepoPathInfo())
                continue
            }

            let repoID: Int
            if let existing = repoIDByIdentity[ident.identity] {
                repoID = existing
            } else {
                repoID = try writer.upsertRepo(
                    identity: ident.identity,
                    basis: ident.basis.rawValue,
                    displayName: ident.displayName,
                    commonRoot: ident.commonRoot,
                    repoHash: RepoHasher.hash(identity: ident.identity),
                    seenAt: now
                )
                repoIDByIdentity[ident.identity] = repoID
            }

            try writer.cachePathRepo(
                path: cwd, repoID: repoID,
                identity: RepoPathInfo(
                    origin: ident.basis == .origin ? ident.identity : nil,
                    rootCommit: ident.basis == .rootCommit ? ident.identity : nil,
                    commonRoot: ident.commonRoot))
            try writer.assignRepo(repoID: repoID, toEventsWithCwd: cwd)
        }
        return repoIDByIdentity.count
    }

    // MARK: - Reading back

    /// Load every stored event, in time order, for derivation.
    public static func loadEvents(db: SQLiteDB, since: Double? = nil) throws -> [NormalizedEvent] {
        var out: [NormalizedEvent] = []
        let sql = """
            SELECT event_uid, harness, source_id, ordinal, native_session_id, native_event_id,
                   native_parent_id, agent_id, is_sidechain, on_live_path, ts, cwd,
                   harness_version, model, effort, dedupe_key, usage_authoritative,
                   tok_in, tok_out, tok_cache_read, tok_cache_w5m, tok_cache_w1h,
                   kind, role, tool_name, tool_id, target_path, lines_added, lines_removed,
                   duration_ms, title, leaf_uuid, repo_id
            FROM raw_event
            \(since != nil ? "WHERE ts >= ?" : "")
            ORDER BY ts
            """
        try db.query(sql, since.map { [.double($0)] } ?? []) { s in
            guard let harness = Harness(rawValue: s.text(1) ?? "") else { return }
            var e = NormalizedEvent(
                eventUID: s.text(0) ?? "",
                harness: harness,
                sourceID: s.text(2) ?? "",
                ordinal: s.int(3) ?? 0,
                nativeSessionID: s.text(4),
                nativeEventID: s.text(5),
                nativeParentID: s.text(6),
                agentID: s.text(7),
                isSidechain: s.bool(8),
                onLivePath: s.isNull(9) ? nil : s.bool(9),
                ts: s.double(10),
                cwd: s.text(11),
                harnessVersion: s.text(12),
                model: s.text(13),
                effort: s.text(14),
                dedupeKey: s.text(15),
                usageAuthoritative: s.bool(16),
                tokIn: s.int(17), tokOut: s.int(18), tokCacheRead: s.int(19),
                tokCacheW5m: s.int(20), tokCacheW1h: s.int(21),
                kind: EventKind(rawValue: s.text(22) ?? "") ?? .unknown,
                role: s.text(23),
                toolName: s.text(24), toolID: s.text(25), targetPath: s.text(26),
                linesAdded: s.int(27), linesRemoved: s.int(28),
                durationMs: s.int(29), title: s.text(30), leafUUID: s.text(31)
            )
            if let repoID = s.int(32) { e.extra = ["repo_id": String(repoID)] }
            out.append(e)
        }
        return out
    }

    /// `repo_id -> display name`, for labelling sessions without a second git call.
    public static func repoNames(db: SQLiteDB) throws -> [Int: String] {
        var out: [Int: String] = [:]
        try db.query("SELECT repo_id, COALESCE(display_name, origin_url_norm) FROM repo") { s in
            if let id = s.int(0), let name = s.text(1) { out[id] = name }
        }
        return out
    }
}
