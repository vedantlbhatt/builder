import Foundation
import SQLite3

public struct SQLiteError: Error, CustomStringConvertible {
    public let code: Int32
    public let message: String
    public let sql: String?

    public var description: String {
        let base = "sqlite error \(code): \(message)"
        return sql.map { "\(base)\n  sql: \($0)" } ?? base
    }

    /// True when the failure is a constraint violation. The partial unique index on token
    /// usage relies on this being distinguishable from a real error.
    public var isConstraintViolation: Bool { code == SQLITE_CONSTRAINT || (code & 0xFF) == SQLITE_CONSTRAINT }
}

/// Tells SQLite to copy bound text/blob data rather than retain our pointer. Without it,
/// binding a Swift String hands SQLite a pointer that Swift is free to deallocate before
/// the statement steps — which fails intermittently, under load, on large batches.
private let SQLITE_TRANSIENT = unsafeBitCast(
    -1, to: (@convention(c) (UnsafeMutableRawPointer?) -> Void).self)

/// A thin, deliberately boring wrapper over the system SQLite.
///
/// Not `Sendable`: one connection belongs to one actor or one thread. The ingest layer
/// keeps a single write connection; readers open their own.
public final class SQLiteDB {

    let handle: OpaquePointer
    public let path: String

    // MARK: - Opening

    /// Open one of Builder's OWN databases for reading and writing.
    ///
    /// `state.sqlite` is the durable, append-only store — once we ingest a day, we are the
    /// only copy of it, because Claude Code prunes at 30 days and Cursor garbage-collects
    /// message bodies at roughly two months.
    public static func open(path: String, createIfMissing: Bool = true) throws -> SQLiteDB {
        var flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_NOMUTEX
        if createIfMissing { flags |= SQLITE_OPEN_CREATE }

        if createIfMissing {
            let dir = (path as NSString).deletingLastPathComponent
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        }

        var h: OpaquePointer?
        let rc = sqlite3_open_v2(path, &h, flags, nil)
        guard rc == SQLITE_OK, let h else {
            let msg = h.map { String(cString: sqlite3_errmsg($0)) } ?? "unable to open"
            if let h { sqlite3_close_v2(h) }
            throw SQLiteError(code: rc, message: msg, sql: nil)
        }

        let db = SQLiteDB(handle: h, path: path)
        try db.exec("PRAGMA journal_mode = WAL")
        try db.exec("PRAGMA synchronous = NORMAL")
        try db.exec("PRAGMA foreign_keys = ON")
        try db.exec("PRAGMA busy_timeout = 5000")
        return db
    }

    /// Open ANOTHER APPLICATION'S database, read-only and non-destructively.
    ///
    /// Two rules here, both learned the hard way, both load-bearing:
    ///
    /// 1. `mode=ro` and **never `immutable=1`**. Cursor's `state.vscdb` runs with a live
    ///    write-ahead log — MEASURED at 5.1 MB alongside a 1.21 GB main file. `immutable=1`
    ///    tells SQLite the file cannot change, so it skips the WAL entirely and returns
    ///    *stale data with no error*. You get plausible, wrong, silently-old rows.
    ///
    /// 2. `query_only`, and close the connection immediately after each poll. Holding a
    ///    read connection open blocks the owning app from checkpointing its WAL, which
    ///    means Builder would slowly degrade the performance of the editor it is observing.
    ///
    /// MEASURED: roughly 1% of Cursor's per-workspace databases fail to open at all (one
    /// returned `unable to open database file (14)`), so callers must handle a throw here
    /// as "skip this source", never as a fatal error.
    public static func openForeignReadOnly(path: String) throws -> SQLiteDB {
        let uri = "file:\(path.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? path)?mode=ro"
        var h: OpaquePointer?
        let rc = sqlite3_open_v2(uri, &h, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI | SQLITE_OPEN_NOMUTEX, nil)
        guard rc == SQLITE_OK, let h else {
            let msg = h.map { String(cString: sqlite3_errmsg($0)) } ?? "unable to open"
            if let h { sqlite3_close_v2(h) }
            throw SQLiteError(code: rc, message: msg, sql: path)
        }
        let db = SQLiteDB(handle: h, path: path)
        try? db.exec("PRAGMA query_only = 1")
        try? db.exec("PRAGMA busy_timeout = 2000")
        return db
    }

