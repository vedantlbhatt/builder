import BuilderModel
import Foundation

/// Reads Gemini CLI chat recordings:
/// `~/.gemini/tmp/<project>/chats/session-<YYYY-MM-DDTHH-MM>-<id8>.jsonl`, and the subagent
/// recordings one level down at `chats/<parentSessionId>/<sessionId>.jsonl`.
///
/// The reference implementation is `analysis/gemini.py`; every rule below carries the
/// VERIFIED / ASSUMED label it has there. VERIFIED means read from the Gemini CLI source on
/// 2026-09-05 (`packages/core/src/services/chatRecordingService.ts`, `chatRecordingTypes.ts`,
/// `core/geminiChat.ts`, `utils/sessionUtils.ts`, `scheduler/types.ts`); ASSUMED means an
/// older on-disk form or a rule nobody has compared against a corpus.
///
/// This parser has NOT yet been measured against a real corpus — only against the synthetic
/// fixture under `spec/fixtures/gemini/`. The house rule applies: a parser written from a
/// description ships with diagnostics first, and the first real corpus decides what this
/// file got wrong before any number reaches a card. Every lenient branch increments a
/// counter that surfaces as a `ParseDiagnostic`.
///
/// Four shape facts that drive the design:
///
/// - **The file is a log of a mutable record, not a log of events.** `pushMessage` appends
///   the FULL message record every time it changes, and `recordMessageTokens` /
///   `recordToolCalls` change the last gemini message in place — so one message id appears
///   on several lines and the reader (`loadConversationRecord`) keeps a Map keyed by id in
///   which the LAST copy wins. A streaming parser cannot rewrite history, so it does the
///   next best thing: it emits each FACET of a message (its prompt, its text, each tool
///   call, each result) the first time that facet appears, under an id derived from the
///   message id rather than the line, and never again. A re-append that adds a facet emits
///   only the new facet. `$rewindTo` records are the one thing this cannot honour — see
///   `gemini_rewind_seen`.
///
/// - **THE GEMINI RE-APPEND TRAP.** The fixture's first gemini message is written three
///   times: bare text, then text + `tokens`, then text + `tokens` + `toolCalls`. Two of the
///   three lines carry the identical `tokens` object, so summing per line overcounts (the
///   reference: 75,042 naive against 51,862 per id, `usage()` reports both side by side).
///   Usage is therefore claimed ONCE per `(source, message id)`: the first line to carry
///   `tokens` for an id is the authoritative carrier, and the set of claimed ids is REPLAYED
///   from the lines before the watermark on a resume (`priorContext`), so the second carrier
///   is emitted non-authoritative on purpose rather than colliding with the store's partial
///   unique index and being demoted there. Non-authoritative carriers still carry the
///   numbers, which is what lets `TokenAccountant.naiveTotal` reproduce the trap in a test.
///
/// - **Tokens ride on a per-line carrier, never on a facet event.** A facet's id repeats
///   across lines (that is how the store's `INSERT OR IGNORE` suppresses a re-append that
///   crosses a watermark), so a usage claim attached to a facet event could be silently
///   dropped along with the duplicate row. The carrier's id is the line index, unique by
///   construction — the same shape `CodexParser` uses for `token_count`.
///
/// - **There is no DAG.** Records carry no parent pointer, so `nativeParentID` is nil and
///   `LivePathResolver` treats every event as live. A `$rewindTo` therefore leaves the
///   abandoned messages' events — and their tokens — in place; the API charged for them,
///   which is also how a rewound Claude Code branch is treated (`TokenAccountant.ledger`).
public struct GeminiParser: HarnessParser {

    public let harness: Harness = .geminiCLI

    /// Bump when the interpretation of the same bytes changes. Sources whose stored
    /// watermark carries an older version are deleted and re-read from zero.
    public let parserVersion = 1

    private let tmpRoot: String

    /// Not stored: FileManager is not Sendable, and this struct is.
    private var fm: FileManager { .default }

    public init(tmpRoot: String? = nil) {
        self.tmpRoot =
            tmpRoot
            ?? (NSHomeDirectory() as NSString).appendingPathComponent(".gemini/tmp")
    }

    // MARK: - Constants carried over from analysis/gemini.py

    /// VERIFIED: tools/definitions/base-declarations.ts.
    static let shellTool = "run_shell_command"
    static let writeFileTool = "write_file"
    static let editTool = "replace"
    static let fileTools: Set<String> = ["write_file", "replace", "read_file", "read_many_files"]

    /// VERIFIED: chatRecordingTypes.ts `ConversationRecordExtra["type"]`.
    static let knownMessageTypes: Set<String> = ["user", "gemini", "info", "error", "warning"]

    /// VERIFIED: scheduler/types.ts `CoreToolCallStatus`.
    static let knownStatuses: Set<String> = [
        "validating", "scheduled", "error", "success", "executing", "cancelled", "awaiting_approval",
    ]

    /// VERIFIED: the `@google/genai` Part keys the CLI writes. `thought` / `thoughtSignature`
    /// are metadata on a text part, not part kinds.
    static let knownPartKeys: Set<String> = [
        "text", "functionCall", "functionResponse", "inlineData", "fileData",
        "executableCode", "codeExecutionResult", "videoMetadata",
    ]
    static let partMetaKeys: Set<String> = ["thought", "thoughtSignature"]

