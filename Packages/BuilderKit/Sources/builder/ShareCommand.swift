import BuilderIngest
import BuilderModel
import BuilderSchema
import BuilderSQLite
import BuilderStore
import BuilderUI
import Foundation

/// `builder share` — render a session to a PNG and put it on the pasteboard.
///
/// Deliberately a one-liner with no UI. The target audience already screenshots `ccusage`
/// from a terminal, so a command that produces a better image in the same gesture is the
/// shortest path from "I did a thing" to "I posted a thing".
enum ShareCommand {

    static func run() throws {
        let state = try SchemaManager.openState()
        let (cache, _) = try SchemaManager.openCache(tuningVersion: Tuning.version)

        let wantsPortrait = CLIArgs.flag("portrait")
        let light = CLIArgs.flag("light")
        let legend = CLIArgs.flag("legend")
        let explicitID = CLIArgs.value("session")

        guard let model = try RecapLoader.model(state: state, cache: cache, sessionID: explicitID) else {
            print("No shareable session found. Run `builder scan`, or pass --session <id>.")
            return
        }

        let outPath =
            CLIArgs.value("out")
            ?? (NSHomeDirectory() as NSString).appendingPathComponent(
                "Desktop/builder-\(String(model.clientSessionID.prefix(6))).png")

        let result = try MainActor.assumeIsolated {
            let r = try ImageExport.writeCard(
                model, to: outPath,
                shape: wantsPortrait ? .portrait : .landscape,
                dark: !light, showLegend: legend)
            if let data = FileManager.default.contents(atPath: outPath) {
                ImageExport.copyToPasteboard(data)
            }
            return r
        }

        let superlative = Superlative.choose(model)
        print("")
        print("  \(superlative.headline)")
        if let sub = superlative.subline { print("  \(sub)") }
        print("")
        print("  \(model.repoName ?? "private repo")  ·  \(Fmt.date(model.startedAt))")
        print("  \(Fmt.duration(model.activeSeconds)) active of \(Fmt.duration(model.wallSeconds)) elapsed")
        print("")
        print("  \(AnsiStrip.render(cols: model.stripColumns, width: 64, marks: model.stripMarks, spanMs: Int(model.wallSeconds * 1000)))")
        print("")
        print("  written  \(result.path)")
        print("  size     \(result.width)x\(result.height)  (\(result.bytes / 1024) KB)")
        print("  copied   image is on the pasteboard")
        print("")
        print("  caption  \(ImageExport.caption(for: model))")
    }
}
