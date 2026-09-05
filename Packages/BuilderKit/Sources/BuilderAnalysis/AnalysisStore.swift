import BuilderModel
import BuilderSQLite
import Foundation

/// One row of `state.session_analysis`: a stored `SessionAnalysis` plus what it cost and
/// which digest it was read from.
///
/// Tier A on purpose. MEASURED: $0.33 and 150 s per session on sonnet, so this is the one
/// derived thing that is NOT free to recompute, and a cache rebuild must leave it alone.
public struct AnalysisRecord: Sendable, Equatable {
    public var clientSessionID: String
    public var analysisVersion: Int
    public var digestHash: String
    public var digestCoverage: Double
    public var model: String?
    /// Unix seconds; the same instant as `SessionAnalysis.generatedAt` inside `body`.
    public var generatedAt: Double
    public var costUSD: Double?
    /// The `SessionAnalysis` as JSON, exactly what goes on the wire under `analysis`.
    public var body: String
    /// A live mid-run reading, taken every `Tuning.analysisCheckpointSec` during an
    /// autonomous run. Replaced by the final analysis under the same key.
    public var checkpoint: Bool
    public var createdAt: Double

    public init(
        clientSessionID: String, analysisVersion: Int, digestHash: String, digestCoverage: Double,
        model: String?, generatedAt: Double, costUSD: Double?, body: String, checkpoint: Bool,
        createdAt: Double
    ) {
        self.clientSessionID = clientSessionID
        self.analysisVersion = analysisVersion
        self.digestHash = digestHash
        self.digestCoverage = digestCoverage
        self.model = model
        self.generatedAt = generatedAt
        self.costUSD = costUSD
        self.body = body
        self.checkpoint = checkpoint
        self.createdAt = createdAt
    }

    public func decoded() throws -> SessionAnalysis { try AnalysisStore.decode(body) }
}

/// Reads and writes `session_analysis`. Every caller goes through here so the JSON on
/// disk is always produced by the same encoder that the sync payload uses.
public enum AnalysisStore {

    /// `generated_at` is an ISO 8601 string inside the document, which is what the
    /// server's Pydantic model, the phone and `JSONDecoder(.iso8601)` all read.
    public static func encoder() -> JSONEncoder {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        e.outputFormatting = [.sortedKeys]
        return e
    }

    public static func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }

    public static func encode(_ analysis: SessionAnalysis) throws -> String {
        String(decoding: try encoder().encode(analysis), as: UTF8.self)
    }

    public static func decode(_ body: String) throws -> SessionAnalysis {
        try decoder().decode(SessionAnalysis.self, from: Data(body.utf8))
    }

    public static func upsert(_ r: AnalysisRecord, in db: SQLiteDB) throws {
        try db.run(
            """
            INSERT INTO session_analysis (
              client_session_id, analysis_version, digest_hash, digest_coverage, model,
              generated_at, cost_usd, body, checkpoint, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(client_session_id) DO UPDATE SET
              analysis_version = excluded.analysis_version,
              digest_hash = excluded.digest_hash,
              digest_coverage = excluded.digest_coverage,
              model = excluded.model,
              generated_at = excluded.generated_at,
              cost_usd = excluded.cost_usd,
              body = excluded.body,
              checkpoint = excluded.checkpoint,
              created_at = excluded.created_at
            """,
            [
                .text(r.clientSessionID), .int(r.analysisVersion), .text(r.digestHash),
                .double(r.digestCoverage), .optionalText(r.model), .double(r.generatedAt),
                .optionalDouble(r.costUSD), .text(r.body), .bool(r.checkpoint), .double(r.createdAt),
            ])
    }

    public static func record(for clientSessionID: String, in db: SQLiteDB) throws -> AnalysisRecord? {
        var found: AnalysisRecord?
        try db.query(
            "\(selectSQL) WHERE client_session_id = ?", [.text(clientSessionID)]
        ) { s in found = read(s) }
        return found
    }

    /// Every stored analysis, keyed by session. Sync reads this once per pass.
    public static func all(in db: SQLiteDB) throws -> [String: AnalysisRecord] {
        var out: [String: AnalysisRecord] = [:]
        try db.query(selectSQL) { s in
            let r = read(s)
            out[r.clientSessionID] = r
        }
        return out
    }

    /// Just enough to schedule with: no bodies, which are ~10 KB each and would otherwise
    /// be re-read on every 30-second tick.
    public static func index(in db: SQLiteDB) throws -> [String: (checkpoint: Bool, generatedAt: Double)] {
        var out: [String: (checkpoint: Bool, generatedAt: Double)] = [:]
        try db.query("SELECT client_session_id, checkpoint, generated_at FROM session_analysis") { s in
            if let id = s.text(0) { out[id] = (s.bool(1), s.double(2) ?? 0) }
        }
        return out
    }

    private static let selectSQL = """
        SELECT client_session_id, analysis_version, digest_hash, digest_coverage, model,
               generated_at, cost_usd, body, checkpoint, created_at
        FROM session_analysis
        """

    private static func read(_ s: Statement) -> AnalysisRecord {
        AnalysisRecord(
            clientSessionID: s.text(0) ?? "",
            analysisVersion: s.int(1) ?? 0,
            digestHash: s.text(2) ?? "",
            digestCoverage: s.double(3) ?? 1,
            model: s.text(4),
            generatedAt: s.double(5) ?? 0,
            costUSD: s.isNull(6) ? nil : s.double(6),
            body: s.text(7) ?? "",
            checkpoint: s.bool(8),
            createdAt: s.double(9) ?? 0)
    }
}

/// The knobs, in one place, with the same defaults as the Python runner.
public enum AnalysisSettings {

    /// `BUILDER_ANALYSIS_MODEL` wins, then the `BuilderAnalysisModel` default, then
    /// `sonnet` — the model docs/analysis.md's numbers were measured on.
    public static func model() -> String {
        if let m = ProcessInfo.processInfo.environment["BUILDER_ANALYSIS_MODEL"], !m.isEmpty { return m }
        if let m = UserDefaults.standard.string(forKey: "BuilderAnalysisModel"), !m.isEmpty { return m }
        return "sonnet"
    }

    /// Whether a stored analysis rides along on the upload. Default ON: it is private to
    /// the account under RLS and leaves it only when the session is shared. `BuilderAnalysisUpload`
    /// (UserDefaults) or `BUILDER_ANALYSIS_UPLOAD=0` turns it off; the field is then
    /// omitted from the wire rather than sent as null.
    public static func uploadEnabled() -> Bool {
        if ProcessInfo.processInfo.environment["BUILDER_ANALYSIS_UPLOAD"] == "0" { return false }
        if let v = UserDefaults.standard.object(forKey: "BuilderAnalysisUpload") as? Bool { return v }
        return true
    }

    /// Whether the agent runs `claude -p` at all. `BUILDER_ANALYSIS=0` or the
    /// `BuilderAnalysis` default set to false stops every automatic call — a dev box
    /// running `builder watch` against a copied store should not bill anyone.
    public static func runnerEnabled() -> Bool {
        if ProcessInfo.processInfo.environment["BUILDER_ANALYSIS"] == "0" { return false }
        if let v = UserDefaults.standard.object(forKey: "BuilderAnalysis") as? Bool { return v }
        return true
    }
}
