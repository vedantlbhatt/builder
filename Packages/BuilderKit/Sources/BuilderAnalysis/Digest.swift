import BuilderModel
import BuilderParse
import Foundation

/// The session digest: a transcript reduced to something small enough to analyse and
/// honest enough to trust. A line-by-line port of `analysis/digest.py`, which is the
/// reference; the two must produce the SAME TEXT for the same records, because
/// `digest_hash` is what lets the server tell a fresh analysis from a replayed one, and
/// because the Python runner is how the prompt gets tuned.
///
/// Three rules, in priority order (docs/analysis.md):
///
/// 1. Every human prompt, verbatim, bounded per prompt. MEASURED: 1,456 typed prompts
///    against 23,838 tool calls in the reference corpus — they are never sampled out.
/// 2. Every error. Friction is what a person most wants explained.
/// 3. Everything else degrades under a character budget: assistant text is truncated,
///    then runs of tool calls collapse into one line, then the middle of the session is
///    thinned. `coverage` reports how much survived, and the model is told.
///
/// Nothing here reads thinking blocks, file contents or full tool output. Secrets are
/// masked before anything is written: the digest is local, but the analysis it produces
/// can be uploaded, and a model will happily copy a token into a "friction" note.
///
/// Porting notes, so the next person does not "fix" one of these into a divergence:
///   - Python `len()` counts code points, so every length here is `unicodeScalars.count`
///     and every slice is by scalar, never by `Character`.
///   - `json.dumps(text, ensure_ascii=False)` is reproduced by `pyJSONString`, not by
///     `JSONEncoder`, which escapes `/` and orders nothing the way Python does.
///   - `round()` in Python is half-to-even; `.rounded(.toNearestOrEven)` here.
///   - The one known divergence: the fallback tool line for MCP tools serialises the
///     input dict with sorted keys, where Python keeps file order. Both are bounded to
///     100 characters and neither is a prompt or an error.
public enum SessionDigest {

    public static let interruptPrefix = Tuning.interruptPrefix

    public static let promptMax = 1_400
    public static let assistantMax = 320
    public static let assistantMaxTight = 120
    public static let commandMax = 160
    public static let errorMax = 240
    /// Characters; ~15k tokens. MEASURED: a 45-minute, 212-event session renders to 33 KB
    /// at level 0, so a typical session never degrades at all.
    public static let defaultBudget = 60_000

    /// Tool names that mean "ran a shell command" / "edited a file", per harness. Claude
    /// Code's names first; the Codex names are documented in analysis/codex.py. Membership
    /// here is what `stats` keys commits, test runs and files-edited on.
    public static let shellTools: Set<String> = ["Bash", "shell", "exec_command", "local_shell", "shell_command"]
    public static let editTools: Set<String> = ["Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"]

    // MARK: - Types

    public enum Kind: String, Sendable {
        case prompt, interrupt, assistant, tool
        case resultError = "result_error"
        case humanEdit = "human_edit"
        case compaction
    }

    /// One digest event — the Python `Ev` dataclass.
    public struct Event: Sendable, Equatable {
        public var n: Int
        public var ts: Double
        public var kind: Kind
        public var text: String = ""
        public var tool: String? = nil
        public var path: String? = nil
        public var added: Int? = nil
        public var removed: Int? = nil
        public var ok: Bool = true
        public var toolID: String? = nil
        public var model: String? = nil

        public init(
            n: Int = 0, ts: Double, kind: Kind, text: String = "", tool: String? = nil,
            path: String? = nil, added: Int? = nil, removed: Int? = nil, ok: Bool = true,
            toolID: String? = nil, model: String? = nil
        ) {
            self.n = n
            self.ts = ts
            self.kind = kind
            self.text = text
            self.tool = tool
            self.path = path
            self.added = added
            self.removed = removed
            self.ok = ok
            self.toolID = toolID
            self.model = model
        }
    }

    /// The header lines above the deterministic numbers — the Python `meta` dict. Keys are
    /// emitted in this order, and only when present. Seconds are whole numbers so the line
    /// reads `attended_seconds: 433`, the way a Python int formats.
    public struct Meta: Sendable, Equatable {
        public var repo: String?
        public var harness: String?
        public var startedAtLocal: String?
        public var endReason: String?
        public var attendedSeconds: Int?
        public var autonomousSeconds: Int?

