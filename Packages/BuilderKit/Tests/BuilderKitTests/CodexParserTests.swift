import BuilderIngest
import BuilderModel
import BuilderParse
import Foundation
import Testing

/// The Swift half of the Codex conformance gate.
///
/// `analysis/codex.py` is the reference reader and `scripts/gen_codex_fixture.py` records
/// what it says about the synthetic rollout under `spec/fixtures/codex/`. This suite runs
/// the same bytes through the real `CodexParser` — discovered from a temp tree shaped like
/// `~/.codex/sessions`, parsed from a fresh watermark — and must agree with the reference
/// on every count that maps onto a normalized event: prompts, tool calls and their mix,
/// interrupts, compactions, deduplicated replies, patch line deltas, and the token total.
///
/// Everything here is measured against the FIXTURE, not a real corpus. The counts a real
/// corpus produces through the `codex_*` diagnostics are the next measurement.
@Suite("Codex rollouts — reference fixture")
struct CodexParserTests {

    // MARK: - Expected values, decoded from the reference output

    struct Expected: Decodable {
        struct Stats: Decodable {
            let promptsSent: Int
            let repliesReceived: Int
            let interrupts: Int
            let compactions: Int
            let toolCalls: Int
            let toolMix: [String: Int]
            let linesAddedAgent: Int
            let linesRemovedAgent: Int
            let filesEdited: Int

            enum CodingKeys: String, CodingKey {
                case promptsSent = "prompts_sent"
                case repliesReceived = "replies_received"
                case interrupts
                case compactions
                case toolCalls = "tool_calls"
                case toolMix = "tool_mix"
                case linesAddedAgent = "lines_added_agent"
                case linesRemovedAgent = "lines_removed_agent"
                case filesEdited = "files_edited"
            }
        }

        struct TokenUsage: Decodable {
            let inputTokens: Int
            let cachedInputTokens: Int
            let cacheWriteInputTokens: Int
            let outputTokens: Int
            let totalTokens: Int

            enum CodingKeys: String, CodingKey {
                case inputTokens = "input_tokens"
                case cachedInputTokens = "cached_input_tokens"
                case cacheWriteInputTokens = "cache_write_input_tokens"
                case outputTokens = "output_tokens"
                case totalTokens = "total_tokens"
            }
        }

        struct Usage: Decodable {
            let tokenCountEvents: Int
            let naiveSumLastTokenUsage: TokenUsage
            let finalTotalTokenUsage: TokenUsage

            enum CodingKeys: String, CodingKey {
                case tokenCountEvents = "token_count_events"
                case naiveSumLastTokenUsage = "naive_sum_last_token_usage"
                case finalTotalTokenUsage = "final_total_token_usage"
            }
        }

        struct Meta: Decodable {
            let sessionId: String
            let cwd: String
            let cliVersion: String
            let model: String

            enum CodingKeys: String, CodingKey {
                case sessionId = "session_id"
                case cwd
                case cliVersion = "cli_version"
                case model
            }
        }

        let events: Int
        let stats: Stats
        let usage: Usage
        let meta: Meta
    }

