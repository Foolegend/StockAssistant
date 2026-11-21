import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';

import { StockChatComponent } from './stock-chat.component';

describe('StockChatComponent delete UI', () => {
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
      new Response('{"code":0,"data":{"threads":[]}}', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    fixture = TestBed.createComponent(StockChatComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('renders a delete button per conversation and a clear-all control', async () => {
    const stream = 'event: complete\ndata: {"answer":"完成","sources":[]}\n\n';
    (window.fetch as jasmine.Spy).and.resolveTo(new Response(stream));
    component.userQuestion = '一个会话';
    await component.sendChat();
    fixture.detectChanges();

    const items = fixture.nativeElement.querySelectorAll('.conversation-item');
    expect(items.length).toBe(1);
    expect(items[0].querySelector('.conversation-delete')).toBeTruthy();
    expect(items[0].querySelector('.conversation-title').textContent.trim()).toBe('一个会话');
    expect(fixture.nativeElement.querySelector('.clear-all-button')).toBeTruthy();

    // The clear-all control disappears once nothing is stored.
    component.clearAllConversations();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.clear-all-button')).toBeNull();
  });

  it('keeps the conversation title readable next to the delete button', async () => {
    const stream = 'event: complete\ndata: {"answer":"完成","sources":[]}\n\n';
    (window.fetch as jasmine.Spy).and.resolveTo(new Response(stream));
    component.userQuestion = '很长的会话标题用来检查省略号处理是否正常显示出来';
    await component.sendChat();
    fixture.detectChanges();

    const title = fixture.nativeElement.querySelector('.conversation-title');
    expect(title.textContent).toContain('很长的会话标题');
  });
});
