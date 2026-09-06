import Foundation

/// What a shell command did to a file.
///
/// Agents running in a permission mode that prefers the shell do not call `Write`. They
/// write files with `cat > path <<'EOF'`. MEASURED on such a session: 100 Bash calls, 0
/// Edit/Write calls, and every source file in the resulting commit created by a heredoc.
///
/// This lived inside `BuilderAnalysis.Digest` for its whole life, which is why the
/// analysis prompt and the session card disagreed about the same session: the digest read
/// heredocs and the INGEST PARSER did not, so a session could be described to the analyst
/// as "agent lines +2450" and rendered on the card as nothing at all. MEASURED on this
/// repository's own container corpus (17 root transcripts, 2026-09-06): 2,452 of 2,458
/// attributable lines were written through the shell, so the card's number was 0.2% of
/// the digest's. It is a `BuilderParse` type now because `BuilderAnalysis` depends on
/// `BuilderParse` and not the other way round, and both need it.
///
/// The count is the heredoc body length: approximate, labelled so, and better than zero.
public enum ShellFileEffect {

    private static let heredoc = regex(
        #"(?:cat|tee)\s*(?:>>?|-a\s+)?\s*(?<path>[\w./~\-]+)\s*<<\s*-?['"]?(?<delim>\w+)['"]?"#)
    /// ANY heredoc opener, not only the ones that write a file. Used to SKIP bodies; see
    /// `commandLines`. `<<<` is a here-STRING and takes no body, so it is excluded — and
    /// it is excluded from BOTH SIDES. `<<(?!<)` alone still matches the second and third
    /// `<` of `<<<`, which made `grep x <<< 'hello'` open a heredoc with the delimiter
    /// `hello` and swallow every command after it.
    private static let anyHeredoc = regex(#"(?<!<)<<(?!<)\s*-?\s*['"]?(?<delim>\w+)['"]?"#)
    private static let sedInPlace = regex(#"\bsed\s+-i\S*\s+.*?\s(?<path>[\w./~\-]+\.\w+)(?:\s|$)"#)

    /// The indices of the lines of `command` that are COMMANDS, with heredoc bodies
    /// skipped.
    ///
    /// A heredoc body is data. It can contain anything, including text that looks exactly
    /// like another shell command, and scanning it is how this parser read a piece of
    /// DOCUMENTATION as a file write. FOUND BY RUNNING IT on this repository's own corpus:
    /// CLAUDE.md contains the sentence "`analysis/digest.py` has read `cat > path <<'EOF'`
    /// writes since it was written", and that file is edited through `python3 - <<'PY' … PY`.
    /// The outer opener is not a `cat` or `tee`, so the old scan skipped past it, found the
    /// `cat > path <<'EOF'` INSIDE the prose, and attributed 134 lines to a file literally
    /// named `path` — a file that has never existed, on a corpus of 10,487 attributable
    /// lines. Mirrors `analysis/digest.py._command_lines`.
    private static func commandLines(_ lines: [String]) -> [Int] {
        var out: [Int] = []
        var i = 0
        while i < lines.count {
            out.append(i)
            let line = lines[i]
            let ns = line as NSString
            guard
                let m = anyHeredoc.firstMatch(
                    in: line, range: NSRange(location: 0, length: ns.length))
            else {
                i += 1
                continue
            }
            // Skip the body: up to and including the terminator, or the rest of the
            // command when there is none (a truncated call).
            let delim = ns.substring(with: m.range(withName: "delim"))
            var j = i + 1
            while j < lines.count,
                lines[j].trimmingCharacters(in: .whitespaces) != delim
            {
                j += 1
            }
            i = j + 1
        }
        return out
    }

    /// `(path, approx lines written)`. A `sed -i` names a path but has no countable
    /// magnitude, so it returns a path and a nil count: the file was touched, and by how
    /// much is not knowable from the command.
    ///
    /// The body is the lines strictly between the opener and the terminator. FOUND ON A
    /// REAL SESSION (Claude Code 2.1.261, `claude -p`, 2026-09-05):
    /// `mkdir -p tests && cat > tests/test_fail.py <<'EOF'\ndef test_fail():\n    assert 1
    /// == 2\nEOF\ngit add -A && git commit -m …` scored +3 under the older `newlines - 1`
    /// rule while git's own result said `2 insertions(+)` — the terminator line and the
    /// commands after it were being counted as file content. Mirrors
    /// `analysis/digest.py._bash_file_effect`, which the Python tests pin.
    public static func of(_ command: String) -> (path: String?, approx: Int?) {
        let lines = command.components(separatedBy: "\n")
        let commandIndices = commandLines(lines)

        for i in commandIndices {
            let line = lines[i]
            let ns = line as NSString
            guard
                let m = heredoc.firstMatch(
                    in: line, range: NSRange(location: 0, length: ns.length))
            else { continue }
            let delim = ns.substring(with: m.range(withName: "delim"))
            let body = Array(lines.dropFirst(i + 1))
            var n = 0
            var terminated = false
            for bodyLine in body {
                if bodyLine.trimmingCharacters(in: .whitespaces) == delim {
                    terminated = true
                    break
                }
                n += 1
            }
            // No terminator (a truncated command): the trailing empty line is not a line.
            if !terminated, let last = body.last, last.isEmpty { n -= 1 }
            return (ns.substring(with: m.range(withName: "path")), max(0, n))
        }

        for i in commandIndices {
            let line = lines[i]
            let ns = line as NSString
            if let m = sedInPlace.firstMatch(
                in: line, range: NSRange(location: 0, length: ns.length))
            {
                return (ns.substring(with: m.range(withName: "path")), nil)
            }
        }
        return (nil, nil)
    }

    private static func regex(_ pattern: String) -> NSRegularExpression {
        // Every pattern here is a constant pinned by the tests; a typo fails the suite rather
        // than silently attributing zero lines to every shell write a user makes.
        do {
            return try NSRegularExpression(pattern: pattern)
        } catch {
            preconditionFailure("invalid shell-effect regex \(pattern): \(error)")
        }
    }
}
