import Foundation

/// What a record *is*, normalized across harnesses.
///
/// Parsers map their own vocabulary onto this and nothing downstream ever branches on the
/// harness to decide what happened — only to decide what is *available*.
public enum EventKind: String, Sendable, CaseIterable, Codable {
    /// A human typed something. Claude Code: `type == "user"` with
    /// `promptSource == "typed"` and `isMeta != true`. MEASURED: this is 1,456 records
    /// where a naive `type == "user"` count gives 18,836 — the other ~17k are tool results
    /// and system-injected context. Counting them as prompts inflates by ~13x.
    case prompt

    case assistantMessage = "assistant_message"
    case thinking
    case toolUse = "tool_use"
    case toolResult = "tool_result"

    /// The human edited a file OUTSIDE the agent. Claude Code surfaces this as an
    /// `attachment` of type `edited_text_file`. MEASURED: 296 records, against 2,227 Edits
    /// and 581 Writes by the agent.
    ///
    /// It carries NO line count. Existence is measurable; magnitude never is. That single
    /// fact is why human-vs-agent ships as a bounded bucket rather than a percentage.
    case humanEdit = "human_edit"

    /// `system` / `turn_duration`, carrying real per-turn wall time.
    /// MEASURED: 1,904 records against 30,840 assistant records — 6% coverage. Used as a
    /// cross-check on derived agent spans, never as the sole source.
    case turnDuration = "turn_duration"

    /// `system` / `compact_boundary`. A zero-duration marker, never a session boundary.
    /// MEASURED: 7 occurrences, all mid-flow. `parentUuid` is null across it, so the DAG
    /// walk must follow `logicalParentUuid` or the conversation fragments.
    /// Its `preTokens`/`postTokens` are CONTEXT SIZES, not usage. Never summed.
    case compaction

    /// `ai-title` / `last-prompt`. The title the harness already wrote to disk.
    case title

    /// Bookkeeping with no product meaning. MEASURED: ~24,151 of 108,504 records have no
    /// timestamp at all and are entirely of this kind — `mode`, `permission-mode`,
    /// `file-history-snapshot`, `relocated`, `worktree-state`, `agent-name`, and friends.
    case noise

    /// A record shape this parser version does not recognise. Recorded, not dropped, and
    /// counted in diagnostics — harness formats drift, and a silent skip is how a parser
    /// starts under-reporting without anyone noticing.
    case unknown

    /// Events that can open or extend a session. Bookkeeping cannot.
    public var isSubstantive: Bool {
        switch self {
        case .prompt, .assistantMessage, .thinking, .toolUse, .toolResult, .humanEdit:
            return true
        case .turnDuration, .compaction, .title, .noise, .unknown:
            return false
        }
    }

    /// Events that count toward `Tuning.countedMinMeaningfulEvents`.
    public var isMeaningful: Bool {
        switch self {
        case .prompt, .toolUse, .humanEdit: return true
        default: return false
        }
    }
}

/// One row as every parser emits it, and exactly what `raw_event` stores.
///
/// All scalars, `Sendable`, no references into the source buffer. Parsers hand these back
/// in file order and the ingest layer never re-reads the file to fill something in.
public struct NormalizedEvent: Sendable, Equatable {

    // MARK: Identity

    /// `sha256(harness | source_id | native_event_id)`.
    ///
    /// The ordinal is deliberately NOT part of this. Embedding it means a `parserVersion`
    /// bump shifts every uid, `INSERT OR IGNORE` stops suppressing re-ingested rows, and
    /// the partial unique index on token usage aborts that source's transaction forever —
    /// while the watermark commits in the same transaction, so the source never advances.
    public var eventUID: String

    public var harness: Harness

    /// Hash of a canonical source descriptor, not the mutable path. A file that moves is
    /// still the same source; two files with the same name in different projects are not.
    public var sourceID: String

    /// Position in the file. NOT time order — MEASURED: 2,472 adjacent pairs are out of
    /// chronological order because background and parallel agents flush interleaved. Used
    /// for tie-breaking and for choosing the authoritative usage row, never for ordering.
    public var ordinal: Int

    public var nativeSessionID: String?
    public var nativeEventID: String?

    /// `.parentUuid ?? .logicalParentUuid`. MEASURED: this forms a DAG, not a chain —
    /// 225 fork points from rewinds and edits. See `LivePathResolver`.
    public var nativeParentID: String?

    public var agentID: String?

    /// True for subagent sidecar transcripts. They inherit the PARENT's `sessionId`, so
    /// they attach to the root session and never form their own.
    public var isSidechain: Bool

    /// `nil` until `LivePathResolver` runs; `false` marks a record on a rewound branch.
    ///
    /// Tokens are summed over every record regardless — the API charged for the abandoned
    /// branch. Lines, file counts, tool counts and strip segments are summed over
    /// `!= false` only, because an edit that was rewound never reached the file.
    public var onLivePath: Bool?

    // MARK: Time

