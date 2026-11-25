/**
 * PTY + WebSocket server that exposes StockAssistant CLI sessions.
 *
 * Sessions are persisted in SQLite (metadata + bounded terminal output), so the
 * sidebar keeps them across backend restarts. The live PTY cannot survive a
 * restart: on re-attach the server starts a new CLI, resuming the CLI's own
 * session file (`--resume`) when it exists, otherwise creating it with the same
 * id (`--session-id`).
 *
 * HTTP:
 *   GET    /health
 *   GET    /sessions
 *   POST   /sessions
 *   DELETE /sessions
 *   PATCH  /sessions/:id                      (rename)
 *   DELETE /sessions/:id
 *   GET    /sessions/export
 *   GET    /sessions/:id/output
 *   GET    /sessions/:id/transcript
 *   GET    /sessions/:id/transcript/stream   (SSE, live transcript)
 *   GET    /agent/preferences
 *   POST   /agent/preferences
 *   PATCH  /agent/preferences/:id
 *   DELETE /agent/preferences/:id
 *
 * WebSocket:
 *   /terminal/ws?session=<id>&cols=<n>&rows=<n>
 *   client -> server: {"type":"input","data":"..."} / {"type":"resize","cols":N,"rows":N}
 *   server -> client: session buffer replay + raw ANSI output
 */

import { join } from 'node:path';
import type { ServerWebSocket } from 'bun';
import { loadStockExtensions } from '../lib/catalog';
import { PreferenceStore, type Preference } from './preferences';
import { SessionStore } from './store';
import {
  clearTranscriptCache,
  hasCliSession,
  readTranscript,
  stripAnsi,
  type Transcript,
} from './transcript';
import { buildTerminalLaunch } from './wiring';

const serverDir = import.meta.dir;
const stockRoot = join(serverDir, '..');
const repoRoot = join(stockRoot, '..');

const host =
  process.env.INVEST_TERMINAL_HOST ??
  process.env.INVEST_SERVER_HOST ??
  '127.0.0.1';
const port = Number(
  process.env.INVEST_TERMINAL_PORT ?? process.env.INVEST_SERVER_PORT ?? 8015,
);
const dbPath =
  process.env.INVEST_TERMINAL_DB ??
  process.env.STOCK_CHECKPOINT_DB ??
  join(stockRoot, 'data', 'terminal-sessions.db');

interface PseudoTerminal {
  write(data: string): void;
  resize(cols: number, rows: number): void;
  close(): void;
}

interface TerminalOptions {
  cols: number;
  rows: number;
  data: (term: PseudoTerminal, chunk: Uint8Array) => void;
  exit: (term: PseudoTerminal, code: number) => void;
}

type TerminalConstructor = new (options: TerminalOptions) => PseudoTerminal;
const TerminalCtor = (Bun as unknown as { Terminal: TerminalConstructor })
  .Terminal;

interface Session {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  cols: number;
  rows: number;
  term: PseudoTerminal | null;
  proc: { kill(): void } | null;
  decoder: TextDecoder;
  buffer: string;
  sockets: Set<ServerWebSocket<SocketData>>;
  alive: boolean;
  started: boolean;
  saveTimer?: ReturnType<typeof setTimeout>;
  /**
   * True once the CLI has mounted its prompt and reads stdin.  Input that
   * arrives earlier is swallowed by the CLI, so it is queued instead.
   */
  ready: boolean;
  /** Tail of the CLI output, used to detect the ready marker. */
  readyProbe: string;
  /** Input received before the CLI was ready, replayed once it is. */
  pendingInput: string[];
  /** Timer that replays the queued input, one chunk at a time. */
  flushTimer?: ReturnType<typeof setTimeout>;
  /** Timer that gives up waiting for the ready marker. */
  readyTimer?: ReturnType<typeof setTimeout>;
}

interface SocketData {
  sessionId: string;
  cols: number;
  rows: number;
  /** When false the terminal buffer is not replayed to the client. */
  echo: boolean;
}

const sessions = new Map<string, Session>();
const store = new SessionStore(dbPath);
const preferenceStore = new PreferenceStore(dbPath);
const MAX_BUFFER = 200_000;
/** Upper bound for a manually entered session title. */
const MAX_TITLE_LENGTH = 80;
/** Upper bound for a stored long-term preference. */
const MAX_PREFERENCE_LENGTH = 500;
let shuttingDown = false;

