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

/** One selectable answer of a pending AskUserQuestion. */
export interface PendingQuestionOption {
  label: string;
  description?: string;
}

/** A question the CLI is waiting for the user to answer. */
export interface PendingQuestion {
  question: string;
  header?: string;
  multiSelect: boolean;
  options: PendingQuestionOption[];
  /**
   * True when an option carries a preview. The CLI renders those questions
   * with a different layout: digits move the highlight instead of selecting,
   * and the free-text "Other" option is not offered.
   */
  hasPreview: boolean;
}

/** One selectable answer of a permission dialog shown in the terminal. */
export interface PendingApprovalOption {
  /** Keystroke the CLI's select widget accepts for this option. */
  key: string;
  label: string;
}

/**
 * A permission dialog the CLI is displaying in the PTY and waiting on.
 *
 * These prompts are not part of the JSONL transcript: they only exist on the
 * terminal screen. Without surfacing them the structured view looks stuck on
 * the pending tool call, while the CLI is actually blocked on confirmation.
 */
export interface PendingApproval {
  /** The tool call the confirmation belongs to. */
  tool_id: string;
  /** Question as rendered by the CLI, e.g. "Do you want to make this edit to x.py?". */
  question: string;
  options: PendingApprovalOption[];
}

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
  /** Main file/path argument of the call (kept untruncated for matching). */
  target?: string;
  /**
   * Questions carried by an AskUserQuestion call, so the web UI can render
   * them as buttons instead of leaving the user stuck in the terminal.
   */
  questions?: PendingQuestion[];
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

/** Tool name of the built-in multiple-choice prompt. */
const ASK_USER_QUESTION_TOOL = 'askuserquestion';

/**
 * Extracts the questions of an AskUserQuestion input, dropping everything the
 * web UI does not need (previews can be large HTML fragments).
 */
function extractQuestions(input: unknown): PendingQuestion[] | undefined {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    return undefined;
  }
  const raw = (input as { questions?: unknown }).questions;
  if (!Array.isArray(raw)) return undefined;

  const questions: PendingQuestion[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) continue;
    const record = item as Record<string, unknown>;
    const question =
      typeof record['question'] === 'string' ? record['question'].trim() : '';
    if (!question) continue;

    const options: PendingQuestionOption[] = [];
    const rawOptions = Array.isArray(record['options']) ? record['options'] : [];
    let hasPreview = false;
    for (const rawOption of rawOptions) {
      if (!rawOption || typeof rawOption !== 'object') continue;
      const option = rawOption as Record<string, unknown>;
      const label =
        typeof option['label'] === 'string' ? option['label'].trim() : '';
      if (!label) continue;
      if (typeof option['preview'] === 'string' && option['preview'].trim()) {
        hasPreview = true;
      }
      options.push({
        label,
        ...(typeof option['description'] === 'string' &&
        option['description'].trim()
          ? { description: option['description'].trim() }
          : {}),
      });
    }
    if (!options.length) continue;

    const multiSelect = record['multiSelect'] === true;
    questions.push({
      question,
      ...(typeof record['header'] === 'string' && record['header'].trim()
        ? { header: record['header'].trim() }
        : {}),
      multiSelect,
      options,
      // The preview layout is only used for single-select questions.
      hasPreview: hasPreview && !multiSelect,
    });
  }
  return questions.length ? questions : undefined;
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

/** Argument keys that identify what a tool call acts on. */
const TARGET_KEYS = ['file_path', 'notebook_path', 'path'] as const;

/** Main file/path argument of a tool call, if any. */
function toolTarget(input: unknown): string | undefined {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    return undefined;
  }
  const record = input as Record<string, unknown>;
  for (const key of TARGET_KEYS) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number') return String(value);
  }
  return undefined;
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
            const tool = String(item.name ?? 'tool');
            const target = toolTarget(item.input);
            const entry: TranscriptEntry = {
              kind: 'tool',
              key: `tool-${current.entries.length}`,
              timestamp,
              tool,
              tool_id: toolId,
              args: truncate(JSON.stringify(item.input ?? {}), MAX_ARGS),
              ...(target ? { target } : {}),
            };
            if (tool.toLowerCase() === ASK_USER_QUESTION_TOOL) {
              const questions = extractQuestions(item.input);
              if (questions) entry.questions = questions;
            }
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