    /// VERIFIED: utils/sessionUtils.ts `isIgnoredUserContent` — slash commands, `?` help
    /// and the two injected context envelopes are not a person typing.
    static let ignoredPromptPrefixes = ["/", "?", "<session_context>", "<hook_context>"]

    /// `file_path` is VERIFIED; `absolute_path` and `path` are ASSUMED older spellings.
    static let pathArgKeys = ["file_path", "absolute_path", "path"]

    /// Above this many DP cells the `replace` line diff degrades to "every old line removed,
    /// every new line added" rather than allocating quadratically for a pathological edit.
    static let maxDiffCells = 4_000_000

    // MARK: - Discovery

    public func discover() throws -> [SourceRef] {
        var out: [SourceRef] = []
        // Must not throw for a missing root — most users do not have Gemini CLI installed.
        guard let entries = fm.enumerator(atPath: tmpRoot) else { return [] }

        for case let rel as String in entries {
            guard let shape = Self.recordingShape(relativePath: rel) else { continue }
            let full = (tmpRoot as NSString).appendingPathComponent(rel)
            out.append(
                SourceRef(
                    // The relative path, not the absolute one: the file is the same source
                    // if the home directory moves, and two recordings never share a
                    // relative path because the session id is in the filename.
                    sourceID: Hashing.sourceID(harness: .geminiCLI, descriptor: rel),
                    harness: .geminiCLI,
                    kind: .jsonl,
                    path: full,
                    isSidecar: shape.parentSessionID != nil
                )
            )
        }
        return out
    }

    /// Where a recording sits in the tree. `parentSessionID` is non-nil for a subagent
    /// recording, whose events attach to the parent's session.
    public struct RecordingShape: Equatable, Sendable {
        public let projectID: String
        public let parentSessionID: String?
    }

    /// A recording is EXACTLY `<project>/chats/<name>.jsonl` (a root) or
    /// `<project>/chats/<parentSessionId>/<name>.jsonl` (a subagent sidecar). An ALLOWLIST on
    /// shape, like `ClaudeCodeParser.isRootTranscript`: the project directory also holds
    /// `checkpoints/`, `logs.json` and whatever a future release adds, and a `**/*.jsonl`
    /// glob would read any of it as a session. The legacy whole-file `.json` form is not
    /// read by this parser (the CLI migrates it to `.jsonl` on resume; `analysis/gemini.py`
    /// still reads it for the probe).
    public static func recordingShape(relativePath rel: String) -> RecordingShape? {
        let parts = rel.split(separator: "/", omittingEmptySubsequences: false)
        guard parts.count == 3 || parts.count == 4 else { return nil }
        guard !parts[0].isEmpty, parts[1] == "chats" else { return nil }
        let name = parts[parts.count - 1]
        guard name.hasSuffix(".jsonl"), name.count > ".jsonl".count else { return nil }
        if parts.count == 3 {
            return RecordingShape(projectID: String(parts[0]), parentSessionID: nil)
        }
        guard !parts[2].isEmpty else { return nil }
        return RecordingShape(projectID: String(parts[0]), parentSessionID: String(parts[2]))
    }

    // MARK: - Per-file state

    /// Everything one pass over a recording has to remember between lines.
    ///
    /// A reference type on purpose: the per-line handler builds events through nested
    /// functions that read this state while helpers mutate it, and an `inout` struct in
    /// that position trips Swift's exclusivity checking.
    private final class FileContext {
        /// From the path, for a sidecar: the session its events belong to.
        let parentSessionID: String?
        /// From the metadata line. For a root this IS the session; for a sidecar it is the
        /// subagent's own id, stamped as `agentID`.
        var ownSessionID: String?
        var projectHash: String?
        var cwd: String?
        /// Last `model` seen on a gemini record; tool calls inherit it.
        var model: String?

        /// Message ids whose `tokens` have already been claimed by an authoritative carrier.
        /// REPLAYED from before the watermark on a resume — the one piece of state that must
        /// survive a watermark, or the second carrier of a re-appended id is marked
        /// authoritative again (see the trap in the type comment).
        var claimedTokenIDs = Set<String>()

        /// Facets already emitted in THIS batch. Not replayed: a facet re-emitted after a
        /// watermark carries the same `nativeEventID` as its first emission, so the store's
        /// `INSERT OR IGNORE` on `event_uid` suppresses it.
        var emittedPrompt = Set<String>()
        var emittedText = Set<String>()
        var emittedCalls = Set<String>()
        var emittedResults = Set<String>()
        var seenMessageIDs = Set<String>()

        /// call key -> (tool name, path), so a result recorded on a later line can name
        /// its tool and file.
        var toolByCallKey: [String: (name: String, path: String?)] = [:]

