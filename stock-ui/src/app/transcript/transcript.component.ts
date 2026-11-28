import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  EventEmitter,
  HostListener,
  Input,
  OnChanges,
  OnDestroy,
  Output,
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

/** One selectable answer of a pending AskUserQuestion. */
interface PendingQuestionOption {
  label: string;
  description?: string;
}

/** A multiple-choice question the CLI is waiting for the user to answer. */
interface PendingQuestion {
  question: string;
  header?: string;
  multiSelect: boolean;
  options: PendingQuestionOption[];
  /**
   * The CLI uses a preview layout for these questions: digits move the
   * highlight, Enter selects, and there is no "Other" option.
   */
  hasPreview?: boolean;
}

/** One selectable answer of a permission dialog shown in the terminal. */
interface PendingApprovalOption {
  /** Keystroke the CLI's select widget accepts for this option. */
  key: string;
  label: string;
}

/**
 * A permission dialog the CLI is displaying in the terminal and waiting on.
 * Safety-checked paths (`.stock/`, `.git/`, …) prompt even in bypass mode,
 * and the prompt never reaches the JSONL transcript.
 */
interface PendingApproval {
  tool_id: string;
  question: string;
  options: PendingApprovalOption[];
}

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
  /** Main file/path argument of the call. */
  target?: string;
  /** Present while an AskUserQuestion call is waiting to be answered. */
  questions?: PendingQuestion[];
}

/** Local answer progress for one pending AskUserQuestion call. */
interface AskState {
  /** Stable id of the pending tool call this state belongs to. */
  toolId: string;
  /** Index of the question being answered; equals the count at the review step. */
  active: number;
  /** Toggled option indexes per question (multi-select only). */
  selected: number[][];
  /** Free-text "Other" value per question. */
  other: string[];
  /** True while keystrokes are being written to the CLI. */
  sending: boolean;
  /** True once the answer is handed to the CLI and we wait for its reply. */
  locked: boolean;
}

