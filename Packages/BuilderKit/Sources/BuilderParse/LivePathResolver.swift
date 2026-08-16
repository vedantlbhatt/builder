import BuilderModel
import BuilderSQLite
import Foundation

/// Marks which records are on the conversation's surviving branch.
///
/// THE FINDING THIS EXISTS FOR. `parentUuid` forms a DAG, not a chain — MEASURED: 225
/// fork points on the reference corpus, created by rewinds and message edits. When you
/// rewind and take a different path, the abandoned branch stays in the transcript file
/// with valid timestamps and *distinct* `message.id` values.
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

    /// Resolve one source in memory.
    ///
    /// Walks backwards from every leaf (a record no other record claims as parent) that
    /// is the newest such record, following `parentUuid ?? logicalParentUuid` to a root.
    /// Returns the set of `nativeEventID`s on the surviving path.
    ///
    /// Records with no parent linkage at all — most bookkeeping types — are treated as
    /// live, because excluding them would drop events that were never part of any branch.
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

    public static func liveEventIDs(in events: [NormalizedEvent]) -> Set<String> {
        var byID: [String: NormalizedEvent] = [:]
        var claimedAsParent = Set<String>()
        var linked = 0

        for e in events {
            // Index on the BASE id, and keep the first event for each record so the walk
            // sees one node per transcript record rather than one per content block.
            if let id = e.nativeEventID {
                let base = baseID(id)
                if byID[base] == nil { byID[base] = e }
            }
            if let p = e.nativeParentID {
                claimedAsParent.insert(baseID(p))
                linked += 1
            }
        }

        // No DAG in this source (Cursor's bubbles, for instance). Everything is live.
        guard linked > 0 else {
            return Set(events.compactMap(\.nativeEventID))
        }

        // Leaves are records nobody claims as a parent. The LIVE leaf is the latest one
        // by timestamp, falling back to file order — the abandoned branches' leaves are
        // older, because you rewound away from them and kept going.
        let leaves = byID.values.filter { e in
            guard let id = e.nativeEventID else { return false }
            return !claimedAsParent.contains(baseID(id))
        }

        guard let liveLeaf = leaves.max(by: { a, b in
            let at = a.ts ?? -.infinity
            let bt = b.ts ?? -.infinity
            return at == bt ? a.ordinal < b.ordinal : at < bt
        }) else {
            return Set(events.compactMap(\.nativeEventID))
        }

        // Walk the surviving branch back to its root, on base ids.
        var liveBases = Set<String>()
        var cursor: NormalizedEvent? = liveLeaf
        var guardrail = events.count + 1  // a malformed cycle must not hang the parse

        while let node = cursor, guardrail > 0 {
            guardrail -= 1
            guard let id = node.nativeEventID else { break }
            if !liveBases.insert(baseID(id)).inserted { break }  // cycle
            if let p = node.nativeParentID { cursor = byID[baseID(p)] } else { cursor = nil }
        }

        // Bookkeeping records sit outside the conversation graph entirely — they have no
        // parent and nothing claims them. They are not "abandoned", they were never on a
        // branch, so they stay live.
        for e in events where e.nativeParentID == nil {
            guard let id = e.nativeEventID else { continue }
            if !e.kind.isSubstantive { liveBases.insert(baseID(id)) }
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
