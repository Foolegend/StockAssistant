/**
 * Reads the `.stock` extension directories and returns the same catalog shape
 * the Angular UI consumes (`CapabilityCatalog` in capabilities.types.ts).
 */

import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { basename, join, relative } from 'node:path';
import {
  asBoolean,
  asString,
  asStringList,
  parseFrontmatter,
  pick,
} from './frontmatter';

export interface CapabilityRef {
  id: string;
  name: string;
}

export interface AgentCapability extends CapabilityRef {
  description: string;
  when_to_use: string | null;
  skills: CapabilityRef[];
  mcp_servers: CapabilityRef[];
  source: string;
  file_path: string | null;
  enabled: boolean;
}

export interface SkillCapability extends CapabilityRef {
  description: string;
  when_to_use: string | null;
  allowed_tools: string | string[] | null;
  version: string | null;
  aliases: string[];
  source: string;
  file_path: string | null;
  user_invocable: boolean;
  enabled: boolean;
}

export interface CommandCapability extends CapabilityRef {
  description: string;
  argument_hint: string | null;
  arguments: string[];
  when_to_use: string | null;
  allowed_tools: string | string[] | null;
  agent_id: string | null;
  skills: CapabilityRef[];
  mcp_servers: CapabilityRef[];
  source: string;
  file_path: string | null;
  user_invocable: boolean;
  enabled: boolean;
}

export interface McpCapability extends CapabilityRef {
  description: string;
  transport: 'stdio' | 'streamable_http';
  status: 'idle' | 'connected' | 'error';
  tool_count: number;
  tools: string[];
  error: string | null;
  updated_at: string | null;
  source: string;
  file_path: string | null;
}

export interface HookCapability extends CapabilityRef {
  event: string;
  enabled: boolean;
  source: string;
}

export interface CapabilityCatalog {
  version: number;
  agents: AgentCapability[];
  skills: SkillCapability[];
  commands: CommandCapability[];
  mcp_servers: McpCapability[];
  hooks: HookCapability[];
  counts: {
    agents: number;
    skills: number;
    commands: number;
    mcp_servers: number;
    hooks: number;
  };
  diagnostics: { slash_conflicts: Array<{ name: string; owners: string[] }> };
}

export interface CommandSource {
  id: string;
  body: string;
}

export interface SkillSource {
  id: string;
  body: string;
}

export interface StockExtensions {
  catalog: CapabilityCatalog;
  commandBodies: Map<string, string>;
  skillBodies: Map<string, string>;
  agentBodies: Map<string, string>;
  extensionRoot: string;
}

function walk(dir: string): string[] {
  if (!existsSync(dir)) return [];
  const files: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...walk(full));
    } else if (entry.isFile()) {
      files.push(full);
    }
  }
  return files;
}

function displayPath(repoRoot: string, file: string): string {
  return relative(repoRoot, file).replace(/\\/g, '/');
}

function readJson<T>(file: string): T | null {
  try {
    return JSON.parse(readFileSync(file, 'utf8')) as T;
  } catch {
    return null;
  }
}

/**
 * Extension directory resolution.
 *
 * The CLI's project-config dir is `.stock`, so the repository root is the
 * preferred location. Keep the old `stock-server/.stock` path as a fallback
 * for checkouts that have not moved it yet.
 */
export function resolveExtensionRoot(repoRoot: string): string {
  const root = join(repoRoot, '.stock');
  const legacy = join(repoRoot, 'stock-server', '.stock');
  return existsSync(root) ? root : existsSync(legacy) ? legacy : root;
}

