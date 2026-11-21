import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';

import { StockChatComponent } from './stock-chat.component';

/** Build a Response with a JSON body. */
function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/**
 * Route fetch calls by method + path so one spy can serve the whole flow.
 */
function routeFetch(
  handlers: Array<{
    method: string;
    path: string;
    response: () => Response | Promise<Response>;
  }>,
) {
  return (window.fetch as jasmine.Spy).and.callFake(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      const handler = handlers.find(
        (item) => item.method === method && url.includes(item.path),
      );
      if (handler) {
        return Promise.resolve(handler.response());
      }
      return Promise.resolve(jsonResponse({ detail: `unhandled ${method} ${url}` }, 500));
    },
  );
}

const preferenceFixture = {
  id: 1,
  category: 'market',
  content: '只关注 A 股',
  source: 'manual',
  enabled: true,
  created_at: '2026-01-01T00:00:00+00:00',
  updated_at: '2026-01-01T00:00:00+00:00',
};

describe('StockChatComponent preferences', () => {
  let component: StockChatComponent;
  let fixture: ComponentFixture<StockChatComponent>;

  beforeEach(async () => {
    localStorage.removeItem('stock-assistant-conversations-v1');
    localStorage.removeItem('stock-assistant-conversation-order-v1');
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

  it('opens the panel and loads preferences', async () => {
    routeFetch([
      {
        method: 'GET',
        path: '/agent/preferences',
        response: () => jsonResponse({ code: 0, data: { preferences: [preferenceFixture] } }),
      },
    ]);

    await component.openPreferences();
    await fixture.whenStable();

    expect(component.preferencesOpen).toBe(true);
    expect(component.preferences.length).toBe(1);
    expect(component.preferences[0].content).toBe('只关注 A 股');
  });

  it('adds a preference and clears the draft', async () => {
    const calls: string[] = [];
    (window.fetch as jasmine.Spy).and.callFake(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const method = (init?.method ?? 'GET').toUpperCase();
        calls.push(`${method} ${String(input)}`);
        if (method === 'POST') {
          return Promise.resolve(
            jsonResponse({ code: 0, data: { preference: { ...preferenceFixture, id: 2, content: '偏好短线' } } }),
          );
        }
        return Promise.resolve(
          jsonResponse({ code: 0, data: { preferences: [{ ...preferenceFixture, id: 2, content: '偏好短线' }] } }),
        );
      },
    );

    component.preferenceDraft = '偏好短线';
    component.preferenceCategory = 'style';
    await component.addPreference();
    await fixture.whenStable();

    expect(calls.some((call) => call.startsWith('POST'))).toBeTrue();
    expect(component.preferenceDraft).toBe('');
    expect(component.preferences[0].content).toBe('偏好短线');
  });

  it('toggles a preference through PATCH', async () => {
    let patchedBody = '';
    (window.fetch as jasmine.Spy).and.callFake(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const method = (init?.method ?? 'GET').toUpperCase();
        if (method === 'PATCH') {
          patchedBody = String(init?.body ?? '');
          return Promise.resolve(
            jsonResponse({ code: 0, data: { preference: { ...preferenceFixture, enabled: false } } }),
          );
        }
        return Promise.resolve(
          jsonResponse({ code: 0, data: { preferences: [preferenceFixture] } }),
        );
      },
    );

    component.preferences = [preferenceFixture as never];
    await component.togglePreference(preferenceFixture as never);
    await fixture.whenStable();

    expect(patchedBody).toContain('"enabled":false');
    expect(component.preferences[0].enabled).toBeFalse();
  });

  it('deletes a preference and removes it from the list', async () => {
    (window.fetch as jasmine.Spy).and.resolveTo(
      jsonResponse({ code: 0, data: { id: 1 } }),
    );
    component.preferences = [preferenceFixture as never];

    await component.deletePreference(preferenceFixture as never);
    await fixture.whenStable();

    expect(component.preferences).toEqual([]);
  });

  it('edits a preference inline', async () => {
    let patchedBody = '';
    (window.fetch as jasmine.Spy).and.callFake(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const method = (init?.method ?? 'GET').toUpperCase();
        if (method === 'PATCH') {
          patchedBody = String(init?.body ?? '');
          return Promise.resolve(
            jsonResponse({
              code: 0,
              data: { preference: { ...preferenceFixture, content: '只看沪市' } },
            }),
          );
        }
        return Promise.resolve(jsonResponse({ code: 0, data: { preferences: [] } }));
      },
    );

    component.preferences = [preferenceFixture as never];
    component.startEditingPreference(preferenceFixture as never);
    expect(component.editingPreferenceId).toBe(1);

    component.editingContent = '只看沪市';
    await component.savePreferenceContent(preferenceFixture as never);
    await fixture.whenStable();

    expect(patchedBody).toContain('只看沪市');
    expect(component.editingPreferenceId).toBeNull();
    expect(component.preferences[0].content).toBe('只看沪市');
  });

  it('shows a backend error instead of silently failing', async () => {
    (window.fetch as jasmine.Spy).and.resolveTo(
      jsonResponse({ detail: '偏好服务不可用' }, 503),
    );

    await component.openPreferences();
    await fixture.whenStable();

    expect(component.preferencesError).toBe('偏好服务不可用');
  });

  it('renders the panel in the DOM when opened', async () => {
    // Rendering is checked synchronously; loading is covered by the first test.
    component.preferences = [];
    component.preferencesOpen = true;
    fixture.detectChanges();
    await fixture.whenStable();

    const modal = fixture.nativeElement.querySelector('.modal');
    expect(modal).toBeTruthy();
    expect(modal.textContent).toContain('长期偏好');

    component.closePreferences();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.modal')).toBeNull();
  });
});

