import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';

import { StockChatComponent } from './stock-chat.component';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('StockChatComponent server history', () => {
  let component: StockChatComponent;
  let fixture: ComponentFixture<StockChatComponent>;

  beforeEach(async () => {
    localStorage.removeItem('stock-assistant-conversations-v1');
    await TestBed.configureTestingModule({
      imports: [StockChatComponent],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();

    spyOn(window, 'fetch').and.resolveTo(
      jsonResponse({ code: 0, data: { threads: [] } }),
    );

    fixture = TestBed.createComponent(StockChatComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('merges server conversations into the sidebar as stubs', async () => {
    (window.fetch as jasmine.Spy).and.resolveTo(
      jsonResponse({
        code: 0,
        data: {
          threads: [
            {
              thread_id: 'server-1',
              title: '服务端会话',
              message_count: 4,
              updated_at: '2026-11-20T12:00:00+00:00',
            },
          ],
        },
      }),
    );

    await (component as unknown as { syncFromServer: () => Promise<void> }).syncFromServer();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(component.conversations.length).toBe(1);
    const conversation = component.conversations[0];
    expect(conversation.threadId).toBe('server-1');
    expect(conversation.title).toBe('服务端会话');
    expect(conversation.messages).toEqual([]);
    expect(conversation.remote).toBeTrue();
  });

  it('hydrates a server-backed conversation when opened', async () => {
    component.conversations = [
      {
        threadId: 'server-2',
        title: '按需载入',
        updatedAt: Date.now(),
        messages: [],
        remote: true,
      },
    ];

    (window.fetch as jasmine.Spy).and.resolveTo(
      jsonResponse({
        code: 0,
        data: {
          messages: [
            { role: 'user', content: '分析 TSLA', created_at: '2026-11-20T12:00:00Z' },
            {
              role: 'assistant',
              content: '参考 [公告](https://example.com/a)',
              sources: [{ title: '公告', url: 'https://example.com/a' }],
              created_at: '2026-11-20T12:00:05Z',
            },
          ],
        },
      }),
    );

    await component.openConversation(component.conversations[0]);
    await fixture.whenStable();
    fixture.detectChanges();

    const conversation = component.conversations[0];
    expect(conversation.remote).toBeFalse();
    expect(conversation.messages.length).toBe(2);
    expect(conversation.messages[0].role).toBe('user');
    expect(conversation.messages[1].content).toContain('公告');
    expect(conversation.messages[1].sources?.[0].url).toBe('https://example.com/a');
    expect(fixture.nativeElement.textContent).toContain('分析 TSLA');
  });

  it('treats a missing server record as an empty local conversation', async () => {
    component.conversations = [
      {
        threadId: 'gone',
        title: '已过期',
        updatedAt: Date.now(),
        messages: [],
        remote: true,
      },
    ];
    (window.fetch as jasmine.Spy).and.resolveTo(
      jsonResponse({ detail: '没有记录' }, 404),
    );

    await component.openConversation(component.conversations[0]);
    await fixture.whenStable();

    expect(component.conversations[0].remote).toBeFalse();
    expect(component.conversations[0].messages).toEqual([]);
  });

  it('keeps at most 30 cached conversations and stubs the rest', () => {
    type SaveFn = (conversation: {
      threadId: string;
      title: string;
      updatedAt: number;
      messages: Array<{ id: string; role: 'user'; content: string }>;
    }) => void;
    const save = (
      component as unknown as { saveConversation: SaveFn }
    ).saveConversation.bind(component);

    for (let index = 0; index < 35; index += 1) {
      save({
        threadId: `t-${index}`,
        title: `会话 ${index}`,
        updatedAt: Date.now() + index,
        messages: [{ id: `m-${index}`, role: 'user', content: `内容 ${index}` }],
      });
    }

    expect(component.conversations.length).toBe(35);
    const cached = component.conversations.filter((item) => item.messages.length);
    const stubs = component.conversations.filter((item) => !item.messages.length);
    expect(cached.length).toBe(30);
    expect(stubs.length).toBe(5);
    expect(stubs.every((item) => item.remote)).toBeTrue();
  });

  it('exports every conversation in one download', async () => {
    component.conversations = [
      {
        threadId: 't-1',
        title: '会话一',
        updatedAt: Date.now(),
        messages: [],
        remote: true,
      },
    ];
    (window.fetch as jasmine.Spy).and.resolveTo(
      new Response('# 全部会话导出\n\n## 会话一\n', {
        status: 200,
        headers: { 'Content-Type': 'text/markdown' },
      }),
    );
    const createObjectURL = spyOn(URL, 'createObjectURL').and.returnValue('blob:all');
    spyOn(URL, 'revokeObjectURL');
    const clickSpy = spyOn(HTMLAnchorElement.prototype, 'click');

    await component.exportAllConversations();
    await fixture.whenStable();

    expect(window.fetch).toHaveBeenCalledWith(
      `${component.apiBaseUrl}/agent/threads/export?format=markdown`,
    );
    expect(clickSpy).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:all');
    const blob = createObjectURL.calls.mostRecent().args[0] as Blob;
    expect(await blob.text()).toContain('# 全部会话导出');
  });

  it('shows the export-all control only when there is history', async () => {
    expect(fixture.nativeElement.querySelector('.export-all-button')).toBeNull();

    localStorage.setItem(
      'stock-assistant-conversations-v1',
      JSON.stringify([
        {
          threadId: 't-1',
          title: '会话一',
          updatedAt: Date.now(),
          messages: [],
          remote: true,
        },
      ]),
    );
    const fresh = TestBed.createComponent(StockChatComponent);
    fresh.detectChanges();
    await fresh.whenStable();
    fresh.detectChanges();

    expect(fresh.nativeElement.querySelector('.export-all-button')).toBeTruthy();
  });

  it('reports a failed bulk export', async () => {
    component.conversations = [
      {
        threadId: 't-1',
        title: '会话一',
        updatedAt: Date.now(),
        messages: [],
        remote: true,
      },
    ];
    (window.fetch as jasmine.Spy).and.resolveTo(
      jsonResponse({ detail: '导出服务不可用' }, 503),
    );

    await component.exportAllConversations();
    await fixture.whenStable();

    expect(component.apiError).toBe('导出服务不可用');
  });

  it('does not delete server conversations when the local cache evicts them', () => {
    type SaveFn = (conversation: {
      threadId: string;
      title: string;
      updatedAt: number;
      messages: Array<{ id: string; role: 'user'; content: string }>;
    }) => void;
    const save = (
      component as unknown as { saveConversation: SaveFn }
    ).saveConversation.bind(component);
    const fetchSpy = window.fetch as jasmine.Spy;
    fetchSpy.calls.reset();

    for (let index = 0; index < 35; index += 1) {
      save({
        threadId: `t-${index}`,
        title: `会话 ${index}`,
        updatedAt: Date.now() + index,
        messages: [{ id: `m-${index}`, role: 'user', content: `内容 ${index}` }],
      });
    }

    const deletes = fetchSpy.calls
      .allArgs()
      .filter((args: unknown[]) => {
        const init = args[1] as RequestInit | undefined;
        return String(init?.method ?? '').toUpperCase() === 'DELETE';
      });
    expect(deletes.length).toBe(0);
  });
});
