import BuilderModel
import Foundation

/// Reads `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`.
///
/// Every line is one `RolloutLine`: `{"timestamp": ..., "ordinal"?: n, "type": <tag>,
/// "payload": {...}}`. The reference implementation is `analysis/codex.py`; every rule
/// below carries the VERIFIED / ASSUMED label it has there. VERIFIED means read from the
/// Codex source on 2026-09-05 (`codex-rs/...` on `main`); ASSUMED means an older on-disk
/// form the current source no longer writes, or a field we have only seen described.
///
/// This parser has NOT yet been measured against a real corpus — only against the
/// synthetic fixture under `spec/fixtures/codex/`. The house rule applies: a parser
/// written from a description ships with diagnostics first, and the first real corpus
/// decides what this file got wrong before any number reaches a card. Every lenient
/// branch therefore increments a counter that surfaces as a `ParseDiagnostic`.
///
/// Three shape facts that drive the design:
///
/// - **There is no per-record context.** `cwd`, the session id and the CLI version are
///   written ONCE, on the `session_meta` line; the model is on `turn_context` lines and
///   applies to everything that follows. Claude Code stamps these on every record, so its
///   parser can resume mid-file for free. This one cannot: a resumed read that starts past
///   line 0 would emit every event with a nil cwd and a nil session id, the `.nativeSession`
///   pool would collapse every Codex file on the machine into one key, and repo pooling
///   would lose the directory entirely — silently, on every daemon tick while a session is
///   live. So a resume first replays the context-bearing lines before the watermark
///   (`priorContext`), without emitting events for them.
///
/// - **There is no DAG.** Lines carry no uuid and no parent pointer, so `nativeParentID`
///   is nil everywhere and `LivePathResolver` treats every event as live (its
///   `linked == 0` branch). A Codex rewind, if one exists on disk, is not visible here.
///
/// - **Tokens are per-turn deltas alongside a cumulative total.** `event_msg/token_count`
///   carries `info.last_token_usage` (this turn) and `info.total_token_usage` (the thread
///   so far). The delta is what is stored, one authoritative row per carrier, so
///   `TokenAccountant` sums it exactly once; the cumulative total is used only to detect a
///   carrier that repeats unchanged (see `token_count`).
public struct CodexParser: HarnessParser {

    public let harness: Harness = .codex

    /// Bump when the interpretation of the same bytes changes. Sources whose stored
    /// watermark carries an older version are deleted and re-read from zero.
    public let parserVersion = 1

    private let sessionsRoot: String

    /// Not stored: FileManager is not Sendable, and this struct is.
    private var fm: FileManager { .default }

    public init(sessionsRoot: String? = nil) {
        self.sessionsRoot =
            sessionsRoot
            ?? (NSHomeDirectory() as NSString).appendingPathComponent(".codex/sessions")
    }

    // MARK: - Constants carried over from analysis/codex.py

    /// Two texts are "the same message" when they match exactly and land within this
    /// window. A `response_item` assistant message and its `agent_message` event are
    /// written by the same turn a few milliseconds apart (1 ms on every pair in the
    /// fixture); 2 s is generous and still far below any inter-turn gap.
    static let dedupeWindowSec: Double = 2.0

    /// The duplicate is always the very next text-bearing record, so a short ring is
    /// enough. Bounded so a long autonomous run cannot grow it without limit.
    static let dedupeRingCapacity = 16

    /// VERIFIED: `USER_MESSAGE_BEGIN` in protocol/src/protocol.rs; `strip_user_message_prefix`
    /// removes everything up to and including it before a user message is previewed.
    static let userMessageBegin = "## My request for Codex:"

    /// VERIFIED constants from protocol/src/protocol.rs. A user message that BEGINS with one
    /// of these is harness-injected context, not a person typing.
    static let envelopeTags = ["<user_instructions>", "<environment_context>", "<turn_context>"]

    static let shellTools: Set<String> = [
        "shell", "exec_command", "local_shell", "shell_command", "container.exec",
    ]
    static let applyPatch = "apply_patch"