    /// Walk up from this file to the repo root, exactly as `BoundaryFixtureTests` does.
    static var fixtureDirectory: URL? {
        var dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            let candidate = dir.appendingPathComponent("spec/fixtures/codex")
            if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
            dir = dir.deletingLastPathComponent()
        }
        return nil
    }

    static func fixtureData() throws -> Data {
        guard let dir = fixtureDirectory else { throw NSError(domain: "CodexParserTests", code: 1) }
        return try Data(contentsOf: dir.appendingPathComponent("synthetic_session.jsonl"))
    }

    /// The fixture's lines, without terminators and without the trailing empty element.
    static func fixtureLines() throws -> [String] {
        let text = String(decoding: try fixtureData(), as: UTF8.self)
        return text.split(separator: "\n", omittingEmptySubsequences: false)
            .map(String.init)
            .filter { !$0.isEmpty }
    }

    static func expected() throws -> Expected {
        guard let dir = fixtureDirectory else { throw NSError(domain: "CodexParserTests", code: 1) }
        let data = try Data(contentsOf: dir.appendingPathComponent("synthetic_session.expected.json"))
        return try JSONDecoder().decode(Expected.self, from: data)
    }

    // MARK: - A temp tree shaped like ~/.codex/sessions

    struct Rollout {
        let root: String
        let path: String

        static let fileName = "rollout-2025-11-03T14-00-00-0192f3a0-7f4e-7c1a-9b2d-3e4f5a6b7c8d.jsonl"

        static func make() throws -> Rollout {
            let root = NSTemporaryDirectory() + "builder-codex-\(UUID().uuidString)"
            let day = root + "/2025/11/03"
            try FileManager.default.createDirectory(atPath: day, withIntermediateDirectories: true)
            return Rollout(root: root, path: day + "/" + fileName)
        }

        func write(_ data: Data) throws {
            try data.write(to: URL(fileURLWithPath: path))
        }

        func write(lines: [String], terminated: Bool = true) throws {
            try write(Data((lines.joined(separator: "\n") + (terminated ? "\n" : "")).utf8))
        }

        func append(_ data: Data) throws {
            let h = try FileHandle(forWritingTo: URL(fileURLWithPath: path))
            defer { try? h.close() }
            _ = try h.seekToEnd()
            try h.write(contentsOf: data)
        }

        func append(lines: [String]) throws {
            try append(Data((lines.joined(separator: "\n") + "\n").utf8))
        }

        var size: Int {
            ((try? FileManager.default.attributesOfItem(atPath: path))?[.size] as? NSNumber)?.intValue ?? -1
        }

        func remove() { try? FileManager.default.removeItem(atPath: root) }

        var parser: CodexParser { CodexParser(sessionsRoot: root) }

        /// The one source the tree must contain.
        func source() throws -> SourceRef {
            let found = try parser.discover()
            #expect(found.count == 1, "expected exactly one rollout, found \(found.count)")
            guard let s = found.first else { throw NSError(domain: "CodexParserTests", code: 2) }
            return s
        }

        func parseAll() throws -> ParseResult {
            let s = try source()
            return try parser.parseAll(source: s)
        }
    }

    /// Run the whole fixture through discovery and a fresh-watermark parse.
    static func parseFixture() throws -> ParseResult {
        let tree = try Rollout.make()
        defer { tree.remove() }
        try tree.write(try fixtureData())
        return try tree.parseAll()
    }

    /// Run synthetic lines through the real parser.
    static func parse(lines: [String]) throws -> ParseResult {
        let tree = try Rollout.make()
        defer { tree.remove() }
        try tree.write(lines: lines)
        return try tree.parseAll()
    }

    // MARK: - Synthetic line builders

    static func line(_ ts: String, _ type: String, _ payload: String) -> String {
        #"{"timestamp": "\#(ts)", "type": "\#(type)", "payload": \#(payload)}"#
    }

    static let meta = line(
        "2025-11-03T14:00:00.000Z", "session_meta",
        #"{"id": "sess-1", "timestamp": "2025-11-03T14:00:00.000Z", "cwd": "/Users/dev/proj", "originator": "codex_cli_rs", "cli_version": "0.55.0", "source": "cli", "history_mode": "legacy"}"#)

    static let turn = line(
        "2025-11-03T14:00:00.010Z", "turn_context",
        #"{"cwd": "/Users/dev/proj", "approval_policy": "on-request", "model": "gpt-5-codex", "effort": "medium", "summary": "auto"}"#)

    static func userMessage(_ ts: String, _ text: String) -> String {
        line(ts, "event_msg", #"{"type": "user_message", "message": "\#(text)"}"#)
    }

    static func agentMessage(_ ts: String, _ text: String) -> String {
        line(ts, "event_msg", #"{"type": "agent_message", "message": "\#(text)"}"#)
    }

    static func assistantItem(_ ts: String, _ text: String) -> String {
        line(ts, "response_item",
             #"{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "\#(text)"}]}"#)
    }

    static func tokenCount(_ ts: String, last: (Int, Int, Int), total: (Int, Int, Int)) -> String {
        func usage(_ u: (Int, Int, Int)) -> String {
            #"{"input_tokens": \#(u.0), "cached_input_tokens": \#(u.1), "cache_write_input_tokens": 0, "output_tokens": \#(u.2), "reasoning_output_tokens": 0, "total_tokens": \#(u.0 + u.2)}"#
        }
        return line(ts, "event_msg",
                    #"{"type": "token_count", "info": {"total_token_usage": \#(usage(total)), "last_token_usage": \#(usage(last)), "model_context_window": 272000}, "rate_limits": null}"#)
    }

    static func kinds(_ r: ParseResult) -> [EventKind] {
        r.events.sorted { $0.ordinal < $1.ordinal }.map(\.kind)
    }

    static func count(_ r: ParseResult, _ kind: EventKind) -> Int {
        r.events.filter { $0.kind == kind }.count
    }

    static func diagnostic(_ r: ParseResult, _ code: String) -> ParseDiagnostic? {
        r.diagnostics.first { $0.code == code }
    }

    // MARK: - Discovery

    @Test func fixtureExists() throws {
        #expect(Self.fixtureDirectory != nil, "codex fixture missing — run scripts/gen_codex_fixture.py")
        #expect(try Self.fixtureLines().count == 40)
    }

    @Test func discoversOnlyRolloutShapedFiles() throws {
        let tree = try Rollout.make()
        defer { tree.remove() }
        try tree.write(try Self.fixtureData())

        // Strays that a `**/*.jsonl` glob would read as sessions.
        let fm = FileManager.default
        try Data("{}\n".utf8).write(to: URL(fileURLWithPath: tree.root + "/notes.jsonl"))
        try Data("{}\n".utf8).write(to: URL(fileURLWithPath: tree.root + "/2025/11/rollout-shallow.jsonl"))
        try Data("{}\n".utf8).write(to: URL(fileURLWithPath: tree.root + "/2025/11/03/notes.jsonl"))
        try fm.createDirectory(atPath: tree.root + "/20x5/11/03", withIntermediateDirectories: true)
        try Data("{}\n".utf8).write(to: URL(fileURLWithPath: tree.root + "/20x5/11/03/rollout-bad-year.jsonl"))
        try fm.createDirectory(atPath: tree.root + "/2025/11/03/nested", withIntermediateDirectories: true)
        try Data("{}\n".utf8).write(to: URL(fileURLWithPath: tree.root + "/2025/11/03/nested/rollout-deep.jsonl"))

        let found = try tree.parser.discover()
        #expect(found.count == 1)
        #expect(found.first?.path == tree.path)
        #expect(found.first?.kind == .jsonl)
        #expect(found.first?.harness == .codex)
        #expect(found.first?.isSidecar == false)
        #expect(found.first?.sourceID == Hashing.sourceID(harness: .codex, descriptor: "2025/11/03/" + Rollout.fileName))

        #expect(CodexParser.isRollout(relativePath: "2025/11/03/rollout-x.jsonl"))
        #expect(!CodexParser.isRollout(relativePath: "2025/11/rollout-x.jsonl"))
        #expect(!CodexParser.isRollout(relativePath: "2025/11/03/x.jsonl"))
        #expect(!CodexParser.isRollout(relativePath: "2025/11/03/rollout-x.json"))
        #expect(!CodexParser.isRollout(relativePath: "2025/1/03/rollout-x.jsonl"))
        #expect(!CodexParser.isRollout(relativePath: "2025/11/03/sub/rollout-x.jsonl"))

        // A missing root is the common case, never an error.
        #expect(try CodexParser(sessionsRoot: tree.root + "/does-not-exist").discover().isEmpty)
    }

    // MARK: - The gate

    @Test func reproducesTheReferenceCounts() throws {
        let exp = try Self.expected()
        let r = try Self.parseFixture()
        let events = r.events

        // One event per line, and the watermark comes from the reader.
        let fixtureBytes = try Self.fixtureData().count
        #expect(events.count == 40)
        #expect(r.watermark.lineCount == 40)
        #expect(r.watermark.byteOffset == fixtureBytes)
        #expect(r.fidelity == .full)

        #expect(Self.count(r, .prompt) == exp.stats.promptsSent)
        #expect(Self.count(r, .toolUse) == exp.stats.toolCalls)
        #expect(Self.count(r, .interrupt) == exp.stats.interrupts)
        #expect(Self.count(r, .compaction) == exp.stats.compactions)
        // Three agent_message events, each with a response_item copy 1 ms later.
        #expect(Self.count(r, .assistantMessage) == exp.stats.repliesReceived)
        #expect(Self.count(r, .unknown) == 0)
        #expect(Self.diagnostic(r, "unknown_record_shape") == nil)
        #expect(Self.diagnostic(r, "codex_unknown_event_msg_type") == nil)
        #expect(Self.diagnostic(r, "codex_assistant_deduped")?.detail.hasPrefix("3 ") == true)

        var mix: [String: Int] = [:]
        for e in events where e.kind == .toolUse { mix[e.toolName ?? "?", default: 0] += 1 }
        #expect(mix == exp.stats.toolMix)

        // Every output names its call. Seven, not eight: call_8 (`git push`) is cut off by
        // the interrupt and never gets an output.
        let results = events.filter { $0.kind == .toolResult }
        #expect(results.count == 7)
        #expect(results.allSatisfy { $0.toolID != nil && $0.toolName != nil })
        #expect(Self.diagnostic(r, "codex_output_without_call") == nil)

        // Patch effects: the first apply_patch is (scripts/deploy.sh, +3, -1); the pool is +4/-2.
        let lines = TokenAccountant.agentLines(events)
        #expect(lines.added == exp.stats.linesAddedAgent)
        #expect(lines.removed == exp.stats.linesRemovedAgent)
        let patches = events.filter { $0.kind == .toolUse && $0.toolName == "apply_patch" }
            .sorted { $0.ordinal < $1.ordinal }
        #expect(patches.first?.targetPath == "scripts/deploy.sh")
        #expect(patches.first?.linesAdded == 3)
        #expect(patches.first?.linesRemoved == 1)
        #expect(Set(patches.compactMap(\.targetPath)).count == exp.stats.filesEdited)
        // Shell commands carry no path: there is no file effect to claim.
        #expect(events.filter { $0.kind == .toolUse && $0.toolName != "apply_patch" }.allSatisfy { $0.targetPath == nil })
    }

    @Test func tokensAreSummedExactlyOnce() throws {
        let exp = try Self.expected()
        let r = try Self.parseFixture()
        let carriers = r.events.filter(\.usageAuthoritative)

        #expect(carriers.count == exp.usage.tokenCountEvents)
        #expect(carriers.allSatisfy { $0.kind == .noise && $0.dedupeKey != nil })
        #expect(Set(carriers.compactMap(\.dedupeKey)).count == carriers.count)
        #expect(carriers.allSatisfy { $0.model == exp.meta.model })

        let ledger = TokenAccountant.ledger(r.events, harness: .codex)
        #expect(ledger.reported)
        #expect(ledger.coverage == .complete)

        // `cached_input_tokens` is a subset of `input_tokens`, so the buckets split the
        // raw input into uncached + cached and the display total equals Codex's own.
        let naive = exp.usage.naiveSumLastTokenUsage
        #expect(ledger.buckets.input + ledger.buckets.cacheRead == naive.inputTokens)
        #expect(ledger.buckets.cacheRead == naive.cachedInputTokens)
        #expect(ledger.buckets.input == naive.inputTokens - naive.cachedInputTokens)
        #expect(ledger.buckets.output == naive.outputTokens)
        #expect(ledger.buckets.cacheWrite5m == naive.cacheWriteInputTokens)
        #expect(ledger.buckets.cacheWrite1h == 0)
        #expect(ledger.buckets.displayTotal == exp.usage.finalTotalTokenUsage.totalTokens)
        #expect(ledger.buckets.displayTotal == 31_800)

        // No carrier is duplicated, so the naive sum and the deduplicated sum agree.
        #expect(TokenAccountant.naiveTotal(r.events) == ledger.buckets)
        #expect(Self.diagnostic(r, "codex_token_count_repeated") == nil)
    }

    @Test func contextIsStampedOnEveryEvent() throws {
        let exp = try Self.expected()
        let r = try Self.parseFixture()

        #expect(r.events.allSatisfy { $0.cwd == exp.meta.cwd })
        #expect(r.events.allSatisfy { $0.nativeSessionID == exp.meta.sessionId })
        #expect(r.events.allSatisfy { $0.harnessVersion == exp.meta.cliVersion })
        #expect(r.events.allSatisfy { $0.harness == .codex && !$0.isSidechain })

        let agent = r.events.filter { $0.kind == .assistantMessage || $0.kind == .toolUse }
        #expect(agent.count == 11)
        #expect(agent.allSatisfy { $0.model == exp.meta.model && $0.role == "assistant" })
        #expect(r.events.filter { $0.kind == .prompt }.allSatisfy { $0.role == "user" })

        // No DAG: nothing has a parent, so everything is live.
        #expect(r.events.allSatisfy { $0.nativeParentID == nil })
        let live = LivePathResolver.liveEventIDs(in: r.events)
        #expect(live.count == r.events.count)
        #expect(r.events.allSatisfy { live.contains($0.nativeEventID ?? "") })
    }

    @Test func sessionizerFindsOneAttendedSession() throws {
        let r = try Self.parseFixture()
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York")!
        let sessions = Sessionizer.sessions(
            from: r.events, options: Sessionizer.Options(pooling: .nativeSession, calendar: cal))

        #expect(sessions.count == 1)
        guard let s = sessions.first else { return }
        #expect(s.harness == .codex)
        #expect(s.promptCount == 2)
        // 2 prompts + 1 interrupt. The compaction, tool calls and token carriers are not presence.
        #expect(s.presenceCount == 3)
        #expect(s.endReason == .stillRunning)
        // session_meta at 14:00:00.000 through turn_aborted at 14:02:01.000.
        #expect(abs(s.wallSeconds - 121) <= 1.0)
        #expect(s.activeSeconds <= s.wallSeconds + 0.001)
        #expect(s.eventCount == 40)
    }

    // MARK: - Watermarks

    /// A resumed read starts past the only lines that carry cwd, session id and model.
    /// The parser must replay them, or every event after the watermark loses its context
    /// and `.nativeSession` pooling collapses to "unknown".
    @Test func resumeReplaysFileContext() throws {
        let lines = try Self.fixtureLines()
        let tree = try Rollout.make()
        defer { tree.remove() }

        // Pad the head past `Tuning.headHashBytes` with one large bookkeeping record, so
        // the append below leaves the head hash unchanged and the second read is a real
        // `.resume`, not a `.restart`.
        let pad = Self.line(
            "2025-11-03T14:00:00.001Z", "response_item",
            #"{"type": "reasoning", "summary": [], "encrypted_content": "\#(String(repeating: "A", count: Tuning.headHashBytes + 4096))"}"#)
        let head = [lines[0], pad] + Array(lines[1..<20])   // through the first token_count
        let tail = Array(lines[20...])                       // second prompt, interrupt, two more token_counts

        try tree.write(lines: head)
        let source = try tree.source()
        let first = try tree.parser.parseAll(source: source)
        #expect(first.watermark.byteOffset == tree.size)
        #expect(first.watermark.lineCount == head.count)

        try tree.append(lines: tail)
        let second = try tree.parser.parse(source: source, from: first.watermark)

        #expect(Self.diagnostic(second, "source_restart") == nil, "the append must resume, not restart")
        #expect(second.events.count == tail.count)
        #expect(second.watermark.byteOffset == tree.size)
        #expect(second.watermark.lineCount == head.count + tail.count)

        // Context recovered from before the watermark.
        #expect(second.events.allSatisfy { $0.cwd == "/Users/dev/proj" })
        #expect(second.events.allSatisfy { $0.nativeSessionID == "0192f3a0-7f4e-7c1a-9b2d-3e4f5a6b7c8d" })
        #expect(second.events.allSatisfy { $0.harnessVersion == "0.55.0" })
        #expect(second.events.filter { $0.kind == .assistantMessage }.allSatisfy { $0.model == "gpt-5-codex" })

        // The two batches together are the whole fixture.
        let all = first.events + second.events
        #expect(all.filter { $0.kind == .prompt }.count == 2)
        #expect(all.filter { $0.kind == .interrupt }.count == 1)
        #expect(all.filter { $0.kind == .toolUse }.count == 8)
        #expect(all.filter { $0.kind == .assistantMessage }.count == 3)
        #expect(all.filter(\.usageAuthoritative).count == 3)
        #expect(TokenAccountant.ledger(all, harness: .codex).buckets.displayTotal == 31_800)

        // Nothing more to read: the third pass is a skip.
        let third = try tree.parser.parse(source: source, from: second.watermark)
        #expect(third.events.isEmpty)
        #expect(third.watermark == second.watermark)
    }

    /// Rollouts are appended to while being read; the last line is routinely half-written.
    @Test func partialTrailingLineIsNeverConsumed() throws {
        let lines = try Self.fixtureLines()
        let tree = try Rollout.make()
        defer { tree.remove() }

        try tree.write(lines: lines, terminated: false)
        let source = try tree.source()
        let first = try tree.parser.parseAll(source: source)

        // The unterminated interrupt is not an event yet, and the watermark stops at the
        // start of that line rather than at end of file.
        #expect(first.events.count == 39)
        #expect(Self.count(first, .interrupt) == 0)
        #expect(first.watermark.lineCount == 39)
        #expect(first.watermark.byteOffset == tree.size - lines[39].utf8.count)

        // The writer finishes the line. Whether the next read resumes or restarts (the
        // file is smaller than the head hash window, so the hash moves), the interrupt is
        // seen exactly once.
        try tree.append(Data("\n".utf8))
        let second = try tree.parser.parse(source: source, from: first.watermark)
        #expect(Self.count(second, .interrupt) == 1)
        #expect(second.watermark.byteOffset == tree.size)
        #expect(second.watermark.lineCount == 40)
    }

    // MARK: - Rules ported from the reference

    @Test func assistantDedupeIsOrderIndependentAndWindowed() throws {
        let r = try Self.parse(lines: [
            Self.meta, Self.turn,
            // response_item first, event copy 1 ms later: one reply, whichever came first.
            Self.assistantItem("2025-11-03T14:00:10.000Z", "Done."),
            Self.agentMessage("2025-11-03T14:00:10.001Z", "Done."),
            // Same text five seconds on is a new reply — outside the 2 s window.
            Self.agentMessage("2025-11-03T14:00:15.000Z", "Done."),
            // Different text inside the window is not a duplicate.
            Self.assistantItem("2025-11-03T14:00:15.001Z", "Other."),
            // Whitespace-only is nothing.
            Self.agentMessage("2025-11-03T14:00:16.000Z", "   "),
        ])
        #expect(Self.kinds(r) == [.noise, .noise, .assistantMessage, .noise, .assistantMessage, .assistantMessage, .noise])
        #expect(Self.diagnostic(r, "codex_assistant_deduped")?.detail.hasPrefix("1 ") == true)
        #expect(r.events.filter { $0.kind == .assistantMessage }.allSatisfy { $0.model == "gpt-5-codex" && $0.effort == "medium" })
    }

    @Test func promptRulesFollowTheReference() throws {
        let r = try Self.parse(lines: [
            Self.meta, Self.turn,
            // A harness envelope on the prompt channel is not a person.
            Self.userMessage("2025-11-03T14:00:01.000Z", #"<environment_context>\n  <cwd>/x</cwd>\n</environment_context>"#),
            // The preview prefix is stripped before anything is judged.
            Self.userMessage("2025-11-03T14:00:02.000Z", #"## My request for Codex:\n\nfix it"#),
            // Paginated copy of the same text 1 ms later: one prompt.
            Self.line("2025-11-03T14:00:02.001Z", "event_msg",
                      #"{"type": "item_completed", "thread_id": "t", "turn_id": "u", "item": {"type": "UserMessage", "content": [{"type": "text", "text": "fix it"}]}}"#),
            // Empty after the strip: nothing was sent.
            Self.userMessage("2025-11-03T14:00:03.000Z", "   "),
            // A paginated-only prompt (no user_message event at all) still counts.
            Self.line("2025-11-03T14:01:00.000Z", "event_msg",
                      #"{"type": "item_completed", "thread_id": "t", "turn_id": "u", "item": {"type": "UserMessage", "content": [{"type": "text", "text": "another"}]}}"#),
            // The response_item user channel proves nothing, however human it looks.
            Self.line("2025-11-03T14:01:00.002Z", "response_item",
                      #"{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "another"}]}"#),
            // Only `interrupted` is a presence signal.
            Self.line("2025-11-03T14:01:05.000Z", "event_msg", #"{"type": "turn_aborted", "turn_id": "u", "reason": "replaced"}"#),
            Self.line("2025-11-03T14:01:06.000Z", "event_msg", #"{"type": "turn_aborted", "turn_id": "u", "reason": "interrupted"}"#),
        ])
        #expect(Self.kinds(r) == [.noise, .noise, .noise, .prompt, .noise, .noise, .prompt, .noise, .noise, .interrupt])
        #expect(r.events.filter { $0.kind.isPresence }.count == 3)
        #expect(Self.diagnostic(r, "codex_prompt_envelope_skipped")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "codex_prompt_deduped")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "codex_prompt_empty")?.detail.hasPrefix("1 ") == true)
        // Prompt text never reaches the event.
        #expect(r.events.allSatisfy { $0.title == nil && $0.extra == nil })
    }

    @Test func tokenCountRepeatIsNotSummedTwice() throws {
        let r = try Self.parse(lines: [
            Self.meta, Self.turn,
            Self.tokenCount("2025-11-03T14:00:10.000Z", last: (1000, 400, 50), total: (1000, 400, 50)),
            // A rate-limit refresh: identical info, cumulative total unchanged.
            Self.tokenCount("2025-11-03T14:00:11.000Z", last: (1000, 400, 50), total: (1000, 400, 50)),
            // A real second turn advances the total.
            Self.tokenCount("2025-11-03T14:00:20.000Z", last: (500, 500, 10), total: (1500, 900, 60)),
            // `info` absent: nothing to store, counted.
            Self.line("2025-11-03T14:00:21.000Z", "event_msg", #"{"type": "token_count", "info": null, "rate_limits": null}"#),
        ])
        let carriers = r.events.filter(\.usageAuthoritative).sorted { $0.ordinal < $1.ordinal }
        #expect(carriers.count == 2)
        #expect(carriers.map(\.tokIn) == [600, 0])
        #expect(carriers.map(\.tokCacheRead) == [400, 500])
        #expect(carriers.map(\.tokOut) == [50, 10])
        #expect(Self.diagnostic(r, "codex_token_count_repeated")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "codex_token_count_no_usage")?.detail.hasPrefix("1 ") == true)
        #expect(TokenAccountant.ledger(r.events, harness: .codex).buckets.displayTotal == 1050 + 510)
        #expect(r.events.allSatisfy { $0.kind == .noise })
    }

    @Test func toolShapesAreRecognised() throws {
        let r = try Self.parse(lines: [
            Self.meta, Self.turn,
            // A patch run through the shell scores like the custom tool. `arguments` is a
            // JSON string holding JSON, so the command's newlines are double-escaped.
            Self.line("2025-11-03T14:00:10.000Z", "response_item",
                      #"{"type": "function_call", "name": "shell", "arguments": "{\"command\": [\"bash\", \"-lc\", \"apply_patch <<'EOF'\\n*** Begin Patch\\n*** Add File: a.txt\\n+one\\n+two\\n*** End Patch\\nEOF\"]}", "call_id": "c1"}"#),
            Self.line("2025-11-03T14:00:10.100Z", "response_item", #"{"type": "function_call_output", "call_id": "c1", "output": "Success. Updated the following files:\nA a.txt\n"}"#),
            // ASSUMED older form: apply_patch as a function_call with the patch under `input`.
            Self.line("2025-11-03T14:00:11.000Z", "response_item",
                      #"{"type": "function_call", "name": "apply_patch", "arguments": "{\"input\": \"*** Begin Patch\\n*** Update File: b.py\\n@@\\n-x = 1\\n+x = 2\\n*** End Patch\"}", "call_id": "c2"}"#),
            // A delete-only patch still names its file.
            Self.line("2025-11-03T14:00:12.000Z", "response_item",
                      #"{"type": "custom_tool_call", "name": "apply_patch", "input": "*** Begin Patch\n*** Delete File: c.txt\n*** End Patch", "call_id": "c3", "status": "completed"}"#),
            // exec_command carries `cmd`, not `command`; no path either way.
            Self.line("2025-11-03T14:00:13.000Z", "response_item",
                      #"{"type": "function_call", "name": "exec_command", "arguments": "{\"cmd\": \"pytest -q\"}", "call_id": "c4"}"#),
            // An output whose call was never seen is counted, not dropped.
            Self.line("2025-11-03T14:00:14.000Z", "response_item", #"{"type": "custom_tool_call_output", "call_id": "ghost", "output": "x"}"#),
            // A list-shaped output is still just a result.
            Self.line("2025-11-03T14:00:14.500Z", "response_item", #"{"type": "function_call_output", "call_id": "c4", "output": [{"type": "input_text", "text": "ok"}]}"#),
        ])
        let byOrdinal = r.events.sorted { $0.ordinal < $1.ordinal }
        #expect(byOrdinal.map(\.kind) == [.noise, .noise, .toolUse, .toolResult, .toolUse, .toolUse, .toolUse, .toolResult, .toolResult])

        let shell = byOrdinal[2]
        #expect(shell.toolName == "shell" && shell.toolID == "c1")
        #expect(shell.targetPath == "a.txt" && shell.linesAdded == 2 && shell.linesRemoved == 0)
        #expect(byOrdinal[3].toolName == "shell" && byOrdinal[3].targetPath == "a.txt")

        let legacy = byOrdinal[4]
        #expect(legacy.toolName == "apply_patch" && legacy.targetPath == "b.py")
        #expect(legacy.linesAdded == 1 && legacy.linesRemoved == 1)

        #expect(byOrdinal[5].targetPath == "c.txt" && byOrdinal[5].linesAdded == 0)
        #expect(byOrdinal[6].toolName == "exec_command" && byOrdinal[6].targetPath == nil && byOrdinal[6].linesAdded == nil)

        #expect(byOrdinal[7].toolID == "ghost" && byOrdinal[7].toolName == nil)
        #expect(Self.diagnostic(r, "codex_output_without_call")?.detail.hasPrefix("1 ") == true)
        #expect(byOrdinal[8].toolName == "exec_command")
    }

    @Test func unknownShapesAreRecordedNotDropped() throws {
        let r = try Self.parse(lines: [
            Self.meta, Self.turn,
            Self.line("2025-11-03T14:00:10.000Z", "hologram", #"{"beam": 1}"#),
            Self.line("2025-11-03T14:00:11.000Z", "response_item", #"{"type": "teleport"}"#),
            Self.line("2025-11-03T14:00:12.000Z", "event_msg", #"{"type": "brand_new_thing"}"#),
            Self.line("2025-11-03T14:00:13.000Z", "token_usage_record", #"{"usage": {"input_tokens": 999}}"#),
            "{not json",
            Self.line("2025-11-03T14:00:14.000Z", "compacted", #"{"message": "summary"}"#),
        ])
        #expect(Self.kinds(r) == [.noise, .noise, .unknown, .unknown, .noise, .noise, .compaction])
        #expect(Self.diagnostic(r, "unknown_record_shape")?.detail.hasPrefix("2 ") == true)
        #expect(Self.diagnostic(r, "codex_unknown_event_msg_type")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "malformed_json_line")?.detail.hasPrefix("1 ") == true)
        // token_usage_record is never summed until a corpus says which channel is right.
        #expect(r.events.filter(\.usageAuthoritative).isEmpty)
        // The malformed line still advanced the watermark: 8 lines, 7 events.
        #expect(r.watermark.lineCount == 8)
        #expect(r.events.count == 7)
    }

    @Test func harnessFlagsMatchTheParser() {
        #expect(Harness.codex.isImplemented)
        #expect(Harness.codex.reportsTokens)
        #expect(Harness.codex.reportsModel)
        #expect(CodexParser().harness == .codex)
        #expect(CodexParser().parserVersion == 1)
    }
}
