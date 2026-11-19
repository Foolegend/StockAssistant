import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';

import { StockChatComponent } from './stock-chat.component';

describe('StockChatComponent', () => {
  let component: StockChatComponent;
  let fixture: ComponentFixture<StockChatComponent>;

  beforeEach(async () => {
    localStorage.removeItem('stock-assistant-conversations-v1');
    await TestBed.configureTestingModule({
      imports: [StockChatComponent],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();

    fixture = TestBed.createComponent(StockChatComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('creates a conversation-first workspace', () => {
    expect(component).toBeTruthy();
    expect(fixture.nativeElement.textContent).toContain('把市场问题，聊清楚。');
    expect(fixture.nativeElement.querySelector('.evidence-panel')).toBeTruthy();
  });

  it('streams real tool activity, renders the answer, and saves local history', async () => {
    const stream = [
      { event: 'status', data: { stage: 'routing', message: '正在判断分析能力' } },
      { event: 'tool_start', data: { tool_id: 'tool-1', tool: '查询行情' } },
      { event: 'tool_end', data: { tool_id: 'tool-1', tool: '查询行情' } },
      {
        event: 'complete',
        data: {
          answer: '## 分析摘要\n\n参考 [公司公告](https://example.com/report)。',
          sources: [{ title: '公司公告', url: 'https://example.com/report' }],
          agent_id: 'stock-analyst',
          command_id: null,
          model: 'test-model',
        },
      },
    ]
      .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
      .join('');
    spyOn(window, 'fetch').and.resolveTo(new Response(stream));

    component.userQuestion = '分析 TSLA';
    await component.sendChat();
    fixture.detectChanges();

    expect(window.fetch).toHaveBeenCalledWith(
      `${component.apiBaseUrl}/agent/chat/stream`,
      jasmine.objectContaining({
        method: 'POST',
        body: jasmine.stringMatching(/"question":"分析 TSLA"/),
      }),
    );
    expect(component.currentConversation.messages[1].content).toContain('分析摘要');
    expect(component.currentActivities[0]).toEqual({
      id: 'tool-1',
      tool: '查询行情',
      status: 'complete',
    });
    expect(component.currentSources[0].url).toBe('https://example.com/report');
    expect(fixture.nativeElement.querySelector('.answer-content h2')?.textContent).toContain(
      '分析摘要',
    );
    expect(localStorage.getItem('stock-assistant-conversations-v1')).toContain('分析 TSLA');
  });

  it('shows backend errors instead of a success-shaped answer', async () => {
    spyOn(window, 'fetch').and.resolveTo(
      new Response(JSON.stringify({ detail: 'Agent 服务暂不可用' }), {
        status: 502,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    component.userQuestion = '分析中兴通讯';
    await component.sendChat();
    fixture.detectChanges();

    expect(component.currentConversation.messages[1].error).toBe('Agent 服务暂不可用');
    expect(fixture.nativeElement.querySelector('.message-error')?.textContent).toContain(
      'Agent 服务暂不可用',
    );
  });

  it('starts a new thread without discarding earlier conversations', async () => {
    const stream = 'event: complete\ndata: {"answer":"完成","sources":[]}\n\n';
    spyOn(window, 'fetch').and.resolveTo(new Response(stream));
    component.userQuestion = '第一次分析';
    await component.sendChat();
    const firstThread = component.currentConversation.threadId;

    component.newChat();
    expect(component.currentConversation.threadId).not.toBe(firstThread);
    expect(component.conversations[0].threadId).toBe(firstThread);
  });
});
