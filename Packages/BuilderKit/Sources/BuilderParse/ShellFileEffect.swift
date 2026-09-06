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
    private static let sedInPlace = regex(#"\bsed\s+-i\S*\s+.*?\s(?<path>[\w./~\-]+\.\w+)(?:\s|$)"#)

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
        let ns = command as NSString
        let whole = NSRange(location: 0, length: ns.length)
        if let m = heredoc.firstMatch(in: command, range: whole) {
            let delim = ns.substring(with: m.range(withName: "delim"))
            var lines = ns.substring(from: m.range.location + m.range.length)
                .components(separatedBy: "\n")
            lines.removeFirst()  // the rest of the opener line
            var n = 0
            var terminated = false
            for line in lines {
                if line.trimmingCharacters(in: .whitespaces) == delim {
                    terminated = true
                    break
                }
                n += 1
            }
            // No terminator (a truncated command): the trailing empty line is not a line.
            if !terminated, let last = lines.last, last.isEmpty { n -= 1 }
            return (ns.substring(with: m.range(withName: "path")), max(0, n))
        }
        if let m = sedInPlace.firstMatch(in: command, range: whole) {
            return (ns.substring(with: m.range(withName: "path")), nil)
        }
        return (nil, nil)
    }

    private static func regex(_ pattern: String) -> NSRegularExpression {
        // Both patterns are constants pinned by the tests; a typo fails the suite rather
        // than silently attributing zero lines to every shell write a user makes.
        do {
            return try NSRegularExpression(pattern: pattern)
        } catch {
            preconditionFailure("invalid shell-effect regex \(pattern): \(error)")
        }
    }
}
