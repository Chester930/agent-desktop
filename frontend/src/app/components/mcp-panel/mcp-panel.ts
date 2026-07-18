import { Component, EventEmitter, Input, OnDestroy, OnInit, Output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgClass } from '@angular/common';
import { ClaudeService, McpServer, McpServerDef } from '../../claude.service';

@Component({
  selector: 'app-mcp-panel',
  standalone: true,
  imports: [FormsModule, NgClass],
  templateUrl: './mcp-panel.html',
})
export class McpPanelComponent implements OnInit, OnDestroy {
  @Input() mcpServerDefs: Record<string, McpServerDef> = {};
  @Input() externalMcpServers: McpServer[] = [];
  @Input() localMcpServers: McpServer[] = [];
  @Input() expandedMcpId = '';
  @Input() mcpLoading = false;
  @Input() mcpSplitPct = 45;
  // Precomputed by App — same reasoning as skill-panel's Sets: avoids
  // re-running a full cross-tab lookup per card on every change-detection
  // pass (see app.ts requiredMcpNames/linkedMcpNames/sessionMcpNames).
  @Input() requiredMcpNames: Set<string> = new Set();
  @Input() linkedMcpNames: Set<string> = new Set();
  @Input() sessionMcpNames: Set<string> = new Set();
  @Input() selectedAgentLabel = '';

  @Output() addServerDef = new EventEmitter<void>();
  @Output() refreshList = new EventEmitter<void>();
  @Output() deleteServerDef = new EventEmitter<string>();
  @Output() toggleExpand = new EventEmitter<string>();
  @Output() toggleInTab = new EventEmitter<string>();
  @Output() dividerMousedown = new EventEmitter<MouseEvent>();
  @Output() start = new EventEmitter<string>();
  @Output() stop = new EventEmitter<string>();
  @Output() restart = new EventEmitter<string>();
  @Output() toast = new EventEmitter<{ text: string; type: 'success' | 'error' | 'info' | 'warn' }>();

  constructor(private claude: ClaudeService) {}

  // Local MCP Docker/compose metadata: only read/written within this panel
  // (confirmed via a full-file grep before extraction), so it loads its own
  // copy instead of receiving it as an @Input.
  localMcpConfigs = signal<Record<string, any>>({});
  localDockerConfig = signal<{ name: string; containerName: string; composeFile: string; composeService: string; port: string; notes: string } | null>(null);
  editingDockerMcp = signal<string | null>(null);

  // MCP log viewer (#15) — cleans up its own 2.5s polling interval in
  // ngOnDestroy, which fires whenever Angular destroys this component
  // (leaving the MCP tab via the @if wrapping the @defer block, or app
  // teardown).
  mcpLogOpen = signal<string | null>(null);
  mcpLogLines = signal<string[]>([]);
  private _mcpLogInterval: any = null;

  // MCP Live Debugger state — self-contained, no cross-tab reads/writes.
  mcpRpcName = '';
  mcpRpcMethod = 'tools/list';
  mcpRpcParamsText = '{}';
  mcpRpcResult = '';
  isMcpRpcSending = false;
  mcpPendingAuth = signal<any>(null);

  ngOnInit() {
    this.claude.getLocalMcpConfig().subscribe(cfg => this.localMcpConfigs.set(cfg));
  }

  ngOnDestroy() {
    if (this._mcpLogInterval) {
      clearInterval(this._mcpLogInterval);
      this._mcpLogInterval = null;
    }
  }

  objectKeys(obj: Record<string, unknown>): string[] {
    return Object.keys(obj);
  }

  isMcpRunning(status: string): boolean {
    return status?.toLowerCase().includes('connected');
  }

  /** CSS class for the status lamp — encodes the 4-state traffic-light logic. */
  getMcpLampClass(m: McpServer): string {
    const running = this.isMcpRunning(m.status);
    const inUse = this.linkedMcpNames.has(m.name);
    if (!running && inUse) return 'lamp-red';    // ⚠ 需要關注
    if (!running) return 'lamp-off';    // ● 停止（灰）
    if (!inUse) return 'lamp-yellow'; // ● 運行中但未啟用
    return 'lamp-green';                          // ● 運行中且啟用
  }

  getMcpLampTitle(m: McpServer): string {
    const running = this.isMcpRunning(m.status);
    const inUse = this.linkedMcpNames.has(m.name);
    if (!running && inUse) return '⚠ 伺服器未啟動，但已被 Agent 使用';
    if (!running) return '● 已停止';
    if (!inUse) return '● 運行中（未綁定到目前 Agent）';
    return '● 運行中 · 已啟用';
  }

  openDockerConfig(m: McpServer) {
    const cfg = this.localMcpConfigs()[m.name] ?? {};
    this.localDockerConfig.set({
      name: m.name,
      containerName: cfg.containerName ?? m.containerName ?? '',
      composeFile: cfg.composeFile ?? m.composeFile ?? '',
      composeService: cfg.composeService ?? m.composeService ?? '',
      port: cfg.port ?? m.port ?? '',
      notes: cfg.notes ?? '',
    });
    this.editingDockerMcp.set(m.name);
  }

