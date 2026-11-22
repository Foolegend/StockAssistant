import {
  Component,
  EventEmitter,
  Input,
  Output,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  AgentCapability,
  CapabilityCatalog,
  CapabilityKind,
  CommandCapability,
  HookCapability,
  McpCapability,
  SkillCapability,
} from './capabilities.types';

export type CapabilityTab = 'commands' | 'skills' | 'agents' | 'mcp' | 'hooks';

interface TabDefinition {
  id: CapabilityTab;
  label: string;
}

@Component({
  selector: 'app-capabilities-dialog',
  imports: [FormsModule],
  templateUrl: './capabilities-dialog.component.html',
  styleUrl: './capabilities-dialog.component.css',
})
export class CapabilitiesDialogComponent {
  @Input() catalog: CapabilityCatalog | null = null;
  @Input() loading = false;
  @Input() error: string | null = null;

  @Output() closeRequested = new EventEmitter<void>();
  @Output() refreshRequested = new EventEmitter<void>();
  @Output() insertSlash = new EventEmitter<string>();

  readonly tabs: TabDefinition[] = [
    { id: 'commands', label: '命令' },
    { id: 'skills', label: '技能' },
    { id: 'agents', label: '智能体' },
    { id: 'mcp', label: 'MCP' },
    { id: 'hooks', label: '钩子' },
  ];

  activeTab: CapabilityTab = 'commands';
  search = '';

  get counts() {
    return (
      this.catalog?.counts ?? {
        commands: 0,
        skills: 0,
        agents: 0,
        mcp_servers: 0,
        hooks: 0,
      }
    );
  }

  get commands(): CommandCapability[] {
    return this.filter(this.catalog?.commands ?? [], (item) =>
      [item.id, item.name, item.description, item.when_to_use]
    );
  }

  get skills(): SkillCapability[] {
    return this.filter(this.catalog?.skills ?? [], (item) =>
      [item.id, item.name, item.description, item.when_to_use, ...item.aliases]
    );
  }

  get agents(): AgentCapability[] {
    return this.filter(this.catalog?.agents ?? [], (item) =>
      [item.id, item.name, item.description, item.when_to_use]
    );
  }

  get mcpServers(): McpCapability[] {
    return this.filter(this.catalog?.mcp_servers ?? [], (item) =>
      [item.id, item.name, item.description, ...item.tools]
    );
  }

  get hooks(): HookCapability[] {
    return this.filter(this.catalog?.hooks ?? [], (item) => [
      item.id,
      item.name,
      item.event,
    ]);
  }

  get conflicts() {
    return this.catalog?.diagnostics?.slash_conflicts ?? [];
  }

  selectTab(tab: CapabilityTab): void {
    this.activeTab = tab;
  }

  countFor(tab: CapabilityTab): number {
    if (!this.catalog) {
      return 0;
    }
    if (tab === 'mcp') {
      return this.catalog.counts.mcp_servers;
    }
    return this.catalog.counts[tab];
  }

  conflictNames(): string {
    return this.conflicts.map((item) => item.name).join('、');
  }

  close(): void {
    this.closeRequested.emit();
  }

  refresh(): void {
    this.refreshRequested.emit();
  }

  pick(kind: CapabilityKind, id: string): void {
    this.insertSlash.emit(`/${id} `);
  }

  canPick(item: CommandCapability | SkillCapability): boolean {
    return item.enabled && item.user_invocable;
  }

  statusLabel(server: McpCapability): string {
    if (server.status === 'connected') {
      return `已连接 · ${server.tool_count} 个工具`;
    }
    if (server.status === 'error') {
      return '加载失败';
    }
    return '尚未加载';
  }

  allowedTools(value: string | string[] | null | undefined): string {
    if (!value) {
      return '';
    }
    return Array.isArray(value) ? value.join(' ') : value;
  }

  private filter<T>(
    items: T[],
    fields: (item: T) => Array<string | null | undefined>,
  ): T[] {
    const query = this.search.trim().toLowerCase();
    if (!query) {
      return items;
    }
    return items.filter((item) =>
      fields(item).some(
        (value) => typeof value === 'string' && value.toLowerCase().includes(query),
      ),
    );
  }
}
