/**
 * SQLite persistence for terminal sessions.
 *
 * Stores metadata plus the bounded ANSI output buffer so sessions survive a
 * backend restart. The live PTY process cannot survive the restart; on
 * re-attach the server recreates the CLI, resuming its own session file when
 * available.
 */

import { Database } from 'bun:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

export interface PersistedSession {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  output: string;
  started: boolean;
}

export class SessionStore {
  private readonly db: Database;

  constructor(path: string) {
    mkdirSync(dirname(path), { recursive: true });
    this.db = new Database(path);
    this.db.run('PRAGMA journal_mode = WAL;');
    this.db.run(`
      CREATE TABLE IF NOT EXISTS terminal_sessions (
        id         TEXT PRIMARY KEY,
        title      TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        output     TEXT NOT NULL DEFAULT '',
        started    INTEGER NOT NULL DEFAULT 1
      );
    `);
  }

  loadAll(): PersistedSession[] {
    const rows = this.db
      .query(
        `SELECT id, title, created_at, updated_at, output, started
           FROM terminal_sessions
          ORDER BY updated_at DESC`,
      )
      .all() as Array<{
      id: string;
      title: string;
      created_at: string;
      updated_at: string;
      output: string;
      started: number;
    }>;
    return rows.map((row) => ({
      id: row.id,
      title: row.title,
      created_at: row.created_at,
      updated_at: row.updated_at,
      output: row.output,
      started: Boolean(row.started),
    }));
  }

  insert(session: PersistedSession): void {
    this.db
      .query(
        `INSERT OR REPLACE INTO terminal_sessions
           (id, title, created_at, updated_at, output, started)
         VALUES (?, ?, ?, ?, ?, ?)`,
      )
      .run(
        session.id,
        session.title,
        session.created_at,
        session.updated_at,
        session.output,
        session.started ? 1 : 0,
      );
  }

  saveOutput(id: string, output: string, updatedAt: string): void {
    this.db
      .query(
        'UPDATE terminal_sessions SET output = ?, updated_at = ? WHERE id = ?',
      )
      .run(output, updatedAt, id);
  }

  markStarted(id: string): void {
    this.db
      .query('UPDATE terminal_sessions SET started = 1 WHERE id = ?')
      .run(id);
  }

  rename(id: string, title: string): void {
    this.db
      .query('UPDATE terminal_sessions SET title = ? WHERE id = ?')
      .run(title, id);
  }

  delete(id: string): void {
    this.db.query('DELETE FROM terminal_sessions WHERE id = ?').run(id);
  }

  deleteAll(): void {
    this.db.query('DELETE FROM terminal_sessions').run();
  }

  close(): void {
    this.db.close();
  }
}
