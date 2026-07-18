import { Component, EventEmitter, Input, Output } from '@angular/core';
import { Agent, SoulProfile } from '../../claude.service';

@Component({
  selector: 'app-agent-panel',
  standalone: true,
  templateUrl: './agent-panel.html',
})
export class AgentPanelComponent {
  // Sorted/filtered snapshot — App keeps sortedAgents() since it depends on
  // rightPanelFilter(), shared across right-panel tabs.
  @Input() agents: Agent[] = [];
  // activeAgentId is just an alias for selectedAgent() (App-wide, drives
  // the main chat too), read-only snapshot.
  @Input() activeAgentId = '';
  // expandedAgentId/expandedTranslation are App-owned, not local like
  // Teams' expandedTeams: a global Escape-key handler (window:keydown)
  // resets expandedAgentId/expandedSkillId/expandedMcpId together
  // regardless of which tab is mounted, so the source of truth must stay
  // in App. expandedTranslation is also shared with the skills tab.
  @Input() expandedAgentId = '';
  @Input() expandedTranslation: string | null = null;
  // For the expanded detail view's soul-persona preview — a single
  // low-frequency lookup, simpler to pass the list than precompute a Map.
  @Input() souls: SoulProfile[] = [];

  @Output() toggleExpand = new EventEmitter<string>();
  @Output() activate = new EventEmitter<Agent>();
  @Output() favorite = new EventEmitter<Agent>();
  @Output() edit = new EventEmitter<Agent | undefined>();
  @Output() translate = new EventEmitter<string>();
  @Output() jumpToSkill = new EventEmitter<string>();
  @Output() jumpToMcp = new EventEmitter<string>();
  @Output() removeSkillPerm = new EventEmitter<{ agentId: string; skillId: string }>();
  @Output() removeMcpPerm = new EventEmitter<{ agentId: string; mcpName: string }>();
  @Output() openHelp = new EventEmitter<void>();

  getAgentSoulContent(soulId: string): string {
    const s = this.souls.find(x => x.id === soulId);
    return s ? s.content : '';
  }
}
