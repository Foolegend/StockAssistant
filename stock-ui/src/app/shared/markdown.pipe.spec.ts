import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';

import { MarkdownPipe } from './markdown.pipe';

describe('MarkdownPipe', () => {
  let pipe: MarkdownPipe;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [MarkdownPipe, provideZonelessChangeDetection()],
    });
    pipe = TestBed.inject(MarkdownPipe);
  });

  it('renders headings as HTML instead of raw Markdown', () => {
    const html = pipe.transform('## 决策看板');

    expect(html).toContain('<h2>');
    expect(html).toContain('决策看板');
  });

  it('renders bold text', () => {
    expect(pipe.transform('**数据截止**')).toContain('<strong>');
  });

  it('renders GFM tables', () => {
    const html = pipe.transform('| 指标 | 数值 |\n|---|---|\n| 现价 | 28.06 |');

    expect(html).toContain('<table>');
    expect(html).toContain('28.06');
  });

  it('keeps single newlines as line breaks', () => {
    expect(pipe.transform('第一行\n第二行')).toContain('<br>');
  });

  it('returns empty output for blank input', () => {
    expect(pipe.transform('')).toBe('');
    expect(pipe.transform(null)).toBe('');
    expect(pipe.transform(undefined)).toBe('');
  });

  it('strips script tags from the rendered output', () => {
    const html = pipe.transform('<script>alert(1)</script>\n\n正文');

    expect(html).not.toContain('<script>');
    expect(html).toContain('正文');
  });
});
