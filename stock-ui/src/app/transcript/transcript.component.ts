import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  Input,
  OnChanges,
  OnDestroy,
  SimpleChanges,
  ViewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  CapabilityCatalog,
  SlashSuggestion,
} from '../extensions/capabilities.types';
import { MarkdownPipe } from '../shared/markdown.pipe';

const API_BASE = 'http://127.0.0.1:8015';

/** One step of a turn: a thought, a tool call or interim assistant text. */
interface TranscriptEntry {
  kind: 'thought' | 'tool' | 'text';
  key: string;
  timestamp: string | null;
  text?: string;
  durationMs?: number;
  tool?: string;
  tool_id?: string;
  args?: string;
  output?: string;
  error?: boolean;
}

interface TranscriptTurn {
  index: number;
  prompt: string;
  startedAt: string | null;
  updatedAt: string | null;
  entries: TranscriptEntry[];
  answer: string | null;
  running: boolean;
}

interface TranscriptPayload {
  session_id: string;
  turns: TranscriptTurn[];
  updated_at: string | null;
  running: boolean;
}

/** Human-readable names for the tools the CLI may call. */
const TOOL_LABELS: Record<string, string> = {
  bash: 'Bash',
  read: 'Read',
  read_file: 'Read',
  read_text_file: 'Read',
  write: 'Write',
  write_file: 'Write',
  edit: 'Edit',
  edit_file: 'Edit',
  glob: 'Glob',
  grep: 'Grep',
  ls: 'List',
  list_directory: 'List',
  web_search: 'WebSearch',
  web_fetch: 'WebFetch',
  task: 'Task',
  agent: 'Agent',
  todowrite: 'TodoWrite',
  notebook_edit: 'NotebookEdit',
};

/** Argument keys that best describe what a tool call is acting on. */
const TOOL_TARGET_KEYS = [
  'file_path',
  'path',
  'notebook_path',
  'command',
  'cmd',
  'pattern',
  'query',
  'url',
  'description',
  'prompt',
  'symbol',
];

