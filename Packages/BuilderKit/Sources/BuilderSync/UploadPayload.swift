import BuilderModel
import Foundation

/// The wire payload, and the structural guarantee behind the privacy claim.
///
/// **There is no synthesized `Codable` in this file, and no hand-written `CodingKeys`.**
/// `encode(to:)` opens a container keyed by the GENERATED `UploadField` enum and writes
/// through it. A field that is not a case in that enum is *unrepresentable on the wire* —
/// so adding a stored property to any model, anywhere, cannot change what gets sent.
///
/// That is a stronger guarantee than a test. A test says "we checked"; this says "there
/// is no code path". The tests then check the enum matches the published contract in both
/// directions, and a canary test asserts at the URLSession layer that no transcript text
/// reaches the network through any request at all.
public struct SessionUpload: Sendable {

    // Identity and provenance
    public var clientSessionID: String
    public var machineID: String
    public var contentHash: String
    public var clientVersion: String
    public var sessionizerVersion: Int
    public var activeCalcVersion: Int
    public var harness: Harness
    public var agentObservedAt: Date
    public var clientClockOffsetMs: Int

    // Time
    public var startedAt: Date
    public var endedAt: Date
    public var activeSeconds: Int
    public var idleSeconds: Int
    public var tzOffsetMinutes: Int
    public var timeQuality: String
    /// `live` for an open/idle snapshot, replaced in place when it finalizes; `final` otherwise.
    public var state: String
    /// Why the session ended (docs/session-boundaries.md). `still_running` IS the live upload.
    public var endReason: String
    /// The two clocks. `attendedSeconds + autonomousSeconds == activeSeconds`, and the
    /// server rejects a payload where they disagree by more than a second.
    public var attendedSeconds: Int
    public var autonomousSeconds: Int
    /// Typed prompts + interrupts + human file edits.
    public var presenceCount: Int
    /// Zero presence signals over a notable span. Counts toward hours only: no record, no
    /// streak, no notification. The server rejects `false` with zero presence over 1200 s.
    public var unattended: Bool
    public var visible: Bool
    public var notable: Bool

    // The one field that carries prose. Opt-in, produced locally by the user's own Claude
    // Code against spec/analysis.v1.json, omitted from the wire — not sent as null — when
    // analysis upload is off. See the `analysis` entry in privacy/upload-contract.json.
    public var analysis: SessionAnalysis?

    // Shape — no text, no paths, by construction
    public var stripColumns: String  // base64, exactly 1024 bytes
    public var stripMarks: [StripMarkWire]
    public var timelineFidelity: String

    // Counts only
    public var humanPromptCount: Int
    public var promptCountBasis: String
    public var toolCalls: [String: Int]
    public var filesTouched: Int
    public var filesCreated: Int
    public var linesAddedAgent: Int
    public var linesRemovedAgent: Int
    public var commitCount: Int
    public var commitInsertions: Int
    public var commitDeletions: Int
    public var humanEditEvents: Int
    public var agentLineBucket: String
    public var attribConfidence: String

    // Tokens
    public var tokensReported: Bool
    public var tokens: TokenBucketsWire?
    public var abandonedBranchTokens: Int
    public var tokenDedupe: String
    public var tokenScope: String
    public var tokenCoverage: String
    public var models: [ModelShareWire]
    public var modelState: String

    // Repo
    public var repoHash: String?
    public var repoPepperVersion: Int
    public var repoIDBasis: String

    // Public repos only
    public var repoName: String?
    public var title: String?
    public var titleSource: String?

    // Explicit share only
    public var cardPNGURL: String?

