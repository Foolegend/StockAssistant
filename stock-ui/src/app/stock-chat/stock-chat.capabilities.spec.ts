import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';

import { StockChatComponent } from './stock-chat.component';
import { CapabilityCatalog } from '../extensions/capabilities.types';

const catalog: CapabilityCatalog = {
  version: 2,
  counts: { agents: 1, skills: 1, commands: 1, mcp_servers: 1, hooks: 0 },
  agents: [
    {
      id: 'investment-analyst',
      name: 'Investment Analyst',
      description: '分析股票与财报。',
      skills: [{ id: 'stock-analysis', name: 'stock-analysis' }],
      mcp_servers: [{ id: 'quant-tools', name: 'quant-tools' }],
      source: 'project',
      enabled: true,
    },
  ],
  skills: [
    {
      id: 'stock-analysis',
      name: 'stock-analysis',
      description: '股票智能分析。',
      aliases: ['stock-analysis'],
      source: 'project',
      user_invocable: true,
      enabled: true,
    },
  ],
  commands: [
    {
      id: 'stock-report',
      name: '/stock-report',
      description: '生成研究简报。',
      argument_hint: '<symbol>',
      arguments: ['symbol'],
      agent_id: 'investment-analyst',
      skills: [{ id: 'stock-analysis', name: 'stock-analysis' }],
      mcp_servers: [{ id: 'quant-tools', name: 'quant-tools' }],
      source: 'project',
      user_invocable: true,
      enabled: true,
    },
  ],
  mcp_servers: [
    {
      id: 'quant-tools',
      name: 'quant-tools',
      description: '本地量化计算。',
      transport: 'stdio',
      status: 'connected',
      tool_count: 1,
      tools: ['normalize_symbol'],
      source: 'project',
    },
  ],
  hooks: [],
  diagnostics: { slash_conflicts: [] },
};