@Component({
  selector: 'app-transcript',
  imports: [FormsModule, MarkdownPipe],
  templateUrl: './transcript.component.html',
  styleUrl: './transcript.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TranscriptComponent implements OnChanges, OnDestroy {
  @Input() sessionId: string | null = null;

  @ViewChild('scroller') private scroller?: ElementRef<HTMLElement>;
  @ViewChild('composer') private composer?: ElementRef<HTMLTextAreaElement>;

  turns: TranscriptTurn[] = [];
  running = false;
  connected = false;
  sending = false;
  /** Process steps start folded; only the final answer is always visible. */
  allCollapsed = true;
  draft = '';
  error: string | null = null;

  /** Slash commands offered while the composer starts with "/". */
  slashOpen = false;
  slashMatches: SlashSuggestion[] = [];
  slashIndex = 0;
  private slashQuery = '';

  private source?: EventSource;
  private socket?: WebSocket;
  private disposed = false;
  private catalog: CapabilityCatalog | null = null;
  private catalogRequest?: Promise<void>;

  constructor(private readonly cdr: ChangeDetectorRef) {
    void this.ensureCatalog();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (!changes['sessionId']) return;
    this.restart();
  }

  ngOnDestroy(): void {
    this.disposed = true;
    this.closeStream();
    this.closeSocket();
  }

  get metaText(): string {
    if (this.error) return '连接异常';
    if (!this.sessionId) return '未选择会话';
    if (!this.connected) return '正在连接…';
    if (this.running) return '分析中…';
    return this.turns.length ? '已更新' : '等待输入';
  }

  restart(): void {
    this.closeStream();
    this.turns = [];
    this.running = false;
    this.error = null;
    this.cdr.markForCheck();
    if (!this.sessionId) return;
    this.openStream();
  }

  reload(): void {
    this.openStream();
  }

  /** Formats a thinking duration the way the CLI reports it. */
  duration(ms: number | undefined): string {
    if (ms === undefined || !Number.isFinite(ms)) return '0 秒';
    if (ms < 1000) return `${Math.max(0, Math.round(ms))} 毫秒`;
    return `${(ms / 1000).toFixed(1)} 秒`;
  }

  toolLabel(tool: string | undefined): string {
    if (!tool) return '工具';
    const bare = tool.replace(/^mcp__/, '').split('__').pop() ?? tool;
    return TOOL_LABELS[bare.toLowerCase()] ?? bare;
  }

  /** The argument a tool call acted on, e.g. the file path of a Read. */
  toolTarget(args: string | undefined): string {
    const parsed = this.parseArgs(args);
    if (!parsed) return '';
    for (const key of TOOL_TARGET_KEYS) {
      const value = parsed[key];
      if (typeof value === 'string' && value.trim()) {
        return value.trim().split('\n')[0].trim();
      }
      if (typeof value === 'number') {
        return String(value);
      }
    }
    return '';
  }

  prettyArgs(args: string | undefined): string {
    const parsed = this.parseArgs(args);
    if (!parsed) return args ?? '';
    return JSON.stringify(parsed, null, 2);
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.isComposing) return;

    if (this.slashOpen && this.slashMatches.length) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        this.moveSlashSelection(1);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        this.moveSlashSelection(-1);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        this.closeSlashMenu();
        return;
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault();
        this.applySlashSuggestion(
          this.slashMatches[
            Math.min(this.slashIndex, this.slashMatches.length - 1)
          ],
        );
        return;
      }
    }

    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void this.send();
    }
  }

  /** Shows the slash-command menu once the composer starts with "/". */
  onDraftInput(): void {
    const match = /^\/(\S*)$/.exec(this.draft);
    if (!match) {
      this.closeSlashMenu();
      return;
    }
    this.slashQuery = match[1];
    this.slashOpen = true;
    this.slashIndex = 0;
    this.refreshSlashMatches();
    this.cdr.markForCheck();
  }

  selectSlashSuggestion(suggestion: SlashSuggestion): void {
    this.applySlashSuggestion(suggestion);
  }

  isSlashActive(item: SlashSuggestion): boolean {
    const active = this.slashMatches[this.slashIndex];
    return Boolean(active && active.kind === item.kind && active.id === item.id);
  }

  private applySlashSuggestion(suggestion: SlashSuggestion): void {
    this.draft = `/${suggestion.slashName} `;
    this.closeSlashMenu();
    this.composer?.nativeElement.focus();
  }

  private closeSlashMenu(): void {
    this.slashOpen = false;
    this.slashMatches = [];
  }

  private moveSlashSelection(delta: number): void {
    if (!this.slashMatches.length) return;
    const next = this.slashIndex + delta;
    this.slashIndex =
      (next + this.slashMatches.length) % this.slashMatches.length;
  }

  private refreshSlashMatches(): void {
    const catalog = this.catalog;
    if (!catalog) {
      this.slashMatches = [];
      return;
    }
    const query = this.slashQuery.trim().toLowerCase();
    const matches: SlashSuggestion[] = [];
    for (const command of catalog.commands) {
      if (!command.enabled || !command.user_invocable) continue;
      matches.push({
        kind: 'command',
        id: command.id,
        slashName: command.id,
        name: command.name,
        description: command.description,
        argumentHint: command.argument_hint,
        score: this.slashScore(query, command.id, command.name, command.description),
      });
    }
    for (const skill of catalog.skills) {
      if (!skill.enabled || !skill.user_invocable) continue;
      matches.push({
        kind: 'skill',
        id: skill.id,
        slashName: skill.id,
        name: skill.name,
        description: skill.description,
        argumentHint: null,
        score: this.slashScore(query, skill.id, skill.name, skill.description),
      });
    }
    this.slashMatches = matches
      .filter((item) => item.score < 100)
      .sort((left, right) => left.score - right.score)
      .slice(0, 8);
    if (this.slashIndex >= this.slashMatches.length) {
      this.slashIndex = 0;
    }
  }

  private slashScore(
    query: string,
    id: string,
    name: string,
    description: string,
  ): number {
    if (!query) return 10;
    const values = [id, name].map((value) => value.toLowerCase());
    if (values.includes(query)) return 0;
    if (values.some((value) => value.startsWith(query))) return 1;
    if (values.some((value) => value.includes(query))) return 2;
    if (description.toLowerCase().includes(query)) return 3;
    return 100;
  }

  /** Loads the extension catalog used for the slash-command menu. */
  private async ensureCatalog(): Promise<void> {
    if (this.catalog) return;
    if (this.catalogRequest) return this.catalogRequest;
    const request = (async () => {
      try {
        const response = await fetch(`${API_BASE}/catalog`);
        if (!response.ok) return;
        const payload = (await response.json()) as CapabilityCatalog;
        this.catalog = payload;
        if (this.slashOpen) this.refreshSlashMatches();
        this.cdr.markForCheck();
      } catch {
        // The menu simply stays empty; the backend may not be up yet.
      } finally {
        this.catalogRequest = undefined;
      }
    })();
    this.catalogRequest = request;
    return request;
  }

  /**
   * Collapses or expands every process step at once.  The final answer is
   * always visible; only the thinking and tool steps are folded.
   */
  toggleAll(): void {
    const root = this.scroller?.nativeElement;
    if (!root) return;
    const steps = Array.from(
      root.querySelectorAll<HTMLDetailsElement>('details[data-step]'),
    );
    if (!steps.length) return;
    const open = steps.some((step) => !step.open);
    for (const step of steps) {
      step.open = open;
    }
    this.allCollapsed = !open;
    this.cdr.markForCheck();
  }

  async send(): Promise<void> {
    const text = this.draft.trim();
    if (!text || this.sending) return;
    this.sending = true;
    this.error = null;
    try {
      await this.ensureSocket();
      this.socket?.send(JSON.stringify({ type: 'input', data: text }));
      // The CLI treats a burst of input as a paste, so let it settle before
      // the Enter that submits the prompt.
      await new Promise((resolve) => setTimeout(resolve, 220));
      this.socket?.send(JSON.stringify({ type: 'input', data: '\r' }));
      this.draft = '';
    } catch {
      this.error = '无法连接终端服务，请确认后端已启动';
    } finally {
      this.sending = false;
      this.cdr.markForCheck();
    }
  }

  /** Interrupts the running turn (the CLI reacts to Escape). */
  async stop(): Promise<void> {
    try {
      await this.ensureSocket();
      this.socket?.send(JSON.stringify({ type: 'input', data: '\x1b' }));
    } catch {
      this.error = '无法连接终端服务，请确认后端已启动';
      this.cdr.markForCheck();
    }
  }

  focusComposer(): void {
    this.composer?.nativeElement.focus();
  }

  /** Fills the composer (used by the capability dialog's "insert" action). */
  insertDraft(text: string): void {
    this.draft = text;
    this.onDraftInput();
    this.focusComposer();
    this.cdr.markForCheck();
  }

  private openStream(): void {
    this.closeStream();
    const id = this.sessionId;
    if (!id) return;

    const source = new EventSource(
      `${API_BASE}/sessions/${encodeURIComponent(id)}/transcript/stream`,
    );
    this.source = source;

    source.onopen = () => {
      if (this.source !== source) return;
      this.connected = true;
      this.error = null;
      this.cdr.markForCheck();
    };

    source.onmessage = (event: MessageEvent<string>) => {
      if (this.source !== source) return;
      try {
        const payload = JSON.parse(event.data) as TranscriptPayload;
        this.turns = Array.isArray(payload.turns) ? payload.turns : [];
        this.running = payload.running === true;
        this.connected = true;
        this.error = null;
        this.cdr.markForCheck();
        this.scrollToBottom();
      } catch {
        // Ignore malformed frames; the next one will replace it.
      }
    };

    source.onerror = () => {
      if (this.source !== source) return;
      // EventSource reconnects on its own.
      this.connected = false;
      this.cdr.markForCheck();
    };
  }

  private closeStream(): void {
    const source = this.source;
    this.source = undefined;
    try {
      source?.close();
    } catch {
      // Ignore.
    }
  }

  private ensureSocket(): Promise<void> {
    if (this.socket?.readyState === WebSocket.OPEN) {
      return Promise.resolve();
    }
    const id = this.sessionId;
    if (!id) return Promise.reject(new Error('no session'));
    this.closeSocket();

    return new Promise<void>((resolve, reject) => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host =
        window.location.hostname === 'localhost'
          ? '127.0.0.1'
          : window.location.hostname;
      // `echo=0` keeps the server from replaying the whole terminal buffer.
      const socket = new WebSocket(
        `${protocol}//${host}:8015/terminal/ws?session=${encodeURIComponent(
          id,
        )}&cols=120&rows=40&echo=0`,
      );
      this.socket = socket;
      socket.onopen = () => resolve();
      socket.onerror = () => reject(new Error('websocket error'));
      socket.onclose = () => {
        if (this.socket === socket) this.socket = undefined;
      };
    });
  }

  private closeSocket(): void {
    const socket = this.socket;
    this.socket = undefined;
    try {
      socket?.close();
    } catch {
      // Ignore.
    }
  }

  private parseArgs(args: string | undefined): Record<string, unknown> | null {
    if (!args) return null;
    try {
      const parsed: unknown = JSON.parse(args);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // Not JSON: show it verbatim.
    }
    return null;
  }

  private scrollToBottom(): void {
    if (this.disposed) return;
    const element = this.scroller?.nativeElement;
    if (!element) return;
    // The DOM has not been updated yet; wait for the next frame.
    requestAnimationFrame(() => {
      element.scrollTop = element.scrollHeight;
    });
  }
}
