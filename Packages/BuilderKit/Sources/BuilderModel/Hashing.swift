import CryptoKit
import Foundation

/// The hashes Builder relies on. All of them are identity or integrity, never secrecy —
/// the one place a key is involved is repo hashing, and `PRIVACY.md` states plainly that
/// its pepper is global and not secret.
public enum Hashing {

    public static func sha256Hex(_ s: String) -> String {
        sha256Hex(Data(s.utf8))
    }

    public static func sha256Hex(_ d: Data) -> String {
        SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined()
    }

    public static func hmacSHA256Hex(key: [UInt8], message: String) -> String {
        let mac = HMAC<SHA256>.authenticationCode(for: Data(message.utf8), using: SymmetricKey(data: key))
        return mac.map { String(format: "%02x", $0) }.joined()
    }

    /// Hash of the first `bytes` of a file, for watermark validation.
    ///
    /// Catches an in-place rewrite that happens to produce the same file length — which
    /// `size + mtime` cannot see, and which would otherwise make the reader resume at a
    /// byte offset that now lands in the middle of unrelated content.
    public static func headSHA256(path: String, bytes: Int) -> String? {
        guard let h = FileHandle(forReadingAtPath: path) else { return nil }
        defer { try? h.close() }
        guard let d = try? h.read(upToCount: bytes), !d.isEmpty else { return nil }
        return sha256Hex(d)
    }

    /// Stable identity for one parseable source.
    ///
    /// Deliberately built from a canonical descriptor rather than the raw path, so that
    /// moving a file does not orphan everything parsed from it.
    public static func sourceID(harness: Harness, descriptor: String) -> String {
        sha256Hex("builder-source-v1|\(harness.rawValue)|\(descriptor)")
    }

    /// Stable identity for one event.
    ///
    /// NOTE the absence of the ordinal. Including it would mean a `parserVersion` bump
    /// shifts every uid in a file: `INSERT OR IGNORE` would stop suppressing rows already
    /// present, the partial unique index on token usage would fire, and — because the
    /// watermark commits in the same transaction as the batch — that source would abort
    /// on every subsequent run and never advance again.
    public static func eventUID(harness: Harness, sourceID: String, nativeEventID: String) -> String {
        sha256Hex("\(harness.rawValue)|\(sourceID)|\(nativeEventID)")
    }

    /// Stable identity for one build session.
    ///
    /// No timestamp: a late-arriving earlier event moves `startedAt` backwards, and every
    /// stored reference to the session would break. No ordinal, for the reason above. No
    /// repo, because `.cwd` varies within a single file. `machineID` is included so that
    /// a future cross-machine merge has distinguishable inputs to union rather than
    /// colliding ids to reconcile.
    public static func clientSessionID(harness: Harness, machineID: String, firstEventUID: String) -> String {
        sha256Hex("builder-session-v1|\(harness.rawValue)|\(machineID)|\(firstEventUID)")
    }

    /// Opaque, stable identifier for this Mac. The platform UUID is hashed rather than
    /// sent, because the raw value is a hardware identifier.
    public static func machineID(platformUUID: String) -> String {
        sha256Hex("builder-machine-v1|\(platformUUID)")
    }
}
