import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';
import { SettingsService } from './settings.service';

export interface Agent {
  id: string; name: string; description: string;
  soul: string; skills: string[]; memory: string[]; mcp: string[]; output_memory: string[]; tools: string;
  engine?: string;
}
export interface Skill {
  id: string; name: string; description: string; type?: string;
  mcp: string[]; memory: string[]; output_memory: string[];
}
export interface Session {
  id: string; title: string; mtime: number; snippet?: string;
  projectDir?: string; projectPath?: string; messageCount?: number;
}
export interface Profile { slug: string; mtime: number; memoryCount: number; hasSoul: boolean; hasSchedules: boolean; }
export interface SoulProfile { id: string; name: string; content: string; }

// App 自己的 MCP server 定義單一來源——新增/刪除會同步到 Claude／Codex
// 兩邊 CLI 的原生設定（backend/mcp_sync.py）。跟既有 MCP 面板顯示的
// externalMcpServers/localMcpServers（parse `claude mcp list` 輸出）是
// 不同的資料來源，這裡是 app 自己記錄、雙引擎都看得到的那份。
export interface McpServerDef {
  name?: string;
  type: 'stdio' | 'http';
  command?: string; args?: string[]; env?: Record<string, string>;
  url?: string; headers?: Record<string, string>;
  synced?: { claude: boolean; codex: boolean };
}

// 引擎可用性偵測（backend/engines/availability.py）——只做「已安裝／已登入」
// 偵測，用量／額度數字兩邊 CLI 都沒有可腳本化的資料來源，這輪沒有做。
export interface EngineAvailability {
  installed: boolean;
  loggedIn: boolean;
  available: boolean;
  reason: '' | 'not_installed' | 'not_logged_in' | 'check_timeout' | 'unexpected_output';
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'tool' | 'error' | 'system';
  text: string;
  toolName?: string;
  isStreaming?: boolean;
  isRunning?: boolean;
  toolUseId?: string;
  result?: string;
  time?: string;
  startTime?: number;
  cost?: number;
  teamRun?: TeamRun;
  agentId?: string;
  pendingExec?: {
    teamId: string;
    projectPath: string;
    task: string;
    projectName: string;
  };
  hasExecuted?: boolean;
}

export interface TeamMember {
  agent: string;
  role: string;
  input_memory?: string[];   // P2-B2: per-member memory keys to read
  output_memory?: string[];  // P2-B2: per-member memory keys to write
}
export interface Team { id: string; name: string; description: string; leader?: string; members: TeamMember[]; execution_mode?: 'parallel' | 'sequential'; }
export interface TeamRunStep {
  agent: string;
  role: string;
  status: 'pending' | 'running' | 'done' | 'error' | 'pending_permission';
  output: string;
  requestId?: string;
  command?: string;
}
export interface TeamRun {
  id: string; team_id: string; name: string; task: string;
  status: 'running' | 'done' | 'cancelled' | 'error';
  steps: TeamRunStep[]; summary: string;
}

export interface Schedule {
  id: string;
  prompt: string;
  cron: string;
  enabled: boolean;
  last_run?: string;
  delivery?: { channel: string; to: string };
}

export interface FileItem {
  name: string;
  path: string;
  isDir: boolean;
}

export interface ChatTab {
  id: string;
  clientId: string;
  label: string;
  messages: ChatMessage[];
  tokenUsage: { input: number; output: number; cost: number } | null;
  selectedAgent: string;
  isStreaming: boolean;
  sessionSkills: string[];  // 一次性：本對話框有效
  sessionMcps: string[];    // 一次性：本對話框有效
  projectDir: string;       // 建立時繼承 workDir，送出第一則訊息後視為鎖定
  teamId?: string;          // 綁定的團隊 ID，若有則為與組長對話情境
  draft?: string;           // 對話框尚未傳送的草稿文字
}

@Injectable({ providedIn: 'root' })
export class ClaudeService {
  clientId = `client-${Date.now()}`;

  constructor(private http: HttpClient, private settings: SettingsService) {}

