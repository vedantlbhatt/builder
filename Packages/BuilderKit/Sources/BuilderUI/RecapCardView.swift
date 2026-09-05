import BuilderModel
import SwiftUI

/// The share card.
///
/// Design constraints, all of them load-bearing:
///
/// - **What a stranger reads in 0.4 seconds** is the headline and the strip. Everything
///   else is a meta line they will only read if the first two made them stop.
/// - **No cost, ever.** It is structurally impossible for Cursor, wrong by construction
///   for anyone on a subscription, and it would be the fourth thing competing for that
///   half-second. Cost stays in the app, computed locally, never uploaded.
/// - **No legend.** A route map does not explain its own encoding; the moment an artifact
///   does, it is a chart rather than an identity. The legend lives in the app and on the
///   session page instead.
/// - **Flat fills only.** `ImageRenderer` silently drops `Material` and most blend modes,
///   so a card that looks right on screen would export as a grey rectangle.
/// - **System fonts only.** Nothing to license, nothing to bundle, nothing to load.
public struct RecapCardView: View {

    public enum Shape {
        /// 1600x900 — the landscape shape for X.
        case landscape
        /// 1080x1350 — the 4:5 portrait shape.
        case portrait

        var size: CGSize {
            switch self {
            case .landscape: return CGSize(width: 1600, height: 900)
            case .portrait: return CGSize(width: 1080, height: 1350)
            }
        }
    }

    let model: RecapModel
    let shape: Shape
    let dark: Bool
    /// Shown for a new user's first few exports, then never again.
    let showLegend: Bool

    public init(
        model: RecapModel, shape: Shape = .landscape, dark: Bool = true, showLegend: Bool = false
    ) {
        self.model = model
        self.shape = shape
        self.dark = dark
        self.showLegend = showLegend
    }

    private var superlative: Superlative { Superlative.choose(model) }
    private var scale: CGFloat { shape == .landscape ? 1 : 0.85 }

    public var body: some View {
        // Fixed rhythm rather than distributed spacers. Spacers spread the content to the
        // corners and leave a lake of empty space in the middle, which reads as an
        // unfinished slide; the eye should travel headline -> strip -> numbers without
        // crossing a void.
        VStack(alignment: .leading, spacing: 0) {
            header
            Spacer(minLength: 40 * scale)
            headline
            Spacer(minLength: 36 * scale)
            strip
            Spacer(minLength: 40 * scale)
            stats
            if showLegend {
                legend.padding(.top, 26 * scale)
            }
            Spacer(minLength: 0)
            footer
        }
        .padding(.horizontal, shape == .landscape ? 76 : 60)
        .padding(.vertical, shape == .landscape ? 60 : 56)
        .frame(width: shape.size.width, height: shape.size.height)
        .background(StripPalette.card(dark: dark))
    }

