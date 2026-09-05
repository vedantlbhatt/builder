import BuilderAnalysis
import BuilderIngest
import BuilderModel
import BuilderSchema
import BuilderSQLite
import Foundation

/// `builder analyze` — digest one session and have your own Claude Code read it.
///
///   builder analyze <client_session_id | --last | --all-missing> [--print-digest] [--dry-run]
///
/// `--print-digest` prints the digest and stops: no model call, nothing stored. It is the
/// Swift twin of `python -m analysis digest`, and the two must agree byte for byte.
/// `--dry-run` builds everything and prints the `claude -p` command it would run. Without
/// either it runs the analysis, stores the row in `state.session_analysis`, and prints
/// the headline, outcome, confidence and cost. The next `builder sync` attaches it.
enum AnalyzeCommand {

    struct UsageError: Error, CustomStringConvertible {
        let message: String
        var description: String { message }
    }

    static func run() throws {
        let printDigest = CLIArgs.flag("print-digest")
        let dryRun = CLIArgs.flag("dry-run")
        let last = CLIArgs.flag("last")
        let allMissing = CLIArgs.flag("all-missing")
        let idArg = CLIArgs.all.dropFirst().first { !$0.hasPrefix("--") }

        guard idArg != nil || last || allMissing else {
            throw UsageError(
                message: "usage: builder analyze <client_session_id | --last | --all-missing> [--print-digest] [--dry-run]")
        }

        let state = try SchemaManager.openState()
        // The same derivation `builder scan` runs; it is what the daemon holds in memory.
        let sessions = try SessionDeriver.run(db: state, verbose: false)
        guard !sessions.isEmpty else {
            print("No sessions yet. Run `builder scan` first.")
            return
        }
        let repoNames = try IngestCoordinator.repoNames(db: state)
        let lifecycle = SessionLifecycle(db: state)
        let index = try AnalysisStore.index(in: state)
        let now = Date().timeIntervalSince1970
        let model = AnalysisSettings.model()

        // Live means a checkpoint analysis; the lifecycle decides, and a session it has
        // never seen is judged by the clock the way `tick` would.
        func isLive(_ s: DetectedSession) throws -> Bool {
            switch try lifecycle.state(of: s.clientSessionID) {
            case .open?, .idle?: return true
            case nil: return s.endReason == .stillRunning && now - s.endedAt < Tuning.tauSessionSec
            default: return false
            }
        }

        var targets: [DetectedSession] = []
        if let idArg {
            let matches = sessions.filter {
                $0.clientSessionID == idArg || $0.clientSessionID.hasPrefix(idArg)
            }
            guard !matches.isEmpty else { throw UsageError(message: "no session matches \(idArg)") }
            guard matches.count == 1 else {
                throw UsageError(message: "\(matches.count) sessions match \(idArg); give more of the id")
            }
            targets = matches
        } else if last {
            if let s = sessions.max(by: { $0.startedAt < $1.startedAt }) { targets = [s] }
        } else {
            for s in sessions where AnalysisJob.isWorthAnalysing(s) {
                if try isLive(s) { continue }
                if let row = index[s.clientSessionID], !row.checkpoint { continue }
                targets.append(s)
            }
            targets.sort { $0.startedAt < $1.startedAt }
            if targets.isEmpty {
                print("Nothing missing: every finished session worth analysing has an analysis.")
                return
            }
            if !printDigest && !dryRun {
                print("\(targets.count) session(s) without a final analysis; model \(model).")
            }
        }

        for s in targets {
            let short = String(s.clientSessionID.prefix(8))
            let checkpoint = try isLive(s)
            let job = try AnalysisJob.make(for: s, checkpoint: checkpoint, state: state, repoNames: repoNames)

            if job.transcripts.isEmpty {
                print("  \(short)  no transcript on disk for this \(s.harness.displayName) session; skipped")
                continue
            }

            if printDigest {
                let d = try job.digest()
                print(d.text, terminator: "")
                FileHandle.standardError.write(
                    Data(
                        "\n[events \(d.events)  coverage \(d.coverage)  chars \(d.text.unicodeScalars.count)  hash \(d.hash.prefix(12))]\n"
                            .utf8))
                continue
            }

            if dryRun {
                let d = try job.digest()
                let inv = try Analyzer.invocation(digest: d.text, coverage: d.coverage, model: model)
                print("  \(short)  \(checkpoint ? "checkpoint" : "final")  \(d.events) events, "
                        + "\(d.text.unicodeScalars.count) chars, coverage \(d.coverage), "
                        + "\(job.transcripts.count) transcript(s)")
                print("  " + inv.display)
                print("  stdin /dev/null; timeout \(Int(Analyzer.timeoutSeconds))s; "
                        + "env without \(Analyzer.scrubbedEnvironment.joined(separator: ", "))")
                print("  Nothing was run.")
                continue
            }

            if !AnalysisJob.isWorthAnalysing(s) {
                print("  \(short)  \(Fmt.duration(s.activeSeconds)) active, \(s.meaningfulEventCount) meaningful events — "
                        + "below the floor the daemon uses; analysing anyway because you asked.")
            }
            print("  \(short)  analysing with \(model)…")
            let started = Date()
            let (record, result, digest) = try job.perform(in: state, model: model)
            let a = result.analysis
            print("")
            print("  \(a.headline)")
            print("  outcome     \(a.outcome.rawValue)")
            print("  confidence  \(String(format: "%.2f", a.confidence))")
            print("  cost        \(record.costUSD.map { String(format: "$%.2f", $0) } ?? "n/a")"
                    + "  (\(a.model), \(Int(Date().timeIntervalSince(started)))s)")
            print("  digest      \(digest.events) events, \(digest.text.unicodeScalars.count) chars, "
                    + "coverage \(digest.coverage)\(result.droppedExcerpts > 0 ? ", \(result.droppedExcerpts) excerpt(s) dropped" : "")")
            print("  stored as   \(checkpoint ? "checkpoint" : "final") for \(short)")
            print("")
        }
    }
}
