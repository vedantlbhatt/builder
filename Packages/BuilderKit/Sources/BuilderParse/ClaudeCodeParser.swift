import BuilderModel
import Foundation

/// Reads `~/.claude/projects/**/*.jsonl`.
///
/// The reference corpus is 1.2 GB across 312 files and 108,504 records, and essentially
/// every trap below was found by measuring it rather than by reading documentation. Each
/// one is annotated with the count that justifies the handling, because the failure mode
/// of a log parser is not a crash — it is a plausible wrong number that nobody questions.
public struct ClaudeCodeParser: HarnessParser {

    public let harness: Harness = .claudeCode

    /// Bump this when the interpretation of the same bytes changes. Sources whose stored
    /// watermark carries an older version are deleted and re-read from zero.
    ///
    /// 2: `promptSource == "sdk"` + `origin.kind == "human"` is a prompt, and a user
    ///    record beginning `[Request interrupted by user` is an `.interrupt`. Both were
    ///    `.noise` under version 1, so every already-ingested remote session must be
    ///    re-read or it stays filed as unattended (docs/session-boundaries.md).
    /// 3: `LivePathResolver` resolves the DAG as a forest and treats only a fork whose
    ///    surviving child is a human record as a rewind. Under version 2 parallel tool
    ///    calls, stop-hook continuations and second roots were filed as rewound (575 records
    ///    on one machine, `scripts/measure_live_path.py`); `on_live_path` is written at
    ///    ingest, so every source must be re-read.
    public let parserVersion = 3

    private let projectsRoot: String

    /// Not stored: FileManager is not Sendable, and this struct is.
    private var fm: FileManager { .default }

    public init(projectsRoot: String? = nil) {
        self.projectsRoot =
            projectsRoot
            ?? (NSHomeDirectory() as NSString).appendingPathComponent(".claude/projects")
    }

    // MARK: - Discovery

