import Foundation

// The agent and the CLI are the same binary. Everything the menu bar app does, this does
// first — which means the entire engine is exercisable with `swift run`, before any Xcode
// project, signing identity or provisioning profile exists.

/// Async commands need a run loop the CLI does not otherwise have.
func runAsync(_ body: @escaping @Sendable () async throws -> Void) {
    let semaphore = DispatchSemaphore(value: 0)
    var failure: Error?
    Task {
        do { try await body() } catch { failure = error }
        semaphore.signal()
    }
    semaphore.wait()
    if let failure {
        FileHandle.standardError.write(Data("error: \(failure)\n".utf8))
        exit(1)
    }
}

do {
    switch CLIArgs.command {
    case "sync":
        runAsync { try await SyncCommand.sync() }
    case "pair":
        runAsync { try await PairCommand.run() }
    case "scan":
        try ScanCommand.run()
    case "watch":
        try WatchCommand.run()
    case "sessions":
        try SessionsCommand.run()
    case "analyze":
        try AnalyzeCommand.run()
    case "share":
        try ShareCommand.run()
    case "preview":
        try PreviewCommand.run()
    case "doctor":
        try DoctorCommand.run()
    case "groundtruth":
        try GroundTruthCommand.run()
    default:
        print(
            """
            builder — build-session tracking

            USAGE
              builder scan [--rebuild]      parse new transcripts into the store, then derive
              builder watch [--quiet]       run the completion loop: watch, sessionize, notify
              builder sessions [--limit N] [--all]
                                           recent sessions, with their timeline strips
              builder share [--portrait] [--light] [--legend] [--out PATH] [--session ID]
                                            render a session to PNG and copy it
              builder pair                  link this Mac to your account
              builder sync [--dry-run] [--print-payload]
                                            upload sessions; dry-run prints and sends nothing.
                                            Open sessions go up as live snapshots. The stored
                                            analysis is attached unless BuilderAnalysisUpload
                                            (defaults) is false or BUILDER_ANALYSIS_UPLOAD=0
              builder analyze <id | --last | --all-missing> [--print-digest] [--dry-run]
                                            digest a session and have your own Claude Code
                                            read it (claude -p). --print-digest never calls
                                            the model. BUILDER_ANALYSIS_MODEL / defaults
                                            BuilderAnalysisModel pick the model (sonnet)
              builder preview [--out DIR]   render the app surfaces to PNG from real data
              builder doctor                records, contribution graph, projects, diagnostics
              builder groundtruth           reproduce the published measurements
                --project <dir>             which ~/.claude/projects directory

            STORE
              state.sqlite   durable, append-only. Often the only copy left: Claude Code
                             prunes at ~30 days and Cursor drops message bodies at ~60.
              cache.sqlite   derived, disposable, rebuilt whenever Tuning changes.
            """)
    }
} catch {
    FileHandle.standardError.write(Data("error: \(error)\n".utf8))
    exit(1)
}
