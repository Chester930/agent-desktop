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
  claudeHome: string;   // override ~/.claude (leave blank for default)
  // #16 Multi-provider（純聊天，走 /api/chat/provider，沒有工具呼叫、
  // 沒有 Agent/Team/Memory——跟下面的 agentEngine 是完全不同的兩個機制，
  // 不要混用）
  provider: 'claude' | 'openai' | 'openrouter' | 'gemini' | 'custom';
  providerApiUrl: string;
  providerApiKey: string;
  providerModel: string;
  // 2026-07-12：「執行引擎範圍」鎖定新增後，這個欄位語意窄化成「範圍選
  // 『兩者都開放』時，沒有 agent 自行指定／沒有 request 層級覆寫時的預設
  // 引擎」——回答的是「兩者都開放時偏好哪個」，不是「允許哪些」。「允許
  // 哪些」是後端權威的 engineMode（見下方 engineMode 欄位／
  // backend/database.py::get_engine_mode()），範圍鎖定成只用一種時，這裡
  // 的值跟 agent frontmatter 的 engine: 一樣完全不生效。
  agentEngine: 'claude' | 'codex';
  // 執行引擎範圍（後端權威來源，這裡只是本地表單暫存/快取，實際生效與否
  // 以 backend/database.py::get_engine_mode() 為準）：'claude'/'codex' 鎖定
  // 只用單一引擎，agent 自己的 engine: 覆寫在執行期完全不生效；'both' 才
  // 會照舊看 agent frontmatter／上面的 agentEngine 預設值。
  engineMode: 'claude' | 'codex' | 'both';
  // #19 i18n
  lang: 'zh' | 'en';
  // 開機自動啟動
  openAtLogin: boolean;
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
  claudeHome: '',
  provider: 'claude',
  providerApiUrl: '',
  providerApiKey: '',
  providerModel: '',
  agentEngine: 'codex',
  engineMode: 'both',
  lang: 'zh',
  openAtLogin: false,
};

const KEY = 'claude_desktop_settings';

@Injectable({ providedIn: 'root' })
export class SettingsService {
  private _settings: AppSettings;

  // 健檢第二輪修復：providerApiKey（第三方 OpenAI/OpenRouter/Gemini API key）
  // 原本跟其他設定一起被 JSON.stringify 進同一包 localStorage —— Electron 的
  // localStorage 是未加密的 LevelDB 檔案，任何本機程序、備份/同步工具，或
  // 未來的 renderer XSS 都能直接讀到明碼金鑰。改成透過 preload 暴露的
  // window.electronAPI.secureStorage（背後是 Electron safeStorage，即
  // Windows DPAPI／macOS Keychain／Linux libsecret）另外加密存放，不再
  // 進入 localStorage 那包 JSON。非 Electron 環境（例如直接用瀏覽器打開
  // docker-compose 部署的網頁版）沒有這個管道，退回原本存在 localStorage
  // 的行為，維持功能可用（風險與修復前相同，但不會比修復前更差）。
  private get secureStorage() {
    return (window as any).electronAPI?.secureStorage;
  }

  constructor() {
    const saved = localStorage.getItem(KEY);
    let parsed: Partial<AppSettings> = {};
    if (saved) {
      try {
        parsed = JSON.parse(saved);
      } catch {
        // localStorage 資料損毀，重置到預設值
        localStorage.removeItem(KEY);
        parsed = {};
      }
    }
    this._settings = { ...DEFAULTS, ...parsed };
    this.applyTheme(this._settings.theme);

    if (this.secureStorage) {
      // 舊版遺留：如果 localStorage 那包 JSON 裡還留著明碼 key，搬進安全
      // 儲存後，立刻從 localStorage 這包移除，不留舊的明碼副本。
      const legacyKey = parsed.providerApiKey;
      if (legacyKey) {
        this._settings.providerApiKey = '';
        localStorage.setItem(KEY, JSON.stringify(this._settings));
        this.secureStorage.set(legacyKey);
        this._settings.providerApiKey = legacyKey;
      } else {
        this.secureStorage.get().then((key: string) => {
          this._settings = { ...this._settings, providerApiKey: key || '' };
        });
      }
    }
  }

  get(): AppSettings { return { ...this._settings }; }

  save(s: Partial<AppSettings>): void {
    const merged = { ...this._settings, ...s };
    if (s.workDir && s.workDir !== this._settings.workDir) {
      const recent = [s.workDir, ...this._settings.recentWorkDirs.filter(d => d !== s.workDir)].slice(0, 5);
      merged.recentWorkDirs = recent;
    }
    this._settings = merged;

    if (this.secureStorage) {
      if ('providerApiKey' in s) {
        this.secureStorage.set(this._settings.providerApiKey);
      }
      // 寫進 localStorage 前一律把明碼 key 拿掉，安全儲存那邊才是它唯一該存在的地方。
      localStorage.setItem(KEY, JSON.stringify({ ...this._settings, providerApiKey: '' }));
    } else {
      localStorage.setItem(KEY, JSON.stringify(this._settings));
    }
    this.applyTheme(this._settings.theme);
  }

  applyTheme(theme: 'dark' | 'light'): void {
    document.documentElement.setAttribute('data-theme', theme);
  }
}