/** Tools whose confirmation dialog names the file being modified. */
const FILE_EDIT_TOOLS = new Set([
  'edit',
  'edit_file',
  'multiedit',
  'multi_edit',
  'write',
  'write_file',
  'notebookedit',
  'notebook_edit',
  'sededit',
]);

/** A tool call that has been issued but has not produced a result yet. */
function pendingToolCall(
  transcript: Transcript,
): { toolId: string; target: string } | null {
  for (let turnIndex = transcript.turns.length - 1; turnIndex >= 0; turnIndex -= 1) {
    const turn = transcript.turns[turnIndex];
    if (!turn.running) continue;
    for (let index = turn.entries.length - 1; index >= 0; index -= 1) {
      const entry = turn.entries[index];
      if (entry.kind !== 'tool') continue;
      // Only the last tool call can be the one the CLI is blocked on.
      if (entry.output !== undefined) return null;
      const tool = (entry.tool ?? '').toLowerCase();
      if (!FILE_EDIT_TOOLS.has(tool) || !entry.target) return null;
      return {
        toolId: entry.tool_id ?? entry.key,
        target: entry.target,
      };
    }
    return null;
  }
  return null;
}

/** Numbered Yes/No options of a permission dialog, in CLI order. */
function approvalOptions(text: string): PendingApprovalOption[] | null {
  const found: { key: number; isNo: boolean }[] = [];
  // Ink often renders the options without spaces ("1.Yes2.Yes3.No"), so the
  // number must not be preceded by another option's text; lookbehind keeps
  // consecutive matches independent.
  for (const match of text.matchAll(/(?<![\d.])([1-9])\.\s*(Yes|No)/gi)) {
    found.push({ key: Number(match[1]), isNo: match[2].toLowerCase() === 'no' });
  }
  if (found.length < 2) return null;
  if (!found.some((item) => item.isNo)) return null;
  if (!found.some((item) => !item.isNo)) return null;

  let yesSeen = 0;
  return found
    .sort((left, right) => left.key - right.key)
    .filter(
      (item, index, all) =>
        all.findIndex((other) => other.key === item.key) === index,
    )
    .map((item) => {
      if (item.isNo) return { key: String(item.key), label: '拒绝' };
      yesSeen += 1;
      return {
        key: String(item.key),
        label: yesSeen === 1 ? '允许本次' : '允许（本会话内不再询问）',
      };
    });
}

/**
 * Detects a file-modification confirmation the CLI is displaying on the
 * terminal screen.
 *
 * Safety checks for paths such as `.stock/` are bypass-immune, so the CLI
 * can block on a permission dialog even with `--dangerously-skip-permissions`.
 * The dialog never reaches the JSONL transcript; this reads the tail of the
 * PTY output instead and only reports it while the matching tool call is
 * still waiting for its result.
 */
export function detectPendingApproval(
  output: string,
  transcript: Transcript,
): PendingApproval | null {
  const pending = pendingToolCall(transcript);
  if (!pending) return null;

  // Ink only emits changed lines, so the visible dialog is in the recent
  // output; older frames have scrolled far out of the window.
  const tail = stripAnsi(output.slice(-32000));
  if (!tail) return null;

  const name = (pending.target.split(/[\\/]/).pop() ?? '').toLowerCase();
  if (!name) return null;

  const questions = [...tail.matchAll(/Do you want to [^?\n]{0,200}\?/gi)];
  for (let index = questions.length - 1; index >= 0; index -= 1) {
    const match = questions[index];
    const question = match[0];
    if (!question.toLowerCase().includes(name)) continue;
    const rest = tail.slice(
      match.index! + question.length,
      match.index! + question.length + 400,
    );
    const options = approvalOptions(rest);
    if (options) return { tool_id: pending.toolId, question, options };
  }
  return null;
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