export function loadStockExtensions(repoRoot: string): StockExtensions {
  const extensionRoot = resolveExtensionRoot(repoRoot);
  const commandBodies = new Map<string, string>();
  const skillBodies = new Map<string, string>();
  const agentBodies = new Map<string, string>();

  // --- Skills -------------------------------------------------------------
  const skills: SkillCapability[] = [];
  for (const file of walk(join(extensionRoot, 'skills'))) {
    if (basename(file) !== 'SKILL.md') continue;
    const text = readFileSync(file, 'utf8');
    const { data, body } = parseFrontmatter(text);
    const dir = relative(join(extensionRoot, 'skills'), file)
      .replace(/\\/g, '/')
      .replace(/\/SKILL\.md$/, '')
      .replace(/^SKILL\.md$/, '');
    const id =
      asString(pick(data, 'name')) ??
      (dir || basename(join(file, '..')));
    const metadata = (pick(data, 'metadata') ?? {}) as Record<string, unknown>;
    const whenToUse =
      asString(pick(data, 'when_to_use', 'when-to-use', 'whenToUse')) ??
      asString(metadata.trigger);
    const allowed = pick(data, 'allowed-tools', 'allowed_tools');
    const skill: SkillCapability = {
      id,
      name: id,
      description: asString(pick(data, 'description')) ?? '',
      when_to_use: whenToUse ?? null,
      allowed_tools:
        Array.isArray(allowed)
          ? (allowed as unknown[]).map(String)
          : asString(allowed) ?? null,
      version:
        asString(pick(data, 'version')) ??
        asString(metadata.version) ??
        null,
      aliases: asStringList(pick(data, 'aliases', 'alias')),
      source: 'project',
      file_path: displayPath(repoRoot, file),
      user_invocable: asBoolean(
        pick(data, 'user-invocable', 'user_invocable', 'userInvocable'),
        true,
      ),
      enabled: asBoolean(pick(data, 'enabled'), true),
    };
    skills.push(skill);
    skillBodies.set(id, body);
    for (const alias of skill.aliases) {
      if (!skillBodies.has(alias)) skillBodies.set(alias, body);
    }
  }
  skills.sort((a, b) => a.id.localeCompare(b.id));
  const skillById = new Map(skills.map((skill) => [skill.id, skill]));

  // --- Agents -------------------------------------------------------------
  const agents: AgentCapability[] = [];
  for (const file of walk(join(extensionRoot, 'agents'))) {
    if (!file.endsWith('.md')) continue;
    const text = readFileSync(file, 'utf8');
    const { data, body } = parseFrontmatter(text);
    const id =
      asString(pick(data, 'name')) ??
      basename(file).replace(/\.md$/, '');
    const skillIds = asStringList(pick(data, 'skills', 'skill_ids'));
    const mcpIds = asStringList(
      pick(data, 'mcp_server_ids', 'mcp-servers', 'mcp_servers'),
    );
    const agent: AgentCapability = {
      id,
      name: asString(pick(data, 'display_name', 'display-name')) ?? id,
      description: asString(pick(data, 'description')) ?? '',
      when_to_use:
        asString(pick(data, 'when_to_use', 'when-to-use')) ?? null,
      skills: skillIds.map((skillId) => ({
        id: skillId,
        name: skillById.get(skillId)?.name ?? skillId,
      })),
      mcp_servers: mcpIds.map((serverId) => ({
        id: serverId,
        name: serverId,
      })),
      source: 'project',
      file_path: displayPath(repoRoot, file),
      enabled: asBoolean(pick(data, 'enabled'), true),
    };
    agents.push(agent);
    agentBodies.set(id, body);
  }
  agents.sort((a, b) => a.id.localeCompare(b.id));

  // --- Commands -----------------------------------------------------------
  const commands: CommandCapability[] = [];
  for (const file of walk(join(extensionRoot, 'commands'))) {
    if (!file.endsWith('.md')) continue;
    const text = readFileSync(file, 'utf8');
    const { data, body } = parseFrontmatter(text);
    const rel = relative(join(extensionRoot, 'commands'), file)
      .replace(/\\/g, '/')
      .replace(/\.md$/, '');
    const declaredName = asString(pick(data, 'name'));
    const id = declaredName
      ? declaredName.replace(/^\//, '')
      : rel.replace(/\//g, ':');
    const allowed = pick(data, 'allowed-tools', 'allowed_tools');
    const skillIds = asStringList(pick(data, 'skills', 'skill_ids'));
    const mcpIds = asStringList(
      pick(data, 'mcp_server_ids', 'mcp-servers', 'mcp_servers'),
    );
    const command: CommandCapability = {
      id,
      name: id,
      description: asString(pick(data, 'description')) ?? '',
      argument_hint:
        asString(pick(data, 'argument-hint', 'argument_hint')) ?? null,
      arguments: asStringList(pick(data, 'arguments')),
      when_to_use:
        asString(pick(data, 'when_to_use', 'when-to-use')) ?? null,
      allowed_tools:
        Array.isArray(allowed)
          ? (allowed as unknown[]).map(String)
          : asString(allowed) ?? null,
      agent_id: asString(pick(data, 'agent')) ?? null,
      skills: skillIds.map((skillId) => ({
        id: skillId,
        name: skillById.get(skillId)?.name ?? skillId,
      })),
      mcp_servers: mcpIds.map((serverId) => ({
        id: serverId,
        name: serverId,
      })),
      source: 'project',
      file_path: displayPath(repoRoot, file),
      user_invocable: asBoolean(
        pick(data, 'user-invocable', 'user_invocable', 'userInvocable'),
        true,
      ),
      enabled: asBoolean(pick(data, 'enabled'), true),
    };
    commands.push(command);
    commandBodies.set(id, body);
  }
  commands.sort((a, b) => a.id.localeCompare(b.id));

  // --- MCP servers --------------------------------------------------------
  const mcpFile = join(extensionRoot, 'mcp-configs', 'mcp-servers.json');
  const mcpConfig = readJson<{
    mcpServers?: Record<string, Record<string, unknown>>;
  }>(mcpFile);
  const mcpServers: McpCapability[] = Object.entries(
    mcpConfig?.mcpServers ?? {},
  ).map(([id, server]) => ({
    id,
    name: asString(server.name) ?? id,
    description: asString(server.description) ?? '',
    transport:
      String(server.type ?? '').toLowerCase() === 'http'
        ? 'streamable_http'
        : 'stdio',
    status: 'idle',
    tool_count: 0,
    tools: [],
    error: null,
    updated_at: null,
    source: 'project',
    file_path: displayPath(repoRoot, mcpFile),
  }));
  const mcpById = new Map(mcpServers.map((server) => [server.id, server]));
  for (const agent of agents) {
    agent.mcp_servers = agent.mcp_servers.map((ref) => ({
      id: ref.id,
      name: mcpById.get(ref.id)?.name ?? ref.id,
    }));
  }
  for (const command of commands) {
    command.mcp_servers = command.mcp_servers.map((ref) => ({
      id: ref.id,
      name: mcpById.get(ref.id)?.name ?? ref.id,
    }));
  }

  // --- Hooks --------------------------------------------------------------
  const hooksFile = join(extensionRoot, 'hooks', 'hooks.json');
  const hookConfig = readJson<{
    hooks?: Record<
      string,
      Array<{ description?: string; hooks?: Array<{ command?: string }> }>
    >;
  }>(hooksFile);
  const hooks: HookCapability[] = [];
  let hookIndex = 0;
  for (const [event, groups] of Object.entries(hookConfig?.hooks ?? {})) {
    for (const group of groups) {
      hookIndex += 1;
      const firstCommand = group.hooks?.[0]?.command ?? '';
      hooks.push({
        id: `${event}:${hookIndex}`,
        name: group.description || firstCommand || `${event} hook`,
        event,
        enabled: true,
        source: 'project',
      });
    }
  }

  // Slash-name conflicts across commands and user-invocable skills.
  const owners = new Map<string, string[]>();
  for (const item of [...commands, ...skills]) {
    if ('user_invocable' in item && !item.user_invocable) continue;
    const list = owners.get(item.id) ?? [];
    list.push(item.file_path ?? item.id);
    owners.set(item.id, list);
  }
  const slash_conflicts = [...owners.entries()]
    .filter(([, list]) => list.length > 1)
    .map(([name, list]) => ({ name, owners: list }));

  return {
    catalog: {
      version: 2,
      agents,
      skills,
      commands,
      mcp_servers: mcpServers,
      hooks,
      counts: {
        agents: agents.length,
        skills: skills.length,
        commands: commands.length,
        mcp_servers: mcpServers.length,
        hooks: hooks.length,
      },
      diagnostics: { slash_conflicts },
    },
    commandBodies,
    skillBodies,
    agentBodies,
    extensionRoot,
  };
}
