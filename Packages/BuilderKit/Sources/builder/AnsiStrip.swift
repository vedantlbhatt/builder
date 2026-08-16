import BuilderModel
import Foundation

/// Renders a strip in the terminal.
///
/// This is the cheapest possible conformance test for `spec/strip.v1.json`: it decodes the
/// same bytes, uses the same ordinals, and resamples with the same nearest-neighbour rule
/// as the SwiftUI and React Native renderers. If a class ordinal ever gets swapped, this
/// shows it in a shell before anyone opens an app — and a swapped ordinal is the failure
/// that does not crash, does not look empty, and survives code review.
enum AnsiStrip {

    /// 24-bit colour from the generated design tokens, so the terminal, the Mac and the
    /// phone are all reading the same hex.
    private static func fg(_ c: SRGB) -> String {
        let r = Int((c.r * 255).rounded())
        let g = Int((c.g * 255).rounded())
        let b = Int((c.b * 255).rounded())
        return "\u{1B}[38;2;\(r);\(g);\(b)m"
    }

    private static let reset = "\u{1B}[0m"

    private static func colour(_ k: StripClass) -> SRGB {
        switch k {
        case .idle: return DesignTokens.idle.dark
        case .prompting: return DesignTokens.prompting.dark
        case .agent: return DesignTokens.agent.dark
        case .human_edit: return DesignTokens.human_edit.dark
        }
    }

    /// Denser glyph for denser columns — the second channel that makes the bar read as a
    /// barcode rather than a solid block.
    private static func glyph(_ k: StripClass, density: UInt8) -> String {
        if k == .idle { return "·" }
        switch density {
        case 0: return "▁"
        case 1: return "▃"
        case 2: return "▅"
        default: return "█"
        }
    }

    /// Render, overlaying marks on top of the columns.
    ///
    /// Marks are the reason the format keeps them out of the column array. A typed prompt
    /// occupies a second or two of a session that may run for hours; at any render width
    /// its column is dominated by the agent run that follows it, so column data alone
    /// erases the human from their own timeline. Overlaying restores them at full
    /// contrast regardless of scale.
    static func render(
        cols: [UInt8], width: Int = 60,
        marks: [(ms: Int, kind: StripMarkKind)] = [], spanMs: Int = 0
    ) -> String {
        guard !cols.isEmpty else { return "" }
        let resampled = StripSpec.resample(cols, to: width)

        var markAt: [Int: StripMarkKind] = [:]
        if spanMs > 0 {
            for m in marks {
                let col = max(0, min(width - 1, Int(Double(m.ms) / Double(spanMs) * Double(width))))
                // A prompt outranks a compaction marker on the same column: one is you,
                // the other is bookkeeping.
                if markAt[col] == nil || m.kind == .prompt { markAt[col] = m.kind }
            }
        }

        var out = ""
        var lastColour: SRGB?
        for (i, byte) in resampled.enumerated() {
            let (k, density) = StripSpec.unpack(byte)
            if let mark = markAt[i] {
                let c = mark == .prompt ? DesignTokens.prompt.dark : DesignTokens.compact.dark
                out += fg(c) + (mark == .prompt ? "▐" : "┊")
                lastColour = c
                continue
            }
            let c = colour(k)
            if lastColour == nil || lastColour! != c {
                out += fg(c)
                lastColour = c
            }
            out += glyph(k, density: density)
        }
        return out + reset
    }

    /// Decode the marks column stored as JSON `[[ms, kind], ...]`.
    static func decodeMarks(_ json: String?) -> [(ms: Int, kind: StripMarkKind)] {
        guard let json, let data = json.data(using: .utf8),
              let arr = try? JSONSerialization.jsonObject(with: data) as? [[Int]]
        else { return [] }
        return arr.compactMap { pair in
            guard pair.count == 2, let kind = StripMarkKind(rawValue: UInt8(pair[1])) else { return nil }
            return (ms: pair[0], kind: kind)
        }
    }

    /// A legend, shown in the terminal but deliberately NOT on a shared card: the moment
    /// an artifact explains its own encoding on its face, it is a chart, not an identity.
    static func legend() -> String {
        let items: [(StripClass, String)] = [
            (.prompting, "you prompting"),
            (.agent, "agent working"),
            (.human_edit, "you editing"),
            (.idle, "idle"),
        ]
        return items.map { fg(colour($0.0)) + "██" + reset + " " + $0.1 }.joined(separator: "   ")
    }
}
