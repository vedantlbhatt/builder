import BuilderIngest
import BuilderModel
import BuilderParse
import Foundation
import Testing

/// The Swift half of the session-boundary conformance gate.
///
/// `scripts/measure_boundaries.py` is the reference implementation of the v3 rules and
/// `scripts/gen_boundary_fixtures.py` records what it says about the synthetic
/// transcripts under `spec/fixtures/boundaries/`. This suite runs the same bytes through
/// the real parser and the real sessionizer and must agree on every cut: count, end
/// reason, start and end (±1 s), active/attended/autonomous (±1 s), prompts, presence.
/// A fixture whose expected.json carries `tau.mode == "auto"` must first reproduce the
/// reference's FITTED threshold (to 1e-6) from the same events; the cases under
/// `cross_pool/` carry two native session ids and are pooled per id, since
/// `switched_repo` is a rule about what happened in the OTHER pool.
///
/// Every fixture is stamped in America/New_York on purpose. The 04:00 rule is exercised
/// against DST-real offsets, and a sessionizer that reads the machine's zone instead of
/// the one it is handed would pass on one continent and fail on another.
@Suite("Session boundaries — reference fixtures")
struct BoundaryFixtureTests {

    struct Expected: Decodable {
        struct Session: Decodable {
            let startedAt: Double
            let endedAt: Double
            let activeSeconds: Double
            let attendedSeconds: Double
            let autonomousSeconds: Double
            let prompts: Int
            let presence: Int
            let endReason: String
            /// Cross-pool cases only: the native session id the session belongs to.
            let pool: String?

            enum CodingKeys: String, CodingKey {
                case startedAt = "started_at"
                case endedAt = "ended_at"
                case activeSeconds = "active_seconds"
                case attendedSeconds = "attended_seconds"
                case autonomousSeconds = "autonomous_seconds"
                case prompts
                case presence
                case endReason = "end_reason"
                case pool
            }
        }

        struct Tau: Decodable {
            let mode: String
            let value: Double
            let sessionsAtFallback: Int

            enum CodingKeys: String, CodingKey {
                case mode, value
                case sessionsAtFallback = "sessions_at_fallback"
            }
        }

        let tz: String
        let records: Int
        let sessions: [Session]
        /// Present when the reference fitted the threshold rather than using the fallback.
        let tau: Tau?
    }

    /// The subdirectory of two-pool cases.
    static let crossPool = "cross_pool"

