import { Pipe, PipeTransform } from '@angular/core';
import DOMPurify from 'dompurify';
import { marked } from 'marked';

/**
 * Renders the agent answer as Markdown.
 *
 * The backend returns raw Markdown (headings, tables, bold text, ...).  Binding
 * it with `{{ }}` showed the Markdown source verbatim and collapsed every
 * newline into a single wall of text.  This pipe converts the source to HTML
 * so the answer renders as a readable dashboard.
 *
 * `breaks: true` keeps single newlines meaningful because the model often
 * writes list items without a blank line in between.  `gfm: true` enables
 * tables, strikethrough and task lists, which the stock dashboard format
 * relies on.
 *
 * The result is sanitized with DOMPurify before it is bound with `[innerHTML]`,
 * so the model output cannot inject scripts into the page.
 */
@Pipe({
  name: 'markdown',
})
export class MarkdownPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    const source = value ?? '';
    if (!source.trim()) {
      return '';
    }

    const html = marked.parse(source, {
      gfm: true,
      breaks: true,
      async: false,
    }) as string;

    return DOMPurify.sanitize(html);
  }
}