  private get api(): string {
    const s = this.settings.get();
    if (s.backendUrl) return s.backendUrl.replace(/\/$/, '') + '/api';
    return `http://localhost:${s.backendPort}/api`;
  }

  getAgents(): Observable<Agent[]>  { return this.http.get<Agent[]>(`${this.api}/agents`); }
  getAgent(id: string): Observable<Agent> { return this.http.get<Agent>(`${this.api}/agents/${id}`); }
  createAgent(data: Partial<Agent>): Observable<{ ok: boolean; id: string }> {
    return this.http.post<{ ok: boolean; id: string }>(`${this.api}/agents`, data);
  }
  updateAgent(id: string, data: Partial<Agent>): Observable<{ ok: boolean }> {
    return this.http.put<{ ok: boolean }>(`${this.api}/agents/${id}`, data);
  }

  listMcpServers(): Observable<Record<string, McpServerDef>> {
    return this.http.get<Record<string, McpServerDef>>(`${this.api}/mcp-servers`);
  }
  createMcpServer(name: string, data: McpServerDef): Observable<McpServerDef & { ok: boolean; name: string }> {
    return this.http.post<McpServerDef & { ok: boolean; name: string }>(`${this.api}/mcp-servers`, { name, ...data });
  }
  deleteMcpServer(name: string): Observable<{ ok: boolean; synced: { claude: boolean; codex: boolean } }> {
    return this.http.delete<{ ok: boolean; synced: { claude: boolean; codex: boolean } }>(`${this.api}/mcp-servers/${name}`);
  }

  getEngineStatus(force = false): Observable<Record<string, EngineAvailability>> {
    const q = force ? '?force=1' : '';
    return this.http.get<Record<string, EngineAvailability>>(`${this.api}/engines/status${q}`);
  }
  deleteAgent(id: string): Observable<{ ok: boolean }> {
    return this.http.delete<{ ok: boolean }>(`${this.api}/agents/${id}`);
  }
  importAgencyAgents(): Observable<{ ok: boolean; message: string }> {
    return this.http.post<{ ok: boolean; message: string }>(`${this.api}/agents/import-agency`, {});
  }

  getSkills(): Observable<Skill[]> { return this.http.get<Skill[]>(`${this.api}/skills`); }
  getSkill(id: string): Observable<Skill> { return this.http.get<Skill>(`${this.api}/skills/${id}`); }
  updateSkill(id: string, data: Partial<Skill>): Observable<{ ok: boolean }> {
    return this.http.put<{ ok: boolean }>(`${this.api}/skills/${id}`, data);
  }
  getSessions(q = '', offset = 0): Observable<{ items: Session[]; has_more: boolean }> {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (offset) params.set('offset', String(offset));
    const url = `${this.api}/sessions${params.toString() ? '?' + params : ''}`;
    return this.http.get<{ items: Session[]; has_more: boolean }>(url);
  }
  getMemory(): Observable<Record<string, string>> {
    return this.http.get<Record<string, string>>(`${this.api}/memory`);
  }
  deleteMemory(key: string): Observable<any> {
    return this.http.delete(`${this.api}/memory/${key}`);
  }

  getSoul(): Observable<string> {
    return this.http.get<{ content: string }>(`${this.api}/soul`).pipe(
      map(r => r.content)
    );
  }
  saveSoul(content: string): Observable<any> {
    return this.http.put(`${this.api}/soul`, { content });
  }

  getSouls(): Observable<SoulProfile[]> {
    return this.http.get<SoulProfile[]>(`${this.api}/souls`);
  }
  saveSoulProfile(id: string, content: string): Observable<any> {
    return this.http.put(`${this.api}/souls/${id}`, { content });
  }
  deleteSoulProfile(id: string): Observable<any> {
    return this.http.delete(`${this.api}/souls/${id}`);
  }

  renameSoulProfile(oldId: string, newName: string): Observable<{ ok: boolean; id: string }> {
    return this.http.patch<{ ok: boolean; id: string }>(
      `${this.api}/souls/${encodeURIComponent(oldId)}`, { new_name: newName }
    );
  }