    /// VERIFIED: history/src/rollout_payload.rs `RolloutItemWire` variants, snake_case.
    static let knownTypes: Set<String> = [
        "session_meta", "response_item", "inter_agent_communication",
        "inter_agent_communication_metadata", "compacted", "turn_context",
        "token_usage_record", "world_state", "retained_context", "security_risk_score",
        "event_msg", "realtime_item",
    ]

    /// VERIFIED: protocol/src/protocol.rs `EventMsg` variants, snake_case (84 names,
    /// generated from the source on 2026-09-05; `task_started`/`task_complete` are the wire
    /// names, the `turn_*` forms are their serde aliases and are kept so v2 files are not
    /// "unknown"). Only used to COUNT drift in diagnostics — an unrecognised event_msg is
    /// still `.noise`, because none of the counted kinds can hide behind a new name.
    static let knownEventTypes: Set<String> = [
        "agent_message", "agent_message_content_delta", "agent_reasoning",
        "agent_reasoning_raw_content", "agent_reasoning_section_break",
        "apply_patch_approval_request", "auth_recovery_completed", "auth_recovery_started",
        "collab_agent_interaction_begin", "collab_agent_interaction_end",
        "collab_agent_spawn_begin", "collab_agent_spawn_end", "collab_close_begin",
        "collab_close_end", "collab_resume_begin", "collab_resume_end",
        "collab_waiting_begin", "collab_waiting_end", "context_compacted",
        "deprecation_notice", "dynamic_tool_call_request", "dynamic_tool_call_response",
        "elicitation_request", "entered_review_mode", "environment_connected",
        "environment_disconnected", "error", "exec_approval_request", "exec_command_begin",
        "exec_command_end", "exec_command_output_delta", "exited_review_mode",
        "guardian_assessment", "guardian_warning", "hook_completed", "hook_started",
        "image_generation_begin", "image_generation_end", "item_completed", "item_started",
        "mcp_startup_complete", "mcp_startup_update", "mcp_tool_call_begin",
        "mcp_tool_call_end", "model_reroute", "model_verification", "patch_apply_begin",
        "patch_apply_end", "patch_apply_updated", "plan_delta", "plan_update",
        "raw_response_completed", "raw_response_item", "realtime_conversation_closed",
        "realtime_conversation_list_voices_response", "realtime_conversation_realtime",
        "realtime_conversation_sdp", "realtime_conversation_started",
        "reasoning_content_delta", "reasoning_raw_content_delta", "request_permissions",
        "request_user_input", "safety_buffering", "session_configured", "stream_error",
        "sub_agent_activity", "task_complete", "task_started", "terminal_interaction",
        "thread_goal_updated", "thread_queue_changed", "thread_rolled_back",
        "thread_settings_applied", "token_count", "turn_aborted", "turn_complete",
        "turn_diff", "turn_moderation_metadata", "turn_started", "user_message",
        "view_image_tool_call", "warning", "web_search_begin", "web_search_end",
    ]

    /// VERIFIED: protocol/src/models.rs `ResponseItem` variants, snake_case.
    static let knownResponseTypes: Set<String> = [
        "additional_tools", "message", "agent_message", "reasoning", "local_shell_call",
        "function_call", "tool_search_call", "function_call_output", "custom_tool_call",
        "custom_tool_call_output", "tool_search_output", "web_search_call",
        "image_generation_call", "compaction", "configuration_update", "compaction_trigger",
        "context_compaction", "other",
    ]

    // MARK: - Discovery

    public func discover() throws -> [SourceRef] {
        var out: [SourceRef] = []
        // Must not throw for a missing root — most users do not have Codex installed.
        guard let entries = fm.enumerator(atPath: sessionsRoot) else { return [] }

        for case let rel as String in entries {
            guard Self.isRollout(relativePath: rel) else { continue }
            let full = (sessionsRoot as NSString).appendingPathComponent(rel)
            out.append(
                SourceRef(
                    // The relative path, not the absolute one: the file is the same source
                    // if the home directory moves, and two rollouts never share a relative
                    // path because the uuid is in the filename.
                    sourceID: Hashing.sourceID(harness: .codex, descriptor: rel),
                    harness: .codex,
                    kind: .jsonl,
                    path: full,
                    isSidecar: false
                )
            )
        }
        return out
    }

