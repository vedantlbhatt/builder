import BuilderModel
import BuilderSQLite
import Foundation

/// Reads Cursor's conversation history out of its own SQLite store.
///
/// A completely different shape of problem from Claude Code's append-only JSONL, and the
/// constraints are unusually sharp:
///
/// - The database is **1.21 GB with a live 5.1 MB write-ahead log**, being written by an
///   app that is probably running right now.
/// - **Tokens do not exist.** Across all 14,565 message rows, `tokenCount.inputTokens > 0`
///   matches zero rows and `outputTokens > 0` matches zero rows. Usage is accounted
///   server-side and never written locally. This is not missing data to backfill later; it
///   is structurally absent, and the UI has to say so rather than render a zero.
/// - **The model is usually unknown**: `modelName` is the literal string `"default"` on
///   115 of 137 composers, and message rows carry no model at all.
/// - **Cursor garbage-collects aggressively.** 482 conversation headers survive but
///   message bodies for only 49 composers — 433 conversations are header-only, and the
///   cliff falls at roughly two months.
///
/// So the strategy is: read the cheap relational header table for everything, and only
/// touch the enormous blob tables for the handful of conversations that still have bodies.
public struct CursorIDEParser: HarnessParser {

    public let harness: Harness = .cursorIDE
    public let parserVersion = 1

    private let globalStoragePath: String
    private let workspaceStoragePath: String

    public init(cursorSupportRoot: String? = nil) {
        let root =
            cursorSupportRoot
            ?? (NSHomeDirectory() as NSString)
                .appendingPathComponent("Library/Application Support/Cursor")
        globalStoragePath = (root as NSString).appendingPathComponent("User/globalStorage/state.vscdb")
        workspaceStoragePath = (root as NSString).appendingPathComponent("User/workspaceStorage")
    }

    // MARK: - Discovery

    public func discover() throws -> [SourceRef] {
        guard FileManager.default.fileExists(atPath: globalStoragePath) else { return [] }
        return [
            SourceRef(
                sourceID: Hashing.sourceID(harness: .cursorIDE, descriptor: "globalStorage"),
                harness: .cursorIDE,
                kind: .sqlite,
                path: globalStoragePath)
        ]
    }

    // MARK: - Parsing