    // MARK: - Sections

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 16) {
            Text(model.repoName ?? "private repo")
                .font(.system(size: 30 * scale, weight: .semibold, design: .rounded))
                .foregroundStyle(StripPalette.text(dark: dark))
            Text(dateLine)
                .font(.system(size: 26 * scale, weight: .regular))
                .foregroundStyle(StripPalette.textDim(dark: dark))
            Spacer()
            badge(model.harness.displayName)
            if let m = model.primaryModelName { badge(m) }
        }
    }

    private var headline: some View {
        VStack(alignment: .leading, spacing: 10 * scale) {
            Text(superlative.headline)
                .font(.system(size: 78 * scale, weight: .bold, design: .rounded))
                .foregroundStyle(StripPalette.text(dark: dark))
                .lineLimit(2)
                .minimumScaleFactor(0.5)
                .kerning(-1.5)
            if let sub = superlative.subline {
                Text(sub)
                    .font(.system(size: 30 * scale, weight: .medium))
                    .foregroundStyle(StripPalette.accent(dark: dark))
            }
            // "shipped · velocity machine" — the analysis's two words, dim, under the
            // headline. Absent entirely when no analysis has been stored, so a card
            // without one is byte-identical to the card before analysis existed.
            if let line = model.analysisLine {
                Text(line)
                    .font(.system(size: 30 * scale, weight: .medium))
                    .foregroundStyle(StripPalette.textDim(dark: dark))
                    .lineLimit(1)
            }
        }
    }

    private var strip: some View {
        VStack(alignment: .leading, spacing: 14 * scale) {
            TimelineStripView(
                columns: model.stripColumns,
                marks: model.stripMarks,
                spanMs: max(1, Int(model.wallSeconds * 1000)),
                preset: .hero,
                dark: dark
            )
            .frame(height: 34 * scale)

            HStack {
                Text(timeOfDay(model.startedAt))
                Spacer()
                // Elapsed vs active, stated plainly. This is the honest pair, and it is
                // the one thing on the card a competitor cannot fake.
                Text("\(fmt(model.activeSeconds)) active · \(fmt(model.wallSeconds)) elapsed")
                Spacer()
                Text(timeOfDay(model.startedAt + model.wallSeconds))
            }
            .font(.system(size: 24 * scale, weight: .medium, design: .monospaced))
            .foregroundStyle(StripPalette.textDim(dark: dark))
        }
    }

    private var stats: some View {
        HStack(spacing: 0) {
            stat(fmt(model.activeSeconds), "active")
            if model.commits > 0 { stat("\(model.commits)", "commits") }
            if model.agentLinesAdded > 0 {
                stat("+\(int(model.agentLinesAdded))", "lines")
            }
            if model.filesTouched > 0 { stat("\(model.filesTouched)", "files") }
            stat("\(model.prompts)", model.prompts == 1 ? "prompt" : "prompts")
            // Tokens appear only when the harness actually reports them. Cursor never
            // does, and a "0" there would read as a bug in Builder rather than as a fact
            // about Cursor.
            if model.tokensReported {
                stat(compact(model.totalTokens), "tokens")
            }
            // The five dimension bars sit at the trailing end of the stats row — the
            // lower-right quadrant — at a fixed width, so the stats share what is left
            // and nothing above this row moves. Only when scores exist.
            if !model.dimensionScores.isEmpty {
                dimensionStrip(model.dimensionScores)
                    .fixedSize()
            }
        }
    }

    /// Five vertical 0...100 bars with short labels: steer · exec · eng · product · plan.
    ///
    /// Amber on the token border colour, no numbers. docs/analysis.md: the scores are
    /// one model's reading and are never shown as a bare number — a bar reads as a
    /// shape, which is the honest amount of precision.
    private func dimensionStrip(_ dims: [RecapModel.Dimension]) -> some View {
        let barWidth = 26 * scale
        let barHeight = 56 * scale
        return HStack(alignment: .bottom, spacing: 14 * scale) {
            ForEach(Array(dims.enumerated()), id: \.offset) { item in
                let d = item.element
                VStack(spacing: 6 * scale) {
                    ZStack(alignment: .bottom) {
                        RoundedRectangle(cornerRadius: 3 * scale)
                            .fill(StripPalette.border(dark: dark))
                            .frame(width: barWidth, height: barHeight)
                        RoundedRectangle(cornerRadius: 3 * scale)
                            .fill(StripPalette.accent(dark: dark))
                            .frame(
                                width: barWidth,
                                height: max(3 * scale, barHeight * CGFloat(d.score) / 100))
                    }
                    Text(d.label)
                        .font(.system(size: 16 * scale, weight: .medium))
                        .foregroundStyle(StripPalette.textDim(dark: dark))
                        .lineLimit(1)
                }
            }
        }
    }

    private var footer: some View {
        HStack {
            HStack(spacing: 10) {
                RoundedRectangle(cornerRadius: 3)
                    .fill(StripPalette.accent(dark: dark))
                    .frame(width: 16, height: 16)
                Text("builder")
                    .font(.system(size: 26 * scale, weight: .semibold, design: .rounded))
                    .foregroundStyle(StripPalette.text(dark: dark))
            }
            Spacer()
            Text(shortCode)
                .font(.system(size: 22 * scale, weight: .regular, design: .monospaced))
                .foregroundStyle(StripPalette.textDim(dark: dark))
        }
    }

    private var legend: some View {
        HStack(spacing: 28 * scale) {
            legendItem(.prompting, "you prompting")
            legendItem(.agent, "agent working")
            legendItem(.human_edit, "your edits")
            legendItem(.idle, "idle")
        }
    }

    // MARK: - Pieces

    private func stat(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 4 * scale) {
            Text(value)
                .font(.system(size: 40 * scale, weight: .semibold, design: .rounded))
                .foregroundStyle(StripPalette.text(dark: dark))
            Text(label)
                .font(.system(size: 22 * scale, weight: .regular))
                .foregroundStyle(StripPalette.textDim(dark: dark))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func badge(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 22 * scale, weight: .medium))
            .foregroundStyle(StripPalette.textDim(dark: dark))
            .padding(.horizontal, 16 * scale)
            .padding(.vertical, 8 * scale)
            .background(
                Capsule().stroke(StripPalette.border(dark: dark), lineWidth: 1.5))
    }

    private func legendItem(_ k: StripClass, _ label: String) -> some View {
        HStack(spacing: 8) {
            RoundedRectangle(cornerRadius: 2)
                .fill(StripPalette.stripClass(k, dark: dark))
                .frame(width: 18, height: 12)
            Text(label)
                .font(.system(size: 20 * scale))
                .foregroundStyle(StripPalette.textDim(dark: dark))
        }
    }

    // MARK: - Formatting

    private var shortCode: String {
        "builder.dev/s/" + String(model.clientSessionID.prefix(6))
    }

    private var dateLine: String {
        let df = DateFormatter()
        df.dateFormat = "EEEE d MMMM"
        return df.string(from: Date(timeIntervalSince1970: model.startedAt))
    }

    private func timeOfDay(_ ts: Double) -> String {
        let df = DateFormatter()
        df.dateFormat = "HH:mm"
        return df.string(from: Date(timeIntervalSince1970: ts))
    }

    private func fmt(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        let h = s / 3600
        let m = (s % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }

    private func int(_ n: Int) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: n)) ?? "\(n)"
    }

    /// 5.4M rather than 5,412,883. A card is read at a glance.
    private func compact(_ n: Int) -> String {
        switch n {
        case 1_000_000...: return String(format: "%.1fM", Double(n) / 1_000_000)
        case 1_000...: return String(format: "%.0fk", Double(n) / 1_000)
        default: return "\(n)"
        }
    }
}
