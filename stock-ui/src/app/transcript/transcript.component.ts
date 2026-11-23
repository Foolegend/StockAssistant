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
  allCollapsed = false;
  draft = '';
  error: string | null = null;

  private source?: EventSource;
  private socket?: WebSocket;
  private disposed = false;

  constructor(private readonly cdr: ChangeDetectorRef) {}

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
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      void this.send();
    }
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
