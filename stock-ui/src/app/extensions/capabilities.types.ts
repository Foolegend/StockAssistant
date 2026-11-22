export interface CapabilityRef {
  id: string;
  name: string;
}

export interface AgentCapability extends CapabilityRef {
  description: string;
  when_to_use?: string | null;
  skills: CapabilityRef[];
  mcp_servers: CapabilityRef[];
  source: string;
  file_path?: string | null;
  enabled: boolean;
}

export interface SkillCapability extends CapabilityRef {
  description: string;
  when_to_use?: string | null;
  allowed_tools?: string | string[] | null;
  version?: string | null;
  aliases: string[];
  source: string;
  file_path?: string | null;
  user_invocable: boolean;
  enabled: boolean;
}

export interface CommandCapability extends CapabilityRef {
  description: string;
  argument_hint?: string | null;
  arguments: string[];
  when_to_use?: string | null;
  allowed_tools?: string | string[] | null;
  agent_id?: string | null;
  skills: CapabilityRef[];
  mcp_servers: CapabilityRef[];
  source: string;
  file_path?: string | null;
  user_invocable: boolean;
  enabled: boolean;
}

export interface McpCapability extends CapabilityRef {
  description: string;
  transport: 'stdio' | 'streamable_http';
  status: 'idle' | 'connected' | 'error';
  tool_count: number;
  tools: string[];
  error?: string | null;
  updated_at?: string | null;
  source: string;
  file_path?: string | null;
}

export interface HookCapability extends CapabilityRef {
  event: string;
  enabled: boolean;
  source: string;
}

export interface CapabilityCounts {
  agents: number;
  skills: number;
  commands: number;
  mcp_servers: number;
  hooks: number;
}

export interface CapabilityDiagnostics {
  slash_conflicts?: Array<{ name: string; owners: string[] }>;
}

export interface CapabilityCatalog {
  version: number;
  agents: AgentCapability[];
  skills: SkillCapability[];
  commands: CommandCapability[];
  mcp_servers: McpCapability[];
  hooks: HookCapability[];
  counts: CapabilityCounts;
  diagnostics?: CapabilityDiagnostics;
}

export type CapabilityKind = 'command' | 'skill';

export interface SlashSuggestion {
  kind: CapabilityKind;
  id: string;
  slashName: string;
  name: string;
  description: string;
  argumentHint?: string | null;
  score: number;
}

export interface UsageInfo {
  agentId?: string | null;
  agentName?: string | null;
  command?: CapabilityRef | null;
  skills: CapabilityRef[];
  mcpServers: CapabilityRef[];
}
