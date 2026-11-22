/**
 * Minimal YAML frontmatter reader for `.stock` extension files.
 *
 * The backend only needs a handful of scalar fields plus simple lists, so this
 * avoids pulling a full YAML parser into the runtime. It understands:
 *   - `key: value` scalars (quoted or bare)
 *   - `key: |` / `key: >` block scalars
 *   - `key: [a, b, c]` inline lists
 *   - `key:\n  - item` dash lists
 *   - one level of nested maps (used by `metadata:`)
 */

export interface Frontmatter {
  data: Record<string, unknown>;
  body: string;
}

export function parseFrontmatter(text: string): Frontmatter {
  const normalized = text.replace(/\r\n/g, '\n');
  if (!normalized.startsWith('---\n')) {
    return { data: {}, body: text };
  }
  const end = normalized.indexOf('\n---', 4);
  if (end === -1) {
    return { data: {}, body: text };
  }
  const raw = normalized.slice(4, end);
  const body = normalized.slice(end + 4).replace(/^\n/, '');
  return { data: parseYamlLite(raw), body };
}

export function pick(
  data: Record<string, unknown>,
  ...keys: string[]
): unknown {
  for (const key of keys) {
    if (key in data) return data[key];
  }
  return undefined;
}

export function asString(value: unknown): string | undefined {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed ? trimmed : undefined;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return undefined;
}

export function asBoolean(value: unknown, fallback = true): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    const v = value.trim().toLowerCase();
    if (['false', '0', 'no', 'off'].includes(v)) return false;
    if (['true', '1', 'yes', 'on'].includes(v)) return true;
  }
  return fallback;
}

export function asStringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed) return [];
    if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
      return splitInlineList(trimmed.slice(1, -1));
    }
    return trimmed.split(/[\s,]+/).filter(Boolean);
  }
  return [];
}

function splitInlineList(inner: string): string[] {
  const items: string[] = [];
  let current = '';
  let quote: string | null = null;
  for (const char of inner) {
    if (quote) {
      current += char;
      if (char === quote) quote = null;
    } else if (char === '"' || char === "'") {
      quote = char;
      current += char;
    } else if (char === ',') {
      items.push(unquote(current.trim()));
      current = '';
    } else {
      current += char;
    }
  }
  if (current.trim()) items.push(unquote(current.trim()));
  return items.filter(Boolean);
}

function unquote(value: string): string {
  if (value.length >= 2) {
    const first = value[0];
    const last = value[value.length - 1];
    if ((first === '"' && last === '"') || (first === "'" && last === "'")) {
      return value.slice(1, -1);
    }
  }
  return value;
}

function parseYamlLite(raw: string): Record<string, unknown> {
  const data: Record<string, unknown> = {};
  const lines = raw.split('\n');

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim() || line.trim().startsWith('#') || /^\s/.test(line)) {
      continue;
    }
    const match = /^([A-Za-z0-9_.-]+):\s*(.*)$/.exec(line);
    if (!match) continue;

    const key = match[1];
    const inline = match[2].trim();

    // Gather indented continuation lines (including blanks in between).
    const continuation: string[] = [];
    let j = i + 1;
    while (
      j < lines.length &&
      (/^\s+/.test(lines[j]) || lines[j].trim() === '')
    ) {
      continuation.push(lines[j]);
      j++;
    }

    if (inline === '|' || inline === '>' || inline === '|-' || inline === '>-') {
      const folded = inline.startsWith('>');
      const text = continuation
        .map((entry) => entry.replace(/^\s+/, ''))
        .join(folded ? ' ' : '\n')
        .trim();
      data[key] = text;
      i = j - 1;
      continue;
    }

    if (inline === '') {
      const items = continuation
        .map((entry) => entry.trim())
        .filter(Boolean);
      if (items.length > 0 && items.every((entry) => entry.startsWith('- '))) {
        data[key] = items.map((entry) => unquote(entry.slice(2).trim()));
      } else {
        const nested: Record<string, unknown> = {};
        for (const entry of continuation) {
          const nestedMatch = /^\s+([A-Za-z0-9_.-]+):\s*(.*)$/.exec(entry);
          if (nestedMatch) {
            nested[nestedMatch[1]] = unquote(nestedMatch[2].trim());
          }
        }
        if (Object.keys(nested).length > 0) data[key] = nested;
      }
      i = j - 1;
      continue;
    }

    if (inline.startsWith('[') && inline.endsWith(']')) {
      data[key] = splitInlineList(inline.slice(1, -1));
    } else {
      data[key] = unquote(inline);
    }
    i = j - 1;
  }

  return data;
}