/** Sent to the CLI as a cursor-down keypress by the Ink select widgets. */
const KEY_DOWN = '\u001b[B';
/** Sent to the CLI as Enter. */
const KEY_ENTER = '\r';
/** Sent to the CLI as Escape. */
const KEY_ESCAPE = '\u001b';

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
  /** True once the CLI has mounted its prompt and reads stdin. */
  ready?: boolean;
  /** A permission dialog the CLI is waiting on, if any. */
  pending_approval?: PendingApproval | null;
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
  /** Emits the first prompt so the sidebar can name the session at once. */
  @Output() firstPrompt = new EventEmitter<string>();
  /** Asks the parent to show the raw terminal instead of this view. */
  @Output() rawTerminalRequested = new EventEmitter<void>();

  @ViewChild('scroller') private scroller?: ElementRef<HTMLElement>;
  @ViewChild('composer') private composer?: ElementRef<HTMLTextAreaElement>;

  turns: TranscriptTurn[] = [];
  running = false;
  connected = false;
  /** False while the CLI is still starting up. */
  ready = true;
  sending = false;
  /** Process steps start folded; only the final answer is always visible. */
  allCollapsed = true;
  draft = '';
  error: string | null = null;
  /** Answer progress for the AskUserQuestion card, if one is pending. */
  askState: AskState | null = null;

  /** Permission dialog the CLI is waiting on, if any. */
  pendingApproval: PendingApproval | null = null;
  /** True while the chosen option is being written to the CLI. */
  approvalSending = false;
  /** True once an option was sent for the current dialog. */
  approvalLocked = false;
  /** Tool call the current approval belongs to, used to reset the lock. */
  private approvalToolId: string | null = null;

  /** Image opened in the lightbox, if any. */
  previewImage: string | null = null;
  previewAlt = '';
  previewZoomed = false;

  /** Slash commands offered while the composer starts with "/". */
  slashOpen = false;
  slashMatches: SlashSuggestion[] = [];
  slashIndex = 0;
  private slashQuery = '';

  private source?: EventSource;
  private socket?: WebSocket;
  /** Session the open socket is attached to; it must match before reuse. */
  private socketSessionId?: string;
  /** Session whose first prompt has already been announced to the parent. */
  private namedSessionId?: string;
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
    if (this.pendingApproval) return '等待确认…';
    if (this.running) return '分析中…';
    if (!this.ready) return 'CLI 启动中…';
    return this.turns.length ? '已更新' : '等待输入';
  }

  /** True while the permission card's buttons must stay disabled. */
  get approvalBusy(): boolean {
    return this.approvalSending || this.approvalLocked;
  }

  restart(): void {
    this.closeStream();
    // The socket is bound to the old session; reusing it would send the next
    // question to the previous session's CLI.
    this.closeSocket();
    this.turns = [];
    this.running = false;
    this.error = null;
    this.askState = null;
    this.pendingApproval = null;
    this.approvalSending = false;
    this.approvalLocked = false;
    this.approvalToolId = null;
    this.namedSessionId = undefined;
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

  // --- AskUserQuestion ---------------------------------------------------
  //
  // The CLI shows its multiple-choice dialog inside the PTY and waits for
  // keystrokes. The structured view knows the questions from the transcript,
  // so it renders them as buttons and forwards the matching keys: digits
  // select an option directly (the Ink select supports 1-based indexes), the
  // arrow keys move focus, and Enter confirms.

  /** Questions to answer, or null when this entry is not a pending prompt. */
  pendingQuestions(
    turn: TranscriptTurn,
    entry: TranscriptEntry,
  ): PendingQuestion[] | null {
    if (!turn.running || entry.kind !== 'tool') return null;
    if (entry.output !== undefined) return null;
    return entry.questions?.length ? entry.questions : null;
  }

  askActive(entry: TranscriptEntry): number {
    return this.askStateFor(entry)?.active ?? 0;
  }

  askBusy(entry: TranscriptEntry): boolean {
    const state = this.askStateFor(entry);
    return !!state && (state.sending || state.locked);
  }

  askLocked(entry: TranscriptEntry): boolean {
    return this.askStateFor(entry)?.locked ?? false;
  }

  /**
   * A single single-select question is submitted by picking an option. Any
   * other shape finishes on a separate review step inside the CLI.
   */
  askNeedsReview(entry: TranscriptEntry): boolean {
    const questions = entry.questions ?? [];
    return !(questions.length === 1 && !questions[0]?.multiSelect);
  }

  isOptionSelected(
    entry: TranscriptEntry,
    questionIndex: number,
    optionIndex: number,
  ): boolean {
    const selected = this.askStateFor(entry)?.selected[questionIndex];
    return Array.isArray(selected) && selected.includes(optionIndex);
  }

  getOtherAnswer(entry: TranscriptEntry, questionIndex: number): string {
    return this.askStateFor(entry)?.other[questionIndex] ?? '';
  }

  setOtherAnswer(
    entry: TranscriptEntry,
    questionIndex: number,
    value: string,
  ): void {
    const state = this.askStateFor(entry);
    if (!state) return;
    state.other[questionIndex] = value;
    this.cdr.markForCheck();
  }

  /** Multi-select options toggle locally; the answer is sent on submit. */
  toggleOption(
    entry: TranscriptEntry,
    questionIndex: number,
    optionIndex: number,
  ): void {
    const state = this.askStateFor(entry);
    if (!state || state.sending || state.locked) return;
    if (state.active !== questionIndex) return;
    const selected = state.selected[questionIndex];
    if (!selected) return;
    state.selected[questionIndex] = selected.includes(optionIndex)
      ? selected.filter((index) => index !== optionIndex)
      : [...selected, optionIndex];
    this.cdr.markForCheck();
  }

  /** Picks one option of a single-select question. */
  async answerOption(
    entry: TranscriptEntry,
    questionIndex: number,
    optionIndex: number,
  ): Promise<void> {
    const state = this.askStateFor(entry);
    const question = entry.questions?.[questionIndex];
    if (!state || !question || question.multiSelect) return;
    if (state.sending || state.locked || state.active !== questionIndex) return;

    state.sending = true;
    this.cdr.markForCheck();
    try {
      // The preview layout moves the highlight on a digit and needs Enter,
      // while the compact layout selects on the digit itself.
      const keys = question.hasPreview
        ? [String(optionIndex + 1), KEY_ENTER]
        : [String(optionIndex + 1)];
      await this.sendKeys(keys, {
        gapMs: question.hasPreview ? 220 : 160,
        settleMs: 320,
      });
      this.advanceQuestion(entry, questionIndex);
    } catch {
      this.error = '无法连接终端服务，请切换到原始终端回答';
    } finally {
      state.sending = false;
      this.cdr.markForCheck();
    }
  }

  /** Focuses the CLI's free-text "Other" option, types, and confirms. */
  async answerOther(
    entry: TranscriptEntry,
    questionIndex: number,
  ): Promise<void> {
    const state = this.askStateFor(entry);
    const question = entry.questions?.[questionIndex];
    if (!state || !question || question.multiSelect) return;
    if (state.sending || state.locked || state.active !== questionIndex) return;
    const text = (state.other[questionIndex] ?? '').trim();
    if (!text) return;

    state.sending = true;
    this.cdr.markForCheck();
    try {
      // The "Other" input is appended after the listed options.
      const otherIndex = question.options.length + 1;
      await this.sendKeys([String(otherIndex), text, KEY_ENTER], {
        gapMs: 240,
        settleMs: 320,
      });
      this.advanceQuestion(entry, questionIndex);
    } catch {
      this.error = '无法连接终端服务，请切换到原始终端回答';
    } finally {
      state.sending = false;
      this.cdr.markForCheck();
    }
  }

  /** Sends the toggled options of a multi-select question and confirms it. */
  async submitMultiSelect(
    entry: TranscriptEntry,
    questionIndex: number,
  ): Promise<void> {
    const state = this.askStateFor(entry);
    const question = entry.questions?.[questionIndex];
    if (!state || !question || !question.multiSelect) return;
    if (state.sending || state.locked || state.active !== questionIndex) return;

    const selected = state.selected[questionIndex] ?? [];
    const other = (state.other[questionIndex] ?? '').trim();
    if (!selected.length && !other) return;

    // Options are toggled by their 1-based number; the cursor does not move.
    const keys: string[] = selected.map((index) => String(index + 1));
    // "Other" is the last option; walk down to it and type the answer.
    const optionCount = question.options.length + 1;
    if (other) {
      keys.push(
        ...Array(optionCount - 1).fill(KEY_DOWN),
        other,
        KEY_ENTER,
        KEY_DOWN,
        KEY_ENTER,
      );
    } else {
      // Walk past every option to focus the submit button, then confirm.
      keys.push(...Array(optionCount).fill(KEY_DOWN), KEY_ENTER);
    }

    state.sending = true;
    this.cdr.markForCheck();
    try {
      await this.sendKeys(keys, { gapMs: 160, settleMs: 320 });
      this.advanceQuestion(entry, questionIndex);
    } catch {
      this.error = '无法连接终端服务，请切换到原始终端回答';
    } finally {
      state.sending = false;
      this.cdr.markForCheck();
    }
  }

  /** Confirms the CLI's "Review your answers" step. */
  async submitReview(entry: TranscriptEntry): Promise<void> {
    const state = this.askStateFor(entry);
    if (!state || state.sending || state.locked) return;
    if (!this.askNeedsReview(entry)) return;

    state.sending = true;
    this.cdr.markForCheck();
    try {
      // The review list focuses "Submit answers" first.
      await this.sendKeys(['1'], { settleMs: 320 });
      state.locked = true;
    } catch {
      this.error = '无法连接终端服务，请切换到原始终端回答';
    } finally {
      state.sending = false;
      this.cdr.markForCheck();
    }
  }

  /** Declines the question (Escape in the CLI dialog). */
  async cancelQuestion(entry: TranscriptEntry): Promise<void> {
    const state = this.askStateFor(entry);
    if (!state || state.sending || state.locked) return;

    state.sending = true;
    this.cdr.markForCheck();
    try {
      await this.sendKeys([KEY_ESCAPE], { settleMs: 320 });
      state.locked = true;
    } catch {
      this.error = '无法连接终端服务，请切换到原始终端回答';
    } finally {
      state.sending = false;
      this.cdr.markForCheck();
    }
  }

  // --- Permission dialog --------------------------------------------------
  //
  // Safety-checked paths (`.stock/`, `.git/`, shell configs) prompt even in
  // bypass mode, and that dialog exists only on the terminal screen. The
  // server lifts it out of the PTY buffer; the card forwards the chosen
  // digit to the CLI, the same way the AskUserQuestion card does.

  /** Keeps the card in step with the dialog the server detected. */
  private syncApproval(approval: PendingApproval | null): void {
    if (approval && approval.tool_id !== this.approvalToolId) {
      this.approvalToolId = approval.tool_id;
      this.approvalSending = false;
      this.approvalLocked = false;
    }
    this.pendingApproval = approval;
  }

  /** Sends the digit of the chosen option to the CLI's select widget. */
  async answerApproval(option: PendingApprovalOption): Promise<void> {
    if (!this.pendingApproval || this.approvalBusy) return;
    this.approvalSending = true;
    this.error = null;
    this.cdr.markForCheck();
    try {
      await this.sendKeys([option.key], { settleMs: 320 });
      this.approvalLocked = true;
    } catch {
      this.error = '无法连接终端服务，请切换到原始终端处理';
    } finally {
      this.approvalSending = false;
      this.cdr.markForCheck();
    }
  }

  requestRawTerminal(): void {
    this.rawTerminalRequested.emit();
  }

  // --- Embedded images ---------------------------------------------------
  //
  // Charts are embedded by the answer Markdown.  Inline they are scaled down
  // to fit the viewport so the whole plot is visible; clicking one opens a
  // lightbox that can switch between "fit" and natural size (scroll to pan).

  /** Opens an answer image in the lightbox. */
  onContentClick(event: MouseEvent): void {
    const target = event.target as HTMLElement | null;
    if (!target || target.tagName !== 'IMG') return;
    if (!target.closest('.answer')) return;
    const src = target.getAttribute('src');
    if (!src) return;
    event.preventDefault();
    this.previewImage = src;
    this.previewAlt = target.getAttribute('alt') || '图表';
    this.previewZoomed = false;
    this.cdr.markForCheck();
  }

  closeImagePreview(): void {
    if (!this.previewImage) return;
    this.previewImage = null;
    this.previewZoomed = false;
    this.cdr.markForCheck();
  }

  toggleImageZoom(): void {
    this.previewZoomed = !this.previewZoomed;
    this.cdr.markForCheck();
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.closeImagePreview();
  }

  private askId(entry: TranscriptEntry): string {
    return entry.tool_id || entry.key;
  }

  private askStateFor(entry: TranscriptEntry): AskState | null {
    const state = this.askState;
    return state && state.toolId === this.askId(entry) ? state : null;
  }

  /** Moves to the next question, or to the review step after the last one. */
  private advanceQuestion(
    entry: TranscriptEntry,
    questionIndex: number,
  ): void {
    const state = this.askStateFor(entry);
    if (!state) return;
    const total = entry.questions?.length ?? 0;
    if (questionIndex + 1 < total) {
      state.active = questionIndex + 1;
    } else {
      state.active = total;
      // Without a review step the CLI has already submitted the answer.
      if (!this.askNeedsReview(entry)) {
        state.locked = true;
      }
    }
    this.cdr.markForCheck();
  }

  /** Creates or clears the local answer state when the pending prompt changes. */
  private syncAskState(): void {
    const pending = this.findPendingQuestion();
    if (!pending) {
      this.askState = null;
      return;
    }
    if (this.askState?.toolId === pending.toolId) return;
    const count = pending.questions.length;
    this.askState = {
      toolId: pending.toolId,
      active: 0,
      selected: Array.from({ length: count }, () => [] as number[]),
      other: Array.from({ length: count }, () => ''),
      sending: false,
      locked: false,
    };
  }

  private findPendingQuestion(): {
    toolId: string;
    questions: PendingQuestion[];
  } | null {
    for (let turnIndex = this.turns.length - 1; turnIndex >= 0; turnIndex -= 1) {
      const turn = this.turns[turnIndex];
      if (!turn.running) continue;
      for (let index = turn.entries.length - 1; index >= 0; index -= 1) {
        const entry = turn.entries[index];
        const questions = this.pendingQuestions(turn, entry);
        if (questions) {
          return { toolId: this.askId(entry), questions };
        }
      }
    }
    return null;
  }

  /** Writes keys to the CLI one message at a time, with a gap between them. */
  private async sendKeys(
    keys: string[],
    options: { gapMs?: number; settleMs?: number } = {},
  ): Promise<void> {
    const gapMs = options.gapMs ?? 160;
    await this.ensureSocket();
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      throw new Error('socket not open');
    }
    const sends = keys.filter((key) => key.length > 0);
    for (let index = 0; index < sends.length; index += 1) {
      socket.send(JSON.stringify({ type: 'input', data: sends[index] }));
      if (index < sends.length - 1) {
        await this.delay(gapMs);
      }
    }
    if (options.settleMs) {
      await this.delay(options.settleMs);
    }
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
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
      const socket = this.socket;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        throw new Error('socket not open');
      }
      socket.send(JSON.stringify({ type: 'input', data: text }));
      // The CLI treats a burst of input as a paste, so let it settle before
      // the Enter that submits the prompt.
      await new Promise((resolve) => setTimeout(resolve, 220));
      socket.send(JSON.stringify({ type: 'input', data: '\r' }));
      this.draft = '';
    } catch {
      // Keep the draft so the question is not lost.
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
        this.ready = payload.ready !== false;
        this.connected = true;
        this.error = null;
        // The transcript is replaced on every push, so the pending-question
        // card keeps its progress keyed by the tool call id.
        this.syncAskState();
        this.syncApproval(payload.pending_approval ?? null);
        const prompt = this.turns[0]?.prompt;
        if (prompt && this.namedSessionId !== this.sessionId) {
          this.namedSessionId = this.sessionId ?? undefined;
          this.firstPrompt.emit(prompt);
        }
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
    const id = this.sessionId;
    if (!id) return Promise.reject(new Error('no session'));
    // Only reuse the socket when it belongs to the session being written to.
    if (
      this.socket?.readyState === WebSocket.OPEN &&
      this.socketSessionId === id
    ) {
      return Promise.resolve();
    }
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
      this.socketSessionId = id;
      // The promise must settle even when the socket closes before it opens,
      // otherwise the composer waits forever and looks unresponsive.
      let settled = false;
      const settle = (action: () => void): void => {
        if (settled) return;
        settled = true;
        action();
      };
      socket.onopen = () => settle(resolve);
      socket.onerror = () => settle(() => reject(new Error('websocket error')));
      socket.onclose = () => {
        settle(() => reject(new Error('websocket closed')));
        if (this.socket === socket) {
          this.socket = undefined;
          this.socketSessionId = undefined;
        }
      };
    });
  }

  private closeSocket(): void {
    const socket = this.socket;
    this.socket = undefined;
    this.socketSessionId = undefined;
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