    public init(
        clientSessionID: String, machineID: String, contentHash: String, clientVersion: String,
        sessionizerVersion: Int, activeCalcVersion: Int, harness: Harness, agentObservedAt: Date,
        clientClockOffsetMs: Int, startedAt: Date, endedAt: Date, activeSeconds: Int,
        idleSeconds: Int, tzOffsetMinutes: Int, timeQuality: String, state: String,
        visible: Bool, notable: Bool, stripColumns: String, stripMarks: [StripMarkWire],
        timelineFidelity: String, humanPromptCount: Int, promptCountBasis: String,
        toolCalls: [String: Int], filesTouched: Int, filesCreated: Int, linesAddedAgent: Int,
        linesRemovedAgent: Int, commitCount: Int, commitInsertions: Int, commitDeletions: Int,
        humanEditEvents: Int, agentLineBucket: String, attribConfidence: String,
        tokensReported: Bool, tokens: TokenBucketsWire?, abandonedBranchTokens: Int,
        tokenDedupe: String, tokenScope: String, tokenCoverage: String,
        models: [ModelShareWire], modelState: String, repoHash: String?,
        repoPepperVersion: Int, repoIDBasis: String, repoName: String? = nil,
        title: String? = nil, titleSource: String? = nil, cardPNGURL: String? = nil,
        // Contract v2. Defaulted, at the end, so every v1 call site still compiles; the
        // defaults describe a fully attended final session, which is what every v1 payload
        // implicitly claimed. A caller that knows better must pass all six.
        endReason: String = "idle_gap", attendedSeconds: Int = 0, autonomousSeconds: Int = 0,
        presenceCount: Int = 0, unattended: Bool = false, analysis: SessionAnalysis? = nil
    ) {
        self.clientSessionID = clientSessionID
        self.machineID = machineID
        self.contentHash = contentHash
        self.clientVersion = clientVersion
        self.sessionizerVersion = sessionizerVersion
        self.activeCalcVersion = activeCalcVersion
        self.harness = harness
        self.agentObservedAt = agentObservedAt
        self.clientClockOffsetMs = clientClockOffsetMs
        self.startedAt = startedAt
        self.endedAt = endedAt
        self.activeSeconds = activeSeconds
        self.idleSeconds = idleSeconds
        self.tzOffsetMinutes = tzOffsetMinutes
        self.timeQuality = timeQuality
        self.state = state
        self.endReason = endReason
        self.attendedSeconds = attendedSeconds
        self.autonomousSeconds = autonomousSeconds
        self.presenceCount = presenceCount
        self.unattended = unattended
        self.visible = visible
        self.notable = notable
        self.analysis = analysis
        self.stripColumns = stripColumns
        self.stripMarks = stripMarks
        self.timelineFidelity = timelineFidelity
        self.humanPromptCount = humanPromptCount
        self.promptCountBasis = promptCountBasis
        self.toolCalls = toolCalls
        self.filesTouched = filesTouched
        self.filesCreated = filesCreated
        self.linesAddedAgent = linesAddedAgent
        self.linesRemovedAgent = linesRemovedAgent
        self.commitCount = commitCount
        self.commitInsertions = commitInsertions
        self.commitDeletions = commitDeletions
        self.humanEditEvents = humanEditEvents
        self.agentLineBucket = agentLineBucket
        self.attribConfidence = attribConfidence
        self.tokensReported = tokensReported
        self.tokens = tokens
        self.abandonedBranchTokens = abandonedBranchTokens
        self.tokenDedupe = tokenDedupe
        self.tokenScope = tokenScope
        self.tokenCoverage = tokenCoverage
        self.models = models
        self.modelState = modelState
        self.repoHash = repoHash
        self.repoPepperVersion = repoPepperVersion
        self.repoIDBasis = repoIDBasis
        self.repoName = repoName
        self.title = title
        self.titleSource = titleSource
        self.cardPNGURL = cardPNGURL
    }
}

extension SessionUpload: Encodable {

