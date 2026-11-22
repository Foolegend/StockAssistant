import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';

import { StockChatComponent } from './stock-chat.component';

describe('StockChatComponent', () => {
  let component: StockChatComponent;
  let fixture: ComponentFixture<StockChatComponent>;

  beforeEach(async () => {
    localStorage.removeItem('stock-assistant-conversations-v1');
    localStorage.removeItem('stock-assistant-conversation-order-v1');
    await TestBed.configureTestingModule({
      imports: [StockChatComponent],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();

    // The component merges the server's conversation list on construction.
    // Default to an empty list; individual tests override the behaviour.
    spyOn(window, 'fetch').and.resolveTo(
      new Response('{"code":0,"data":{"threads":[]}}', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

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
      {
        event: 'tool_start',
        data: { tool_id: 'tool-1', tool: '查询行情', args: '{"symbol": "TSLA"}' },
      },
      {
        event: 'tool_end',
        data: {
          tool_id: 'tool-1',
          tool: '查询行情',
          output: '{"price": 100}',
          error: false,
          duration_ms: 12,
        },
      },
      {
        event: 'complete',
        data: {
          answer: '## 分析摘要\n\n参考 [公司公告](https://example.com/report)。',
          sources: [{ title: '公司公告', url: 'https://example.com/report' }],
          agent_id: 'investment-analyst',
          model: 'test-model',
        },
      },
    ]
      .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
      .join('');
    (window.fetch as jasmine.Spy).and.resolveTo(new Response(stream));

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
      args: '{"symbol": "TSLA"}',
      output: '{"price": 100}',
      durationMs: 12,
    });
    const detailBlocks: HTMLElement[] = Array.from(
      fixture.nativeElement.querySelectorAll('.activity-detail pre'),
    );
    expect(detailBlocks.map((block) => block.textContent)).toEqual([
      '{"symbol": "TSLA"}',
      '{"price": 100}',
    ]);
    expect(component.currentSources[0].url).toBe('https://example.com/report');
    expect(fixture.nativeElement.querySelector('.answer-content h2')?.textContent).toContain(
      '分析摘要',
    );
    expect(localStorage.getItem('stock-assistant-conversations-v1')).toContain('分析 TSLA');
  });

  it('offers a copy button for the user question', async () => {
    const stream = 'event: complete\ndata: {"answer":"完成","sources":[]}\n\n';
    (window.fetch as jasmine.Spy).and.resolveTo(new Response(stream));
    const writeText = jasmine.createSpy('writeText').and.resolveTo();
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    component.userQuestion = '帮我分析中兴通讯的财报';
    await component.sendChat();
    fixture.detectChanges();

    const copyButton: HTMLButtonElement | null = fixture.nativeElement.querySelector(
      '.user-row .copy-button',
    );
    expect(copyButton).toBeTruthy();

    copyButton?.click();
    await fixture.whenStable();
    expect(writeText).toHaveBeenCalledWith('帮我分析中兴通讯的财报');
  });

  it('keeps long tool steps from squeezing the conversation column', () => {
    // The Karma viewport is narrower than the desktop breakpoint, so force the
    // desktop layout to exercise the side-by-side flex layout.
    const desktopLayout = document.createElement('style');
    desktopLayout.textContent = `
      .workspace { display: flex !important; }
      .sidebar { display: block !important; }
      .content-layout { display: flex !important; }
    `;
    document.head.appendChild(desktopLayout);

    try {
      const longCommand = `python -c "${'x'.repeat(400)}"`;
      component.currentConversation.messages = [
        {
          id: 'assistant-1',
          role: 'assistant',
          content: '',
          activities: [
            {
              id: 'tool-1',
              tool: 'execute',
              status: 'complete',
              args: JSON.stringify({ command: longCommand }),
            },
          ],
        },
      ];
      fixture.detectChanges();

      const conversation = fixture.nativeElement.querySelector(
        '.conversation-panel',
      ) as HTMLElement;
      const evidence = fixture.nativeElement.querySelector('.evidence-panel') as HTMLElement;
      expect(conversation.getBoundingClientRect().width).toBeGreaterThan(150);
      expect(evidence.getBoundingClientRect().width).toBeLessThan(320);
    } finally {
      desktopLayout.remove();
    }
  });

  it('shows the local command and working directory for a tool step', async () => {
    const stream = [
      {
        event: 'tool_start',
        data: {
          tool_id: 'tool-2',
          tool: 'execute',
          args: '{"command":"python probe.py"}',
          command: 'python probe.py',
          cwd: 'D:\\repo',
        },
      },
      {
        event: 'tool_end',
        data: { tool_id: 'tool-2', tool: 'execute', output: 'ok', duration_ms: 5 },
      },
      { event: 'complete', data: { answer: '完成', sources: [] } },
    ]
      .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
      .join('');
    (window.fetch as jasmine.Spy).and.resolveTo(new Response(stream));

    component.userQuestion = '跑一下';
    await component.sendChat();
    fixture.detectChanges();

    const inputBlock: HTMLElement | null =
      fixture.nativeElement.querySelector('.io-block');
    expect(inputBlock).toBeTruthy();
    expect(inputBlock?.querySelector('.io-label')?.textContent).toContain('IN');
    expect(inputBlock?.querySelector('pre')?.textContent).toContain('python probe.py');
    expect(inputBlock?.querySelector('.io-cwd')?.textContent).toContain(
      '工作目录：D:\\repo',
    );
    expect(fixture.nativeElement.querySelector('.activity-desc')?.textContent).toContain(
      'python probe.py',
    );

    const outputBlock = fixture.nativeElement.querySelectorAll('.io-block')[1] as HTMLElement;
    expect(outputBlock?.querySelector('.io-label')?.textContent).toContain('OUT');
    expect(outputBlock?.querySelector('pre')?.textContent).toContain('ok');
  });

  it('shows backend errors instead of a success-shaped answer', async () => {
    (window.fetch as jasmine.Spy).and.resolveTo(
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
    (window.fetch as jasmine.Spy).and.resolveTo(new Response(stream));
    component.userQuestion = '第一次分析';
    await component.sendChat();
    const firstThread = component.currentConversation.threadId;

    component.newChat();
    expect(component.currentConversation.threadId).not.toBe(firstThread);
    expect(component.conversations[0].threadId).toBe(firstThread);
  });

  it('deletes a conversation from the list and from local storage', async () => {
    const stream = 'event: complete\ndata: {"answer":"完成","sources":[]}\n\n';
    (window.fetch as jasmine.Spy).and.resolveTo(new Response(stream));
    component.userQuestion = '待删除的分析';
    await component.sendChat();
    const threadId = component.currentConversation.threadId;
    expect(component.conversations.length).toBe(1);

    component.deleteConversation(component.conversations[0]);
    fixture.detectChanges();

    expect(component.conversations.length).toBe(0);
    expect(localStorage.getItem('stock-assistant-conversations-v1')).not.toContain(threadId);
    // Deleting the open conversation starts a fresh one.
    expect(component.currentConversation.threadId).not.toBe(threadId);
    expect(component.currentConversation.messages.length).toBe(0);
  });

  it('keeps the open conversation when a different one is deleted', async () => {
    const stream = 'event: complete\ndata: {"answer":"完成","sources":[]}\n\n';
    (window.fetch as jasmine.Spy).and.resolveTo(new Response(stream));
    component.userQuestion = '第一条';
    await component.sendChat();
    const firstThread = component.currentConversation.threadId;

    component.newChat();
    component.userQuestion = '第二条';
    await component.sendChat();
    const secondThread = component.currentConversation.threadId;
    expect(component.conversations.length).toBe(2);

    component.deleteConversation(
      component.conversations.find((item) => item.threadId === firstThread)!,
    );
    fixture.detectChanges();

    expect(component.conversations.length).toBe(1);
    expect(component.conversations[0].threadId).toBe(secondThread);
    expect(component.currentConversation.threadId).toBe(secondThread);
  });

  it('asks the server to release the deleted thread', async () => {
    const stream = 'event: complete\ndata: {"answer":"完成","sources":[]}\n\n';
    const fetchSpy = (window.fetch as jasmine.Spy).and.resolveTo(new Response(stream));
    component.userQuestion = '需要释放';
    await component.sendChat();
    const threadId = component.currentConversation.threadId;

    component.deleteConversation(component.conversations[0]);
    await fixture.whenStable();

    expect(fetchSpy).toHaveBeenCalledWith(
      `${component.apiBaseUrl}/agent/threads/${encodeURIComponent(threadId)}`,
      jasmine.objectContaining({ method: 'DELETE' }),
    );
  });

  it('clears every conversation after confirmation', async () => {
    const stream = 'event: complete\ndata: {"answer":"完成","sources":[]}\n\n';
    (window.fetch as jasmine.Spy).and.resolveTo(new Response(stream));
    component.userQuestion = '第一条';
    await component.sendChat();
    component.newChat();
    component.userQuestion = '第二条';
    await component.sendChat();
    expect(component.conversations.length).toBe(2);

    spyOn(window, 'confirm').and.returnValue(true);
    component.confirmClearAll();
    fixture.detectChanges();

    expect(component.conversations.length).toBe(0);
    expect(component.currentConversation.messages.length).toBe(0);
    expect(localStorage.getItem('stock-assistant-conversations-v1')).not.toContain('第一条');
  });

  it('keeps conversations when the clear confirmation is declined', async () => {
    const stream = 'event: complete\ndata: {"answer":"完成","sources":[]}\n\n';
    (window.fetch as jasmine.Spy).and.resolveTo(new Response(stream));
    component.userQuestion = '保留';
    await component.sendChat();

    spyOn(window, 'confirm').and.returnValue(false);
    component.confirmClearAll();

    expect(component.conversations.length).toBe(1);
  });
});
