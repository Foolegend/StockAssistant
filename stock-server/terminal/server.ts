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
 *   DELETE /sessions/:id
 *   GET    /sessions/export
 *   GET    /sessions/:id/output
 *
 * WebSocket:
 *   /terminal/ws?session=<id>&cols=<n>&rows=<n>
 *   client -> server: {"type":"input","data":"..."} / {"type":"resize","cols":N,"rows":N}
 *   server -> client: session buffer replay + raw ANSI output
 */

import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import type { ServerWebSocket } from 'bun';
import { loadStockExtensions } from '../lib/catalog';
import { SessionStore } from './store';
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
}

interface SocketData {
  sessionId: string;
  cols: number;
  rows: number;
}

const sessions = new Map<string, Session>();
const store = new SessionStore(dbPath);
const MAX_BUFFER = 200_000;
let shuttingDown = false;

const CORS_HEADERS: Record<string, string> = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,DELETE,OPTIONS',
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

function stripAnsi(text: string): string {
  return text
    .replace(/\x1b\][^\x07]*\x07/g, '')
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/\x1b[@-Z\\-_]/g, '');
}

/** Finds the CLI's own saved session file (~/.stock/projects/<dir>/<id>.jsonl). */
function findCliSessionPath(id: string): string | null {
  const root = join(homedir(), '.stock', 'projects');
  if (!existsSync(root)) return null;
  try {
    for (const entry of readdirSync(root, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const file = join(root, entry.name, `${id}.jsonl`);
      if (existsSync(file)) return file;
    }
  } catch {
    // Ignore unreadable project dirs.
  }
  return null;
}

function hasCliSession(id: string): boolean {
  return findCliSessionPath(id) !== null;
}

interface AnalysisMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: string | null;
}

interface AnalysisStep {
  kind: 'user' | 'thought' | 'tool_start' | 'tool_end' | 'text';
  text?: string;
  tool?: string;
  tool_id?: string;
  args?: string;
  output?: string;
  error?: boolean;
  timestamp: string | null;
}

const MAX_STEP_CHARS = 4000;

function truncateText(value: string, max = MAX_STEP_CHARS): string {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function textBlocks(content: unknown): string {
  if (typeof content === 'string') return content.trim();
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (!block || typeof block !== 'object') return '';
        const item = block as { type?: unknown; text?: unknown };
        return item.type === 'text' ? String(item.text ?? '') : '';
      })
      .filter(Boolean)
      .join('\n')
      .trim();
  }
  return '';
}

/**
 * Extracts the last turn's analysis process (thoughts, tool calls and tool
 * results) plus the final assistant answer from the CLI transcript.
 */
