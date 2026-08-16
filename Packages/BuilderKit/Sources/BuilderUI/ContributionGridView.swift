import BuilderAnalysis
import BuilderModel
import SwiftUI

/// The contribution graph, coloured by ACTIVE HOURS rather than by tokens.
///
/// Hours are the honest metric: every harness has them, including the ones that never
/// write a token count. Tokens are the flex metric — shown elsewhere, never ranked by, and
/// deliberately not the colour channel here, because a graph coloured by tokens would show
/// a Cursor user an empty year.
///
/// The bucket edges are ABSOLUTE (`Tuning.graphHourBuckets`) rather than per-user
/// quantiles. Self-relative shading would make two people's graphs incomparable, which
/// defeats the point of a graph anyone screenshots.
///
/// Drawn in a single `Canvas`. A 17-week grid is 119 cells and a full year is 371; as a
/// view hierarchy that is hundreds of nodes redrawn every time the live timer ticks.
public struct ContributionGridView: View {

    let days: [Analysis.GraphDay]
    let dark: Bool
    let cell: CGFloat
    let gap: CGFloat

    public init(days: [Analysis.GraphDay], dark: Bool = true, cell: CGFloat = 11, gap: CGFloat = 3) {
        self.days = days
        self.dark = dark
        self.cell = cell
        self.gap = gap
    }

    private var weeks: Int { max(1, Int(ceil(Double(days.count) / 7.0))) }

    public var body: some View {
        Canvas { context, _ in
            // Column = week, row = weekday, exactly like the graph everyone already knows.
            // The first column is padded so weekdays line up: a grid whose rows do not
            // mean the same day is unreadable, and this is the single most-copied
            // visualisation in developer tooling, so breaking the convention costs
            // recognition for nothing.
            guard let first = days.first else { return }
            let leading = weekdayIndex(first.day)

            for (i, day) in days.enumerated() {
                let slot = i + leading
                let week = slot / 7
                let weekday = slot % 7
                let rect = CGRect(
                    x: CGFloat(week) * (cell + gap),
                    y: CGFloat(weekday) * (cell + gap),
                    width: cell, height: cell)
                context.fill(
                    Path(roundedRect: rect, cornerRadius: 2.5),
                    with: .color(StripPalette.graphLevel(day.level, dark: dark)))
            }
        }
        .frame(
            width: CGFloat(weeks + 1) * (cell + gap),
            height: 7 * (cell + gap))
        .accessibilityLabel(summary)
    }

    /// Monday-first, matching the row order the labels imply.
    private func weekdayIndex(_ day: String) -> Int {
        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd"
        guard let date = df.date(from: day) else { return 0 }
        return (Calendar.current.component(.weekday, from: date) + 5) % 7
    }

    private var summary: String {
        let active = days.filter { $0.activeSeconds > 0 }.count
        let total = days.reduce(0.0) { $0 + $1.activeSeconds } / 3600
        return String(
            format: "Contribution graph: %.1f active hours across %d of %d days",
            total, active, days.count)
    }
}
