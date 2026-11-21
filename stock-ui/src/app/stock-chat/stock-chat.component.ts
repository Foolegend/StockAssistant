import { ChangeDetectorRef, Component, ChangeDetectionStrategy } from '@angular/core';

import { FormsModule } from '@angular/forms';

import { MarkdownPipe } from '../shared/markdown.pipe';

interface SourceLink {
  title: string;
  url: string;
}

interface AgentActivity {
  id: string;
  tool: string;
  status: 'running' | 'complete';
  args?: string;
  output?: string;
  error?: boolean;
  durationMs?: number;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  error?: string;
  status?: string;
  cancelled?: boolean;
  activities?: AgentActivity[];
  sources?: SourceLink[];
  agentId?: string | null;
  model?: string | null;
}

interface ChatConversation {
  threadId: string;
  title: string;
  updatedAt: number;
  messages: ChatMessage[];
  /** True when the messages live only on the server and load on demand. */
  remote?: boolean;
}

interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}

interface UserPreference {
  id: number;
  category: string;
  content: string;
  source: 'manual' | 'auto' | string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

interface PreferenceCategory {
  id: string;
  label: string;
}

@Component({
  selector: 'app-stock-chat',
  imports: [FormsModule, MarkdownPipe],
  changeDetection: ChangeDetectionStrategy.Eager,
  templateUrl: './stock-chat.component.html',
})
export class StockChatComponent {
  readonly suggestions = [
    '梳理今天影响市场的主要因素',
    '分析一只股票时，应该先看哪些指标？',
    '帮我比较两家公司的商业模式',
  ];
  readonly apiBaseUrl = 'http://127.0.0.1:8015';
  readonly copyStates: Record<string, 'idle' | 'copied' | 'failed'> = {};
  readonly preferenceCategories: PreferenceCategory[] = [
    { id: 'market', label: '市场' },
    { id: 'investment', label: '投资' },
    { id: 'style', label: '风格' },
    { id: 'profile', label: '个人' },
    { id: 'general', label: '其他' },
  ];

  conversations: ChatConversation[] = [];
  currentConversation: ChatConversation;
  userQuestion = '';
  isLoading = false;
  conversationLoading = false;
  agentStatus = '';
  apiError: string | null = null;
  storageWarning: string | null = null;

  exportingAll = false;
  exportingConversationId: string | null = null;
  draggedThreadId: string | null = null;
  dragOverThreadId: string | null = null;

  preferences: UserPreference[] = [];
  preferencesOpen = false;
  preferencesLoading = false;
  preferencesError: string | null = null;
  preferenceDraft = '';
  preferenceCategory = 'general';
  editingPreferenceId: number | null = null;
  editingContent = '';

  private abortController?: AbortController;
  private activeRunId?: string;
  private readonly storageKey = 'stock-assistant-conversations-v1';
  private readonly orderKey = 'stock-assistant-conversation-order-v1';
  /** Conversations whose full message history is cached locally. */
  private readonly maxCachedConversations = 30;
  /** Upper bound for the sidebar list, including server-only entries. */
  private readonly maxListedConversations = 100;

  constructor(private cdr: ChangeDetectorRef) {
    this.conversations = this.applyOrder(this.loadConversations());
    this.currentConversation = this.createConversation();
    void this.syncFromServer();
  }

