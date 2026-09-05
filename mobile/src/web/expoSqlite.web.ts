/**
 * Web stand-in for `expo-sqlite`, resolved ONLY when Metro bundles for `platform === 'web'`
 * (see `metro.config.js`). Native builds import the real module and never see this file.
 *
 * `src/data/cache.ts` is the only caller. It opens one database and runs a small, fixed
 * vocabulary of SQL over three key/value-ish tables (`sessions`, `profile`, `kv`):
 *
 *   SELECT <cols> FROM <t> [WHERE <col> = <lit|?>] [ORDER BY <col> DESC] [LIMIT ?]
 *   INSERT INTO <t> (<cols>) VALUES (<lit|?>, …) [ON CONFLICT(<pk>) DO UPDATE SET …]
 *   DELETE FROM <t> [WHERE <col> = ?]
 *   PRAGMA … / CREATE TABLE … / CREATE INDEX … / ALTER TABLE … ADD COLUMN …
 *
 * This file executes exactly that subset in memory, keyed on each table's primary key, and
 * THROWS on any statement outside it. The cache wraps every call in `guarded`, which logs
 * the failure once and degrades to "nothing cached" — a loud miss rather than a plausible
 * empty result set. It is a page-lifetime cache: the app re-syncs from the server on every
 * load, which is what the SQLite file buys a phone between launches and what a browser tab
 * does not need.
 */

type Param = string | number | null;
type Row = Record<string, Param>;

const PRIMARY_KEY: Record<string, string> = { sessions: 'id', profile: 'k', kv: 'k' };

class Table {
  readonly rows = new Map<string, Row>();
  constructor(readonly pk: string) {}
}

function unsupported(sql: string): never {
  throw new Error(`[expoSqlite.web] unsupported statement: ${sql.trim().slice(0, 80)}`);
}

/** Split a comma list that contains no nested parentheses, trimming each item. */
function list(s: string): string[] {
  return s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);
}

/** A literal in the statement text ('me', 42) or the next bound parameter for `?`. */
function value(token: string, params: Param[], cursor: { i: number }): Param {
  if (token === '?') {
    if (cursor.i >= params.length) throw new Error('[expoSqlite.web] too few parameters');
    return params[cursor.i++] ?? null;
  }
  const m = /^'(.*)'$/s.exec(token);
  if (m) return m[1]!.replace(/''/g, "'");
  if (/^-?\d+(\.\d+)?$/.test(token)) return Number(token);
  if (token.toUpperCase() === 'NULL') return null;
  return unsupported(token);
}

class MemoryDatabase {
  private readonly tables = new Map<string, Table>();

  private table(name: string): Table {
    const key = name.toLowerCase();
    let t = this.tables.get(key);
    if (!t) {
      const pk = PRIMARY_KEY[key];
      if (!pk) unsupported(`table ${name}`);
      t = new Table(pk);
      this.tables.set(key, t);
    }
    return t;
  }

  async execAsync(sql: string): Promise<void> {
    for (const stmt of sql.split(';')) {
      const s = stmt.trim();
      if (!s) continue;
      const head = s.split(/\s+/, 2)[0]!.toUpperCase();
      if (head === 'PRAGMA' || head === 'CREATE' || head === 'ALTER') continue;
      if (head === 'DELETE') {
        this.run(s, []);
        continue;
      }
      unsupported(s);
    }
  }

  async runAsync(sql: string, ...params: Param[]): Promise<{ changes: number }> {
    return { changes: this.run(sql, params) };
  }

  async getAllAsync<T>(sql: string, ...params: Param[]): Promise<T[]> {
    return this.select(sql, params) as T[];
  }

  async getFirstAsync<T>(sql: string, ...params: Param[]): Promise<T | null> {
    const rows = this.select(sql, params) as T[];
    return rows.length ? rows[0]! : null;
  }

  // ---------------------------------------------------------------- statements

