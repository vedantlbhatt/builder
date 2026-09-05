import BuilderAnalysis
import BuilderIngest
import BuilderModel
import BuilderParse
import BuilderSchema
import BuilderSQLite
import Foundation
import Testing

/// The Swift digest against the Python reference (`analysis/digest.py`).
///
/// The fixture transcript is `spec/fixtures/boundaries/remote_sdk_prompts.jsonl`, run
/// through the REAL parser and sessionizer for its window, then digested from the file
/// the way the agent does. `Fixtures/remote_sdk_prompts.digest.txt` is what
/// `python -m analysis digest` printed for the same file; the two must be byte-identical,
/// because `digest_hash` is how the server tells a fresh analysis from a replayed one.
@Suite("Session digest — parity with analysis/digest.py")
struct DigestTests {

    static var fixtureTranscript: URL? {
        BoundaryFixtureTests.fixtureDirectory?.appendingPathComponent("remote_sdk_prompts.jsonl")
    }

    /// Next to this file, copied into the test bundle by `.copy("Fixtures")`; located the
    /// way the strip golden tests locate theirs.
    static var goldenDigest: URL {
        URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("Fixtures/remote_sdk_prompts.digest.txt")
    }

    /// The fixture's one session, digested from its file over its own window.
    static func fixtureDigest() throws -> SessionDigest.Output {
        let sessions = try BoundaryFixtureTests.sessions(for: "remote_sdk_prompts")
        let s = try #require(sessions.first)
        #expect(sessions.count == 1)
        let path = try #require(Self.fixtureTranscript).path
        return try SessionDigest.build(
            harness: .claudeCode, transcripts: [path], start: s.startedAt, end: s.endedAt,
            meta: SessionDigest.Meta())
    }

    // MARK: - The fixture

    @Test func headerCountsThePromptsTheParserCounts() throws {
        let d = try Self.fixtureDigest()
        let lines = d.text.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        #expect(lines.first == "# SESSION DIGEST")
        #expect(lines.contains("harness: claude_code"))
        #expect(d.text.contains("prompts sent: 3"))
        #expect(d.text.contains("interrupts: 1"))
        #expect(d.text.contains("tool calls: 54"))
        #expect(d.coverage == 1.0)
        #expect(d.events == 58)
        #expect(d.stats.promptsSent == 3)
        #expect(d.stats.interrupts == 1)
        #expect(d.stats.toolCalls == 54)
        #expect(d.stats.toolMix.count == 1 && d.stats.toolMix.first?.0 == "Bash" && d.stats.toolMix.first?.1 == 54)
    }

