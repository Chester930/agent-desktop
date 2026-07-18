import { Component, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ClaudeService, Agent } from '../../claude.service';
import { AppSettings } from '../../settings.service';
import { RecentWorkDirsComponent } from '../recent-work-dirs/recent-work-dirs';

@Component({
  selector: 'app-general-settings',
  standalone: true,
  imports: [FormsModule, RecentWorkDirsComponent],
  templateUrl: './general-settings.html',
})
export class GeneralSettingsComponent {
  // Same AppSettings object App holds — [(ngModel)] mutates it in place,
  // same pattern as the other settingsForm sub-block components.
  @Input() settingsForm!: AppSettings;
  // App-wide signals/computed App still owns; passed as read-only snapshots.
  @Input() resolvedClaudeHome = '';
  @Input() dropdownAgents: Agent[] = [];

  // Pure environment check, not app state — cheaper to compute here than
  // thread through an @Input.
  readonly isElectron = !!(window as any).electronAPI;

  constructor(private claude: ClaudeService) {}

  async pickProjectDir() {
    const dir = await this.claude.pickDirectory();
    if (dir) this.settingsForm.projectDir = dir;
  }

  async pickClaudeHome() {
    const dir = await this.claude.pickDirectory();
    if (dir) this.settingsForm.claudeHome = dir;
  }
}
