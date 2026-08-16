import BuilderModel
import SwiftUI

/// The only place generated colour tokens become SwiftUI `Color`s.
///
/// **Always `Color(.sRGB, ...)`, never `Color(red:green:blue:)`.** The bare initialiser is
/// Display P3, while `react-native-svg` on the phone takes sRGB hex — so using it would
/// make the Mac card visibly more saturated than the byte-identical phone card, and the
/// difference would show up only when someone put two screenshots side by side.
public enum StripPalette {

    public static func color(_ srgb: SRGB) -> Color {
        Color(.sRGB, red: srgb.r, green: srgb.g, blue: srgb.b, opacity: 1)
    }

    public static func pair(_ p: SRGBPair, dark: Bool) -> Color {
        color(p.resolve(dark: dark))
    }

    public static func stripClass(_ k: StripClass, dark: Bool) -> Color {
        switch k {
        case .idle: return pair(DesignTokens.idle, dark: dark)
        case .prompting: return pair(DesignTokens.prompting, dark: dark)
        case .agent: return pair(DesignTokens.agent, dark: dark)
        case .human_edit: return pair(DesignTokens.human_edit, dark: dark)
        }
    }

    public static func mark(_ k: StripMarkKind, dark: Bool) -> Color {
        switch k {
        case .prompt: return pair(DesignTokens.prompt, dark: dark)
        case .commit: return pair(DesignTokens.commit, dark: dark)
        case .compact: return pair(DesignTokens.compact, dark: dark)
        }
    }

    public static func background(dark: Bool) -> Color { pair(DesignTokens.bg, dark: dark) }
    public static func card(dark: Bool) -> Color { pair(DesignTokens.card, dark: dark) }
    public static func text(dark: Bool) -> Color { pair(DesignTokens.text, dark: dark) }
    public static func textDim(dark: Bool) -> Color { pair(DesignTokens.textDim, dark: dark) }
    public static func border(dark: Bool) -> Color { pair(DesignTokens.border, dark: dark) }
    public static func accent(dark: Bool) -> Color { pair(DesignTokens.accent, dark: dark) }

    /// Contribution-graph ramp, 0...5.
    public static func graphLevel(_ level: Int, dark: Bool) -> Color {
        let ramp = dark ? DesignTokens.graphDark : DesignTokens.graphLight
        return color(ramp[max(0, min(ramp.count - 1, level))])
    }
}
