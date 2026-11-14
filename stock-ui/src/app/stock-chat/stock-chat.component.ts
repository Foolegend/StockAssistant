import { Component, ChangeDetectorRef } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { MarkdownPipe } from '../shared/markdown.pipe';

@Component({
  selector: 'app-stock-chat',
  imports: [CommonModule, FormsModule, MarkdownPipe],
  templateUrl: './stock-chat.component.html',
  styleUrl: './stock-chat.component.css',
})
export class StockChatComponent {
  userQuestion = '你好';
  chatAnswer = '';
  rawAnswer = '';
  copyState: 'idle' | 'copied' | 'failed' = 'idle';
  isLoading = false;
  apiError: string | null = null;
  private readonly threadId =
    globalThis.crypto?.randomUUID?.() ?? `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;

  constructor(private http: HttpClient, private cdr: ChangeDetectorRef) {}

  sendChat(): void {
    if (!this.userQuestion.trim()) {
      return;
    }

    this.isLoading = true;
    this.apiError = null;
    const url = 'http://127.0.0.1:8015/agent/chat';

    const postData = {
      question: this.userQuestion,
      thread_id: this.threadId,
    };

    this.http.post<{ code: number; message?: string; data?: { answer: string } }>(url, postData).subscribe({
      next: (res) => {
        if (res.code === 0 && res.data) {
          this.chatAnswer = res.data.answer;
          this.rawAnswer = res.data.answer;
        } else {
          this.apiError = res.message || '接口返回错误';
        }
        this.isLoading = false;
        this.cdr.detectChanges();
      },
      error: (error: unknown) => {
        this.apiError = this.getApiErrorMessage(error);
        this.isLoading = false;
        this.cdr.detectChanges();
      }
    });
  }

  get copyLabel(): string {
    if (this.copyState === 'copied') {
      return '已复制';
    }
    if (this.copyState === 'failed') {
      return '复制失败';
    }
    return '复制原文';
  }

  async copyAnswer(): Promise<void> {
    if (!this.rawAnswer) {
      return;
    }

    try {
      await navigator.clipboard.writeText(this.rawAnswer);
      this.copyState = 'copied';
    } catch {
      this.copyState = 'failed';
    }

    this.cdr.detectChanges();
    window.setTimeout(() => {
      this.copyState = 'idle';
      this.cdr.detectChanges();
    }, 2000);
  }

  private getApiErrorMessage(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      const body: unknown = error.error;
      if (
        typeof body === 'object' &&
        body !== null &&
        'detail' in body &&
        typeof body.detail === 'string' &&
        body.detail.trim()
      ) {
        return body.detail;
      }
      if (typeof body === 'string' && body.trim()) {
        return body;
      }
      return `调用 Agent 失败（HTTP ${error.status}），请检查后端服务和扩展配置`;
    }

    if (error instanceof Error && error.message) {
      return `调用 Agent 失败：${error.message}`;
    }
    return '调用 Agent 失败，请检查后端服务和扩展配置';
  }
}