describe('StockChatComponent capabilities', () => {
  let component: StockChatComponent;
  let fixture: ComponentFixture<StockChatComponent>;

  beforeEach(async () => {
    localStorage.removeItem('stock-assistant-conversations-v1');
    localStorage.removeItem('stock-assistant-conversation-order-v1');
    await TestBed.configureTestingModule({
      imports: [StockChatComponent],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();

    spyOn(window, 'fetch').and.callFake(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/agent/catalog')) {
        return new Response(JSON.stringify({ code: 0, data: catalog }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response('{"code":0,"data":{"threads":[]}}', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    fixture = TestBed.createComponent(StockChatComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('loads the catalog lazily when the capabilities dialog opens', async () => {
    const callsBefore = (window.fetch as jasmine.Spy).calls
      .allArgs()
      .filter((args) => String(args[0]).includes('/agent/catalog'));
    expect(callsBefore.length).toBe(0);

    await (
      component as unknown as { ensureCatalog: () => Promise<void> }
    ).ensureCatalog();
    fixture.detectChanges();

    const callsAfter = (window.fetch as jasmine.Spy).calls
      .allArgs()
      .filter((args) => String(args[0]).includes('/agent/catalog'));
    expect(callsAfter.length).toBe(1);
    expect(component.capabilityCount).toBe(4);

    component.openCapabilities();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.capability-modal')).toBeTruthy();

    component.closeCapabilities();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.capability-modal')).toBeNull();
  });

  it('filters the slash menu and inserts the selected command', () => {
    component.catalog = catalog;
    const textarea = fixture.nativeElement.querySelector(
      '.composer textarea',
    ) as HTMLTextAreaElement;
    textarea.value = '/sto';
    textarea.dispatchEvent(new Event('input'));
    fixture.detectChanges();

    const items = Array.from(
      fixture.nativeElement.querySelectorAll('.slash-item .slash-name'),
    ).map((node) => (node as HTMLElement).textContent?.trim());
    expect(items).toEqual(['/stock-report', '/stock-analysis']);

    component.handleKeydown(new KeyboardEvent('keydown', { key: 'ArrowDown' }));
    component.handleKeydown(new KeyboardEvent('keydown', { key: 'Enter' }));

    expect(component.userQuestion).toBe('/stock-analysis ');
    expect(component.slashMenuOpen).toBeFalse();
  });

  it('does not interrupt IME composition', () => {
    component.userQuestion = '分析 TSLA';
    spyOn(component, 'sendChat');

    const event = new KeyboardEvent('keydown', { key: 'Enter', isComposing: true });
    component.handleKeydown(event);

    expect(component.sendChat).not.toHaveBeenCalled();
  });

  it('interleaves thought rows with tool steps', async () => {
    const stream = [
      {
        event: 'thought',
        data: { thought_id: 'thought-1', text: '先核对公告。', duration_ms: 1200 },
      },
      {
        event: 'tool_start',
        data: {
          tool_id: 'tool-1',
          tool: 'execute',
          args: '{"command":"echo hi"}',
          command: 'echo hi',
        },
      },
      {
        event: 'tool_end',
        data: { tool_id: 'tool-1', tool: 'execute', output: 'hi', duration_ms: 5 },
      },
      {
        event: 'thought',
        data: { thought_id: 'thought-2', text: '整理结论。', duration_ms: 300 },
      },
      { event: 'complete', data: { answer: '完成', sources: [] } },
    ]
      .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
      .join('');
    (window.fetch as jasmine.Spy).and.resolveTo(new Response(stream));

    component.userQuestion = '分析一下';
    await component.sendChat();
    fixture.detectChanges();

    const entries = Array.from(
      fixture.nativeElement.querySelectorAll(
        '.evidence-panel .activity-list > li',
      ),
    ) as HTMLElement[];
    expect(entries.length).toBe(3);
    expect(entries[0].querySelector('.thought-label')?.textContent).toContain('思考');
    expect(entries[0].querySelector('.thought-state')?.textContent).toContain('1.2 s');
    expect(entries[0].querySelector('.thought-text')?.textContent).toContain('先核对公告');
    expect(entries[1].querySelector('.activity-tool')?.textContent).toContain('执行命令');
    expect(entries[2].querySelector('.thought-text')?.textContent).toContain('整理结论');
  });

  it('renders usage chips from status and complete events', async () => {
    const stream = [
      {
        event: 'status',
        data: {
          stage: 'analysis',
          message: '正在分析问题',
          agent_id: 'investment-analyst',
          agent: { id: 'investment-analyst', name: 'Investment Analyst' },
          command: { id: 'stock-report', name: '/stock-report' },
          skills: [{ id: 'stock-analysis', name: 'stock-analysis' }],
          mcp_servers: [{ id: 'quant-tools', name: 'quant-tools' }],
        },
      },
      {
        event: 'complete',
        data: {
          answer: '报告完成',
          sources: [],
          agent_id: 'investment-analyst',
          command: { id: 'stock-report', name: '/stock-report' },
          skills: [{ id: 'stock-analysis', name: 'stock-analysis' }],
          mcp_servers: [{ id: 'quant-tools', name: 'quant-tools' }],
        },
      },
    ]
      .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
      .join('');
    (window.fetch as jasmine.Spy).and.resolveTo(new Response(stream));

    component.userQuestion = '/stock-report 中兴通讯';
    await component.sendChat();
    fixture.detectChanges();

    const chips = Array.from(
      fixture.nativeElement.querySelectorAll('.message-body .usage-chip'),
    ).map((node) => (node as HTMLElement).textContent?.trim());
    expect(chips).toEqual([
      '命令 · /stock-report',
      '智能体 · Investment Analyst',
      '技能 · stock-analysis',
      'MCP · quant-tools',
    ]);
    expect(component.currentUsage?.command?.id).toBe('stock-report');

    const evidenceChips = fixture.nativeElement.querySelectorAll(
      '.usage-section .usage-chip',
    );
    expect(evidenceChips.length).toBe(4);
  });
});