/**
 * The CLI renders its input box with Ink, which turns on bracketed paste
 * mode as soon as the prompt is mounted.  That marker is the earliest sign
 * that the CLI actually reads stdin.
 */
const READY_MARKER = '\u001b[?2004h';
/** How long queued input waits for the ready marker before it is sent anyway. */
const READY_FALLBACK_MS = 15_000;
/** Gap between two replayed input chunks, so a burst is not read as a paste. */
const REPLAY_GAP_MS = 220;

const CORS_HEADERS: Record<string, string> = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

function cors(extra: Record<string, string> = {}): Record<string, string> {
  return { ...CORS_HEADERS, ...extra };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: cors({ 'Content-Type': 'application/json; charset=utf-8' }),
  });
}

/** Parses a JSON object body, or returns null when it is invalid. */
async function readJsonBody(
  request: Request,
): Promise<Record<string, unknown> | null> {
  try {
    const parsed: unknown = await request.json();
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // Not JSON.
  }
  return null;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function nowIso(): string {
  return new Date().toISOString();
}

function defaultTitle(): string {
  const now = new Date();
  const hh = String(now.getHours()).padStart(2, '0');
  const mm = String(now.getMinutes()).padStart(2, '0');
  const ss = String(now.getSeconds()).padStart(2, '0');
  return `终端会话 ${hh}:${mm}:${ss}`;
}

const PREFERENCE_CATEGORY_LABELS: Record<string, string> = {
  market: '市场',
  investment: '投资',
  style: '风格',
  profile: '个人',
  general: '其他',
};

/** Formats stored preferences as prompt lines for the CLI. */
function preferencePromptLines(items: Preference[]): string[] {
  return items.map((item) => {
    const label = PREFERENCE_CATEGORY_LABELS[item.category] ?? item.category;
    return `[${label}] ${item.content}`;
  });
}

/**
 * Renames a session after its first prompt, so the sidebar shows the question
 * instead of a timestamp.
 */
function syncSessionTitle(session: Session, transcript: Transcript): void {
  const first = transcript.turns[0]?.prompt;
  if (!first) return;
  if (!session.title.startsWith('终端会话 ')) return;
  const title = first.length > 28 ? `${first.slice(0, 28)}…` : first;
  session.title = title;
  store.rename(session.id, title);
}

/**
 * Server-sent events stream of the session transcript.
 *
 * The CLI appends to its JSONL transcript as the conversation progresses, so
 * the stream re-reads the file and pushes it whenever it changed.  The client
 * therefore sees thoughts, tool calls and the answer while they happen,
 * instead of polling for them.
 */
function transcriptEventStream(sessionId: string): Response {
  const encoder = new TextEncoder();
  let timer: ReturnType<typeof setInterval> | undefined;
  let last = '';

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const push = (): void => {
        try {
          const payload = readTranscript(sessionId);
          const session = sessions.get(sessionId);
          // The structured view only uses this stream, so the rename that
          // used to live on the polling endpoint must happen here too.
          if (session) syncSessionTitle(session, payload);
          const text = JSON.stringify({
            ...payload,
            // Whether the CLI has mounted its prompt and reads stdin yet.
            ready: session?.ready ?? false,
            alive: session?.alive ?? false,
          });
          if (text === last) return;
          last = text;
          controller.enqueue(encoder.encode(`data: ${text}\n\n`));
        } catch {
          // The session may be gone; keep the stream open until it closes.
        }
      };
      push();
      timer = setInterval(push, 600);
    },
    cancel() {
      if (timer) clearInterval(timer);
    },
  });

  return new Response(stream, {
    headers: cors({
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
    }),
  });
}

function summary(session: Session) {
  return {
    id: session.id,
    title: session.title,
    created_at: session.createdAt,
    updated_at: session.updatedAt,
    alive: session.alive && Boolean(session.proc),
    started: session.started,
  };
}

function listSessions() {
  return [...sessions.values()]
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .map(summary);
}

function scheduleSave(session: Session): void {
  if (session.saveTimer) return;
  session.saveTimer = setTimeout(() => {
    session.saveTimer = undefined;
    store.saveOutput(session.id, session.buffer, session.updatedAt);
  }, 500);
}

