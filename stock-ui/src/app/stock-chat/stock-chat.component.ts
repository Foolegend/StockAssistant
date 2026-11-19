import { ChangeDetectorRef, Component } from '@angular/core';
import { CommonModule } from '@angular/common';
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
  commandId?: string | null;
  model?: string | null;
}

interface ChatConversation {
  threadId: string;
  title: string;
  updatedAt: number;
  messages: ChatMessage[];
}

interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}

@Component({
  selector: 'app-stock-chat',
  imports: [CommonModule, FormsModule, MarkdownPipe],
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

  conversations: ChatConversation[] = [];
  currentConversation: ChatConversation;
  userQuestion = '';
  isLoading = false;
  agentStatus = '';
  apiError: string | null = null;
  storageWarning: string | null = null;

  private abortController?: AbortController;
  private activeRunId?: string;
  private readonly storageKey = 'stock-assistant-conversations-v1';

  constructor(private cdr: ChangeDetectorRef) {
    this.conversations = this.loadConversations();
    this.currentConversation = this.createConversation();
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

  openConversation(conversation: ChatConversation): void {
    if (this.isLoading) {
      return;
    }
    this.currentConversation = conversation;
    this.userQuestion = '';
    this.apiError = null;
    this.agentStatus = '';
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
      assistant.activities.push({ id: toolId, tool, status: 'running' });
      this.agentStatus = `正在使用${tool}`;
    } else if (event.event === 'tool_end') {
      const toolId = data['tool_id'];
      const activity = assistant.activities?.find((item) => item.id === toolId);
      if (activity) {
        activity.status = 'complete';
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
      assistant.commandId =
        typeof data['command_id'] === 'string' ? data['command_id'] : null;
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
    this.conversations = [
      conversation,
      ...this.conversations.filter((item) => item.threadId !== conversation.threadId),
    ]
      .sort((left, right) => right.updatedAt - left.updatedAt)
      .slice(0, 30);
    try {
      localStorage.setItem(this.storageKey, JSON.stringify(this.conversations));
      this.storageWarning = null;
    } catch {
      this.storageWarning = '浏览器无法保存会话记录；可继续对话，但刷新后记录可能丢失。';
    }
  }
}
