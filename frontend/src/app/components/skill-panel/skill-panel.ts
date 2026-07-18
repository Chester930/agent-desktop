import { Component, EventEmitter, Input, Output } from '@angular/core';
import { Skill } from '../../claude.service';

@Component({
  selector: 'app-skill-panel',
  standalone: true,
  templateUrl: './skill-panel.html',
})
export class SkillPanelComponent {
  // Sorted/filtered snapshot — App keeps sortedSkills() since it depends on
  // rightPanelFilter()/selectedAgent(), shared across right-panel tabs.
  @Input() skills: Skill[] = [];
  @Input() selectedAgent = '';
  // Precomputed membership sets instead of passing isSkillLinkedToActiveAgent/
  // isSkillInActiveAgentFrontmatter/isSkillInTab as function @Inputs.
  @Input() linkedSkillIds = new Set<string>();
  @Input() frontmatterSkillIds = new Set<string>();
  @Input() tabSkillIds = new Set<string>();
  // expandedTranslation is shared across agents/skills/mcp tabs (App owns
  // it); expandedSkillId is written cross-tab too (agents tab's "jump to
  // skill" link), so both stay App-owned read-only snapshots.
  @Input() expandedSkillId = '';
  @Input() expandedTranslation: string | null = null;

  @Output() toggleExpand = new EventEmitter<string>();
  @Output() toggleInTab = new EventEmitter<string>();
  @Output() edit = new EventEmitter<Skill>();
  @Output() translate = new EventEmitter<string>();
  @Output() jumpToMcp = new EventEmitter<string>();
  @Output() openHelp = new EventEmitter<void>();

  // Static lookup, duplicated from App (which still needs its own copy for
  // sortedMcpServers' internal use) — same reasoning as ENGINE_LABEL.
  private readonly SKILL_MCPS_MAP: Record<string, string[]> = {
    'google-agents-cli-adk-code': ['claude.ai Play Sheet Music', 'claude.ai Digits'],
    'google-agents-cli-deploy': ['claude.ai Google Drive', 'claude.ai Box'],
    'google-agents-cli-eval': ['claude.ai Linear', 'claude.ai Ticket Tailor'],
    'google-agents-cli-observability': ['claude.ai Gamma', 'claude.ai Gmail'],
    'google-agents-cli-publish': ['claude.ai Google Calendar', 'claude.ai Google Drive'],
    'google-agents-cli-scaffold': ['claude.ai Digits', 'claude.ai Box'],
    'google-agents-cli-workflow': ['claude.ai Linear', 'claude.ai Gamma']
  };

  isSkillLinkedToActiveAgent(skillId: string): boolean {
    return this.linkedSkillIds.has(skillId);
  }

  isSkillInActiveAgentFrontmatter(skillId: string): boolean {
    return this.frontmatterSkillIds.has(skillId);
  }

  isSkillInTab(skillId: string): boolean {
    return this.tabSkillIds.has(skillId);
  }

  getUsedMcps(skillId: string): string[] {
    const cleanId = skillId.replace(/^\//, '');
    return this.SKILL_MCPS_MAP[cleanId] || [];
  }
}