function appendOutput(session: Session, text: string): void {
  if (!text) return;
  session.buffer += text;
  if (session.buffer.length > MAX_BUFFER) {
    session.buffer = session.buffer.slice(-MAX_BUFFER);
  }
  session.updatedAt = nowIso();
  scheduleSave(session);
  for (const socket of session.sockets) {
    try {
      socket.send(text);
    } catch {
      // Socket closed between iteration and send.
    }
  }
}

function spawnCli(session: Session, resume: boolean): void {
  const extensions = loadStockExtensions(repoRoot);
  const launch = buildTerminalLaunch(
    repoRoot,
    stockRoot,
    extensions.catalog,
    extensions.agentBodies,
    {
      sessionId: session.id,
      resume,
      // Preferences are read once per CLI start; a new session picks up the
      // latest set, a running one keeps the prompt it was started with.
      preferences: preferencePromptLines(preferenceStore.listEnabled()),
    },
  );

  const decoder = new TextDecoder();
  session.decoder = decoder;
  session.ready = false;
  session.readyProbe = '';
  session.pendingInput = [];
  if (session.flushTimer) {
    clearTimeout(session.flushTimer);
    session.flushTimer = undefined;
  }
  if (session.readyTimer) {
    clearTimeout(session.readyTimer);
    session.readyTimer = undefined;
  }

  const term = new TerminalCtor({
    cols: session.cols,
    rows: session.rows,
    data(_term, chunk) {
      const text = decoder.decode(chunk, { stream: true });
      appendOutput(session, text);
      if (!session.ready) {
        // The marker can be split across two chunks, so keep a short tail.
        session.readyProbe = (session.readyProbe + text).slice(-64);
        if (session.readyProbe.includes(READY_MARKER)) {
          session.ready = true;
          flushPendingInput(session);
        }
      }
    },
    exit(_term, code) {
      session.alive = false;
      appendOutput(
        session,
        `\r\n\x1b[90m[StockAssistant] CLI 已退出（code=${code}）\x1b[0m\r\n`,
      );
    },
  });

  try {
    const proc = Bun.spawn({
      cmd: [process.execPath, ...launch.args],
      cwd: repoRoot,
      env: launch.env,
      terminal: term,
    } as never);
    session.term = term;
    session.proc = proc;
    session.alive = true;
    session.started = true;
    store.markStarted(session.id);
    console.log(
      `cli ${resume ? 'resume' : 'start'}: session=${session.id} pid=${proc.pid}`,
    );
  } catch (error) {
    session.term = null;
    session.proc = null;
    session.alive = false;
    const message = error instanceof Error ? error.message : String(error);
    appendOutput(
      session,
      `\r\n\x1b[31m[StockAssistant] 无法启动 CLI：${message}\x1b[0m\r\n`,
    );
  }
}

function ensureLiveSession(session: Session): void {
  if (session.term && session.proc && session.alive) return;
  const resume = session.started && hasCliSession(session.id);
  spawnCli(session, resume);
}

/**
 * Replays the input that arrived while the CLI was still starting up.
 *
 * The chunks are written one at a time with a small gap: the CLI treats a
 * burst of characters as a paste, and a paste must not swallow the Enter
 * that submits the prompt.
 */
function flushPendingInput(session: Session): void {
  if (session.flushTimer) return;
  const chunk = session.pendingInput.shift();
  if (chunk === undefined) return;
  try {
    session.term?.write(chunk);
  } catch {
    // The CLI may have exited.
  }
  if (session.pendingInput.length > 0) {
    session.flushTimer = setTimeout(() => {
      session.flushTimer = undefined;
      flushPendingInput(session);
    }, REPLAY_GAP_MS);
  }
}

/**
 * Sends input to the CLI, or queues it while the CLI is still booting.
 *
 * The CLI ignores everything that arrives before its prompt is mounted, so
 * an early keystroke would disappear without a trace.
 */
function writeInput(session: Session, data: string): void {
  if (!session.ready) {
    session.pendingInput.push(data);
    if (!session.readyTimer) {
      session.readyTimer = setTimeout(() => {
        session.readyTimer = undefined;
        if (session.ready) return;
        // The marker never showed up; send the input anyway.
        session.ready = true;
        flushPendingInput(session);
      }, READY_FALLBACK_MS);
    }
    return;
  }
  try {
    session.term?.write(data);
  } catch {
    // The CLI may have exited.
  }
}

