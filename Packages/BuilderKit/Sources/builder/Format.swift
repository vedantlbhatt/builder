import Foundation

enum Fmt {
    static func int(_ n: Int) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: n)) ?? "\(n)"
    }

    /// Durations read as a person would say them. "1h 42m", not "102 minutes".
    static func duration(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        if s < 60 { return "\(s)s" }
        let h = s / 3600
        let m = (s % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }

    static func pad(_ s: String, _ n: Int) -> String {
        s.count >= n ? s : s + String(repeating: " ", count: n - s.count)
    }

    static func rpad(_ s: String, _ n: Int) -> String {
        s.count >= n ? s : String(repeating: " ", count: n - s.count) + s
    }

    static func date(_ ts: Double, _ format: String = "EEE d MMM HH:mm") -> String {
        let df = DateFormatter()
        df.dateFormat = format
        return df.string(from: Date(timeIntervalSince1970: ts))
    }

    static func heading(_ s: String) {
        print("")
        print(s)
    }

    static func bar(_ fraction: Double, width: Int = 28) -> String {
        let filled = max(0, min(width, Int(fraction * Double(width))))
        return String(repeating: "█", count: filled) + String(repeating: "·", count: width - filled)
    }
}

enum CLIArgs {
    static let all = Array(CommandLine.arguments.dropFirst())
    static var command: String { all.first ?? "help" }

    static func value(_ name: String) -> String? {
        guard let i = all.firstIndex(of: "--\(name)"), i + 1 < all.count else { return nil }
        return all[i + 1]
    }

    static func flag(_ name: String) -> Bool { all.contains("--\(name)") }

    static func double(_ name: String) -> Double? { value(name).flatMap(Double.init) }
}