describe('StockChatComponent export', () => {
  let component: StockChatComponent;
  let fixture: ComponentFixture<StockChatComponent>;

  beforeEach(async () => {
    localStorage.removeItem('stock-assistant-conversations-v1');
    localStorage.removeItem('stock-assistant-conversation-order-v1');
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

  const conversation = {
    threadId: 'thread-1',
    title: '测试 / 会话',
    updatedAt: 0,
    messages: [
      { id: '1', role: 'user' as const, content: '分析 TSLA' },
      {
        id: '2',
        role: 'assistant' as const,
        content: '## 结论\n\n看好。',
        sources: [{ title: '公告', url: 'https://example.com/a' }],
      },
    ],
  };

  it('prefers the server record when it is available', async () => {
    const createObjectURL = spyOn(URL, 'createObjectURL').and.returnValue('blob:server');
    spyOn(URL, 'revokeObjectURL');
    const clickSpy = spyOn(HTMLAnchorElement.prototype, 'click');
    (window.fetch as jasmine.Spy).and.resolveTo(
      new Response('# 服务端导出\n\n## 我\n\n来自服务端', {
        status: 200,
        headers: { 'Content-Type': 'text/markdown' },
      }),
    );

    await component.exportConversation(conversation);

    expect(window.fetch).toHaveBeenCalledWith(
      `${component.apiBaseUrl}/agent/threads/thread-1/export?format=markdown`,
    );
    expect(clickSpy).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:server');
    const blob = createObjectURL.calls.mostRecent().args[0] as Blob;
    expect(await blob.text()).toContain('服务端导出');
  });

  it('falls back to the local record when the server has none', async () => {
    const createObjectURL = spyOn(URL, 'createObjectURL').and.returnValue('blob:local');
    spyOn(URL, 'revokeObjectURL');
    spyOn(HTMLAnchorElement.prototype, 'click');
    (window.fetch as jasmine.Spy).and.resolveTo(
      jsonResponse({ detail: '没有记录' }, 404),
    );

    await component.exportConversation(conversation);

    const blob = createObjectURL.calls.mostRecent().args[0] as Blob;
    const text = await blob.text();
    expect(text).toContain('# 测试 / 会话');
    expect(text).toContain('## 我');
    expect(text).toContain('分析 TSLA');
    expect(text).toContain('## 研究助手');
    expect(text).toContain('[公告](https://example.com/a)');
  });

  it('exports a server-only conversation without opening it', async () => {
    const createObjectURL = spyOn(URL, 'createObjectURL').and.returnValue('blob:stub');
    spyOn(URL, 'revokeObjectURL');
    spyOn(HTMLAnchorElement.prototype, 'click');
    (window.fetch as jasmine.Spy).and.resolveTo(
      new Response('# 服务端导出', { status: 200 }),
    );

    await component.exportConversation({
      threadId: 'server-only',
      title: '服务端会话',
      updatedAt: 0,
      messages: [],
      remote: true,
    });

    expect(createObjectURL).toHaveBeenCalled();
  });

  it('reports an error when neither side has a record', async () => {
    const createObjectURL = spyOn(URL, 'createObjectURL');
    (window.fetch as jasmine.Spy).and.resolveTo(
      jsonResponse({ detail: '该会话没有可导出的记录' }, 404),
    );

    await component.exportConversation({
      threadId: 'gone',
      title: '已过期',
      updatedAt: 0,
      messages: [],
      remote: true,
    });

    expect(component.apiError).toBe('该会话没有可导出的记录');
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it('does nothing for an empty local conversation', async () => {
    const createObjectURL = spyOn(URL, 'createObjectURL');
    const fetchSpy = window.fetch as jasmine.Spy;
    fetchSpy.calls.reset();

    await component.exportConversation({
      threadId: 'thread-2',
      title: '空会话',
      updatedAt: 0,
      messages: [],
    });

    expect(createObjectURL).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