    public func parse(source: SourceRef, from watermark: Watermark) throws -> ParseResult {
        var diagnostics: [ParseDiagnostic] = []

        // `mode=ro` and deliberately NOT `immutable=1`.
        //
        // `immutable=1` tells SQLite the file cannot change, so it skips the write-ahead
        // log entirely and returns whatever the main file held at the last checkpoint —
        // silently, with no error. Against a live 5.1 MB WAL that is stale data wearing a
        // successful return code, which is the worst possible failure for this product.
        let db: SQLiteDB
        do {
            db = try SQLiteDB.openForeignReadOnly(path: globalStoragePath)
        } catch {
            // Roughly 1% of Cursor databases fail to open at all. Skip the source, record
            // it, and never let it abort the whole scan.
            return ParseResult(
                events: [], watermark: watermark,
                diagnostics: [ParseDiagnostic(code: "sqlite_open_failed", detail: "\(error)")],
                fidelity: .headerOnly)
        }

        // Feature-detect rather than assume. `composerHeaders` is a first-class relational
        // table in current Cursor but did not always exist; older installs keep the same
        // data as a blob in `ItemTable`. Schema shape is never assumed anywhere in this
        // codebase — Codex version-stamps its filenames for the same reason.
        let hasHeaders = try db.tableExists("composerHeaders")
        guard hasHeaders else {
            diagnostics.append(
                ParseDiagnostic(
                    code: "cursor_schema_unknown",
                    detail: "no composerHeaders table; legacy ItemTable layout not yet supported"))
            return ParseResult(
                events: [], watermark: watermark, diagnostics: diagnostics, fidelity: .headerOnly)
        }

        let workspaceFolders = loadWorkspaceFolders()

        // Only conversations touched since the last run. `lastUpdatedAt` is epoch ms.
        let since = Double(watermark.lastRowKey ?? "0") ?? 0
        var events: [NormalizedEvent] = []
        var maxUpdated = since
        var headerOnlyCount = 0
        var fullCount = 0

        struct Composer {
            var id: String
            var workspaceID: String?
            var createdAt: Double
            var updatedAt: Double
            var name: String?
            var linesAdded: Int
            var linesRemoved: Int
            var filesChanged: Int
            var repoPath: String?
            var workspacePath: String?
            var isSubagent: Bool
        }

        var composers: [Composer] = []

        // Everything the header table can give, without touching a blob table. Measured
        // at ~30 ms for all 482 conversations.
        try db.query(
            """
            SELECT composerId, workspaceId, createdAt, lastUpdatedAt, isSubagent,
                   json_extract(value, '$.name'),
                   json_extract(value, '$.totalLinesAdded'),
                   json_extract(value, '$.totalLinesRemoved'),
                   json_extract(value, '$.filesChangedCount'),
                   json_extract(value, '$.trackedGitRepos[0].repoPath'),
                   json_extract(value, '$.workspaceIdentifier.uri.fsPath')
            FROM composerHeaders
            WHERE lastUpdatedAt > ?
            ORDER BY lastUpdatedAt
            """,
            [.double(since)]
        ) { s in
            guard let id = s.text(0) else { return }
            composers.append(
                Composer(
                    id: id,
                    workspaceID: s.text(1),
                    createdAt: (s.double(2) ?? 0) / 1000,
                    updatedAt: (s.double(3) ?? 0) / 1000,
                    name: s.text(5),
                    linesAdded: s.int(6) ?? 0,
                    linesRemoved: s.int(7) ?? 0,
                    filesChanged: s.int(8) ?? 0,
                    repoPath: s.text(9),
                    workspacePath: s.text(10),
                    isSubagent: s.bool(4)))
            maxUpdated = max(maxUpdated, s.double(3) ?? 0)
        }

        // Which conversations still have message bodies. One cheap grouped query rather
        // than a per-composer probe.
        var bubbleCounts: [String: Int] = [:]
        try db.query(
            """
            SELECT substr(key, 10, 36) AS composerId, COUNT(*)
            FROM cursorDiskKV WHERE key LIKE 'bubbleId:%' GROUP BY composerId
            """
        ) { s in
            if let id = s.text(0) { bubbleCounts[id] = s.int(1) ?? 0 }
        }

        for c in composers {
            let cwd = c.repoPath ?? c.workspacePath ?? c.workspaceID.flatMap { workspaceFolders[$0] }
            let hasBodies = (bubbleCounts[c.id] ?? 0) > 0

            func base(_ kind: EventKind, id: String, ts: Double) -> NormalizedEvent {
                NormalizedEvent(
                    eventUID: Hashing.eventUID(
                        harness: .cursorIDE, sourceID: source.sourceID, nativeEventID: id),
                    harness: .cursorIDE,
                    sourceID: source.sourceID,
                    ordinal: 0,
                    nativeSessionID: c.id,
                    nativeEventID: id,
                    isSidechain: c.isSubagent,
                    ts: ts,
                    cwd: cwd,
                    kind: kind)
            }

            if hasBodies {
                fullCount += 1
                // Project with json_extract IN SQL. Never `SELECT value` across the 14,565
                // bubble rows: one of them is 2.8 MB, several exceed a megabyte, and
                // pulling them into memory to read two fields would cost hundreds of MB.
                // `agentKv:blob:` is skipped entirely — 28,039 rows and 562 MB with no
                // session key in them at all, so avoiding it is half the database.
                try? db.query(
                    """
                    SELECT substr(key, 47) AS bubbleId,
                           json_extract(value, '$.createdAt'),
                           json_extract(value, '$.type'),
                           json_extract(value, '$.toolFormerData.name')
                    FROM cursorDiskKV
                    WHERE key GLOB ?
                    """,
                    [.text("bubbleId:\(c.id):*")]
                ) { s in
                    guard let bubbleID = s.text(0), let ts = ISO8601.seconds(s.text(1)) else { return }
                    let role = s.int(2)  // 1 = user, 2 = assistant
                    let tool = s.text(3)

                    var e: NormalizedEvent
                    if let tool, !tool.isEmpty {
                        e = base(.toolUse, id: "\(c.id)#\(bubbleID)", ts: ts)
                        // Key off the tool NAME, never the untyped integer beside it —
                        // those enum values drift between releases.
                        e.toolName = tool
                    } else if role == 1 {
                        // Cursor has no `promptSource` equivalent, so every user bubble
                        // counts. That is a LOOSER rule than Claude Code's typed-prompt
                        // filter, which is exactly why `prompt_count_basis` is carried on
                        // every session: the two must never be summed as if they meant
                        // the same thing.
                        e = base(.prompt, id: "\(c.id)#\(bubbleID)", ts: ts)
                    } else {
                        e = base(.assistantMessage, id: "\(c.id)#\(bubbleID)", ts: ts)
                    }
                    e.role = role == 1 ? "user" : "assistant"
                    events.append(e)
                }
            } else {
                headerOnlyCount += 1
                // Bodies are gone. Two synthetic events preserve the session's span so its
                // hours still count, and `timeline_fidelity` marks it header-only so it is
                // never rendered as a card — a card with an empty strip reads as a broken
                // app rather than as integrity.
                //
                // Deliberately NOT reconstructed from `aiService.generations`: that table
                // is keyed by WORKSPACE, not by composer, so its timestamps cannot be
                // attributed to a specific conversation. Painting them would fabricate
                // per-session events on the product's own brand asset.
                if c.createdAt > 0 {
                    events.append(base(.noise, id: "\(c.id)#start", ts: c.createdAt))
                }
                if c.updatedAt > c.createdAt {
                    events.append(base(.noise, id: "\(c.id)#end", ts: c.updatedAt))
                }
            }

            // Line counts come free from the header and survive the body GC.
            if c.linesAdded > 0 || c.linesRemoved > 0 {
                var e = base(.toolUse, id: "\(c.id)#lines", ts: c.updatedAt)
                e.toolName = "edit_file_v2"
                e.linesAdded = c.linesAdded
                e.linesRemoved = c.linesRemoved
                events.append(e)
            }

            if let name = c.name, !name.isEmpty {
                var e = base(.title, id: "\(c.id)#title", ts: c.updatedAt)
                e.title = name
                events.append(e)
            }
        }

        if headerOnlyCount > 0 {
            diagnostics.append(
                ParseDiagnostic(
                    code: "cursor_bodies_gc",
                    detail: "\(headerOnlyCount) conversations header-only, \(fullCount) with bodies"))
        }

        var wm = watermark
        wm.lastRowKey = String(format: "%.0f", maxUpdated)
        wm.parserVersion = parserVersion
        wm.mtime = (try? FileManager.default.attributesOfItem(atPath: globalStoragePath))
            .flatMap { ($0[.modificationDate] as? Date)?.timeIntervalSince1970 }

        // Fidelity is MONOTONIC UPWARD ONLY. Cursor vacuums while running, so a read that
        // races the GC can see bodies missing for a conversation that had them moments
        // ago; letting fidelity fall would permanently downgrade a good session because of
        // a transient lock.
        let observed: TimelineFidelity = fullCount > 0 ? .full : .headerOnly
        return ParseResult(
            events: events, watermark: wm, diagnostics: diagnostics, fidelity: observed)
    }

    /// `workspaceId -> folder path`, from each workspace's `workspace.json`.
    ///
    /// The 32-hex directory name IS `composerHeaders.workspaceId`, so this is a direct
    /// join and needs no guessing.
    private func loadWorkspaceFolders() -> [String: String] {
        var out: [String: String] = [:]
        guard let dirs = try? FileManager.default.contentsOfDirectory(atPath: workspaceStoragePath)
        else { return out }

        for dir in dirs {
            let jsonPath = (workspaceStoragePath as NSString)
                .appendingPathComponent("\(dir)/workspace.json")
            guard let data = FileManager.default.contents(atPath: jsonPath),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let folder = obj["folder"] as? String
            else { continue }
            // `file:///Users/...` -> `/Users/...`
            out[dir] = URL(string: folder)?.path ?? folder
        }
        return out
    }
}