        struct Counters {
            var unknownShapes = 0
            var messageRewrite = 0
            var rewindSeen = 0
            var rewindUnknownID = 0
            var setMessagesRebuild = 0
            var legacyRecordInline = 0
            var messageNoTimestamp = 0
            var toolCallNoTimestamp = 0
            var promptIgnored = 0
            var promptFromDisplayContent = 0
            var userToolResponseRecords = 0
            var userToolResponseDeduped = 0
            var toolCallNotObject = 0
            var toolStatusUnknown = 0
            var toolStatusIncomplete = 0
            var toolStatusCancelled = 0
            var toolFromFunctionCallPart = 0
            var functionCallPartDeduped = 0
            var resultError = 0
            var unknownPartShape = 0
            var messageTypeInfo = 0
            var messageTypeError = 0
            var messageTypeWarning = 0
            var messageTypeUnknown = 0
            var tokenReappend = 0
            var tokensOnNonGemini = 0
            var cachedExceedsInput = 0
            var toolTokensSeen = 0
            var tokenTotalMismatch = 0
            var cwdFromDirectories = 0
        }
        var counters = Counters()

        init(parentSessionID: String?) {
            self.parentSessionID = parentSessionID
        }

        var sessionID: String? { parentSessionID ?? ownSessionID }
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

        // The parent id comes from the directory name, so a sidecar's events attach to the
        // parent session even when the resume starts past the metadata line.
        let rel = Self.relativePath(of: source.path, under: tmpRoot)
        let ctx = FileContext(parentSessionID: Self.recordingShape(relativePath: rel)?.parentSessionID)
        if startOffset > 0 {
            // Replay session id / cwd / claimed token ids from the lines before the
            // watermark. Without this every resumed event carries a nil session id and the
            // first re-appended carrier after the watermark is claimed a second time.
            try priorContext(path: source.path, before: startOffset, into: ctx)
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

        var malformed = 0

        while let line = try reader.next() {
            guard let node = JSONLine.parse(line.data), node.isObject else {
                malformed += 1
                continue
            }
            events.append(contentsOf: self.events(from: node, source: source, ordinal: line.index, ctx: ctx))
        }

        if malformed > 0 {
            diagnostics.append(
                ParseDiagnostic(code: "malformed_json_line", detail: "\(malformed) line(s) in \(source.path)")
            )
        }
        let c = ctx.counters
        func note(_ code: String, _ n: Int, _ what: String) {
            if n > 0 { diagnostics.append(ParseDiagnostic(code: code, detail: "\(n) \(what)")) }
        }
        note("unknown_record_shape", c.unknownShapes, "record(s)")
        note("gemini_rewind_seen", c.rewindSeen,
             "$rewindTo record(s); rows already emitted for the rewound messages are not retracted and their tokens stay counted")
        note("gemini_rewind_unknown_id", c.rewindUnknownID, "$rewindTo record(s) naming an id never seen in this batch (the reader would clear every message)")
        note("gemini_message_rewrite", c.messageRewrite, "message record(s) re-appending an id already seen in this batch")
        note("gemini_token_reappend", c.tokenReappend, "re-appended message line(s) carrying tokens already claimed; stored non-authoritative")
        note("gemini_set_messages_rebuild", c.setMessagesRebuild, "$set record(s) replacing the whole message list")
        note("gemini_legacy_record_inline", c.legacyRecordInline, "metadata line(s) carrying an inline legacy message list")
        note("gemini_message_no_timestamp", c.messageNoTimestamp, "message record(s) without a timestamp (never imputed)")
        note("gemini_tool_call_no_timestamp", c.toolCallNoTimestamp, "tool call record(s) without a timestamp (stamped with the message's)")
        note("gemini_prompt_ignored", c.promptIgnored, "user message(s) that were empty, slash commands or injected context")
        note("gemini_prompt_from_display_content", c.promptFromDisplayContent, "prompt(s) judged by displayContent (@file expansion)")
        note("gemini_user_tool_response_records", c.userToolResponseRecords, "user record(s) that were tool responses, not prompts")
        note("gemini_user_tool_response_deduped", c.userToolResponseDeduped, "user functionResponse part(s) whose result a toolCalls record already carried")
        note("gemini_tool_call_not_object", c.toolCallNotObject, "toolCalls entry(ies) that were not objects")
        note("gemini_tool_status_unknown", c.toolStatusUnknown, "tool call(s) with a status not in CoreToolCallStatus")
        note("gemini_tool_status_incomplete", c.toolStatusIncomplete, "tool call(s) recorded while still validating / scheduled / executing / awaiting approval")
        note("gemini_tool_status_cancelled", c.toolStatusCancelled, "cancelled tool call(s) — counted, not emitted as an interrupt: the source does not say whether it was Escape or a policy denial")
        note("gemini_tool_from_function_call_part", c.toolFromFunctionCallPart, "tool call(s) taken from a functionCall part with no toolCalls record")
        note("gemini_function_call_part_deduped", c.functionCallPartDeduped, "functionCall part(s) already covered by a toolCalls record")
        note("gemini_result_error", c.resultError, "tool result(s) flagged as errors by status or response.error")
        note("gemini_unknown_part_shape", c.unknownPartShape, "content part(s) with no recognised key")
        note("gemini_message_type_info", c.messageTypeInfo, "info record(s)")
        note("gemini_message_type_error", c.messageTypeError, "error record(s) (UI/API errors, not tool failures)")
        note("gemini_message_type_warning", c.messageTypeWarning, "warning record(s)")
        note("gemini_message_type_unknown", c.messageTypeUnknown, "message record(s) with an unlisted type")
        note("gemini_tokens_on_non_gemini", c.tokensOnNonGemini, "non-gemini record(s) carrying tokens; not summed")
        note("gemini_cached_exceeds_input", c.cachedExceedsInput, "token summary(ies) with cached > input")
        note("gemini_tool_tokens_seen", c.toolTokensSeen, "token summary(ies) with tool > 0 (folded into input; ASSUMED prompt-side)")
        note("gemini_token_total_mismatch", c.tokenTotalMismatch, "token summary(ies) where input + output + thoughts + tool != total — the first real corpus decides whether cached is inside input")
        note("gemini_cwd_from_directories", c.cwdFromDirectories, "recording(s) whose cwd came from the ASSUMED `directories` field")

        var wm = watermark
        wm.byteOffset = reader.endOffset
        // From the reader, NOT from events.count: several lines yield several events.
        wm.lineCount = reader.nextLineIndex
        wm.sizeBytes = size
        wm.mtime = mtime
        wm.stIno = ino
        wm.stDev = dev
        wm.headSHA256 = headSHA
        wm.parserVersion = parserVersion

        return ParseResult(events: events, watermark: wm, diagnostics: diagnostics, fidelity: .full)
    }

