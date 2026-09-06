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
    case geminiCLI = "gemini_cli"
    /// Declared ahead of its parser, like `cursorAgent`: the contract, the Postgres enum
    /// and this case land together so the parser is later a code change, not a migration.
    case cline = "cline"
    /// Uploaded by `python -m capture` (capture/harnesses.py), which reads every store on
    /// the machine, and read by no Swift parser: this build has no reason to open a SQLite
    /// database in `~/.local/share/opencode` or a markdown chat log in somebody's repo.
    /// The case exists because the WIRE carries these values, and a value the enum cannot
    /// name is a decode failure on a session the user can see on their phone.
    case opencode = "opencode"
    case aider = "aider"

    /// The label the menu bar, the card and the CLI print for this harness.
    public var displayName: String {
        switch self {
        case .claudeCode: return "Claude Code"
        case .cursorIDE: return "Cursor"
        case .cursorAgent: return "cursor-agent"
        case .codex: return "Codex"
        case .geminiCLI: return "Gemini CLI"
        case .cline: return "Cline"
        case .opencode: return "opencode"
        case .aider: return "Aider"
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
    ///
    /// Gemini CLI: `recordMessageTokens` writes a `TokensSummary` on the gemini message
    /// (analysis/gemini.py, VERIFIED from the CLI source). Cline: ASSUMED true from its
    /// per-message `api_req_started` usage until `analysis/cline.py` says otherwise; the
    /// flag is moot while `isImplemented` is false.
    public var reportsTokens: Bool {
        switch self {
        case .claudeCode, .codex, .geminiCLI, .cline: return true
        // opencode's `step-finish` parts and Aider's rounded `Tokens: 8.2k sent` lines are
        // both real, but `capture/harnesses.py` uploads these sessions with
        // `tokens_reported: false` because neither maps onto the per-message ledger without
        // becoming a number that is not comparable with the one printed beside it. Absent,
        // not zero, exactly as for Cursor.
        case .cursorIDE, .cursorAgent, .opencode, .aider: return false
        }
    }

    /// Whether a per-session model name is reliably recoverable.
    ///
    /// MEASURED for Cursor: `composerData.modelConfig.modelName` is the literal string
    /// `"default"` on 115 of 137 composers and names a real model on only 22, and
    /// individual message rows carry no model field at all. Any per-model breakdown for
    /// Cursor is therefore ~84% unknown, which is a labelling problem, not a parsing bug.
    ///
    /// Gemini CLI stamps `model` on every `type: 'gemini'` record (VERIFIED, same source).
    public var reportsModel: Bool {
        switch self {
        case .claudeCode, .codex, .geminiCLI, .cline: return true
        // Both stores name a model per message, but this build never reads them, and the
        // uploader that does cannot state a per-model token share without token counts.
        case .cursorIDE, .cursorAgent, .opencode, .aider: return false
        }
    }

    /// Whether this harness's parser is wired up in the current build.
    ///
    /// Codex: `CodexParser` reads `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. Its
    /// rules are ported from `analysis/codex.py` and verified against the synthetic
    /// fixture only — not yet against a real corpus.
    ///
    /// Gemini CLI: `GeminiParser` reads `~/.gemini/tmp/<project>/chats/**/*.jsonl`, ported
    /// from `analysis/gemini.py` and held to `spec/fixtures/gemini` — also not yet measured
    /// against a real corpus. Cline: contract and enum only; no parser in this build.
    public var isImplemented: Bool {
        switch self {
        case .claudeCode, .cursorIDE, .codex, .geminiCLI: return true
        // opencode and Aider are read by `python -m capture`, never by this build.
        case .cursorAgent, .cline, .opencode, .aider: return false
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
