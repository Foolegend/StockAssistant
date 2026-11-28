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

  it('loads generated artifacts from the backend file endpoint', () => {
    const html = pipe.transform(
      '![走势图](out/index-charts/上证指数/上证指数_走势图.png)',
    );

    expect(html).toContain('<img');
    expect(html).toContain(
      'src="http://127.0.0.1:8015/files/out/index-charts/',
    );
  });

  it('turns artifact file references into downloadable links', () => {
    const html = pipe.transform(
      '[月度数据](out/index-charts/上证指数/上证指数_月度数据.xlsx)',
    );

    expect(html).toContain(
      'href="http://127.0.0.1:8015/files/out/index-charts/',
    );
    expect(html).toContain('target="_blank"');
  });

  it('leaves external links untouched', () => {
    const html = pipe.transform('[示例](https://example.com/a.png)');

    expect(html).toContain('https://example.com/a.png');
    expect(html).not.toContain('/files/');
  });
});