    /// `source.path` relative to the root, or the path itself when it is not under it (a
    /// test handing in an arbitrary file); the shape check then simply finds no parent.
    static func relativePath(of path: String, under root: String) -> String {
        let prefix = root.hasSuffix("/") ? root : root + "/"
        guard path.hasPrefix(prefix) else { return path }
        return String(path.dropFirst(prefix.count))
    }

    /// Recover the file-level context from the lines before `offset`, emitting nothing.
    ///
    /// Only lines that can carry context are JSON-parsed (a cheap byte scan for a marker
    /// first): the metadata line, `$set` records, and any message line carrying `tokens` —
    /// the claim set is what a resume must not lose. It stops at the first complete line
    /// that begins at or past the watermark, which always sits on a line boundary because
    /// `LineReader` never commits mid-line.
    private func priorContext(path: String, before offset: Int, into ctx: FileContext) throws {
        let reader = try LineReader(path: path, startOffset: 0, startLineIndex: 0)
        defer { reader.close() }
        let markers = [
            Data("\"projectHash\"".utf8),
            Data("\"$set\"".utf8),
            Data("\"tokens\"".utf8),
        ]
        while let line = try reader.next() {
            if line.offset >= offset { break }
            guard markers.contains(where: { line.data.range(of: $0) != nil }) else { continue }
            guard let node = JSONLine.parse(line.data), node.isObject else { continue }
            absorbContext(node, into: ctx)
        }
    }

    /// The context-bearing record shapes, in one place so the live pass and the resume
    /// replay cannot disagree about them. Returns nothing; counts nothing.
    private func absorbContext(_ r: JSONNode, into ctx: FileContext) {
        if r["$rewindTo"].isString { return }
        if r.id.isString {
            if r.tokens.isObject, let id = r.id.nonEmptyString { ctx.claimedTokenIDs.insert(id) }
            return
        }
        if r["$set"].isObject {
            let s = r["$set"]
            absorbDirectories(s.directories, into: ctx, count: false)
            for m in s.messages.array ?? [] where m.isObject {
                if m.tokens.isObject, let id = m.id.nonEmptyString { ctx.claimedTokenIDs.insert(id) }
            }
            return
        }
        if r.sessionId.isString, r.projectHash.isString {
            // VERIFIED: `{sessionId, projectHash, startTime, lastUpdated, kind?, directories?}`.
            if let id = r.sessionId.nonEmptyString { ctx.ownSessionID = id }
            if let h = r.projectHash.nonEmptyString { ctx.projectHash = h }
            absorbDirectories(r.directories, into: ctx, count: false)
            for m in r.messages.array ?? [] where m.isObject {
                if m.tokens.isObject, let id = m.id.nonEmptyString { ctx.claimedTokenIDs.insert(id) }
            }
        }
    }

    /// ASSUMED: `directories` lists the workspace directories, first one primary. There is
    /// no `cwd` anywhere in a recording (the project is identified by hash), so this is the
    /// only path a repo could be resolved from. Counted whenever it is used.
    private func absorbDirectories(_ node: JSONNode, into ctx: FileContext, count: Bool) {
        guard let first = node.array?.first?.nonEmptyString else { return }
        if ctx.cwd != first {
            ctx.cwd = first
            if count { ctx.counters.cwdFromDirectories += 1 }
        }
    }

    // MARK: - Record -> events