    public func discover() throws -> [SourceRef] {
        var out: [SourceRef] = []
        guard let projectDirs = try? fm.contentsOfDirectory(atPath: projectsRoot) else { return [] }

        for dir in projectDirs {
            let dirPath = (projectsRoot as NSString).appendingPathComponent(dir)
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: dirPath, isDirectory: &isDir), isDir.boolValue else { continue }

            // A project directory can legitimately contain ZERO .jsonl files and still
            // exist: entering a worktree mid-session writes only sidecar directories
            // under the worktree-encoded name. Observed on the reference machine. A
            // parser that assumes dir -> transcripts reports a phantom empty project.
            guard let entries = fm.enumerator(atPath: dirPath) else { continue }

            for case let rel as String in entries {
                guard rel.hasSuffix(".jsonl") else { continue }
                let full = (dirPath as NSString).appendingPathComponent(rel)
                let sidecar = !Self.isRootTranscript(relativePath: rel)
                out.append(
                    SourceRef(
                        sourceID: Hashing.sourceID(harness: .claudeCode, descriptor: "\(dir)/\(rel)"),
                        harness: .claudeCode,
                        kind: .jsonl,
                        path: full,
                        isSidecar: sidecar
                    )
                )
            }
        }
        return out
    }

    /// A root transcript is EXACTLY `<projectdir>/<uuid>.jsonl` — one path component.
    ///
    /// This is an ALLOWLIST on shape, deliberately, not a denylist on `subagents/`.
    /// The tree contains `<uuid>/subagents/`, `<uuid>/workflows/` and `<uuid>/tool-results/`
    /// as siblings, so a denylist naming only `subagents` waves the other two through as
    /// roots. Since `Agent` tool results already carry their subagent's aggregated usage,
    /// counting a sidecar as a root adds those tokens a second time — the ~3x overcount
    /// that a `**/*.jsonl` glob produces, arriving through a path nobody grepped for.
    ///
    /// Test: a file at `<projectdir>/<uuid>/futuredir/x.jsonl` must be a sidecar.
    public static func isRootTranscript(relativePath: String) -> Bool {
        !relativePath.contains("/")
    }

    // MARK: - Parsing

    public func parse(source: SourceRef, from watermark: Watermark) throws -> ParseResult {
        var diagnostics: [ParseDiagnostic] = []
        var events: [NormalizedEvent] = []

        let attrs = try? fm.attributesOfItem(atPath: source.path)
        let size = (attrs?[.size] as? NSNumber)?.intValue ?? 0
        let mtime = (attrs?[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0
        let ino = (attrs?[.systemFileNumber] as? NSNumber)?.intValue
        let dev = (attrs?[.systemNumber] as? NSNumber)?.intValue
        let headSHA = Hashing.headSHA256(path: source.path, bytes: Tuning.headHashBytes)

        let decision = watermark.decide(
            currentSize: size,
            currentMtime: mtime,
            currentDev: dev,
            currentIno: ino,
            currentHeadSHA: headSHA,
            parserVersion: parserVersion
        )

        var startOffset = 0
        var startLine = 0
        switch decision {
        case .skip:
            return ParseResult(events: [], watermark: watermark, diagnostics: [])
        case .resume(let off, let idx):
            startOffset = off
            startLine = idx
        case .restart(let reason):
            diagnostics.append(ParseDiagnostic(code: "source_restart", detail: reason))
        }

        let reader = try LineReader(
            path: source.path,
            startOffset: startOffset,
            startLineIndex: startLine,
            onDiagnostic: { code, detail in
                diagnostics.append(ParseDiagnostic(code: code, detail: detail))
            }
        )
        defer { reader.close() }

        // Deduplication is per (source, message.id). MEASURED: 44,419 records carry
        // .message.usage but only 22,887 distinct .message.id exist, because Claude Code
        // writes ONE RECORD PER CONTENT BLOCK and repeats the identical usage object on
        // each. Naive 10,922,288,007 vs deduped 5,815,701,063 => 1.878x.
        //
        // Resuming mid-file cannot see keys claimed before the watermark, so the database's
        // partial UNIQUE INDEX is the real enforcement; this set only avoids generating
        // obvious duplicates within one batch.
        var seenUsageKeys = Set<String>()

        var malformed = 0
        var unknownShapes = 0

        while let line = try reader.next() {
            guard let node = JSONLine.parse(line.data) else {
                malformed += 1
                continue
            }
            let produced = self.events(
                from: node,
                source: source,
                ordinal: line.index,
                seenUsageKeys: &seenUsageKeys,
                unknownShapes: &unknownShapes
            )
            events.append(contentsOf: produced)
        }

        if malformed > 0 {
            diagnostics.append(
                ParseDiagnostic(code: "malformed_json_line", detail: "\(malformed) line(s) in \(source.path)")
            )
        }
        if unknownShapes > 0 {
            diagnostics.append(
                ParseDiagnostic(code: "unknown_record_shape", detail: "\(unknownShapes) record(s)")
            )
        }

        var wm = watermark
        wm.byteOffset = reader.endOffset
        // From the reader, NOT from events.count: one line can yield several events and
        // several lines can yield none, so they are different quantities.
        wm.lineCount = reader.nextLineIndex
        wm.sizeBytes = size
        wm.mtime = mtime
        wm.stIno = ino
        wm.stDev = dev
        wm.headSHA256 = headSHA
        wm.parserVersion = parserVersion

        return ParseResult(events: events, watermark: wm, diagnostics: diagnostics, fidelity: .full)
    }

    // MARK: - Record -> events

    private func events(
        from r: JSONNode,
        source: SourceRef,
        ordinal: Int,
        seenUsageKeys: inout Set<String>,
        unknownShapes: inout Int
    ) -> [NormalizedEvent] {

        let type = r.type.string ?? ""

        // `sessionId`, not `session_id`. Both keys exist; the snake_case duplicate is
        // present on only 51,748 of 108,504 records, so keying off it silently loses half.
        let sessionID = r.sessionId.string ?? r.session_id.string

        // ISO-8601 UTC with milliseconds, always `Z`. Absent on ~24,151 of 108,504
        // records — all bookkeeping types. NEVER imputed.
        let ts = ISO8601.seconds(r.timestamp.string)

        let cwd = r.cwd.nonEmptyString
        let version = r.version.nonEmptyString
        let uuid = r.uuid.string

        // `parentUuid` is null across a compact_boundary; `logicalParentUuid` bridges it.
        // Without that fallback the DAG fragments at every compaction and the live-path
        // walk stops early, silently attributing rewound work to the live branch.
        let parent = r.parentUuid.string ?? r.logicalParentUuid.string

        let isSidechain = r.isSidechain.bool ?? source.isSidecar
        let agentID = r.agentId.string

        func base(_ kind: EventKind, idSuffix: String = "") -> NormalizedEvent {
            let nativeID = (uuid ?? "l\(ordinal)") + idSuffix
            return NormalizedEvent(
                eventUID: Hashing.eventUID(
                    harness: .claudeCode, sourceID: source.sourceID, nativeEventID: nativeID),
                harness: .claudeCode,
                sourceID: source.sourceID,
                ordinal: ordinal,
                nativeSessionID: sessionID,
                nativeEventID: nativeID,
                nativeParentID: parent,
                agentID: agentID,
                isSidechain: isSidechain,
                ts: ts,
                cwd: cwd,
                harnessVersion: version,
                kind: kind
            )
        }

        switch type {

        case "user":
            // MEASURED: 18,836 records of type "user", but only 1,456 are a human typing.
            // The rest are tool results and system-injected context. Counting them all as
            // prompts inflates the number by ~13x, and "prompts" is a card metric.
            //
            // Sessions driven from the Claude Code web/phone UI stamp their prompts as
            // `promptSource: "sdk"` with `origin.kind: "human"` instead of `typed`.
            // MEASURED on a remote transcript: 9 of 9 human prompts were sdk/human and 0
            // were typed, so the typed-only rule counted zero prompts and would have
            // filed the whole sitting as unattended. Slash commands (`/model`, `/effort`)
            // and `isMeta` injections remain non-prompts under both shapes.
            let promptSource = r.promptSource.string
            let isMeta = r.isMeta.bool ?? false
            let isHumanPrompt =
                !isMeta
                && (promptSource == "typed"
                    || (promptSource == "sdk" && r.origin.kind.string == "human"))

            var out: [NormalizedEvent] = []

            // `.message.content` is a plain String on 3,299 user records and an array of
            // blocks on the rest. `contentBlocks` normalizes both.
            let blocks = r.message.content.contentBlocks

            // The harness sentinel for Escape / "stop". The text is the String content, or
            // the joined `text` blocks; a record with no text block is never an interrupt.
            // A presence signal, not a prompt — nobody presses stop from the other room.
            var textParts: [String] = []
            for b in blocks where b.type.string == "text" {
                textParts.append(b.text.string ?? "")
            }
            let isInterrupt =
                !textParts.isEmpty
                && textParts.joined(separator: "\n").hasPrefix(Tuning.interruptPrefix)
            for (i, b) in blocks.enumerated() where b.type.string == "tool_result" {
                var e = base(.toolResult, idSuffix: "#tr\(i)")
                e.toolID = b.tool_use_id.string
                // `.toolUseResult` is a dict on 15,355 records, a String on 401, and a
                // list on 3. Only reach into it when it is actually an object.
                if let res = r.toolUseResult.object {
                    let resNode = JSONNode(res)
                    // Read's path is nested one level deeper than Edit's or Write's.
                    e.targetPath =
                        resNode.filePath.string
                        ?? resNode.file.filePath.string
                    if let patch = resNode.structuredPatch.array, !patch.isEmpty {
                        // Edit: count the +/- lines in the patch hunks.
                        var added = 0
                        var removed = 0
                        for hunk in patch {
                            for l in hunk.lines.array ?? [] {
                                guard let s = l.string else { continue }
                                if s.hasPrefix("+") { added += 1 } else if s.hasPrefix("-") { removed += 1 }
                            }
                        }
                        e.linesAdded = added
                        e.linesRemoved = removed
                    } else if resNode.type.string == "create", let content = resNode.content.string {
                        // Write: structuredPatch is ALWAYS [] for a creation, so the patch
                        // path above yields zero. Count newlines in the created content
                        // instead, or every file the agent wrote from scratch scores 0.
                        e.linesAdded = content.isEmpty ? 0 : content.split(separator: "\n", omittingEmptySubsequences: false).count
                        e.linesRemoved = 0
                    }
                }
                out.append(e)
            }

            if isHumanPrompt {
                out.append(base(.prompt))
            } else if isInterrupt {
                out.append(base(.interrupt))
            } else if out.isEmpty {
                out.append(base(.noise))
            }
            return out

        case "assistant":
            var out: [NormalizedEvent] = []

            // MEASURED: `<synthetic>` appears literally, on 15 records. These are locally
            // generated placeholders for errors and interrupts, not API calls — excluded
            // from cost AND from token totals before any price lookup.
            let rawModel = r.message.model.nonEmptyString
            let model = (rawModel == Tuning.syntheticModelSentinel) ? nil : rawModel
            let isSynthetic = rawModel == Tuning.syntheticModelSentinel

            // THE DEDUPE KEY. First record per (source, message.id) in file order carries
            // the usage; every later content block for the same message carries none.
            let messageID = r.message.id.nonEmptyString ?? r.requestId.nonEmptyString
            let dedupeKey = messageID.map { "\(source.sourceID)|\($0)" }
            var authoritative = false
            if let k = dedupeKey, !isSynthetic, r.message.usage.exists, !seenUsageKeys.contains(k) {
                seenUsageKeys.insert(k)
                authoritative = true
            }

            let u = r.message.usage
            let blocks = r.message.content.contentBlocks

            var emittedAny = false
            for (i, b) in blocks.enumerated() {
                let bt = b.type.string ?? ""
                var e: NormalizedEvent
                switch bt {
                case "tool_use":
                    e = base(.toolUse, idSuffix: "#tu\(i)")
                    e.toolName = b.name.string
                    e.toolID = b.id.string
                    // Read / Edit / Write all put the path at .input.file_path.
                    e.targetPath = b.input.file_path.string
                case "thinking":
                    e = base(.thinking, idSuffix: "#th\(i)")
                case "text":
                    e = base(.assistantMessage, idSuffix: "#tx\(i)")
                default:
                    unknownShapes += 1
                    e = base(.unknown, idSuffix: "#u\(i)")
                }
                e.model = model
                e.effort = r.effort.nonEmptyString
                e.role = "assistant"
                e.dedupeKey = dedupeKey

                // Usage is recorded on EVERY block, exactly as the file writes it, but
                // exactly one block per message is flagged authoritative.
                //
                // Storing it faithfully rather than dropping it matters for two reasons:
                // the difference between the two sums IS the 1.878x overcount, so the
                // regression test can measure it rather than assume it; and a future
                // schema change cannot silently turn a dropped field into a summed one.
                // Only `usage_authoritative = 1` rows are ever summed.
                if u.exists && !isSynthetic {
                    e.tokIn = u.input_tokens.int
                    e.tokOut = u.output_tokens.int
                    e.tokCacheRead = u.cache_read_input_tokens.int
                    e.tokCacheW5m =
                        u.cache_creation.ephemeral_5m_input_tokens.int
                        ?? u.cache_creation_input_tokens.int
                    e.tokCacheW1h = u.cache_creation.ephemeral_1h_input_tokens.int
                    e.usageAuthoritative = authoritative && !emittedAny
                }
                emittedAny = true
                out.append(e)
            }

            if out.isEmpty {
                var e = base(.assistantMessage)
                e.model = model
                e.dedupeKey = dedupeKey
                if authoritative {
                    e.usageAuthoritative = true
                    e.tokIn = u.input_tokens.int
                    e.tokOut = u.output_tokens.int
                    e.tokCacheRead = u.cache_read_input_tokens.int
                    e.tokCacheW5m =
                        u.cache_creation.ephemeral_5m_input_tokens.int
                        ?? u.cache_creation_input_tokens.int
                    e.tokCacheW1h = u.cache_creation.ephemeral_1h_input_tokens.int
                }
                out.append(e)
            }
            return out

        case "system":
            switch r.subtype.string ?? "" {
            case "turn_duration":
                // Real per-turn wall time, free. But MEASURED coverage is 1,904 records
                // against 30,840 assistant records — 6% — and whether it measures the
                // assistant alone or the full round trip is unverified. Used as a
                // cross-check on derived spans, never as the sole source of truth.
                var e = base(.turnDuration)
                e.durationMs = r.durationMs.int
                return [e]
            case "compact_boundary":
                // A zero-duration marker, never a session boundary. Its preTokens and
                // postTokens are CONTEXT SIZES, not usage, and are never summed.
                return [base(.compaction)]
            default:
                return [base(.noise)]
            }

        case "attachment":
            // `edited_text_file` means the human edited a file OUTSIDE the agent — the
            // only true-positive human-authorship signal available. MEASURED: 296 records
            // against 2,227 Edits and 581 Writes. It carries NO line count, which is
            // precisely why human-vs-agent ships as a bounded bucket, not a percentage.
            if r.attachment.type.string == "edited_text_file" {
                var e = base(.humanEdit)
                e.targetPath = r.attachment.filename.string ?? r.attachment.path.string
                return [e]
            }
            return [base(.noise)]

        case "ai-title":
            var e = base(.title)
            e.title = r.aiTitle.nonEmptyString
            return [e]

        case "last-prompt":
            // `leafUuid` points at a real timestamped record, which is how a title finds
            // the session that actually contains it. One file routinely yields many
            // sessions — 43 files have internal gaps over an hour and the longest single
            // file spans 121.3 hours — but carries only one current title. "Last title in
            // the file" would stamp this afternoon's title on a card for three days ago.
            var e = base(.title)
            e.leafUUID = r.leafUuid.string
            return [e]

        case "mode", "permission-mode", "file-history-snapshot", "file-history-delta",
             "relocated", "worktree-state", "agent-name", "agent-color", "bridge-session",
             "queue-operation", "frame-link", "pr-link", "started", "result",
             "fork-context-ref":
            return [base(.noise)]

        default:
            unknownShapes += 1
            return [base(.unknown)]
        }
    }
}
