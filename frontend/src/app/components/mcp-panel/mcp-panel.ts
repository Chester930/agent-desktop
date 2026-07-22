import { Component, EventEmitter, Input, OnDestroy, OnInit, Output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgClass } from '@angular/common';
import { ClaudeService, McpServer, McpServerDef, ImportableMcp } from '../../claude.service';

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
  // % of panel height for each resizable section above the last one
  // (本地 API, which is the flexible remainder) — keyed by section key.
  @Input() mcpPaneHeights: Record<string, number> = {};
  // Precomputed by App — same reasoning as skill-panel's Sets: avoids
  // re-running a full cross-tab lookup per card on every change-detection
  // pass (see app.ts requiredMcpNames/linkedMcpNames/sessionMcpNames).
  @Input() requiredMcpNames: Set<string> = new Set();
  @Input() linkedMcpNames: Set<string> = new Set();
  @Input() sessionMcpNames: Set<string> = new Set();
  // Servers manually force-classified into 本地 API (App.managedMcpNames) —
  // isMcpLocal() checks this before falling back to mcpType/URL detection.
  @Input() managedMcpNames: Set<string> = new Set();
  @Input() selectedAgentLabel = '';

  @Output() addServerDef = new EventEmitter<void>();
  @Output() refreshList = new EventEmitter<void>();
  @Output() deleteServerDef = new EventEmitter<string>();
  @Output() toggleExpand = new EventEmitter<string>();
  @Output() toggleInTab = new EventEmitter<string>();
  @Output() toggleManaged = new EventEmitter<string>();
  @Output() dividerMousedown = new EventEmitter<{ event: MouseEvent; key: string }>();
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

  // Codex 這邊的 MCP 即時連線狀態——`codex mcp list` 本身只回報設定是否
  // enabled，不像 `claude mcp list` 會主動做健康檢查，所以後端另外對每個
  // Codex 註冊的 stdio server 做一次真實的 MCP initialize 握手（見
  // /api/mcp/codex-status），這裡把結果存起來供樣板顯示燈號。因為要真的
  // 短暫啟動每個伺服器測試，比 Claude 那邊查表慢，改成手動刷新，不在
  // ngOnInit 自動打。
  codexMcpStatus = signal<Record<string, { enabled: boolean; connected: boolean; checked: boolean; transportType: string }>>({});
  codexMcpLoading = signal(false);

  // 各區塊折疊狀態（持久化到 localStorage）
  collapsedSections = signal<Record<string, boolean>>(
    JSON.parse(localStorage.getItem('mcp_collapsed_sections') || '{}')
  );

  toggleSection(key: string) {
    this.collapsedSections.update(s => {
      const next = { ...s, [key]: !s[key] };
      localStorage.setItem('mcp_collapsed_sections', JSON.stringify(next));
      return next;
    });
  }

  isSectionCollapsed(key: string): boolean {
    return !!this.collapsedSections()[key];
  }

  /** Height % for a resizable pane, or null when collapsed (falls back to
   * the pane's CSS `flex: 0 0 auto`, i.e. header-only height). */
  paneHeightPct(key: string): number | null {
    return this.isSectionCollapsed(key) ? null : (this.mcpPaneHeights[key] ?? 20);
  }

  refreshCodexMcp() {
    this.codexMcpLoading.set(true);
    this.claude.getCodexMcpStatus().subscribe({
      next: (status) => { this.codexMcpStatus.set(status); this.codexMcpLoading.set(false); },
      error: (e) => {
        this.codexMcpLoading.set(false);
        this.toast.emit({ text: `Codex MCP 狀態查詢失敗: ${e.message ?? e}`, type: 'error' });
      },
    });
  }

  // 可認領的 MCP——Claude／Codex 原生已經有，但這個 app 自己的單一來源
  // （App 管理清單）還沒採納的項目（例如直接手動下 claude/codex mcp add
  // 加的，沒經過「＋新增」）。跟 codexMcpStatus 不同，這裡只是讀設定檔跟
  // `codex mcp list --json`，不用真的握手測試，夠快可以在 ngOnInit 自動
  // 載入。
  importableMcp = signal<ImportableMcp[]>([]);
  importableMcpLoading = signal(false);
  importingMcpName = signal<string | null>(null);

  refreshImportableMcp() {
    this.importableMcpLoading.set(true);
    this.claude.getImportableMcp().subscribe({
      next: (res) => { this.importableMcp.set(res.importable); this.importableMcpLoading.set(false); },
      error: (e) => {
        this.importableMcpLoading.set(false);
        this.toast.emit({ text: `可認領 MCP 查詢失敗: ${e.message ?? e}`, type: 'error' });
      },
    });
  }

  adoptMcp(name: string) {
    this.importingMcpName.set(name);
    this.claude.importMcp(name).subscribe({
      next: () => {
        this.importingMcpName.set(null);
        this.importableMcp.update(list => list.filter(i => i.name !== name));
        this.toast.emit({ text: `已將 ${name} 認領進 App 管理，並同步到兩邊 CLI`, type: 'success' });
        this.refreshList.emit();
      },
      error: (e) => {
        this.importingMcpName.set(null);
        this.toast.emit({ text: `認領 ${name} 失敗: ${e.error?.error ?? e.message ?? e}`, type: 'error' });
      },
    });
  }

  /** 這個名稱是否也在 Claude 那邊註冊過（外部/本地清單或 App 管理定義）。
   * 用一般方法而非 computed()：@Input 是一般欄位不是 signal，computed()
   * 包住它們只會在建立當下算一次，不會隨父層更新的輸入重新求值。 */
  isInClaudeList(name: string): boolean {
    return this.externalMcpServers.some(m => m.name === name)
      || this.localMcpServers.some(m => m.name === name)
      || name in this.mcpServerDefs;
  }

  ngOnInit() {
    this.claude.getLocalMcpConfig().subscribe(cfg => this.localMcpConfigs.set(cfg));
    this.refreshImportableMcp();
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
