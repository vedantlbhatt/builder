import BuilderGit
import BuilderIngest
import BuilderModel
import BuilderParse
import Foundation

// The agent and the CLI are the same binary. Everything the menu bar app will do, this
// does first — which means the whole engine is testable with `swift run`, before any
// Xcode project, signing identity or provisioning profile exists.

let args = Array(CommandLine.arguments.dropFirst())
let command = args.first ?? "help"

func flagValue(_ name: String) -> String? {
    guard let i = args.firstIndex(of: "--\(name)"), i + 1 < args.count else { return nil }
    return args[i + 1]
}
func hasFlag(_ name: String) -> Bool { args.contains("--\(name)") }

func fmtInt(_ n: Int) -> String {
    let f = NumberFormatter()
    f.numberStyle = .decimal
    return f.string(from: NSNumber(value: n)) ?? "\(n)"
}
func fmtDuration(_ seconds: Double) -> String {
    let s = Int(seconds.rounded())
    let h = s / 3600
    let m = (s % 3600) / 60
    return h > 0 ? "\(h)h \(m)m" : "\(m)m"
}

// MARK: - scan

func runScan() throws {
    let started = Date()
    let parser = ClaudeCodeParser()
    let sources = try parser.discover()

    let roots = sources.filter { !$0.isSidecar }
    let sidecars = sources.filter(\.isSidecar)

    print("SOURCES")
    print("  .jsonl files          \(fmtInt(sources.count))")
    print("  root transcripts      \(fmtInt(roots.count))")
    print("  subagent sidecars     \(fmtInt(sidecars.count))   (parsed for timeline, never for usage)")

    var events: [NormalizedEvent] = []
    var diagnostics: [String: Int] = [:]
    var lineCount = 0

    // Sidecars are parsed too — they carry real tool calls and real timeline — but the
    // parser marks them non-authoritative for usage, which is what stops the ~3x
    // subagent double count that a `**/*.jsonl` glob produces.
    for src in sources {
        let r = try parser.parseAll(source: src)
        events.append(contentsOf: r.events)
        lineCount += r.watermark.lineCount
        for d in r.diagnostics { diagnostics[d.code, default: 0] += 1 }
    }

    // Live-path resolution is per source: the DAG never spans files.
    var liveBySource: [String: Set<String>] = [:]
    for (sid, group) in Dictionary(grouping: events, by: \.sourceID) {
        liveBySource[sid] = LivePathResolver.liveEventIDs(in: group)
    }
    for i in events.indices {
        if let live = liveBySource[events[i].sourceID], let id = events[i].nativeEventID {
            events[i].onLivePath = live.contains(id)
        }
    }

    let parseSeconds = Date().timeIntervalSince(started)

    print("")
    print("RECORDS")
    print("  lines read            \(fmtInt(lineCount))")
    print("  normalized events     \(fmtInt(events.count))   (one record can yield several)")
    print("  with a timestamp      \(fmtInt(events.filter { $0.ts != nil }.count))")
    print("  without               \(fmtInt(events.filter { $0.ts == nil }.count))   (bookkeeping; never imputed)")

    let byKind = Dictionary(grouping: events, by: \.kind).mapValues(\.count)
    let kindLine = EventKind.allCases
        .compactMap { k in byKind[k].map { "\(k.rawValue) \(fmtInt($0))" } }
        .joined(separator: " · ")
    print("  kinds                 \(kindLine)")

    // THE TWO INDEPENDENT OVERCOUNTS, printed together because each hides the other.
    //
    //   1. Content-block duplication. Claude Code repeats the identical `usage` object on
    //      every content block of a message. Deduplicating by message.id removes it.
    //   2. Subagent double counting. Sidecar transcripts hold the same tokens the parent's
    //      `Agent` tool result already reports in aggregate. Deduplication does NOT remove
    //      this one — the ids genuinely differ — so a correctly-deduplicated total that
    //      globbed `**/*.jsonl` is still inflated.
    let naive = TokenAccountant.naiveTotal(events)
    let dedupedAllFiles = TokenAccountant.ledger(
        events.map { var e = $0; e.isSidechain = false; return e }, harness: .claudeCode)
    let ledger = TokenAccountant.ledger(events, harness: .claudeCode)

    func ratio(_ a: Int, _ b: Int) -> String {
        b > 0 ? String(format: "%.3f", Double(a) / Double(b)) : "-"
    }

    print("")
    print("TOKENS")
    print("  naive sum                     \(fmtInt(naive.displayTotal))")
    print("  deduped, all files            \(fmtInt(dedupedAllFiles.buckets.displayTotal))"
        + "   \(ratio(naive.displayTotal, dedupedAllFiles.buckets.displayTotal))x less")
    print("  deduped, parent-aggregated    \(fmtInt(ledger.buckets.displayTotal))   <- the honest number")
    print("  total overcount avoided       \(ratio(naive.displayTotal, ledger.buckets.displayTotal))x")
    print("  dedupe basis          \(ledger.dedupe.rawValue) / \(ledger.scope.rawValue) / \(ledger.coverage.rawValue)")
    print("  abandoned branches    \(fmtInt(ledger.abandonedBranchTokens))   (rewound work you still paid for)")
    print("    input               \(fmtInt(ledger.buckets.input))")
    print("    output              \(fmtInt(ledger.buckets.output))")
    print("    cache read          \(fmtInt(ledger.buckets.cacheRead))   (billed, at a reduced rate)")
    print("    cache write 5m      \(fmtInt(ledger.buckets.cacheWrite5m))")
    print("    cache write 1h      \(fmtInt(ledger.buckets.cacheWrite1h))")

    let typed = events.filter { $0.kind == .prompt }.count
    print("")
    print("PROMPTS")
    print("  you typed             \(fmtInt(typed))")

    let live = events.filter { $0.onLivePath == false }.count
    if live > 0 {
        print("")
        print("REWOUND WORK")
        print("  events on abandoned branches  \(fmtInt(live))")
    }

    // Repository identity. Pooling by raw `cwd` fragments a session every time the agent
    // works in a subdirectory, and it splits one repository into one project per worktree
    // — six of thirteen project directories on this machine are worktrees of a single
    // repo. Identity comes from the normalized origin URL via `--git-common-dir`.
    let resolver = RepoResolverCache()
    var repoKeyByCwd: [String: String] = [:]
    for cwd in Set(events.compactMap(\.cwd)) {
        repoKeyByCwd[cwd] = resolver.identity(for: cwd)?.identity ?? "no-repo:\(cwd)"
    }

    print("")
    print("REPOS")
    print("  distinct cwds         \(fmtInt(repoKeyByCwd.count))")
    print("  resolved to repos     \(fmtInt(resolver.repositories.count))")
    for r in resolver.repositories.sorted(by: { ($0.displayName ?? "") < ($1.displayName ?? "") }).prefix(12) {
        let name = (r.displayName ?? "?").padding(toLength: 22, withPad: " ", startingAt: 0)
        print("    \(name)\(r.identity)   [\(r.basis.rawValue)]")
    }

    let repoKeys = repoKeyByCwd
    let pooling = Sessionizer.Pooling.explicit { e in
        "\(e.harness.rawValue)|\(e.cwd.flatMap { repoKeys[$0] } ?? "unknown")"
    }

    print("")
    print("SESSIONS  (pooled by repo, cut on idle gap)")
    for tau in [300.0, 900.0, 1800.0, 3600.0] {
        let ss = Sessionizer.sessions(from: events, options: .init(tau: tau, pooling: pooling))
        // Total active is over ALL sessions: it is invariant to tau by construction, and
        // seeing it hold across this row is the cheapest check that it still is.
        let activeHours = ss.reduce(0.0) { $0 + $1.activeSeconds } / 3600
        let counted = ss.filter(\.counted)
        let notable = ss.filter(\.notable)
        let meanMin = counted.isEmpty ? 0 : counted.reduce(0.0) { $0 + $1.activeSeconds } / Double(counted.count) / 60
        let marker = tau == Tuning.tauSessionSec ? "  <- default" : ""
        print(
            "  tau=\(Int(tau))s".padding(toLength: 12, withPad: " ", startingAt: 0)
                + "\(fmtInt(ss.count)) total".padding(toLength: 13, withPad: " ", startingAt: 0)
                + "\(fmtInt(counted.count)) counted".padding(toLength: 14, withPad: " ", startingAt: 0)
                + "\(fmtInt(notable.count)) notable".padding(toLength: 14, withPad: " ", startingAt: 0)
                + "\(String(format: "%.2f", activeHours))h active".padding(toLength: 17, withPad: " ", startingAt: 0)
                + "mean \(String(format: "%.1f", meanMin))min\(marker)"
        )
    }

    // The longest session, which is the personal record people actually care about.
    let sessions = Sessionizer.sessions(from: events, options: .init(pooling: pooling))
    if let longest = sessions.filter({ $0.notable }).max(by: { $0.activeSeconds < $1.activeSeconds }) {
        let df = DateFormatter()
        df.dateFormat = "EEE d MMM, HH:mm"
        print("")
        print("LONGEST SESSION")
        print("  \(fmtDuration(longest.activeSeconds)) active of \(fmtDuration(longest.wallSeconds)) elapsed")
        print("  \(df.string(from: Date(timeIntervalSince1970: longest.startedAt)))")
        print("  \(longest.poolKey)")
        print("  \(fmtInt(longest.eventCount)) events, \(fmtInt(longest.promptCount)) prompts you typed")
    }

    if !diagnostics.isEmpty {
        print("")
        print("DIAGNOSTICS")
        for (code, n) in diagnostics.sorted(by: { $0.value > $1.value }) {
            print("  \(code.padding(toLength: 24, withPad: " ", startingAt: 0))\(fmtInt(n))")
        }
    }

    print("")
    print("parsed in \(String(format: "%.2f", parseSeconds))s")
}

