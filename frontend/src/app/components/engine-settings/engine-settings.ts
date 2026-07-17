import { Component, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { EngineAvailability } from '../../claude.service';
import { AppSettings } from '../../settings.service';

@Component({
  selector: 'app-engine-settings',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './engine-settings.html',
})
export class EngineSettingsComponent {
  // Same AppSettings object App holds — [(ngModel)] mutates it in place,
  // same pattern as the other settingsForm sub-block components.
  @Input() settingsForm!: AppSettings;
  // App-wide signal App still owns (also used elsewhere, e.g. the agent
  // editor's per-agent engine override); passed as a read-only snapshot.
  @Input() engineStatus: Record<string, EngineAvailability> = {};

  // Duplicated from App (which keeps its own copies — engineOptionDisabled/
  // engineOptionLabel are also used outside the settings modal) rather than
  // threaded through as function @Inputs: these are pure lookups over the
  // engineStatus snapshot above plus two static label maps.
  private readonly ENGINE_LABEL: Record<string, string> = { claude: 'Claude Code CLI', codex: 'OpenAI Codex CLI' };
  private readonly ENGINE_REASON_LABEL: Record<string, string> = {
    not_installed: '未安裝', not_logged_in: '未登入',
    check_timeout: '狀態檢查逾時', unexpected_output: '狀態檢查失敗',
  };

  engineOptionDisabled(name: 'claude' | 'codex'): boolean {
    const s = this.engineStatus[name];
    return !!s && !s.available;
  }

  engineOptionLabel(name: 'claude' | 'codex'): string {
    const s = this.engineStatus[name];
    const base = this.ENGINE_LABEL[name];
    if (!s || s.available) return base;
    const reason = this.ENGINE_REASON_LABEL[s.reason] || '不可用';
    return `${base}（${reason}）`;
  }
}