  translate(text: string): Observable<string> {
    return this.http.post<{ result: string }>(`${this.api}/translate`, { text }).pipe(
      map(r => r.result)
    );
  }

  getStatus(): Observable<any>     { return this.http.get<any>(`${this.api}/status`); }
  getStats(): Observable<any>      { return this.http.get<any>(`${this.api}/stats`); }
  getFiles(path?: string): Observable<{ path: string; parent: string; items: FileItem[] }> {
    const url = `${this.api}/files${path ? '?path=' + encodeURIComponent(path) : ''}`;
    return this.http.get<any>(url);
  }
  getLogs():   Observable<string[]> {
    return this.http.get<{ logs: string[] }>(`${this.api}/logs`).pipe(map(r => r.logs));
  }

  getTeams(): Observable<Team[]> { return this.http.get<Team[]>(`${this.api}/teams`); }
  getTeam(id: string): Observable<Team> { return this.http.get<Team>(`${this.api}/teams/${id}`); }
  createTeam(data: Partial<Team>): Observable<{ ok: boolean; id: string }> {
    return this.http.post<{ ok: boolean; id: string }>(`${this.api}/teams`, data);
  }
  updateTeam(id: string, data: Partial<Team>): Observable<{ ok: boolean }> {
    return this.http.put<{ ok: boolean }>(`${this.api}/teams/${id}`, data);
  }
  deleteTeam(id: string): Observable<{ ok: boolean }> {
    return this.http.delete<{ ok: boolean }>(`${this.api}/teams/${id}`);
  }

  runTeam(teamId: string, task: string, model?: string, cwd?: string, team?: any, agentEngine?: string): Observable<{ ok: boolean; run_id: string }> {
    return this.http.post<{ ok: boolean; run_id: string }>(`${this.api}/team/run`, {
      team_id: teamId, task, model: model ?? '', cwd: cwd ?? '', team,
      agent_engine: agentEngine ?? '',
    });
  }

  dispatchHR(task: string, engine?: string): Observable<any> {
    return this.http.post<any>(`${this.api}/hr/dispatch`, { task, engine: engine ?? '' });
  }

  getTeamRun(runId: string): Observable<TeamRun> {
    return this.http.get<TeamRun>(`${this.api}/team/run/${runId}`);
  }

  cancelTeamRun(runId: string): Observable<{ ok: boolean }> {
    return this.http.delete<{ ok: boolean }>(`${this.api}/team/run/${runId}`);
  }