// MARK: - groundtruth

/// Reproduce the published exploration numbers exactly, against whatever is on disk now.
///
/// This is the strongest correctness signal available: the measurements were taken
/// independently of this code, so agreement is evidence, not a tautology. It pools by
/// PROJECT DIRECTORY rather than by resolved repository, because that is how the
/// reference figures were computed.
func runGroundTruth() throws {
    let project = flagValue("project") ?? "-Users-vedantbhatt-Downloads-projects-RideGT"
    let root = (NSHomeDirectory() as NSString).appendingPathComponent(".claude/projects")
    let dir = (root as NSString).appendingPathComponent(project)

    let parser = ClaudeCodeParser()
    let all = try parser.discover()
    let mine = all.filter { $0.path.hasPrefix(dir + "/") }

    // The reference measurement pooled the project directory's own transcripts.
    let roots = mine.filter { !$0.isSidecar }

    var events: [NormalizedEvent] = []
    for s in roots {
        events.append(contentsOf: try parser.parseAll(source: s).events)
    }

    // One entry per transcript RECORD, matching how the reference figures were computed.
    var seenRecords = Set<String>()
    let timestamped = events.filter { e in
        guard e.ts != nil else { return false }
        let base = e.nativeEventID.map { id -> String in
            guard let h = id.firstIndex(of: "#") else { return id }
            return String(id[id.startIndex..<h])
        } ?? "ord\(e.ordinal)"
        return seenRecords.insert("\(e.sourceID)|\(base)").inserted
    }
    let times = timestamped.compactMap(\.ts).sorted()
    var gaps: [Double] = []
    if times.count > 1 {
        for i in 1..<times.count { gaps.append(times[i] - times[i - 1]) }
    }
    gaps.sort()

    func pct(_ p: Double) -> Double {
        guard !gaps.isEmpty else { return 0 }
        let i = min(gaps.count - 1, max(0, Int(p * Double(gaps.count))))
        return gaps[i]
    }

    print("GROUND TRUTH — project \(project)")
    print("  root transcripts       \(fmtInt(roots.count))          reference: 68")
    print("  substantive events     \(fmtInt(timestamped.count))")
    print("  span                   \(String(format: "%.1f", (times.last ?? 0) - (times.first ?? 0)) )s"
        + "  = \(String(format: "%.1f", ((times.last ?? 0) - (times.first ?? 0)) / 3600))h        reference: 309.5h")
    print("")
    print("  GAP DISTRIBUTION                                   reference")
    print("    p50   \(String(format: "%8.1f", pct(0.50)))s                             1.0")
    print("    p75   \(String(format: "%8.1f", pct(0.75)))s                             5")
    print("    p90   \(String(format: "%8.1f", pct(0.90)))s                            13")
    print("    p95   \(String(format: "%8.1f", pct(0.95)))s                            22")
    print("    p98   \(String(format: "%8.1f", pct(0.98)))s                            63")
    print("    p99   \(String(format: "%8.1f", pct(0.99)))s                           171")
    print("    max   \(String(format: "%8.1f", gaps.last ?? 0))s                        52,386")
    print("    gaps > 60s   \(fmtInt(gaps.filter { $0 > 60 }.count)) of \(fmtInt(gaps.count))"
        + "                     997 of 48,095")

    print("")
    print("  SESSION COUNT BY THRESHOLD                         reference")
    let expected: [Double: Int] = [300: 217, 900: 84, 1800: 52, 3600: 30, 7200: 22]
    // Pool everything in this project directory together, exactly as measured.
    let pooling = Sessionizer.Pooling.explicit { _ in "project" }
    for tau in [300.0, 900.0, 1800.0, 3600.0, 7200.0] {
        let ss = Sessionizer.sessions(from: events, options: .init(tau: tau, pooling: pooling))
        let hours = Sessionizer.sumOfSubThresholdGapsHours(from: events, tau: tau, pooling: pooling)
        let exp = expected[tau] ?? 0
        let ok = ss.count == exp ? "OK " : "DIFF"
        print(
            "    tau=\(Int(tau))s".padding(toLength: 16, withPad: " ", startingAt: 0)
                + "\(fmtInt(ss.count)) sessions".padding(toLength: 16, withPad: " ", startingAt: 0)
                + "\(String(format: "%.2f", hours))h".padding(toLength: 12, withPad: " ", startingAt: 0)
                + "  \(ok)  expected \(exp)"
        )
    }

    print("")
    print("  NOTE: 'hours' above is the sum of sub-threshold gaps, which is how the")
    print("  reference figures were computed. It grows with tau by construction. The")
    print("  product reports capped active time instead, which does not.")
}

// MARK: - dispatch

do {
    switch command {
    case "scan":
        try runScan()
    case "groundtruth":
        try runGroundTruth()
    default:
        print("""
            builder — build-session tracking

            USAGE
              builder scan                 parse everything on disk and report
              builder groundtruth          reproduce the published measurements
                --project <dir>            which ~/.claude/projects directory

            Not yet wired: watch, share, sync, doctor, pair, delete.
            """)
    }
} catch {
    FileHandle.standardError.write(Data("error: \(error)\n".utf8))
    exit(1)
}