    @Test func everyPromptIsPresentVerbatimAndInOrder() throws {
        let d = try Self.fixtureDigest()
        let lines = d.text.split(separator: "\n").map(String.init)
        let prompts = lines.filter { $0.contains(" PROMPT: ") }
        #expect(
            prompts == [
                "[0] +  0.0m PROMPT: \"hello\"",
                "[9] +  1.1m PROMPT: \"do it\"",
                "[49] +  6.2m PROMPT: \"no, the other one\"",
            ])
        // Ordinals ascend — the model cites them as evidence.
        let ordinals = prompts.compactMap { line -> Int? in
            guard let close = line.firstIndex(of: "]") else { return nil }
            return Int(line[line.index(after: line.startIndex)..<close])
        }
        #expect(ordinals == ordinals.sorted() && ordinals.count == 3)
        #expect(lines.contains("[48] +  6.2m INTERRUPT (human stopped the agent)"))
        // A Bash call with an empty command renders as the bare tool name: the trailing
        // ": " is stripped, exactly as Python's rstrip(": ") does.
        #expect(lines.contains("[1] +  0.1m Bash"))
    }

    @Test func fixtureDigestIsByteIdenticalToThePythonReference() throws {
        let golden = try String(contentsOf: Self.goldenDigest, encoding: .utf8)
        let d = try Self.fixtureDigest()
        #expect(d.text == golden)
        #expect(d.hash == "235a4efeec141eca86d8197f118e5f31fb81993b5d89ee5e2fab4586d52e67ce")
        #expect(d.text.unicodeScalars.count == 1376)
    }

    // MARK: - Masking

    @Test func maskRedactsSecretsBeforeAnythingIsWritten() {
        #expect(SessionDigest.mask("key sk-abcdefghijklmnopqrstuvwx here") == "key [redacted] here")
        #expect(SessionDigest.mask("mail me at someone@example.com") == "mail me at [email]")
        #expect(SessionDigest.mask("password=hunter2222 done") == "[redacted] done")
        #expect(SessionDigest.mask("token: eyJabcdefghijk.abcdefghijkl.abcdefghijklm") == "[redacted]")
        #expect(SessionDigest.mask("AKIAABCDEFGHIJKLMNOP") == "[redacted]")
        #expect(SessionDigest.mask("ghp_" + String(repeating: "a", count: 36)) == "[redacted]")
        // Short strings that merely look like a prefix are left alone.
        #expect(SessionDigest.mask("sk-short") == "sk-short")
        #expect(SessionDigest.mask("plain text, no secrets") == "plain text, no secrets")
    }

    @Test func maskedPromptSurvivesIntoTheDigest() throws {
        let events = [
            SessionDigest.Event(
                ts: 0, kind: .prompt,
                text: SessionDigest.mask("use sk-abcdefghijklmnopqrstuvwx for the call")),
        ]
        let d = SessionDigest.Output(events: events, meta: SessionDigest.Meta())
        #expect(d.text.contains("PROMPT: \"use [redacted] for the call\""))
        #expect(!d.text.contains("sk-abcdefghijklmnopqrstuvwx"))
    }

    // MARK: - Error detection and shell writes

    @Test func onlyShapesThatMeanTheCommandFailedAreErrors() {
        #expect(SessionDigest.looksLikeError("Traceback (most recent call last):\n  File x"))
        #expect(SessionDigest.looksLikeError("fatal: not a git repository"))
        #expect(SessionDigest.looksLikeError("ok\nExit code 2"))
        #expect(SessionDigest.looksLikeError("ValueError: bad thing"))
        // Output that merely mentions an error string is not a failure.
        #expect(!SessionDigest.looksLikeError("ls: no such file or directory"))
        #expect(!SessionDigest.looksLikeError("test_failed_login PASSED"))
        #expect(!SessionDigest.looksLikeError("Exit code 0"))
    }

    @Test func heredocAndSedWritesAreCredited() {
        let heredoc = "cat > src/cache.ts <<'EOF'\nline1\nline2\nline3\nEOF"
        let effect = SessionDigest.bashFileEffect(heredoc)
        #expect(effect.path == "src/cache.ts")
        #expect(effect.approx == 3)
        let sed = SessionDigest.bashFileEffect("sed -i '' 's/a/b/' lib/util.py")
        #expect(sed.path == "lib/util.py")
        #expect(sed.approx == nil)
        let plain = SessionDigest.bashFileEffect("swift test")
        #expect(plain.path == nil && plain.approx == nil)
    }

    // MARK: - The budget

    /// A large transcript, generated identically by the Python that produced the expected
    /// hash below (600 assistant turns, 600 Bash calls, six prompts; ~500 KB on disk).
    static func syntheticTranscript() throws -> String {
        let dir = NSTemporaryDirectory() + "builder-digest-\(UUID().uuidString)"
        try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        let path = dir + "/synthetic.jsonl"
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        f.timeZone = TimeZone(identifier: "UTC")
        func iso(_ ts: Double) -> String { f.string(from: Date(timeIntervalSince1970: ts)) }
        let t0 = 1_773_140_400.0
        var out = ""
        let filler = String(repeating: "x", count: 250)
        for i in 0..<600 {
            let ts = t0 + Double(i) * 20
            let n4 = String(format: "%04d", i)
            if i % 100 == 0 {
                out +=
                    "{\"type\":\"user\",\"uuid\":\"p\(i)\",\"sessionId\":\"s\",\"timestamp\":\"\(iso(ts))\","
                    + "\"promptSource\":\"typed\",\"message\":{\"role\":\"user\","
                    + "\"content\":\"prompt number \(i): please refactor module \(i) carefully\"}}\n"
            }
            out +=
                "{\"type\":\"assistant\",\"uuid\":\"a\(i)\",\"sessionId\":\"s\",\"timestamp\":\"\(iso(ts + 5))\","
                + "\"message\":{\"role\":\"assistant\",\"id\":\"msg_a\(i)\",\"model\":\"claude-sonnet-5\","
                + "\"content\":[{\"type\":\"text\",\"text\":\"Working on step \(n4). \(filler)\"}],"
                + "\"usage\":{\"input_tokens\":1,\"output_tokens\":1}}}\n"
            out +=
                "{\"type\":\"assistant\",\"uuid\":\"t\(i)\",\"sessionId\":\"s\",\"timestamp\":\"\(iso(ts + 10))\","
                + "\"message\":{\"role\":\"assistant\",\"id\":\"msg_t\(i)\",\"model\":\"claude-sonnet-5\","
                + "\"content\":[{\"type\":\"tool_use\",\"id\":\"tu_\(n4)\",\"name\":\"Bash\","
                + "\"input\":{\"command\":\"swift build --target Mod\(n4) && echo done\"}}],"
                + "\"usage\":{\"input_tokens\":1,\"output_tokens\":1}}}\n"
        }
        try out.write(toFile: path, atomically: true, encoding: .utf8)
        return path
    }

    @Test func largeSessionDegradesUnderTheBudgetAndSaysSo() throws {
        let path = try Self.syntheticTranscript()
        defer { try? FileManager.default.removeItem(atPath: (path as NSString).deletingLastPathComponent) }
        let d = try SessionDigest.build(
            harness: .claudeCode, transcripts: [path], start: nil, end: nil,
            meta: SessionDigest.Meta(harness: "claude_code"))

        #expect(d.events == 1206)
        #expect(d.coverage < 1)
        #expect(d.text.contains("(… 620 of 1206 timeline lines omitted to fit; every prompt and error is present. coverage=0.49)"))
        // Every prompt survives thinning.
        for i in stride(from: 0, to: 600, by: 100) {
            #expect(d.text.contains("PROMPT: \"prompt number \(i): please refactor module \(i) carefully\""))
        }
        // The body is thinned to the budget; the closing note and the level-2 rounding
        // slack sit outside the accounting in the reference too (MEASURED: 60,531 chars
        // for this input from analysis/digest.py against a 60,000 budget).
        #expect(d.text.unicodeScalars.count <= SessionDigest.defaultBudget + 1_000)
        #expect(d.text.unicodeScalars.count > SessionDigest.defaultBudget / 2)

        // And the same bytes as the Python reference produced for the same records.
        #expect(d.coverage == 0.486)
        #expect(d.text.unicodeScalars.count == 60531)
        #expect(d.hash == "0615d4abbe6e0823833030e7078c91d6d38c4fe7f6722865c9136f6ebb4e7530")
    }

    // MARK: - Root transcripts only

    /// A subagent sidecar is ingested with its parent's cwd, repo and timestamps, so the
    /// scheduler's pool predicate alone would hand the digest two files where the Python
    /// reference reads one — and the two would never again agree on `digest_hash`.
    ///
    /// The fixture tree is exactly what `~/.claude/projects` holds: the root at
    /// `<projectdir>/<uuid>.jsonl` and a sidecar at `<projectdir>/<uuid>/subagents/agent-x.jsonl`,
    /// ingested through the real coordinator into a scratch store, sessionized with the
    /// deriver's pooling, and resolved through `AnalysisJob.make` — the scheduler's path.
    @Test func sidecarContributesNothingToTheDigest() throws {
        let tmp = NSTemporaryDirectory() + "builder-digest-sidecar-\(UUID().uuidString)"
        let proj = tmp + "/projects/proj"
        let sidecarDir = proj + "/00000000-0000-4000-8000-000000000001/subagents"
        try FileManager.default.createDirectory(atPath: sidecarDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(atPath: tmp) }

        let rootPath = proj + "/00000000-0000-4000-8000-000000000001.jsonl"
        let sidecarPath = sidecarDir + "/agent-x.jsonl"
        try FileManager.default.copyItem(atPath: try #require(Self.fixtureTranscript).path, toPath: rootPath)
        // Inside the root's window (which opens 13:00:00Z), same cwd, flagged the way a
        // real subagent record is; a Read call and a prompt the fixture does not have.
        let sidecar = """
            {"type":"user","uuid":"side-0001","parentUuid":null,"sessionId":"00000000-0000-4000-8000-000000000001","timestamp":"2026-03-10T13:00:30.000Z","cwd":"/Users/dev/proj","version":"2.1.0","isSidechain":true,"agentId":"agent-x","message":{"role":"user","content":"SIDECAR-PROMPT-MUST-NOT-APPEAR"},"promptSource":"typed"}
            {"type":"assistant","uuid":"side-0002","parentUuid":"side-0001","sessionId":"00000000-0000-4000-8000-000000000001","timestamp":"2026-03-10T13:00:31.000Z","cwd":"/Users/dev/proj","version":"2.1.0","isSidechain":true,"agentId":"agent-x","message":{"role":"assistant","model":"claude-sonnet-5","id":"msg_side_2","content":[{"type":"tool_use","id":"tu_side_2","name":"Read","input":{"file_path":"/Users/dev/proj/SIDECAR.md"}}],"usage":{"input_tokens":1,"output_tokens":1}}}

            """
        try sidecar.write(toFile: sidecarPath, atomically: true, encoding: .utf8)

        let state = try SchemaManager.openState(path: tmp + "/state.sqlite")
        let parser = ClaudeCodeParser(projectsRoot: tmp + "/projects")
        try IngestCoordinator(db: state, parsers: [parser]).run()

        // The deriver's pooling, so `poolKey` is what `AnalysisJob.make` expects.
        let pooling = Sessionizer.Pooling.explicit { e in
            "\(e.harness.rawValue)|\(e.extra?["repo_id"] ?? e.cwd ?? "unknown")"
        }
        let sessions = Sessionizer.sessions(
            from: try IngestCoordinator.loadEvents(db: state),
            options: .init(pooling: pooling, calendar: BoundaryFixtureTests.newYork))
        let s = try #require(sessions.first)
        #expect(sessions.count == 1)

        // The negative control: the sidecar WAS ingested, inside the window, in the pool,
        // and without the filters it would have been the second source. A test that
        // cannot reach the code it is trying to violate passes for the wrong reason.
        let pool = String(s.poolKey.dropFirst("claude_code|".count))
        let sidecarTS = 1_773_147_630.0  // 2026-03-10T13:00:30Z
        #expect(s.startedAt <= sidecarTS && sidecarTS <= s.endedAt)
        #expect(try state.scalarInt("SELECT COUNT(*) FROM raw_event WHERE is_sidechain = 1") ?? 0 >= 2)
        #expect(try state.scalarText(
            "SELECT path FROM ingest_watermark WHERE path = ?", [.text(sidecarPath)]) == sidecarPath)
        #expect(
            try state.scalarInt(
                """
                SELECT COUNT(DISTINCT source_id) FROM raw_event
                WHERE harness = 'claude_code' AND ts >= ? AND ts <= ?
                  AND COALESCE(CAST(repo_id AS TEXT), cwd, 'unknown') = ?
                """, [.double(s.startedAt), .double(s.endedAt), .text(pool)]) == 2)

        let job = try AnalysisJob.make(for: s, checkpoint: false, state: state, repoNames: [:])
        #expect(job.transcripts == [rootPath])

        let d = try job.digest()
        #expect(!d.text.contains("SIDECAR"))
        #expect(!d.text.contains("Read"))
        #expect(d.stats.promptsSent == 3)
        #expect(d.stats.toolCalls == 54)
        // And byte-identical to the root digested alone over the same window.
        let alone = try SessionDigest.build(
            harness: .claudeCode, transcripts: [rootPath], start: s.startedAt, end: s.endedAt,
            meta: job.meta)
        #expect(d.text == alone.text && d.hash == alone.hash)
    }

    /// The path allowlist on its own, for the layer the SQL filter cannot reach: a
    /// sidecar whose records claim `isSidechain: false` still resolves to a path that is
    /// not `<projectdir>/<uuid>.jsonl`, and the source id says which split of the
    /// absolute path is the project directory.
    @Test func onlyRootShapedPathsPassTheAllowlist() {
        func id(_ descriptor: String) -> String {
            Hashing.sourceID(harness: .claudeCode, descriptor: descriptor)
        }
        let root = "/Users/me/.claude/projects/-Users-me-app/abc.jsonl"
        #expect(AnalysisJob.isRootTranscript(path: root, sourceID: id("-Users-me-app/abc.jsonl")))
        #expect(AnalysisJob.claudeCodeRelativePath(path: root, sourceID: id("-Users-me-app/abc.jsonl")) == "abc.jsonl")

        let sidecar = "/Users/me/.claude/projects/-Users-me-app/abc/subagents/agent-x.jsonl"
        let sidecarID = id("-Users-me-app/abc/subagents/agent-x.jsonl")
        #expect(AnalysisJob.claudeCodeRelativePath(path: sidecar, sourceID: sidecarID) == "abc/subagents/agent-x.jsonl")
        #expect(!AnalysisJob.isRootTranscript(path: sidecar, sourceID: sidecarID))
        // Not a denylist on `subagents/`: any depth under the uuid directory is a sidecar.
        let future = "/Users/me/.claude/projects/-Users-me-app/abc/futuredir/x.jsonl"
        #expect(!AnalysisJob.isRootTranscript(path: future, sourceID: id("-Users-me-app/abc/futuredir/x.jsonl")))
        // A projects root of any depth, including one component, still resolves.
        #expect(AnalysisJob.isRootTranscript(path: "/p/abc.jsonl", sourceID: id("p/abc.jsonl")))
        // A path the id does not name is not a root — fail closed, never open.
        #expect(!AnalysisJob.isRootTranscript(path: root, sourceID: id("-Users-me-other/abc.jsonl")))
        #expect(!AnalysisJob.isRootTranscript(path: "abc.jsonl", sourceID: id("abc.jsonl")))
    }

    @Test func emptySessionRendersTheEmptyDigest() {
        let d = SessionDigest.Output(events: [], meta: SessionDigest.Meta(repo: "x"))
        #expect(d.text == "# SESSION DIGEST\n(no events)\n")
        #expect(d.coverage == 1.0)
    }

    @Test func headerMetaAppearsInSpecOrder() {
        let events = [SessionDigest.Event(ts: 0, kind: .prompt, text: "go")]
        let meta = SessionDigest.Meta(
            repo: "gt-transit", harness: "claude_code", startedAtLocal: "2026-03-10T09:00:00-04:00",
            endReason: "idle_gap", attendedSeconds: 433, autonomousSeconds: 0)
        let d = SessionDigest.Output(events: events, meta: meta)
        #expect(
            d.text.hasPrefix(
                "# SESSION DIGEST\nrepo: gt-transit\nharness: claude_code\n"
                    + "started_at_local: 2026-03-10T09:00:00-04:00\nend_reason: idle_gap\n"
                    + "attended_seconds: 433\nautonomous_seconds: 0\nwall: 0m  prompts sent: 1"))
    }
}
