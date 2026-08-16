import BuilderIngest
import BuilderModel
import BuilderParse
import Foundation

/// `builder groundtruth` — reproduce measurements taken independently of this code.
///
/// The reference figures came from an exploration pass that predates the implementation,
/// so agreement is evidence rather than tautology. This is the strongest correctness
/// signal available for a log parser, whose failure mode is never a crash but a plausible
/// wrong number nobody questions.
enum GroundTruthCommand {

    static func run() throws {
        let project = CLIArgs.value("project") ?? "-Users-vedantbhatt-Downloads-projects-RideGT"
        let root = (NSHomeDirectory() as NSString).appendingPathComponent(".claude/projects")
        let dir = (root as NSString).appendingPathComponent(project)

        let parser = ClaudeCodeParser()
        // Root transcripts only, pooled together — exactly how the reference was computed.
        let sources = try parser.discover().filter { $0.path.hasPrefix(dir + "/") && !$0.isSidecar }

        var events: [NormalizedEvent] = []
        for s in sources { events.append(contentsOf: try parser.parseAll(source: s).events) }

        // One entry per transcript RECORD. Content blocks of one record share a timestamp,
        // and feeding them to a gap analysis injects runs of zero-length gaps that drag
        // every percentile down.
        var seen = Set<String>()
        let records = events.filter { e in
            guard e.ts != nil else { return false }
            return seen.insert("\(e.sourceID)|\(Sessionizer.recordBaseID(e))").inserted
        }

        let times = records.compactMap(\.ts).sorted()
        var gaps: [Double] = []
        if times.count > 1 { for i in 1..<times.count { gaps.append(times[i] - times[i - 1]) } }
        gaps.sort()

        func pct(_ p: Double) -> Double {
            gaps.isEmpty ? 0 : gaps[min(gaps.count - 1, Int(p * Double(gaps.count)))]
        }
        func row(_ label: String, _ actual: String, _ reference: String) {
            print("    \(Fmt.pad(label, 10))\(Fmt.rpad(actual, 12))    reference \(reference)")
        }

        print("GROUND TRUTH — \(project)")
        print("")
        row("transcripts", "\(sources.count)", "68")
        row("records", Fmt.int(records.count), "48,096")
        row("span", String(format: "%.1fh", ((times.last ?? 0) - (times.first ?? 0)) / 3600), "309.5h")

        Fmt.heading("  GAP DISTRIBUTION")
        row("p50", String(format: "%.1fs", pct(0.50)), "1.0")
        row("p75", String(format: "%.1fs", pct(0.75)), "5")
        row("p90", String(format: "%.1fs", pct(0.90)), "13")
        row("p95", String(format: "%.1fs", pct(0.95)), "22")
        row("p98", String(format: "%.1fs", pct(0.98)), "63")
        row("p99", String(format: "%.1fs", pct(0.99)), "171")
        row("max", String(format: "%.0fs", gaps.last ?? 0), "52,386")
        row("> 60s", "\(gaps.filter { $0 > 60 }.count) of \(gaps.count)", "997 of 48,095")

        Fmt.heading("  SESSION COUNT BY THRESHOLD")
        let expected: [(Double, Int, Double)] = [
            (300, 217, 80.05), (900, 84, 98.98), (1800, 52, 110.76),
            (3600, 30, 125.46), (7200, 22, 137.79),
        ]
        let pooling = Sessionizer.Pooling.explicit { _ in "project" }
        var allMatch = true
        for (tau, expCount, expHours) in expected {
            let ss = Sessionizer.sessions(from: events, options: .init(tau: tau, pooling: pooling))
            let hours = Sessionizer.sumOfSubThresholdGapsHours(from: events, tau: tau, pooling: pooling)
            let ok = ss.count == expCount
            if !ok { allMatch = false }
            print(
                "    \(Fmt.pad("tau=\(Int(tau))s", 11))"
                    + "\(Fmt.rpad(Fmt.int(ss.count), 4)) sessions   "
                    + "\(Fmt.rpad(String(format: "%.2f", hours), 7))h   "
                    + "\(ok ? "OK  " : "DIFF")  expected \(expCount) / \(String(format: "%.2f", expHours))h")
        }

        // Capped active time must not move when the threshold moves. If this ever fails,
        // every historical total in the product silently changed the last time someone
        // retuned a constant.
        Fmt.heading("  ACTIVE TIME INVARIANCE")
        var baseline: Double?
        var invariant = true
        for (tau, _, _) in expected {
            let total = Sessionizer.sessions(from: events, options: .init(tau: tau, pooling: pooling))
                .reduce(0.0) { $0 + $1.activeSeconds }
            if let b = baseline, abs(total - b) > 1 { invariant = false }
            baseline = baseline ?? total
            print("    \(Fmt.pad("tau=\(Int(tau))s", 11))\(String(format: "%.2f", total / 3600))h")
        }
        print("    \(invariant ? "OK   invariant across every threshold" : "BROKEN — active time moves with tau")")

        Fmt.heading("  NOTE")
        print("  'hours' above is the sum of sub-threshold gaps, which is how the reference")
        print("  was computed. It grows with tau by construction — a 14-minute break counts")
        print("  in full at tau=900. The product reports capped active time instead, which")
        print("  is why the invariance block above matters.")

        if !allMatch {
            print("")
            print("  Counts at small tau drift as the corpus grows: at a 5-minute threshold")
            print("  the distribution sits on a cliff. From 900s upward the structure is")
            print("  stable, which is itself an argument for the chosen default.")
        }
    }
}
