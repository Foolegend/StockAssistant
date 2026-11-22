/**
 * Shared wiring for launching the StockAssistant CLI in a real terminal.
 *
 * Mirrors the `.stock` injection used by the HTTP API: MCP config, hooks,
 * system prompt (agent + skills + commands) and `.env` model settings.
 */

import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { CapabilityCatalog } from '../lib/catalog';

export interface TerminalLaunch {
  args: string[];
  env: Record<string, string | undefined>;
}

export function loadDotEnv(file: string): Record<string, string> {
  const result: Record<string, string> = {};
  if (!existsSync(file)) return result;
  for (const raw of readFileSync(file, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const index = line.indexOf('=');
    if (index < 1) continue;
    const key = line.slice(0, index).trim();
    let value = line.slice(index + 1).trim();
    if (
      value.length >= 2 &&
      ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'")))
    ) {
      value = value.slice(1, -1);
    }
    result[key] = value;
  }
  return result;
}

export function buildTerminalLaunch(
  repoRoot: string,
  stockRoot: string,
  catalog: CapabilityCatalog,
  agentBodies: Map<string, string>,
): TerminalLaunch {
  const dotenv = loadDotEnv(join(stockRoot, '.env'));
  const env: Record<string, string | undefined> = {
    ...process.env,
    ...dotenv,
    PATH: process.env.PATH,
    TERM: process.env.TERM ?? 'xterm-256color',
    COLORTERM: process.env.COLORTERM ?? 'truecolor',
  };

  const args = [
    'run',
    '--preload',
    join(stockRoot, 'compat', 'preload.ts'),
    join(stockRoot, 'src', 'entrypoints', 'cli.tsx'),
    '--dangerously-skip-permissions',
    '--strict-mcp-config',
    '--mcp-config',
    buildMcpConfig(repoRoot, stockRoot),
    '--append-system-prompt',
    buildSystemAppend(catalog, agentBodies, repoRoot),
    '--add-dir',
    repoRoot,
  ];

  const settings = buildSettings(repoRoot, stockRoot);
  if (settings) args.push('--settings', settings);

  return { args, env };
}

function buildSystemAppend(
  catalog: CapabilityCatalog,
  agentBodies: Map<string, string>,
  repoRoot: string,
): string {
  const agent =
    catalog.agents.find((item) => item.enabled) ?? catalog.agents[0] ?? null;
  const lines: string[] = [];

  if (agent) {
    const body = agentBodies.get(agent.id)?.trim();
    if (body) lines.push(body);
  }

  const skills = catalog.skills.filter((item) => item.enabled);
  if (skills.length > 0) {
    lines.push(
      '## 可用技能',
      '需要时先用 Read 工具读取对应 SKILL.md，再按其步骤执行：',
    );
    for (const skill of skills) {
      const description = skill.description
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 240);
      lines.push(
        `- ${skill.id}: ${description || '（无描述）'} — ${skill.file_path}`,
      );
    }
  }

  const commands = catalog.commands.filter((item) => item.enabled);
  if (commands.length > 0) {
    lines.push(
      '## 可用命令',
      ...commands.map(
        (command) =>
          `- /${command.id}: ${command.description
            .replace(/\s+/g, ' ')
            .trim()
            .slice(0, 160)}`,
      ),
    );
  }

  lines.push(
    '## 运行环境',
    `- 仓库根目录：${repoRoot}`,
    '- 扩展目录：stock-server/.stock（skills / commands / agents / mcp-configs）',
    '- 文件与 shell 路径均相对于仓库根目录；临时文件只写入 .tmp/。',
    '- 回答投资问题时给出结论、依据与风险；缺少标的或周期时先询问。',
  );

  return lines.join('\n');
}

export function buildMcpConfig(repoRoot: string, stockRoot: string): string {
  const file = join(stockRoot, '.stock', 'mcp-configs', 'mcp-servers.json');
  const fallback = '{"mcpServers":{}}';
  if (!existsSync(file)) return fallback;
  try {
    const parsed = JSON.parse(readFileSync(file, 'utf8')) as {
      mcpServers?: Record<string, Record<string, unknown>>;
    };
    const servers = parsed.mcpServers ?? {};
    const python = resolvePython(repoRoot);
    for (const server of Object.values(servers)) {
      if (server.command === 'python' || server.command === 'python3') {
        server.command = python;
      }
      if (Array.isArray(server.args)) {
        server.args = server.args.map((arg) =>
          typeof arg === 'string' && arg.startsWith('.')
            ? join(stockRoot, arg)
            : arg,
        );
      }
      server.cwd = repoRoot;
    }
    return JSON.stringify({ mcpServers: servers });
  } catch {
    return fallback;
  }
}

export function buildSettings(
  repoRoot: string,
  stockRoot: string,
): string | null {
  const file = join(stockRoot, '.stock', 'hooks', 'hooks.json');
  if (!existsSync(file)) return null;
  try {
    const parsed = JSON.parse(readFileSync(file, 'utf8')) as Record<
      string,
      unknown
    >;
    if (!parsed.hooks) return null;
    return JSON.stringify(replaceVars(parsed, repoRoot));
  } catch {
    return null;
  }
}

function replaceVars(value: unknown, repoRoot: string): unknown {
  if (typeof value === 'string') {
    return value
      .split('${STOCK_PLUGIN_ROOT}').join(repoRoot)
      .split('%STOCK_PLUGIN_ROOT%').join(repoRoot);
  }
  if (Array.isArray(value)) {
    return value.map((item) => replaceVars(item, repoRoot));
  }
  if (value && typeof value === 'object') {
    const result: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      result[key] = replaceVars(item, repoRoot);
    }
    return result;
  }
  return value;
}

function resolvePython(repoRoot: string): string {
  const candidates = [
    process.env.STOCK_PYTHON,
    join(repoRoot, '.venv', 'Scripts', 'python.exe'),
    join(repoRoot, '.venv', 'bin', 'python'),
  ].filter(Boolean) as string[];
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }
  return 'python';
}