        public init(
            repo: String? = nil, harness: String? = nil, startedAtLocal: String? = nil,
            endReason: String? = nil, attendedSeconds: Int? = nil, autonomousSeconds: Int? = nil
        ) {
            self.repo = repo
            self.harness = harness
            self.startedAtLocal = startedAtLocal
            self.endReason = endReason
            self.attendedSeconds = attendedSeconds
            self.autonomousSeconds = autonomousSeconds
        }
    }

    /// Deterministic numbers. These go on the card; the model never invents them.
    public struct Stats: Sendable, Equatable {
        public var events = 0
        public var wallSeconds = 0
        public var promptsSent = 0
        public var repliesReceived = 0
        public var interrupts = 0
        public var humanEdits = 0
        public var compactions = 0
        public var toolCalls = 0
        /// `Counter.most_common()` order: count descending, ties in first-seen order.
        public var toolMix: [(String, Int)] = []
        public var errors = 0
        public var linesAddedAgent = 0
        public var linesRemovedAgent = 0
        public var filesEdited = 0
        public var filesWrittenViaShell = 0
        public var filesRead = 0
        public var gitCommitsRun = 0
        public var testRuns = 0
        public var models: [(String, Int)] = []
        public var longestSilenceSeconds = 0

        public static func == (a: Stats, b: Stats) -> Bool {
            func same(_ x: [(String, Int)], _ y: [(String, Int)]) -> Bool {
                x.count == y.count && zip(x, y).allSatisfy { $0.0 == $1.0 && $0.1 == $1.1 }
            }
            return a.events == b.events && a.wallSeconds == b.wallSeconds && a.promptsSent == b.promptsSent
                && a.repliesReceived == b.repliesReceived && a.interrupts == b.interrupts
                && a.humanEdits == b.humanEdits && a.compactions == b.compactions
                && a.toolCalls == b.toolCalls && same(a.toolMix, b.toolMix) && a.errors == b.errors
                && a.linesAddedAgent == b.linesAddedAgent && a.linesRemovedAgent == b.linesRemovedAgent
                && a.filesEdited == b.filesEdited && a.filesWrittenViaShell == b.filesWrittenViaShell
                && a.filesRead == b.filesRead && a.gitCommitsRun == b.gitCommitsRun
                && a.testRuns == b.testRuns && same(a.models, b.models)
                && a.longestSilenceSeconds == b.longestSilenceSeconds
        }
    }

    public struct Output: Sendable {
        public let text: String
        public let coverage: Double
        /// SHA-256 hex of `text`. What `SessionAnalysis.digestHash` carries.
        public let hash: String
        public let stats: Stats
        public let events: Int

        public init(text: String, coverage: Double, hash: String, stats: Stats, events: Int) {
            self.text = text
            self.coverage = coverage
            self.hash = hash
            self.stats = stats
            self.events = events
        }

        /// From already-loaded events, for callers and tests that have no file.
        public init(events: [Event], meta: Meta, budget: Int = defaultBudget) {
            let (text, coverage) = SessionDigest.render(events: events, meta: meta, budget: budget)
            self.init(
                text: text, coverage: coverage, hash: SessionDigest.hash(text),
                stats: SessionDigest.stats(events), events: events.count)
        }
    }

    public enum DigestError: Error, CustomStringConvertible {
        case noTranscripts
        case unsupportedHarness(Harness)

        public var description: String {
            switch self {
            case .noTranscripts:
                return "no transcript files on disk for this session"
            case .unsupportedHarness(let h):
                return "no digest loader for \(h.rawValue) yet"
            }
        }
    }

    // MARK: - Masking

