import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

interface AgentOption {
  id: string;
  name: string;
  description: string;
  skills: { id: string; name: string }[];
}

interface CommandOption {
  id: string;
  name: string;
  description: string;
  agent_ids: string[];
}

interface AgentCatalog {
  agents: AgentOption[];
  commands: CommandOption[];
  default_agent_id: string;
  default_command_id: string | null;
}

@Component({
  selector: 'app-stock-chat',
  imports: [CommonModule, FormsModule],
  templateUrl: './stock-chat.component.html',
  styleUrl: './stock-chat.component.css',
})
export class StockChatComponent implements OnInit {
  userQuestion = '你好';
  chatAnswer = '';
  isLoading = false;
  apiError: string | null = null;
  catalogError: string | null = null;
  agents: AgentOption[] = [];
  commands: CommandOption[] = [];
  selectedAgentId = '';
  selectedCommandId = '';
  private readonly threadId =
    globalThis.crypto?.randomUUID?.() ?? `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;

  constructor(private http: HttpClient, private cdr: ChangeDetectorRef) {}

  ngOnInit(): void {
    this.loadCatalog();
  }

  get availableCommands(): CommandOption[] {
    return this.commands.filter(
      (command) => command.agent_ids.length === 0 || command.agent_ids.includes(this.selectedAgentId),
    );
  }

  get selectedAgentDescription(): string {
    return this.agents.find((agent) => agent.id === this.selectedAgentId)?.description ?? '';
  }

  get selectedCommandDescription(): string {
    return this.availableCommands.find((command) => command.id === this.selectedCommandId)?.description ?? '';
  }

  onAgentChange(): void {
    if (!this.availableCommands.some((command) => command.id === this.selectedCommandId)) {
      this.selectedCommandId = this.availableCommands[0]?.id ?? '';
    }
  }

  loadCatalog(): void {
    this.catalogError = null;
    this.http.get<{ code: number; data: AgentCatalog }>('http://127.0.0.1:8015/agent/catalog').subscribe({
      next: (res) => {
        this.agents = res.data.agents;
        this.commands = res.data.commands;
        this.selectedAgentId = res.data.default_agent_id;
        this.selectedCommandId = res.data.default_command_id ?? '';
        if (!this.selectedCommandId && this.availableCommands.length) {
          this.selectedCommandId = this.availableCommands[0].id;
        }
        this.cdr.detectChanges();
      },
      error: () => {
        this.catalogError = '无法加载 Agent 和命令目录，请确认后端服务已启动';
        this.cdr.detectChanges();
      },
    });
  }

  sendChat(): void {
    if (!this.selectedAgentId || !this.userQuestion.trim()) {
      return;
    }

    this.isLoading = true;
    this.apiError = null;
    const url = 'http://127.0.0.1:8015/agent/chat';

    const postData = {
      question: this.userQuestion,
      agent_id: this.selectedAgentId,
      command_id: this.selectedCommandId || null,
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