  saveDockerConfig() {
    const cfg = this.localDockerConfig();
    if (!cfg) return;
    this.claude.saveLocalMcpConfig(cfg.name, cfg).subscribe({
      next: () => {
        this.localMcpConfigs.update(all => ({ ...all, [cfg.name]: cfg }));
        this.toast.emit({ text: `Docker 設定已儲存：${cfg.name}`, type: 'success' });
        this.editingDockerMcp.set(null);
      },
      error: (e) => this.toast.emit({ text: `Docker 設定儲存失敗: ${e.message ?? e}`, type: 'error' }),
    });
  }

  toggleMcpLog(name: string) {
    if (this._mcpLogInterval) {
      clearInterval(this._mcpLogInterval);
      this._mcpLogInterval = null;
    }

    if (this.mcpLogOpen() === name) {
      this.mcpLogOpen.set(null);
      return;
    }
    this.mcpLogOpen.set(name);
    this.refreshMcpLog(name);

    // 每 2.5 秒自動重整日誌，方便使用者調試
    this._mcpLogInterval = setInterval(() => {
      if (this.mcpLogOpen() === name) {
        this.refreshMcpLog(name);
      } else {
        clearInterval(this._mcpLogInterval);
        this._mcpLogInterval = null;
      }
    }, 2500);
  }

  refreshMcpLog(name: string) {
    this.claude.getMcpLogs(name).subscribe(r => {
      this.mcpLogLines.set(r.lines);
      setTimeout(() => {
        const el = document.querySelector('.mcp-log-body');
        if (el) {
          el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
        }
      }, 50);
    });
  }

  sendMcpRpcDebug() {
    if (!this.mcpRpcName || !this.mcpRpcMethod) {
      this.mcpRpcResult = '錯誤: 必須填寫 MCP 名稱與 Method';
      return;
    }
    if (this.mcpPendingAuth()) {
      // 上一筆敏感操作還在等待使用者核准/拒絕，直接送出新請求會讓那筆掛起狀態
      // 從畫面上悄悄消失（後端 pending_id 仍存在，只是 UI 不再追蹤），
      // 因此在此擋下，要求使用者先處理完再繼續。
      this.mcpRpcResult = '⚠️ 尚有一筆敏感操作正在等待授權，請先核准或拒絕後再送出新請求。';
      return;
    }
    let paramsObj = {};
    try {
      paramsObj = JSON.parse(this.mcpRpcParamsText || '{}');
    } catch (e: any) {
      this.mcpRpcResult = `錯誤: Params 不是有效的 JSON - ${e.message}`;
      return;
    }
    this.isMcpRpcSending = true;
    this.mcpRpcResult = '發送中...';

    this.claude.sendMcpRpc(this.mcpRpcName, this.mcpRpcMethod, paramsObj).subscribe({
      next: (res) => {
        this.mcpRpcResult = JSON.stringify(res, null, 2);
        this.isMcpRpcSending = false;
      },
      error: (err) => {
        // 當遇到後端敏感關鍵字安全閘口攔截 (403 pending_authorization)
        if (err.status === 403 && (err.error?.status === 'pending_authorization' || err.error?.error?.includes('敏感操作'))) {
          const errMsg = err.error?.error || '敏感操作已被掛起';
          const pId = err.error?.pending_id;
          this.mcpPendingAuth.set({
            pendingId: pId,
            name: this.mcpRpcName,
            method: this.mcpRpcMethod,
            params: paramsObj
          });
          this.mcpRpcResult = `⚠️ ${errMsg}`;
          this.isMcpRpcSending = false;
          return;
        }

        this.mcpRpcResult = `請求失敗: ${err.error?.error || err.message || JSON.stringify(err)}`;
        this.isMcpRpcSending = false;
      }
    });
  }

  authorizeMcpRpc(authorized: boolean) {
    const auth = this.mcpPendingAuth();
    if (!auth) return;

    if (!authorized) {
      this.mcpRpcResult = '授權拒絕。敏感操作已取消。';
      this.mcpPendingAuth.set(null);
      return;
    }

    this.isMcpRpcSending = true;
    this.mcpRpcResult = '授權通過，發送中...';

    this.claude.sendMcpRpc(auth.name, auth.method, auth.params, true, auth.pendingId).subscribe({
      next: (res) => {
        this.mcpRpcResult = JSON.stringify(res, null, 2);
        this.isMcpRpcSending = false;
        this.mcpPendingAuth.set(null);
      },
      error: (err) => {
        this.mcpRpcResult = `授權執行失敗: ${err.error?.error || err.message || JSON.stringify(err)}`;
        this.isMcpRpcSending = false;
        this.mcpPendingAuth.set(null);
      }
    });
  }
}