    /// VERIFIED classification order from `loadConversationRecord`: `$rewindTo` string →
    /// rewind; `id` string → message; `$set` object → metadata update; `sessionId` +
    /// `projectHash` → metadata. Anything else is unknown.
    private func events(
        from r: JSONNode,
        source: SourceRef,
        ordinal: Int,
        ctx: FileContext
    ) -> [NormalizedEvent] {

        let lineID = "l\(ordinal)"

        if r["$rewindTo"].isString {
            // VERIFIED `rewindTo`: the reader drops that message and everything after it
            // (or everything, if the id is unknown). Already-emitted rows cannot be taken
            // back from here; the diagnostic is the record of that.
            ctx.counters.rewindSeen += 1
            if let target = r["$rewindTo"].nonEmptyString, !ctx.seenMessageIDs.contains(target) {
                ctx.counters.rewindUnknownID += 1
            }
            return [base(.noise, id: lineID, ts: nil, source: source, ordinal: ordinal, ctx: ctx)]
        }

        if r.id.isString {
            var out = messageEvents(r, source: source, ordinal: ordinal, carrierID: lineID, ctx: ctx)
            if out.isEmpty {
                // Every line leaves a row, so a file of nothing but bookkeeping still shows
                // it was read.
                out.append(base(.noise, id: lineID, ts: ISO8601.seconds(r.timestamp.string), source: source, ordinal: ordinal, ctx: ctx))
            }
            return out
        }

        if r["$set"].isObject {
            // VERIFIED: `$set` merges metadata; `$set.messages` REPLACES the message list
            // (`updateMessagesFromHistory`). Each replaced message goes through the same
            // facet logic, so only genuinely new facets produce rows.
            let s = r["$set"]
            absorbDirectories(s.directories, into: ctx, count: true)
            let ts = ISO8601.seconds(s.lastUpdated.string)
            var out: [NormalizedEvent] = []
            if let msgs = s.messages.array {
                ctx.counters.setMessagesRebuild += 1
                for (i, m) in msgs.enumerated() where m.isObject && m.id.isString {
                    out.append(contentsOf: messageEvents(m, source: source, ordinal: ordinal, carrierID: "\(lineID)#m\(i)", ctx: ctx))
                }
            }
            if let summary = s.summary.nonEmptyString {
                // The title the harness wrote to disk. No leaf pointer exists in this
                // format; the deriver attaches an unanchored title to the session that
                // holds the record.
                var e = base(.title, id: lineID, ts: ts, source: source, ordinal: ordinal, ctx: ctx)
                e.title = summary
                out.append(e)
            } else {
                out.append(base(.noise, id: lineID, ts: ts, source: source, ordinal: ordinal, ctx: ctx))
            }
            return out
        }

        if r.sessionId.isString, r.projectHash.isString {
            // Line 0. Its startTime is the one clock reading that precedes every message,
            // so the metadata row carries it — as bookkeeping, which cannot open a session.
            // VERIFIED: `{sessionId, projectHash, startTime, lastUpdated, kind?, directories?}`.
            if let id = r.sessionId.nonEmptyString { ctx.ownSessionID = id }
            if let h = r.projectHash.nonEmptyString { ctx.projectHash = h }
            absorbDirectories(r.directories, into: ctx, count: true)
            let ts = ISO8601.seconds(r.startTime.string)
            var out: [NormalizedEvent] = []
            if let msgs = r.messages.array {
                // A whole legacy ConversationRecord written as one line (reference:
                // `legacy_record_inline`).
                ctx.counters.legacyRecordInline += 1
                for (i, m) in msgs.enumerated() where m.isObject && m.id.isString {
                    out.append(contentsOf: messageEvents(m, source: source, ordinal: ordinal, carrierID: "\(lineID)#m\(i)", ctx: ctx))
                }
            }
            if let summary = r.summary.nonEmptyString {
                var e = base(.title, id: lineID, ts: ts, source: source, ordinal: ordinal, ctx: ctx)
                e.title = summary
                out.append(e)
            } else {
                out.append(base(.noise, id: lineID, ts: ts, source: source, ordinal: ordinal, ctx: ctx))
            }
            return out
        }

        ctx.counters.unknownShapes += 1
        return [base(.unknown, id: lineID, ts: nil, source: source, ordinal: ordinal, ctx: ctx)]
    }

    private func base(
        _ kind: EventKind,
        id nativeID: String,
        ts: Double?,
        source: SourceRef,
        ordinal: Int,
        ctx: FileContext
    ) -> NormalizedEvent {
        NormalizedEvent(
            eventUID: Hashing.eventUID(
                harness: .geminiCLI, sourceID: source.sourceID, nativeEventID: nativeID),
            harness: .geminiCLI,
            sourceID: source.sourceID,
            ordinal: ordinal,
            nativeSessionID: ctx.sessionID,
            nativeEventID: nativeID,
            nativeParentID: nil,
            agentID: source.isSidecar ? ctx.ownSessionID : nil,
            isSidechain: source.isSidecar,
            ts: ts,
            cwd: ctx.cwd,
            harnessVersion: nil,  // the recording carries no CLI version
            kind: kind
        )
    }

