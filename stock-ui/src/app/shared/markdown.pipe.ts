import { Pipe, PipeTransform } from '@angular/core';
import DOMPurify from 'dompurify';
import { marked } from 'marked';

/** Backend port that serves session data and generated artifacts. */
const API_PORT = 8015;

/**
 * Extracts the repo-relative artifact path from a Markdown link target.
 *
 * Skills write their output under `out/`, so answers can reference a chart as
 * `out/index-charts/上证指数/上证指数_走势图.png`.  The browser cannot read the
 * server's filesystem, so those targets are rewritten to the backend's
 * `/files/` endpoint (see `artifactResponse` in `terminal/server.ts`).
 */
function artifactRelativePath(raw: string): string | null {
  let value = raw.trim();
  if (!value) return null;
  if (/^file:\/\//i.test(value)) {
    value = value.replace(/^file:\/\//i, '');
  } else if (/^[a-z][a-z0-9+.-]*:/i.test(value)) {
    // Any other scheme (https:, data:, mailto:, ...) is not a local artifact.
    return null;
  }
  // Absolute paths that came from the CLI keep only the part under `/out/`.
  const marker = value.indexOf('/out/');
  if (marker >= 0) value = value.slice(marker + 1);
  value = value.replace(/^\.?\//, '');
  return /^out\//.test(value) ? value : null;
}

function backendOrigin(): string {
  if (typeof window === 'undefined') return `http://127.0.0.1:${API_PORT}`;
  const hostname =
    window.location.hostname === 'localhost'
      ? '127.0.0.1'
      : window.location.hostname;
  const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
  return `${protocol}//${hostname}:${API_PORT}`;
}

function artifactUrl(raw: string): string | null {
  const relative = artifactRelativePath(raw);
  // `marked` already percent-encodes link targets; browsers also encode raw
  // characters in an attribute URL, so the path can be used as-is.
  return relative ? `${backendOrigin()}/files/${relative}` : null;
}

/** Rewrites `out/...` image sources and links to the artifact endpoint. */
function rewriteArtifactUrls(html: string): string {
  if (!html || typeof document === 'undefined') return html;
  const template = document.createElement('template');
  template.innerHTML = html;
  let changed = false;

  template.content.querySelectorAll<HTMLImageElement>('img[src]').forEach((image) => {
    const url = artifactUrl(image.getAttribute('src') ?? '');
    if (!url) return;
    image.setAttribute('src', url);
    image.setAttribute('loading', 'lazy');
    image.setAttribute('decoding', 'async');
    if (!image.getAttribute('title')) {
      image.setAttribute('title', '点击查看大图');
    }
    changed = true;
  });

  template.content.querySelectorAll<HTMLAnchorElement>('a[href]').forEach((anchor) => {
    const url = artifactUrl(anchor.getAttribute('href') ?? '');
    if (!url) return;
    anchor.setAttribute('href', url);
    anchor.setAttribute('target', '_blank');
    anchor.setAttribute('rel', 'noopener');
    changed = true;
  });

  return changed ? template.innerHTML : html;
}

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
 * so the model output cannot inject scripts into the page.  Artifact links
 * (`out/...`) are then pointed at the local backend, turning generated charts
 * into images the browser can show inline.
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

    return rewriteArtifactUrls(DOMPurify.sanitize(html));
  }
}
