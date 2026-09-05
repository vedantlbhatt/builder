import BuilderAnalysis
import BuilderIngest
import BuilderModel
import BuilderSchema
import BuilderSQLite
import BuilderSync
import Foundation

/// `builder sync` and `builder pair`.
///
/// `--dry-run --print-payload` is the command the privacy page tells people to run. It
/// prints every byte that would be sent and sends nothing, so the claim is checkable
/// rather than merely stated.
///
/// Contract v2: a session the lifecycle still holds `open`/`idle` goes up as a
/// `state: "live"` snapshot with `end_reason: "still_running"`, at most once per
/// `Tuning.liveUploadMinIntervalSec`, and is replaced in place by the final upload under
/// the same `client_session_id`. The stored analysis rides along under `analysis` when
/// analysis upload is on (UserDefaults `BuilderAnalysisUpload`, default true;
/// `BUILDER_ANALYSIS_UPLOAD=0` overrides) and is omitted from the wire otherwise.
enum SyncCommand {

    static func baseURL() -> URL {
        let raw =
            ProcessInfo.processInfo.environment["BUILDER_API_URL"]
            ?? CLIArgs.value("api")
            ?? "http://localhost:8000"
        return URL(string: raw) ?? URL(string: "http://localhost:8000")!
    }

    // MARK: - pair

    static func pair() async throws {
        let client = SyncClient(baseURL: baseURL())
        let machineID = Self.machineID()
        let label = Host.current().localizedName ?? "Mac"

        let start = try await client.startPairing(machineID: machineID, label: label)

        print("")
        print("  Open \(start.verificationURI) on a device where you are signed in,")
        print("  or use the Builder app on your phone, and enter:")
        print("")
        print("      \(start.userCode)")
        print("")
        print("  Waiting…")

        try await client.awaitPairing(
            deviceCode: start.deviceCode, intervalSeconds: start.intervalSeconds)

        print("  Paired. Run `builder sync` to upload.")
    }

    // MARK: - sync

    static func sync() async throws {
        let dryRun = CLIArgs.flag("dry-run")
        let printPayload = CLIArgs.flag("print-payload")

        let state = try SchemaManager.openState()
        let (cache, _) = try SchemaManager.openCache(tuningVersion: Tuning.version)

        let payloads = try build(state: state, cache: cache)

        if payloads.isEmpty {
            print("Nothing to sync. Run `builder scan` first.")
            return
        }

        let liveCount = payloads.filter { $0.state == "live" }.count
        let withAnalysis = payloads.filter { $0.analysis != nil }.count

        if dryRun || printPayload {
            let encoder = SessionUpload.encoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let json = try encoder.encode(["sessions": payloads])
            print(String(decoding: json, as: UTF8.self))

            if dryRun {
                FileHandle.standardError.write(
                    Data(
                        """

                        \(payloads.count) session(s) would be sent (\(liveCount) live, \
                        \(payloads.count - liveCount) final, \(withAnalysis) with an analysis). Nothing was.

                        Every key above is declared in privacy/upload-contract.json, and the
                        client encodes through a generated key enum with no synthesized
                        Codable — a field absent from that enum has no way to be sent.
                        `analysis` is the one field that carries prose; it is
                        \(AnalysisSettings.uploadEnabled() ? "ON" : "OFF") here \
                        (UserDefaults BuilderAnalysisUpload, or BUILDER_ANALYSIS_UPLOAD=0).

                        Diff it against the published contract:
                          builder sync --dry-run --print-payload \\
                            | jq -r '[paths(scalars)]|.[]|join(".")' \\
                            | sed 's/\\.[0-9][0-9]*\\./.[]./g' | sort -u

                        """.utf8))
                return
            }
        }

        let client = SyncClient(baseURL: baseURL())
        guard await client.isPaired else {
            print("Not paired. Run `builder pair` first.")
            return
        }

        // Skip anything the server already has with the same content hash. A client that
        // lost its sync state and replays everything then costs bandwidth, not
        // correctness — and on a slow link, not much bandwidth either.
        let known = (try? await client.knownHashes()) ?? [:]

        // Live snapshots are additionally rate-limited per session: on any pass where the
        // payload hash changed, at most once per `liveUploadMinIntervalSec`. The record is
        // Tier A so a cache rebuild cannot re-send every live snapshot in one burst.
        let now = Date().timeIntervalSince1970
        var lastLive: [String: (at: Double, hash: String?)] = [:]
        try state.query("SELECT client_session_id, last_uploaded_at, content_hash FROM live_upload") { s in
            if let id = s.text(0) { lastLive[id] = (s.double(1) ?? 0, s.text(2)) }
        }

        let toSend = payloads.filter { p in
            if known[p.clientSessionID] == p.contentHash { return false }
            if p.state == "live", let last = lastLive[p.clientSessionID] {
                if now - last.at < Tuning.liveUploadMinIntervalSec { return false }
                if last.hash == p.contentHash { return false }
            }
            return true
        }

        if toSend.isEmpty {
            print("Already up to date (\(payloads.count) sessions, \(liveCount) live).")
            return
        }

        let result = try await client.upload(toSend)
        let rejected = Set(result.rejected.map(\.sessionID))

        // Remember what went up. A final upload retires the session's live row: the
        // server replaced the snapshot, and the limiter has nothing left to limit.
        try state.transaction {
            for p in toSend where !rejected.contains(p.clientSessionID) {
                if p.state == "live" {
                    try state.run(
                        """
                        INSERT INTO live_upload (client_session_id, last_uploaded_at, content_hash)
                        VALUES (?,?,?)
                        ON CONFLICT(client_session_id) DO UPDATE SET
                          last_uploaded_at = excluded.last_uploaded_at,
                          content_hash = excluded.content_hash
                        """,
                        [.text(p.clientSessionID), .double(now), .text(p.contentHash)])
                } else if lastLive[p.clientSessionID] != nil {
                    try state.run(
                        "DELETE FROM live_upload WHERE client_session_id = ?", [.text(p.clientSessionID)])
                }
            }
        }

        print("  accepted   \(result.accepted)")
        print("  unchanged  \(result.unchanged)")
        print("  live       \(toSend.filter { $0.state == "live" }.count)")
        if !result.rejected.isEmpty {
            print("  rejected   \(result.rejected.count)")
            for r in result.rejected.prefix(10) {
                print("    \(r.sessionID.prefix(8))  \(r.reason)")
            }
        }
    }

