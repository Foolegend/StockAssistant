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
  });

  afterEach(() => {
    httpTestingController.verify();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('does not expose agent or command selectors', () => {
    expect(fixture.nativeElement.querySelectorAll('select').length).toBe(0);
  });

  it('sends only the question and conversation thread', () => {
    component.userQuestion = '分析 TSLA';
    component.sendChat();

    const request = httpTestingController.expectOne('http://127.0.0.1:8015/agent/chat');
    expect(request.request.body).toEqual({
      question: '分析 TSLA',
      thread_id: jasmine.any(String),
    });
    request.flush({ code: 0, data: { answer: '完成' } });
  });

  it('does not load the component catalog for chat routing', () => {
    httpTestingController.expectNone('http://127.0.0.1:8015/agent/catalog');
    component.userQuestion = '你好';
    component.sendChat();

    const request = httpTestingController.expectOne('http://127.0.0.1:8015/agent/chat');
    expect(request.request.body.agent_id).toBeUndefined();
    expect(request.request.body.command_id).toBeUndefined();
    request.flush({ code: 0, data: { answer: '你好' } });
  });

  it('renders the answer as Markdown instead of raw text', () => {
    component.userQuestion = '分析中兴通讯股票';
    component.sendChat();

    const request = httpTestingController.expectOne('http://127.0.0.1:8015/agent/chat');
    request.flush({
      code: 0,
      data: {
        answer: '## 决策看板\n\n| 指标 | 数值 |\n|---|---|\n| 现价 | 28.06 |',
      },
    });
    fixture.detectChanges();

    const answer = fixture.nativeElement.querySelector('.answer-content');
    expect(answer.querySelector('h2')).toBeTruthy();
    expect(answer.querySelector('table')).toBeTruthy();
    expect(answer.textContent).toContain('28.06');
  });

  it('displays the backend detail for a failed request', () => {
    component.userQuestion = '帮我分析下中兴通讯公司的股票';
    component.sendChat();

    const request = httpTestingController.expectOne('http://127.0.0.1:8015/agent/chat');
    request.flush(
      { detail: "Component routing failed: Router selected unknown agent 'null'" },
      { status: 502, statusText: 'Bad Gateway' },
    );
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.error')?.textContent).toContain(
      "Router selected unknown agent 'null'",
    );
  });
});
