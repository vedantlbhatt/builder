import BuilderModel
import Foundation

/// The copy for a "your session finished" alert.
///
/// Built here rather than in the UI so the terminal, the menu bar app and the phone all
/// say the same thing, and so the wording can be tested.
public struct SessionAlert: Sendable {
    /// Which headline. They are different sentences to different people: one
    /// congratulates the person who was there, the other tells them a machine they
    /// walked away from has stopped.
    public enum Kind: String, Sendable {
        case sessionFinished = "session_finished"
        case runFinished = "run_finished"
    }

    public let kind: Kind
    public let title: String
    public let body: String
    public let clientSessionID: String

    /// "Session finished: 1h 42m in gt-transit" / "+2,140 lines · 9 prompts"
    /// "Agent run finished: 6h 12m in gt-transit" / "+2,101 lines · ran unattended · started 23:04"
    ///
    /// Duration first because it is the one number that is always available and always
    /// true — Cursor sessions have no tokens and many sessions have no commits, so
    /// leading with either would produce an alert that reads as broken for a large
    /// fraction of real sessions.
    public init(
        session: DetectedSession, kind: Kind = .sessionFinished,
        repoName: String?, agentLines: Int, prompts: Int
    ) {
        self.kind = kind
        let duration = SessionAlert.duration(session.activeSeconds)
        let headline: String
        switch kind {
        case .sessionFinished: headline = "Session finished"
        case .runFinished: headline = "Agent run finished"
        }
        if let repoName {
            title = "\(headline): \(duration) in \(repoName)"
        } else {
            title = "\(headline): \(duration)"
        }

        var parts: [String] = []
        if agentLines > 0 { parts.append("+\(SessionAlert.int(agentLines)) lines") }
        switch kind {
        case .sessionFinished:
            if prompts > 0 { parts.append("\(prompts) prompt\(prompts == 1 ? "" : "s")") }
            let elapsed = SessionAlert.duration(session.wallSeconds)
            if session.wallSeconds > session.activeSeconds * 1.2 {
                parts.append("\(elapsed) elapsed")
            }
        case .runFinished:
            // No prompt count: by definition there were none. When it started is the
            // one thing the person does remember about a run they walked away from.
            parts.append("ran unattended")
            parts.append("started \(SessionAlert.clock(session.startedAt))")
        }
        body = parts.joined(separator: " · ")
        clientSessionID = session.clientSessionID
    }

    /// Local wall-clock time, "23:04".
    static func clock(_ ts: Double) -> String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return f.string(from: Date(timeIntervalSince1970: ts))
    }

    static func duration(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        let h = s / 3600
        let m = (s % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }

    static func int(_ n: Int) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: n)) ?? "\(n)"
    }
}

public protocol Notifier: Sendable {
    var channel: String { get }
    func deliver(_ alert: SessionAlert) throws
}

/// Prints to the terminal. What `builder watch` uses.
public struct ConsoleNotifier: Notifier {
    public let channel = "console"
    public init() {}

    public func deliver(_ alert: SessionAlert) throws {
        let stamp = DateFormatter()
        stamp.dateFormat = "HH:mm:ss"
        print("")
        print("  \u{1B}[38;2;255;179;0m●\u{1B}[0m \(alert.title)")
        if !alert.body.isEmpty { print("    \(alert.body)") }
        print("    \(stamp.string(from: Date()))")
    }
}

/// A real macOS notification, via `osascript`.
///
/// The CLI cannot use `UNUserNotificationCenter`: that API requires a bundle identifier,
/// and a bare SwiftPM executable has no bundle. The menu bar app will use the real API
/// with actionable View/Share buttons; this exists so the completion loop is observable
/// end to end long before any Xcode project exists.
public struct MacOSNotifier: Notifier {
    public let channel = "local"
    public init() {}

    public func deliver(_ alert: SessionAlert) throws {
        func escape(_ s: String) -> String {
            s.replacingOccurrences(of: "\\", with: "\\\\")
                .replacingOccurrences(of: "\"", with: "\\\"")
        }
        let script =
            "display notification \"\(escape(alert.body))\" with title \"\(escape(alert.title))\""

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", script]
        p.standardError = Pipe()
        try? p.run()
        p.waitUntilExit()
    }
}

/// Delivers to several channels, and never lets one failure suppress the others.
public struct MultiNotifier: Notifier {
    public let channel = "multi"
    private let children: [any Notifier]

    public init(_ children: [any Notifier]) { self.children = children }

    public func deliver(_ alert: SessionAlert) throws {
        for c in children { try? c.deliver(alert) }
    }
}
