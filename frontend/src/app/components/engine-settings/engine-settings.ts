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
    quota_exhausted: '用量已滿', runtime_error: '最近執行失敗',
  };
  private readonly ENGINE_STATE_LABEL: Record<string, string> = {
    ready: '已就緒',
    quota_low: '用量偏低',
    quota_exhausted: '用量已滿',
    not_installed: '未安裝',
    not_logged_in: '未登入',
    check_timeout: '狀態檢查逾時',
    unexpected_output: '狀態檢查失敗',
    runtime_error: '最近執行失敗',
    unknown: '狀態未知',
  };

  engineOptionDisabled(_name: 'claude' | 'codex'): boolean {
    return false;
  }

  engineRunnable(name: 'claude' | 'codex'): boolean {
    const s = this.engineStatus[name];
    return !s || (s.runnable ?? s.available) === true;
  }

  engineStatusLabel(name: 'claude' | 'codex'): string {
    const s = this.engineStatus[name];
    if (!s) return '檢查中';
    const state = s.state || (s.available ? 'ready' : (s.reason || 'unknown'));
    return this.ENGINE_STATE_LABEL[state] || this.ENGINE_REASON_LABEL[s.reason] || '不可用';
  }

  engineStatusDetail(name: 'claude' | 'codex'): string {
    const s = this.engineStatus[name];
    if (!s) return '正在檢查本機 CLI 狀態。';
    return s.detail || `${this.ENGINE_LABEL[name]}：${this.engineStatusLabel(name)}`;
  }

  engineOptionLabel(name: 'claude' | 'codex'): string {
    const s = this.engineStatus[name];
    const base = this.ENGINE_LABEL[name];
    if (!s || this.engineRunnable(name)) return base;
    const reason = this.engineStatusLabel(name);
    return `${base}（${reason}）`;
  }
}