    /// Hand-written, through the generated key enum, and filtered by privacy mode.
    ///
    /// `repo_name`, `title` and `title_source` are omitted entirely for anonymous
    /// repositories — not sent as null, not sent as empty. A null would still be a
    /// statement about the session; absence is the only honest encoding of "we did not
    /// look at that".
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: UploadField.self)
        let mode: PrivacyMode = repoName == nil ? .anonymous : .publicRepo
        let allowed = mode.allowedFields

        func put<T: Encodable>(_ value: T, _ field: UploadField) throws {
            guard allowed.contains(field) else { return }
            try c.encode(value, forKey: field)
        }
        func putIfPresent<T: Encodable>(_ value: T?, _ field: UploadField) throws {
            guard allowed.contains(field), let value else { return }
            try c.encode(value, forKey: field)
        }

        try put(clientSessionID, .client_session_id)
        try put(machineID, .machine_id)
        try put(contentHash, .content_hash)
        try put(clientVersion, .client_version)
        try put(sessionizerVersion, .sessionizer_version)
        try put(activeCalcVersion, .active_calc_version)
        try put(harness.rawValue, .harness)
        try put(agentObservedAt, .agent_observed_at)
        try put(clientClockOffsetMs, .client_clock_offset_ms)

        try put(startedAt, .started_at)
        try put(endedAt, .ended_at)
        try put(activeSeconds, .active_seconds)
        try put(idleSeconds, .idle_seconds)
        try put(tzOffsetMinutes, .tz_offset_minutes)
        try put(timeQuality, .time_quality)
        try put(state, .state)
        try put(endReason, .end_reason)
        try put(attendedSeconds, .attended_seconds)
        try put(autonomousSeconds, .autonomous_seconds)
        try put(presenceCount, .presence_count)
        try put(unattended, .unattended)
        try put(visible, .visible)
        try put(notable, .notable)

        try put(stripColumns, .strip_columns)
        try put(stripMarks, .strip_marks)
        try put(timelineFidelity, .timeline_fidelity)

        try put(humanPromptCount, .human_prompt_count)
        try put(promptCountBasis, .prompt_count_basis)
        try put(toolCalls, .tool_calls)
        try put(filesTouched, .files_touched)
        try put(filesCreated, .files_created)
        try put(linesAddedAgent, .lines_added_agent)
        try put(linesRemovedAgent, .lines_removed_agent)
        try put(commitCount, .commit_count)
        try put(commitInsertions, .commit_insertions)
        try put(commitDeletions, .commit_deletions)
        try put(humanEditEvents, .human_edit_events)
        try put(agentLineBucket, .agent_line_bucket)
        try put(attribConfidence, .attrib_confidence)

        try put(tokensReported, .tokens_reported)
        try putIfPresent(tokens, .tokens)
        try put(abandonedBranchTokens, .abandoned_branch_tokens)
        try put(tokenDedupe, .token_dedupe)
        try put(tokenScope, .token_scope)
        try put(tokenCoverage, .token_coverage)
        try put(models, .models)
        try put(modelState, .model_state)

        try putIfPresent(repoHash, .repo_hash)
        try put(repoPepperVersion, .repo_pepper_version)
        try put(repoIDBasis, .repo_id_basis)

        try putIfPresent(repoName, .repo_name)
        try putIfPresent(title, .title)
        try putIfPresent(titleSource, .title_source)
        try putIfPresent(cardPNGURL, .card_png_url)

        // Omitted, never null, when analysis upload is off — absence is the only honest
        // encoding of "we did not produce one". `SessionAnalysis` is Codable through its
        // own generated CodingKeys, and its `generated_at` rides the same ISO-8601 strategy
        // as every other date in this payload.
        try putIfPresent(analysis, .analysis)
    }

    /// The encoder every caller must use.
    ///
    /// ISO-8601 with fractional seconds, matching what the server's Pydantic model parses
    /// and what every harness writes. `.deferredToDate` would emit a bare double and the
    /// server would reject the whole batch.
    public static func encoder() -> JSONEncoder {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        e.outputFormatting = [.sortedKeys]
        return e
    }
}
