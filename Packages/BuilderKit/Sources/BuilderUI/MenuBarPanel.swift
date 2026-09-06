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

    /// Whether this Mac is linked to the phone app. `nil` in the model hides the row
    /// entirely, which is what offscreen previews and screenshots want.
    public enum PhoneLink: Sendable, Equatable {
        case notPaired
        case paired(label: String)
    }

    /// The part of a stored `SessionAnalysis` the panel shows. Plain strings, already
    /// worded for display, so the panel neither imports the document type nor decides
    /// how an enum is spelled.
    public struct AnalysisSummary: Sendable, Equatable {
        public let headline: String
        public let summary: String
        /// Display form, e.g. "shipped".
        public let outcome: String
        /// Display form, e.g. "velocity machine". nil when the session was too short.
        public let archetype: String?
        /// A live mid-run reading rather than the final one.
        public let checkpoint: Bool

        public init(
            headline: String, summary: String, outcome: String, archetype: String? = nil,
            checkpoint: Bool = false
        ) {
            self.headline = headline
            self.summary = summary
            self.outcome = outcome
            self.archetype = archetype
            self.checkpoint = checkpoint
        }

        /// Underscores become spaces, the same `labelize` the phone applies.
        public init(analysis: SessionAnalysis, checkpoint: Bool = false) {
            self.init(
                headline: analysis.headline,
                summary: analysis.summary,
                outcome: analysis.outcome.rawValue.replacingOccurrences(of: "_", with: " "),
                archetype: analysis.archetype?.rawValue.replacingOccurrences(of: "_", with: " "),
                checkpoint: checkpoint)
        }

        /// One-sentence-ish: the summary cut at about `limit` characters with "…".
        ///
        /// Cuts at the last word boundary before the limit rather than mid-word, and the
        /// first sentence wins outright when it fits, so a 700-character summary reads as
        /// a lead rather than a fragment.
        public func shortSummary(limit: Int = 160) -> String {
            let text = summary.trimmingCharacters(in: .whitespacesAndNewlines)
            if text.count <= limit { return text }
            // A full stop followed by a space ends a sentence; a bare "." may be "1.5 h".
            if let stop = text.range(of: ". "),
               text.distance(from: text.startIndex, to: stop.lowerBound) < limit {
                return String(text[...stop.lowerBound])
            }
            let cut = text.index(text.startIndex, offsetBy: limit)
            let head = text[..<cut]
            let atWord = head.lastIndex(of: " ").map { head[..<$0] } ?? head
            return String(atWord).trimmingCharacters(in: .punctuationCharacters) + "…"
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
        public let phone: PhoneLink?
        /// The reading for the session the top card describes: the live one when it has
        /// a checkpoint, otherwise the last notable session. nil renders the quiet
        /// "Analysis runs when a session ends" line.
        public let analysis: AnalysisSummary?

        public init(
            todayActiveSeconds: Double, streakDays: Int, totalSessions: Int,
            allTimeSeconds: Double, live: SessionRow?, recent: [SessionRow],
            graph: [Analysis.GraphDay], phone: PhoneLink? = nil,
            analysis: AnalysisSummary? = nil
        ) {
            self.todayActiveSeconds = todayActiveSeconds
            self.streakDays = streakDays
            self.totalSessions = totalSessions
            self.allTimeSeconds = allTimeSeconds
            self.live = live
            self.recent = recent
            self.graph = graph
            self.phone = phone
            self.analysis = analysis
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
                AnalysisBlock(summary: model.analysis, dark: dark)
                recentSection
                graphSection
                if let phone = model.phone {
                    PhoneConnectRow(status: phone, dark: dark)
                }
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

/// What the model made of the last session, under the top card.
///
/// Shared between the pure-data panel and the live app, like `PhoneConnectRow`, so the
/// two cannot drift. The full reading (highlights, dimensions, growth edge) lives on the
/// phone; this is the headline and enough of the summary to decide whether to open it.
/// With nothing stored it is one quiet line, never an empty section.
public struct AnalysisBlock: View {

    let summary: MenuBarPanel.AnalysisSummary?
    let dark: Bool

    public init(summary: MenuBarPanel.AnalysisSummary?, dark: Bool = true) {
        self.summary = summary
        self.dark = dark
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("ANALYSIS")
                .font(.system(size: 10, weight: .bold)).kerning(0.8)
                .foregroundStyle(StripPalette.textDim(dark: dark))

            if let a = summary {
                Text(a.headline)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(StripPalette.text(dark: dark))
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                Text(a.shortSummary())
                    .font(.system(size: 11))
                    .foregroundStyle(StripPalette.textDim(dark: dark))
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 6) {
                    chip(a.outcome, accent: true)
                    if let archetype = a.archetype { chip(archetype, accent: false) }
                    Spacer(minLength: 8)
                    Text(a.checkpoint ? "Mid-run reading · read it on your phone" : "Read on your phone")
                        .font(.system(size: 10))
                        .foregroundStyle(StripPalette.textDim(dark: dark))
                        .lineLimit(1)
                }
            } else {
                Text("Analysis runs when a session ends")
                    .font(.system(size: 11))
                    .foregroundStyle(StripPalette.textDim(dark: dark))
            }
        }
    }

    private func chip(_ text: String, accent: Bool) -> some View {
        Text(text)
            .font(.system(size: 10, weight: .semibold))
            .foregroundStyle(accent ? StripPalette.accent(dark: dark) : StripPalette.textDim(dark: dark))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(
                Capsule().stroke(
                    accent ? StripPalette.accent(dark: dark).opacity(0.5) : StripPalette.border(dark: dark),
                    lineWidth: 1))
    }
}

/// The last row of the panel: how to get sessions onto the phone, or the fact that they
/// already arrive there.
///
/// Shared between the pure-data panel and the live app so the two cannot drift. The
/// closures are optional because the offscreen panel has nothing to call — a row with no
/// action renders the same text without a button.
public struct PhoneConnectRow: View {

    let status: MenuBarPanel.PhoneLink
    let dark: Bool
    let onConnect: (() -> Void)?
    let onDisconnect: (() -> Void)?

    public init(
        status: MenuBarPanel.PhoneLink, dark: Bool = true,
        onConnect: (() -> Void)? = nil, onDisconnect: (() -> Void)? = nil
    ) {
        self.status = status
        self.dark = dark
        self.onConnect = onConnect
        self.onDisconnect = onDisconnect
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("PHONE")
                .font(.system(size: 10, weight: .bold)).kerning(0.8)
                .foregroundStyle(StripPalette.textDim(dark: dark))

            switch status {
            case .notPaired:
                HStack(spacing: 8) {
                    Image(systemName: "qrcode")
                        .font(.system(size: 12))
                        .foregroundStyle(StripPalette.accent(dark: dark))
                    Text("Connect your phone")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(StripPalette.text(dark: dark))
                    Spacer(minLength: 8)
                    if let onConnect {
                        Button(action: onConnect) {
                            Text("Show code")
                                .font(.system(size: 11, weight: .medium))
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(StripPalette.accent(dark: dark))
                    }
                }
                Text("Scan a code once and finished sessions arrive on your phone on their own.")
                    .font(.system(size: 11))
                    .foregroundStyle(StripPalette.textDim(dark: dark))

            case .paired(let label):
                HStack(spacing: 6) {
                    Image(systemName: "iphone")
                        .font(.system(size: 11))
                    Text("Phone connected")
                    Text("·")
                    Text(label).lineLimit(1)
                    Spacer(minLength: 8)
                    if let onDisconnect {
                        Button(action: onDisconnect) {
                            Text("Disconnect")
                                .font(.system(size: 11, weight: .medium))
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(StripPalette.accent(dark: dark))
                    }
                }
                .font(.system(size: 11))
                .foregroundStyle(StripPalette.textDim(dark: dark))
            }
        }
    }
}
