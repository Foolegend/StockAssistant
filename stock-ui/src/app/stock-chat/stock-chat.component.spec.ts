import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideZonelessChangeDetection } from '@angular/core';

import { StockChatComponent } from './stock-chat.component';

describe('StockChatComponent', () => {
  let component: StockChatComponent;
  let fixture: ComponentFixture<StockChatComponent>;
  let httpTestingController: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [StockChatComponent],
      providers: [provideZonelessChangeDetection(), provideHttpClient(), provideHttpClientTesting()],
    })
    .compileComponents();

    httpTestingController = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(StockChatComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();

    httpTestingController.expectOne('http://127.0.0.1:8015/agent/catalog').flush({
      code: 0,
      data: {
        agents: [{ id: 'stock-analyst', name: '股票分析师', description: '分析股票', skills: [] }],
        commands: [{ id: 'stock-analysis', name: '股票分析', description: '', agent_ids: ['stock-analyst'] }],
        default_agent_id: 'stock-analyst',
        default_command_id: 'stock-analysis',
      },
    });
  });

  afterEach(() => {
    httpTestingController.verify();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('shows readable agent and command names', () => {
    expect(fixture.nativeElement.textContent).toContain('股票分析师');
    expect(fixture.nativeElement.textContent).toContain('股票分析');
  });

  it('sends the selected registered IDs', () => {
    component.userQuestion = '分析 TSLA';
    component.sendChat();

    const request = httpTestingController.expectOne('http://127.0.0.1:8015/agent/chat');
    expect(request.request.body.agent_id).toBe('stock-analyst');
    expect(request.request.body.command_id).toBe('stock-analysis');
    expect(request.request.body.skill_dirs).toBeUndefined();
    request.flush({ code: 0, data: { answer: '完成' } });
  });

  it('updates the command when the selected agent cannot use it', () => {
    component.commands = [
      { id: 'command-a', name: '命令 A', description: '', agent_ids: ['agent-a'] },
      { id: 'command-b', name: '命令 B', description: '', agent_ids: ['agent-b'] },
    ];
    component.selectedAgentId = 'agent-b';
    component.selectedCommandId = 'command-a';

    component.onAgentChange();

    expect(component.selectedCommandId).toBe('command-b');
  });
});