    /// A rollout is EXACTLY `YYYY/MM/DD/rollout-*.jsonl` — four components, three of them
    /// all-digit. An ALLOWLIST on shape, like `ClaudeCodeParser.isRootTranscript`, so a
    /// future sibling directory or a stray file at the root is skipped rather than read as
    /// a session.
    public static func isRollout(relativePath rel: String) -> Bool {
        let parts = rel.split(separator: "/", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return false }
        func digits(_ s: Substring, _ n: Int) -> Bool {
            s.utf8.count == n && s.utf8.allSatisfy { $0 >= 0x30 && $0 <= 0x39 }
        }
        guard digits(parts[0], 4), digits(parts[1], 2), digits(parts[2], 2) else { return false }
        let name = parts[3]
        return name.hasPrefix("rollout-") && name.hasSuffix(".jsonl")
    }

    // MARK: - Per-file state

    /// Everything one pass over a rollout has to remember between lines.
    ///
    /// A reference type on purpose: the per-line handler builds events through a nested
    /// function that reads this state while helpers mutate it, and an `inout` struct in
    /// that position trips Swift's exclusivity checking.
    private final class FileContext {
        // From session_meta (line 0) and turn_context; stamped on every event.
        var cwd: String?
        var sessionID: String?
        var cliVersion: String?
        var model: String?
        var effort: String?

        /// `info.total_token_usage.total_tokens` of the last token_count carrier seen.
        var lastCumulativeTotal: Int?

        /// call_id -> (tool name, first touched path), so an output can name its tool.
        var toolByCallID: [String: (name: String, path: String?)] = [:]

        /// (timestamp, sha256 of text) of recently emitted prompts / assistant messages.
        var recentPrompts: [(ts: Double, hash: String)] = []
        var recentAssistant: [(ts: Double, hash: String)] = []

        struct Counters {
            var unknownShapes = 0
            var unknownEventTypes = 0
            var payloadNotObject = 0
            var outputWithoutCall = 0
            var tokenCountNoUsage = 0
            var tokenCountRepeated = 0
            var cachedExceedsInput = 0
            var promptEmpty = 0
            var promptEnvelope = 0
            var promptDeduped = 0
            var assistantDeduped = 0
        }
        var counters = Counters()

        /// True when `text` was already emitted within `dedupeWindowSec` of `ts`; otherwise
        /// records it and returns false. Order-independent on purpose: whichever of the
        /// `event_msg` and the `response_item` copy is written first wins, and the other
        /// is the duplicate. (That is what the reference's shared `emitted_*` index does.)
        static func isRecent(_ ring: inout [(ts: Double, hash: String)], text: String, ts: Double?) -> Bool {
            guard let ts else { return false }  // no timestamp, no window to judge by
            let h = Hashing.sha256Hex(text)
            if ring.contains(where: { $0.hash == h && abs($0.ts - ts) <= CodexParser.dedupeWindowSec }) {
                return true
            }
            ring.append((ts: ts, hash: h))
            if ring.count > CodexParser.dedupeRingCapacity {
                ring.removeFirst(ring.count - CodexParser.dedupeRingCapacity)
            }
            return false
        }

        func promptIsRecent(_ text: String, ts: Double?) -> Bool {
            FileContext.isRecent(&recentPrompts, text: text, ts: ts)
        }

        func assistantIsRecent(_ text: String, ts: Double?) -> Bool {
            FileContext.isRecent(&recentAssistant, text: text, ts: ts)
        }
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