function createSession(cols: number, rows: number, title?: string): Session {
  const id = crypto.randomUUID();
  const timestamp = nowIso();
  const session: Session = {
    id,
    title: title ?? defaultTitle(),
    createdAt: timestamp,
    updatedAt: timestamp,
    cols,
    rows,
    term: null,
    proc: null,
    decoder: new TextDecoder(),
    buffer: '',
    sockets: new Set(),
    alive: false,
    started: true,
    ready: false,
    readyProbe: '',
    pendingInput: [],
  };
  sessions.set(id, session);
  store.insert({
    id,
    title: session.title,
    created_at: timestamp,
    updated_at: timestamp,
    output: '',
    started: true,
  });
  spawnCli(session, false);
  console.log(`session created: id=${id} size=${cols}x${rows}`);
  return session;
}

function loadPersistedSessions(): void {
  const rows = store.loadAll();
  for (const row of rows) {
    sessions.set(row.id, {
      id: row.id,
      title: row.title,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
      cols: 120,
      rows: 40,
      term: null,
      proc: null,
      decoder: new TextDecoder(),
      buffer: row.output,
      sockets: new Set(),
      alive: false,
      started: row.started,
      ready: false,
      readyProbe: '',
      pendingInput: [],
    });
  }
  // Sessions created before automatic naming are still titled with a
  // timestamp. Adopt their first prompt now so the sidebar loads with names.
  for (const session of sessions.values()) {
    if (!session.title.startsWith('终端会话 ')) continue;
    try {
      syncSessionTitle(session, readTranscript(session.id));
    } catch {
      // An unreadable transcript keeps the default title.
    }
  }
  console.log(`sessions loaded: ${rows.length}`);
}

/** Applies a manual rename, both in memory and in SQLite. */
function renameSession(session: Session, title: string): void {
  session.title = title;
  session.updatedAt = nowIso();
  store.rename(session.id, title);
}

function deleteSession(id: string): boolean {
  const session = sessions.get(id);
  if (!session) return false;
  if (session.saveTimer) {
    clearTimeout(session.saveTimer);
    session.saveTimer = undefined;
  }
  if (session.flushTimer) {
    clearTimeout(session.flushTimer);
    session.flushTimer = undefined;
  }
  if (session.readyTimer) {
    clearTimeout(session.readyTimer);
    session.readyTimer = undefined;
  }
  try {
    session.proc?.kill();
  } catch {
    // Already gone.
  }
  try {
    session.term?.close();
  } catch {
    // Already closed.
  }
  for (const socket of [...session.sockets]) {
    try {
      socket.close(4001, 'session deleted');
    } catch {
      // Ignore.
    }
  }
  sessions.delete(id);
  store.delete(id);
  clearTranscriptCache(id);
  console.log(`session deleted: id=${id}`);
  return true;
}

function sessionOutput(id: string): string | null {
  const session = sessions.get(id);
  return session ? stripAnsi(session.buffer) : null;
}

function exportAllSessions(): string {
  const parts: string[] = ['# StockAssistant 终端会话导出', ''];
  for (const session of listSessions()) {
    const output = sessionOutput(session.id) ?? '';
    parts.push(`## ${session.title}`, '', `- 会话 ID：${session.id}`);
    parts.push(`- 最后活动：${session.updated_at}`, '', '```text', output, '```', '');
  }
  return parts.join('\n');
}

function flushAll(): void {
  for (const session of sessions.values()) {
    if (session.saveTimer) {
      clearTimeout(session.saveTimer);
      session.saveTimer = undefined;
    }
    store.saveOutput(session.id, session.buffer, session.updatedAt);
  }
}

