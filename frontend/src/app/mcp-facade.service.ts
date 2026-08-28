import { Injectable, inject, signal } from '@angular/core';
import { Observable } from 'rxjs';
import { finalize } from 'rxjs/operators';
import { ClaudeService, McpServerDef } from './claude.service';

/**
 * Owns MCP list/editor state while App keeps the template-facing API stable.
 * The live CLI output and the managed server definitions are intentionally
 * separate sources: the former reflects Claude's installed state, while the
 * latter is the app's dual-engine definition registry.
 */
@Injectable({ providedIn: 'root' })
export class McpFacade {
  private readonly claude = inject(ClaudeService);

  readonly mcpList = signal('');
  readonly mcpLoading = signal(false);
  readonly serverDefs = signal<Record<string, McpServerDef>>({});

  readonly editorOpen = signal(false);
  readonly editorData = signal<McpServerDef>({ type: 'stdio' });
  readonly editorName = signal('');
  readonly editorArgsText = signal('');
  readonly editorEnvText = signal('');
  readonly editorHeadersText = signal('');
  readonly saving = signal(false);

  loadMcp(onCliList?: (output: string) => void): void {
    this.mcpLoading.set(true);
    this.claude.runCliCommand(['mcp', 'list']).subscribe({
      next: output => {
        const normalized = output || '（無已安裝的 MCP）';
        this.mcpList.set(normalized);
        onCliList?.(output || '');
        this.mcpLoading.set(false);
      },
      error: () => {
        this.mcpList.set('[無法取得清單]');
        this.mcpLoading.set(false);
      },
    });
    this.loadServerDefs();
  }

  loadServerDefs(): void {
    this.claude.listMcpServers().subscribe({
      next: defs => this.serverDefs.set(defs),
      error: () => {},
    });
  }

  openEditor(): void {
    this.editorName.set('');
    this.editorArgsText.set('');
    this.editorEnvText.set('');
    this.editorHeadersText.set('');
    this.editorData.set({ type: 'stdio' });
    this.editorOpen.set(true);
  }

  parseKvLines(text: string): Record<string, string> {
    const out: Record<string, string> = {};
    for (const line of text.split('\n')) {
      const idx = line.indexOf('=');
      if (idx > 0) out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
    }
    return out;
  }

  buildPayload(
    data: McpServerDef,
    argsText: string,
    envText: string,
    headersText = '',
  ): McpServerDef {
    if (data.type === 'http') {
      return {
        type: 'http',
        url: (data.url || '').trim(),
        headers: this.parseKvLines(headersText),
      };
    }
    return {
      type: 'stdio',
      command: (data.command || '').trim(),
      args: argsText.split('\n').map(value => value.trim()).filter(Boolean),
      env: this.parseKvLines(envText),
    };
  }

  createServer(name: string, payload: McpServerDef): Observable<McpServerDef & { ok: boolean; name: string }> {
    this.saving.set(true);
    return this.claude.createMcpServer(name, payload).pipe(
      finalize(() => this.saving.set(false)),
    );
  }

  deleteServer(name: string): Observable<{ ok: boolean; synced: { claude: boolean; codex: boolean } }> {
    return this.claude.deleteMcpServer(name);
  }
}