        let ctx = FileContext()
        if startOffset > 0 {
            // Replay cwd / session id / model / cumulative-token state from the lines
            // before the watermark. Without this every resumed event carries nil context.
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
            guard let node = JSONLine.parse(line.data) else {
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
        note("codex_unknown_event_msg_type", c.unknownEventTypes, "event_msg record(s) with an unlisted payload type")
        note("codex_payload_not_object", c.payloadNotObject, "record(s) whose payload is not an object")
        note("codex_output_without_call", c.outputWithoutCall, "tool output(s) whose call_id was never seen")
        note("codex_token_count_no_usage", c.tokenCountNoUsage, "token_count event(s) without last_token_usage")
        note("codex_token_count_repeated", c.tokenCountRepeated, "token_count event(s) repeating an unchanged cumulative total")
        note("codex_cached_exceeds_input", c.cachedExceedsInput, "token_count event(s) with cached_input_tokens > input_tokens")
        note("codex_prompt_empty", c.promptEmpty, "user message(s) empty after prefix strip")
        note("codex_prompt_envelope_skipped", c.promptEnvelope, "user message(s) that were harness envelopes")
        note("codex_prompt_deduped", c.promptDeduped, "duplicate prompt text(s) within \(Int(Self.dedupeWindowSec)) s")
        note("codex_assistant_deduped", c.assistantDeduped, "duplicate assistant text(s) within \(Int(Self.dedupeWindowSec)) s")

        var wm = watermark
        wm.byteOffset = reader.endOffset
        // From the reader, NOT from events.count: several lines yield no event.
        wm.lineCount = reader.nextLineIndex
        wm.sizeBytes = size
        wm.mtime = mtime
        wm.stIno = ino
        wm.stDev = dev
        wm.headSHA256 = headSHA
        wm.parserVersion = parserVersion

        return ParseResult(events: events, watermark: wm, diagnostics: diagnostics, fidelity: .full)
    }

    /// Recover the file-level context from the lines before `offset`, emitting nothing.
    ///
    /// Only lines that can carry context are JSON-parsed (a cheap byte scan for the quoted
    /// type name first), so this is a sequential read of the prefix, not a second full
    /// parse. It stops at the first complete line that begins at or past the watermark,
    /// which always sits on a line boundary because `LineReader` never commits mid-line.
    private func priorContext(path: String, before offset: Int, into ctx: FileContext) throws {
        let reader = try LineReader(path: path, startOffset: 0, startLineIndex: 0)
        defer { reader.close() }
        let markers = [
            Data("\"session_meta\"".utf8),
            Data("\"turn_context\"".utf8),
            Data("\"token_count\"".utf8),
        ]
        while let line = try reader.next() {
            if line.offset >= offset { break }
            guard markers.contains(where: { line.data.range(of: $0) != nil }) else { continue }
            guard let node = JSONLine.parse(line.data) else { continue }
            absorbContext(node, into: ctx)
        }
    }

    /// The context-bearing record shapes, in one place so the live pass and the resume
    /// replay cannot disagree about them.
    private func absorbContext(_ r: JSONNode, into ctx: FileContext) {
        let p = r.payload
        switch r.type.string ?? "" {
        case "session_meta":
            // VERIFIED: `SessionMeta` {id, timestamp, cwd, originator, cli_version, source,
            // model_provider?, history_mode, …} + `git?`. There is NO model field here; the
            // model is on `turn_context`.
            if let c = p.cwd.nonEmptyString { ctx.cwd = c }
            if let id = p.id.nonEmptyString { ctx.sessionID = id }
            if let v = p.cli_version.nonEmptyString { ctx.cliVersion = v }
        case "turn_context":
            // VERIFIED: `TurnContextItem` {cwd, approval_policy, sandbox_policy, model,
            // effort?, summary, turn_id?}. The model applies to everything that follows.
            if let m = p.model.nonEmptyString { ctx.model = m }
            ctx.effort = p.effort.nonEmptyString
            if ctx.cwd == nil, let c = p.cwd.nonEmptyString { ctx.cwd = c }
        case "event_msg":
            if p.type.string == "token_count", let t = p.info.total_token_usage.total_tokens.int {
                ctx.lastCumulativeTotal = t
            }
        default:
            break
        }
    }

    // MARK: - Record -> events

    private func events(
        from r: JSONNode,
        source: SourceRef,
        ordinal: Int,
        ctx: FileContext
    ) -> [NormalizedEvent] {

        let type = r.type.string ?? ""

        // VERIFIED: `[year]-[month]-[day]T[hour]:[minute]:[second].[subsecond digits:3]Z`,
        // always UTC — rollout/src/recorder.rs. `ISO8601.seconds` also accepts the form
        // without a fraction. Never imputed.
        let ts = ISO8601.seconds(r.timestamp.string)

        // Context lines update the file state BEFORE their own event is built, so even
        // the session_meta record itself carries the cwd it declares.
        if type == "session_meta" || type == "turn_context" {
            if r.payload.isObject {
                absorbContext(r, into: ctx)
            } else {
                ctx.counters.payloadNotObject += 1
            }
        }

        func base(_ kind: EventKind) -> NormalizedEvent {
            // Codex lines have no uuid, so the line index is the identity — the same
            // fallback `ClaudeCodeParser` uses. One event per line, so no block suffix.
            let nativeID = "l\(ordinal)"
            return NormalizedEvent(
                eventUID: Hashing.eventUID(
                    harness: .codex, sourceID: source.sourceID, nativeEventID: nativeID),
                harness: .codex,
                sourceID: source.sourceID,
                ordinal: ordinal,
                nativeSessionID: ctx.sessionID,
                nativeEventID: nativeID,
                nativeParentID: nil,
                isSidechain: false,
                ts: ts,
                cwd: ctx.cwd,
                harnessVersion: ctx.cliVersion,
                kind: kind
            )
        }

        /// A human prompt, or `.noise` when the text is empty, a harness envelope, or a
        /// duplicate of one already emitted. Presence only — the text is never stored.
        func prompt(from raw: String?) -> NormalizedEvent {
            guard let text = Self.promptText(raw) else {
                ctx.counters.promptEmpty += 1
                return base(.noise)
            }
            if Self.isEnvelope(text) {
                ctx.counters.promptEnvelope += 1
                return base(.noise)
            }
            if ctx.promptIsRecent(text, ts: ts) {
                ctx.counters.promptDeduped += 1
                return base(.noise)
            }
            var e = base(.prompt)
            e.role = "user"
            return e
        }

        /// An assistant message, or `.noise` when empty or a duplicate within the window.
        func assistant(from raw: String) -> NormalizedEvent {
            let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { return base(.noise) }
            if ctx.assistantIsRecent(text, ts: ts) {
                ctx.counters.assistantDeduped += 1
                return base(.noise)
            }
            var e = base(.assistantMessage)
            e.role = "assistant"
            e.model = ctx.model
            e.effort = ctx.effort
            return e
        }

        /// A tool call. `body` is the command text for shell tools or the patch text for
        /// apply_patch; only a patch yields a path and line counts.
        func toolUse(name: String, callID: String?, body: String) -> NormalizedEvent {
            var e = base(.toolUse)
            e.toolName = name
            e.toolID = callID
            e.role = "assistant"
            e.model = ctx.model
            e.effort = ctx.effort
            // VERIFIED that the apply-patch crate accepts `apply_patch <<'EOF' … EOF` run
            // through the shell (`maybe_parse_apply_patch`), so a shell body holding a
            // patch is scored like the custom tool.
            let isPatch =
                name == Self.applyPatch
                || (Self.shellTools.contains(name) && body.contains("*** Begin Patch"))
            if isPatch {
                let effect = Self.patchEffect(body)
                e.targetPath = effect.path
                e.linesAdded = effect.added
                e.linesRemoved = effect.removed
            }
            if let callID { ctx.toolByCallID[callID] = (name: name, path: e.targetPath) }
            return e
        }

        switch type {

        case "session_meta", "turn_context":
            return [base(.noise)]

        case "compacted":
            // A context compaction: a marker, never a session boundary.
            return [base(.compaction)]

        case "event_msg":
            let p = r.payload
            guard p.isObject else {
                ctx.counters.payloadNotObject += 1
                return [base(.noise)]
            }
            let pt = p.type.string ?? ""
            if !Self.knownEventTypes.contains(pt) { ctx.counters.unknownEventTypes += 1 }

            switch pt {
            case "user_message":
                // THE prompt source (VERIFIED `UserMessageEvent {message, images?, …}`).
                // `response_item` user messages are NOT used: that channel also carries the
                // `<environment_context>` / `<user_instructions>` envelopes and per-turn
                // context injections, indistinguishable by role alone. The event_msg is
                // written only for what the person actually sent.
                return [prompt(from: p.message.string)]

            case "agent_message":
                // VERIFIED `AgentMessageEvent {message, phase?}`.
                return [assistant(from: p.message.string ?? "")]

            case "turn_aborted":
                // VERIFIED `TurnAbortReason`: interrupted | replaced | review_ended |
                // budget_limited. Only `interrupted` is a human at the keyboard.
                return [base(p.reason.string == "interrupted" ? .interrupt : .noise)]

            case "item_completed":
                // VERIFIED: paginated rollouts (`history_mode == "paginated"`) persist
                // `item_completed` TurnItems INSTEAD of user_message / agent_message
                // (rollout/src/policy.rs). Tags are PascalCase because `TurnItem` has no
                // rename_all. A parser reading only the legacy events counts zero prompts
                // on such a file and files the whole sitting as unattended.
                let item = p.item
                guard item.isObject else { return [base(.noise)] }
                switch item.type.string ?? "" {
                case "UserMessage", "user_message":
                    return [prompt(from: Self.contentText(item.content, kinds: ["text", "input_text"]))]
                case "AgentMessage", "agent_message":
                    return [assistant(from: Self.contentText(item.content, kinds: ["Text", "text", "output_text"]))]
                default:
                    return [base(.noise)]
                }

            case "token_count":
                // VERIFIED `TokenCountEvent {info: Option<TokenUsageInfo>, rate_limits}`,
                // `TokenUsageInfo {total_token_usage, last_token_usage, model_context_window}`,
                // `TokenUsage {input_tokens, cached_input_tokens, cache_write_input_tokens,
                // output_tokens, reasoning_output_tokens, total_tokens}`.
                //
                // Not an event in the reference (it is usage, not activity), but the store
                // needs the numbers: a `.noise` carrier with the per-turn delta, flagged
                // authoritative under a key unique to this line, so the partial unique
                // index and `TokenAccountant` see exactly one row per turn.
                var e = base(.noise)
                let info = p.info
                let last = info.last_token_usage
                guard info.isObject, last.isObject else {
                    ctx.counters.tokenCountNoUsage += 1
                    return [e]
                }

                // ASSUMED (reference docstring): token_count events can repeat an
                // unchanged `info` on rate-limit refreshes. A refresh cannot advance the
                // cumulative total, and a real turn cannot fail to, so a carrier whose
                // `total_token_usage.total_tokens` equals the previous carrier's is a
                // repeat and its delta is not summed a second time. Counted, so the first
                // real corpus shows whether this branch ever fires.
                let cumulative = info.total_token_usage.total_tokens.int
                if let cumulative, let prev = ctx.lastCumulativeTotal, cumulative == prev {
                    ctx.counters.tokenCountRepeated += 1
                    return [e]
                }
                if let cumulative { ctx.lastCumulativeTotal = cumulative }

                // VERIFIED by the fixture arithmetic and OpenAI's usage semantics:
                // `cached_input_tokens` is a SUBSET of `input_tokens` (12,000 in + 900 out
                // = 12,900 total, with 8,000 of the 12,000 cached), whereas Anthropic's
                // cache-read is disjoint from input and `TokenBuckets.displayTotal` adds
                // `input + cacheRead`. Storing the raw input alongside the cached figure
                // would therefore report 20,900 for a turn Codex itself calls 12,900. The
                // uncached remainder goes in `tokIn`, so displayTotal == Codex's total.
                let input = last.input_tokens.int ?? 0
                let cached = last.cached_input_tokens.int ?? 0
                if cached > input { ctx.counters.cachedExceedsInput += 1 }
                e.tokIn = max(0, input - cached)
                e.tokCacheRead = cached
                // `reasoning_output_tokens` is inside `output_tokens` (900 out, 300
                // reasoning, total unchanged) and has no bucket; not stored separately.
                e.tokOut = last.output_tokens.int ?? 0
                // ASSUMED disjoint from input_tokens; zero on every fixture carrier.
                e.tokCacheW5m = last.cache_write_input_tokens.int
                e.model = ctx.model
                e.dedupeKey = "\(source.sourceID)|tc\(ordinal)"
                e.usageAuthoritative = true
                return [e]

            default:
                // task_started / task_complete, reasoning deltas, approvals, exec begin/end
                // (never persisted anyway), …: bookkeeping.
                return [base(.noise)]
            }

        case "response_item":
            let p = r.payload
            guard p.isObject else {
                ctx.counters.payloadNotObject += 1
                return [base(.noise)]
            }
            let pt = p.type.string ?? ""

            switch pt {
            case "function_call":
                // VERIFIED `{name, arguments: String (JSON text), call_id}`. The `shell`
                // tool's arguments have `command: [String]`; `exec_command`'s have
                // `cmd: String`.
                let name = p.name.nonEmptyString ?? "tool"
                let args = Self.parseArguments(p.arguments)
                let body: String
                if name == Self.applyPatch {
                    // ASSUMED older form: apply_patch as a function_call whose arguments
                    // JSON has an `input` (or `patch`) key holding the patch. Unverified.
                    body = args.input.string ?? args.patch.string ?? ""
                } else if Self.shellTools.contains(name) {
                    body = Self.commandText(args)
                } else {
                    body = ""
                }
                return [toolUse(name: name, callID: p.call_id.string, body: body)]

            case "local_shell_call":
                // VERIFIED `{call_id?, status, action: {type: "exec", command: [String], …}}`.
                let cid = p.call_id.string ?? p.id.string
                return [toolUse(name: "shell", callID: cid, body: Self.commandText(p.action))]

            case "custom_tool_call":
                // VERIFIED: apply_patch is a freeform tool, so it arrives as
                // `custom_tool_call {call_id, name: "apply_patch", input: String}` with the
                // patch text in `input`.
                let name = p.name.nonEmptyString ?? "tool"
                return [toolUse(name: name, callID: p.call_id.string, body: p.input.string ?? "")]

            case "function_call_output", "custom_tool_call_output":
                // VERIFIED `{call_id?, name?, output}`; `output` is a plain string or a list
                // of `{type: "input_text", text}`. Success/failure is a digest-level
                // judgement on the text (exit-code header, "apply_patch verification
                // failed:") and is not made here — the event carries no error flag.
                var e = base(.toolResult)
                let cid = p.call_id.string
                e.toolID = cid
                if let cid, let call = ctx.toolByCallID[cid] {
                    e.toolName = call.name
                    e.targetPath = call.path
                } else {
                    ctx.counters.outputWithoutCall += 1
                }
                return [e]

            case "message":
                // VERIFIED `{role, content: [ContentItem]}`, ContentItem tags input_text |
                // input_image | input_audio | output_text.
                switch p.role.string ?? "" {
                case "assistant":
                    // Used only when no `agent_message` event carried the same text: the
                    // legacy history mode writes both, milliseconds apart.
                    return [assistant(from: Self.contentText(p.content, kinds: ["output_text"]))]
                default:
                    // user: envelopes and context injections share the channel with real
                    // prompts, so the role alone proves nothing. developer / system:
                    // instructions. None is a presence signal.
                    return [base(.noise)]
                }

            default:
                // reasoning, web_search_call, compaction items, …: never read.
                if Self.knownResponseTypes.contains(pt) { return [base(.noise)] }
                ctx.counters.unknownShapes += 1
                return [base(.unknown)]
            }

        case "inter_agent_communication", "inter_agent_communication_metadata",
             "token_usage_record", "world_state", "retained_context", "security_risk_score",
             "realtime_item":
            // Known top-level shapes with no product meaning here. `token_usage_record`
            // is deliberately NOT summed: the reference reports it beside token_count so
            // the first real corpus can say which of the two is right; summing both here
            // would double every figure before that measurement exists.
            return [base(.noise)]

        default:
            ctx.counters.unknownShapes += 1
            return [base(.unknown)]
        }
    }

    // MARK: - Helpers

    /// VERIFIED: protocol.rs `strip_user_message_prefix`. Everything up to and including
    /// `USER_MESSAGE_BEGIN` is removed, then whitespace is trimmed. `nil` when nothing is
    /// left — a record with no text is not a prompt.
    static func promptText(_ raw: String?) -> String? {
        guard let raw else { return nil }
        var text = raw
        if let range = text.range(of: userMessageBegin) {
            text = String(text[range.upperBound...])
        }
        text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? nil : text
    }

    /// `text` must already be trimmed (the reference `lstrip`s before testing).
    static func isEnvelope(_ text: String) -> Bool {
        envelopeTags.contains { text.hasPrefix($0) }
    }

    /// Join the text of ContentItem / UserInput / AgentMessageContent lists. A bare
    /// string is returned as-is.
    static func contentText(_ content: JSONNode, kinds: Set<String>) -> String {
        if let s = content.string { return s }
        guard let arr = content.array else { return "" }
        var parts: [String] = []
        for b in arr where kinds.contains(b.type.string ?? "") {
            if let t = b.text.string { parts.append(t) }
        }
        return parts.joined(separator: "\n")
    }

    /// `arguments` is a JSON STRING on the wire (VERIFIED); an object is accepted too.
    /// Anything else yields an empty node, so every lookup on it is `nil`.
    static func parseArguments(_ node: JSONNode) -> JSONNode {
        if node.isObject { return node }
        guard let s = node.string,
              let parsed = JSONLine.parse(Data(s.utf8)),
              parsed.isObject
        else { return JSONNode(nil) }
        return parsed
    }

    /// `command: [String]` (shell, local_shell_call action) or `cmd: String` (exec_command).
    static func commandText(_ args: JSONNode) -> String {
        let cmd = args.command.exists ? args.command : args.cmd
        if let arr = cmd.array { return arr.compactMap(\.string).joined(separator: " ") }
        return cmd.string ?? ""
    }

    /// (first touched path, '+' lines, '-' lines) for an apply_patch body.
    ///
    /// The path is the first `*** Update File:` / `*** Add File:` header, falling back to
    /// a `*** Delete File:`. Header lines (`*** …`, `@@ …`) are excluded from the counts;
    /// the Codex patch grammar has no `+++`/`---` file headers, so every remaining +/- line
    /// is a content line. Same rule as the reference's `_patch_effect`, and the fixture's
    /// first patch scores (`scripts/deploy.sh`, +3, -1) under both.
    static func patchEffect(_ patch: String) -> (path: String?, added: Int, removed: Int) {
        var path: String?
        var deletedPath: String?
        var added = 0
        var removed = 0

        func header(_ line: Substring, _ marker: String) -> String? {
            guard line.hasPrefix(marker) else { return nil }
            let p = line.dropFirst(marker.count).trimmingCharacters(in: .whitespacesAndNewlines)
            return p.isEmpty ? nil : p
        }

        for line in patch.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.hasPrefix("***") {
                if path == nil {
                    path = header(line, "*** Update File:") ?? header(line, "*** Add File:")
                }
                if deletedPath == nil {
                    deletedPath = header(line, "*** Delete File:")
                }
                continue
            }
            if line.hasPrefix("@@") { continue }
            if line.hasPrefix("+") {
                added += 1
            } else if line.hasPrefix("-") {
                removed += 1
            }
        }
        return (path ?? deletedPath, added, removed)
    }
}