    private init(handle: OpaquePointer, path: String) {
        self.handle = handle
        self.path = path
    }

    deinit { sqlite3_close_v2(handle) }

    // MARK: - Statements

    public func exec(_ sql: String) throws {
        var err: UnsafeMutablePointer<CChar>?
        let rc = sqlite3_exec(handle, sql, nil, nil, &err)
        guard rc == SQLITE_OK else {
            let msg = err.map { String(cString: $0) } ?? String(cString: sqlite3_errmsg(handle))
            sqlite3_free(err)
            throw SQLiteError(code: rc, message: msg, sql: sql)
        }
    }

    public func prepare(_ sql: String) throws -> Statement {
        var stmt: OpaquePointer?
        let rc = sqlite3_prepare_v2(handle, sql, -1, &stmt, nil)
        guard rc == SQLITE_OK, let stmt else {
            throw SQLiteError(code: rc, message: String(cString: sqlite3_errmsg(handle)), sql: sql)
        }
        return Statement(handle: stmt, db: self, sql: sql)
    }

    @discardableResult
    public func run(_ sql: String, _ params: [SQLValue] = []) throws -> Int {
        let s = try prepare(sql)
        defer { s.finalize() }
        try s.bind(params)
        try s.step()
        return Int(sqlite3_changes(handle))
    }

    public func scalarInt(_ sql: String, _ params: [SQLValue] = []) throws -> Int? {
        let s = try prepare(sql)
        defer { s.finalize() }
        try s.bind(params)
        guard try s.step() else { return nil }
        return s.int(0)
    }

    public func scalarDouble(_ sql: String, _ params: [SQLValue] = []) throws -> Double? {
        let s = try prepare(sql)
        defer { s.finalize() }
        try s.bind(params)
        guard try s.step() else { return nil }
        return s.double(0)
    }

    public func scalarText(_ sql: String, _ params: [SQLValue] = []) throws -> String? {
        let s = try prepare(sql)
        defer { s.finalize() }
        try s.bind(params)
        guard try s.step() else { return nil }
        return s.text(0)
    }

    /// Iterate rows. The `Statement` handed to the body is only valid inside it.
    public func query(_ sql: String, _ params: [SQLValue] = [], _ body: (Statement) throws -> Void) throws {
        let s = try prepare(sql)
        defer { s.finalize() }
        try s.bind(params)
        while try s.step() { try body(s) }
    }

    // MARK: - Transactions

    /// Run `body` inside a transaction, rolling back on any throw.
    ///
    /// The ingest layer commits a batch of parsed rows and that source's watermark in the
    /// SAME transaction. If they could diverge, a crash between them would either lose
    /// events forever or replay them into a unique-index violation that then blocks the
    /// source permanently.
    public func transaction<T>(_ body: () throws -> T) throws -> T {
        try exec("BEGIN IMMEDIATE")
        do {
            let result = try body()
            try exec("COMMIT")
            return result
        } catch {
            try? exec("ROLLBACK")
            throw error
        }
    }

    // MARK: - Introspection

    /// Feature-detect before querying. Cursor moved conversation headers from a blob in
    /// `ItemTable` into a first-class `composerHeaders` table at some version, and Codex
    /// version-stamps its filenames (`state_5.sqlite`), so schema shape is never assumed.
    public func tableExists(_ name: String) throws -> Bool {
        try scalarInt(
            "SELECT count(*) FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
            [.text(name)]
        ).map { $0 > 0 } ?? false
    }

    public func columnNames(of table: String) throws -> [String] {
        var out: [String] = []
        try query("PRAGMA table_info(\(table))") { s in
            if let n = s.text(1) { out.append(n) }
        }
        return out
    }

    public var userVersion: Int {
        get { (try? scalarInt("PRAGMA user_version")) .flatMap { $0 } ?? 0 }
    }

    public func setUserVersion(_ v: Int) throws {
        try exec("PRAGMA user_version = \(v)")
    }
}

