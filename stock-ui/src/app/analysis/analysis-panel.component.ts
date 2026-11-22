import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  Input,
  OnChanges,
  OnDestroy,
  SimpleChanges,
} from '@angular/core';
import { MarkdownPipe } from '../shared/markdown.pipe';

interface AnalysisMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: string | null;
}

interface AnalysisResponse {
  answer: string | null;
  updated_at: string | null;
  messages: AnalysisMessage[];
}

/**
 * Web view of the latest assistant answer from the active terminal session.
 *
 * The terminal itself shows the full Ink UI; this panel polls the session's
 * CLI transcript and renders the final Markdown answer as a readable page.
 */
@Component({
  selector: 'app-analysis-panel',
  imports: [MarkdownPipe],
  templateUrl: './analysis-panel.component.html',
  styleUrl: './analysis-panel.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AnalysisPanelComponent implements OnChanges, OnDestroy {
  @Input() sessionId: string | null = null;

  answer: string | null = null;
  updatedAt: string | null = null;
  loading = false;
  error: string | null = null;

  private timer?: ReturnType<typeof setInterval>;
  private disposed = false;
  private fetching = false;

  constructor(private readonly cdr: ChangeDetectorRef) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (!changes['sessionId']) return;
    this.restart();
  }

  ngOnDestroy(): void {
    this.disposed = true;
    if (this.timer) clearInterval(this.timer);
  }

  refresh(): void {
    void this.fetchAnalysis();
  }

  get metaText(): string {
    if (this.error) return '读取失败';
    if (this.updatedAt) {
      const date = new Date(this.updatedAt);
      return Number.isNaN(date.getTime())
        ? '已更新'
        : date.toLocaleString();
    }
    if (!this.sessionId) return '未选择会话';
    return this.loading ? '加载中…' : '等待分析';
  }

  private restart(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = undefined;
    }
    this.answer = null;
    this.updatedAt = null;
    this.error = null;
    this.loading = Boolean(this.sessionId);
    this.cdr.markForCheck();
    if (!this.sessionId) return;

    void this.fetchAnalysis();
    this.timer = setInterval(() => void this.fetchAnalysis(), 3000);
  }

  private async fetchAnalysis(): Promise<void> {
    const id = this.sessionId;
    if (!id || this.disposed || this.fetching) return;

    this.fetching = true;
    try {
      const response = await fetch(
        `http://127.0.0.1:8015/sessions/${encodeURIComponent(id)}/analysis`,
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = (await response.json()) as AnalysisResponse;
      if (this.disposed || this.sessionId !== id) return;

      this.answer = body.answer ?? null;
      this.updatedAt = body.updated_at ?? null;
      this.error = null;
    } catch {
      if (!this.disposed && this.sessionId === id) {
        this.error = '暂时无法读取分析结果';
      }
    } finally {
      this.fetching = false;
      this.loading = false;
      this.cdr.markForCheck();
    }
  }
}
