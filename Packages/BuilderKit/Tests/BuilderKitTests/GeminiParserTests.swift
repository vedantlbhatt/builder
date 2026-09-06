import BuilderIngest
import BuilderModel
import BuilderParse
import BuilderSync
import Foundation
import Testing

/// The Swift half of the Gemini CLI conformance gate.
///
/// `analysis/gemini.py` is the reference reader and `scripts/gen_gemini_fixture.py` records
/// what it says about the synthetic recording under `spec/fixtures/gemini/`. This suite runs
/// the same bytes through the real `GeminiParser` — discovered from a temp tree shaped like
/// `~/.gemini/tmp`, parsed from a fresh watermark — and must agree with the reference on
/// every count that maps onto a normalized event.
///
/// Three counts deliberately do NOT map, and the tests below pin the exact difference rather
/// than looking away:
///
/// - The reference replays the whole file and honours `$rewindTo`, so the rewound gemini
///   message is neither a reply nor a token carrier there. A streaming parser cannot take
///   back rows it has already emitted; it keeps them (the API charged for them) and records
///   `gemini_rewind_seen`. So replies and tokens here equal the reference PLUS the rewound
///   message, and the tests derive that message's figures from the fixture itself.
/// - The reference credits a `cat > file <<EOF` heredoc with a file and its lines
///   (`digest._bash_file_effect`). Like `CodexParser`, this parser claims no file effect for
///   a shell command; the one heredoc in the fixture is the whole difference.
/// - The reference falls back to `digest._looks_like_error` on every successful tool
///   output. Its Swift twin lives in `BuilderAnalysis.Digest`, which depends on this module,
///   so the parser applies only the rules it can verify from the recording itself: `status`,
///   `response.error`, and the shell tool's `Exit Code: N` line. The fixture's c4 — a pytest
///   run whose output has a `FAILED` line and no exit status — is an error there and not
///   here; errors here equal the reference MINUS that one call.
///
/// Everything here is measured against the FIXTURE, not a real corpus. The counts a real
/// corpus produces through the `gemini_*` diagnostics are the next measurement.
@Suite("Gemini CLI recordings — reference fixture")
struct GeminiParserTests {

    // MARK: - Expected values, decoded from the reference output

    struct Expected: Decodable {
        struct Stats: Decodable {
            let promptsSent: Int
            let repliesReceived: Int
            let toolCalls: Int
            let toolMix: [String: Int]
            let errors: Int
            let linesAddedAgent: Int
            let linesRemovedAgent: Int
            let filesEdited: Int
            let filesWrittenViaShell: Int
            let wallSeconds: Int

            enum CodingKeys: String, CodingKey {
                case promptsSent = "prompts_sent"
                case repliesReceived = "replies_received"
                case toolCalls = "tool_calls"
                case toolMix = "tool_mix"
                case errors
                case linesAddedAgent = "lines_added_agent"
                case linesRemovedAgent = "lines_removed_agent"
                case filesEdited = "files_edited"
                case filesWrittenViaShell = "files_written_via_shell"
                case wallSeconds = "wall_seconds"
            }
        }

        struct Tokens: Decodable {
            let input: Int
            let output: Int
            let cached: Int
            let thoughts: Int
            let tool: Int
            let total: Int
        }

        struct Usage: Decodable {
            let geminiMessagesWithTokens: Int
            let naiveRecordsWithTokens: Int
            let naiveSumAllRecords: Tokens
            let dedupedByMessageId: Tokens
            let naiveEqualsDeduped: Bool

            enum CodingKeys: String, CodingKey {
                case geminiMessagesWithTokens = "gemini_messages_with_tokens"
                case naiveRecordsWithTokens = "naive_records_with_tokens"
                case naiveSumAllRecords = "naive_sum_all_records"
                case dedupedByMessageId = "deduped_by_message_id"
                case naiveEqualsDeduped = "naive_equals_deduped"
            }
        }

        struct Meta: Decodable {
            let sessionId: String
            let model: String
            let summary: String

            enum CodingKeys: String, CodingKey {
                case sessionId = "session_id"
                case model
                case summary
            }
        }

        struct Diagnostics: Decodable {
            let lines: Int
            let recordKinds: [String: Int]

            enum CodingKeys: String, CodingKey {
                case lines
                case recordKinds = "record_kinds"
            }
        }

        /// The `kind: "subagent"` recording the generator writes next to the session.
        struct Subagent: Decodable {
            let file: String
            let stats: Stats
            let diagnostics: Diagnostics
        }

        let stats: Stats
        let usage: Usage
        let meta: Meta
        let diagnostics: Diagnostics
        let subagent: Subagent
    }

