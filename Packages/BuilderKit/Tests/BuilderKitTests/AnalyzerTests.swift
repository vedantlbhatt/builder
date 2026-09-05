import BuilderAnalysis
import BuilderModel
import BuilderSchema
import BuilderSQLite
import Foundation
import Testing

/// The analyst without the model: envelope parsing, the field overwrite, excerpt
/// verification, the shared prompt resource, and the Tier A row. Nothing here launches a
/// process — a model call is $0.33 and 150 s (docs/analysis.md), so the pure half is what
/// gets exercised on every CI run.
@Suite("Analyzer — envelope, prompt resource, store")
struct AnalyzerTests {

    /// Modelled on what `claude -p --output-format json --json-schema` prints, with the
    /// keys `analysis/run.py` reads: `is_error`, `result`, `structured_output`,
    /// `modelUsage`, `total_cost_usd`, `duration_ms`. The structured output carries the
    /// placeholder values the model is asked to emit for the fields the runner overwrites.
    static let sampleEnvelope = #"""
        {
          "type": "result",
          "subtype": "success",
          "is_error": false,
          "duration_ms": 150321,
          "num_turns": 5,
          "result": "{\"headline\": \"ignored when structured_output is present\"}",
          "session_id": "3f2a5a4e-6a1e-4b0f-9d7d-1c2b3a4d5e6f",
          "total_cost_usd": 0.33,
          "modelUsage": {
            "claude-haiku-4-5-20251001": {"inputTokens": 800, "outputTokens": 15, "costUSD": 0.001},
            "claude-sonnet-4-5-20250929": {"inputTokens": 17000, "outputTokens": 16435, "costUSD": 0.329}
          },
          "structured_output": {
            "analysis_version": 1,
            "model": "placeholder",
            "generated_at": "1970-01-01T00:00:00Z",
            "digest_hash": "0000000000000000000000000000000000000000000000000000000000000000",
            "digest_coverage": 0.5,
            "headline": "Wired the live upload and the analyst into the agent",
            "summary": "Built the digest, the runner and the sync changes. Tests were added. Ended on a commit.",
            "outcome": "shipped",
            "features": [
              {"name": "Live session upload", "status": "done", "detail": null, "evidence": [3, 9]}
            ],
            "work_mix": {"feature": 0.7, "test": 0.3},
            "build_style": {
              "planning": "explicit_plan",
              "iteration": "linear",
              "steering": "guided",
              "verification": "ran_tests",
              "scope_control": "held",
              "architecture_note": null
            },
            "dimensions": [
              {"dimension": "steering", "score": 70, "rationale": "clear asks, one correction landed"},
              {"dimension": "execution", "score": 65, "rationale": "steady, verified by tests"},
              {"dimension": "engineering", "score": 60, "rationale": "tests run, generated files regenerated"},
              {"dimension": "product_instinct", "score": 55, "rationale": "scope held"},
              {"dimension": "planning", "score": 75, "rationale": "steps stated up front"}
            ],
            "archetype": "architect",
            "decision_patterns": [
              {"pattern": "cuts scope explicitly", "prompt_excerpt": "no, the   other one", "effect": "redirected the agent"},
              {"pattern": "invented", "prompt_excerpt": "this sentence is not in any prompt", "effect": null}
            ],
            "pivots": [],
            "friction": [{"kind": "tool_failure", "description": "one flaky test", "cost_minutes": 4}],
            "prompting": {"tone": "terse", "specificity": 70, "correction_share": 0.33, "question_share": 0.0, "note": null},
            "growth_edge": ["State the acceptance test in the first prompt"],
            "tags": ["sync", "analysis"],
            "confidence": 0.8,
            "contains_sensitive": false
          }
        }
        """#

    static func digest() -> SessionDigest.Output {
        let events = [
            SessionDigest.Event(ts: 0, kind: .prompt, text: "hello"),
            SessionDigest.Event(ts: 60, kind: .tool, text: "swift test", tool: "Bash"),
            SessionDigest.Event(ts: 120, kind: .prompt, text: "no, the other one"),
        ]
        return SessionDigest.Output(events: events, meta: SessionDigest.Meta(harness: "claude_code"))
    }

    // MARK: - Envelope

    @Test func parsesTheEnvelopeAndPicksTheModelThatWroteTheOutput() throws {
        let env = try Analyzer.parseEnvelope(Data(Self.sampleEnvelope.utf8))
        #expect(env.structured["headline"] as? String == "Wired the live upload and the analyst into the agent")
        #expect(env.costUSD == 0.33)
        #expect(env.durationMs == 150321)
        // The bookkeeping model wrote 15 tokens; the analyst wrote 16,435.
        #expect(env.topModel == "claude-sonnet-4-5-20250929")
    }

    @Test func finalizeOverwritesPlaceholdersAndDropsUnverifiableExcerpts() throws {
        let env = try Analyzer.parseEnvelope(Data(Self.sampleEnvelope.utf8))
        let digest = Self.digest()
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let (a, dropped) = try Analyzer.finalize(
            envelope: env, digest: digest, requestedModel: "sonnet", now: now)

        #expect(a.analysisVersion == AnalysisSpec.version)
        #expect(a.model == "claude-sonnet-4-5-20250929")
        #expect(a.generatedAt == now)
        #expect(a.digestHash == digest.hash)
        #expect(a.digestCoverage == digest.coverage)
        #expect(a.headline == "Wired the live upload and the analyst into the agent")
        #expect(a.outcome == .shipped)
        #expect(a.buildStyle.planning == .explicitPlan)
        #expect(a.dimensions.count == 5)
        #expect(a.workMix["feature"] == 0.7)

        // "no, the   other one" normalises to a verbatim substring of the digest and is
        // kept; the invented one is dropped — same rule as run._verify_excerpts.
        #expect(dropped == 1)
        #expect(a.decisionPatterns.count == 1)
        #expect(a.decisionPatterns.first?.pattern == "cuts scope explicitly")
    }

    @Test func finalizedAnalysisRoundTripsThroughTheStoreEncoding() throws {
        let env = try Analyzer.parseEnvelope(Data(Self.sampleEnvelope.utf8))
        let (a, _) = try Analyzer.finalize(envelope: env, digest: Self.digest(), requestedModel: "sonnet")
        let body = try AnalysisStore.encode(a)
        #expect(body.contains("\"generated_at\":\""))
        let back = try AnalysisStore.decode(body)
        #expect(back == a)
    }

    @Test func envelopeErrorsAreLoud() throws {
        #expect(throws: Analyzer.AnalysisError.self) {
            _ = try Analyzer.parseEnvelope(Data("not json at all".utf8))
        }
        #expect(throws: Analyzer.AnalysisError.self) {
            _ = try Analyzer.parseEnvelope(Data(#"{"is_error": true, "result": "rate limited"}"#.utf8))
        }
        #expect(throws: Analyzer.AnalysisError.self) {
            _ = try Analyzer.parseEnvelope(Data(#"{"type": "result", "result": "just prose"}"#.utf8))
        }
        // Older CLIs put the JSON in `result` as a string.
        let older = try Analyzer.parseEnvelope(Data(#"{"result": "{\"headline\": \"from result\"}"}"#.utf8))
        #expect(older.structured["headline"] as? String == "from result")
    }

    // MARK: - Prompt, schema, invocation

    @Test func promptResourceIsTheSharedAnalystPrompt() throws {
        let prompt = try Analyzer.systemPrompt()
        #expect(prompt.hasPrefix("You are Builder's session analyst."))
        #expect(prompt.hasSuffix("Output ONLY the JSON object."))
        #expect(prompt.contains("DECISION PATTERNS"))
    }

    @Test func schemaResourceIsStrippedOfTheHeaderTheCLIRejects() throws {
        let schema = try Analyzer.schema()
        #expect(schema["$schema"] == nil)
        #expect(schema["$comment"] == nil)
        #expect(schema["title"] as? String == "SessionAnalysis")
        let props = try #require(schema["properties"] as? [String: Any])
        #expect(props["decision_patterns"] != nil)
        #expect(schema["additionalProperties"] as? Bool == false)
    }

    @Test func userMessageMatchesThePythonShape() {
        #expect(Analyzer.userMessage(digest: "D", coverage: 1.0) == "\nD\n\nProduce the analysis JSON now.")
        let thinned = Analyzer.userMessage(digest: "D", coverage: 0.486)
        #expect(thinned.hasPrefix("\nNOTE: only 49% of timeline lines fit in this digest."))
        #expect(thinned.hasSuffix("\n\nD\n\nProduce the analysis JSON now."))
    }

    @Test func invocationIsTheReferenceCommandLine() throws {
        let id = UUID()
        let inv = try Analyzer.invocation(digest: "D", coverage: 1.0, model: "sonnet", sessionID: id)
        #expect(inv.executable == "/usr/bin/env")
        #expect(inv.arguments.first == "claude")
        #expect(inv.arguments[1] == "-p")
        #expect(inv.arguments.contains("--output-format"))
        #expect(inv.arguments.contains("--json-schema"))
        #expect(inv.arguments.contains("--system-prompt"))
        // `--tools ""`: an empty argument, present, not dropped.
        let tools = try #require(inv.arguments.firstIndex(of: "--tools"))
        #expect(inv.arguments[tools + 1] == "")
        #expect(inv.arguments.suffix(2) == ["--session-id", id.uuidString.lowercased()])
        // `--max-turns` breaks structured output and must never be passed.
        #expect(!inv.arguments.contains("--max-turns"))
        for k in Analyzer.scrubbedEnvironment { #expect(inv.environment[k] == nil) }
        #expect(inv.display.contains("--system-prompt <"))
        #expect(inv.display.contains("--tools \"\""))
    }

    // MARK: - The Tier A row

    @Test func analysisRowIsStoredInStateAndReadBack() throws {
        let dir = NSTemporaryDirectory() + "builder-analysis-tests-\(UUID().uuidString)"
        try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(atPath: dir) }
        let db = try SchemaManager.openState(path: dir + "/state.sqlite")
        #expect(db.userVersion == SchemaManager.stateVersion)
        #expect(try db.tableExists("session_analysis"))
        #expect(try db.tableExists("live_upload"))

        let env = try Analyzer.parseEnvelope(Data(Self.sampleEnvelope.utf8))
        let digest = Self.digest()
        let (a, _) = try Analyzer.finalize(envelope: env, digest: digest, requestedModel: "sonnet")
        let record = AnalysisRecord(
            clientSessionID: "abc", analysisVersion: a.analysisVersion, digestHash: digest.hash,
            digestCoverage: digest.coverage, model: a.model,
            generatedAt: a.generatedAt.timeIntervalSince1970, costUSD: 0.33,
            body: try AnalysisStore.encode(a), checkpoint: true, createdAt: 1)
        try AnalysisStore.upsert(record, in: db)

        let back = try #require(try AnalysisStore.record(for: "abc", in: db))
        #expect(back.checkpoint)
        #expect(try back.decoded() == a)
        #expect(try AnalysisStore.index(in: db)["abc"]?.checkpoint == true)

        // The final analysis replaces the checkpoint under the same key.
        var final = record
        final.checkpoint = false
        final.createdAt = 2
        try AnalysisStore.upsert(final, in: db)
        #expect(try AnalysisStore.all(in: db).count == 1)
        #expect(try AnalysisStore.index(in: db)["abc"]?.checkpoint == false)
    }
}
