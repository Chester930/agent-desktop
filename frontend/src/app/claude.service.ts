import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';
import { SettingsService } from './settings.service';

export interface Agent   { id: string; name: string; description: string; }
export interface Skill   { id: string; name: string; description: string; }
export interface Session { id: string; title: string; mtime: number; snippet?: string; }
export interface SoulProfile { id: string; name: string; content: string; }

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
}

export interface Schedule {
  id: string;
  prompt: string;
  cron: string;
  enabled: boolean;
  last_run?: string;
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

  getAgents(): Observable<Agent[]>     { return this.http.get<Agent[]>(`${this.api}/agents`); }
  getSkills(): Observable<Skill[]>     { return this.http.get<Skill[]>(`${this.api}/skills`); }
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

  getSchedules(): Observable<Schedule[]> { return this.http.get<Schedule[]>(`${this.api}/schedules`); }
  addSchedule(prompt: string, cron: string): Observable<any> {
    return this.http.post(`${this.api}/schedules`, { prompt, cron });
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

  deleteSession(id: string): Observable<any> {
    return this.http.delete(`${this.api}/sessions/${id}`);
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

  restoreBackup(file: File): Promise<any> {
    const form = new FormData();
    form.append('file', file);
    return fetch(`${this.api}/restore`, { method: 'POST', body: form }).then(r => r.json());
  }

  stopChat(): Observable<any> {
    return this.http.post(`${this.api}/chat/stop`, { client_id: this.clientId });
  }

  runCliCommand(args: string[]): Observable<string> {
    return this.http.post<{ output: string }>(`${this.api}/cli`, { args }).pipe(map(r => r.output));
  }

  getConfig(): Observable<{ projectDir: string; slug?: string }> {
    return this.http.get<{ projectDir: string; slug?: string }>(`${this.api}/config`);
  }

  setConfig(cfg: { projectDir?: string; apiKeyCmd?: string }): Observable<{ ok: boolean; slug: string }> {
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
    attachments: string[] = []
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
        client_id: this.clientId,
        cwd: s.workDir || undefined,
        claude_bin: s.claudeBin !== 'claude' ? s.claudeBin : undefined,
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
}
