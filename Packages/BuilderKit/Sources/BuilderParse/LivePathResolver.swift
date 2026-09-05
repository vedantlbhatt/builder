import BuilderModel
import BuilderSQLite
import Foundation

/// Marks which records are on the conversation's surviving branch.
///
/// THE FINDING THIS EXISTS FOR. `parentUuid` forms a DAG, not a chain — MEASURED: 225
/// fork points on the reference corpus, created by rewinds and message edits (and, it
/// turned out later, by the harness itself: see `liveEventIDs`). When you rewind and take
/// a different path, the abandoned branch stays in the transcript file with valid
/// timestamps and *distinct* `message.id` values.
///
/// That combination defeats every obvious defence. Sorting by timestamp includes it.
/// Message-id deduplication does not touch it, because the ids really are different. So a
/// straightforward "sum the edits" counts work that was undone — and it does so most on
/// exactly the sessions where the user iterated hardest, which are the sessions they are
/// most likely to look at closely.
///
/// The split:
///
///   TOKENS  — summed over ALL records. You paid for the abandoned branch; the API
///             charged for it. Reported separately as `abandonedBranchTokens` so the card
///             can be honest about it.
///
///   LINES, FILES TOUCHED, TOOL COUNTS, STRIP SEGMENTS — `on_live_path = 1` only. An Edit
///             on an abandoned branch was applied and then rewound. Its `+` lines are not
///             in the file. Counting them inflates the denominator of the human-vs-agent
///             claim, invisibly and plausibly.
public enum LivePathResolver {

    /// One transcript record's identity, with any content-block suffix removed.
    ///
    /// A single assistant record becomes several events — `<uuid>#th0`, `<uuid>#tu1` — but
    /// `parentUuid` always refers to the bare record uuid. Walking the graph on suffixed
    /// ids means every parent lookup misses, the chain terminates at the first hop, and
    /// essentially the entire transcript is misclassified as abandoned. (Observed: 80,923
    /// of 109,820 events, and a session's whole token total reported as rewound.)
    @inline(__always)
    static func baseID(_ id: String) -> String {
        guard let hash = id.firstIndex(of: "#") else { return id }
        return String(id[id.startIndex..<hash])
    }

