/**
 * Long-term user preferences, persisted in the same SQLite database as the
 * terminal sessions.
 *
 * Preferences are injected into the CLI's system prompt when a session is
 * spawned, so a newly created session always starts with the current set.
 * A CLI that is already running keeps the prompt it was started with; the
 * user can start a new session to pick up changes.
 */

import { Database } from 'bun:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

export interface Preference {
  id: number;
  category: string;
  content: string;
  source: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface PreferenceChanges {
  content?: string;
  category?: string;
  enabled?: boolean;
}

interface PreferenceRow {
  id: number;
  category: string;
  content: string;
  source: string;
  enabled: number;
  created_at: string;
  updated_at: string;
}

function toPreference(row: PreferenceRow): Preference {
  return {
    id: row.id,
    category: row.category,
    content: row.content,
    source: row.source,
    enabled: Boolean(row.enabled),
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

export class PreferenceStore {
  private readonly db: Database;

  constructor(path: string) {
    mkdirSync(dirname(path), { recursive: true });
    this.db = new Database(path);
    this.db.run('PRAGMA journal_mode = WAL;');
    this.db.run(`
      CREATE TABLE IF NOT EXISTS user_preferences (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        category   TEXT NOT NULL DEFAULT 'general',
        content    TEXT NOT NULL,
        source     TEXT NOT NULL DEFAULT 'manual',
        enabled    INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
    `);
  }

  list(): Preference[] {
    const rows = this.db
      .query(
        `SELECT id, category, content, source, enabled, created_at, updated_at
           FROM user_preferences
          ORDER BY id ASC`,
      )
      .all() as PreferenceRow[];
    return rows.map(toPreference);
  }

  /** The enabled preferences, in the order they were added. */
  listEnabled(): Preference[] {
    const rows = this.db
      .query(
        `SELECT id, category, content, source, enabled, created_at, updated_at
           FROM user_preferences
          WHERE enabled = 1
          ORDER BY id ASC`,
      )
      .all() as PreferenceRow[];
    return rows.map(toPreference);
  }

  get(id: number): Preference | null {
    const row = this.db
      .query(
        `SELECT id, category, content, source, enabled, created_at, updated_at
           FROM user_preferences
          WHERE id = ?`,
      )
      .get(id) as PreferenceRow | null;
    return row ? toPreference(row) : null;
  }

  create(input: { content: string; category: string }): Preference {
    const timestamp = new Date().toISOString();
    const result = this.db
      .query(
        `INSERT INTO user_preferences
           (category, content, source, enabled, created_at, updated_at)
         VALUES (?, ?, 'manual', 1, ?, ?)`,
      )
      .run(input.category, input.content, timestamp, timestamp);
    return this.get(Number(result.lastInsertRowid)) as Preference;
  }

  update(id: number, changes: PreferenceChanges): Preference | null {
    const current = this.get(id);
    if (!current) return null;
    this.db
      .query(
        `UPDATE user_preferences
            SET content = ?, category = ?, enabled = ?, updated_at = ?
          WHERE id = ?`,
      )
      .run(
        changes.content ?? current.content,
        changes.category ?? current.category,
        (changes.enabled ?? current.enabled) ? 1 : 0,
        new Date().toISOString(),
        id,
      );
    return this.get(id);
  }

  delete(id: number): boolean {
    const result = this.db
      .query('DELETE FROM user_preferences WHERE id = ?')
      .run(id);
    return result.changes > 0;
  }

  close(): void {
    this.db.close();
  }
}