  streamTeamRun(
    runId: string,
    onEvent: (ev: any) => void,
    onDone: () => void,
    onError: (e: any) => void,
  ): () => void {
    const s = this.settings.get();
    const api = s.backendUrl ? s.backendUrl.replace(/\/$/, '') + '/api' : `http://localhost:${s.backendPort}/api`;
    const controller = new AbortController();
    fetch(`${api}/team/run/${runId}/stream`, { signal: controller.signal })
      .then(async (res) => {
        if (!res.body) { onError(new Error('no response body')); return; }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const parts = buf.split('\n\n');
          buf = parts.pop() ?? '';
          for (const part of parts) {
            const line = part.replace(/^data: /, '').trim();
            if (!line) continue;
            try { onEvent(JSON.parse(line)); } catch {}
          }
        }
        onDone();
      })
      .catch(e => { if (e?.name !== 'AbortError') onError(e); });
    return () => controller.abort();
  }

  getSchedules(): Observable<Schedule[]> { return this.http.get<Schedule[]>(`${this.api}/schedules`); }
  addSchedule(prompt: string, cron: string, delivery?: { channel: string; to: string }): Observable<any> {
    return this.http.post(`${this.api}/schedules`, { prompt, cron, delivery });
  }
  parseCron(text: string): Observable<{ cron: string }> {
    return this.http.post<{ cron: string }>(`${this.api}/schedules/parse-cron`, { text });
  }
  deleteSchedule(id: string): Observable<any> {
    return this.http.delete(`${this.api}/schedules/${id}`);
  }
  toggleSchedule(id: string, enabled: boolean): Observable<any> {
    return this.http.patch(`${this.api}/schedules/${id}`, { enabled });
  }
  runSchedule(id: string): Observable<any> {
    return this.http.post(`${this.api}/schedules/${id}/run`, {});
  }

  uploadFile(file: File): Promise<{ path: string; name: string }> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = async () => {
        const b64 = (reader.result as string).split(',')[1];
        const res = await fetch(`${this.api}/upload`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ data: b64, name: file.name }),
        });
        if (res.ok) resolve(await res.json());
        else reject(new Error('Upload failed'));
      };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  resumeSession(sessionId: string): Observable<any> {
    return this.http.post(`${this.api}/sessions/resume`, {
      client_id: this.clientId,
      session_id: sessionId,
    });
  }

  getSessionMessages(sessionId: string): Observable<{ messages: ChatMessage[] }> {
    return this.http.get<{ messages: ChatMessage[] }>(`${this.api}/sessions/${sessionId}/messages`);
  }

  getUsage(): Observable<any> {
    return this.http.get<any>(`${this.api}/usage`);
  }

  deleteSession(id: string): Observable<any> {
    return this.http.delete(`${this.api}/sessions/${id}`);
  }

  truncateSession(id: string, count: number): Observable<any> {
    return this.http.post(`${this.api}/sessions/${id}/truncate`, { count });
  }

  renameSession(id: string, title: string): Observable<any> {
    return this.http.patch(`${this.api}/sessions/${id}`, { title });
  }

  saveMemory(key: string, content: string): Observable<any> {
    return this.http.put(`${this.api}/memory/${key}`, { content });
  }

  resetSoul(): Observable<any> { return this.http.delete(`${this.api}/soul`); }

  startMcp(name: string):   Observable<any> { return this.http.post(`${this.api}/mcp/${encodeURIComponent(name)}/start`,   {}); }
  stopMcp(name: string):    Observable<any> { return this.http.post(`${this.api}/mcp/${encodeURIComponent(name)}/stop`,    {}); }
  restartMcp(name: string): Observable<any> { return this.http.post(`${this.api}/mcp/${encodeURIComponent(name)}/restart`, {}); }

  getMcpInfo(name: string): Observable<any> {
    return this.http.get<any>(`${this.api}/mcp/${encodeURIComponent(name)}/info`);
  }
  getLocalMcpConfig(): Observable<Record<string, any>> {
    return this.http.get<Record<string, any>>(`${this.api}/mcp-local-config`);
  }
  saveLocalMcpConfig(name: string, cfg: any): Observable<any> {
    return this.http.put(`${this.api}/mcp-local-config/${encodeURIComponent(name)}`, cfg);
  }

  restoreBackup(file: File): Promise<any> {
    const form = new FormData();
    form.append('file', file);
    return fetch(`${this.api}/restore`, { method: 'POST', body: form }).then(r => r.json());
  }

  stopChat(clientId?: string): Observable<any> {
    return this.http.post(`${this.api}/chat/stop`, { client_id: clientId || this.clientId });
  }

  clearChat(clientId?: string): Observable<any> {
    return this.http.post(`${this.api}/chat/clear`, { client_id: clientId || this.clientId });
  }

  runCliCommand(args: string[]): Observable<string> {
    return this.http.post<{ output: string }>(`${this.api}/cli`, { args }).pipe(map(r => r.output));
  }

  getConfig(): Observable<{ projectDir: string; slug?: string; engineMode?: 'claude' | 'codex' | 'both' }> {
    return this.http.get<{ projectDir: string; slug?: string; engineMode?: 'claude' | 'codex' | 'both' }>(`${this.api}/config`);
  }

  setConfig(cfg: { projectDir?: string; apiKeyCmd?: string; codexApiKeyCmd?: string; claudeHome?: string; engineMode?: 'claude' | 'codex' | 'both' }): Observable<{ ok: boolean; slug: string }> {
    return this.http.put<{ ok: boolean; slug: string }>(`${this.api}/config`, cfg);
  }

  generateSkill(sessionId: string): Observable<{ ok: boolean; slug: string; path: string; content: string }> {
    return this.http.post<{ ok: boolean; slug: string; path: string; content: string }>(
      `${this.api}/skills/generate`, { session_id: sessionId }
    );
  }

  autoTitleSession(sessionId: string): Observable<{ ok: boolean; title: string }> {
    return this.http.post<{ ok: boolean; title: string }>(
      `${this.api}/sessions/${sessionId}/auto-title`, {}
    );
  }

  getMcpLogs(name: string): Observable<{ name: string; lines: string[] }> {
    return this.http.get<{ name: string; lines: string[] }>(
      `${this.api}/mcp/${encodeURIComponent(name)}/logs`
    );
  }

  getProfiles(): Observable<{ profiles: Profile[]; current: string }> {
    return this.http.get<{ profiles: Profile[]; current: string }>(`${this.api}/profiles`);
  }

  getTelegram(): Observable<{ token: string; enabled: boolean; running: boolean }> {
    return this.http.get<any>(`${this.api}/telegram`);
  }

  setTelegram(cfg: { token?: string; enabled?: boolean }): Observable<{ ok: boolean; running: boolean }> {
    return this.http.put<any>(`${this.api}/telegram`, cfg);
  }

  debugDumpUrl(): string {
    return `${this.api}/debug-dump`;
  }

  getMemoryOverview(): Observable<any> {
    return this.http.get<any>(`${this.api}/mem/overview`);
  }

  putMemoryUser(content: string): Observable<any> {
    return this.http.put(`${this.api}/mem/user`, { content });
  }

  putMemorySystem(content: string): Observable<any> {
    return this.http.put(`${this.api}/mem/system`, { content });
  }

  putMemoryAgent(agentId: string, content: string): Observable<any> {
    return this.http.put(`${this.api}/mem/agents/${encodeURIComponent(agentId)}`, { content });
  }

  putMemoryTeam(teamId: string, content: string): Observable<any> {
    return this.http.put(`${this.api}/mem/teams/${encodeURIComponent(teamId)}`, { content });
  }

  streamProviderChat(
    messages: { role: string; content: string }[],
    onEvent: (ev: any) => void,
    onDone: () => void,
    onError: (e: any) => void,
  ): () => void {
    const s       = this.settings.get();
    const controller = new AbortController();
    const PRESET_URLS: Record<string, string> = {
      openai:     'https://api.openai.com/v1',
      openrouter: 'https://openrouter.ai/api/v1',
      gemini:     'https://generativelanguage.googleapis.com/v1beta/openai',
    };
    const apiUrl  = s.providerApiUrl || PRESET_URLS[s.provider] || 'https://api.openai.com/v1';
    fetch(`${this.api}/chat/provider`, {
      method: 'POST',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages, apiUrl, apiKey: s.providerApiKey, model: s.providerModel || 'gpt-4o-mini' }),
    }).then(async (res) => {
      const reader  = res.body!.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop() ?? '';
        for (const part of parts) {
          const line = part.replace(/^data: /, '').trim();
          if (!line) continue;
          try { onEvent(JSON.parse(line)); } catch {}
        }
      }
      onDone();
    }).catch(e => { if (e?.name !== 'AbortError') onError(e); });
    return () => controller.abort();
  }

  async pickDirectory(): Promise<string | null> {
    const api = (window as any).electronAPI;
    if (api?.openDirectory) return api.openDirectory();
    return null;
  }

  streamChat(
    message: string,
    agent: string,
    onEvent: (ev: any) => void,
    onDone: () => void,
    onError: (e: any) => void,
    attachments: string[] = [],
    cwdOverride?: string,        // 對話欄鎖定的目錄，優先於 settings.workDir
    teamId?: string,
    clientId?: string            // 對話分頁的 clientId
  ): () => void {
    const controller = new AbortController();
    const s = this.settings.get();
    fetch(`${this.api}/chat`, {
      method: 'POST',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        agent,
        client_id: clientId || this.clientId,
        cwd: cwdOverride || s.workDir || undefined,
        claude_bin: s.claudeBin !== 'claude' ? s.claudeBin : undefined,
        codex_bin: s.codexBin !== 'codex' ? s.codexBin : undefined,
        attachments,
        model: s.model,
        effort: s.effort,
        permission_mode: s.permissionMode,
        team_id: teamId,
      }),
    })
      .then(async (res) => {
        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split('\n\n');
          buffer = parts.pop() ?? '';
          for (const part of parts) {
            const line = part.replace(/^data: /, '').trim();
            if (!line) continue;
            try { onEvent(JSON.parse(line)); } catch {}
          }
        }
        onDone();
      })
      .catch(e => { if (e?.name !== 'AbortError') onError(e); });
    return () => controller.abort();
  }

  streamTeamChat(
    message: string,
    teamId: string,
    onEvent: (ev: any) => void,
    onDone: () => void,
    onError: (e: any) => void,
    attachments: string[] = [],
    cwdOverride?: string,
    clientId?: string            // 對話分頁的 clientId
  ): () => void {
    const controller = new AbortController();
    const s = this.settings.get();
    fetch(`${this.api}/team/chat`, {
      method: 'POST',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        team_id: teamId,
        client_id: clientId || this.clientId,
        cwd: cwdOverride || s.workDir || undefined,
        claude_bin: s.claudeBin !== 'claude' ? s.claudeBin : undefined,
        codex_bin: s.codexBin !== 'codex' ? s.codexBin : undefined,
        attachments,
        model: s.model,
        effort: s.effort,
        permission_mode: s.permissionMode,
      }),
    })
      .then(async (res) => {
        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split('\n\n');
          buffer = parts.pop() ?? '';
          for (const part of parts) {
            const line = part.replace(/^data: /, '').trim();
            if (!line) continue;
            try { onEvent(JSON.parse(line)); } catch {}
          }
        }
        onDone();
      })
      .catch(e => { if (e?.name !== 'AbortError') onError(e); });
    return () => controller.abort();
  }

  executeTeamTask(
    teamId: string,
    projectPath: string,
    task: string,
    onEvent: (ev: any) => void,
    onDone: () => void,
    onError: (e: any) => void,
    clientId?: string            // 對話分頁的 clientId
  ): () => void {
    const controller = new AbortController();
    const s = this.settings.get();
    fetch(`${this.api}/team/execute`, {
      method: 'POST',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        team_id: teamId,
        project_path: projectPath,
        task,
        client_id: clientId || this.clientId,
        claude_bin: s.claudeBin !== 'claude' ? s.claudeBin : undefined,
        codex_bin: s.codexBin !== 'codex' ? s.codexBin : undefined,
        model: s.model,
        effort: s.effort,
        permission_mode: s.permissionMode,
      }),
    })
      .then(async (res) => {
        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split('\n\n');
          buffer = parts.pop() ?? '';
          for (const part of parts) {
            const line = part.replace(/^data: /, '').trim();
            if (!line) continue;
            try { onEvent(JSON.parse(line)); } catch {}
          }
        }
        onDone();
      })
      .catch(e => { if (e?.name !== 'AbortError') onError(e); });
    return () => controller.abort();
  }

  authorizeTeamTask(requestId: string, decision: 'approve' | 'reject'): Observable<any> {
    return this.http.post(`${this.api}/team/authorize`, { request_id: requestId, decision });
  }

  sendMcpRpc(mcpName: string, method: string, params: any, authorized?: boolean, pendingId?: string): Observable<any> {
    return this.http.post(`${this.api}/mcp/rpc`, {
      mcp_name: mcpName,
      method,
      params,
      authorized,
      pending_id: pendingId
    });
  }

  getTeamRunArtifacts(runId: string): Observable<any> {
    return this.http.get(`${this.api}/team/run/${runId}/artifacts`);
  }
}
