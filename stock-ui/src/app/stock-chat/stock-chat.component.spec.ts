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
});