function readAnalysis(id: string): {
  answer: string | null;
  updated_at: string | null;
  steps: AnalysisStep[];
  messages: AnalysisMessage[];
  turnRunning: boolean;
} {
  const empty = {
    answer: null,
    updated_at: null,
    steps: [] as AnalysisStep[],
    messages: [] as AnalysisMessage[],
    turnRunning: false,
  };
  const file = findCliSessionPath(id);
  if (!file) return empty;

  const steps: AnalysisStep[] = [];
  const messages: AnalysisMessage[] = [];
  let lastTimestamp: string | null = null;
  let lastAssistantStepIndex = -1;
  let lastAssistantStopReason: string | null = null;

  try {
    for (const line of readFileSync(file, 'utf8').split(/\r?\n/)) {
      if (!line.trim()) continue;
      let record: Record<string, unknown>;
      try {
        record = JSON.parse(line) as Record<string, unknown>;
      } catch {
        continue;
      }
      if (record.type !== 'user' && record.type !== 'assistant') continue;
      if (record.isSidechain === true) continue;

      const timestamp =
        typeof record.timestamp === 'string' ? record.timestamp : null;
      if (timestamp) lastTimestamp = timestamp;
      const inner = record.message as {
        content?: unknown;
        stop_reason?: unknown;
      } | undefined;
      const content = inner?.content;

      if (record.type === 'user') {
        if (typeof content === 'string') {
          const text = content.trim();
          if (text) {
            steps.push({ kind: 'user', text, timestamp });
            messages.push({ role: 'user', content: text, timestamp });
          }
        } else if (Array.isArray(content)) {
          for (const block of content) {
            if (!block || typeof block !== 'object') continue;
            const item = block as {
              type?: unknown;
              text?: unknown;
              tool_use_id?: unknown;
              content?: unknown;
              is_error?: unknown;
            };
            if (item.type === 'tool_result') {
              const output = truncateText(
                stripAnsi(textBlocks(item.content)),
              );
              steps.push({
                kind: 'tool_end',
                tool_id: String(item.tool_use_id ?? ''),
                output,
                error: item.is_error === true,
                timestamp,
              });
            }
            // Text blocks in user records are CLI-injected context (for
            // example a skill body), not the user's own prompt; ignore them.
          }
        }
        continue;
      }

      // assistant
      lastAssistantStepIndex = steps.length;
      lastAssistantStopReason =
        typeof inner?.stop_reason === 'string' ? inner.stop_reason : null;

      if (Array.isArray(content)) {
        for (const block of content) {
          if (!block || typeof block !== 'object') continue;
          const item = block as {
            type?: unknown;
            thinking?: unknown;
            text?: unknown;
            id?: unknown;
            name?: unknown;
            input?: unknown;
          };
          if (item.type === 'thinking') {
            const text = String(item.thinking ?? '').trim();
            if (text) {
              steps.push({
                kind: 'thought',
                text: truncateText(text),
                timestamp,
              });
            }
          } else if (item.type === 'text') {
            const text = String(item.text ?? '').trim();
            if (text) {
              steps.push({ kind: 'text', text, timestamp });
              messages.push({ role: 'assistant', content: text, timestamp });
            }
          } else if (item.type === 'tool_use') {
            steps.push({
              kind: 'tool_start',
              tool: String(item.name ?? 'tool'),
              tool_id: String(item.id ?? ''),
              args: truncateText(JSON.stringify(item.input ?? {}), 2000),
              timestamp,
            });
          }
        }
      } else if (typeof content === 'string') {
        const text = content.trim();
        if (text) {
          steps.push({ kind: 'text', text, timestamp });
          messages.push({ role: 'assistant', content: text, timestamp });
        }
      }
    }
  } catch {
    return empty;
  }

  // Keep only the latest turn's process (starting at its user prompt).
  let lastUserIndex = -1;
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    if (steps[index].kind === 'user') {
      lastUserIndex = index;
      break;
    }
  }
  let turnSteps =
    lastUserIndex >= 0 ? steps.slice(lastUserIndex) : steps.slice(-80);

  const hasAssistantAfterUser = lastAssistantStepIndex > lastUserIndex;
  const turnRunning =
    !hasAssistantAfterUser || lastAssistantStopReason === 'tool_use';

  // Only a completed turn has a final answer. While tools are still running,
  // assistant text is shown in the process timeline as an interim output.
  let answer: string | null = null;
  let answerTimestamp: string | null = null;
  let answerIndex = -1;
  if (!turnRunning) {
    for (let index = turnSteps.length - 1; index >= 0; index -= 1) {
      const step = turnSteps[index];
      if (step.kind === 'text' && step.text) {
        answer = step.text;
        answerTimestamp = step.timestamp;
        answerIndex = index;
        break;
      }
    }
    if (answerIndex >= 0) {
      turnSteps = turnSteps.filter((_, index) => index !== answerIndex);
    }
  }
  if (turnSteps.length > 80) {
    turnSteps = turnSteps.slice(-80);
  }

  return {
    answer,
    updated_at: answerTimestamp ?? lastTimestamp,
    steps: turnSteps,
    messages,
    turnRunning,
  };
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
    { sessionId: session.id, resume },
  );

  const decoder = new TextDecoder();
  session.decoder = decoder;

  const term = new TerminalCtor({
    cols: session.cols,
    rows: session.rows,
    data(_term, chunk) {
      appendOutput(session, decoder.decode(chunk, { stream: true }));
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
    });
  }
  console.log(`sessions loaded: ${rows.length}`);
}

function deleteSession(id: string): boolean {
  const session = sessions.get(id);
  if (!session) return false;
  if (session.saveTimer) {
    clearTimeout(session.saveTimer);
    session.saveTimer = undefined;
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
  fetch(request, srv) {
    const url = new URL(request.url);
    const method = request.method.toUpperCase();

    if (method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: cors() });
    }

    if (method === 'GET' && url.pathname === '/health') {
      return json({ ok: true });
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

    const analysisMatch = /^\/sessions\/([^/]+)\/analysis$/.exec(url.pathname);
    if (method === 'GET' && analysisMatch) {
      const id = decodeURIComponent(analysisMatch[1]);
      const analysis = readAnalysis(id);
      return json({
        answer: analysis.answer,
        updated_at: analysis.updated_at,
        steps: analysis.steps,
        messages: analysis.messages,
        running: (sessions.get(id)?.alive ?? false) && analysis.turnRunning,
      });
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
      const upgraded = srv.upgrade(request, {
        data: { sessionId, cols, rows },
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
      const { cols, rows, sessionId } = ws.data;
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
      if (session.buffer) {
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
          session.term?.write(payload.data);
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
      session.term?.write(message);
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