    /// Walk up from this file to the repo root, exactly as `StripGoldenTests` does. The
    /// fixtures are shared with the Python reference, so they live in `spec/`.
    static var fixtureDirectory: URL? {
        var dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            let candidate = dir.appendingPathComponent("spec/fixtures/boundaries")
            if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
            dir = dir.deletingLastPathComponent()
        }
        return nil
    }

    /// The zone every fixture was generated in.
    static var newYork: Calendar {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York")!
        return cal
    }

    static func fixtureNames(in sub: String? = nil) throws -> [String] {
        guard var dir = fixtureDirectory else { return [] }
        if let sub { dir = dir.appendingPathComponent(sub) }
        return try FileManager.default.contentsOfDirectory(atPath: dir.path)
            .filter { $0.hasSuffix(".jsonl") }
            .map { String($0.dropLast(".jsonl".count)) }
            .sorted()
    }

    static func expected(for name: String, in sub: String? = nil) throws -> Expected {
        guard var dir = fixtureDirectory else {
            throw NSError(domain: "BoundaryFixtureTests", code: 1)
        }
        if let sub { dir = dir.appendingPathComponent(sub) }
        let data = try Data(contentsOf: dir.appendingPathComponent(name + ".expected.json"))
        return try JSONDecoder().decode(Expected.self, from: data)
    }

    /// Run raw JSONL through the REAL parser: a temp tree shaped like `~/.claude/projects`
    /// (`<root>/<projectdir>/<uuid>.jsonl`, so the file is a root transcript, not a
    /// sidecar), discovered and parsed from a fresh watermark.
    static func parse(jsonl: Data) throws -> [NormalizedEvent] {
        let tmp = NSTemporaryDirectory() + "builder-boundaries-\(UUID().uuidString)"
        let proj = tmp + "/proj"
        try FileManager.default.createDirectory(atPath: proj, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(atPath: tmp) }

        let dest = proj + "/00000000-0000-4000-8000-000000000001.jsonl"
        try jsonl.write(to: URL(fileURLWithPath: dest))

        let parser = ClaudeCodeParser(projectsRoot: tmp)
        var events: [NormalizedEvent] = []
        for source in try parser.discover() where !source.isSidecar {
            let result = try parser.parse(
                source: source,
                from: Watermark(sourceID: source.sourceID, parserVersion: parser.parserVersion))
            events.append(contentsOf: result.events)
        }
        return events
    }

    static func parse(lines: [String]) throws -> [NormalizedEvent] {
        try parse(jsonl: Data((lines.joined(separator: "\n") + "\n").utf8))
    }

    static func events(for name: String, in sub: String? = nil) throws -> [NormalizedEvent] {
        guard var dir = fixtureDirectory else { return [] }
        if let sub { dir = dir.appendingPathComponent(sub) }
        return try parse(jsonl: Data(contentsOf: dir.appendingPathComponent(name + ".jsonl")))
    }

    /// The threshold a fixture is cut at: the reference's fitted value when its
    /// expected.json says `auto` (and this build must reproduce it), else the fallback.
    static func tau(for name: String, in sub: String? = nil, events: [NormalizedEvent]) throws -> Double {
        guard let auto = try expected(for: name, in: sub).tau, auto.mode == "auto" else {
            return Tuning.tauSessionSec
        }
        let options = Sessionizer.Options(pooling: .nativeSession, calendar: newYork)
        let fitted = SessionThresholds.fitted(gaps: Sessionizer.presenceGaps(from: events, options: options))
        #expect(fitted.fit.source == .fitted, "\(name): the reference fitted tau; this build fell back (\(fitted.fit.reason))")
        #expect(abs(fitted.tau - auto.value) < 1e-6, "\(name): fitted tau \(fitted.tau) vs reference \(auto.value)")
        return fitted.tau
    }

    static func sessions(
        for name: String, in sub: String? = nil, pooling: Sessionizer.Pooling = .nativeSession
    ) throws -> [DetectedSession] {
        let events = try events(for: name, in: sub)
        let tau = try tau(for: name, in: sub, events: events)
        let options = Sessionizer.Options(tau: tau, pooling: pooling, calendar: newYork)
        return Sessionizer.sessions(from: events, options: options)
    }

    // MARK: - The gate

    @Test func fixturesExist() throws {
        let names = try Self.fixtureNames()
        #expect(names.count >= 12, "boundary fixtures missing — run scripts/gen_boundary_fixtures.py")
        #expect(try Self.fixtureNames(in: Self.crossPool).count >= 2, "cross-pool fixtures missing")
    }

    @Test func reproducesTheReferenceCuts() throws {
        for sub in [nil, Self.crossPool] {
            for name in try Self.fixtureNames(in: sub) {
                try Self.check(name, in: sub)
            }
        }
    }

    static func check(_ name: String, in sub: String?) throws {
            let exp = try Self.expected(for: name, in: sub)
            #expect(exp.tz == "America/New_York", "\(name): unexpected fixture zone")
            let got = try Self.sessions(for: name, in: sub)

            #expect(got.count == exp.sessions.count,
                    "\(name): \(got.count) sessions, reference says \(exp.sessions.count)")
            guard got.count == exp.sessions.count else { return }

            for (i, pair) in zip(got, exp.sessions).enumerated() {
                let (g, e) = pair
                let tag = "\(name)[\(i)]"
                if let pool = e.pool {
                    #expect(g.poolKey.hasSuffix(pool), "\(tag): pool \(g.poolKey) vs \(pool)")
                }
                #expect(g.endReason.rawValue == e.endReason,
                        "\(tag): end reason \(g.endReason.rawValue) vs \(e.endReason)")
                #expect(abs(g.startedAt - e.startedAt) <= 1.0,
                        "\(tag): started_at \(g.startedAt) vs \(e.startedAt)")
                #expect(abs(g.endedAt - e.endedAt) <= 1.0,
                        "\(tag): ended_at \(g.endedAt) vs \(e.endedAt)")
                #expect(abs(g.activeSeconds - e.activeSeconds) <= 1.0,
                        "\(tag): active \(g.activeSeconds) vs \(e.activeSeconds)")
                #expect(abs(g.attendedSeconds - e.attendedSeconds) <= 1.0,
                        "\(tag): attended \(g.attendedSeconds) vs \(e.attendedSeconds)")
                #expect(abs(g.autonomousSeconds - e.autonomousSeconds) <= 1.0,
                        "\(tag): autonomous \(g.autonomousSeconds) vs \(e.autonomousSeconds)")
                #expect(g.promptCount == e.prompts, "\(tag): prompts \(g.promptCount) vs \(e.prompts)")
                #expect(g.presenceCount == e.presence, "\(tag): presence \(g.presenceCount) vs \(e.presence)")
            }
    }

    /// `active == attended + autonomous`, always, and `active <= elapsed` — the server's
    /// sanity gate rejects a payload where moving time exceeds elapsed time.
    @Test func activeIsAttendedPlusAutonomousAndNeverExceedsElapsed() throws {
        for name in try Self.fixtureNames() {
            for s in try Self.sessions(for: name) {
                #expect(abs(s.activeSeconds - (s.attendedSeconds + s.autonomousSeconds)) < 0.001,
                        "\(name): \(s.activeSeconds) != \(s.attendedSeconds) + \(s.autonomousSeconds)")
                #expect(s.activeSeconds <= s.wallSeconds + 0.001,
                        "\(name): active \(s.activeSeconds) exceeds elapsed \(s.wallSeconds)")
                #expect(s.attendedSeconds >= 0 && s.autonomousSeconds >= 0)
            }
        }
    }

    /// Which fixture ends how is the design in miniature; if these move, read
    /// docs/session-boundaries.md before touching the constant that moved them.
    @Test func endReasonsMatchTheDesign() throws {
        func reasons(_ name: String) throws -> [EndReason] {
            try Self.sessions(for: name).map(\.endReason)
        }
        // An attended sitting across 04:00 is ONE session: the day rule never splits it.
        #expect(try reasons("attended_across_4am") == [.stillRunning])
        // Lunch: 50 autonomous minutes inside an afternoon is still your sitting.
        #expect(try reasons("lunch_autonomy_no_split") == [.stillRunning])
        // Kickoff at 23:00, run through 04:00, human back at 09:00 after an idle gap.
        #expect(try reasons("overnight_run_day_boundary") == [.dayBoundary, .idleGap, .stillRunning])
        // A loop that never stops: split at 04:00, then cut the instant the human types.
        #expect(try reasons("endless_loop_human_returns") == [.dayBoundary, .humanReturned, .stillRunning])
        // A day-boundary piece with no presence at all is unattended, never notable.
        let robot = try Self.sessions(for: "robot_thirty_hours")
        #expect(robot.allSatisfy { $0.presenceCount == 0 && $0.unattended && !$0.notable })
        // The 04:00 piece is a cut: the work continued, so it is never announced. The
        // still-running piece IS the run that will be announced when silence ends it.
        #expect(robot.filter(\.isCut).allSatisfy { !$0.runFinished }, "a cut is not a finished run")
        let livePieces = robot.filter { !$0.isCut }
        #expect(livePieces.allSatisfy { $0.runFinished }, "the live piece is the run")
        // And a robot's first human visitor opens a new session rather than joining it.
        #expect(try reasons("robot_then_human_arrives") == [.humanReturned, .stillRunning])

        // v3. `/clear` ends a session 3 s before the next prompt — no gap could have — and
        // a `/clear` as the LAST record leaves the pool final, not live. Both are announced.
        let cleared = try Self.sessions(for: "cleared_twice")
        #expect(cleared.map(\.endReason) == [.cleared, .cleared])
        #expect(cleared.allSatisfy { $0.isStructuralEnd && $0.isFinalOnDerivation && !$0.isCut })
        // `/model` in that fixture is neither a prompt nor presence: 2 prompts, 4 presence
        // (two prompts, two clears) across the two sessions.
        #expect(cleared.reduce(0) { $0 + $1.promptCount } == 2)
        #expect(cleared.reduce(0) { $0 + $1.presenceCount } == 4)

        // The fitted threshold cuts a 700 s mid-sitting silence that the fallback lets pass.
        let fitted = try Self.sessions(for: "auto_tau_bimodal")
        let atFallback = Sessionizer.sessions(
            from: try Self.events(for: "auto_tau_bimodal"),
            options: .init(pooling: .nativeSession, calendar: Self.newYork))
        #expect(fitted.count == atFallback.count + 1)
        let sessionsAtFallback = try Self.expected(for: "auto_tau_bimodal").tau?.sessionsAtFallback
        #expect(atFallback.count == sessionsAtFallback)

        // Two pools: the human opening B cuts A (`switched_repo`); A's late records open a
        // fresh session with nobody in it; B is untouched. A headless start in B does not.
        #expect(try Self.sessions(for: "switched_repo_two_pools", in: Self.crossPool).map(\.endReason)
                == [.switchedRepo, .stillRunning, .stillRunning])
        #expect(try Self.sessions(for: "headless_start_elsewhere_no_switch", in: Self.crossPool).map(\.endReason)
                == [.stillRunning, .stillRunning])
    }

    /// The coordinator's real-input shape: prompts stamped with the shell's home cwd, tool
    /// calls with the repository, interleaved within seconds. Under the product's
    /// per-event `.repository` pooling this was TWO overlapping sessions — the prompts in
    /// one, the commits in the other. The lineage fold makes it one, with everything in it.
    @Test func cwdChangesInsideOneConversationDoNotSplitTheSitting() throws {
        let events = try Self.events(for: "cwd_interleaved_one_sitting")
        let cwds = Set(events.compactMap(\.cwd))
        #expect(cwds.count == 2, "the fixture must carry two cwds, got \(cwds)")
        let perEvent = Sessionizer.sessions(
            from: events, options: .init(pooling: .repository, calendar: Self.newYork))
        #expect(perEvent.count == 1, "lineage fold: one sitting, not \(perEvent.count)")
        guard let s = perEvent.first else { return }
        #expect(s.promptCount == 5)
        #expect(s.eventCount == events.count, "every event of the conversation is in the one session")
        #expect(s.endReason == .stillRunning)
        // The same under native-session pooling, which the reference used.
        #expect(try Self.sessions(for: "cwd_interleaved_one_sitting").count == 1)
    }

    /// Two conversations in ONE pool are one sitting (v1 rule, unchanged): the cross-pool
    /// fixture pooled by an explicit constant key never fires `switched_repo`.
    @Test func twoConversationsInOnePoolAreOneSitting() throws {
        let events = try Self.events(for: "switched_repo_two_pools", in: Self.crossPool)
        let one = Sessionizer.sessions(
            from: events,
            options: .init(pooling: .explicit { _ in "one" }, calendar: Self.newYork))
        #expect(one.map(\.endReason) == [.stillRunning])
    }

    // MARK: - Presence signals in the parser

    @Test func remoteSdkHumanPromptsArePrompts() throws {
        let events = try Self.events(for: "remote_sdk_prompts")
        // 3 sdk/human prompts, one isMeta injection (not a prompt), one interrupt.
        #expect(events.filter { $0.kind == .prompt }.count == 3)
        #expect(events.filter { $0.kind == .interrupt }.count == 1)
        #expect(events.filter { $0.kind.isPresence }.count == 4)
    }

    @Test func promptClassificationFollowsTheRecordShape() throws {
        func user(_ extra: String, content: String = "\"hello\"") -> String {
            "{\"type\":\"user\",\"uuid\":\"u-\(UUID().uuidString)\",\"sessionId\":\"s\","
                + "\"timestamp\":\"2026-03-10T13:00:00.000Z\",\"cwd\":\"/p\","
                + "\"message\":{\"role\":\"user\",\"content\":\(content)}\(extra)}"
        }
        let lines = [
            user(",\"promptSource\":\"typed\""),                                            // 0 prompt
            user(",\"promptSource\":\"sdk\",\"origin\":{\"kind\":\"human\"}"),                // 1 prompt
            user(",\"promptSource\":\"sdk\",\"origin\":{\"kind\":\"agent\"}"),                // 2 not
            user(",\"promptSource\":\"sdk\""),                                              // 3 not
            user(",\"promptSource\":\"typed\",\"isMeta\":true"),                             // 4 not
            user(",\"promptSource\":\"sdk\",\"origin\":{\"kind\":\"human\"},\"isMeta\":true"),// 5 not
            user(""),                                                                       // 6 not
        ]
        let kinds = try Self.parse(lines: lines).sorted { $0.ordinal < $1.ordinal }.map(\.kind)
        #expect(kinds == [.prompt, .prompt, .noise, .noise, .noise, .noise, .noise])
    }

    /// `/clear` is the one slash command that is a boundary and a presence signal; the
    /// others stay noise. The record shape is the harness's: no promptSource, the command
    /// named in the text.
    @Test func clearIsPresenceAndABoundaryOtherSlashCommandsAreNoise() throws {
        func user(_ content: String, extra: String = "") -> String {
            "{\"type\":\"user\",\"uuid\":\"u-\(UUID().uuidString)\",\"sessionId\":\"s\","
                + "\"timestamp\":\"2026-03-10T13:00:00.000Z\",\"cwd\":\"/p\","
                + "\"message\":{\"role\":\"user\",\"content\":\(content)}\(extra)}"
        }
        let lines = [
            user("\"<command-name>/clear</command-name>\\n<command-message>clear</command-message>\\n<command-args></command-args>\""),
            user("\"<command-name>/model</command-name>\\n<command-message>model</command-message>\\n<command-args></command-args>\""),
            user("[{\"type\":\"text\",\"text\":\"<command-name>/clear</command-name>\"}]"),
            // Mentioning the marker inside a typed prompt is a prompt, not a clear.
            user("\"why does <command-name>/clear</command-name> appear in my logs\"",
                 extra: ",\"promptSource\":\"typed\""),
        ]
        let kinds = try Self.parse(lines: lines).sorted { $0.ordinal < $1.ordinal }.map(\.kind)
        #expect(kinds == [.clear, .noise, .clear, .prompt])
        #expect(EventKind.clear.isPresence && !EventKind.clear.isMeaningful && EventKind.clear.isSubstantive)
    }

    @Test func interruptIsAPresenceSignalButNotAPrompt() throws {
        func user(_ content: String) -> String {
            "{\"type\":\"user\",\"uuid\":\"u-\(UUID().uuidString)\",\"sessionId\":\"s\","
                + "\"timestamp\":\"2026-03-10T13:00:00.000Z\",\"cwd\":\"/p\","
                + "\"message\":{\"role\":\"user\",\"content\":\(content)}}"
        }
        let lines = [
            // String content, the shape the fixtures use.
            user("\"[Request interrupted by user]\""),
            // Block content, the shape a tool-use interrupt writes.
            user("[{\"type\":\"text\",\"text\":\"[Request interrupted by user for tool use]\"}]"),
            // The sentinel must begin the text; a mention of it is not an interrupt.
            user("\"please do not write [Request interrupted by user] anywhere\""),
            // A tool result alone is never an interrupt, whatever it says.
            user("[{\"type\":\"tool_result\",\"tool_use_id\":\"t1\",\"content\":\"[Request interrupted by user for tool use]\"}]"),
        ]
        let events = try Self.parse(lines: lines).sorted { $0.ordinal < $1.ordinal }
        let kinds = events.map(\.kind)
        #expect(kinds == [.interrupt, .interrupt, .noise, .toolResult])

        #expect(EventKind.interrupt.isPresence)
        #expect(EventKind.interrupt.isSubstantive)
        #expect(!EventKind.interrupt.isMeaningful)
        #expect(EventKind.prompt.isPresence && EventKind.humanEdit.isPresence)
        #expect(!EventKind.toolUse.isPresence && !EventKind.noise.isPresence)
    }

    // MARK: - The 04:00 rule across DST

    /// 2026-03-08 in America/New_York: 02:00 EST becomes 03:00 EDT, so 04:00 arrives 23
    /// real hours after the previous 04:00. Built from calendar components, not by adding
    /// 86,400; the reference values come from Python's zoneinfo.
    @Test func nextDayBoundaryAcrossSpringForward() {
        let cal = Self.newYork
        let fourAMEDT = 1_772_956_800.0  // 2026-03-08T04:00:00-04:00

        // 01:30 EST, before the jump: the boundary is 1.5 real hours away, not 2.5.
        #expect(Sessionizer.nextDayBoundary(after: 1_772_951_400, calendar: cal) == fourAMEDT)
        // 03:30 EDT, after the jump: half an hour away.
        #expect(Sessionizer.nextDayBoundary(after: 1_772_955_000, calendar: cal) == fourAMEDT)
        // 23:30 EST the evening before: 3.5 real hours, not 4.5.
        #expect(Sessionizer.nextDayBoundary(after: 1_772_944_200, calendar: cal) == fourAMEDT)
        // Exactly 04:00 is not strictly after itself: the NEXT 04:00, a full day on.
        #expect(Sessionizer.nextDayBoundary(after: fourAMEDT, calendar: cal) == 1_773_043_200)
        // An ordinary afternoon: 14:00 EDT -> 04:00 EDT next day, 14 hours.
        #expect(Sessionizer.nextDayBoundary(after: 1_773_165_600, calendar: cal) == 1_773_216_000)
    }

    /// 2026-11-01: 02:00 EDT becomes 01:00 EST, so 04:00 arrives 25 real hours after the
    /// previous 04:00 and 10 hours after 19:00 the evening before.
    @Test func nextDayBoundaryAcrossFallBack() {
        let cal = Self.newYork
        let fourAMEST = 1_793_523_600.0  // 2026-11-01T04:00:00-05:00
        #expect(Sessionizer.nextDayBoundary(after: 1_793_487_600, calendar: cal) == fourAMEST)
        // The second 01:30 of the night (EST): 2.5 hours away.
        #expect(Sessionizer.nextDayBoundary(after: 1_793_514_600, calendar: cal) == fourAMEST)
    }

    /// The boundary is read from the calendar it is HANDED, not the machine's. The same
    /// instant is a different local hour in Tokyo.
    @Test func nextDayBoundaryHonoursTheCalendarZone() {
        var tokyo = Calendar(identifier: .gregorian)
        tokyo.timeZone = TimeZone(identifier: "Asia/Tokyo")!
        // 2026-03-08T08:00:00Z is 04:00 EDT and 17:00 JST.
        let ny = Sessionizer.nextDayBoundary(after: 1_772_956_800, calendar: Self.newYork)
        let jp = Sessionizer.nextDayBoundary(after: 1_772_956_800, calendar: tokyo)
        #expect(ny == 1_773_043_200)
        // 04:00 JST on 2026-03-09 is 2026-03-08T19:00:00Z.
        #expect(jp == 1_772_996_400)
    }
}
