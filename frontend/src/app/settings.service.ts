import { Injectable } from '@angular/core';

export interface QuickPrompt { label: string; text: string; }

export interface AppSettings {
  claudeBin: string;
  workDir: string;
  defaultAgent: string;
  backendPort: number;
  backendUrl: string;
  theme: 'dark' | 'light';
  recentWorkDirs: string[];
  quickPrompts: QuickPrompt[];
  enterToSend: boolean;
  model: string;
  effort: 'low' | 'medium' | 'high' | 'xhigh' | 'max';
  permissionMode: 'default' | 'acceptEdits' | 'bypassPermissions' | 'plan' | 'auto';
  projectDir: string;
  apiKeyCmd: string;
}

const DEFAULTS: AppSettings = {
  claudeBin: 'claude',
  workDir: '',
  defaultAgent: '',
  backendPort: 8765,
  backendUrl: '',
  theme: 'dark',
  recentWorkDirs: [],
  quickPrompts: [
    { label: '📋 程式碼審查', text: '幫我 code review 目前的 diff' },
    { label: '🏗 架構分析',   text: '描述目前的專案架構' },
    { label: '💡 改善建議',   text: '有什麼可以改善的地方？' },
    { label: '🤖 查看設定',   text: '列出目前已安裝的 skills 與 agents' },
  ],
  enterToSend: true,
  model: 'sonnet',
  effort: 'medium',
  permissionMode: 'acceptEdits',
  projectDir: '',
  apiKeyCmd: '',
};

const KEY = 'claude_desktop_settings';

@Injectable({ providedIn: 'root' })
export class SettingsService {
  private _settings: AppSettings;

  constructor() {
    const saved = localStorage.getItem(KEY);
    this._settings = saved ? { ...DEFAULTS, ...JSON.parse(saved) } : { ...DEFAULTS };
    this.applyTheme(this._settings.theme);
  }

  get(): AppSettings { return { ...this._settings }; }

  save(s: Partial<AppSettings>): void {
    const merged = { ...this._settings, ...s };
    if (s.workDir && s.workDir !== this._settings.workDir) {
      const recent = [s.workDir, ...this._settings.recentWorkDirs.filter(d => d !== s.workDir)].slice(0, 5);
      merged.recentWorkDirs = recent;
    }
    this._settings = merged;
    localStorage.setItem(KEY, JSON.stringify(this._settings));
    this.applyTheme(this._settings.theme);
  }

  applyTheme(theme: 'dark' | 'light'): void {
    document.documentElement.setAttribute('data-theme', theme);
  }
}