  get currentSources(): SourceLink[] {
    const messages = this.currentConversation.messages;
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index].role === 'assistant') {
        return messages[index].sources ?? [];
      }
    }
    return [];
  }

  get currentActivities(): AgentActivity[] {
    const messages = this.currentConversation.messages;
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index].role === 'assistant') {
        return messages[index].activities ?? [];
      }
    }
    return [];
  }

  newChat(): void {
    if (this.isLoading) {
      return;
    }
    this.currentConversation = this.createConversation();
    this.userQuestion = '';
    this.apiError = null;
    this.agentStatus = '';
  }

  async openConversation(conversation: ChatConversation): Promise<void> {
    if (this.isLoading || this.conversationLoading) {
      return;
    }
    this.currentConversation = conversation;
    this.userQuestion = '';
    this.apiError = null;
    this.agentStatus = '';

    if (conversation.remote && !conversation.messages.length) {
      await this.loadConversationMessages(conversation);
    }
  }

  deleteConversation(conversation: ChatConversation): void {
    if (this.isLoading) {
      return;
    }
    this.conversations = this.conversations.filter(
      (item) => item.threadId !== conversation.threadId,
    );
    if (this.currentConversation.threadId === conversation.threadId) {
      this.currentConversation = this.createConversation();
      this.userQuestion = '';
      this.apiError = null;
      this.agentStatus = '';
    }
    this.persistConversations();
    this.saveOrder();
    this.cdr.detectChanges();
    void this.releaseThreadOnServer(conversation.threadId);
  }

  clearAllConversations(): void {
    if (this.isLoading || !this.conversations.length) {
      return;
    }
    const threadIds = this.conversations.map((item) => item.threadId);
    this.conversations = [];
    this.currentConversation = this.createConversation();
    this.userQuestion = '';
    this.apiError = null;
    this.agentStatus = '';
    this.persistConversations();
    this.saveOrder();
    this.cdr.detectChanges();
    for (const threadId of threadIds) {
      void this.releaseThreadOnServer(threadId);
    }
  }

  onDragStart(conversation: ChatConversation, event: DragEvent): void {
    if (this.isLoading) {
      event.preventDefault();
      return;
    }
    this.draggedThreadId = conversation.threadId;
    this.dragOverThreadId = null;
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', conversation.threadId);
    }
  }

  onDragOver(event: DragEvent, conversation: ChatConversation): void {
    if (!this.draggedThreadId || this.draggedThreadId === conversation.threadId) {
      return;
    }
    // Preventing the default is what makes the element a valid drop target.
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'move';
    }
    this.dragOverThreadId = conversation.threadId;
  }

  onDragLeave(conversation: ChatConversation): void {
    if (this.dragOverThreadId === conversation.threadId) {
      this.dragOverThreadId = null;
    }
  }

  onDrop(event: DragEvent, target: ChatConversation): void {
    event.preventDefault();
    const sourceId = this.draggedThreadId;
    this.draggedThreadId = null;
    this.dragOverThreadId = null;
    if (!sourceId || sourceId === target.threadId) {
      this.cdr.detectChanges();
      return;
    }

    const list = [...this.conversations];
    const from = list.findIndex((item) => item.threadId === sourceId);
    const to = list.findIndex((item) => item.threadId === target.threadId);
    if (from < 0 || to < 0) {
      this.cdr.detectChanges();
      return;
    }

    const [moved] = list.splice(from, 1);
    // Dropping above the target places the item before it.
    list.splice(from < to ? to - 1 : to, 0, moved);
    this.conversations = list;
    this.saveOrder();
    this.cdr.detectChanges();
  }

  onDragEnd(): void {
    this.draggedThreadId = null;
    this.dragOverThreadId = null;
    this.cdr.detectChanges();
  }

  confirmClearAll(): void {
    if (this.isLoading || !this.conversations.length) {
      return;
    }
    if (!window.confirm(`确定清空全部 ${this.conversations.length} 条会话记录吗？此操作无法撤销。`)) {
      return;
    }
    this.clearAllConversations();
  }

  async exportConversation(conversation: ChatConversation): Promise<void> {
    const hasLocal = conversation.messages.length > 0;
    if (
      this.isLoading ||
      this.exportingConversationId ||
      (!hasLocal && !conversation.remote)
    ) {
      return;
    }

    this.exportingConversationId = conversation.threadId;
    this.apiError = null;
    this.cdr.detectChanges();
    try {
      // The server has the authoritative record; fall back to the local copy
      // when it is unavailable (offline, expired, or never persisted).
      const response = await fetch(
        `${this.apiBaseUrl}/agent/threads/${encodeURIComponent(conversation.threadId)}/export?format=markdown`,
      );
      if (response.ok) {
        const body = await response.text();
        this.downloadText(
          body,
          `${this.safeFileName(conversation.title)}.md`,
        );
        return;
      }
      if (!hasLocal) {
        this.apiError =
          response.status === 404
            ? '该会话没有可导出的记录'
            : await this.readError(response);
        return;
      }
    } catch {
      if (!hasLocal) {
        this.apiError = '导出失败，请稍后重试';
        return;
      }
    } finally {
      this.exportingConversationId = null;
      this.cdr.detectChanges();
    }

    this.exportLocalConversation(conversation);
  }

  private exportLocalConversation(conversation: ChatConversation): void {
    const lines: string[] = [
      `# ${conversation.title}`,
      '',
      `- 会话 ID：${conversation.threadId}`,
      `- 导出时间：${new Date().toLocaleString()}`,
      '',
    ];

    for (const message of conversation.messages) {
      lines.push(message.role === 'user' ? '## 我' : '## 研究助手');
      lines.push('');
      if (message.content) {
        lines.push(message.content);
      } else if (message.error) {
        lines.push(`（请求失败：${message.error}）`);
      } else if (message.cancelled) {
        lines.push(`（已停止：${message.status ?? '用户中止'}）`);
      } else {
        lines.push('（无内容）');
      }
      if (message.sources?.length) {
        lines.push('');
        lines.push('参考来源：');
        for (const source of message.sources) {
          lines.push(`- [${source.title || source.url}](${source.url})`);
        }
      }
      lines.push('');
    }

    this.downloadText(
      lines.join('\n'),
      `${this.safeFileName(conversation.title)}.md`,
    );
  }

  /** Export every stored conversation in one Markdown file. */
  async exportAllConversations(): Promise<void> {
    if (this.exportingAll || !this.conversations.length) {
      return;
    }
    this.exportingAll = true;
    this.apiError = null;
    this.cdr.detectChanges();
    try {
      const response = await fetch(
        `${this.apiBaseUrl}/agent/threads/export?format=markdown`,
      );
      if (!response.ok) {
        throw new Error(await this.readError(response));
      }
      const body = await response.text();
      const stamp = new Date().toISOString().slice(0, 10);
      this.downloadText(body, `conversations-${stamp}.md`);
    } catch (error: unknown) {
      this.apiError =
        error instanceof Error ? error.message : '批量导出失败';
    } finally {
      this.exportingAll = false;
      this.cdr.detectChanges();
    }
  }

  async openPreferences(): Promise<void> {
    this.preferencesOpen = true;
    this.preferencesError = null;
    await this.loadPreferences();
  }

  closePreferences(): void {
    this.preferencesOpen = false;
    this.editingPreferenceId = null;
    this.editingContent = '';
  }

  async addPreference(): Promise<void> {
    const content = this.preferenceDraft.trim();
    if (!content || this.preferencesLoading) {
      return;
    }
    this.preferencesLoading = true;
    try {
      const response = await fetch(`${this.apiBaseUrl}/agent/preferences`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, category: this.preferenceCategory }),
      });
      if (!response.ok) {
        throw new Error(await this.readError(response));
      }
      this.preferenceDraft = '';
      this.preferencesError = null;
      await this.loadPreferences();
    } catch (error: unknown) {
      this.preferencesError =
        error instanceof Error ? error.message : '偏好保存失败';
    } finally {
      this.preferencesLoading = false;
      this.cdr.detectChanges();
    }
  }

  startEditingPreference(preference: UserPreference): void {
    this.editingPreferenceId = preference.id;
    this.editingContent = preference.content;
  }

  cancelEditingPreference(): void {
    this.editingPreferenceId = null;
    this.editingContent = '';
  }

  async savePreferenceContent(preference: UserPreference): Promise<void> {
    const content = this.editingContent.trim();
    if (!content) {
      return;
    }
    await this.patchPreference(preference.id, { content });
    this.cancelEditingPreference();
  }

  async togglePreference(preference: UserPreference): Promise<void> {
    await this.patchPreference(preference.id, { enabled: !preference.enabled });
  }

  async deletePreference(preference: UserPreference): Promise<void> {
    if (this.preferencesLoading) {
      return;
    }
    this.preferencesLoading = true;
    try {
      const response = await fetch(
        `${this.apiBaseUrl}/agent/preferences/${preference.id}`,
        { method: 'DELETE' },
      );
      if (!response.ok) {
        throw new Error(await this.readError(response));
      }
      this.preferences = this.preferences.filter((item) => item.id !== preference.id);
      this.preferencesError = null;
    } catch (error: unknown) {
      this.preferencesError =
        error instanceof Error ? error.message : '偏好删除失败';
    } finally {
      this.preferencesLoading = false;
      this.cdr.detectChanges();
    }
  }

  categoryLabel(category: string): string {
    return (
      this.preferenceCategories.find((item) => item.id === category)?.label ?? '其他'
    );
  }

  askSuggestion(question: string): void {
    this.userQuestion = question;
    this.sendChat();
  }

  handleKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.sendChat();
    }
  }

  async sendChat(): Promise<void> {
    const question = this.userQuestion.trim();
    if (!question || this.isLoading) {
      return;
    }

    const now = Date.now();
    const userMessage: ChatMessage = {
      id: this.createId(),
      role: 'user',
      content: question,
    };
    const assistantMessage: ChatMessage = {
      id: this.createId(),
      role: 'assistant',
      content: '',
      status: '正在连接分析服务',
      activities: [],
      sources: [],
    };

    this.currentConversation.messages.push(userMessage, assistantMessage);
    this.currentConversation.updatedAt = now;
    if (this.currentConversation.title === '新建分析') {
      this.currentConversation.title =
        question.length > 28 ? `${question.slice(0, 28)}…` : question;
    }
    this.userQuestion = '';
    this.apiError = null;
    this.isLoading = true;
    this.agentStatus = '正在连接分析服务';
    const controller = new AbortController();
    this.abortController = controller;
    const runId = this.createId();
    this.activeRunId = runId;
    this.saveConversation(this.currentConversation);

    try {
      const response = await fetch(`${this.apiBaseUrl}/agent/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          thread_id: this.currentConversation.threadId,
          run_id: runId,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(await this.readError(response));
      }
      if (!response.body) {
        throw new Error('浏览器未能建立分析事件流，请重试');
      }

      await this.readEventStream(response.body, assistantMessage);
      if (!assistantMessage.content && !assistantMessage.error && !assistantMessage.cancelled) {
        assistantMessage.error = '分析连接已结束，但没有收到完整结果。请重试。';
      }
    } catch (error: unknown) {
      if (controller.signal.aborted) {
        assistantMessage.cancelled = true;
        assistantMessage.status = '已停止后续分析步骤';
      } else {
        assistantMessage.error =
          error instanceof Error ? error.message : '分析请求失败，请检查后端服务';
      }
    } finally {
      this.isLoading = false;
      this.agentStatus = '';
      this.abortController = undefined;
      this.activeRunId = undefined;
      this.currentConversation.updatedAt = Date.now();
      this.saveConversation(this.currentConversation);
      this.cdr.detectChanges();
    }
  }

  async stopChat(): Promise<void> {
    if (!this.isLoading || !this.abortController || !this.activeRunId) {
      return;
    }

    const runId = this.activeRunId;
    const controller = this.abortController;
    this.agentStatus = '正在请求停止；当前工具步骤结束后将停止后续分析';
    this.cdr.detectChanges();
    try {
      const response = await fetch(
        `${this.apiBaseUrl}/agent/chat/runs/${encodeURIComponent(runId)}/cancel`,
        { method: 'POST' },
      );
      if (!response.ok && response.status !== 404) {
        throw new Error(await this.readError(response));
      }
    } catch (error: unknown) {
      this.apiError =
        error instanceof Error
          ? `停止请求未能确认：${error.message}`
          : '停止请求未能确认；关闭连接后，后端会在当前步骤结束时停止';
    } finally {
      controller.abort();
    }
  }

  async copyAnswer(message: ChatMessage): Promise<void> {
    if (!message.content) {
      return;
    }

    try {
      await navigator.clipboard.writeText(message.content);
      this.copyStates[message.id] = 'copied';
    } catch {
      this.copyStates[message.id] = 'failed';
    }
    this.cdr.detectChanges();
    window.setTimeout(() => {
      this.copyStates[message.id] = 'idle';
      this.cdr.detectChanges();
    }, 1800);
  }

  trackById(_index: number, item: { id: string }): string {
    return item.id;
  }

  private async readEventStream(
    stream: ReadableStream<Uint8Array>,
    assistantMessage: ChatMessage,
  ): Promise<void> {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n');
      let boundary = buffer.indexOf('\n\n');
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        this.handleStreamEvent(this.parseEvent(block), assistantMessage);
        boundary = buffer.indexOf('\n\n');
      }
      if (done) {
        if (buffer.trim()) {
          this.handleStreamEvent(this.parseEvent(buffer), assistantMessage);
        }
        break;
      }
    }
  }

  private parseEvent(block: string): StreamEvent {
    let event = 'message';
    const dataLines: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) {
        event = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trim());
      }
    }

    if (!dataLines.length) {
      throw new Error('分析服务返回了无法识别的事件');
    }
    const parsed: unknown = JSON.parse(dataLines.join('\n'));
    if (typeof parsed !== 'object' || parsed === null) {
      throw new Error('分析服务返回了无效事件数据');
    }
    return { event, data: parsed as Record<string, unknown> };
  }

  private handleStreamEvent(event: StreamEvent, assistant: ChatMessage): void {
    const data = event.data;
    if (event.event === 'status') {
      const message = data['message'];
      if (typeof message === 'string') {
        assistant.status = message;
        this.agentStatus = message;
      }
    } else if (event.event === 'tool_start') {
      const toolId = typeof data['tool_id'] === 'string' ? data['tool_id'] : this.createId();
      const tool = typeof data['tool'] === 'string' ? data['tool'] : '分析工具';
      assistant.activities ??= [];
      const args = typeof data['args'] === 'string' ? data['args'] : '';
      assistant.activities.push({
        id: toolId,
        tool,
        status: 'running',
        ...(args ? { args } : {}),
      });
      this.agentStatus = `正在使用${tool}`;
    } else if (event.event === 'tool_end') {
      const toolId = data['tool_id'];
      const activity = assistant.activities?.find((item) => item.id === toolId);
      if (activity) {
        activity.status = 'complete';
        const output = data['output'];
        if (typeof output === 'string' && output) {
          activity.output = output;
        }
        if (data['error'] === true) {
          activity.error = true;
        }
        const duration = data['duration_ms'];
        if (typeof duration === 'number' && Number.isFinite(duration)) {
          activity.durationMs = duration;
        }
      }
      this.agentStatus = '工具步骤已完成，继续整理分析';
    } else if (event.event === 'complete') {
      const answer = data['answer'];
      assistant.content = typeof answer === 'string' ? answer : '';
      assistant.sources = Array.isArray(data['sources'])
        ? data['sources'].filter(
            (source): source is SourceLink =>
              typeof source === 'object' &&
              source !== null &&
              typeof (source as SourceLink).title === 'string' &&
              typeof (source as SourceLink).url === 'string',
          )
        : [];
      assistant.agentId = typeof data['agent_id'] === 'string' ? data['agent_id'] : null;
      assistant.model = typeof data['model'] === 'string' ? data['model'] : null;
      assistant.status = '分析完成';
    } else if (event.event === 'cancelled') {
      assistant.cancelled = true;
      assistant.status =
        typeof data['message'] === 'string' ? data['message'] : '已停止后续分析步骤';
    } else if (event.event === 'error') {
      assistant.error =
        typeof data['message'] === 'string' ? data['message'] : '分析失败，请重试';
    }
    this.currentConversation.updatedAt = Date.now();
    this.saveConversation(this.currentConversation);
    this.cdr.detectChanges();
  }

  private async readError(response: Response): Promise<string> {
    const body: unknown = await response.json().catch(() => null);
    if (
      typeof body === 'object' &&
      body !== null &&
      'detail' in body &&
      typeof body.detail === 'string'
    ) {
      return body.detail;
    }
    if (
      typeof body === 'object' &&
      body !== null &&
      'message' in body &&
      typeof body.message === 'string'
    ) {
      return body.message;
    }
    return `分析服务请求失败（HTTP ${response.status}）`;
  }

  private createConversation(): ChatConversation {
    return {
      threadId: this.createId(),
      title: '新建分析',
      updatedAt: Date.now(),
      messages: [],
    };
  }

  private createId(): string {
    return (
      globalThis.crypto?.randomUUID?.() ??
      `web-${Date.now()}-${Math.random().toString(36).slice(2)}`
    );
  }

  private loadConversations(): ChatConversation[] {
    try {
      const saved = localStorage.getItem(this.storageKey);
      if (!saved) {
        return [];
      }
      const parsed: unknown = JSON.parse(saved);
      if (!Array.isArray(parsed)) {
        throw new Error('会话数据格式不正确');
      }
      return parsed.filter(
        (item): item is ChatConversation =>
          typeof item === 'object' &&
          item !== null &&
          typeof item.threadId === 'string' &&
          typeof item.title === 'string' &&
          Array.isArray(item.messages),
      );
    } catch {
      this.storageWarning = '本地会话无法读取；当前分析仍可继续，但不会载入旧记录。';
      return [];
    }
  }

  private saveConversation(conversation: ChatConversation): void {
    if (!conversation.messages.length) {
      return;
    }
    const merged = this.applyOrder([
      conversation,
      ...this.conversations.filter((item) => item.threadId !== conversation.threadId),
    ]);

    // Only a bounded number of conversations keep their messages locally; the
    // rest stay in the sidebar as server-backed entries and load on demand.
    // They are never deleted here -- the server owns the data.
    let cached = 0;
    this.conversations = merged.slice(0, this.maxListedConversations).map((item) => {
      if (!item.messages.length) {
        return item;
      }
      if (cached < this.maxCachedConversations) {
        cached += 1;
        return item;
      }
      return { ...item, messages: [], remote: true };
    });
    this.persistConversations();
    this.saveOrder();
  }

  private loadOrder(): string[] {
    try {
      const raw = localStorage.getItem(this.orderKey);
      if (!raw) {
        return [];
      }
      const parsed: unknown = JSON.parse(raw);
      return Array.isArray(parsed)
        ? parsed.filter((item): item is string => typeof item === 'string')
        : [];
    } catch {
      return [];
    }
  }

  private saveOrder(): void {
    try {
      localStorage.setItem(
        this.orderKey,
        JSON.stringify(
          this.conversations
            .slice(0, this.maxListedConversations)
            .map((item) => item.threadId),
        ),
      );
    } catch {
      // Ordering is a preference; failing to store it must not break the UI.
    }
  }

  /**
   * Put the list in the user's saved order.
   *
   * Threads with a saved position keep it, so an older conversation that
   * receives a new message does not jump to the top. Threads that are not in
   * the saved order (new conversations, history discovered from the server)
   * go first, newest first, until the user drags them.
   */
  private applyOrder(list: ChatConversation[]): ChatConversation[] {
    const order = this.loadOrder();
    const rank = new Map(order.map((threadId, index) => [threadId, index]));
    const known = list
      .filter((item) => rank.has(item.threadId))
      .sort((left, right) => (rank.get(left.threadId) ?? 0) - (rank.get(right.threadId) ?? 0));
    const unknown = list
      .filter((item) => !rank.has(item.threadId))
      .sort((left, right) => right.updatedAt - left.updatedAt);
    return [...unknown, ...known];
  }

  /** Merge the server's conversation list into the local one. */
  private async syncFromServer(): Promise<void> {
    try {
      const response = await fetch(
        `${this.apiBaseUrl}/agent/threads?limit=${this.maxListedConversations}`,
      );
      if (!response.ok) {
        return;
      }
      const body: unknown = await response.json();
      const list =
        typeof body === 'object' && body !== null
          ? (body as { data?: { threads?: unknown } }).data?.threads
          : undefined;
      if (!Array.isArray(list) || !list.length) {
        return;
      }

      const byId = new Map(this.conversations.map((item) => [item.threadId, item]));
      for (const raw of list) {
        if (typeof raw !== 'object' || raw === null) {
          continue;
        }
        const record = raw as {
          thread_id?: unknown;
          title?: unknown;
          updated_at?: unknown;
        };
        if (typeof record.thread_id !== 'string' || !record.thread_id) {
          continue;
        }
        const updatedAt = Date.parse(String(record.updated_at ?? '')) || Date.now();
        const title =
          typeof record.title === 'string' && record.title ? record.title : '未命名会话';
        const existing = byId.get(record.thread_id);
        if (existing) {
          existing.remote = true;
          existing.updatedAt = Math.max(existing.updatedAt, updatedAt);
          if (!existing.messages.length || existing.title === '新建分析') {
            existing.title = title;
          }
        } else {
          byId.set(record.thread_id, {
            threadId: record.thread_id,
            title,
            updatedAt,
            messages: [],
            remote: true,
          });
        }
      }

      this.conversations = this.applyOrder(Array.from(byId.values())).slice(
        0,
        this.maxListedConversations,
      );
      this.persistConversations();
      this.saveOrder();
      this.cdr.detectChanges();
    } catch {
      // Offline, backend down, or no server history yet: local history still
      // works, so this is intentionally silent.
    }
  }

  private async loadConversationMessages(conversation: ChatConversation): Promise<void> {
    this.conversationLoading = true;
    this.cdr.detectChanges();
    try {
      const response = await fetch(
        `${this.apiBaseUrl}/agent/threads/${encodeURIComponent(conversation.threadId)}/messages`,
      );
      if (response.status === 404) {
        conversation.remote = false;
        return;
      }
      if (!response.ok) {
        throw new Error(await this.readError(response));
      }
      const body: unknown = await response.json();
      const list =
        typeof body === 'object' && body !== null
          ? (body as { data?: { messages?: unknown } }).data?.messages
          : undefined;
      const messages = Array.isArray(list)
        ? list
            .map((item) => this.toChatMessage(item))
            .filter((item): item is ChatMessage => item !== null)
        : [];
      conversation.messages = messages;
      conversation.remote = false;
      this.saveConversation(conversation);
    } catch (error: unknown) {
      this.apiError =
        error instanceof Error ? error.message : '会话记录载入失败';
    } finally {
      this.conversationLoading = false;
      this.cdr.detectChanges();
    }
  }

  private toChatMessage(raw: unknown): ChatMessage | null {
    if (typeof raw !== 'object' || raw === null) {
      return null;
    }
    const record = raw as {
      role?: unknown;
      content?: unknown;
      sources?: unknown;
    };
    if (typeof record.content !== 'string') {
      return null;
    }
    const sources = Array.isArray(record.sources)
      ? record.sources.filter(
          (source): source is SourceLink =>
            typeof source === 'object' &&
            source !== null &&
            typeof (source as SourceLink).title === 'string' &&
            typeof (source as SourceLink).url === 'string',
        )
      : [];
    return {
      id: this.createId(),
      role: record.role === 'user' ? 'user' : 'assistant',
      content: record.content,
      sources,
    };
  }

  private persistConversations(): void {
    try {
      localStorage.setItem(this.storageKey, JSON.stringify(this.conversations));
      this.storageWarning = null;
    } catch {
      this.storageWarning = '浏览器无法保存会话记录；可继续对话，但刷新后记录可能丢失。';
    }
  }

  /**
   * Ask the server to forget the thread's checkpoints and offloaded history.
   *
   * The local copy is already gone at this point, so a failure here only
   * leaves stale state on the server -- it must not surface as an error in
   * the UI.
   */
  private async releaseThreadOnServer(threadId: string): Promise<void> {
    try {
      await fetch(`${this.apiBaseUrl}/agent/threads/${encodeURIComponent(threadId)}`, {
        method: 'DELETE',
      });
    } catch {
      // The conversation is removed locally regardless; server-side cleanup
      // is best effort.
    }
  }

  private async loadPreferences(): Promise<void> {
    this.preferencesLoading = true;
    try {
      const response = await fetch(`${this.apiBaseUrl}/agent/preferences`);
      if (!response.ok) {
        throw new Error(await this.readError(response));
      }
      const body: unknown = await response.json();
      const data =
        typeof body === 'object' && body !== null
          ? (body as { data?: { preferences?: unknown } }).data
          : undefined;
      const list = Array.isArray(data?.preferences) ? data.preferences : [];
      this.preferences = list.filter(
        (item): item is UserPreference =>
          typeof item === 'object' &&
          item !== null &&
          typeof (item as UserPreference).id === 'number' &&
          typeof (item as UserPreference).content === 'string',
      );
      this.preferencesError = null;
    } catch (error: unknown) {
      this.preferencesError =
        error instanceof Error ? error.message : '偏好加载失败';
    } finally {
      this.preferencesLoading = false;
      this.cdr.detectChanges();
    }
  }

  private async patchPreference(
    preferenceId: number,
    changes: { content?: string; enabled?: boolean },
  ): Promise<void> {
    if (this.preferencesLoading) {
      return;
    }
    this.preferencesLoading = true;
    try {
      const response = await fetch(
        `${this.apiBaseUrl}/agent/preferences/${preferenceId}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(changes),
        },
      );
      if (!response.ok) {
        throw new Error(await this.readError(response));
      }
      const body: unknown = await response.json();
      const updated =
        typeof body === 'object' && body !== null
          ? (body as { data?: { preference?: UserPreference } }).data?.preference
          : undefined;
      if (updated) {
        this.preferences = this.preferences.map((item) =>
          item.id === updated.id ? updated : item,
        );
      }
      this.preferencesError = null;
    } catch (error: unknown) {
      this.preferencesError =
        error instanceof Error ? error.message : '偏好更新失败';
    } finally {
      this.preferencesLoading = false;
      this.cdr.detectChanges();
    }
  }

  private safeFileName(title: string): string {
    const cleaned = title.replace(/[\\/:*?"<>|\s]+/g, '_').replace(/^_+|_+$/g, '');
    return cleaned || 'conversation';
  }

  private downloadText(content: string, fileName: string): void {
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = fileName;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }
}
