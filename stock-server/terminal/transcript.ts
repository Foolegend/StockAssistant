/**
 * Parses a CLI session transcript into a UI-friendly transcript.
 *
 * The CLI stores every conversation it has in a JSONL file
 * (`~/.stock/projects/<project>/<session-id>.jsonl`).  Each line is one
 * message record: `user` records carry either the user's prompt (string
 * content) or the results of a tool call (array content), `assistant`
 * records carry thinking blocks, plain text and `tool_use` blocks.
 *
 * `parseTranscript` turns that stream into one entry per user turn:
 *
 *   prompt → [thought] → [tool(args)] → [tool(result)] → … → answer
 *
 * The frontend renders the result directly, so tool calls are already
 * merged (arguments + output in a single entry) and the final answer is
 * lifted out of the entry list.
 */

import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export interface TranscriptEntry {
  kind: 'thought' | 'tool' | 'text';
  key: string;
  timestamp: string | null;
  /** Thinking text, or the assistant's interim text output. */
  text?: string;
  /** How long the thinking phase took, in milliseconds. */
  durationMs?: number;
  /** Tool name for `kind: 'tool'`. */
  tool?: string;
  tool_id?: string;
  /** Tool arguments, JSON encoded. */
  args?: string;
  /** Tool output, ANSI stripped. */
  output?: string;
  error?: boolean;
}

export interface TranscriptTurn {
  index: number;
  prompt: string;
  startedAt: string | null;
  updatedAt: string | null;
  entries: TranscriptEntry[];
  /** The final answer of a completed turn. */
  answer: string | null;
  /** True while the turn is still being worked on. */
  running: boolean;
}

export interface Transcript {
  session_id: string;
  turns: TranscriptTurn[];
  updated_at: string | null;
  /** True while the last turn is still running. */
  running: boolean;
}

const MAX_TEXT = 4000;
const MAX_ARGS = 2000;

function truncate(value: string, max = MAX_TEXT): string {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

export function stripAnsi(text: string): string {
  return text
    .replace(/\x1b\][^\x07]*\x07/g, '')
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/\x1b[@-Z\\-_]/g, '');
}

/** Joins the text blocks of a tool result or message content. */
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

