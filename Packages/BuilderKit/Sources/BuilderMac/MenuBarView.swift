import BuilderAnalysis
import BuilderModel
import BuilderUI
import SwiftUI

/// What drops down from the menu bar.
///
/// The ordering is the whole design: **today, then the live session, then recent work,
/// then the graph.** A menu bar item is opened to answer "how am I doing right now", and
/// anything that makes that question take a scroll has failed.
struct MenuBarView: View {

    @Environment(AppStore.self) private var store
    @State private var showPairing = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if let live = store.liveSession {
                        liveCard(live)
                    }
                    recentSection
                    graphSection
                    phoneRow
                }
                .padding(16)
            }

            Divider()
            footer
        }
        .frame(width: 420, height: 560)
        .background(StripPalette.card(dark: true))
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showPairing, onDismiss: { store.cancelPairing() }) {
            pairingSheet
        }
        .onChange(of: store.pairing) { _, now in
            if now == .paired { showPairing = false }
        }
    }

    // MARK: Header

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text(duration(store.todayActiveSeconds))
                    .font(.system(size: 28, weight: .bold, design: .rounded))
                    .foregroundStyle(StripPalette.text(dark: true))
                Text("active today")
                    .font(.system(size: 12))
                    .foregroundStyle(StripPalette.textDim(dark: true))
            }
            Spacer()
            if store.streakDays > 1 {
                VStack(alignment: .trailing, spacing: 2) {
                    Text("\(store.streakDays)")
                        .font(.system(size: 20, weight: .semibold, design: .rounded))
                        .foregroundStyle(StripPalette.accent(dark: true))
                    Text("day streak")
                        .font(.system(size: 11))
                        .foregroundStyle(StripPalette.textDim(dark: true))
                }
            }
        }
        .padding(16)
    }

    // MARK: Live

    private func liveCard(_ row: AppStore.SessionRow) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Circle()
                    .fill(StripPalette.accent(dark: true))
                    .frame(width: 7, height: 7)
                Text("IN PROGRESS")
                    .font(.system(size: 10, weight: .bold))
                    .kerning(0.8)
                    .foregroundStyle(StripPalette.accent(dark: true))
                Spacer()
                Text(row.repo)
                    .font(.system(size: 11))
                    .foregroundStyle(StripPalette.textDim(dark: true))
            }

            Text(duration(row.activeSeconds))
                .font(.system(size: 22, weight: .semibold, design: .rounded))
                .foregroundStyle(StripPalette.text(dark: true))

            if !row.strip.isEmpty {
                TimelineStripView(
                    columns: row.strip, marks: row.marks,
                    spanMs: max(1, Int(row.wallSeconds * 1000)), preset: .row, dark: true)
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(StripPalette.accent(dark: true).opacity(0.08)))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(StripPalette.accent(dark: true).opacity(0.35), lineWidth: 1))
    }

    // MARK: Recent

    private var recentSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionTitle("Recent sessions")

            if store.recent.isEmpty {
                Text(store.scanning ? "Reading your history…" : "No sessions yet.")
                    .font(.system(size: 12))
                    .foregroundStyle(StripPalette.textDim(dark: true))
            }

            ForEach(store.recent) { row in
                sessionRow(row)
            }
        }
    }

    private func sessionRow(_ row: AppStore.SessionRow) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(row.headline)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(StripPalette.text(dark: true))
                    .lineLimit(1)
                Spacer(minLength: 8)
                Text(duration(row.activeSeconds))
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(StripPalette.textDim(dark: true))
            }

            if !row.strip.isEmpty {
                TimelineStripView(
                    columns: row.strip, marks: row.marks,
                    spanMs: max(1, Int(row.wallSeconds * 1000)), preset: .row, dark: true)
            }

            HStack(spacing: 8) {
                Text(row.repo)
                Text("·")
                Text(relativeDate(row.startedAt))
                Spacer()
                Button {
                    store.share(sessionID: row.id)
                } label: {
                    Text("Share")
                        .font(.system(size: 11, weight: .medium))
                }
                .buttonStyle(.plain)
                .foregroundStyle(StripPalette.accent(dark: true))
            }
            .font(.system(size: 11))
            .foregroundStyle(StripPalette.textDim(dark: true))
        }
        .padding(.bottom, 4)
    }

    // MARK: Graph

    private var graphSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionTitle("Last 17 weeks")
            ContributionGridView(days: store.graph, dark: true)
            HStack(spacing: 6) {
                Text("less")
                ForEach(0..<6, id: \.self) { level in
                    RoundedRectangle(cornerRadius: 2)
                        .fill(StripPalette.graphLevel(level, dark: true))
                        .frame(width: 9, height: 9)
                }
                Text("more")
                Spacer()
                Text("by active hours")
            }
            .font(.system(size: 10))
            .foregroundStyle(StripPalette.textDim(dark: true))
        }
    }

    // MARK: Phone

    private var phoneRow: some View {
        PhoneConnectRow(
            status: store.pairing == .paired
                ? .paired(label: store.pairedLabel ?? "this Mac")
                : .notPaired,
            dark: true,
            onConnect: {
                showPairing = true
                store.startPairing()
            },
            onDisconnect: { store.disconnectPhone() })
    }

    /// The QR sheet. Same card colour and dark scheme as the panel underneath it, so it
    /// reads as part of the popover rather than a system dialog.
    private var pairingSheet: some View {
        VStack(spacing: 16) {
            HStack {
                Text("Connect your phone")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(StripPalette.text(dark: true))
                Spacer()
                Button {
                    showPairing = false
                } label: {
                    Text("Close")
                        .font(.system(size: 11, weight: .medium))
                }
                .buttonStyle(.plain)
                .foregroundStyle(StripPalette.accent(dark: true))
            }

            switch store.pairing {
            case .idle, .starting:
                Text("Requesting a code…")
                    .font(.system(size: 12))
                    .foregroundStyle(StripPalette.textDim(dark: true))
                    .frame(height: 220)

            case .waiting(let userCode, let deepLink, let expiresAt):
                PairingQRView(userCode: userCode, payload: deepLink, dark: true)
                Text("Waiting for your phone · code expires \(relativeTime(expiresAt))")
                    .font(.system(size: 11))
                    .foregroundStyle(StripPalette.textDim(dark: true))

            case .approved(let label):
                Text("Paired — this Mac is linked as “\(label)”.")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(StripPalette.text(dark: true))
                    .frame(height: 220)

            case .paired:
                Text("Phone connected.")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(StripPalette.text(dark: true))
                    .frame(height: 220)

            case .failed(let message):
                VStack(spacing: 10) {
                    Text(message)
                        .font(.system(size: 12))
                        .foregroundStyle(StripPalette.textDim(dark: true))
                        .multilineTextAlignment(.center)
                    Button {
                        store.startPairing()
                    } label: {
                        Text("Try again")
                            .font(.system(size: 11, weight: .medium))
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(StripPalette.accent(dark: true))
                }
                .frame(height: 220)
            }
        }
        .padding(20)
        .frame(width: 340)
        .background(StripPalette.card(dark: true))
        .preferredColorScheme(.dark)
    }

    // MARK: Footer

    private var footer: some View {
        HStack {
            if store.scanning {
                Text("scanning…")
            } else if let last = store.lastScanAt {
                Text("updated \(relativeTime(last))")
            }
            Spacer()
            Text("\(store.totalSessions) sessions")
        }
        .font(.system(size: 10))
        .foregroundStyle(StripPalette.textDim(dark: true))
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private func sectionTitle(_ s: String) -> some View {
        Text(s.uppercased())
            .font(.system(size: 10, weight: .bold))
            .kerning(0.8)
            .foregroundStyle(StripPalette.textDim(dark: true))
    }

    // MARK: Formatting

    private func duration(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        if s < 60 { return "\(s)s" }
        let h = s / 3600
        let m = (s % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }

    private func relativeDate(_ ts: Double) -> String {
        let df = DateFormatter()
        df.doesRelativeDateFormatting = true
        df.dateStyle = .medium
        df.timeStyle = .short
        return df.string(from: Date(timeIntervalSince1970: ts))
    }

    private func relativeTime(_ date: Date) -> String {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f.localizedString(for: date, relativeTo: Date())
    }
}
