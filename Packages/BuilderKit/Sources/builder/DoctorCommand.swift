import BuilderAnalysis
import BuilderModel
import BuilderSchema
import BuilderSQLite
import Foundation

/// `builder doctor` — everything the profile screen will show, plus everything that could
/// be quietly wrong.
///
/// Diagnostics are first-class here rather than buried in a log, because the entire
/// correctness story of this product is "the numbers are right", and a parser that
/// degrades silently is indistinguishable from one that works.
enum DoctorCommand {

    static func run() throws {
        let state = try SchemaManager.openState()
        let (cache, _) = try SchemaManager.openCache(tuningVersion: Tuning.version)

        let events = try state.scalarInt("SELECT COUNT(*) FROM raw_event") ?? 0
        if events == 0 {
            print("Nothing ingested yet. Run `builder scan`.")
            return
        }

        print("STORE")
        print("  events                \(Fmt.int(events))")
        print("  sources               \(Fmt.int(try state.scalarInt("SELECT COUNT(*) FROM ingest_watermark") ?? 0))")
        print("  repos                 \(Fmt.int(try state.scalarInt("SELECT COUNT(*) FROM repo") ?? 0))")
        print("  sessions              \(Fmt.int(try cache.scalarInt("SELECT COUNT(*) FROM session") ?? 0))")
        print("  tuning                \(Tuning.version)")

        // Retention is the whole reason this store exists.
        if let oldest = try state.scalarDouble("SELECT MIN(ts) FROM raw_event WHERE ts IS NOT NULL"),
           let newest = try state.scalarDouble("SELECT MAX(ts) FROM raw_event WHERE ts IS NOT NULL") {
            let days = (newest - oldest) / 86400
            print("  history               \(String(format: "%.1f", days)) days"
                + "  (\(Fmt.date(oldest, "d MMM")) → \(Fmt.date(newest, "d MMM")))")
            if days > 28 {
                print("                        Claude Code prunes at ~30 days. Builder is now the")
                print("                        only copy of the oldest of this.")
            }
        }

        let analysis = Analysis(cache: cache, state: state)

        Fmt.heading("RECORDS")
        for r in try analysis.records() {
            print("  \(Fmt.pad(r.label, 22))\(Fmt.pad(r.formatted, 18))\(r.context)")
        }

        Fmt.heading("CONTRIBUTION  (active hours per day)")
        let graph = try analysis.contributionGraph(days: 70)
        print(analysis.renderGraph(graph))
        print("  \(AnsiStrip.legend())")

        Fmt.heading("PROJECTS")
        for arc in try analysis.projectArcs().prefix(12) {
            print(
                "  \(Fmt.pad(arc.name, 24))"
                    + "\(Fmt.rpad(Fmt.duration(arc.activeSeconds), 9))  "
                    + "\(Fmt.rpad(Fmt.int(arc.sessions), 4)) sessions  "
                    + "\(Fmt.date(arc.firstSession, "d MMM")) → \(Fmt.date(arc.lastSession, "d MMM"))")
        }

        Fmt.heading("HUMAN vs AGENT")
        let attribution = try analysis.attributionSummary()
        print("  agent lines (live path)   \(Fmt.int(attribution.agentLines))")
        print("  your edits outside agent  \(Fmt.int(attribution.humanEditEvents)) events")
        print("  prompts you typed         \(Fmt.int(attribution.prompts))")
        print("")
        print("  These are three separately measured numbers and they are deliberately not")
        print("  combined into a percentage. `edited_text_file` records existence with no")
        print("  line count, and most sessions have no commit in their window, so the")
        print("  obvious subtraction reads 0% human regardless of how much you typed.")

        Fmt.heading("MODELS")
        for (model, share) in try analysis.modelShare().prefix(8) {
            print("  \(Fmt.pad(model, 26))\(Fmt.rpad(String(format: "%.1f", share * 100), 5))% of output tokens")
        }

        let diags = try state.scalarInt("SELECT COUNT(*) FROM diagnostics") ?? 0
        Fmt.heading("DIAGNOSTICS  (\(Fmt.int(diags)) total)")
        if diags == 0 {
            print("  none")
        } else {
            try state.query(
                "SELECT code, COUNT(*), MAX(detail) FROM diagnostics GROUP BY code ORDER BY 2 DESC LIMIT 12"
            ) { s in
                print("  \(Fmt.pad(s.text(0) ?? "?", 26))\(Fmt.rpad(Fmt.int(s.int(1) ?? 0), 6))  "
                    + String((s.text(2) ?? "").prefix(60)))
            }
        }
    }
}