/** Finds the CLI's own saved session file (~/.stock/projects/<dir>/<id>.jsonl). */
export function findCliSessionPath(id: string): string | null {
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

export function hasCliSession(id: string): boolean {
  return findCliSessionPath(id) !== null;
}

/**
 * Parses the raw JSONL transcript into turns and entries.
 */
export function parseTranscript(jsonl: string, sessionId: string): Transcript {
  const turns: TranscriptTurn[] = [];
  let current: TranscriptTurn | null = null;
  /** Tool entries of the current turn, keyed by tool_use id. */
  const pendingTools = new Map<string, TranscriptEntry>();
  let lastTimestampMs: number | null = null;
  let lastStopReason: string | null = null;
  let sawAssistant = false;

  for (const line of jsonl.split(/\r?\n/)) {
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
    const timestampMs = timestamp ? Date.parse(timestamp) : null;
    const inner = record.message as
      | { content?: unknown; stop_reason?: unknown }
      | undefined;
    const content = inner?.content;

    if (record.type === 'user') {
      if (typeof content === 'string') {
        // A real user prompt starts a new turn.
        const prompt = content.trim();
        if (prompt) {
          current = {
            index: turns.length,
            prompt,
            startedAt: timestamp,
            updatedAt: timestamp,
            entries: [],
            answer: null,
            running: false,
          };
          turns.push(current);
          pendingTools.clear();
          sawAssistant = false;
          lastStopReason = null;
        }
      } else if (Array.isArray(content)) {
        // Tool results arrive as user records; merge them into their call.
        for (const block of content) {
          if (!block || typeof block !== 'object') continue;
          const item = block as {
            type?: unknown;
            content?: unknown;
            tool_use_id?: unknown;
            is_error?: unknown;
          };
          if (item.type !== 'tool_result') continue;
          const entry = pendingTools.get(String(item.tool_use_id ?? ''));
          if (!entry) continue;
          entry.output = truncate(stripAnsi(textBlocks(item.content)));
          entry.error = item.is_error === true;
        }
      }
    } else if (current) {
      // Assistant record: thinking, text and tool calls of one API response.
      sawAssistant = true;
      lastStopReason =
        typeof inner?.stop_reason === 'string' ? inner.stop_reason : null;
      const elapsed =
        timestampMs !== null && lastTimestampMs !== null
          ? Math.max(0, timestampMs - lastTimestampMs)
          : undefined;

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
              current.entries.push({
                kind: 'thought',
                key: `thought-${current.entries.length}`,
                timestamp,
                text: truncate(text),
                ...(elapsed !== undefined ? { durationMs: elapsed } : {}),
              });
            }
          } else if (item.type === 'text') {
            const text = String(item.text ?? '').trim();
            if (text) {
              current.entries.push({
                kind: 'text',
                key: `text-${current.entries.length}`,
                timestamp,
                text,
              });
            }
          } else if (item.type === 'tool_use') {
            const toolId = String(item.id ?? '');
            const entry: TranscriptEntry = {
              kind: 'tool',
              key: `tool-${current.entries.length}`,
              timestamp,
              tool: String(item.name ?? 'tool'),
              tool_id: toolId,
              args: truncate(JSON.stringify(item.input ?? {}), MAX_ARGS),
            };
            current.entries.push(entry);
            pendingTools.set(toolId, entry);
          }
        }
      } else if (typeof content === 'string') {
        const text = content.trim();
        if (text) {
          current.entries.push({
            kind: 'text',
            key: `text-${current.entries.length}`,
            timestamp,
            text,
          });
        }
      }
    }

    if (current && timestamp) current.updatedAt = timestamp;
    if (timestampMs !== null) lastTimestampMs = timestampMs;
  }

  // The last turn is running while the model is still working on it.
  const last = turns[turns.length - 1] ?? null;
  const lastRunning = last ? !sawAssistant || lastStopReason === 'tool_use' : false;
  if (last) {
    last.running = lastRunning;
  }

  // A completed turn has a final answer: the last text entry. While the turn
  // is still running the text stays in the entry list as interim output.
  for (const turn of turns) {
    for (let index = turn.entries.length - 1; index >= 0; index -= 1) {
      const entry = turn.entries[index];
      if (entry.kind !== 'text' || !entry.text) continue;
      if (turn.running) break;
      turn.answer = entry.text;
      turn.entries.splice(index, 1);
      break;
    }
  }

  return {
    session_id: sessionId,
    turns,
    updated_at: turns[turns.length - 1]?.updatedAt ?? null,
    running: lastRunning,
  };
}

interface TranscriptCacheEntry {
  mtimeMs: number;
  size: number;
  payload: Transcript;
}

const cache = new Map<string, TranscriptCacheEntry>();

/**
 * Reads the transcript of a session, re-parsing only when the file changed.
 */
export function readTranscript(sessionId: string): Transcript {
  const file = findCliSessionPath(sessionId);
  if (!file) {
    return {
      session_id: sessionId,
      turns: [],
      updated_at: null,
      running: false,
    };
  }
  let mtimeMs = 0;
  let size = 0;
  try {
    const stat = statSync(file);
    mtimeMs = stat.mtimeMs;
    size = stat.size;
  } catch {
    // Fall through to a fresh parse.
  }
  const cached = cache.get(sessionId);
  if (cached && cached.mtimeMs === mtimeMs && cached.size === size) {
    return cached.payload;
  }
  let payload: Transcript;
  try {
    payload = parseTranscript(readFileSync(file, 'utf8'), sessionId);
  } catch {
    payload = {
      session_id: sessionId,
      turns: [],
      updated_at: null,
      running: false,
    };
  }
  cache.set(sessionId, { mtimeMs, size, payload });
  return payload;
}

export function clearTranscriptCache(sessionId?: string): void {
  if (sessionId) cache.delete(sessionId);
  else cache.clear();
}