    /// Walk up from this file to the repo root, exactly as `CodexParserTests` does.
    static var fixtureDirectory: URL? {
        var dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            let candidate = dir.appendingPathComponent("spec/fixtures/gemini")
            if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
            dir = dir.deletingLastPathComponent()
        }
        return nil
    }

    static func fixtureData() throws -> Data {
        guard let dir = fixtureDirectory else { throw NSError(domain: "GeminiParserTests", code: 1) }
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
        guard let dir = fixtureDirectory else { throw NSError(domain: "GeminiParserTests", code: 1) }
        let data = try Data(contentsOf: dir.appendingPathComponent("synthetic_session.expected.json"))
        return try JSONDecoder().decode(Expected.self, from: data)
    }

    /// The tokens of the message a `$rewindTo` line points at — what the reference drops and
    /// this parser keeps. Read from the fixture, not typed in, so the two cannot drift.
    static func rewoundTokens() throws -> Expected.Tokens {
        let lines = try fixtureLines()
        var target: String?
        for l in lines {
            if let obj = JSONLine.parse(Data(l.utf8)), let t = obj["$rewindTo"].string { target = t }
        }
        guard let target else { throw NSError(domain: "GeminiParserTests", code: 3) }
        for l in lines {
            guard let obj = JSONLine.parse(Data(l.utf8)), obj.id.string == target, obj.tokens.isObject else { continue }
            let t = obj.tokens
            return Expected.Tokens(
                input: t.input.int ?? 0, output: t.output.int ?? 0, cached: t.cached.int ?? 0,
                thoughts: t.thoughts.int ?? 0, tool: t.tool.int ?? 0, total: t.total.int ?? 0)
        }
        throw NSError(domain: "GeminiParserTests", code: 4)
    }

    // MARK: - A temp tree shaped like ~/.gemini/tmp

    struct Tree {
        let root: String
        let path: String

        static let project = "proj-abc"
        static let fileName = "session-2025-11-03T14-00-abcdef12.jsonl"

        static func make() throws -> Tree {
            let root = NSTemporaryDirectory() + "builder-gemini-\(UUID().uuidString)"
            let chats = root + "/" + project + "/chats"
            try FileManager.default.createDirectory(atPath: chats, withIntermediateDirectories: true)
            return Tree(root: root, path: chats + "/" + fileName)
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

        var parser: GeminiParser { GeminiParser(tmpRoot: root) }

        /// The one root recording the tree must contain.
        func source() throws -> SourceRef {
            let found = try parser.discover().filter { !$0.isSidecar }
            #expect(found.count == 1, "expected exactly one root recording, found \(found.count)")
            guard let s = found.first else { throw NSError(domain: "GeminiParserTests", code: 2) }
            return s
        }

        func parseAll() throws -> ParseResult {
            let s = try source()
            return try parser.parseAll(source: s)
        }
    }

    /// Run the whole fixture through discovery and a fresh-watermark parse.
    static func parseFixture() throws -> ParseResult {
        let tree = try Tree.make()
        defer { tree.remove() }
        try tree.write(try fixtureData())
        return try tree.parseAll()
    }

    /// Run synthetic lines through the real parser.
    static func parse(lines: [String]) throws -> ParseResult {
        let tree = try Tree.make()
        defer { tree.remove() }
        try tree.write(lines: lines)
        return try tree.parseAll()
    }

    // MARK: - Synthetic line builders

    static let meta =
        #"{"sessionId": "sess-1", "projectHash": "abc123", "startTime": "2025-11-04T09:00:00.000Z", "lastUpdated": "2025-11-04T09:00:00.000Z", "kind": "main"}"#

    static func user(_ id: String, _ ts: String, _ text: String) -> String {
        #"{"id": "\#(id)", "timestamp": "\#(ts)", "type": "user", "content": "\#(text)"}"#
    }

    static func gemini(_ id: String, _ ts: String, _ text: String, tokens: (Int, Int, Int, Int)? = nil, extra: String = "") -> String {
        var s = #"{"id": "\#(id)", "timestamp": "\#(ts)", "type": "gemini", "content": "\#(text)", "model": "gemini-2.5-pro""#
        if let t = tokens {
            s += #", "tokens": {"input": \#(t.0), "output": \#(t.1), "cached": \#(t.2), "thoughts": \#(t.3), "tool": 0, "total": \#(t.0 + t.1 + t.3)}"#
        }
        s += extra
        s += "}"
        return s
    }

    /// In emission order. Several events share one line (and so one ordinal), and the
    /// parser emits them in file order, so the array is NOT re-sorted.
    static func kinds(_ r: ParseResult) -> [EventKind] {
        r.events.map(\.kind)
    }

    static func delta(_ old: String, _ new: String) -> [Int] {
        let d = GeminiParser.lineDelta(old: old, new: new)
        return [d.added, d.removed]
    }

    static func count(_ r: ParseResult, _ kind: EventKind) -> Int {
        r.events.filter { $0.kind == kind }.count
    }

    static func diagnostic(_ r: ParseResult, _ code: String) -> ParseDiagnostic? {
        r.diagnostics.first { $0.code == code }
    }

    static func isError(_ e: NormalizedEvent) -> Bool {
        e.extra?["is_error"] == "true"
    }

    // MARK: - Discovery

    @Test func fixtureExists() throws {
        let exp = try Self.expected()
        #expect(Self.fixtureDirectory != nil, "gemini fixture missing — run scripts/gen_gemini_fixture.py")
        #expect(try Self.fixtureLines().count == exp.diagnostics.lines)
        #expect(exp.diagnostics.lines == 16)
        // The fixture is the trap: naive and per-id totals differ.
        #expect(!exp.usage.naiveEqualsDeduped)
        #expect(exp.usage.naiveSumAllRecords.total == 75_042)
        #expect(exp.usage.dedupedByMessageId.total == 51_862)
    }

    @Test func discoversOnlyRecordingShapedFiles() throws {
        let tree = try Tree.make()
        defer { tree.remove() }
        try tree.write(try Self.fixtureData())

        let fm = FileManager.default
        let proj = tree.root + "/" + Tree.project
        // Strays that a `**/*.jsonl` glob would read as sessions.
        try Data("{}\n".utf8).write(to: URL(fileURLWithPath: tree.root + "/notes.jsonl"))
        try Data("{}\n".utf8).write(to: URL(fileURLWithPath: proj + "/logs.jsonl"))
        try fm.createDirectory(atPath: proj + "/checkpoints", withIntermediateDirectories: true)
        try Data("{}\n".utf8).write(to: URL(fileURLWithPath: proj + "/checkpoints/x.jsonl"))
        try fm.createDirectory(atPath: proj + "/chats/a/b", withIntermediateDirectories: true)
        try Data("{}\n".utf8).write(to: URL(fileURLWithPath: proj + "/chats/a/b/deep.jsonl"))
        // The legacy whole-file form is not this parser's to read.
        try Data("{}\n".utf8).write(to: URL(fileURLWithPath: proj + "/chats/legacy.json"))
        // A subagent recording, one level down under its parent's id: a sidecar.
        let parent = "5d2c1a0e-4f3b-4c2d-9e8f-7a6b5c4d3e2f"
        try fm.createDirectory(atPath: proj + "/chats/" + parent, withIntermediateDirectories: true)
        try Data(Self.meta.utf8).write(to: URL(fileURLWithPath: proj + "/chats/" + parent + "/sub-1.jsonl"))

        let found = try tree.parser.discover().sorted { $0.path < $1.path }
        #expect(found.count == 2)
        let roots = found.filter { !$0.isSidecar }
        let sidecars = found.filter(\.isSidecar)
        #expect(roots.count == 1 && sidecars.count == 1)
        #expect(roots.first?.path == tree.path)
        #expect(roots.first?.kind == .jsonl)
        #expect(roots.first?.harness == .geminiCLI)
        #expect(roots.first?.sourceID == Hashing.sourceID(harness: .geminiCLI, descriptor: Tree.project + "/chats/" + Tree.fileName))
        #expect(sidecars.first?.path == proj + "/chats/" + parent + "/sub-1.jsonl")

        #expect(GeminiParser.recordingShape(relativePath: "p/chats/s.jsonl") == GeminiParser.RecordingShape(projectID: "p", parentSessionID: nil))
        #expect(GeminiParser.recordingShape(relativePath: "p/chats/parent/s.jsonl") == GeminiParser.RecordingShape(projectID: "p", parentSessionID: "parent"))
        #expect(GeminiParser.recordingShape(relativePath: "p/chats/a/b/s.jsonl") == nil)
        #expect(GeminiParser.recordingShape(relativePath: "p/checkpoints/s.jsonl") == nil)
        #expect(GeminiParser.recordingShape(relativePath: "p/chats/s.json") == nil)
        #expect(GeminiParser.recordingShape(relativePath: "p/chats/.jsonl") == nil)
        #expect(GeminiParser.recordingShape(relativePath: "chats/s.jsonl") == nil)
        #expect(GeminiParser.recordingShape(relativePath: "s.jsonl") == nil)

        // A missing root is the common case, never an error.
        #expect(try GeminiParser(tmpRoot: tree.root + "/does-not-exist").discover().isEmpty)
    }

    // MARK: - The gate

    @Test func reproducesTheReferenceCounts() throws {
        let exp = try Self.expected()
        let r = try Self.parseFixture()
        let events = r.events

        let fixtureBytes = try Self.fixtureData().count
        #expect(r.watermark.lineCount == exp.diagnostics.lines)
        #expect(r.watermark.byteOffset == fixtureBytes)
        #expect(r.fidelity == .full)
        // Per line, every line leaving at least one row: metadata 1 | `/help` 1 | prompt 1 |
        // `$set` 1 | g1 text 1 | g1 + tokens: carrier 1 | g1 + toolCalls: 8 calls + 8 results
        // + carrier = 17 | `$set` 1 | u2 (c8's response, already carried) 1 | g2 text +
        // carrier 2 | prompt 1 | g_wrong text + carrier 2 | `$rewindTo` 1 | g3 call + result
        // + carrier 3 | g4 text + carrier 2 | `$set` summary → title 1.
        // 6 + 17 + 8 + 3 + 3 = 37.
        #expect(events.count == 37)

        #expect(Self.count(r, .prompt) == exp.stats.promptsSent)
        #expect(Self.count(r, .toolUse) == exp.stats.toolCalls)
        #expect(Self.count(r, .interrupt) == 0)
        #expect(Self.count(r, .unknown) == 0)
        #expect(Self.diagnostic(r, "unknown_record_shape") == nil)
        #expect(Self.diagnostic(r, "gemini_message_type_unknown") == nil)

        // The rewound reply is a reply here and not in the reference (see the suite note).
        #expect(Self.count(r, .assistantMessage) == exp.stats.repliesReceived + 1)
        #expect(Self.diagnostic(r, "gemini_rewind_seen")?.detail.hasPrefix("\(exp.diagnostics.recordKinds["rewind"] ?? -1) ") == true)

        var mix: [String: Int] = [:]
        for e in events where e.kind == .toolUse { mix[e.toolName ?? "?", default: 0] += 1 }
        #expect(mix == exp.stats.toolMix)

        // One result per call, each naming its call. The user-recorded copy of c8's
        // response is deduped. Errors: c5 (status "error", response.error) and c7 (status
        // "success", `Exit Code: 2` in its output). The reference counts a third, c4, whose
        // pytest output has a `FAILED` line and no exit status — caught there only by the
        // `digest._looks_like_error` fallback this module cannot reach (see the suite note).
        let results = events.filter { $0.kind == .toolResult }
        #expect(results.count == exp.stats.toolCalls)
        #expect(results.allSatisfy { $0.toolID != nil && $0.toolName != nil })
        #expect(results.filter(Self.isError).map(\.toolID) == ["c5", "c7"])
        #expect(results.filter(Self.isError).count == exp.stats.errors - 1)
        #expect(exp.stats.errors == 3)
        // The failed replace's RESULT still names the file it was about; the credit is
        // what is withheld, on the call.
        #expect(results.first { $0.toolID == "c5" }?.targetPath == "/Users/dev/proj/tests/test_deploy.py")
        #expect(Self.diagnostic(r, "gemini_result_error")?.detail.hasPrefix("2 ") == true)
        #expect(Self.diagnostic(r, "gemini_shell_exit_code_error")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_tool_credit_withheld")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_user_tool_response_records")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_user_tool_response_deduped")?.detail.hasPrefix("1 ") == true)
        // g3's functionCall part duplicates its toolCalls record.
        #expect(Self.diagnostic(r, "gemini_function_call_part_deduped")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_tool_from_function_call_part") == nil)

        // Prompt rules: `/help` ignored; the @file expansion judged by displayContent.
        #expect(Self.diagnostic(r, "gemini_prompt_ignored")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_prompt_from_display_content")?.detail.hasPrefix("1 ") == true)
        // g1 is written three times; g4 has no timestamp and is never given one.
        #expect(Self.diagnostic(r, "gemini_message_rewrite")?.detail.hasPrefix("2 ") == true)
        #expect(Self.diagnostic(r, "gemini_message_no_timestamp")?.detail.hasPrefix("1 ") == true)
        #expect(events.filter { $0.kind == .assistantMessage && $0.ts == nil }.count == 1)

        // Line credit. write_file: 5 terminated lines. replace c2: `set -e` → 3 lines (+3/-1).
        // replace c5 asked for one line for one (+1/-1) and FAILED, so it earns nothing and
        // names no file — the reference agrees (+11/-1, 3 files, not +12/-2, 4). The
        // reference's total also credits the `cat > … <<EOF` heredoc with 3 lines and a
        // third file; a shell command claims nothing here, so lines here are 5 + 3 = 8.
        let byTool = events.filter { $0.kind == .toolUse }  // file order
        let write = byTool.first { $0.toolName == "write_file" }
        #expect(write?.targetPath == "/Users/dev/proj/tests/helpers.py")
        #expect(write?.linesAdded == 5 && write?.linesRemoved == 0)
        let replaces = byTool.filter { $0.toolName == "replace" }
        #expect(replaces.map(\.linesAdded) == [3, nil])
        #expect(replaces.map(\.linesRemoved) == [1, nil])
        #expect(replaces.map(\.targetPath) == ["/Users/dev/proj/scripts/deploy.sh", nil])
        let lines = TokenAccountant.agentLines(events)
        #expect(lines.added == 8)
        #expect(lines.removed == exp.stats.linesRemovedAgent)
        #expect(exp.stats.linesAddedAgent - lines.added == 3)
        let edited = Set(byTool.filter { $0.toolName == "write_file" || $0.toolName == "replace" }.compactMap(\.targetPath))
        #expect(edited.count == 2)
        #expect(edited.count == exp.stats.filesEdited - exp.stats.filesWrittenViaShell)
        #expect(byTool.filter { $0.toolName == "run_shell_command" }.allSatisfy { $0.targetPath == nil && $0.linesAdded == nil })
        // read_file names its file but scores no lines.
        let read = byTool.first { $0.toolName == "read_file" }
        #expect(read?.targetPath == "/Users/dev/proj/scripts/deploy.sh" && read?.linesAdded == nil)

        // Tool timestamps are completion times, from the ToolCallRecord, not the message.
        #expect(byTool.first?.toolID == "c1")
        #expect(byTool.first?.ts == ISO8601.seconds("2025-11-04T09:00:07.000Z"))

        // The title the harness wrote: the final `$set.summary`.
        let titles = events.filter { $0.kind == .title }
        #expect(titles.count == 1)
        #expect(titles.first?.title == exp.meta.summary)
        // No prompt or assistant text reaches any event.
        #expect(events.allSatisfy { $0.kind == .title || $0.title == nil })
    }

    @Test func tokensAreClaimedOncePerMessageID() throws {
        let exp = try Self.expected()
        let rewound = try Self.rewoundTokens()
        let r = try Self.parseFixture()
        let carriers = r.events.filter(\.usageAuthoritative)

        // One claim per gemini message that ever carried tokens — including the rewound one.
        #expect(carriers.count == exp.usage.geminiMessagesWithTokens + 1)
        #expect(carriers.allSatisfy { $0.kind == .noise && $0.dedupeKey != nil })
        #expect(Set(carriers.compactMap(\.dedupeKey)).count == carriers.count)
        #expect(carriers.allSatisfy { $0.model == exp.meta.model })

        let ledger = TokenAccountant.ledger(r.events, harness: .geminiCLI)
        #expect(ledger.reported)
        #expect(ledger.coverage == .complete)

        // Buckets: cached is a subset of input (uncached remainder in `input`), thoughts are
        // billed as output and are outside `output` in the summary, so displayTotal equals
        // Gemini's own `total`.
        let ded = exp.usage.dedupedByMessageId
        #expect(ledger.buckets.input + ledger.buckets.cacheRead == ded.input + rewound.input)
        #expect(ledger.buckets.cacheRead == ded.cached + rewound.cached)
        #expect(ledger.buckets.output == ded.output + ded.thoughts + rewound.output + rewound.thoughts)
        #expect(ledger.buckets.cacheWrite5m == 0 && ledger.buckets.cacheWrite1h == 0)
        #expect(ledger.buckets.displayTotal == ded.total + rewound.total)
        #expect(ledger.buckets.displayTotal == 65_882)
        #expect(rewound.total == 14_020)
        #expect(Self.diagnostic(r, "gemini_token_total_mismatch") == nil)
        #expect(Self.diagnostic(r, "gemini_cached_exceeds_input") == nil)

        // THE TRAP, pinned: summing every line that carries tokens reproduces the reference's
        // naive figure, because the second copy of g1's tokens is on the row, unclaimed.
        let naive = TokenAccountant.naiveTotal(r.events)
        #expect(naive.displayTotal == exp.usage.naiveSumAllRecords.total)
        #expect(naive.displayTotal == 75_042)
        #expect(r.events.filter { $0.tokIn != nil }.count == exp.usage.naiveRecordsWithTokens)
        #expect(Self.diagnostic(r, "gemini_token_reappend")?.detail.hasPrefix("1 ") == true)
        let unclaimed = r.events.filter { $0.tokIn != nil && !$0.usageAuthoritative }
        #expect(unclaimed.count == 1)
        #expect(unclaimed.first?.dedupeKey == carriers.first?.dedupeKey)
    }

    @Test func contextIsStampedOnEveryEvent() throws {
        let exp = try Self.expected()
        let r = try Self.parseFixture()

        #expect(r.events.allSatisfy { $0.nativeSessionID == exp.meta.sessionId })
        #expect(r.events.allSatisfy { $0.harness == .geminiCLI && !$0.isSidechain && $0.agentID == nil })
        // No cwd anywhere in a recording; no `directories` in the fixture either.
        #expect(r.events.allSatisfy { $0.cwd == nil })
        #expect(Self.diagnostic(r, "gemini_cwd_from_directories") == nil)

        // 4 assistant texts (g1, g2, g_wrong, g4) + 9 tool calls.
        let agent = r.events.filter { $0.kind == .assistantMessage || $0.kind == .toolUse }
        #expect(agent.count == 13)
        #expect(agent.allSatisfy { $0.model == exp.meta.model && $0.role == "assistant" })
        #expect(r.events.filter { $0.kind == .prompt }.allSatisfy { $0.role == "user" })

        // No DAG: nothing has a parent, so everything is live — including the rewound rows.
        #expect(r.events.allSatisfy { $0.nativeParentID == nil })
        let live = LivePathResolver.liveEventIDs(in: r.events)
        #expect(live.count == r.events.count)
        #expect(Set(r.events.compactMap(\.nativeEventID)).count == r.events.count)
        #expect(Set(r.events.map(\.eventUID)).count == r.events.count)
    }

    @Test func sessionizerFindsOneAttendedSession() throws {
        let exp = try Self.expected()
        let r = try Self.parseFixture()
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York")!
        let sessions = Sessionizer.sessions(
            from: r.events, options: Sessionizer.Options(pooling: .nativeSession, calendar: cal))

        #expect(sessions.count == 1)
        guard let s = sessions.first else { return }
        #expect(s.harness == .geminiCLI)
        #expect(s.promptCount == exp.stats.promptsSent)
        // Two prompts and nothing else: tool calls and token carriers are not presence.
        #expect(s.presenceCount == exp.stats.promptsSent)
        #expect(s.endReason == .stillRunning)
        // 63 s, the reference's figure, from the metadata row's startTime (09:00:00 — the
        // Sessionizer pools every timestamped record, as it does for Codex's `session_meta`)
        // to c9's result at 09:01:03. The reference gets the same start by stamping the
        // untimestamped g4 with startTime. The final `$set` (summary, lastUpdated 09:01:04)
        // does NOT extend the clock: a `$set` row takes the last stamped clock seen, so a
        // summary saved after the last message cannot push a session's end past it.
        #expect(abs(s.wallSeconds - Double(exp.stats.wallSeconds)) < 0.001)
        #expect(exp.stats.wallSeconds == 63)
        #expect(s.activeSeconds <= s.wallSeconds + 0.001)
        // The title row is inside the session (it is what SessionDeriver names the card by).
        #expect(s.eventIndices.contains { r.events[$0].kind == .title })
    }

    // MARK: - Watermarks

    /// A resumed read starts past the metadata line AND past the first carrier of a message
    /// that is re-appended after the watermark. The parser must replay both, or the resumed
    /// events lose their session id and the re-append is claimed a second time.
    @Test func resumeReplaysContextAndDoesNotDoubleClaim() throws {
        let lines = try Self.fixtureLines()
        let tree = try Tree.make()
        defer { tree.remove() }

        // Pad the head past `Tuning.headHashBytes` with one large bookkeeping record, so the
        // append below leaves the head hash unchanged and the second read is a real
        // `.resume`, not a `.restart`. An unknown key inside `$set` is merged and ignored.
        let pad = #"{"$set": {"lastUpdated": "2025-11-04T09:00:00.500Z", "pad": "\#(String(repeating: "A", count: Tuning.headHashBytes + 4096))"}}"#
        let head = [lines[0], pad] + Array(lines[1..<6])  // through g1's FIRST token carrier
        let tail = Array(lines[6...])                     // g1 again with tokens + toolCalls, then the rest

        try tree.write(lines: head)
        let source = try tree.source()
        let first = try tree.parser.parseAll(source: source)
        #expect(first.watermark.byteOffset == tree.size)
        #expect(first.watermark.lineCount == head.count)
        #expect(first.events.filter(\.usageAuthoritative).count == 1)
        #expect(Self.count(first, .toolUse) == 0)

        try tree.append(lines: tail)
        let second = try tree.parser.parse(source: source, from: first.watermark)

        #expect(Self.diagnostic(second, "source_restart") == nil, "the append must resume, not restart")
        #expect(second.watermark.byteOffset == tree.size)
        #expect(second.watermark.lineCount == head.count + tail.count)

        // Context recovered from before the watermark.
        #expect(second.events.allSatisfy { $0.nativeSessionID == "5d2c1a0e-4f3b-4c2d-9e8f-7a6b5c4d3e2f" })

        // g1's re-append carries its tokens again; the claim was replayed, so this carrier
        // is stored non-authoritative and the batch says so.
        #expect(Self.diagnostic(second, "gemini_token_reappend")?.detail.hasPrefix("1 ") == true)
        #expect(second.events.filter(\.usageAuthoritative).count == 4)
        let g1Key = first.events.first(where: \.usageAuthoritative)?.dedupeKey
        #expect(second.events.contains { $0.dedupeKey == g1Key && !$0.usageAuthoritative && $0.tokIn != nil })
        #expect(!second.events.contains { $0.dedupeKey == g1Key && $0.usageAuthoritative })

        // The two batches together are the whole fixture, and the total does not move.
        // g1's TEXT facet is emitted again by the second batch (facet state is not replayed)
        // under the same nativeEventID, hence the same eventUID: that is the row the store's
        // `INSERT OR IGNORE` on event_uid suppresses, and the only duplicate in the union.
        let all = first.events + second.events
        #expect(all.filter { $0.kind == .prompt }.count == 2)
        #expect(all.filter { $0.kind == .toolUse }.count == 9)
        #expect(all.filter { $0.kind == .toolResult }.count == 9)
        #expect(all.filter { $0.kind == .assistantMessage }.count == 5)
        #expect(Set(all.map(\.eventUID)).count == all.count - 1)
        let g1Text = all.filter { $0.nativeEventID == "g1#text" }
        #expect(g1Text.count == 2 && Set(g1Text.map(\.eventUID)).count == 1)
        #expect(g1Text.allSatisfy { $0.tokIn == nil && !$0.usageAuthoritative })
        #expect(all.filter(\.usageAuthoritative).count == 5)
        #expect(TokenAccountant.ledger(all, harness: .geminiCLI).buckets.displayTotal == 65_882)
        #expect(TokenAccountant.naiveTotal(all).displayTotal == 75_042)

        // Nothing more to read: the third pass is a skip.
        let third = try tree.parser.parse(source: source, from: second.watermark)
        #expect(third.events.isEmpty)
        #expect(third.watermark == second.watermark)
    }

    /// Recordings are appended to while being read; the last line is routinely half-written.
    @Test func partialTrailingLineIsNeverConsumed() throws {
        let lines = try Self.fixtureLines()
        let tree = try Tree.make()
        defer { tree.remove() }

        try tree.write(lines: lines, terminated: false)
        let source = try tree.source()
        let first = try tree.parser.parseAll(source: source)

        // The unterminated final `$set` (the one carrying the summary) is not a row yet, and
        // the watermark stops at the start of that line rather than at end of file.
        #expect(first.watermark.lineCount == lines.count - 1)
        #expect(first.watermark.byteOffset == tree.size - lines[lines.count - 1].utf8.count)
        #expect(Self.count(first, .title) == 0)
        // 37 rows for the whole fixture (see `reproducesTheReferenceCounts`), minus the title.
        #expect(first.events.count == 36)

        // The writer finishes the line. Whether the next read resumes or restarts (the file
        // is smaller than the head hash window, so the hash moves), the title is seen once.
        try tree.append(Data("\n".utf8))
        let second = try tree.parser.parse(source: source, from: first.watermark)
        #expect(Self.count(second, .title) == 1)
        #expect(second.watermark.byteOffset == tree.size)
        #expect(second.watermark.lineCount == lines.count)
    }

    // MARK: - Rules ported from the reference

    @Test func rewindIsRecordedNotApplied() throws {
        let r = try Self.parse(lines: [
            Self.meta,
            Self.user("u1", "2025-11-04T09:00:01.000Z", "fix it"),
            Self.gemini("g1", "2025-11-04T09:00:05.000Z", "Pushing to main.", tokens: (1000, 10, 400, 5)),
            #"{"$rewindTo": "g1"}"#,
            // An id never seen: the reader would clear every message. Counted apart.
            #"{"$rewindTo": "ghost"}"#,
            Self.gemini("g2", "2025-11-04T09:00:09.000Z", "Committing instead.", tokens: (1200, 12, 400, 6)),
        ])
        #expect(Self.kinds(r) == [.noise, .prompt, .assistantMessage, .noise, .noise, .noise, .assistantMessage, .noise])
        #expect(Self.diagnostic(r, "gemini_rewind_seen")?.detail.hasPrefix("2 ") == true)
        #expect(Self.diagnostic(r, "gemini_rewind_unknown_id")?.detail.hasPrefix("1 ") == true)
        // g1's rows stay, tokens included: 1000 + 10 + 5 + 1200 + 12 + 6.
        #expect(r.events.filter(\.usageAuthoritative).count == 2)
        #expect(TokenAccountant.ledger(r.events, harness: .geminiCLI).buckets.displayTotal == 2233)
    }

    @Test func promptRulesFollowTheReference() throws {
        let r = try Self.parse(lines: [
            Self.meta,
            Self.user("u0", "2025-11-04T09:00:01.000Z", "/model"),
            Self.user("u1", "2025-11-04T09:00:02.000Z", "?"),
            Self.user("u2", "2025-11-04T09:00:03.000Z", #"<session_context>\nresumed\n</session_context>"#),
            Self.user("u3", "2025-11-04T09:00:04.000Z", #"<hook_context>x</hook_context>"#),
            Self.user("u4", "2025-11-04T09:00:05.000Z", "   "),
            // Leading whitespace is trimmed before the prefix test, as in the reference.
            Self.user("u5", "2025-11-04T09:00:06.000Z", "  /help"),
            // A part list with an @file expansion: displayContent decides.
            #"{"id": "u6", "timestamp": "2025-11-04T09:00:07.000Z", "type": "user", "content": [{"text": "--- Content from referenced files ---\nx\n--- End ---\n@a.py fix"}], "displayContent": [{"text": "@a.py fix"}]}"#,
            // displayContent that is itself a slash command is ignored even though content is not.
            #"{"id": "u7", "timestamp": "2025-11-04T09:00:08.000Z", "type": "user", "content": [{"text": "expanded"}], "displayContent": "/memory show"}"#,
            Self.user("u8", "2025-11-04T09:00:09.000Z", "now commit"),
            // The same prompt id appended again is one prompt.
            Self.user("u8", "2025-11-04T09:00:09.000Z", "now commit"),
        ])
        #expect(Self.kinds(r) == [.noise, .noise, .noise, .noise, .noise, .noise, .noise, .prompt, .noise, .prompt, .noise])
        #expect(Self.diagnostic(r, "gemini_prompt_ignored")?.detail.hasPrefix("7 ") == true)
        #expect(Self.diagnostic(r, "gemini_prompt_from_display_content")?.detail.hasPrefix("2 ") == true)
        #expect(Self.diagnostic(r, "gemini_message_rewrite")?.detail.hasPrefix("1 ") == true)
        #expect(r.events.filter { $0.kind.isPresence }.count == 2)
        // Prompt text never reaches the event.
        #expect(r.events.allSatisfy { $0.title == nil && $0.extra == nil })
        let prompts = r.events.filter { $0.kind == .prompt }.sorted { $0.ordinal < $1.ordinal }
        #expect(prompts.map(\.nativeEventID) == ["u6#prompt", "u8#prompt"])
    }

    @Test func toolShapesAreRecognised() throws {
        let r = try Self.parse(lines: [
            Self.meta,
            // The tool response recorded FIRST as a user message (c9 failed), then the
            // ToolCallRecord for it: one call, one result, the error flagged either way.
            #"{"id": "u1", "timestamp": "2025-11-04T09:00:10.000Z", "type": "user", "content": [{"functionResponse": {"id": "c9", "name": "replace", "response": {"error": "no match"}}}]}"#,
            Self.gemini("g1", "2025-11-04T09:00:11.000Z", "", extra: #", "toolCalls": [{"id": "c9", "name": "replace", "args": {"absolute_path": "/p/a.py", "old_string": "a\nb\n", "new_string": "a\nc\nd\n"}, "result": [{"functionResponse": {"id": "c9", "name": "replace", "response": {"error": "no match"}}}], "status": "error", "timestamp": "2025-11-04T09:00:10.500Z"}]"#),
            // A call still running has no result yet; a cancelled one is not an interrupt.
            Self.gemini("g2", "2025-11-04T09:00:12.000Z", "", extra: #", "toolCalls": [{"id": "c10", "name": "run_shell_command", "args": {"command": "pytest -q"}, "status": "executing", "timestamp": "2025-11-04T09:00:12.000Z"}, {"id": "c11", "name": "write_file", "args": {"file_path": "/p/new.txt", "content": "one\ntwo"}, "status": "cancelled", "timestamp": "2025-11-04T09:00:12.500Z"}, {"id": "c12", "name": "glob", "args": {"pattern": "**/*.py"}, "status": "made_up", "timestamp": "2025-11-04T09:00:12.600Z", "result": null}]"#),
            // The same message re-appended once c10 completed: only the new result is a row.
            Self.gemini("g2", "2025-11-04T09:00:12.000Z", "", extra: #", "toolCalls": [{"id": "c10", "name": "run_shell_command", "args": {"command": "pytest -q"}, "result": [{"functionResponse": {"id": "c10", "name": "run_shell_command", "response": {"output": "3 passed"}}}], "status": "success", "timestamp": "2025-11-04T09:00:14.000Z"}, {"id": "c11", "name": "write_file", "args": {"file_path": "/p/new.txt", "content": "one\ntwo"}, "status": "cancelled", "timestamp": "2025-11-04T09:00:12.500Z"}, {"id": "c12", "name": "glob", "args": {"pattern": "**/*.py"}, "status": "made_up", "timestamp": "2025-11-04T09:00:12.600Z", "result": null}]"#),
            // A functionCall part with no record and no id: taken from the part, keyed by position.
            #"{"id": "g3", "timestamp": "2025-11-04T09:00:15.000Z", "type": "gemini", "content": [{"functionCall": {"name": "read_file", "args": {"path": "/p/b.py"}}}], "model": "gemini-2.5-flash"}"#,
            // A toolCalls entry that is not an object, and a part with no known key.
            #"{"id": "g4", "timestamp": "2025-11-04T09:00:16.000Z", "type": "gemini", "content": [{"mystery": 1}, {"text": "done"}], "model": "gemini-2.5-flash", "toolCalls": ["not-an-object"]}"#,
        ])
        let byOrdinal = r.events  // file order; several share a line
        #expect(byOrdinal.map(\.kind) == [
            .noise,
            .toolResult,                       // u1: c9's response, recorded first
            .toolUse,                          // g1: c9's call (its result already emitted)
            .toolUse, .toolUse, .toolUse,      // g2: c10 running, c11 cancelled, c12 unknown status
            .toolResult,                       // g2 again: only c10's completion is new
            .toolUse,                          // g3: from the functionCall part
            .assistantMessage,                 // g4
        ])

        let c9 = byOrdinal[1]
        #expect(c9.toolID == "c9" && c9.toolName == "replace" && Self.isError(c9))
        // c9 failed: the call names no file and scores no lines (it asked for +2/-1).
        let c9Call = byOrdinal[2]
        #expect(c9Call.toolID == "c9" && c9Call.targetPath == nil)
        #expect(c9Call.linesAdded == nil && c9Call.linesRemoved == nil)
        #expect(c9Call.ts == ISO8601.seconds("2025-11-04T09:00:10.500Z"))

        // c11 was cancelled: two lines asked for, none written, no file.
        #expect(byOrdinal[4].toolName == "write_file" && byOrdinal[4].linesAdded == nil && byOrdinal[4].targetPath == nil)
        #expect(Self.diagnostic(r, "gemini_tool_credit_withheld")?.detail.hasPrefix("2 ") == true)
        #expect(byOrdinal[6].toolID == "c10" && byOrdinal[6].toolName == "run_shell_command" && !Self.isError(byOrdinal[6]))
        #expect(byOrdinal[6].ts == ISO8601.seconds("2025-11-04T09:00:14.000Z"))
        #expect(byOrdinal[7].toolName == "read_file" && byOrdinal[7].targetPath == "/p/b.py" && byOrdinal[7].toolID == nil)
        #expect(byOrdinal[7].model == "gemini-2.5-flash")
        #expect(byOrdinal[8].model == "gemini-2.5-flash")

        #expect(Self.count(r, .interrupt) == 0)
        #expect(Self.diagnostic(r, "gemini_result_error")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_tool_status_incomplete")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_tool_status_cancelled")?.detail.hasPrefix("2 ") == true)
        #expect(Self.diagnostic(r, "gemini_tool_status_unknown")?.detail.hasPrefix("2 ") == true)
        #expect(Self.diagnostic(r, "gemini_tool_from_function_call_part")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_tool_call_not_object")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_unknown_part_shape")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_message_rewrite")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_user_tool_response_records")?.detail.hasPrefix("1 ") == true)
    }

    /// VERIFIED tools/shell.ts: a non-zero exit sets no `error`, so tool-executor records the
    /// call as `success`; the exit status is an `Exit Code: N` line in the output.
    @Test func shellExitCodeIsAnErrorDespiteSuccess() throws {
        let exit2 = #"<untrusted_context>\nOutput: make: *** No rule to make target 'lint'.  Stop.\nExit Code: 2\nProcess Group PGID: 4242\n</untrusted_context>"#
        let r = try Self.parse(lines: [
            Self.meta,
            // c20's response recorded FIRST as a user message: flagged on that path too.
            #"{"id": "u1", "timestamp": "2025-11-04T09:00:09.000Z", "type": "user", "content": [{"functionResponse": {"id": "c20", "name": "run_shell_command", "response": {"output": "\#(exit2)"}}}]}"#,
            Self.gemini("g1", "2025-11-04T09:00:10.000Z", "", extra: #", "toolCalls": [{"id": "c20", "name": "run_shell_command", "args": {"command": "make lint"}, "result": [{"functionResponse": {"id": "c20", "name": "run_shell_command", "response": {"output": "\#(exit2)"}}}], "status": "success", "timestamp": "2025-11-04T09:00:09.000Z"}, {"id": "c21", "name": "run_shell_command", "args": {"command": "make lint"}, "result": [{"functionResponse": {"id": "c21", "name": "run_shell_command", "response": {"output": "\#(exit2)"}}}], "status": "success", "timestamp": "2025-11-04T09:00:11.000Z"}, {"id": "c22", "name": "run_shell_command", "args": {"command": "true"}, "result": [{"functionResponse": {"id": "c22", "name": "run_shell_command", "response": {"output": "Output: (empty)\nExit Code: 0\nProcess Group PGID: 1"}}}], "status": "success", "timestamp": "2025-11-04T09:00:12.000Z"}, {"id": "c23", "name": "read_file", "args": {"file_path": "/p/notes.md"}, "result": [{"functionResponse": {"id": "c23", "name": "read_file", "response": {"output": "Exit Code: 1 means the linter found something\n"}}}], "status": "success", "timestamp": "2025-11-04T09:00:13.000Z"}, {"id": "c24", "name": "run_shell_command", "args": {"command": "pytest -q"}, "result": [{"functionResponse": {"id": "c24", "name": "run_shell_command", "response": {"output": "F.\nFAILED tests/test_x.py::test_y - AssertionError\n1 failed\n"}}}], "status": "success", "timestamp": "2025-11-04T09:00:14.000Z"}]"#),
        ])
        let results = r.events.filter { $0.kind == .toolResult }
        #expect(results.map(\.toolID) == ["c20", "c21", "c22", "c23", "c24"])
        // c20 via the user path, c21 via the record. A literal `Exit Code: 0` is never
        // written by the CLI and is not an error; the same words inside a FILE are not a
        // shell exit; and c24 — the reference's `_looks_like_error` case — is pinned as NOT
        // an error here (see the suite note).
        #expect(results.map(Self.isError) == [true, true, false, false, false])
        #expect(Self.diagnostic(r, "gemini_result_error")?.detail.hasPrefix("2 ") == true)
        #expect(Self.diagnostic(r, "gemini_shell_exit_code_error")?.detail.hasPrefix("2 ") == true)
        // A shell command still claims no file or lines, failed or not.
        #expect(r.events.filter { $0.kind == .toolUse }.allSatisfy { $0.linesAdded == nil })
        #expect(Self.diagnostic(r, "gemini_tool_credit_withheld") == nil)
    }

    @Test func unknownShapesAreRecordedNotDropped() throws {
        let r = try Self.parse(lines: [
            Self.meta,
            #"{"beam": 1}"#,
            #"{"id": "x1", "timestamp": "2025-11-04T09:00:01.000Z", "type": "hologram", "content": "?"}"#,
            #"{"id": "i1", "timestamp": "2025-11-04T09:00:02.000Z", "type": "info", "content": "Resumed."}"#,
            #"{"id": "e1", "timestamp": "2025-11-04T09:00:03.000Z", "type": "error", "content": "429"}"#,
            #"{"id": "w1", "timestamp": "2025-11-04T09:00:04.000Z", "type": "warning", "content": "slow"}"#,
            "{not json",
            "[1, 2, 3]",
            // `$set.messages` replaces the list; its gemini message claims its tokens once.
            #"{"$set": {"lastUpdated": "2025-11-04T09:00:05.000Z", "messages": [{"id": "g1", "timestamp": "2025-11-04T09:00:05.000Z", "type": "gemini", "content": "hi", "model": "gemini-2.5-pro", "tokens": {"input": 100, "output": 5, "cached": 20, "thoughts": 0, "tool": 3, "total": 108}}]}}"#,
            // A user record carrying tokens is counted, never summed.
            #"{"id": "u1", "timestamp": "2025-11-04T09:00:06.000Z", "type": "user", "content": "go", "tokens": {"input": 999, "output": 0, "cached": 0, "total": 999}}"#,
        ])
        // The `$set` line yields the rebuilt message's text, its token carrier, and the
        // `$set` row itself.
        #expect(Self.kinds(r) == [.noise, .unknown, .unknown, .noise, .noise, .noise, .assistantMessage, .noise, .noise, .prompt])
        #expect(Self.diagnostic(r, "unknown_record_shape")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_message_type_unknown")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_message_type_info")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_message_type_error")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_message_type_warning")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "malformed_json_line")?.detail.hasPrefix("2 ") == true)
        #expect(Self.diagnostic(r, "gemini_set_messages_rebuild")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_tokens_on_non_gemini")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_tool_tokens_seen")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_token_total_mismatch") == nil)

        let carriers = r.events.filter(\.usageAuthoritative)
        #expect(carriers.count == 1)
        #expect(carriers.first?.tokIn == 83 && carriers.first?.tokCacheRead == 20 && carriers.first?.tokOut == 5)
        #expect(TokenAccountant.ledger(r.events, harness: .geminiCLI).buckets.displayTotal == 108)
        // The malformed lines still advanced the watermark: 10 lines, 10 events.
        #expect(r.watermark.lineCount == 10)
        #expect(r.events.count == 10)
    }

    @Test func sidecarAttachesToTheParentSession() throws {
        let tree = try Tree.make()
        defer { tree.remove() }
        try tree.write(try Self.fixtureData())

        let parent = "5d2c1a0e-4f3b-4c2d-9e8f-7a6b5c4d3e2f"
        let dir = tree.root + "/" + Tree.project + "/chats/" + parent
        try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        let sub = [
            #"{"sessionId": "sub-1", "projectHash": "abc123", "startTime": "2025-11-04T09:00:30.000Z", "lastUpdated": "2025-11-04T09:00:30.000Z", "kind": "subagent", "directories": ["/Users/dev/proj"]}"#,
            // The parent model's instruction, recorded exactly like a typed prompt.
            Self.user("su0", "2025-11-04T09:00:30.500Z", "Find every caller of run() and report back."),
            Self.user("su1", "2025-11-04T09:00:31.000Z", "<hook_context>task</hook_context>"),
            Self.gemini("sg1", "2025-11-04T09:00:35.000Z", "Looked it up.", tokens: (500, 20, 100, 0)),
        ]
        try Data((sub.joined(separator: "\n") + "\n").utf8).write(to: URL(fileURLWithPath: dir + "/sub-1.jsonl"))

        let sources = try tree.parser.discover()
        guard let side = sources.first(where: \.isSidecar) else {
            Issue.record("sidecar not discovered")
            return
        }
        let r = try tree.parser.parseAll(source: side)

        // Every row belongs to the PARENT session, is marked as a sidechain, and names the
        // subagent as its agent. The subagent's own id never becomes a session key.
        #expect(!r.events.isEmpty)
        #expect(r.events.allSatisfy { $0.nativeSessionID == parent && $0.isSidechain && $0.agentID == "sub-1" })
        #expect(r.events.allSatisfy { $0.cwd == "/Users/dev/proj" })
        #expect(Self.diagnostic(r, "gemini_cwd_from_directories")?.detail.hasPrefix("1 ") == true)
        // Nobody typed in a sidecar: the instruction is counted, the injection ignored.
        #expect(Self.count(r, .prompt) == 0)
        #expect(Self.diagnostic(r, "gemini_prompt_agent_authored")?.detail.hasPrefix("1 ") == true)
        #expect(Self.diagnostic(r, "gemini_prompt_ignored")?.detail.hasPrefix("1 ") == true)
        #expect(Self.count(r, .assistantMessage) == 1)

        // Its usage is claimed like any other (so a naive reader could sum it) and skipped
        // by the ledger, exactly as a Claude Code subagent's is.
        #expect(r.events.filter(\.usageAuthoritative).count == 1)
        #expect(TokenAccountant.ledger(r.events, harness: .geminiCLI).buckets.displayTotal == 0)

        // Pooled with the root recording, the sidecar's rows join the one session.
        let root = try tree.parser.parseAll(source: try tree.source())
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York")!
        let sessions = Sessionizer.sessions(
            from: root.events + r.events, options: Sessionizer.Options(pooling: .nativeSession, calendar: cal))
        #expect(sessions.count == 1)
        #expect(sessions.first?.promptCount == 2)
    }

    /// The generator's `kind: "subagent"` recording, placed where the CLI writes one.
    @Test func subagentFixtureHasNoPrompts() throws {
        let exp = try Self.expected()
        guard let dir = Self.fixtureDirectory else { throw NSError(domain: "GeminiParserTests", code: 1) }
        let data = try Data(contentsOf: dir.appendingPathComponent(exp.subagent.file))

        let tree = try Tree.make()
        defer { tree.remove() }
        try tree.write(try Self.fixtureData())
        let parent = exp.meta.sessionId
        let subDir = tree.root + "/" + Tree.project + "/chats/" + parent
        try FileManager.default.createDirectory(atPath: subDir, withIntermediateDirectories: true)
        try data.write(to: URL(fileURLWithPath: subDir + "/" + exp.subagent.file))

        guard let side = try tree.parser.discover().first(where: \.isSidecar) else {
            Issue.record("subagent fixture not discovered")
            return
        }
        let r = try tree.parser.parseAll(source: side)

        #expect(r.watermark.lineCount == exp.subagent.diagnostics.lines)
        #expect(Self.count(r, .prompt) == exp.subagent.stats.promptsSent)
        #expect(exp.subagent.stats.promptsSent == 0)
        #expect(Self.diagnostic(r, "gemini_prompt_agent_authored")?.detail.hasPrefix("1 ") == true)
        #expect(Self.count(r, .toolUse) == exp.subagent.stats.toolCalls)
        var mix: [String: Int] = [:]
        for e in r.events where e.kind == .toolUse { mix[e.toolName ?? "?", default: 0] += 1 }
        #expect(mix == exp.subagent.stats.toolMix)
        #expect(Self.count(r, .assistantMessage) == exp.subagent.stats.repliesReceived)
        #expect(Self.count(r, .toolResult) == exp.subagent.stats.toolCalls)
        #expect(r.events.filter { $0.kind == .toolResult }.allSatisfy { !Self.isError($0) })
        #expect(r.events.allSatisfy { $0.nativeSessionID == parent && $0.isSidechain })
        #expect(r.events.allSatisfy { $0.cwd == "/Users/dev/proj" })
        #expect(r.events.filter(\.usageAuthoritative).count == 2)
        #expect(!r.events.contains { $0.kind.isPresence })
    }

    @Test func lineCreditHelpersMatchTheReference() {
        #expect(GeminiParser.writtenLines("") == 0)
        #expect(GeminiParser.writtenLines("\n") == 1)
        #expect(GeminiParser.writtenLines("a\nb\n") == 2)
        #expect(GeminiParser.writtenLines("a\nb") == 2)
        #expect(GeminiParser.writtenLines("import subprocess\n\n\ndef run(args):\n    return 1\n") == 5)

        #expect(Self.delta("set -e\n", "set -euo pipefail\nDRY_RUN=0\nif x; then y; fi\n") == [3, 1])
        #expect(Self.delta("expect 1", "expect 0") == [1, 1])
        #expect(Self.delta("a\nb\nc\n", "a\nx\nc\n") == [1, 1])
        #expect(Self.delta("a\nb\n", "a\nb\nc\n") == [1, 0])
        #expect(Self.delta("a\nb\nc\n", "a\nc\n") == [0, 1])
        #expect(Self.delta("", "x\ny\n") == [2, 0])
        #expect(Self.delta("x\n", "") == [0, 1])
        #expect(Self.delta("same\n", "same\n") == [0, 0])
    }

    @Test func harnessFlagsMatchTheParser() {
        #expect(Harness.geminiCLI.rawValue == "gemini_cli")
        #expect(Harness.geminiCLI.isImplemented)
        #expect(Harness.geminiCLI.reportsTokens)
        #expect(Harness.geminiCLI.reportsModel)
        #expect(Harness.geminiCLI.displayName == "Gemini CLI")
        #expect(GeminiParser().harness == .geminiCLI)
        #expect(GeminiParser().parserVersion == 2)

        // Cline: contract and enum only, no parser in this build.
        #expect(Harness.cline.rawValue == "cline")
        #expect(!Harness.cline.isImplemented)
        #expect(Harness.cline.displayName == "Cline")

        // Every Harness case is a legal wire value, and vice versa — a case that is not in
        // the contract is a 422 on the user's first sync; a value that is not a case is a
        // decode failure on a session the user can see on their phone. The set stays
        // BIDIRECTIONAL even for harnesses this build has no parser for: `opencode` and
        // `aider` are uploaded by `python -m capture` (capture/harnesses.py) and arrive
        // here through the same wire, so `isImplemented` is what says the Mac cannot read
        // them, not a missing case.
        let wire = Set(UploadContract.enumValues["harness"] ?? [])
        #expect(wire == Set(Harness.allCases.map(\.rawValue)))
        #expect(wire.contains("gemini_cli") && wire.contains("cline"))
        #expect(!Harness.opencode.isImplemented && !Harness.aider.isImplemented)
        #expect(!Harness.opencode.reportsTokens && !Harness.aider.reportsTokens)
    }
}
