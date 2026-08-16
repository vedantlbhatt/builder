import Foundation

/// Type-safe navigation over `JSONSerialization` output.
///
/// This type exists because the reference corpus punishes every assumption about shape,
/// and it does so on a small fraction of records — which is the dangerous kind, since a
/// naive parser works on your test file and quietly mis-parses 0.4% of production:
///
///   - `.message.content` is a plain String on **3,299** user records, and an array of
///     blocks on the rest.
///   - `.toolUseResult` is a dictionary on 15,355 records, a **String on 401**, and a
///     **list on 3**.
///   - `Read`'s result path is `.toolUseResult.file.filePath`, not `.toolUseResult.filePath`.
///   - `Write` has `structuredPatch: []` — empty, always — while `Edit` populates it.
///
/// So: no force-casts, no `as!`, no assuming a container. Ask for what you want, get
/// `nil` if it is not that.
@dynamicMemberLookup
public struct JSONNode {
    public let raw: Any?

    public init(_ raw: Any?) { self.raw = raw }

    public subscript(dynamicMember key: String) -> JSONNode {
        JSONNode((raw as? [String: Any])?[key])
    }

    public subscript(key: String) -> JSONNode {
        JSONNode((raw as? [String: Any])?[key])
    }

    public subscript(index: Int) -> JSONNode {
        guard let arr = raw as? [Any], index >= 0, index < arr.count else { return JSONNode(nil) }
        return JSONNode(arr[index])
    }

    public var exists: Bool { raw != nil && !(raw is NSNull) }

    public var string: String? {
        if let s = raw as? String { return s }
        return nil
    }

    /// A string, but only if non-empty.
    ///
    /// Codex writes EMPTY STRINGS, not NULLs, for columns added in later schema
    /// migrations — `model` and `reasoning_effort` are `''` on rows written by older CLI
    /// versions. `NULLIF(col, '')` is the SQL equivalent; this is the Swift one.
    public var nonEmptyString: String? {
        guard let s = string, !s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return s
    }

    public var int: Int? {
        if let n = raw as? Int { return n }
        if let n = raw as? NSNumber { return n.intValue }
        if let d = raw as? Double { return Int(d) }
        if let s = raw as? String { return Int(s) }
        return nil
    }

    public var double: Double? {
        if let d = raw as? Double { return d }
        if let n = raw as? NSNumber { return n.doubleValue }
        if let s = raw as? String { return Double(s) }
        return nil
    }

    public var bool: Bool? {
        if let b = raw as? Bool { return b }
        if let n = raw as? NSNumber { return n.boolValue }
        return nil
    }

    public var array: [JSONNode]? {
        guard let arr = raw as? [Any] else { return nil }
        return arr.map(JSONNode.init)
    }

    public var object: [String: Any]? { raw as? [String: Any] }

    public var isString: Bool { raw is String }
    public var isArray: Bool { raw is [Any] }
    public var isObject: Bool { raw is [String: Any] }

    /// Content blocks, normalized across the two shapes `.message.content` takes.
    ///
    /// When content is a bare String (3,299 user records) it is reported as a single
    /// synthetic `text` block, so callers never need the special case.
    public var contentBlocks: [JSONNode] {
        if let arr = array { return arr }
        if isString { return [JSONNode(["type": "text", "text": raw as Any])] }
        return []
    }
}

public enum JSONLine {
    /// Parse one JSONL record. Returns `nil` on malformed input rather than throwing —
    /// a single bad line must never abort a 78 MB file, and the caller records a
    /// diagnostic so it is visible rather than silent.
    public static func parse(_ data: Data) -> JSONNode? {
        guard let obj = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
        else { return nil }
        return JSONNode(obj)
    }
}

public enum ISO8601 {
    /// Claude Code writes `2026-08-05T15:31:54.233Z` — always UTC, always with
    /// milliseconds, on 84,349 of 84,349 timestamped records. Cursor's bubble rows use
    /// the same shape. Both formatters are tried because Codex omits fractional seconds
    /// on some record types.
    private static let withFraction: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    /// Unix seconds, or `nil`. Never a fallback to "now" — a fabricated timestamp would
    /// silently create a session that did not happen.
    public static func seconds(_ s: String?) -> Double? {
        guard let s else { return nil }
        if let d = withFraction.date(from: s) { return d.timeIntervalSince1970 }
        if let d = plain.date(from: s) { return d.timeIntervalSince1970 }
        return nil
    }
}