    // MARK: - Building payloads

    static func build(state: SQLiteDB, cache: SQLiteDB) throws -> [SessionUpload] {
        var repoHashes: [Int: (hash: String?, name: String?, basis: String, visibility: String)] = [:]
        try state.query(
            "SELECT repo_id, repo_hash, display_name, repo_id_basis, visibility FROM repo"
        ) { s in
            guard let id = s.int(0) else { return }
            repoHashes[id] = (s.text(1), s.text(2), s.text(3) ?? "origin", s.text(4) ?? "anonymous")
        }

        var strips: [String: (cols: [UInt8], marks: [StripMarkWire])] = [:]
        try cache.query("SELECT client_session_id, cols, marks FROM strip") { s in
            guard let id = s.text(0), let cols = s.blob(1) else { return }
            let marks = (RecapMarks.decode(s.text(2))).map {
                StripMarkWire(ms: $0.ms, k: Int($0.kind.rawValue))
            }
            strips[id] = (cols, marks)
        }

        // The lifecycle table, not the cache row, decides live vs final: the deriver
        // writes every row as `final` and the state machine is what knows a session is
        // still capable of growing.
        var lifecycle: [String: String] = [:]
        try state.query("SELECT client_session_id, state FROM session_lifecycle") { s in
            if let id = s.text(0), let st = s.text(1) { lifecycle[id] = st }
        }

        let attachAnalysis = AnalysisSettings.uploadEnabled()
        let analyses = attachAnalysis ? try AnalysisStore.all(in: state) : [:]

        let machineID = Self.machineID()
        let now = Date().timeIntervalSince1970
        var out: [SessionUpload] = []

        // Column indices below are positional. The tail of the list, for checking by eye:
        //   39 repo_id_primary  40 title  41 title_source  42 unattended
        //   43 attended_seconds  44 autonomous_seconds  45 n_presence  46 end_reason
        try cache.query(
            """
            SELECT client_session_id, harness, started_at, ended_at, active_seconds,
                   idle_seconds, tz_offset_min, time_quality, visible, notable,
                   timeline_fidelity, n_prompts, prompt_count_basis, n_reads, n_edits,
                   n_writes, n_bash, n_files_touched, n_files_created, agent_lines_added,
                   agent_lines_removed, git_commits, git_insertions, git_deletions,
                   n_human_edit_events, agent_line_bucket, attrib_confidence,
                   tokens_reported, tok_in, tok_out, tok_cache_read, tok_cache_w5m,
                   tok_cache_w1h, abandoned_branch_tokens, token_dedupe, token_scope,
                   token_coverage, models_json, model_state, repo_id_primary, title,
                   title_source, unattended, attended_seconds, autonomous_seconds,
                   n_presence, end_reason
            FROM session
            WHERE visible = 1
            ORDER BY started_at DESC
            """
        ) { s in
            guard let id = s.text(0), let harness = Harness(rawValue: s.text(1) ?? "") else { return }

            let repoID = s.int(39)
            let repo = repoID.flatMap { repoHashes[$0] }

            // A session in an excluded repository is DROPPED, never partially uploaded and
            // never uploaded under some other repo's visibility.
            if repo?.visibility == "excluded" { return }

            let isPublic = repo?.visibility == "public"
            let strip = strips[id]

            let tokensReported = s.bool(27)
            let tokens =
                tokensReported
                ? TokenBucketsWire(
                    input: s.int(28) ?? 0, output: s.int(29) ?? 0,
                    cacheRead: s.int(30) ?? 0, cacheWrite5m: s.int(31) ?? 0,
                    cacheWrite1h: s.int(32) ?? 0)
                : nil

            var models: [ModelShareWire] = []
            if let json = s.text(37), let data = json.data(using: .utf8),
               let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] {
                models = arr.compactMap { entry in
                    guard let id = entry["model_id"] as? String else { return nil }
                    let share = Double(entry["output_token_share"] as? String ?? "0") ?? 0
                    return ModelShareWire(modelID: id, outputTokenShare: share)
                }
            }

            var tools: [String: Int] = [:]
            for (name, idx) in [("Read", 13), ("Edit", 14), ("Write", 15), ("Bash", 16)] {
                if let n = s.int(Int32(idx)), n > 0 { tools[name] = n }
            }

            let startedTS = s.double(2) ?? 0
            let endedTS = s.double(3) ?? 0
            let started = Date(timeIntervalSince1970: startedTS)
            let ended = Date(timeIntervalSince1970: endedTS)

            // Live or final. Open/idle in the lifecycle is live. A session the lifecycle has
            // never seen (`builder sync` without `builder watch`) is judged by the clock the
            // same way `SessionLifecycle.tick` would. A finalized session whose row still
            // says `still_running` — the last in its pool, quiet past the threshold — ended
            // on an idle gap; the server rejects `final` paired with `still_running`.
            let rowEndReason = s.text(46) ?? "idle_gap"
            let isLive: Bool
            switch lifecycle[id] {
            case "open", "idle":
                isLive = true
            case nil:
                isLive = rowEndReason == "still_running" && now - endedTS < Tuning.tauSessionSec
            default:
                isLive = false
            }
            let uploadState = isLive ? "live" : "final"
            let endReason: String
            if isLive {
                endReason = "still_running"
            } else if rowEndReason == "still_running" {
                endReason = "idle_gap"
            } else {
                endReason = rowEndReason
            }

            // The two clocks are rounded the same way from the same doubles and the
            // headline is their SUM, never rounded independently: the server rejects
            // `attended + autonomous != active` beyond a second, and three separate
            // roundings can disagree by two.
            let attended = Int((s.double(43) ?? 0).rounded())
            let autonomous = Int((s.double(44) ?? 0).rounded())
            let active = attended + autonomous
            let presence = s.int(45) ?? 0
            // The row's flag, held to the server's derivation: zero presence over a
            // notable span is unattended, any presence at all is not.
            let unattended = presence == 0 && (s.bool(42) || active >= Int(Tuning.notableMinActiveSec))

            // The stored analysis, whichever is newest (a checkpoint until the final one
            // lands). Attached only when upload is on, so it is also folded into the
            // hash only then.
            var analysis: SessionAnalysis?
            var analysisTag = "-"
            if attachAnalysis, let rec = analyses[id] {
                if let decoded = try? rec.decoded() {
                    analysis = decoded
                    analysisTag = "\(rec.digestHash)|\(rec.generatedAt)|\(rec.checkpoint)"
                } else {
                    FileHandle.standardError.write(
                        Data("warning: stored analysis for \(id.prefix(8)) did not decode; not attached\n".utf8))
                }
            }

            let title = isPublic ? s.text(40) : nil

            // The content hash is what makes re-sync free. It covers everything that can
            // change, so an unchanged session is skipped without the server being asked —
            // and everything that CAN change must be in it, or a live→final flip, a title
            // that arrives later, a re-derived strip or a fresh analysis comes back
            // `unchanged` from /v1/sync/known and is never refreshed.
            let stripTag =
                strip.map {
                    Hashing.sha256Hex(Data($0.cols)) + "|"
                        + $0.marks.map { "\($0.ms):\($0.k)" }.joined(separator: ",")
                } ?? "nostrip"
            let hashInput =
                "\(id)|\(endedTS)|\(s.double(4) ?? 0)|\(s.int(19) ?? 0)"
                + "|\(s.int(21) ?? 0)|\(s.int(11) ?? 0)|\(tokensReported)|\(isPublic)"
                + "|\(uploadState)|\(endReason)|\(attended)|\(autonomous)|\(presence)|\(unattended)"
                + "|\(title ?? "")|\(stripTag)|\(analysisTag)"

            out.append(
                SessionUpload(
                    clientSessionID: id,
                    machineID: machineID,
                    contentHash: Hashing.sha256Hex(hashInput),
                    clientVersion: "0.1.0",
                    sessionizerVersion: Tuning.sessionizerVersion,
                    activeCalcVersion: Tuning.activeCalcVersion,
                    harness: harness,
                    agentObservedAt: Date(),
                    clientClockOffsetMs: 0,
                    startedAt: started,
                    endedAt: ended,
                    activeSeconds: active,
                    idleSeconds: Int(s.double(5) ?? 0),
                    tzOffsetMinutes: s.int(6) ?? 0,
                    timeQuality: s.text(7) ?? "ok",
                    state: uploadState,
                    visible: s.bool(8),
                    notable: s.bool(9),
                    stripColumns: Data(strip?.cols ?? []).base64EncodedString(),
                    stripMarks: strip?.marks ?? [],
                    timelineFidelity: s.text(10) ?? "full",
                    humanPromptCount: s.int(11) ?? 0,
                    promptCountBasis: s.text(12) ?? "typed_promptsource",
                    toolCalls: tools,
                    filesTouched: s.int(17) ?? 0,
                    filesCreated: s.int(18) ?? 0,
                    linesAddedAgent: s.int(19) ?? 0,
                    linesRemovedAgent: s.int(20) ?? 0,
                    commitCount: s.int(21) ?? 0,
                    commitInsertions: s.int(22) ?? 0,
                    commitDeletions: s.int(23) ?? 0,
                    humanEditEvents: s.int(24) ?? 0,
                    agentLineBucket: s.text(25) ?? "unknown",
                    attribConfidence: s.text(26) ?? "none",
                    tokensReported: tokensReported,
                    tokens: tokens,
                    abandonedBranchTokens: s.int(33) ?? 0,
                    tokenDedupe: s.text(34) ?? "message_id",
                    tokenScope: s.text(35) ?? "parent_aggregated",
                    tokenCoverage: s.text(36) ?? "complete",
                    models: models,
                    modelState: s.text(38) ?? "unknown",
                    repoHash: repo?.hash,
                    repoPepperVersion: Tuning.repoPepperVersion,
                    repoIDBasis: repo?.basis ?? "origin",
                    // Public repos only. Absent — not null — for anonymous ones.
                    repoName: isPublic ? repo?.name : nil,
                    title: title,
                    titleSource: isPublic ? s.text(41) : nil,
                    endReason: endReason,
                    attendedSeconds: attended,
                    autonomousSeconds: autonomous,
                    presenceCount: presence,
                    unattended: unattended,
                    analysis: analysis))
        }

