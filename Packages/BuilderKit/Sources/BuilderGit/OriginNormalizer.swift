import BuilderModel
import Foundation

/// Turns the many spellings of a git remote into one canonical string.
///
/// Every one of these forms appears in the wild for the same repository:
///
///     https://github.com/VedantLBhatt/gt-transit.git
///     git@github.com:vedantlbhatt/gt-transit.git
///     ssh://git@github.com/vedantlbhatt/gt-transit
///     https://token@github.com:443/vedantlbhatt/gt-transit.git/
///
/// They must all collapse to `github.com/vedantlbhatt/gt-transit`, because the hash of
/// this string is a repository's identity — and two spellings hashing differently would
/// split one project into two on the profile.
public enum OriginNormalizer {

    public static func normalize(_ raw: String?) -> String? {
        guard var s = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { return nil }

        // scp-like syntax: git@host:path -> host/path. Do this before scheme handling,
        // because `git@github.com:vedantlbhatt/x` has no `://` to key off.
        if !s.contains("://"), let at = s.firstIndex(of: "@"), let colon = s.firstIndex(of: ":"),
           at < colon {
            let host = String(s[s.index(after: at)..<colon])
            let path = String(s[s.index(after: colon)...])
            s = "\(host)/\(path)"
        } else {
            // Strip the scheme.
            if let range = s.range(of: "://") { s = String(s[range.upperBound...]) }
            // Strip credentials (`user:token@host`). A token in a remote URL is not
            // identity, and it must never reach a hash that gets uploaded.
            if let at = s.firstIndex(of: "@") { s = String(s[s.index(after: at)...]) }
        }

        // Strip an explicit port: `github.com:443/owner/repo`.
        if let colon = s.firstIndex(of: ":"),
           let slash = s.firstIndex(of: "/"),
           colon < slash,
           s[s.index(after: colon)..<slash].allSatisfy(\.isNumber) {
            s.removeSubrange(colon..<slash)
        }

        while s.hasSuffix("/") { s.removeLast() }
        if s.hasSuffix(".git") { s.removeLast(4) }
        while s.hasSuffix("/") { s.removeLast() }

        guard let firstSlash = s.firstIndex(of: "/") else { return s.lowercased() }
        let host = String(s[s.startIndex..<firstSlash]).lowercased()
        var path = String(s[s.index(after: firstSlash)...])

        // Hosts are always case-insensitive. Paths are not, in general — but the three
        // big forges treat them case-insensitively, and users type them inconsistently.
        // Lowercasing everywhere would merge two genuinely distinct repos on a
        // case-sensitive self-hosted server.
        if ["github.com", "gitlab.com", "bitbucket.org"].contains(host) {
            path = path.lowercased()
        }

        return path.isEmpty ? host : "\(host)/\(path)"
    }

    /// Display name: the last path component of the normalized origin.
    ///
    /// Deliberately NOT the directory name. On the reference machine the flagship project
    /// lives in a folder called `RideGT` while its origin is `.../gt-transit`, so the
    /// folder name would label the project wrongly on every card and every project arc.
    public static func displayName(fromNormalizedOrigin origin: String?) -> String? {
        guard let origin, let last = origin.split(separator: "/").last else { return nil }
        return String(last)
    }
}

/// The identity of a repository, and the basis it was derived from.
public struct RepoIdentity: Sendable, Equatable {
    public enum Basis: String, Sendable {
        case origin
        case rootCommit = "root_commit"
    }

    public let identity: String
    public let basis: Basis
    public let displayName: String?
    /// The shared git directory. All worktrees of one repo agree on this.
    public let commonRoot: String?

    public var hash: String { RepoHasher.hash(identity: identity) ?? "" }
}

public enum RepoHasher {

    /// HMAC of the identity string under a GLOBAL pepper.
    ///
    /// The pepper is not secret and cannot be: matching the same repository across two
    /// machines requires both to derive the same hash, so it ships inside an open-source
    /// binary. `PRIVACY.md` says exactly this, including that the hash does not survive a
    /// dictionary attack on public repository names, and that `excluded` is the answer
    /// for anything sensitive.
    ///
    /// Full 64 hex, never truncated: one design sliced to 32 characters while the server
    /// enforced a 64-hex pattern, which would have rejected every payload carrying a repo.
    public static func hash(identity: String?) -> String? {
        guard let identity, !identity.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return Hashing.hmacSHA256Hex(
            key: Tuning.repoPepper,
            message: Tuning.repoHashPrefix + identity
        )
    }
}
