import BuilderModel
import BuilderParse
import Foundation
import Testing

/// A file written through the shell has to reach the card, not only the analyst.
///
/// `SessionDigest` has read heredocs since it was written; `ClaudeCodeParser` did not.
/// One session therefore produced two different numbers: the analysis prompt was handed
/// "agent lines +2450" and the session card beside that prose said nothing at all.
/// MEASURED on this repository's own container corpus (17 root transcripts, 2026-09-06):
/// 2,452 of 2,458 attributable lines were written with `cat > path <<'EOF'`, so the card
/// was showing 0.2% of the work. `capture/tests/test_shell_writes.py` is the Python half.
@Suite("Shell writes reach the ingest parser")
struct ShellWriteTests {

    static let body = ["def one():", "    return 1", "", "def two():", "    return 2"]

    static func transcript(_ command: String) -> [String] {
        let escaped =
            String(data: try! JSONSerialization.data(
                withJSONObject: [command], options: [.fragmentsAllowed]), encoding: .utf8)!
            .dropFirst().dropLast()  // the JSON array's brackets; keeps the quoted string
        return [
            """
            {"type":"user","uuid":"u1","parentUuid":null,\
            "sessionId":"00000000-0000-4000-8000-000000000001",\
            "timestamp":"2026-03-10T15:00:00.000Z","cwd":"/Users/dev/proj","version":"2.1.0",\
            "message":{"role":"user","content":"write the module"},"promptSource":"typed"}
            """,
            """
            {"type":"assistant","uuid":"a1","parentUuid":"u1",\
            "sessionId":"00000000-0000-4000-8000-000000000001",\
            "timestamp":"2026-03-10T15:00:20.000Z","cwd":"/Users/dev/proj","version":"2.1.0",\
            "message":{"role":"assistant","model":"claude-sonnet-5","id":"msg_a",\
            "content":[{"type":"tool_use","id":"tu_a","name":"Bash","input":{"command":\(escaped)}}],\
            "usage":{"input_tokens":1,"output_tokens":1}}}
            """,
        ]
    }

    static func heredocCommand() -> String {
        "mkdir -p pkg && cat > pkg/thing.py <<'EOF'\n" + body.joined(separator: "\n")
            + "\nEOF\ngit add -A"
    }

    @Test func aHeredocBodyIsAttributedToTheFileItWrote() throws {
        let events = try BoundaryFixtureTests.parse(lines: Self.transcript(Self.heredocCommand()))
        let bash = try #require(events.first { $0.toolName == "Bash" })
        // The `EOF` terminator and the `git add -A` that follows it are not file content:
        // counting them scored +3 on a two-line file against git's own `2 insertions(+)`.
        #expect(bash.linesAdded == Self.body.count)
        #expect(bash.targetPath == "pkg/thing.py")
    }

    @Test func theSessionTotalIsNoLongerZero() throws {
        let events = try BoundaryFixtureTests.parse(lines: Self.transcript(Self.heredocCommand()))
        // This is the number the card renders and the uploader sends. Before the parser
        // read heredocs it was 0 for every shell-written session.
        #expect(TokenAccountant.agentLines(events).added == Self.body.count)
    }

    @Test func aCommandThatWritesNothingAttributesNothing() throws {
        let events = try BoundaryFixtureTests.parse(lines: Self.transcript("swift test"))
        let bash = try #require(events.first { $0.toolName == "Bash" })
        #expect(bash.linesAdded == nil)
        #expect(bash.targetPath == nil)
        #expect(TokenAccountant.agentLines(events).added == 0)
    }

    @Test func sedInPlaceNamesTheFileWithoutInventingAMagnitude() throws {
        let events = try BoundaryFixtureTests.parse(
            lines: Self.transcript("sed -i '' 's/a/b/' lib/util.py"))
        let bash = try #require(events.first { $0.toolName == "Bash" })
        // The file was touched; how much of it changed is not knowable from the command,
        // and a guess here would feed `attribution` a number it would treat as measured.
        #expect(bash.targetPath == "lib/util.py")
        #expect(bash.linesAdded == nil)
    }

    @Test func theParserAndTheDigestApplyTheSameRule() {
        // They are one function now. This asserts the forwarder still points at it, which
        // is the whole reason the two numbers can no longer disagree.
        let cmd = Self.heredocCommand()
        #expect(ShellFileEffect.of(cmd).approx == Self.body.count)
        #expect(ShellFileEffect.of(cmd).path == "pkg/thing.py")
    }
}