  private run(sql: string, params: Param[]): number {
    const s = sql.trim().replace(/\s+/g, ' ');
    const cursor = { i: 0 };

    const ins =
      /^INSERT INTO (\w+) \(([^)]*)\) VALUES \(([^)]*)\)(?: ON CONFLICT\((\w+)\) DO UPDATE SET (.+))?$/i.exec(
        s
      );
    if (ins) {
      const t = this.table(ins[1]!);
      const cols = list(ins[2]!);
      const vals = list(ins[3]!).map((tok) => value(tok, params, cursor));
      if (cols.length !== vals.length) unsupported(s);
      if (ins[4] !== undefined && ins[4].toLowerCase() !== t.pk) unsupported(s);
      const row: Row = {};
      cols.forEach((c, i) => {
        row[c] = vals[i] ?? null;
      });
      const key = String(row[t.pk]);
      if (t.rows.has(key) && ins[4] === undefined) {
        throw new Error(`[expoSqlite.web] UNIQUE constraint failed: ${ins[1]}.${t.pk}`);
      }
      // ON CONFLICT … SET col = excluded.col for every listed column: a plain replace of
      // the named columns, which is what every upsert in cache.ts spells out.
      const existing = t.rows.get(key);
      t.rows.set(key, existing ? { ...existing, ...row } : row);
      return 1;
    }

    const del = /^DELETE FROM (\w+)(?: WHERE (\w+) = (\S+))?$/i.exec(s);
    if (del) {
      const t = this.table(del[1]!);
      if (del[2] === undefined) {
        const n = t.rows.size;
        t.rows.clear();
        return n;
      }
      const col = del[2];
      const v = value(del[3]!, params, cursor);
      let n = 0;
      for (const [k, row] of t.rows) {
        if (row[col] === v) {
          t.rows.delete(k);
          n += 1;
        }
      }
      return n;
    }

    return unsupported(s);
  }

  private select(sql: string, params: Param[]): Row[] {
    const s = sql.trim().replace(/\s+/g, ' ');
    const cursor = { i: 0 };
    const m =
      /^SELECT (.+?) FROM (\w+)(?: WHERE (\w+) = (\S+))?(?: ORDER BY (\w+) (ASC|DESC))?(?: LIMIT (\S+))?$/i.exec(
        s
      );
    if (!m) return unsupported(s);
    const t = this.table(m[2]!);
    const cols = list(m[1]!);
    let rows = [...t.rows.values()];
    if (m[3] !== undefined) {
      const col = m[3];
      const v = value(m[4]!, params, cursor);
      rows = rows.filter((r) => r[col] === v);
    }
    if (m[5] !== undefined) {
      const col = m[5];
      const dir = m[6]!.toUpperCase() === 'DESC' ? -1 : 1;
      rows.sort((a, b) => {
        const x = a[col] ?? '';
        const y = b[col] ?? '';
        return x < y ? -dir : x > y ? dir : 0;
      });
    }
    if (m[7] !== undefined) {
      const n = value(m[7], params, cursor);
      if (typeof n !== 'number') unsupported(s);
      rows = rows.slice(0, n);
    }
    if (cols.length === 1 && cols[0] === '*') return rows.map((r) => ({ ...r }));
    return rows.map((r) => {
      const out: Row = {};
      for (const c of cols) out[c] = r[c] ?? null;
      return out;
    });
  }
}

const open = new Map<string, MemoryDatabase>();

/** Same signature `cache.ts` calls; one in-memory database per name for the page's life. */
export function openDatabaseSync(name: string): MemoryDatabase {
  let d = open.get(name);
  if (!d) {
    d = new MemoryDatabase();
    open.set(name, d);
  }
  return d;
}

export async function openDatabaseAsync(name: string): Promise<MemoryDatabase> {
  return openDatabaseSync(name);
}

export type { MemoryDatabase as SQLiteDatabase };
