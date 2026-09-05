import BuilderModel
import Foundation

/// Runs the analysis: digest -> `claude -p` -> validated `SessionAnalysis`. The Swift
/// twin of `analysis/run.py`, and it must stay one.
///
/// Why `claude -p` and not the API: the user's own Claude Code subscription pays for it,
/// nothing leaves the machine except to Anthropic under the user's existing agreement, and
/// there is no key to manage. `--tools ""` and a replaced system prompt keep the context
/// to the digest alone (MEASURED: 1.6k tokens of overhead versus 41k with the default
/// Claude Code system prompt, and the default prompt would also let the model run tools).
///
/// Two CLI facts that cost an hour each to learn, both encoded below: structured output
/// needs several internal turns, so `--max-turns 1` fails silently with exit 1 and is NOT
/// passed; and the CLI's schema validator rejects a `$schema` header ("no schema with key
/// or ref"), so it is stripped at load.
///
/// MEASURED on a 45-minute, 212-event session: 33 KB digest, five internal turns, 150 s,
/// $0.33 at list price on sonnet.
public enum Analyzer {

    /// MEASURED 150 s for a 33 KB digest; three times that before giving up.
    public static let timeoutSeconds: TimeInterval = 480

    /// Never attach to the caller's session: inside Claude Code these are set, and a nested
    /// `claude -p` would append its turn to the CURRENT transcript.
    public static let scrubbedEnvironment = ["CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION"]

    public enum AnalysisError: Error, CustomStringConvertible {
        case resourceMissing(String)
        case cliNotFound
        case timedOut(TimeInterval)
        case exit(code: Int32, stderr: String)
        case notJSON(String)
        case cliError(String)
        case noStructuredOutput
        case decode(Error)

        public var description: String {
            switch self {
            case .resourceMissing(let n): return "analysis resource \(n) missing from the bundle"
            case .cliNotFound: return "claude CLI not found on PATH"
            case .timedOut(let s): return "claude -p timed out after \(Int(s))s"
            case .exit(let code, let err): return "claude -p exit \(code): \(err.suffix(800))"
            case .notJSON(let head): return "claude -p returned non-JSON: \(head.prefix(300))"
            case .cliError(let m): return "claude -p error: \(m)"
            case .noStructuredOutput: return "no structured_output in claude -p response"
            case .decode(let e): return "analysis did not decode as SessionAnalysis: \(e)"
            }
        }
    }

    // MARK: - Resources

    /// The analyst prompt. Read from the bundle, never embedded: `analysis/prompt.py`
    /// reads the SAME file, so the Python reference and the agent send identical bytes.
    public static func systemPrompt() throws -> String {
        guard let url = Bundle.module.url(forResource: "analyst_prompt", withExtension: "txt"),
              let text = try? String(contentsOf: url, encoding: .utf8), !text.isEmpty
        else { throw AnalysisError.resourceMissing("analyst_prompt.txt") }
        return text
    }

