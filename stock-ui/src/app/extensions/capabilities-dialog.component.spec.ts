import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';

import { CapabilitiesDialogComponent } from './capabilities-dialog.component';
import { CapabilityCatalog } from './capabilities.types';

const catalog: CapabilityCatalog = {
  version: 2,
  counts: { agents: 1, skills: 2, commands: 1, mcp_servers: 2, hooks: 1 },
  agents: [
    {
      id: 'investment-analyst',
      name: 'Investment Analyst',
      description: '分析股票与财报。',
      skills: [{ id: 'stock-analysis', name: 'stock-analysis' }],
      mcp_servers: [{ id: 'quant-tools', name: 'quant-tools' }],
      source: 'project',
      file_path: 'stock-server/.stock/agents/investment-analyst.md',
      enabled: true,
    },
  ],
  skills: [
    {
      id: 'stock-analysis',
      name: 'stock-analysis',
      description: '股票智能分析。',
      when_to_use: '用户提供股票代码时',
      allowed_tools: 'Read Bash',
      aliases: ['stock-analysis', 'analysis/stock'],
      source: 'project',
      file_path: 'stock-server/.stock/skills/stock-analysis/SKILL.md',
      user_invocable: true,
      enabled: true,
    },
    {
      id: 'hidden-skill',
      name: 'hidden-skill',
      description: '不可手动调用。',
      aliases: ['hidden-skill'],
      source: 'project',
      user_invocable: false,
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
      file_path: 'stock-server/.stock/commands/stock-report.md',
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
      tool_count: 2,
      tools: ['normalize_symbol', 'technical_indicators'],
      source: 'project',
      file_path: 'stock-server/.stock/mcp-configs/mcp-servers.json',
    },
    {
      id: 'broken-tools',
      name: 'broken-tools',
      description: '启动失败。',
      transport: 'stdio',
      status: 'error',
      tool_count: 0,
      tools: [],
      error: 'missing env FOO',
      source: 'project',
    },
  ],
  hooks: [
    {
      id: 'stock-posttooluse-0-0',
      name: '校验行情数据',
      event: 'PostToolUse',
      enabled: true,
      source: 'project',
    },
  ],
  diagnostics: { slash_conflicts: [] },
};

describe('CapabilitiesDialogComponent', () => {
  let component: CapabilitiesDialogComponent;
  let fixture: ComponentFixture<CapabilitiesDialogComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [CapabilitiesDialogComponent],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();

    fixture = TestBed.createComponent(CapabilitiesDialogComponent);
    component = fixture.componentInstance;
    component.catalog = catalog;
    fixture.detectChanges();
  });

  it('renders tab counts and command details', () => {
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('命令');
    expect(text).toContain('/stock-report');
    expect(text).toContain('生成研究简报');
    expect(text).toContain('<symbol>');

    const tabCounts = Array.from(
      fixture.nativeElement.querySelectorAll('.capability-tab-count'),
    ).map((node) => (node as HTMLElement).textContent?.trim());
    expect(tabCounts).toEqual(['1', '2', '1', '2', '1']);
  });

  it('emits a slash insertion when a command is picked', () => {
    spyOn(component.insertSlash, 'emit');

    const row = fixture.nativeElement.querySelector(
      '.capability-row',
    ) as HTMLButtonElement;
    row.click();

    expect(component.insertSlash.emit).toHaveBeenCalledWith('/stock-report ');
  });

  it('shows MCP status, tools and errors', () => {
    component.selectTab('mcp');
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent;
    expect(text).toContain('已连接 · 2 个工具');
    expect(text).toContain('加载失败');
    expect(text).toContain('missing env FOO');

    const toolEntries = fixture.nativeElement.querySelectorAll(
      '.capability-tools li',
    );
    expect(toolEntries.length).toBe(2);
  });

  it('disables non-invocable skills and filters by search', () => {
    component.selectTab('skills');
    fixture.detectChanges();

    const rows: HTMLButtonElement[] = Array.from(
      fixture.nativeElement.querySelectorAll('.capability-row'),
    );
    expect(rows.length).toBe(2);
    expect(rows[1].disabled).toBeTrue();

    const search = fixture.nativeElement.querySelector(
      '.capability-search',
    ) as HTMLInputElement;
    search.value = 'stock';
    search.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelectorAll('.capability-row').length).toBe(1);
  });

  it('emits close and refresh requests', () => {
    spyOn(component.closeRequested, 'emit');
    spyOn(component.refreshRequested, 'emit');

    (fixture.nativeElement.querySelector('.modal-close') as HTMLButtonElement).click();
    (fixture.nativeElement.querySelector('.capability-refresh') as HTMLButtonElement).click();

    expect(component.closeRequested.emit).toHaveBeenCalled();
    expect(component.refreshRequested.emit).toHaveBeenCalled();
  });
});