function shutdown(): void {
  if (shuttingDown) return;
  shuttingDown = true;
  try {
    flushAll();
    store.close();
    preferenceStore.close();
  } catch {
    // Best effort on shutdown.
  }
  process.exit(0);
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

const server = Bun.serve<SocketData>({
  hostname: host,
  port,
  idleTimeout: 255,
  async fetch(request, srv) {
    const url = new URL(request.url);
    const method = request.method.toUpperCase();

    if (method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: cors() });
    }

    if (method === 'GET' && url.pathname === '/health') {
      return json({ ok: true });
    }

    // The extension catalog: agents, skills, commands, MCP servers and hooks
    // found in the project's `.stock` directory.
    if (method === 'GET' && url.pathname === '/catalog') {
      const extensions = loadStockExtensions(repoRoot);
      return json(extensions.catalog);
    }

    if (url.pathname === '/agent/preferences') {
      if (method === 'GET') {
        return json({ data: { preferences: preferenceStore.list() } });
      }
      if (method === 'POST') {
        const body = await readJsonBody(request);
        if (!body) return json({ detail: '请求体不是有效的 JSON' }, 400);
        const content =
          typeof body['content'] === 'string' ? body['content'].trim() : '';
        if (!content) return json({ detail: '偏好内容不能为空' }, 400);
        const category =
          typeof body['category'] === 'string' && body['category'].trim()
            ? body['category'].trim().slice(0, 40)
            : 'general';
        const preference = preferenceStore.create({
          content: content.slice(0, MAX_PREFERENCE_LENGTH),
          category,
        });
        return json({ data: { preference } }, 201);
      }
      return json({ detail: '不支持的请求方法' }, 405);
    }

    const preferenceMatch = /^\/agent\/preferences\/(\d+)$/.exec(url.pathname);
    if (preferenceMatch) {
      const id = Number(preferenceMatch[1]);
      if (method === 'PATCH') {
        const body = await readJsonBody(request);
        if (!body) return json({ detail: '请求体不是有效的 JSON' }, 400);
        const changes: {
          content?: string;
          category?: string;
          enabled?: boolean;
        } = {};
        if (typeof body['content'] === 'string') {
          const content = body['content'].trim();
          if (!content) return json({ detail: '偏好内容不能为空' }, 400);
          changes.content = content.slice(0, MAX_PREFERENCE_LENGTH);
        }
        if (typeof body['category'] === 'string' && body['category'].trim()) {
          changes.category = body['category'].trim().slice(0, 40);
        }
        if (typeof body['enabled'] === 'boolean') {
          changes.enabled = body['enabled'];
        }
        const preference = preferenceStore.update(id, changes);
        if (!preference) return json({ detail: '偏好不存在' }, 404);
        return json({ data: { preference } });
      }
      if (method === 'DELETE') {
        return preferenceStore.delete(id)
          ? json({ data: { ok: true } })
          : json({ detail: '偏好不存在' }, 404);
      }
      return json({ detail: '不支持的请求方法' }, 405);
    }

    if (method === 'GET' && url.pathname === '/sessions') {
      return json({ sessions: listSessions() });
    }

    if (method === 'POST' && url.pathname === '/sessions') {
      const cols = clamp(Number(url.searchParams.get('cols')) || 120, 20, 400);
      const rows = clamp(Number(url.searchParams.get('rows')) || 40, 5, 200);
      const session = createSession(cols, rows);
      return json({ session: summary(session) }, 201);
    }

    if (method === 'DELETE' && url.pathname === '/sessions') {
      for (const id of [...sessions.keys()]) deleteSession(id);
      store.deleteAll();
      return json({ ok: true });
    }

    if (method === 'GET' && url.pathname === '/sessions/export') {
      return new Response(exportAllSessions(), {
        headers: cors({
          'Content-Type': 'text/markdown; charset=utf-8',
          'Content-Disposition': 'attachment; filename="terminal-sessions.md"',
        }),
      });
    }

    const streamMatch = /^\/sessions\/([^/]+)\/transcript\/stream$/.exec(
      url.pathname,
    );
    if (method === 'GET' && streamMatch) {
      const id = decodeURIComponent(streamMatch[1]);
      return transcriptEventStream(id);
    }

    const transcriptMatch = /^\/sessions\/([^/]+)\/transcript$/.exec(
      url.pathname,
    );
    if (method === 'GET' && transcriptMatch) {
      const id = decodeURIComponent(transcriptMatch[1]);
      const transcript = readTranscript(id);
      const session = sessions.get(id);
      if (session) syncSessionTitle(session, transcript);
      return json(transcript);
    }

    const outputMatch = /^\/sessions\/([^/]+)\/output$/.exec(url.pathname);
    if (method === 'GET' && outputMatch) {
      const id = decodeURIComponent(outputMatch[1]);
      const output = sessionOutput(id);
      if (output === null) return json({ detail: '会话不存在' }, 404);
      return new Response(output, {
        headers: cors({
          'Content-Type': 'text/plain; charset=utf-8',
          'Content-Disposition': `attachment; filename="${id}.txt"`,
        }),
      });
    }

    const sessionMatch = /^\/sessions\/([^/]+)$/.exec(url.pathname);
    if (method === 'PATCH' && sessionMatch) {
      const id = decodeURIComponent(sessionMatch[1]);
      const session = sessions.get(id);
      if (!session) return json({ detail: '会话不存在' }, 404);
      const body = await readJsonBody(request);
      if (!body) return json({ detail: '请求体不是有效的 JSON' }, 400);
      const rawTitle = body['title'];
      const title = typeof rawTitle === 'string' ? rawTitle.trim() : '';
      if (!title) return json({ detail: '会话名称不能为空' }, 400);
      renameSession(session, title.slice(0, MAX_TITLE_LENGTH));
      return json({ session: summary(session) });
    }

    if (method === 'DELETE' && sessionMatch) {
      const id = decodeURIComponent(sessionMatch[1]);
      return deleteSession(id)
        ? json({ ok: true })
        : json({ detail: '会话不存在' }, 404);
    }

    if (url.pathname === '/terminal/ws') {
      const cols = clamp(Number(url.searchParams.get('cols')) || 120, 20, 400);
      const rows = clamp(Number(url.searchParams.get('rows')) || 40, 5, 200);
      const sessionId = url.searchParams.get('session') ?? '';
      const echo = url.searchParams.get('echo') !== '0';
      const upgraded = srv.upgrade(request, {
        data: { sessionId, cols, rows, echo },
      });
      if (upgraded) return undefined;
      return new Response('WebSocket upgrade failed', { status: 400 });
    }

    return new Response(
      'StockAssistant terminal server\nSessions: /sessions\nWebSocket: /terminal/ws\n',
      { headers: { 'Content-Type': 'text/plain; charset=utf-8' } },
    );
  },
  websocket: {
    open(ws) {
      const { cols, rows, sessionId, echo } = ws.data;
      const session = sessionId ? sessions.get(sessionId) : undefined;

      if (!sessionId) {
        try {
          ws.send('\r\n\x1b[31m[StockAssistant] 缺少会话 ID，请先创建终端会话。\x1b[0m\r\n');
          ws.close(4400, 'session id required');
        } catch {
          // Ignore.
        }
        return;
      }

      if (!session) {
        try {
          ws.send('\r\n\x1b[31m[StockAssistant] 会话不存在或已结束。\x1b[0m\r\n');
          ws.close(4404, 'session not found');
        } catch {
          // Ignore.
        }
        return;
      }

      // Archived sessions (after a restart) are recreated here; the CLI
      // resumes its own session file when available.
      ensureLiveSession(session);

      session.cols = cols;
      session.rows = rows;
      session.sockets.add(ws);
      try {
        session.term?.resize(cols, rows);
      } catch {
        // The CLI may have exited.
      }
      if (echo && session.buffer) {
        try {
          ws.send(session.buffer);
        } catch {
          // Ignore.
        }
      }
      console.log(
        `terminal attach: session=${session.id} size=${cols}x${rows} sockets=${session.sockets.size} alive=${session.alive}`,
      );
    },
    message(ws, message) {
      const session = sessions.get(ws.data.sessionId);
      if (!session || typeof message !== 'string') return;

      try {
        const payload = JSON.parse(message) as Record<string, unknown>;
        if (payload.type === 'input' && typeof payload.data === 'string') {
          session.updatedAt = nowIso();
          writeInput(session, payload.data);
          return;
        }
        if (payload.type === 'resize') {
          const cols = clamp(Number(payload.cols) || 80, 20, 400);
          const rows = clamp(Number(payload.rows) || 24, 5, 200);
          session.cols = cols;
          session.rows = rows;
          session.term?.resize(cols, rows);
          return;
        }
      } catch {
        // Not JSON: treat the frame as raw input.
      }
      session.updatedAt = nowIso();
      writeInput(session, message);
    },
    close(ws) {
      if (!ws.data.sessionId) return;
      const session = sessions.get(ws.data.sessionId);
      session?.sockets.delete(ws);
      console.log(
        `terminal detach: session=${ws.data.sessionId} sockets=${session?.sockets.size ?? 0}`,
      );
    },
  },
});

loadPersistedSessions();

console.log(
  `StockAssistant terminal server listening on ws://${server.hostname}:${server.port}/terminal/ws`,
);
console.log(`  repo root : ${repoRoot}`);
console.log(`  sessions  : http://${server.hostname}:${server.port}/sessions`);
console.log(`  database  : ${dbPath}`);
