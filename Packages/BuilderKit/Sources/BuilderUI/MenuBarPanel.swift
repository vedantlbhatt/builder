import BuilderAnalysis
import BuilderModel
import SwiftUI

/// The menu bar dropdown, as a pure view over plain data.
///
/// Lives in the shared package rather than in the app target so it can be rendered
/// offscreen — for previews, for screenshots, and (more usefully) so the layout can be
/// exercised without launching a menu bar item, which is awkward to test and, on a
/// crowded menu bar, may not even be visible.
///
/// Ordering is the whole design: **today, then what is running now, then recent work,
/// then the graph.** A menu bar item is opened to answer "how am I doing right now", and
/// anything that pushes that below the fold has failed.
public struct MenuBarPanel: View {

    public struct SessionRow: Identifiable, Sendable {
        public let id: String
        public let repo: String
        public let headline: String
        public let activeSeconds: Double
        public let wallSeconds: Double
        public let startedAt: Double
        public let prompts: Int
        public let commits: Int
        public let strip: [UInt8]
        public let marks: [(ms: Int, kind: StripMarkKind)]

        public init(
            id: String, repo: String, headline: String, activeSeconds: Double,
            wallSeconds: Double, startedAt: Double, prompts: Int, commits: Int,
            strip: [UInt8], marks: [(ms: Int, kind: StripMarkKind)]
        ) {
            self.id = id
            self.repo = repo
            self.headline = headline
            self.activeSeconds = activeSeconds
            self.wallSeconds = wallSeconds
            self.startedAt = startedAt
            self.prompts = prompts
            self.commits = commits
            self.strip = strip
            self.marks = marks
        }
    }

    public struct Model: Sendable {
        public let todayActiveSeconds: Double
        public let streakDays: Int
        public let totalSessions: Int
        public let allTimeSeconds: Double
        public let live: SessionRow?
        public let recent: [SessionRow]
        public let graph: [Analysis.GraphDay]

        public init(
            todayActiveSeconds: Double, streakDays: Int, totalSessions: Int,
            allTimeSeconds: Double, live: SessionRow?, recent: [SessionRow],
            graph: [Analysis.GraphDay]
        ) {
            self.todayActiveSeconds = todayActiveSeconds
            self.streakDays = streakDays
            self.totalSessions = totalSessions
            self.allTimeSeconds = allTimeSeconds
            self.live = live
            self.recent = recent
            self.graph = graph
        }
    }

    let model: Model
    let dark: Bool

    public init(model: Model, dark: Bool = true) {
        self.model = model
        self.dark = dark
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider().overlay(StripPalette.border(dark: dark))

            VStack(alignment: .leading, spacing: 20) {
                if let live = model.live { liveCard(live) }
                recentSection
                graphSection
            }
            .padding(16)

            Spacer(minLength: 0)
            Divider().overlay(StripPalette.border(dark: dark))
            footer
        }
        .frame(width: 420, height: 560)
        .background(StripPalette.card(dark: dark))
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text(duration(model.todayActiveSeconds))
                    .font(.system(size: 28, weight: .bold, design: .rounded))
                    .foregroundStyle(StripPalette.text(dark: dark))
                Text("active today")
                    .font(.system(size: 12))
                    .foregroundStyle(StripPalette.textDim(dark: dark))
            }
            Spacer()
            if model.streakDays > 1 {
                VStack(alignment: .trailing, spacing: 2) {
                    Text("\(model.streakDays)")
                        .font(.system(size: 22, weight: .semibold, design: .rounded))
                        .foregroundStyle(StripPalette.accent(dark: dark))
                    Text("day streak")
                        .font(.system(size: 11))
                        .foregroundStyle(StripPalette.textDim(dark: dark))
                }
            }
        }
        .padding(16)
    }

    private func liveCard(_ row: SessionRow) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Circle().fill(StripPalette.accent(dark: dark)).frame(width: 7, height: 7)
                Text("IN PROGRESS")
                    .font(.system(size: 10, weight: .bold)).kerning(0.8)
                    .foregroundStyle(StripPalette.accent(dark: dark))
                Spacer()
                Text(row.repo)
                    .font(.system(size: 11))
                    .foregroundStyle(StripPalette.textDim(dark: dark))
            }
            Text(duration(row.activeSeconds))
                .font(.system(size: 22, weight: .semibold, design: .rounded))
                .foregroundStyle(StripPalette.text(dark: dark))
            if !row.strip.isEmpty {
                TimelineStripView(
                    columns: row.strip, marks: row.marks,
                    spanMs: max(1, Int(row.wallSeconds * 1000)), preset: .row, dark: dark)
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(StripPalette.accent(dark: dark).opacity(0.10)))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(StripPalette.accent(dark: dark).opacity(0.35), lineWidth: 1))
    }

    private var recentSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionTitle("Recent sessions")
            ForEach(model.recent) { row in
                VStack(alignment: .leading, spacing: 6) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(row.headline)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(StripPalette.text(dark: dark))
                            .lineLimit(1)
                        Spacer(minLength: 8)
                        Text(duration(row.activeSeconds))
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(StripPalette.textDim(dark: dark))
                    }
                    if !row.strip.isEmpty {
                        TimelineStripView(
                            columns: row.strip, marks: row.marks,
                            spanMs: max(1, Int(row.wallSeconds * 1000)), preset: .row, dark: dark)
                    }
                    HStack(spacing: 6) {
                        Text(row.repo)
                        Text("·")
                        Text(dayLabel(row.startedAt))
                        if row.commits > 0 {
                            Text("·")
                            Text("\(row.commits) commits")
                        }
                        Spacer()
                        Text("\(row.prompts) prompts")
                    }
                    .font(.system(size: 11))
                    .foregroundStyle(StripPalette.textDim(dark: dark))
                }
            }
        }
    }

    private var graphSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionTitle("Last 17 weeks")
            ContributionGridView(days: model.graph, dark: dark, cell: 9, gap: 3)
            HStack(spacing: 5) {
                Text("less")
                ForEach(0..<6, id: \.self) { level in
                    RoundedRectangle(cornerRadius: 2)
                        .fill(StripPalette.graphLevel(level, dark: dark))
                        .frame(width: 9, height: 9)
                }
                Text("more")
                Spacer()
                Text("by active hours")
            }
            .font(.system(size: 10))
            .foregroundStyle(StripPalette.textDim(dark: dark))
        }
    }

    private var footer: some View {
        HStack {
            Text("\(duration(model.allTimeSeconds)) all time")
            Spacer()
            Text("\(model.totalSessions) sessions")
        }
        .font(.system(size: 10))
        .foregroundStyle(StripPalette.textDim(dark: dark))
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private func sectionTitle(_ s: String) -> some View {
        Text(s.uppercased())
            .font(.system(size: 10, weight: .bold)).kerning(0.8)
            .foregroundStyle(StripPalette.textDim(dark: dark))
    }

    private func duration(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        if s < 60 { return "\(s)s" }
        let h = s / 3600
        let m = (s % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }

    private func dayLabel(_ ts: Double) -> String {
        let df = DateFormatter()
        df.dateFormat = "d MMM HH:mm"
        return df.string(from: Date(timeIntervalSince1970: ts))
    }
}