    private static let secretPatterns: [NSRegularExpression] = [
        regex(#"sk-[A-Za-z0-9_\-]{16,}"#),
        regex(#"AKIA[0-9A-Z]{16}"#),
        regex(#"gh[pousr]_[A-Za-z0-9]{30,}"#),
        regex(#"xox[baprs]-[A-Za-z0-9\-]{10,}"#),
        regex(#"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"#),
        regex(#"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*['"]?[^\s'"]{6,}"#),
        regex(#"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"#),  // JWT
    ]
    private static let emailPattern = regex(#"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"#)

    /// Same patterns, same order, same replacement tokens as `digest.mask`.
    public static func mask(_ text: String) -> String {
        var t = text
        for p in secretPatterns { t = replaceAll(p, in: t, with: "[redacted]") }
        return replaceAll(emailPattern, in: t, with: "[email]")
    }

    // MARK: - Error detection

    /// Only shapes that mean the COMMAND failed, not output that mentions failure.
    ///
    /// MEASURED on a 45-minute transcript: a loose keyword match ("no such file",
    /// "failed") flagged 20 results as errors; 17 were successful commands whose output
    /// quoted an error string. The harness's own `is_error` flag is authoritative when
    /// present; this is the fallback.
    private static let errorHead = regex(
        #"(?m)^(Traceback \(most recent call last\)|Error:|error:|fatal:|FAILED|npm ERR!|Exit code [1-9]\d*|Command failed|Killed|Segmentation fault)"#)
    private static let errorClass = regex(#"(?m)^\w+(Error|Exception): "#)

    public static func looksLikeError(_ s: String) -> Bool {
        let head = prefix(s, 400)
        return matches(errorHead, head) || matches(errorClass, head)
    }

    // MARK: - Shell file effects

    private static let heredoc = regex(
        #"(?:cat|tee)\s*(?:>>?|-a\s+)?\s*(?<path>[\w./~\-]+)\s*<<\s*-?['"]?\w+['"]?"#)
    private static let sedInPlace = regex(#"\bsed\s+-i\S*\s+.*?\s(?<path>[\w./~\-]+\.\w+)(?:\s|$)"#)

    /// `(path, approx lines written)` for shell-driven file writes.
    ///
    /// Agents in permission modes that prefer the shell write files with heredocs instead
    /// of the Write tool. MEASURED on such a session: 100 Bash calls, 0 Edit/Write calls,
    /// and every source file in the commit was created by `cat > path <<'EOF'`. Ignoring
    /// that reports "agent lines +0" on a session that added a thousand. The count is the
    /// heredoc body length — approximate, labelled so, and better than zero.
    public static func bashFileEffect(_ command: String) -> (path: String?, approx: Int?) {
        let ns = command as NSString
        let whole = NSRange(location: 0, length: ns.length)
        if let m = heredoc.firstMatch(in: command, range: whole) {
            let body = ns.substring(from: m.range.location + m.range.length)
            let newlines = body.unicodeScalars.reduce(0) { $0 + ($1 == "\n" ? 1 : 0) }
            return (ns.substring(with: m.range(withName: "path")), max(0, newlines - 1))
        }
        if let m = sedInPlace.firstMatch(in: command, range: whole) {
            return (ns.substring(with: m.range(withName: "path")), nil)
        }
        return (nil, nil)
    }

    // MARK: - Tool lines

    /// `(tool name, file path, one-line description of the input)` — `_tool_line`.
    static func toolLine(_ b: JSONNode) -> (name: String, path: String?, desc: String) {
        let name = b.name.nonEmptyString ?? "tool"
        let inputNode = b.input
        // `inp = b.get("input") or {}`: a missing or falsy input is an empty dict, a
        // truthy non-dict input means "name only".
        var inp: [String: Any] = [:]
        if let obj = inputNode.object {
            inp = obj
        } else if inputNode.exists, isTruthy(inputNode.raw) {
            return (name, nil, "")
        }
        let node = JSONNode(inp)
        let path = truthyString(node.file_path) ?? truthyString(node.path) ?? truthyString(node.notebook_path)

        if name == "Bash" {
            let cmd = node.command.string ?? ""
            let effect = bashFileEffect(cmd)
            return (name, effect.path, trunc(cmd.replacingOccurrences(of: "\n", with: " ⏎ "), commandMax))
        }
        if ["Read", "Write", "Edit", "MultiEdit", "NotebookEdit"].contains(name) {
            return (name, path, path ?? "")
        }
        if name == "Glob" || name == "Grep" {
            return (name, nil, trunc(node.pattern.string ?? "", 80))
        }
        if name == "Agent" || name == "Task" {
            return (name, nil, trunc(truthyString(node.description) ?? node.prompt.string ?? "", 100))
        }
        if name == "WebSearch" || name == "WebFetch" {
            return (name, nil, trunc(truthyString(node.query) ?? node.url.string ?? "", 100))
        }
        if name == "TodoWrite" || name.hasPrefix("Task") {
            return (name, nil, "")
        }
        // MCP and everything else: name only, plus a bounded dump of the input. Sorted keys
        // here where Python keeps file order — the one known divergence, see the type doc.
        if inp.isEmpty { return (name, nil, "") }
        let dump = compactJSON(inp)
        return (name, nil, trunc(prefix(dump, 200), 100))
    }

    // MARK: - Loading

    /// One parsed record with the ordering key the Python loader sorts on: `(ts, i)`,
    /// file index first so several transcripts merge deterministically.
    private struct Raw {
        let ts: Double
        let file: Int
        let line: Int
        let node: JSONNode
    }

    /// Read Claude Code transcripts into digest events, in time order, within
    /// `[start, end]` (inclusive, like the Python). Records without a timestamp are
    /// skipped — they are bookkeeping and the reference skips them too — and a partial
    /// trailing line is never consumed (`LineReader` guarantees it).
    public static func loadClaudeCodeEvents(
        paths: [String], start: Double? = nil, end: Double? = nil
    ) throws -> [Event] {
        var raw: [Raw] = []
        for (fileIndex, path) in paths.enumerated() {
            let reader = try LineReader(path: path)
            defer { reader.close() }
            while let line = try reader.next() {
                guard let node = JSONLine.parse(line.data), node.isObject else { continue }
                guard let ts = ISO8601.seconds(node.timestamp.string) else { continue }
                if let start, ts < start { continue }
                if let end, ts > end { continue }
                raw.append(Raw(ts: ts, file: fileIndex, line: line.index, node: node))
            }
        }
        raw.sort { a, b in
            if a.ts != b.ts { return a.ts < b.ts }
            if a.file != b.file { return a.file < b.file }
            return a.line < b.line
        }
        return events(from: raw.map(\.node))
    }

    /// The record -> event walk, over records already sorted by time.
    static func events(from records: [JSONNode]) -> [Event] {
        var toolNames: [String: (String, String?)] = [:]  // tool_use_id -> (name, path)
        var out: [Event] = []

        for r in records {
            let ts = ISO8601.seconds(r.timestamp.string) ?? 0
            let type = r.type.string

            if type == "user" {
                let content = r.message.content
                let ps = r.promptSource.string
                let origin = r.origin.kind.string
                let isMeta = isTruthy(r.isMeta.raw)
                let text = textOf(content)
                if !isMeta && (ps == "typed" || (ps == "sdk" && origin == "human")) && !strip(text).isEmpty {
                    out.append(Event(ts: ts, kind: .prompt, text: mask(trunc(text, promptMax))))
                } else if text.hasPrefix(interruptPrefix) {
                    out.append(Event(ts: ts, kind: .interrupt))
                }

                if let blocks = content.array {
                    let tur = r.toolUseResult
                    for b in blocks where b.type.string == "tool_result" {
                        let tid = b.tool_use_id.string
                        let known = tid.flatMap { toolNames[$0] } ?? ("tool", nil)
                        let name = known.0
                        var path = known.1
                        let body = textOf(b.content)
                        let isErr = isTruthy(b.is_error.raw) || looksLikeError(body)
                        var added: Int?
                        var removed: Int?
                        if tur.isObject {
                            if let patch = tur.structuredPatch.array, !patch.isEmpty {
                                var a = 0
                                var d = 0
                                for hunk in patch {
                                    for ln in hunk.lines.array ?? [] {
                                        let s = pyStr(ln.raw)
                                        if s.hasPrefix("+") { a += 1 }
                                        if s.hasPrefix("-") { d += 1 }
                                    }
                                }
                                added = a
                                removed = d
                            } else if tur.type.string == "create", let created = tur.content.string {
                                let newlines = created.unicodeScalars.reduce(0) { $0 + ($1 == "\n" ? 1 : 0) }
                                added = newlines + (created.isEmpty ? 0 : 1)
                                removed = 0
                            }
                            if tur.file.isObject {
                                path = path ?? truthyString(tur.filePath) ?? truthyString(tur.file.filePath)
                            } else {
                                path = path ?? truthyString(tur.filePath)
                            }
                        }
                        if isErr {
                            out.append(
                                Event(
                                    ts: ts, kind: .resultError,
                                    text: mask(trunc(body.isEmpty ? "(error)" : body, errorMax)),
                                    tool: name, path: path, ok: false, toolID: tid))
                        } else if let added {
                            // Attach the line delta to the originating tool event.
                            for i in out.indices.reversed() where out[i].kind == .tool && out[i].toolID == tid {
                                out[i].added = added
                                out[i].removed = removed
                                out[i].path = path ?? out[i].path
                                break
                            }
                        }
                    }
                }
            } else if type == "assistant" {
                let msg = r.message
                let model = msg.model.string
                for b in msg.content.array ?? [] where b.isObject {
                    let bt = b.type.string
                    if bt == "text", let text = b.text.string, !strip(text).isEmpty {
                        out.append(
                            Event(ts: ts, kind: .assistant, text: mask(trunc(text, assistantMax)), model: model))
                    } else if bt == "tool_use" {
                        let (name, path, desc) = toolLine(b)
                        if let id = b.id.string { toolNames[id] = (name, path) }
                        var ev = Event(
                            ts: ts, kind: .tool, text: mask(desc), tool: name, path: path,
                            toolID: b.id.string, model: model)
                        if name == "Bash" {
                            let approx = bashFileEffect(b.input.command.string ?? "").approx
                            if let approx {
                                ev.added = approx
                                ev.removed = 0
                            }
                        }
                        out.append(ev)
                    }
                }
            } else if type == "attachment", r.attachment.type.string == "edited_text_file" {
                out.append(Event(ts: ts, kind: .humanEdit, path: r.attachment.filename.string))
            } else if type == "system", r.subtype.string == "compact_boundary" {
                out.append(Event(ts: ts, kind: .compaction))
            }
        }

        for i in out.indices { out[i].n = i }
        return out
    }

    // MARK: - Stats

    private static let gitCommit = regex(#"\bgit commit\b"#)
    private static let testRun = regex(#"\b(pytest|bun test|npm test|swift test|jest|cargo test|go test|make test)\b"#)

    public static func stats(_ events: [Event]) -> Stats {
        var st = Stats()
        st.events = events.count
        guard let first = events.first, let last = events.last else { return st }

        let prompts = events.filter { $0.kind == .prompt }
        let tools = events.filter { $0.kind == .tool }
        st.wallSeconds = Int((last.ts - first.ts).rounded(.toNearestOrEven))
        st.promptsSent = prompts.count
        st.repliesReceived = events.filter { $0.kind == .assistant }.count
        st.interrupts = events.filter { $0.kind == .interrupt }.count
        st.humanEdits = events.filter { $0.kind == .humanEdit }.count
        st.compactions = events.filter { $0.kind == .compaction }.count
        st.toolCalls = tools.count
        st.toolMix = mostCommon(tools.map { $0.tool ?? "tool" })
        st.errors = events.filter { $0.kind == .resultError }.count
        st.linesAddedAgent = tools.reduce(0) { $0 + ($1.added ?? 0) }
        st.linesRemovedAgent = tools.reduce(0) { $0 + ($1.removed ?? 0) }

        var files = Set<String>()
        var reads = Set<String>()
        for e in tools {
            guard let p = e.path, !p.isEmpty, let t = e.tool else { continue }
            if editTools.contains(t) || (shellTools.contains(t) && e.added != nil) { files.insert(p) }
            if t == "Read" { reads.insert(p) }
        }
        st.filesEdited = files.count
        st.filesWrittenViaShell = tools.filter { shellTools.contains($0.tool ?? "") && $0.added != nil }.count
        st.filesRead = reads.count
        st.gitCommitsRun = tools.filter { shellTools.contains($0.tool ?? "") && matches(gitCommit, $0.text) }.count
        st.testRuns = tools.filter { shellTools.contains($0.tool ?? "") && matches(testRun, $0.text) }.count
        st.models = mostCommon(events.compactMap { $0.kind == .assistant ? $0.model : nil }.filter { !$0.isEmpty })

        var longest: Double = 0
        for i in 1..<max(events.count, 1) where events.count > 1 {
            longest = max(longest, events[i].ts - events[i - 1].ts)
        }
        st.longestSilenceSeconds = Int(longest.rounded(.toNearestOrEven))
        return st
    }

    // MARK: - Rendering

    static func fmtT(_ t0: Double, _ ts: Double) -> String {
        String(format: "+%5.1fm", (ts - t0) / 60)
    }

    static func renderEvent(_ e: Event, t0: Double, tight: Bool) -> String {
        let t = fmtT(t0, e.ts)
        switch e.kind {
        case .prompt:
            return "[\(e.n)] \(t) PROMPT: \(pyJSONString(e.text))"
        case .interrupt:
            return "[\(e.n)] \(t) INTERRUPT (human stopped the agent)"
        case .humanEdit:
            return rstrip("[\(e.n)] \(t) HUMAN EDITED FILE \(e.path ?? "")")
        case .compaction:
            return "[\(e.n)] \(t) CONTEXT COMPACTED"
        case .assistant:
            let txt = trunc(e.text, tight ? assistantMaxTight : assistantMax)
            return "[\(e.n)] \(t) ASSISTANT: \(pyJSONString(txt))"
        case .tool:
            let tool = e.tool ?? "tool"
            let delta = e.added.map { " +\($0)/-\(e.removed ?? 0)" } ?? ""
            let body: String
            if shellTools.contains(tool) {
                body = e.text
            } else if let p = e.path, !p.isEmpty {
                body = p
            } else {
                body = e.text
            }
            return rstrip("[\(e.n)] \(t) \(tool)\(delta): \(body)", chars: [":", " "])
        case .resultError:
            return "[\(e.n)] \(t) ERROR from \(e.tool ?? "tool"): \(pyJSONString(e.text))"
        }
    }

    /// Consecutive tool calls (no prompt/assistant/error between) become one summary line.
    static func collapseToolRuns(_ events: [Event], t0: Double, minRun: Int = 4) -> [String] {
        var lines: [String] = []
        var i = 0
        while i < events.count {
            let e = events[i]
            if e.kind != .tool {
                lines.append(renderEvent(e, t0: t0, tight: true))
                i += 1
                continue
            }
            var j = i
            while j < events.count && events[j].kind == .tool { j += 1 }
            let run = Array(events[i..<j])
            if run.count < minRun {
                lines.append(contentsOf: run.map { renderEvent($0, t0: t0, tight: true) })
            } else {
                let mix = mostCommon(run.map { $0.tool ?? "tool" })
                let edited = run.compactMap { x -> String? in
                    guard let added = x.added, let p = x.path, !p.isEmpty else { return nil }
                    let base = p.split(separator: "/", omittingEmptySubsequences: false).last.map(String.init) ?? p
                    return "\(base) +\(added)/-\(x.removed ?? 0)"
                }.prefix(6)
                let cmds = run.filter { shellTools.contains($0.tool ?? "") }.map { x in
                    prefix(x.text.components(separatedBy: " ⏎ ").first ?? "", 40)
                }.prefix(4)
                let span = (run[run.count - 1].ts - run[0].ts) / 60
                let parts = mix.map { "\($0.0)×\($0.1)" }
                var detail = ""
                if !edited.isEmpty { detail += " edits: " + edited.joined(separator: ", ") }
                if !cmds.isEmpty { detail += " bash: " + cmds.joined(separator: " | ") }
                lines.append(
                    "[\(run[0].n)-\(run[run.count - 1].n)] \(fmtT(t0, run[0].ts)) TOOLS ×\(run.count) over "
                        + String(format: "%.1f", span) + "m: " + parts.joined(separator: ", ") + "." + detail)
            }
            i = j
        }
        return lines
    }

    /// Render under a character budget. Returns `(text, coverage)`.
    public static func render(events: [Event], meta: Meta, budget: Int = defaultBudget) -> (text: String, coverage: Double) {
        guard let first = events.first else { return ("# SESSION DIGEST\n(no events)\n", 1.0) }
        let t0 = first.ts
        let st = stats(events)

        var head = ["# SESSION DIGEST"]
        if let v = meta.repo { head.append("repo: \(v)") }
        if let v = meta.harness { head.append("harness: \(v)") }
        if let v = meta.startedAtLocal { head.append("started_at_local: \(v)") }
        if let v = meta.endReason { head.append("end_reason: \(v)") }
        if let v = meta.attendedSeconds { head.append("attended_seconds: \(v)") }
        if let v = meta.autonomousSeconds { head.append("autonomous_seconds: \(v)") }
        head.append(
            "wall: \(st.wallSeconds / 60)m  prompts sent: \(st.promptsSent)  replies: \(st.repliesReceived)  "
                + "tool calls: \(st.toolCalls)  errors: \(st.errors)  interrupts: \(st.interrupts)  "
                + "agent lines +\(st.linesAddedAgent)/-\(st.linesRemovedAgent)  files edited: \(st.filesEdited)  "
                + "git commits run: \(st.gitCommitsRun)  test runs: \(st.testRuns)")
        head.append("tool mix: " + st.toolMix.map { "\($0.0) \($0.1)" }.joined(separator: ", "))
        if !st.models.isEmpty {
            head.append("models: " + st.models.map { "\($0.0) (\($0.1) turns)" }.joined(separator: ", "))
        }
        head.append("")
        head.append("# TIMELINE  ([n] = event ordinal, +m = minutes from start)")
        let header = head.joined(separator: "\n") + "\n"
        let headerLen = plen(header)

        // Level 0: everything, generous truncation.
        var body = events.map { renderEvent($0, t0: t0, tight: false) }.joined(separator: "\n")
        if headerLen + plen(body) <= budget {
            return (header + body + "\n", 1.0)
        }

        // Level 1: tight assistant text, collapse tool runs.
        let lines = collapseToolRuns(events, t0: t0)
        body = lines.joined(separator: "\n")
        if headerLen + plen(body) <= budget {
            return (header + body + "\n", 1.0)
        }

        // Level 2: keep every prompt/error/interrupt/human-edit line and the first/last 12
        // lines; thin the rest evenly until it fits. Coverage reports the loss.
        let mustMarkers = [" PROMPT: ", " ERROR from ", " INTERRUPT", " HUMAN EDITED", " CONTEXT COMPACTED"]
        let must = lines.filter { ln in mustMarkers.contains { ln.contains($0) } }
        let mustSet = Set(must)
        let rest = lines.filter { !mustSet.contains($0) }
        let keepEdges = rest.count > 24 ? Array(rest[0..<12]) + Array(rest[(rest.count - 12)...]) : rest
        let middle = rest.count > 24 ? Array(rest[12..<(rest.count - 12)]) : []
        let budgetLeft =
            budget - headerLen
            - must.reduce(0) { $0 + plen($1) + 1 }
            - keepEdges.reduce(0) { $0 + plen($1) + 1 }
        var keptMiddle: [String] = []
        if !middle.isEmpty && budgetLeft > 0 {
            let avg = max(1, middle.reduce(0) { $0 + plen($1) + 1 } / middle.count)
            let nKeep = max(0, min(middle.count, budgetLeft / avg))
            if nKeep > 0 {
                let step = Double(middle.count) / Double(nKeep)
                keptMiddle = (0..<nKeep).map { middle[Int(Double($0) * step)] }
            }
        }
        let chosen = mustSet.union(keepEdges).union(keptMiddle)
        let bodyLines = lines.filter { chosen.contains($0) }
        let dropped = lines.count - bodyLines.count
        let coverage = Double(bodyLines.count) / Double(max(1, lines.count))
        let note =
            "\n(… \(dropped) of \(lines.count) timeline lines omitted to fit; every prompt and error is present. "
            + String(format: "coverage=%.2f", coverage) + ")\n"
        return (header + bodyLines.joined(separator: "\n") + note, (coverage * 1000).rounded(.toNearestOrEven) / 1000)
    }

    public static func hash(_ text: String) -> String { Hashing.sha256Hex(text) }

    /// Digest one session from its transcript files. Claude Code only for now: Cursor's
    /// bodies live in a SQLite store the digest has no loader for, and the Swift package
    /// has no Codex parser yet.
    public static func build(
        harness: Harness, transcripts: [String], start: Double?, end: Double?,
        meta: Meta, budget: Int = defaultBudget
    ) throws -> Output {
        guard harness == .claudeCode else { throw DigestError.unsupportedHarness(harness) }
        guard !transcripts.isEmpty else { throw DigestError.noTranscripts }
        var meta = meta
        if meta.harness == nil { meta.harness = harness.rawValue }
        let events = try loadClaudeCodeEvents(paths: transcripts, start: start, end: end)
        let (text, coverage) = render(events: events, meta: meta, budget: budget)
        return Output(text: text, coverage: coverage, hash: hash(text), stats: stats(events), events: events.count)
    }

    // MARK: - Python-parity helpers

    /// `len()` on a Python str counts code points.
    static func plen(_ s: String) -> Int { s.unicodeScalars.count }

    /// `s[:n]` on a Python str.
    static func prefix(_ s: String, _ n: Int) -> String {
        n >= s.unicodeScalars.count ? s : String(s.unicodeScalars.prefix(n))
    }

    /// `str.strip()` / `str.rstrip()` with no argument: whitespace only.
    static func strip(_ s: String) -> String {
        var t = Substring(s)
        while let f = t.first, f.isWhitespace { t.removeFirst() }
        while let l = t.last, l.isWhitespace { t.removeLast() }
        return String(t)
    }

    static func rstrip(_ s: String) -> String {
        var t = Substring(s)
        while let l = t.last, l.isWhitespace { t.removeLast() }
        return String(t)
    }

    /// `str.rstrip(chars)`: strip any trailing run drawn from the set.
    static func rstrip(_ s: String, chars: Set<Character>) -> String {
        var t = Substring(s)
        while let l = t.last, chars.contains(l) { t.removeLast() }
        return String(t)
    }

    /// `_trunc`: strip, then cut to `n` code points with a `…[+N]` tail that states how
    /// much was dropped, so the model knows a truncated line is truncated.
    static func trunc(_ s: String, _ n: Int) -> String {
        let t = strip(s)
        let count = t.unicodeScalars.count
        if count <= n { return t }
        let head = rstrip(String(t.unicodeScalars.prefix(n - 12)))
        return head + "…[+\(count - n + 12)]"
    }

    /// `json.dumps(text, ensure_ascii=False)`: quote, escape `"` and `\`, the five short
    /// escapes, `\u00xx` for the remaining control characters, everything else verbatim.
    static func pyJSONString(_ s: String) -> String {
        var out = "\""
        for u in s.unicodeScalars {
            switch u {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            case "\u{08}": out += "\\b"
            case "\u{0C}": out += "\\f"
            default:
                if u.value < 0x20 {
                    out += String(format: "\\u%04x", u.value)
                } else {
                    out.unicodeScalars.append(u)
                }
            }
        }
        return out + "\""
    }

    /// `json.dumps(obj, separators=(",", ":"))` with the default `ensure_ascii=True`,
    /// except that keys are sorted (see the type doc).
    static func compactJSON(_ obj: [String: Any]) -> String {
        guard JSONSerialization.isValidJSONObject(obj),
              let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys, .withoutEscapingSlashes])
        else { return "{}" }
        let text = String(decoding: data, as: UTF8.self)
        var out = ""
        for u in text.unicodeScalars {
            if u.value < 0x80 {
                out.unicodeScalars.append(u)
            } else if u.value <= 0xFFFF {
                out += String(format: "\\u%04x", u.value)
            } else {
                let v = u.value - 0x10000
                out += String(format: "\\u%04x\\u%04x", 0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF))
            }
        }
        return out
    }

    /// `_text_of`: a bare string, or the `text` of every text block joined by newlines.
    static func textOf(_ content: JSONNode) -> String {
        if let s = content.string { return s }
        if let arr = content.array {
            return arr.filter { $0.isObject && $0.type.string == "text" }
                .map { $0.text.string ?? "" }
                .joined(separator: "\n")
        }
        return ""
    }

    /// Python truthiness for the JSON scalars that reach `bool()` and `or` here.
    static func isTruthy(_ raw: Any?) -> Bool {
        guard let raw, !(raw is NSNull) else { return false }
        if let b = raw as? Bool { return b }
        if let s = raw as? String { return !s.isEmpty }
        if let n = raw as? NSNumber { return n.doubleValue != 0 }
        if let a = raw as? [Any] { return !a.isEmpty }
        if let d = raw as? [String: Any] { return !d.isEmpty }
        return true
    }

    /// A non-empty string, the way `a or b` treats `""` as absent.
    static func truthyString(_ n: JSONNode) -> String? {
        guard let s = n.string, !s.isEmpty else { return nil }
        return s
    }

    /// `str(x)` for the values `structuredPatch.lines` actually holds.
    static func pyStr(_ raw: Any?) -> String {
        if let s = raw as? String { return s }
        if let n = raw as? NSNumber { return n.stringValue }
        return raw.map { "\($0)" } ?? "None"
    }

    /// `Counter(items).most_common()`: count descending, ties in first-seen order —
    /// Python's sort is stable and `reverse=True` preserves that.
    static func mostCommon(_ items: [String]) -> [(String, Int)] {
        var counts: [String: Int] = [:]
        var order: [String] = []
        for i in items {
            if counts[i] == nil { order.append(i) }
            counts[i, default: 0] += 1
        }
        return order.enumerated()
            .map { (index: $0.offset, key: $0.element, count: counts[$0.element] ?? 0) }
            .sorted { a, b in a.count != b.count ? a.count > b.count : a.index < b.index }
            .map { ($0.key, $0.count) }
    }

    // MARK: - Regex helpers

    private static func regex(_ pattern: String) -> NSRegularExpression {
        // Every pattern here is a constant checked by the digest tests; a typo fails the
        // suite rather than a user's first analysis.
        do {
            return try NSRegularExpression(pattern: pattern)
        } catch {
            preconditionFailure("invalid digest regex \(pattern): \(error)")
        }
    }

    private static func matches(_ re: NSRegularExpression, _ s: String) -> Bool {
        re.firstMatch(in: s, range: NSRange(location: 0, length: (s as NSString).length)) != nil
    }

    private static func replaceAll(_ re: NSRegularExpression, in s: String, with template: String) -> String {
        re.stringByReplacingMatches(
            in: s, range: NSRange(location: 0, length: (s as NSString).length), withTemplate: template)
    }
}