    /// One `MessageRecord` (VERIFIED `{id, timestamp, type, content, displayContent?}` plus,
    /// for gemini, `{toolCalls?, thoughts?, tokens?, model?}`) → the facets not yet emitted.
    /// Returns an empty list when the line adds nothing; the caller decides whether a
    /// bookkeeping row is still owed.
    private func messageEvents(
        _ m: JSONNode,
        source: SourceRef,
        ordinal: Int,
        carrierID: String,
        ctx: FileContext
    ) -> [NormalizedEvent] {
        guard let mid = m.id.nonEmptyString else {
            ctx.counters.unknownShapes += 1
            return [base(.unknown, id: carrierID, ts: nil, source: source, ordinal: ordinal, ctx: ctx)]
        }
        if !ctx.seenMessageIDs.insert(mid).inserted { ctx.counters.messageRewrite += 1 }

        // VERIFIED: `newMessage` stamps every record with `new Date().toISOString()`. Absent
        // is a never-seen shape; counted and NEVER imputed. The event attaches to the
        // nearest preceding timestamped record downstream, and counts only.
        let ts = ISO8601.seconds(m.timestamp.string)
        if ts == nil { ctx.counters.messageNoTimestamp += 1 }

        let type = m.type.string ?? ""
        let parts = Self.parts(m.content)
        var out: [NormalizedEvent] = []

        switch type {

        case "user":
            if Self.hasFunctionResponse(parts) {
                // VERIFIED: tool responses are ALSO recorded as user messages made of
                // functionResponse parts (`recordSyntheticMessage('user', …)`). Never a
                // prompt. Their results are usually already carried by the toolCalls record
                // that completed them, so a result is emitted once per call, whichever
                // line names it first.
                ctx.counters.userToolResponseRecords += 1
                for (i, p) in parts.enumerated() {
                    let fr = p.functionResponse
                    guard fr.isObject else { continue }
                    let key = fr.id.nonEmptyString ?? "\(mid)#fr\(i)"
                    guard ctx.emittedResults.insert(key).inserted else {
                        ctx.counters.userToolResponseDeduped += 1
                        continue
                    }
                    var e = base(.toolResult, id: "tr:\(key)", ts: ts, source: source, ordinal: ordinal, ctx: ctx)
                    e.toolID = fr.id.nonEmptyString
                    let known = ctx.toolByCallKey[key]
                    e.toolName = known?.name ?? fr.name.nonEmptyString
                    e.targetPath = known?.path
                    // VERIFIED `createErrorResponse`: a failed tool's response is `{error: msg}`.
                    if fr.response.error.exists {
                        e.extra = ["is_error": "true"]
                        ctx.counters.resultError += 1
                    }
                    out.append(e)
                }
                if m.tokens.isObject { ctx.counters.tokensOnNonGemini += 1 }
                return out
            }

            // Presence only — the text is judged, never stored. `displayContent` holds what
            // the person typed when `content` is an `@file` expansion of it.
            var text = Self.text(parts, ctx: ctx)
            let display = Self.parts(m.displayContent)
            if !display.isEmpty {
                let d = Self.text(display, ctx: ctx)
                if !d.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    ctx.counters.promptFromDisplayContent += 1
                    text = d
                }
            }
            if Self.isIgnoredPrompt(text) {
                ctx.counters.promptIgnored += 1
                return out
            }
            if ctx.emittedPrompt.insert(mid).inserted {
                var e = base(.prompt, id: "\(mid)#prompt", ts: ts, source: source, ordinal: ordinal, ctx: ctx)
                e.role = "user"
                out.append(e)
            }
            if m.tokens.isObject { ctx.counters.tokensOnNonGemini += 1 }
            return out

        case "gemini":
            let model = m.model.nonEmptyString
            if let model { ctx.model = model }
            let effectiveModel = model ?? ctx.model

            // Non-thought text. A tool-call-only turn has only thought parts and
            // functionCall parts, so this is legitimately empty.
            let text = Self.text(parts, ctx: ctx)
            if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, ctx.emittedText.insert(mid).inserted {
                var e = base(.assistantMessage, id: "\(mid)#text", ts: ts, source: source, ordinal: ordinal, ctx: ctx)
                e.role = "assistant"
                e.model = effectiveModel
                out.append(e)
            }

            // VERIFIED: `toolCalls[]` is authoritative (`recordCompletedToolCalls`, written
            // at COMPLETION with the final status). A functionCall part in `content` is
            // only used when no record with the same id (or name + args) exists.
            var seenCallIDs = Set<String>()
            var seenSignatures = Set<String>()
            for (i, c) in (m.toolCalls.array ?? []).enumerated() {
                guard c.isObject else {
                    ctx.counters.toolCallNotObject += 1
                    continue
                }
                let name = c.name.nonEmptyString ?? "tool"
                let cid = c.id.nonEmptyString
                let key = cid ?? "\(mid)#tc\(i)"
                if let cid { seenCallIDs.insert(cid) }
                seenSignatures.insert(name + "|" + Self.canonical(c.args))

                // `timestamp` on a ToolCallRecord is the COMPLETION time (VERIFIED).
                var cts = ISO8601.seconds(c.timestamp.string)
                if cts == nil {
                    ctx.counters.toolCallNoTimestamp += 1
                    cts = ts
                }

                let status = c.status.string ?? ""
                if !Self.knownStatuses.contains(status) {
                    ctx.counters.toolStatusUnknown += 1
                } else if status == "cancelled" {
                    ctx.counters.toolStatusCancelled += 1
                } else if status != "success" && status != "error" {
                    ctx.counters.toolStatusIncomplete += 1
                }

                let path = Self.fileTools.contains(name) ? Self.filePath(c.args) : nil
                if ctx.emittedCalls.insert(key).inserted {
                    out.append(toolUse(name: name, callID: cid, key: key, args: c.args, path: path,
                                       ts: cts, model: effectiveModel, source: source, ordinal: ordinal, ctx: ctx))
                }

                // A result exists once the call completed — by status, or because a
                // `result` was written. Incomplete statuses yield the call alone.
                let completed = status == "success" || status == "error" || c.result.exists
                if completed, ctx.emittedResults.insert(key).inserted {
                    var e = base(.toolResult, id: "tr:\(key)", ts: cts, source: source, ordinal: ordinal, ctx: ctx)
                    e.toolName = name
                    e.toolID = cid
                    e.targetPath = path
                    if status == "error" || Self.resultHasError(c.result) {
                        e.extra = ["is_error": "true"]
                        ctx.counters.resultError += 1
                    }
                    out.append(e)
                }
            }

            for (i, p) in parts.enumerated() {
                let fc = p.functionCall
                guard fc.isObject else { continue }
                let name = fc.name.nonEmptyString ?? "tool"
                let fid = fc.id.nonEmptyString
                if let fid, seenCallIDs.contains(fid) {
                    ctx.counters.functionCallPartDeduped += 1
                    continue
                }
                if seenSignatures.contains(name + "|" + Self.canonical(fc.args)) {
                    ctx.counters.functionCallPartDeduped += 1
                    continue
                }
                let key = fid ?? "\(mid)#fc\(i)"
                guard ctx.emittedCalls.insert(key).inserted else { continue }
                ctx.counters.toolFromFunctionCallPart += 1
                let path = Self.fileTools.contains(name) ? Self.filePath(fc.args) : nil
                out.append(toolUse(name: name, callID: fid, key: key, args: fc.args, path: path,
                                   ts: ts, model: effectiveModel, source: source, ordinal: ordinal, ctx: ctx))
            }

            if m.tokens.isObject {
                out.append(tokenCarrier(m.tokens, messageID: mid, carrierID: carrierID, ts: ts,
                                        model: effectiveModel, source: source, ordinal: ordinal, ctx: ctx))
            }
            return out

        case "info":
            ctx.counters.messageTypeInfo += 1
        case "error":
            // UI / API errors, not tool failures (reference: counted, not emitted).
            ctx.counters.messageTypeError += 1
        case "warning":
            ctx.counters.messageTypeWarning += 1
        default:
            ctx.counters.messageTypeUnknown += 1
            return [base(.unknown, id: carrierID, ts: ts, source: source, ordinal: ordinal, ctx: ctx)]
        }
        if m.tokens.isObject { ctx.counters.tokensOnNonGemini += 1 }
        return out
    }

