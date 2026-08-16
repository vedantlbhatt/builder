import BuilderGit
import BuilderIngest
import BuilderModel
import BuilderParse
import BuilderSchema
import BuilderSQLite
import Foundation

/// `builder scan` — parse everything new on disk into the durable store.
///
/// This is the operation the menu bar app runs on launch and on every file-system event.
/// Running it twice in a row must do almost nothing the second time; if it does not, the
/// watermarking is broken and the steady-state daemon would re-read 1.2 GB every tick.
enum ScanCommand {

    static func run() throws {
        let force = CLIArgs.flag("rebuild")
        let db = try SchemaManager.openState()

        if force {
            print("--rebuild: clearing the derived index and re-reading every source.")
            print("(state.sqlite keeps its events — they may be the only copy left.)")
            try db.exec("DELETE FROM ingest_watermark")
        }

        let before = try db.scalarInt("SELECT COUNT(*) FROM raw_event") ?? 0

        let coordinator = IngestCoordinator(db: db)
        var lastPrinted = -1

        let result = try coordinator.run { p in
            let pct = Int(p.fraction * 100)
            guard pct != lastPrinted else { return }
            lastPrinted = pct
            let name = p.currentPath.map { ($0 as NSString).lastPathComponent } ?? "done"
            let line = "  \(Fmt.bar(p.fraction)) \(Fmt.rpad("\(pct)", 3))%  "
                + "\(Fmt.rpad(Fmt.int(p.sourcesDone), 4))/\(p.sourcesTotal) sources  "
                + String(name.prefix(28))
            print("\u{1B}[2K\r" + line, terminator: "")
            fflush(stdout)
        }
        print("\u{1B}[2K\r", terminator: "")

        let after = try db.scalarInt("SELECT COUNT(*) FROM raw_event") ?? 0

        Fmt.heading("INGEST")
        print("  sources parsed        \(Fmt.int(result.sourcesScanned))")
        print("  sources unchanged     \(Fmt.int(result.sourcesSkipped))   (watermark already current)")
        print("  events written        \(Fmt.int(result.eventsWritten))")
        print("  events in store       \(Fmt.int(after))   (+\(Fmt.int(after - before)))")
        print("  repos resolved        \(Fmt.int(result.reposResolved))")
        print("  elapsed               \(String(format: "%.2f", result.elapsed))s")

        if !result.diagnostics.isEmpty {
            Fmt.heading("DIAGNOSTICS")
            for (code, n) in result.diagnostics.sorted(by: { $0.value > $1.value }) {
                print("  \(Fmt.pad(code, 26))\(Fmt.int(n))")
            }
        }

        try SessionDeriver.run(db: db, verbose: true)
    }
}
