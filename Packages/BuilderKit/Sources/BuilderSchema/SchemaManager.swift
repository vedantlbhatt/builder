import BuilderSQLite
import Foundation

/// Where Builder keeps its two databases.
public enum StorePaths {
    public static var root: String = {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        return base.appendingPathComponent("Builder", isDirectory: true).path
    }()

    /// Durable, append-only. See the banner in state_schema.sql before touching it.
    public static var state: String { (root as NSString).appendingPathComponent("state.sqlite") }

    /// Derived, disposable, safe to delete at any time.
    public static var cache: String { (root as NSString).appendingPathComponent("cache.sqlite") }
}

public enum SchemaError: Error, CustomStringConvertible {
    case missingResource(String)
    case downgrade(found: Int, supported: Int)

    public var description: String {
        switch self {
        case .missingResource(let n):
            return "schema resource \(n) missing from the bundle"
        case .downgrade(let found, let supported):
            return """
                state.sqlite was written by a NEWER Builder (schema v\(found), this build \
                supports v\(supported)). Refusing to open it: a forward-only store opened by \
                an older binary would silently drop columns it does not know about. Update Builder.
                """
        }
    }
}

/// Opens and versions the two stores.
///
/// The asymmetry between them is the whole point:
///
///   Tier A (`state.sqlite`) is APPEND-ONLY with forward-only migrations. Builder is
///   frequently the only remaining copy of a day's history — Claude Code prunes at 30
///   days and Cursor has already garbage-collected message bodies for 433 of 482
///   conversations on the reference machine.
///
///   Tier B (`cache.sqlite`) is a pure function of Tier A plus `Tuning`, and is dropped
///   and rebuilt wholesale whenever either changes. Measured rebuild cost over the
///   reference corpus: ~5 seconds. "Rebuild index" must always be safe to press.
public enum SchemaManager {

    /// Bump when state_schema.sql gains a table or column. Migrations are forward-only
    /// and each one is additive; there is no down path, by design.
    ///
    /// 2: `live_upload` (rate limit for live-session snapshots) and `session_analysis`
    ///    (the model-written reading; Tier A because it costs money to regenerate).
    public static let stateVersion = 2

    // MARK: - Tier A

    public static func openState(path: String = StorePaths.state) throws -> SQLiteDB {
        let db = try SQLiteDB.open(path: path)
        let found = db.userVersion

        if found > stateVersion {
            throw SchemaError.downgrade(found: found, supported: stateVersion)
        }

        if found == 0 {
            try db.exec(try resource("state_schema"))
            try db.setUserVersion(stateVersion)
        } else if found < stateVersion {
            try migrateState(db, from: found)
        }

        // Re-running the base schema is safe (every statement is IF NOT EXISTS) and
        // repairs a store whose creation was interrupted between exec and setUserVersion.
        try db.exec(try resource("state_schema"))
        return db
    }

    private static func migrateState(_ db: SQLiteDB, from: Int) throws {
        // Forward-only, additive, one numbered step per version. Each step goes here as
        // `if from < N { try db.exec("ALTER TABLE ..." / "CREATE TABLE IF NOT EXISTS ...") }`
        // and NEVER as a drop-and-rebuild.
        if from < 2 {
            try db.exec(
                """
                CREATE TABLE IF NOT EXISTS live_upload (
                  client_session_id TEXT PRIMARY KEY,
                  last_uploaded_at  REAL NOT NULL,
                  content_hash      TEXT
                )
                """)
            try db.exec(
                """
                CREATE TABLE IF NOT EXISTS session_analysis (
                  client_session_id TEXT PRIMARY KEY,
                  analysis_version  INTEGER NOT NULL,
                  digest_hash       TEXT NOT NULL,
                  digest_coverage   REAL NOT NULL,
                  model             TEXT,
                  generated_at      REAL NOT NULL,
                  cost_usd          REAL,
                  body              TEXT NOT NULL,
                  checkpoint        INTEGER NOT NULL DEFAULT 0,
                  created_at        REAL NOT NULL
                )
                """)
        }
        try db.setUserVersion(stateVersion)
    }

    // MARK: - Tier B

    /// Open the derived store, rebuilding it from scratch if it is stale.
    ///
    /// Staleness is decided by `tuning_hash`, so changing a constant in `Tuning.swift`
    /// automatically invalidates every number computed under the old rules. Without that,
    /// a user who updates Builder keeps a contribution graph bucketed by the previous
    /// thresholds and has no way to notice.
    public static func openCache(
        path: String = StorePaths.cache,
        tuningVersion: String,
        forceRebuild: Bool = false
    ) throws -> (db: SQLiteDB, didRebuild: Bool) {
        let db = try SQLiteDB.open(path: path)
        try db.exec("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

        let storedTuning = try db.scalarText("SELECT value FROM meta WHERE key = 'tuning_hash'")
        let hasSession = try db.tableExists("session")
        let stale = forceRebuild || storedTuning != tuningVersion || !hasSession

        guard stale else { return (db, false) }

        try db.exec(try resource("cache_schema"))
        try db.run(
            "INSERT INTO meta(key, value) VALUES ('tuning_hash', ?) "
                + "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [.text(tuningVersion)]
        )
        try db.run(
            "INSERT INTO meta(key, value) VALUES ('built_from_rowid', '0') "
                + "ON CONFLICT(key) DO UPDATE SET value = '0'"
        )
        return (db, true)
    }

    // MARK: - Resources

    private static func resource(_ name: String) throws -> String {
        guard let url = Bundle.module.url(forResource: name, withExtension: "sql"),
              let text = try? String(contentsOf: url, encoding: .utf8)
        else { throw SchemaError.missingResource(name) }
        return text
    }
}