    /// Resolve one source in memory. Returns the set of `nativeEventID`s on the surviving
    /// path. Two facts are the whole rule — the graph is a FOREST, and a rewind is a HUMAN act:
    ///
    ///   1. Every root (no parent, or a parent not in this file) heads a tree, resolved from
    ///      its own latest leaf — latest by `ts`, then by ordinal — walked back on base ids.
    ///   2. At a fork on a resolved path, look at the child the path came through. If it is
    ///      a human presence record (`.prompt` or `.interrupt`), the human went back to this
    ///      record and continued differently: the other children are rewound. Otherwise the
    ///      HARNESS wrote the sibling and the other children are live too — resolved the same
    ///      way from their own latest leaf, so a rewind nested inside one is still caught.
    ///
    /// The single-chain walk this replaces kept one child per fork and filed five LIVE shapes
    /// as rewound. MEASURED by `scripts/measure_live_path.py` over the root transcripts and
    /// 96 sidecars on one machine — 575 records, 395 substantive, 113 tool calls, 66 usage
    /// rows from the first four shapes, against 0 genuine rewinds in the same files:
    ///   - parallel tool calls chain the assistant's blocks A -> B, and A's `tool_result` is
    ///     written as a child of A: a dead-end leaf. 39 on the largest root. If A was an
    ///     Edit, its lines vanished from the card.
    ///   - the mirror image: the turn continues from A's result, and block B plus B's result
    ///     hang off A. 6 on the largest root; B's usage row became "abandoned".
    ///   - a stop hook: the harness writes a `user` (isMeta) feedback record F, the assistant
    ///     continues from F, and a `system/stop_hook_summary` S is a second child of F at the
    ///     same instant. The NEXT prompt attaches to S. 12 forks and 304 records on the
    ///     largest root — one branch was 32 minutes of committed work.
    ///   - a second root: two headless `-p` runs under one session id. 14 records.
    ///   - a queued message: `queue-operation` records carry a timestamp but no uuid and no
    ///     parent, so one appended after the last conversation record is the NEWEST LEAF,
    ///     the walk covers only it, and the whole session is rewound. Measured while a
    ///     message was queued on the largest root: 1,957 of 2,589 records, 411 tool calls,
    ///     283 usage rows — and since `on_live_path` is written once at ingest, whether a
    ///     session lost everything depended on the moment the scan ran.
    /// Timestamps cannot separate these from a rewind (in the mirror image the surviving
    /// child is LATER than the abandoned block, exactly as after a rewind), and nor can
    /// `message.id` or `tool_use_id` matching (the stop hook and the second root share
    /// neither). Bookkeeping records with no parent need no special case: they are roots.
    ///
    /// Records with no parent linkage anywhere in the source (Cursor's bubbles) are all live.
    /// `spec/fixtures/live_path/` pins both directions: `genuine_rewind` must still lose
    /// exactly its abandoned branch, `harness_forks` must lose nothing.
    public static func liveEventIDs(in events: [NormalizedEvent]) -> Set<String> {
        var byID: [String: NormalizedEvent] = [:]
        var children: [String: [NormalizedEvent]] = [:]
        var humanBases = Set<String>()
        var linked = 0

        for e in events {
            if e.nativeParentID != nil { linked += 1 }
            guard let id = e.nativeEventID else { continue }
            let base = baseID(id)
            // Index on the BASE id, and keep the first event for each record so the graph
            // has one node per transcript record rather than one per content block.
            if byID[base] == nil {
                byID[base] = e
                if let p = e.nativeParentID { children[baseID(p), default: []].append(e) }
            }
            if e.kind == .prompt || e.kind == .interrupt { humanBases.insert(base) }
        }

        // No DAG in this source (Cursor's bubbles, for instance). Everything is live.
        guard linked > 0 else {
            return Set(events.compactMap(\.nativeEventID))
        }

        // Latest by timestamp, falling back to file order. Abandoned branches' leaves are
        // older, because you rewound away from them and kept going.
        func latest(_ candidates: [NormalizedEvent]) -> NormalizedEvent? {
            candidates.max { a, b in
                let at = a.ts ?? -.infinity
                let bt = b.ts ?? -.infinity
                return at == bt ? a.ordinal < b.ordinal : at < bt
            }
        }

        var liveBases = Set<String>()

        /// The latest leaf under `root`, ignoring anything already resolved.
        func latestLeaf(under root: NormalizedEvent) -> NormalizedEvent? {
            var stack = [root]
            var seen = Set<String>()
            var leaves: [NormalizedEvent] = []
            while let node = stack.popLast() {
                guard let id = node.nativeEventID else { continue }
                let base = baseID(id)
                if seen.contains(base) || liveBases.contains(base) { continue }
                seen.insert(base)
                let kids = children[base] ?? []
                if kids.isEmpty { leaves.append(node) } else { stack.append(contentsOf: kids) }
            }
            return latest(leaves)
        }

        /// Walk from `leaf` towards the root on base ids, marking live. Stops after `stopAt`
        /// (a rescued subtree's root), at the tree's root, or at anything already resolved.
        /// Returns the path, leaf first.
        func walk(from leaf: NormalizedEvent, stopAt: String?) -> [NormalizedEvent] {
            var path: [NormalizedEvent] = []
            var cursor: NormalizedEvent? = leaf
            var guardrail = events.count + 1  // a malformed cycle must not hang the parse
            while let node = cursor, guardrail > 0 {
                guardrail -= 1
                guard let id = node.nativeEventID else { break }
                let base = baseID(id)
                if !liveBases.insert(base).inserted { break }  // cycle, or already resolved
                path.append(node)
                if base == stopAt { break }
                if let p = node.nativeParentID { cursor = byID[baseID(p)] } else { cursor = nil }
            }
            return path
        }

        // (where the walk stops, the leaf to walk from): one entry per tree of the forest,
        // in file order, then one per sibling subtree the fork rule rescues.
        var pending: [(stopAt: String?, leaf: NormalizedEvent)] = []
        var seenRoots = Set<String>()
        for e in events {
            guard let id = e.nativeEventID else { continue }
            let base = baseID(id)
            guard seenRoots.insert(base).inserted, let node = byID[base] else { continue }
            let isRoot = node.nativeParentID.map { byID[baseID($0)] == nil } ?? true
            guard isRoot, let leaf = latestLeaf(under: node) else { continue }
            pending.append((stopAt: nil, leaf: leaf))
        }

        while let item = pending.popLast() {
            let path = walk(from: item.leaf, stopAt: item.stopAt)
            // path[i] is a fork candidate; path[i - 1] is the child the path came through.
            for i in stride(from: 1, to: path.count, by: 1) {
                guard let forkID = path[i].nativeEventID,
                      let belowID = path[i - 1].nativeEventID else { continue }
                let others = (children[baseID(forkID)] ?? []).filter { k in
                    k.nativeEventID.map { !liveBases.contains(baseID($0)) } ?? false
                }
                if others.isEmpty { continue }
                // The human went back to this record and continued in a new direction:
                // every other child is a rewound branch. Anything else forking here was
                // written by the harness, and its siblings are live.
                if humanBases.contains(baseID(belowID)) { continue }
                for k in others {
                    guard let kid = k.nativeEventID, let leaf = latestLeaf(under: k) else { continue }
                    pending.append((stopAt: baseID(kid), leaf: leaf))
                }
            }
        }

        // Expand back to per-block ids: if a record is live, so are all of its blocks.
        var live = Set<String>()
        for e in events {
            guard let id = e.nativeEventID else { continue }
            if liveBases.contains(baseID(id)) { live.insert(id) }
        }
        return live
    }

    /// Persist `on_live_path` for one source.
    public static func mark(sourceID: String, events: [NormalizedEvent], db: SQLiteDB) throws {
        let live = liveEventIDs(in: events)
        let stmt = try db.prepare("UPDATE raw_event SET on_live_path = ? WHERE event_uid = ?")
        defer { stmt.finalize() }
        for e in events {
            let isLive = e.nativeEventID.map { live.contains($0) } ?? true
            try stmt.execute([.int(isLive ? 1 : 0), .text(e.eventUID)])
        }
    }
}
