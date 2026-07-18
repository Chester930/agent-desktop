import { Component, EventEmitter, Input, Output, signal } from '@angular/core';
import { Team } from '../../claude.service';

@Component({
  selector: 'app-team-panel',
  standalone: true,
  templateUrl: './team-panel.html',
})
export class TeamPanelComponent {
  // Sorted/filtered snapshot — App keeps sortedTeams() since it depends on
  // rightPanelFilter(), a signal shared across all right-panel tabs.
  @Input() teams: Team[] = [];

  // These all need App-level behavior (team editor modal lives elsewhere
  // in app.html; selectTeamLeader touches chat/session state), so they're
  // relayed via @Output rather than moved.
  @Output() chat = new EventEmitter<Team>();
  @Output() favorite = new EventEmitter<Team>();
  @Output() edit = new EventEmitter<Team | undefined>();

  // Per-team expand/collapse — purely local to this list, not used
  // anywhere else, so it moves wholesale.
  expandedTeams = signal<Record<string, boolean>>({});

  toggleExpanded(tid: string) {
    this.expandedTeams.update(m => ({ ...m, [tid]: !m[tid] }));
  }
}
