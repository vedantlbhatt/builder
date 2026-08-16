import BuilderModel
import Foundation

/// One parseable thing on disk: a transcript file, or a table inside a foreign database.
public struct SourceRef: Sendable, Equatable {
    public enum Kind: String, Sendable {
        case jsonl
        case sqlite
    }

    /// Hash of a canonical descriptor, NOT the mutable path. A file that moves is the same
    /// source; two files with the same basename in different projects are not.
    public let sourceID: String
    public let harness: Harness
    public let kind: Kind
    public let path: String

    /// True when this is a subagent sidecar rather than a root transcript.
    ///
    /// Detected by an ALLOWLIST on path shape, never a denylist on `subagents/`: a root
    /// transcript is exactly `<projectdir>/<uuid>.jsonl`, and ANYTHING under
    /// `<projectdir>/<uuid>/**` is a sidecar. The tree already contains sibling
    /// `workflows/` and `tool-results/` directories, which a `subagents/` denylist would
    /// wave through as roots — reintroducing the ~3x token overcount through a path
    /// nobody thought to grep for.
    public let isSidecar: Bool

    public init(sourceID: String, harness: Harness, kind: Kind, path: String, isSidecar: Bool = false) {
        self.sourceID = sourceID
        self.harness = harness
        self.kind = kind
        self.path = path
        self.isSidecar = isSidecar
    }
}

/// Where we stopped reading a source last time, and enough state to know whether that
/// position is still meaningful.
public struct Watermark: Sendable, Equatable {
    public var sourceID: String
    public var byteOffset: Int
    public var lineCount: Int
    public var stDev: Int?
    public var stIno: Int?
    public var sizeBytes: Int?
    public var mtime: Double?
    /// sha256 of the first 64 KiB. Catches an in-place rewrite that lands on the same
    /// byte length, which `size + mtime` alone would miss — and resuming mid-file after
    /// one of those reads garbage at a plausible offset.
    public var headSHA256: String?
    public var lastRowKey: String?
    public var parserVersion: Int
    public var bodiesMissingFirstSeenAt: Double?

    public init(
        sourceID: String,
        byteOffset: Int = 0,
        lineCount: Int = 0,
        stDev: Int? = nil,
        stIno: Int? = nil,
        sizeBytes: Int? = nil,
        mtime: Double? = nil,
        headSHA256: String? = nil,
        lastRowKey: String? = nil,
        parserVersion: Int = 1,
        bodiesMissingFirstSeenAt: Double? = nil
    ) {
        self.sourceID = sourceID
        self.byteOffset = byteOffset
        self.lineCount = lineCount
        self.stDev = stDev
        self.stIno = stIno
        self.sizeBytes = sizeBytes
        self.mtime = mtime
        self.headSHA256 = headSHA256
        self.lastRowKey = lastRowKey
        self.parserVersion = parserVersion
        self.bodiesMissingFirstSeenAt = bodiesMissingFirstSeenAt
    }

    public enum Decision: Sendable, Equatable {
        /// Nothing changed. Skip the file entirely.
        case skip
        /// Read from `offset`, keeping everything already stored.
        case resume(offset: Int, lineIndex: Int)
        /// Something invalidated the offset. Delete this source's rows and re-read.
        case restart(reason: String)
    }

    /// Decide how to treat a source given what it looks like on disk right now.
    ///
    /// The interesting cases, all of which occur in practice:
    ///
    /// - **Truncated or shrunk** — the file was rotated or rewritten shorter. The stored
    ///   offset now points past valid data.
    /// - **Same size, different content** — an in-place rewrite. Only the head hash sees
    ///   this; size and mtime can both be unchanged.
    /// - **Different inode** — replaced by rename. Same path, entirely different file.
    /// - **Parser version bumped** — our interpretation changed, so previously-stored rows
    ///   are stale even though the bytes are identical.
    public func decide(
        currentSize: Int,
        currentMtime: Double,
        currentDev: Int?,
        currentIno: Int?,
        currentHeadSHA: String?,
        parserVersion: Int
    ) -> Decision {
        if self.parserVersion != parserVersion {
            return .restart(reason: "parser_version \(self.parserVersion) -> \(parserVersion)")
        }
        if let ino = stIno, let cur = currentIno, ino != cur {
            return .restart(reason: "inode changed (file replaced)")
        }
        if let dev = stDev, let cur = currentDev, dev != cur {
            return .restart(reason: "device changed")
        }
        if currentSize < byteOffset {
            return .restart(reason: "file shrank below watermark (\(currentSize) < \(byteOffset))")
        }
        if let stored = headSHA256, let cur = currentHeadSHA, stored != cur {
            return .restart(reason: "head hash changed (in-place rewrite)")
        }
        if currentSize == byteOffset, let m = mtime, m == currentMtime {
            return .skip
        }
        if currentSize == byteOffset {
            return .skip
        }
        return .resume(offset: byteOffset, lineIndex: lineCount)
    }
}

/// Anything a parser wants to tell the operator rather than swallow.
public struct ParseDiagnostic: Sendable, Equatable {
    public let code: String
    public let detail: String
    public init(code: String, detail: String) {
        self.code = code
        self.detail = detail
    }
}

public struct ParseResult: Sendable {
    public var events: [NormalizedEvent]
    public var watermark: Watermark
    public var diagnostics: [ParseDiagnostic]
    /// Best fidelity observed for this source. Merged upward only, never downward.
    public var fidelity: TimelineFidelity

    public init(
        events: [NormalizedEvent],
        watermark: Watermark,
        diagnostics: [ParseDiagnostic] = [],
        fidelity: TimelineFidelity = .full
    ) {
        self.events = events
        self.watermark = watermark
        self.diagnostics = diagnostics
        self.fidelity = fidelity
    }
}

/// One conforming type per harness.
///
/// Adding a harness means adding a parser and an enum case. Nothing downstream branches
/// on `Harness` to decide what *happened* — only to decide what is *available*, which is
/// why `Harness.reportsTokens` exists as a property rather than as an `if` in the UI.
public protocol HarnessParser: Sendable {
    var harness: Harness { get }

    /// Bump when this parser's interpretation of the same bytes changes. Sources whose
    /// stored watermark carries an older version are deleted and re-read.
    var parserVersion: Int { get }

    /// Everything this harness has on disk right now. Must not throw for a missing root —
    /// most users do not have all four tools installed.
    func discover() throws -> [SourceRef]

    /// Parse one source from its watermark forward.
    func parse(source: SourceRef, from watermark: Watermark) throws -> ParseResult
}

public extension HarnessParser {
    /// Convenience for a first, full read.
    func parseAll(source: SourceRef) throws -> ParseResult {
        try parse(source: source, from: Watermark(sourceID: source.sourceID, parserVersion: parserVersion))
    }
}
