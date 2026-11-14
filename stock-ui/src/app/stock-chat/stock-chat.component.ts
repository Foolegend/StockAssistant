import { Component, ChangeDetectorRef } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-stock-chat',
  imports: [CommonModule, FormsModule],
  templateUrl: './stock-chat.component.html',
  styleUrl: './stock-chat.component.css',
})
export class StockChatComponent {
  userQuestion = '你好';
  chatAnswer = '';
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
        } else {
          this.apiError = res.message || '接口返回错误';
        }
        this.isLoading = false;
        this.cdr.detectChanges();
      },
      error: () => {
        this.apiError = '调用 Agent 失败，请检查后端服务和扩展配置';
        this.isLoading = false;
        this.cdr.detectChanges();
      }
    });
  }
}