        return out
    }

    /// Stable, opaque identity for this Mac. The platform UUID is hashed rather than sent.
    static func machineID() -> String {
        let platformUUID = hardwareUUID() ?? NSFullUserName()
        return Hashing.machineID(platformUUID: platformUUID)
    }

    private static func hardwareUUID() -> String? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/sbin/ioreg")
        process.arguments = ["-rd1", "-c", "IOPlatformExpertDevice"]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = Pipe()
        try? process.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        let text = String(decoding: data, as: UTF8.self)
        guard let line = text.split(separator: "\n").first(where: { $0.contains("IOPlatformUUID") }),
              let value = line.split(separator: "\"").dropLast().last
        else { return nil }
        return String(value)
    }
}

/// Shared mark decoding, so the CLI and the sync payload agree on the JSON shape.
enum RecapMarks {
    static func decode(_ json: String?) -> [(ms: Int, kind: StripMarkKind)] {
        guard let json, let data = json.data(using: .utf8),
              let arr = try? JSONSerialization.jsonObject(with: data) as? [[Int]]
        else { return [] }
        return arr.compactMap {
            guard $0.count == 2, let k = StripMarkKind(rawValue: UInt8($0[1])) else { return nil }
            return (ms: $0[0], kind: k)
        }
    }
}