    /// A tool call. Only file tools yield a path; only `write_file` and `replace` yield
    /// line counts. Shell commands carry no file effect here, as in `CodexParser` — the
    /// reference's heredoc heuristic (`_bash_file_effect`) is a digest-level rule.
    private func toolUse(
        name: String,
        callID: String?,
        key: String,
        args: JSONNode,
        path: String?,
        ts: Double?,
        model: String?,
        source: SourceRef,
        ordinal: Int,
        ctx: FileContext
    ) -> NormalizedEvent {
        var e = base(.toolUse, id: "tc:\(key)", ts: ts, source: source, ordinal: ordinal, ctx: ctx)
        e.toolName = name
        e.toolID = callID
        e.role = "assistant"
        e.model = model
        e.targetPath = path
        if name == Self.writeFileTool {
            // ASSUMED (reference): lines written = newlines, plus one for an unterminated
            // last line — the same count `wc -l` would give plus the dangling line. Not yet
            // compared against a corpus.
            if let content = args.content.string {
                e.linesAdded = Self.writtenLines(content)
                e.linesRemoved = 0
            }
        } else if name == Self.editTool {
            // ASSUMED (reference): a real line diff of old_string → new_string, which is
            // what a structuredPatch would report. The reference uses difflib; this is an
            // LCS line diff. They agree on the fixture and can differ by a line or two on
            // an edit with many repeated lines.
            if let old = args.old_string.string, let new = args.new_string.string {
                let delta = Self.lineDelta(old: old, new: new)
                e.linesAdded = delta.added
                e.linesRemoved = delta.removed
            }
        }
        ctx.toolByCallKey[key] = (name: name, path: path)
        return e
    }

    /// The usage row for one gemini message line. VERIFIED `TokensSummary {input, output,
    /// cached, thoughts?, tool?, total}`, built from `promptTokenCount`,
    /// `candidatesTokenCount`, `cachedContentTokenCount`, `thoughtsTokenCount`,
    /// `toolUsePromptTokenCount`, `totalTokenCount`.
    ///
    /// Bucket mapping, VERIFIED by the fixture arithmetic against `analysis/gemini.py`'s
    /// `usage()`, which sums each key independently: on every carrier
    /// `total == input + output + thoughts` with `cached` NOT added — 12,000 + 30 + 80 =
    /// 12,110 with 8,000 of the 12,000 cached — so `cached` is a subset of `input` (Codex's
    /// convention, not Anthropic's disjoint cache-read) and the uncached remainder goes in
    /// `tokIn`. `thoughts` is NOT inside `output` — `candidatesTokenCount` excludes thinking
    /// and the total adds it separately — so it is folded into `tokOut`, which is also how
    /// the API bills it. `displayTotal` then equals Gemini's own `total`, and any carrier
    /// where it would not is counted in `gemini_token_total_mismatch`.
    private func tokenCarrier(
        _ t: JSONNode,
        messageID mid: String,
        carrierID: String,
        ts: Double?,
        model: String?,
        source: SourceRef,
        ordinal: Int,
        ctx: FileContext
    ) -> NormalizedEvent {
        var e = base(.noise, id: carrierID, ts: ts, source: source, ordinal: ordinal, ctx: ctx)
        let input = t.input.int ?? 0
        let output = t.output.int ?? 0
        let cached = t.cached.int ?? 0
        let thoughts = t.thoughts.int ?? 0
        let tool = t.tool.int ?? 0
        if cached > input { ctx.counters.cachedExceedsInput += 1 }
        if tool > 0 { ctx.counters.toolTokensSeen += 1 }
        if let total = t.total.int, total != input + output + thoughts + tool {
            ctx.counters.tokenTotalMismatch += 1
        }
        // ASSUMED: `tool` (toolUsePromptTokenCount) is prompt-side and disjoint from
        // `input`; zero on every fixture carrier.
        e.tokIn = max(0, input - cached) + tool
        e.tokCacheRead = cached
        e.tokOut = output + thoughts
        e.model = model
        e.dedupeKey = "\(source.sourceID)|\(mid)"
        if ctx.claimedTokenIDs.insert(mid).inserted {
            e.usageAuthoritative = true
        } else {
            // THE RE-APPEND TRAP: the same id, written again with the same tokens. The
            // numbers stay on the row (so the naive sum is reproducible) but the claim does
            // not.
            ctx.counters.tokenReappend += 1
            e.usageAuthoritative = false
        }
        return e
    }