    /// Unix seconds. `nil` on bookkeeping records and NEVER imputed.
    ///
    /// Interpolating from file-order neighbours was considered and rejected: file order is
    /// not time order, so interpolation can run the clock backwards. Untimestamped records
    /// attach to the nearest preceding timestamped record in the same file, contribute to
    /// counts only, and can never create, extend or bound a session or a strip segment.
    public var ts: Double?

    // MARK: Context

    /// The working directory AT THIS RECORD. MEASURED: it varies within a single file, so
    /// one repo per file is wrong. Never derived from the project directory name — that
    /// encoding is `[^a-zA-Z0-9] -> '-'`, which is lossy and irreversible: `/a/b-c`,
    /// `/a/b/c`, `/a/b.c` and `/a/b c` all collapse to `-a-b-c`.
    public var cwd: String?

    public var harnessVersion: String?

    /// Already `NULLIF(model, '')` — Codex writes empty strings, not nulls, for columns
    /// added in later migrations. The `[1m]` context suffix is preserved verbatim.
    public var model: String?
    public var effort: String?

    // MARK: Tokens

    /// `sourceID | message.id`, the deduplication key.
    ///
    /// MEASURED: 44,419 assistant records carry `.message.usage` but there are only 22,887
    /// distinct `.message.id` values, because Claude Code writes ONE RECORD PER CONTENT
    /// BLOCK and repeats the identical usage object on each. Naive sum 10,922,288,007 vs
    /// deduped 5,815,701,063 — a factor of 1.878.
    public var dedupeKey: String?

    /// Exactly one record per `dedupeKey` carries the usage. Enforced by a partial UNIQUE
    /// INDEX so a second one is a constraint violation rather than a plausible number.
    public var usageAuthoritative: Bool

    public var tokIn: Int?
    public var tokOut: Int?
    public var tokCacheRead: Int?
    /// 5m and 1h cache writes are stored SEPARATELY because they price differently.
    public var tokCacheW5m: Int?
    public var tokCacheW1h: Int?

    // MARK: Payload

    public var kind: EventKind
    public var role: String?
    public var toolName: String?
    public var toolID: String?

    /// Kept LOCALLY for file-touch counts and human-edit correlation. Never uploaded —
    /// file paths and file names are on the never-leaves list.
    public var targetPath: String?

    /// From Edit's `structuredPatch` (MEASURED: present on 2,608 records) or, for Write,
    /// from counting newlines in the created content — Write's `structuredPatch` is `[]`.
    public var linesAdded: Int?
    public var linesRemoved: Int?

    public var durationMs: Int?
    public var title: String?

    /// `last-prompt.leafUuid`, which points at a real timestamped record. This is how a
    /// title finds the session that actually contains it: one file routinely produces many
    /// sessions (43 files have internal gaps over an hour; the longest single-file span is
    /// 121.3 hours), but carries only one current title, so "last title in the file" would
    /// stamp this afternoon's title onto a card for three days ago.
    public var leafUUID: String?

    /// Small structured extras. NEVER prompt, code or diff text.
    public var extra: [String: String]?

    public init(
        eventUID: String,
        harness: Harness,
        sourceID: String,
        ordinal: Int,
        nativeSessionID: String? = nil,
        nativeEventID: String? = nil,
        nativeParentID: String? = nil,
        agentID: String? = nil,
        isSidechain: Bool = false,
        onLivePath: Bool? = nil,
        ts: Double? = nil,
        cwd: String? = nil,
        harnessVersion: String? = nil,
        model: String? = nil,
        effort: String? = nil,
        dedupeKey: String? = nil,
        usageAuthoritative: Bool = false,
        tokIn: Int? = nil,
        tokOut: Int? = nil,
        tokCacheRead: Int? = nil,
        tokCacheW5m: Int? = nil,
        tokCacheW1h: Int? = nil,
        kind: EventKind,
        role: String? = nil,
        toolName: String? = nil,
        toolID: String? = nil,
        targetPath: String? = nil,
        linesAdded: Int? = nil,
        linesRemoved: Int? = nil,
        durationMs: Int? = nil,
        title: String? = nil,
        leafUUID: String? = nil,
        extra: [String: String]? = nil
    ) {
        self.eventUID = eventUID
        self.harness = harness
        self.sourceID = sourceID
        self.ordinal = ordinal
        self.nativeSessionID = nativeSessionID
        self.nativeEventID = nativeEventID
        self.nativeParentID = nativeParentID
        self.agentID = agentID
        self.isSidechain = isSidechain
        self.onLivePath = onLivePath
        self.ts = ts
        self.cwd = cwd
        self.harnessVersion = harnessVersion
        self.model = model
        self.effort = effort
        self.dedupeKey = dedupeKey
        self.usageAuthoritative = usageAuthoritative
        self.tokIn = tokIn
        self.tokOut = tokOut
        self.tokCacheRead = tokCacheRead
        self.tokCacheW5m = tokCacheW5m
        self.tokCacheW1h = tokCacheW1h
        self.kind = kind
        self.role = role
        self.toolName = toolName
        self.toolID = toolID
        self.targetPath = targetPath
        self.linesAdded = linesAdded
        self.linesRemoved = linesRemoved
        self.durationMs = durationMs
        self.title = title
        self.leafUUID = leafUUID
        self.extra = extra
    }
}