    /// The generated JSON Schema (scripts/gen_analysis.py writes it here and to
    /// analysis/schema.json), with `$schema` and `$comment` stripped like the Python does.
    public static func schema() throws -> [String: Any] {
        guard let url = Bundle.module.url(forResource: "analysis_schema", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              var obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { throw AnalysisError.resourceMissing("analysis_schema.json") }
        obj.removeValue(forKey: "$schema")
        obj.removeValue(forKey: "$comment")
        return obj
    }

    public static func schemaJSON() throws -> String {
        let data = try JSONSerialization.data(withJSONObject: try schema(), options: [.sortedKeys])
        return String(decoding: data, as: UTF8.self)
    }

    /// `prompt.user_message`, byte for byte.
    public static func userMessage(digest: String, coverage: Double) -> String {
        var cov = ""
        if coverage < 0.999 {
            cov =
                "\nNOTE: only \(String(format: "%.0f", coverage * 100))% of timeline lines fit in this digest. Every prompt and "
                + "every error is present; ordinary tool activity was thinned. Lower confidence accordingly.\n"
        }
        return "\(cov)\n\(digest)\n\nProduce the analysis JSON now."
    }

    // MARK: - The invocation

    public struct Invocation: Sendable {
        public let executable: String
        public let arguments: [String]
        public let environment: [String: String]

        /// The command with the three long arguments elided to their sizes, for `--dry-run`.
        public var display: String {
            var parts: [String] = [executable]
            var i = 0
            while i < arguments.count {
                let a = arguments[i]
                if ["-p", "--json-schema", "--system-prompt"].contains(a), i + 1 < arguments.count {
                    parts.append(a)
                    parts.append("<\(arguments[i + 1].unicodeScalars.count) chars>")
                    i += 2
                    continue
                }
                parts.append(a.isEmpty ? "\"\"" : a)
                i += 1
            }
            return parts.joined(separator: " ")
        }
    }

    /// Exactly `run.call_claude`'s argv: `/usr/bin/env claude -p <user> --output-format json
    /// --json-schema <schema> --system-prompt <prompt> --tools "" --model <model>
    /// --session-id <uuid>`.
    public static func invocation(
        digest: String, coverage: Double, model: String, sessionID: UUID = UUID()
    ) throws -> Invocation {
        let arguments = [
            "claude",
            "-p", userMessage(digest: digest, coverage: coverage),
            "--output-format", "json",
            "--json-schema", try schemaJSON(),
            "--system-prompt", try systemPrompt(),
            "--tools", "",
            "--model", model,
            "--session-id", sessionID.uuidString.lowercased(),
        ]
        var env = ProcessInfo.processInfo.environment
        for k in scrubbedEnvironment { env.removeValue(forKey: k) }
        // A menu bar app launched from Finder inherits launchd's PATH, which has none of
        // the places `claude` is installed. Appending rather than replacing, so a shell
        // that already found it is unchanged.
        let home = NSHomeDirectory()
        let extra = ["/opt/homebrew/bin", "/usr/local/bin", "\(home)/.local/bin", "\(home)/.claude/local", "\(home)/.npm-global/bin"]
        var path = (env["PATH"] ?? "/usr/bin:/bin").split(separator: ":").map(String.init)
        for e in extra where !path.contains(e) { path.append(e) }
        env["PATH"] = path.joined(separator: ":")
        return Invocation(executable: "/usr/bin/env", arguments: arguments, environment: env)
    }

    // MARK: - The envelope

    /// What `claude -p --output-format json` prints: the structured output plus the
    /// bookkeeping the runner reads (`is_error`, `result`, `modelUsage`, `total_cost_usd`,
    /// `duration_ms`).
    public struct Envelope {
        public let structured: [String: Any]
        public let raw: [String: Any]

        public var costUSD: Double? { (raw["total_cost_usd"] as? NSNumber)?.doubleValue }
        public var durationMs: Int? { (raw["duration_ms"] as? NSNumber)?.intValue }

        /// The model that wrote the output. The envelope lists every model the CLI
        /// touched, including a small one it uses for bookkeeping (MEASURED: haiku with 15
        /// output tokens beside sonnet with 16,435); the analyst is the one that wrote the
        /// output tokens.
        public var topModel: String? {
            guard let usage = raw["modelUsage"] as? [String: Any], !usage.isEmpty else { return nil }
            let ranked = usage.map { (k, v) -> (String, Int) in
                (k, ((v as? [String: Any])?["outputTokens"] as? NSNumber)?.intValue ?? 0)
            }.sorted { a, b in a.1 != b.1 ? a.1 > b.1 : a.0 < b.0 }
            return ranked.first?.0
        }
    }

    /// Pure: bytes from the CLI's stdout -> envelope, or a precise error. Factored out of
    /// the process launch so the shape can be tested without spending a model call.
    public static func parseEnvelope(_ data: Data) throws -> Envelope {
        guard let obj = try? JSONSerialization.jsonObject(with: data),
              let env = obj as? [String: Any]
        else { throw AnalysisError.notJSON(String(decoding: data.prefix(300), as: UTF8.self)) }

        if let flag = env["is_error"], SessionDigest.isTruthy(flag) {
            let result = (env["result"] as? String) ?? "\(env["result"] ?? "")"
            throw AnalysisError.cliError(result)
        }
        if let so = env["structured_output"] as? [String: Any] {
            return Envelope(structured: so, raw: env)
        }
        // Older CLIs put the JSON in `result` as a string.
        if let text = env["result"] as? String,
           let parsed = try? JSONSerialization.jsonObject(with: Data(text.utf8)),
           let so = parsed as? [String: Any] {
            return Envelope(structured: so, raw: env)
        }
        throw AnalysisError.noStructuredOutput
    }

    /// Pure: overwrite the fields the model only had placeholders for, drop decision
    /// patterns whose excerpt is not a verbatim substring of the digest, decode.
    /// Returns the analysis and how many excerpts were dropped (one of four on the first
    /// real run — the model had trimmed a quote).
    public static func finalize(
        envelope: Envelope, digest: SessionDigest.Output, requestedModel: String, now: Date = Date()
    ) throws -> (analysis: SessionAnalysis, droppedExcerpts: Int) {
        var so = envelope.structured
        so["analysis_version"] = AnalysisSpec.version
        so["model"] = envelope.topModel ?? requestedModel
        so["generated_at"] = iso8601(now)
        so["digest_hash"] = digest.hash
        so["digest_coverage"] = digest.coverage
        let dropped = verifyExcerpts(&so, digestText: digest.text)

        let data: Data
        do {
            data = try JSONSerialization.data(withJSONObject: so)
        } catch {
            throw AnalysisError.decode(error)
        }
        do {
            return (try AnalysisStore.decoder().decode(SessionAnalysis.self, from: data), dropped)
        } catch {
            throw AnalysisError.decode(error)
        }
    }

    /// `run._verify_excerpts`: whitespace-normalise both sides, trim `…. ` from the
    /// excerpt, keep it only if it occurs verbatim.
    static func verifyExcerpts(_ so: inout [String: Any], digestText: String) -> Int {
        let norm = normalizeWhitespace(digestText)
        var kept: [[String: Any]] = []
        var dropped = 0
        for p in (so["decision_patterns"] as? [Any]) ?? [] {
            guard let pattern = p as? [String: Any] else { dropped += 1; continue }
            let raw = (pattern["prompt_excerpt"] as? String) ?? ""
            let ex = normalizeWhitespace(raw).trimmingCharacters(in: CharacterSet(charactersIn: "…. "))
            if !ex.isEmpty && norm.contains(ex) {
                kept.append(pattern)
            } else {
                dropped += 1
            }
        }
        so["decision_patterns"] = kept
        return dropped
    }

    /// `" ".join(s.split())`.
    static func normalizeWhitespace(_ s: String) -> String {
        s.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }

    /// `%Y-%m-%dT%H:%M:%SZ`, UTC, no fraction — what `.iso8601` decodes on the way back.
    static func iso8601(_ d: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        f.timeZone = TimeZone(identifier: "UTC")
        return f.string(from: d)
    }

    // MARK: - Running it

    public struct Result: Sendable {
        public let analysis: SessionAnalysis
        public let costUSD: Double?
        public let durationMs: Int?
        public let droppedExcerpts: Int
    }

    /// Launch `claude -p` for one digest and wait for it.
    ///
    /// stdin is `/dev/null` explicitly: with an inherited pipe the CLI waits for piped
    /// input, warns after 3 s, and treats the prompt as incomplete. stdout and stderr are
    /// drained concurrently — the envelope alone is tens of KB, past a pipe buffer.
    public static func run(
        digest: SessionDigest.Output, model: String = AnalysisSettings.model(),
        timeout: TimeInterval = timeoutSeconds
    ) throws -> Result {
        let inv = try invocation(digest: digest.text, coverage: digest.coverage, model: model)
        let (stdout, stderr, status, timedOut) = try launch(inv, timeout: timeout)
        if timedOut { throw AnalysisError.timedOut(timeout) }
        if status == 127 { throw AnalysisError.cliNotFound }
        if status != 0 {
            throw AnalysisError.exit(code: status, stderr: String(decoding: stderr, as: UTF8.self))
        }
        let envelope = try parseEnvelope(stdout)
        let (analysis, dropped) = try finalize(envelope: envelope, digest: digest, requestedModel: model)
        return Result(
            analysis: analysis, costUSD: envelope.costUSD, durationMs: envelope.durationMs,
            droppedExcerpts: dropped)
    }

    /// A mutable cell that several queues may touch, guarded by its own lock.
    private final class Cell<T>: @unchecked Sendable {
        private var value: T
        private let lock = NSLock()
        init(_ v: T) { value = v }
        func get() -> T { lock.lock(); defer { lock.unlock() }; return value }
        func set(_ v: T) { lock.lock(); defer { lock.unlock() }; value = v }
    }

    private static func launch(_ inv: Invocation, timeout: TimeInterval) throws
        -> (stdout: Data, stderr: Data, status: Int32, timedOut: Bool)
    {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: inv.executable)
        process.arguments = inv.arguments
        process.environment = inv.environment
        process.standardInput = FileHandle.nullDevice
        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err

        try process.run()

        let timedOut = Cell(false)
        let killer = DispatchWorkItem {
            if process.isRunning {
                timedOut.set(true)
                process.terminate()
            }
        }
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + timeout, execute: killer)

        let errData = Cell(Data())
        let group = DispatchGroup()
        group.enter()
        DispatchQueue.global(qos: .utility).async {
            errData.set(err.fileHandleForReading.readDataToEndOfFile())
            group.leave()
        }
        let outData = out.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        group.wait()
        killer.cancel()

        return (outData, errData.get(), process.terminationStatus, timedOut.get())
    }
}
