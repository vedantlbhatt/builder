import Foundation

/// The tools Builder can read.
///
/// `cursorAgent` and `codex` are declared from day one although their parsers land last
/// (WP-14). Having the enum case now means adding the parser later is not a schema
/// migration, and — more importantly — that nothing else in the system needs to learn a
/// new string at that point.
///
/// The raw values are wire format. `cursor_ide`, NOT `cursor`: five of six independent
/// designs emitted `cursor_ide`, and a mismatched label reaching Postgres is a hard 22P02
/// that aborts an entire batch insert and takes the Claude Code rows down with it.
public enum Harness: String, Sendable, CaseIterable, Codable {
    case claudeCode = "claude_code"
    case cursorIDE = "cursor_ide"
    case cursorAgent = "cursor_agent"
    case codex = "codex"

    public var displayName: String {
        switch self {
        case .claudeCode: return "Claude Code"
        case .cursorIDE: return "Cursor"
        case .cursorAgent: return "cursor-agent"
        case .codex: return "Codex"
        }
    }

    /// Whether this harness records token usage on disk at all.
    ///
    /// MEASURED for Cursor: across all 14,565 `bubbleId:` rows in globalStorage,
    /// `tokenCount.inputTokens > 0` matched 0 rows and `outputTokens > 0` matched 0 rows.
    /// The field exists and is always `{0, 0}` — usage is accounted server-side and never
    /// written locally. So a Cursor session's token count is not missing data to be
    /// back-filled later; it is *structurally absent*, and the UI must say so rather than
    /// render a zero that looks like a bug.
    public var reportsTokens: Bool {
        switch self {
        case .claudeCode, .codex: return true
        case .cursorIDE, .cursorAgent: return false
        }
    }

    /// Whether a per-session model name is reliably recoverable.
    ///
    /// MEASURED for Cursor: `composerData.modelConfig.modelName` is the literal string
    /// `"default"` on 115 of 137 composers and names a real model on only 22, and
    /// individual message rows carry no model field at all. Any per-model breakdown for
    /// Cursor is therefore ~84% unknown, which is a labelling problem, not a parsing bug.
    public var reportsModel: Bool {
        switch self {
        case .claudeCode, .codex: return true
        case .cursorIDE, .cursorAgent: return false
        }
    }

    /// Whether this harness's parser is wired up in the current build.
    public var isImplemented: Bool {
        switch self {
        case .claudeCode, .cursorIDE: return true
        case .cursorAgent, .codex: return false
        }
    }
}

/// How much of a session's shape survived on disk by the time we read it.
///
/// This exists because retention is not hypothetical. MEASURED on the reference machine:
/// Cursor keeps 482 conversation headers but message bodies for only 49 distinct
/// composers — 433 conversations are header-only, and the cliff falls at roughly two
/// months. Claude Code runs a 30-day cleanup (`~/.claude/.last-cleanup` advanced twice
/// during a single planning session).
///
/// A header-only session still contributes its hours to the contribution graph, because
/// those hours genuinely happened. It is never rendered as a card, because a card with an
/// empty strip reads as a broken app rather than as integrity.
public enum TimelineFidelity: String, Sendable, CaseIterable, Codable, Comparable {
    /// Full per-event timeline: real strip, real counts.
    case full
    /// Session-level totals and boundaries, but no per-event detail.
    case coarse
    /// Start, end and a few aggregates. Hours only.
    case headerOnly = "header_only"

    private var rank: Int {
        switch self {
        case .headerOnly: return 0
        case .coarse: return 1
        case .full: return 2
        }
    }

    public static func < (a: TimelineFidelity, b: TimelineFidelity) -> Bool { a.rank < b.rank }

    /// Fidelity may only ever be revised UPWARD.
    ///
    /// Cursor vacuums its database while running, so a read that races the GC can see
    /// bodies missing for a conversation that had them ten seconds ago. Letting fidelity
    /// fall would permanently downgrade a good session because of a transient SQLITE_BUSY.
    public func merged(with other: TimelineFidelity) -> TimelineFidelity { Swift.max(self, other) }
}