public enum SQLValue: Sendable, Equatable {
    case null
    case int(Int)
    case double(Double)
    case text(String)
    case blob([UInt8])

    public static func optionalInt(_ v: Int?) -> SQLValue { v.map { .int($0) } ?? .null }
    public static func optionalDouble(_ v: Double?) -> SQLValue { v.map { .double($0) } ?? .null }
    public static func optionalText(_ v: String?) -> SQLValue { v.map { .text($0) } ?? .null }
    public static func bool(_ v: Bool) -> SQLValue { .int(v ? 1 : 0) }
}

/// A prepared statement.
///
/// This and `SQLiteDB` are the only two types in BuilderKit that wrap a raw pointer.
/// The invariant is: a `Statement` never outlives the `SQLiteDB` that made it, and neither
/// crosses a thread boundary. Everything above this layer traffics in value types.
public final class Statement {
    private let handle: OpaquePointer
    private let sql: String
    private unowned let db: SQLiteDB
    private var finalized = false

    init(handle: OpaquePointer, db: SQLiteDB, sql: String) {
        self.handle = handle
        self.db = db
        self.sql = sql
    }

    deinit { if !finalized { sqlite3_finalize(handle) } }

    public func finalize() {
        guard !finalized else { return }
        finalized = true
        sqlite3_finalize(handle)
    }

    public func bind(_ params: [SQLValue]) throws {
        for (i, p) in params.enumerated() {
            let idx = Int32(i + 1)
            let rc: Int32
            switch p {
            case .null: rc = sqlite3_bind_null(handle, idx)
            case .int(let v): rc = sqlite3_bind_int64(handle, idx, Int64(v))
            case .double(let v): rc = sqlite3_bind_double(handle, idx, v)
            case .text(let v): rc = sqlite3_bind_text(handle, idx, v, -1, SQLITE_TRANSIENT)
            case .blob(let v):
                rc = v.isEmpty
                    ? sqlite3_bind_zeroblob(handle, idx, 0)
                    : v.withUnsafeBufferPointer {
                        sqlite3_bind_blob(handle, idx, $0.baseAddress, Int32(v.count), SQLITE_TRANSIENT)
                    }
            }
            guard rc == SQLITE_OK else {
                throw SQLiteError(code: rc, message: String(cString: sqlite3_errmsg(db.handle)), sql: sql)
            }
        }
    }

    /// Advance one row. Returns `false` when the statement is done.
    @discardableResult
    public func step() throws -> Bool {
        let rc = sqlite3_step(handle)
        switch rc {
        case SQLITE_ROW: return true
        case SQLITE_DONE: return false
        default:
            throw SQLiteError(code: rc, message: String(cString: sqlite3_errmsg(db.handle)), sql: sql)
        }
    }

    public func reset() { sqlite3_reset(handle); sqlite3_clear_bindings(handle) }

    /// Rebind and execute, without re-preparing. The insert loop runs thousands of these.
    public func execute(_ params: [SQLValue]) throws {
        reset()
        try bind(params)
        try step()
    }

    // MARK: Column access

    public func isNull(_ i: Int32) -> Bool { sqlite3_column_type(handle, i) == SQLITE_NULL }

    public func int(_ i: Int32) -> Int? {
        isNull(i) ? nil : Int(sqlite3_column_int64(handle, i))
    }

    public func double(_ i: Int32) -> Double? {
        isNull(i) ? nil : sqlite3_column_double(handle, i)
    }

    public func text(_ i: Int32) -> String? {
        guard let c = sqlite3_column_text(handle, i) else { return nil }
        return String(cString: c)
    }

    public func blob(_ i: Int32) -> [UInt8]? {
        guard let p = sqlite3_column_blob(handle, i) else { return nil }
        let n = Int(sqlite3_column_bytes(handle, i))
        return Array(UnsafeRawBufferPointer(start: p, count: n))
    }

    public func bool(_ i: Int32) -> Bool { (int(i) ?? 0) != 0 }

    public var columnCount: Int32 { sqlite3_column_count(handle) }

    public func columnName(_ i: Int32) -> String {
        sqlite3_column_name(handle, i).map { String(cString: $0) } ?? ""
    }
}
