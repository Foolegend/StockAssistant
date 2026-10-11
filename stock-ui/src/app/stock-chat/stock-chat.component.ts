import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { CommonModule, JsonPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-stock-chat',
  imports: [CommonModule, JsonPipe, FormsModule],
  templateUrl: './stock-chat.component.html',
  styleUrl: './stock-chat.component.css',
})
export class StockChatComponent implements OnInit {
// ...existing code...

  // 用户输入的问题
  userQuestion: string = '你好';
  // 接收后端返回的答案
  chatAnswer: string = '';
  // 接口状态
  isLoading: boolean = false;
  apiError: string | null = null;

  // 注入 HttpClient
  constructor(private http: HttpClient, private cdr: ChangeDetectorRef) { }

  ngOnInit(): void {
    // 页面加载时调用 Agent Chat
    this.sendChat();
  }

  // 调用 Agent Chat 接口
  sendChat() {
    this.isLoading = true;
    this.apiError = null;
    const url = 'http://127.0.0.1:8015/agent/chat';

    const postData = {
      question: this.userQuestion,
      model: 'gpt-oss:120b-cloud',
      use_skills: true,
      use_mcp: false,
      thread_id: 'demo-angular'
    };

    this.http.post<any>(url, postData).subscribe({
      next: (res) => {
        console.log('Agent 返回:', res);
        if (res.code === 0 && res.data) {
          this.chatAnswer = res.data.answer;
        } else {
          this.apiError = res.message || '接口返回错误';
        }
        this.isLoading = false;
        this.cdr.detectChanges();
      },
      error: (err) => {
        console.error('调用失败:', err);
        this.apiError = '无法连接到后端服务，请确认服务已在 8015 端口启动';
        this.isLoading = false;
        this.cdr.detectChanges();
      }
    });
  }
}
