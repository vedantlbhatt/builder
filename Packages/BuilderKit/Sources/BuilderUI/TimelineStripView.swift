import BuilderModel
import SwiftUI

/// The session timeline strip. One renderer, three presets.
///
/// This is the product's visual identity — the thing that should make a screenshot
/// recognisable in a timeline the way an orange route map is recognisable. It is drawn in
/// a single `Canvas` rather than as a stack of shapes, because at 1024 columns a view
/// hierarchy would be a thousand nodes for a bar forty points tall.
///
/// The marks are drawn ON TOP of the columns and are never resampled away. A typed prompt
/// occupies seconds of a session that may run for hours, so at any render width its column
/// is dominated by the agent run that follows it; without the overlay the human disappears
/// from their own timeline.
public struct TimelineStripView: View {

    public enum Preset {
        /// Widget / dense list. No marks, no ruler, density flattened to two levels.
        case sparkline
        /// Feed row.
        case row
        /// Share card and session detail.
        case hero

        var trackHeight: CGFloat {
            switch self {
            case .sparkline: return 8
            case .row: return 12
            case .hero: return 28
            }
        }

        var showsMarks: Bool {
            switch self {
            case .sparkline: return false
            case .row, .hero: return true
            }
        }

        var cornerRadius: CGFloat {
            switch self {
            case .sparkline: return 2
            case .row: return 3
            case .hero: return 6
            }
        }
    }

    let columns: [UInt8]
    let marks: [(ms: Int, kind: StripMarkKind)]
    let spanMs: Int
    let preset: Preset
    let dark: Bool

    public init(
        columns: [UInt8],
        marks: [(ms: Int, kind: StripMarkKind)] = [],
        spanMs: Int = 0,
        preset: Preset = .row,
        dark: Bool = true
    ) {
        self.columns = columns
        self.marks = marks
        self.spanMs = spanMs
        self.preset = preset
        self.dark = dark
    }

    public var body: some View {
        Canvas { context, size in
            guard !columns.isEmpty, size.width > 0 else { return }

            // Resample to whole pixels. Nearest-neighbour on column centres, exactly as
            // the spec requires and exactly as the TypeScript renderer does — a box filter
            // would average categorical ordinals, which is meaningless.
            let width = max(1, Int(size.width.rounded()))
            let resampled = StripSpec.resample(columns, to: width)
            let colWidth = size.width / CGFloat(width)
            let h = preset.trackHeight

            for (i, byte) in resampled.enumerated() {
                let (klass, density) = StripSpec.unpack(byte)
                var color = StripPalette.stripClass(klass, dark: dark)

                // Density is the second channel: it is what makes a busy stretch read as a
                // barcode rather than a solid block, and it is why a six-hour session and a
                // twenty-minute one both have visible structure.
                if klass != .idle {
                    let alphas = StripSpec.densityAlphas
                    let idx =
                        preset == .sparkline
                        ? (density >= 2 ? alphas.count - 1 : 0)
                        : Int(density)
                    color = color.opacity(alphas[max(0, min(alphas.count - 1, idx))])
                }

                let rect = CGRect(
                    x: CGFloat(i) * colWidth, y: 0,
                    width: colWidth.rounded(.up), height: h)
                context.fill(Path(rect), with: .color(color))
            }

            guard preset.showsMarks, spanMs > 0 else { return }

            // Marks overlay the track, full height, at full contrast.
            var drawn: [CGFloat] = []
            let minGap = CGFloat(StripSpec.markDedupeMinPx)
            for m in marks.sorted(by: { $0.ms < $1.ms }) {
                let x = size.width * CGFloat(m.ms) / CGFloat(spanMs)
                if let last = drawn.last, x - last < minGap { continue }
                drawn.append(x)
                let markWidth: CGFloat = preset == .hero ? 2.5 : 1.5
                let rect = CGRect(
                    x: max(0, min(size.width - markWidth, x - markWidth / 2)),
                    y: -1, width: markWidth, height: h + 2)
                context.fill(Path(rect), with: .color(StripPalette.mark(m.kind, dark: dark)))
            }
        }
        .frame(height: preset.trackHeight)
        .clipShape(RoundedRectangle(cornerRadius: preset.cornerRadius, style: .continuous))
        .accessibilityLabel(accessibilityDescription)
    }

    /// VoiceOver gets the shape as a sentence. A decorative bar that reads as "image" is
    /// useless, and this is the primary content of a session row.
    private var accessibilityDescription: String {
        var counts: [StripClass: Int] = [:]
        for b in columns { counts[StripSpec.unpack(b).klass, default: 0] += 1 }
        let total = max(columns.count, 1)
        func pct(_ k: StripClass) -> Int { (counts[k] ?? 0) * 100 / total }
        return
            "Session timeline: \(pct(.agent)) percent agent working, "
            + "\(pct(.prompting)) percent prompting, \(pct(.human_edit)) percent your edits, "
            + "\(pct(.idle)) percent idle. \(marks.count) prompts."
    }
}