    // MARK: - Helpers

    /// `PartListUnion` → parts. A bare string is one text part; a lone Part object is a
    /// one-element list; anything that is not an object inside a list becomes a text part
    /// holding its description, as the reference does.
    static func parts(_ content: JSONNode) -> [JSONNode] {
        if let s = content.string { return s.isEmpty ? [] : [JSONNode(["text": s] as [String: Any])] }
        if content.isObject { return [content] }
        guard let arr = content.array else { return [] }
        return arr.map { p -> JSONNode in
            if p.isObject { return p }
            let described = p.raw.map { String(describing: $0) } ?? ""
            return JSONNode(["text": described] as [String: Any])
        }
    }

    /// Non-thought text parts joined with newlines. Parts with no recognised key are
    /// counted, never raised on.
    private static func text(_ parts: [JSONNode], ctx: FileContext) -> String {
        var out: [String] = []
        for p in parts {
            let keys = Set((p.object ?? [:]).keys).subtracting(partMetaKeys)
            if keys.isDisjoint(with: knownPartKeys) {
                ctx.counters.unknownPartShape += 1
                continue
            }
            if let t = p.text.string, !(p.thought.bool ?? false) { out.append(t) }
        }
        return out.joined(separator: "\n")
    }

    static func hasFunctionResponse(_ parts: [JSONNode]) -> Bool {
        parts.contains { $0.functionResponse.isObject }
    }

    static func isIgnoredPrompt(_ text: String) -> Bool {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return true }
        return ignoredPromptPrefixes.contains { t.hasPrefix($0) }
    }

    static func filePath(_ args: JSONNode) -> String? {
        for k in pathArgKeys {
            if let v = args[k].nonEmptyString { return v }
        }
        return nil
    }

    /// True when any functionResponse part of a ToolCallRecord's `result` carries
    /// `response.error` (VERIFIED `createErrorResponse`).
    static func resultHasError(_ result: JSONNode) -> Bool {
        parts(result).contains { $0.functionResponse.response.error.exists }
    }

    /// Sorted-key JSON of a tool's args, for the name + args signature that pairs a
    /// functionCall part with its ToolCallRecord when ids are absent.
    static func canonical(_ args: JSONNode) -> String {
        guard let obj = args.raw, JSONSerialization.isValidJSONObject(obj),
              let d = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])
        else { return "" }
        return String(decoding: d, as: UTF8.self)
    }

    /// Lines in a written file: newlines, plus one for an unterminated last line.
    public static func writtenLines(_ content: String) -> Int {
        guard !content.isEmpty else { return 0 }
        let newlines = content.utf8.reduce(0) { $0 + ($1 == 0x0A ? 1 : 0) }
        return newlines + (content.hasSuffix("\n") ? 0 : 1)
    }

    /// Python's `str.splitlines()` for `\n`-terminated text: no trailing empty element.
    static func splitLines(_ s: String) -> [Substring] {
        guard !s.isEmpty else { return [] }
        var lines = s.split(separator: "\n", omittingEmptySubsequences: false)
        if s.hasSuffix("\n") { lines.removeLast() }
        return lines
    }

    /// (+ lines, - lines) of a minimal line diff: line counts minus the longest common
    /// subsequence. Above `maxDiffCells` the diff is not attempted and every line counts.
    public static func lineDelta(old: String, new: String) -> (added: Int, removed: Int) {
        let a = splitLines(old)
        let b = splitLines(new)
        if a.isEmpty || b.isEmpty { return (b.count, a.count) }
        if a.count * b.count > maxDiffCells { return (b.count, a.count) }

        // Two-row DP over the shorter dimension.
        var prev = [Int](repeating: 0, count: b.count + 1)
        var cur = [Int](repeating: 0, count: b.count + 1)
        for i in 1...a.count {
            for j in 1...b.count {
                if a[i - 1] == b[j - 1] {
                    cur[j] = prev[j - 1] + 1
                } else {
                    cur[j] = Swift.max(prev[j], cur[j - 1])
                }
            }
            swap(&prev, &cur)
        }
        let lcs = prev[b.count]
        return (b.count - lcs, a.count - lcs)
    }
}
