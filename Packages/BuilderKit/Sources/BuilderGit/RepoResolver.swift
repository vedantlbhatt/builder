import BuilderModel
import Foundation

/// Runs `git` and maps a working directory to a stable repository identity.
///
/// Two rules here are load-bearing and were both learned from the reference machine:
///
/// 1. **`--git-common-dir`, never `--show-toplevel`.** Six of thirteen Claude Code project
///    directories on that machine are worktrees of a single repository. `--show-toplevel`
///    returns the worktree root, so one project fragments into seven separate project
///    arcs, seven separate sets of records, and seven entries in the repo privacy list.
///    `--git-common-dir` returns the shared `.git` directory, which every worktree agrees
///    on.
///
/// 2. **Arguments as an array, and `--` before any pathspec.** Claude Code project
///    directory names literally begin with `-`, which any shell — and `git` itself —
///    reads as a flag.
public struct GitEnricher: Sendable {

    public init() {}

    // MARK: - Running git

    @discardableResult
    static func run(_ arguments: [String], cwd: String?) -> (out: String, status: Int32)? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["git"] + arguments
        if let cwd { p.currentDirectoryURL = URL(fileURLWithPath: cwd) }

        let outPipe = Pipe()
        let errPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = errPipe

        do { try p.run() } catch { return nil }

        // Read before waiting: a pipe buffer that fills while we wait deadlocks the child.
        let data = outPipe.fileHandleForReading.readDataToEndOfFile()
        _ = errPipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()

        let text = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        return (text, p.terminationStatus)
    }

    // MARK: - Identity

    /// Resolve a working directory to a repository identity.
    ///
    /// Falls back from origin URL to the root commit SHA, and only then gives up. The root
    /// commit is deliberately the fallback rather than a path hash: it is stable across
    /// clones AND across machines, which is the property that would let two people be
    /// recognised as working in the same repository. A device-salted path fragments the
    /// same repo across a user's own two Macs; an unsalted path hash under a global pepper
    /// makes any two people with `~/projects/api` look like collaborators.
    public func identity(forWorkingDirectory cwd: String) -> RepoIdentity? {
        guard FileManager.default.fileExists(atPath: cwd) else { return nil }

        guard let inside = Self.run(["rev-parse", "--is-inside-work-tree"], cwd: cwd),
              inside.status == 0, inside.out == "true"
        else { return nil }

        // The shared git dir. Worktrees of one repository all report the same value.
        var commonRoot: String?
        if let r = Self.run(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd: cwd),
           r.status == 0, !r.out.isEmpty {
            commonRoot = (r.out as NSString).deletingLastPathComponent
        }

        if let origin = Self.run(["config", "--get", "remote.origin.url"], cwd: cwd),
           origin.status == 0,
           let norm = OriginNormalizer.normalize(origin.out) {
            return RepoIdentity(
                identity: norm,
                basis: .origin,
                displayName: OriginNormalizer.displayName(fromNormalizedOrigin: norm),
                commonRoot: commonRoot
            )
        }

        // No remote: a local-only repository. The first commit identifies it.
        if let root = Self.run(["rev-list", "--max-parents=0", "HEAD"], cwd: cwd),
           root.status == 0, let first = root.out.split(separator: "\n").first {
            let sha = String(first)
            return RepoIdentity(
                identity: "localroot:\(sha)",
                basis: .rootCommit,
                displayName: commonRoot.map { ($0 as NSString).lastPathComponent },
                commonRoot: commonRoot
            )
        }

        return nil
    }

    // MARK: - Window statistics

    public struct WindowStats: Sendable, Equatable {
        public var commits: Int
        public var insertions: Int
        public var deletions: Int
        public var filesChanged: Int

        public init(commits: Int, insertions: Int, deletions: Int, filesChanged: Int) {
            self.commits = commits
            self.insertions = insertions
            self.deletions = deletions
            self.filesChanged = filesChanged
        }

        public static let zero = WindowStats(commits: 0, insertions: 0, deletions: 0, filesChanged: 0)
    }

    /// Commits and line deltas inside a session's time window.
    ///
    /// Vendored and generated files are excluded via `Tuning.gitExcludePathspecs`: a
    /// `package-lock.json` refresh adds thousands of lines that nobody wrote, and it
    /// inflates both sides of any human-versus-agent comparison.
    public func stats(cwd: String, from: Double, to: Double) -> WindowStats {
        let since = String(format: "%.0f", from)
        let until = String(format: "%.0f", to)

        var args = [
            "log",
            "--since=@\(since)",
            "--until=@\(until)",
            "--pretty=format:%H",
            "--numstat",
            "--no-merges",
            "--",
        ]
        args.append(contentsOf: Tuning.gitExcludePathspecs)

        guard let r = Self.run(args, cwd: cwd), r.status == 0, !r.out.isEmpty else {
            return .zero
        }

        var commits = 0
        var insertions = 0
        var deletions = 0
        var files = Set<String>()

        for line in r.out.split(separator: "\n", omittingEmptySubsequences: true) {
            if !line.contains("\t") {
                commits += 1
                continue
            }
            let parts = line.split(separator: "\t", maxSplits: 2, omittingEmptySubsequences: false)
            guard parts.count == 3 else { continue }
            // Binary files report "-\t-" rather than counts. Count the file, not lines.
            if let add = Int(parts[0]) { insertions += add }
            if let del = Int(parts[1]) { deletions += del }
            files.insert(String(parts[2]))
        }

        return WindowStats(
            commits: commits, insertions: insertions, deletions: deletions, filesChanged: files.count)
    }
}

/// Caches `cwd -> repo identity`, including negative results.
///
/// Resolution happens at INGEST time rather than at derive time, deliberately: worktrees
/// get deleted, and once the directory is gone `git -C <cwd> rev-parse` fails and the
/// session loses its project forever. On a cache miss the resolver falls back to the
/// longest already-resolved path prefix, which handles the extremely common case of the
/// agent working in a subdirectory of a repo it has already seen.
public final class RepoResolverCache {
    private var byPath: [String: RepoIdentity?] = [:]
    private let git: GitEnricher

    public init(git: GitEnricher = GitEnricher()) { self.git = git }

    public func identity(for cwd: String?) -> RepoIdentity? {
        guard let cwd, !cwd.isEmpty else { return nil }
        if let cached = byPath[cwd] { return cached }

        // Longest resolved prefix first: this is what stops a session from fragmenting
        // when the agent cd's into `backend/` for twenty minutes.
        var best: RepoIdentity?
        var bestLength = 0
        for (path, ident) in byPath {
            guard let ident, path.count > bestLength, cwd.hasPrefix(path + "/") else { continue }
            best = ident
            bestLength = path.count
        }

        let resolved = git.identity(forWorkingDirectory: cwd) ?? best
        byPath[cwd] = resolved
        return resolved
    }

    public var resolvedCount: Int { byPath.values.filter { $0 != nil }.count }
    public var missCount: Int { byPath.values.filter { $0 == nil }.count }

    /// Distinct repositories seen so far.
    public var repositories: [RepoIdentity] {
        var seen: [String: RepoIdentity] = [:]
        for case let ident? in byPath.values { seen[ident.identity] = ident }
        return Array(seen.values)
    }
}
