import {
  Component, OnInit, OnDestroy, signal, computed,
  ViewChild, ElementRef, AfterViewChecked, HostListener
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { MarkdownPipe } from './markdown.pipe';
import { SettingsService, AppSettings, QuickPrompt } from './settings.service';
import {
  ClaudeService, Agent, Skill, Session, ChatMessage, Schedule, ChatTab, FileItem, SoulProfile, Profile
} from './claude.service';

export interface McpWorkflow {
  type: 'code' | 'node';
  content: string;
  dockerized?: boolean;
  dockerImage?: string;
}

export interface McpTool {
  name: string;
  description: string;
  workflow?: McpWorkflow;
}

export interface McpServer {
  id: string;
  name: string;
  url: string;
  status: string;
  authorized: boolean;
  description: string;
  dockerized?: boolean;
  dockerImage?: string;
  tools?: McpTool[];
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule, DatePipe, DecimalPipe, MarkdownPipe],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App implements OnInit, OnDestroy, AfterViewChecked {
  @ViewChild('chatEnd')    chatEnd!: ElementRef;
  @ViewChild('inputRef')   inputRef!: ElementRef;
  @ViewChild('scrollArea') scrollArea!: ElementRef;

  // Data
  agents    = signal<Agent[]>([]);
  skills    = signal<Skill[]>([]);
  sessions  = signal<Session[]>([]);
  memory    = signal<Record<string, string>>({});
  schedules = signal<Schedule[]>([]);

  rightPanelFilter = signal('');

  sortedAgents = computed(() => {
    const q = this.rightPanelFilter().toLowerCase();
    let list = [...this.agents()];
    if (q) list = list.filter(a => a.name.toLowerCase().includes(q) || a.description?.toLowerCase().includes(q));
    const selected = this.selectedAgent();
    if (!selected) return list.sort((a, b) => a.name.localeCompare(b.name));
    const cleanSelected = selected.replace(/^@/, '');
    return list.sort((a, b) => {
      if (a.id === cleanSelected) return -1;
      if (b.id === cleanSelected) return 1;
      return a.name.localeCompare(b.name);
    });
  });

  sortedSkills = computed(() => {
    const q = this.rightPanelFilter().toLowerCase();
    let list = [...this.skills()];
    if (q) list = list.filter(s => s.name.toLowerCase().includes(q) || s.description?.toLowerCase().includes(q));
    const agentId = this.selectedAgent();
    if (!agentId) return list;
    const linkedIds = this.getLinkedSkills(agentId);
    return list.sort((a, b) => {
      const aLinked = linkedIds.includes(a.id);
      const bLinked = linkedIds.includes(b.id);
      if (aLinked && !bLinked) return -1;
      if (!aLinked && bLinked) return 1;
      return a.name.localeCompare(b.name);
    });
  });

  sortedMcpServers = computed(() => {
    const q = this.rightPanelFilter().toLowerCase();
    let list = [...this.mcpServers()];
    if (q) list = list.filter(m => m.name.toLowerCase().includes(q) || m.description?.toLowerCase().includes(q));
    const agentId = this.selectedAgent();
    if (!agentId) {
      return list;
    }
    const linkedSkills = this.getLinkedSkills(agentId);
    const usedMcps: string[] = [];
    for (const skillId of linkedSkills) {
      usedMcps.push(...this.getUsedMcps(skillId));
    }
    return list.sort((a, b) => {
      const aUsed = usedMcps.includes(a.name);
      const bUsed = usedMcps.includes(b.name);
      if (aUsed && !bUsed) return -1;
      if (!aUsed && bUsed) return 1;
      return a.name.localeCompare(b.name);
    });
  });

  // MCP and Auth data
  mcpServers = signal<McpServer[]>([]);
  authorizedSkills = signal<string[]>([]);
  authorizedMcps = signal<string[]>([]);

  // Collapsible accordion expansion state
  expandedAgentId = signal<string>('');
  expandedSkillId = signal<string>('');
  expandedMcpId = signal<string>('');
  expandedTranslation = signal<string | null>(null);

  // 永久綁定：儲存於 localStorage（由使用者在 UI 操作）
  agentSkillsMap = signal<Record<string, string[]>>({});
  agentMcpsMap   = signal<Record<string, string[]>>({}); // agent 直連 MCP，不透過 skill

  // 自建 MCP server 清單（與外部服務分層顯示）
  managedMcpNames = signal<string[]>([]);
  externalMcpServers = computed(() => this.sortedMcpServers().filter(m => !this.managedMcpNames().includes(m.name)));
  selfMcpServers     = computed(() => this.sortedMcpServers().filter(m =>  this.managedMcpNames().includes(m.name)));

  toggleManagedMcp(name: string) {
    this.managedMcpNames.update(arr =>
      arr.includes(name) ? arr.filter(n => n !== name) : [...arr, name]
    );
    localStorage.setItem('claude_desktop_managed_mcps', JSON.stringify(this.managedMcpNames()));
  }
  isMcpManaged(name: string)   { return this.managedMcpNames().includes(name); }
  isMcpRunning(status: string) { return status?.toLowerCase().includes('connected'); }

  getMcpColor(name: string, status: string): string {
    const running = this.isMcpRunning(status);
    const inUse   = this.isMcpLinkedToActiveAgent(name);
    if (!running && inUse)  return '#ef4444'; // 未啟動 + 使用中 → 紅
    if (!running)           return '';        // 未啟動 + 未使用 → 無色
    if (!inUse)             return '#f59e0b'; // 啟動 + 未使用  → 黃
    return '#10b981';                         // 啟動 + 使用中  → 綠
  }

  startMcp(name: string)   { this.claude.startMcp(name).subscribe();   }
  stopMcp(name: string)    { this.claude.stopMcp(name).subscribe();     }
  restartMcp(name: string) { this.claude.restartMcp(name).subscribe();  }

  private saveAgentSkillsMap() {
    localStorage.setItem('claude_desktop_agent_skills', JSON.stringify(this.agentSkillsMap()));
  }
  private saveAgentMcpsMap() {
    localStorage.setItem('claude_desktop_agent_mcps_direct', JSON.stringify(this.agentMcpsMap()));
  }

  readonly SKILL_MCPS_MAP: Record<string, string[]> = {
    'google-agents-cli-adk-code': ['claude.ai Play Sheet Music', 'claude.ai Digits'],
    'google-agents-cli-deploy': ['claude.ai Google Drive', 'claude.ai Box'],
    'google-agents-cli-eval': ['claude.ai Linear', 'claude.ai Ticket Tailor'],
    'google-agents-cli-observability': ['claude.ai Gamma', 'claude.ai Gmail'],
    'google-agents-cli-publish': ['claude.ai Google Calendar', 'claude.ai Google Drive'],
    'google-agents-cli-scaffold': ['claude.ai Digits', 'claude.ai Box'],
    'google-agents-cli-workflow': ['claude.ai Linear', 'claude.ai Gamma']
  };

  readonly MCP_DESCRIPTIONS: Record<string, string> = {
    'claude.ai Digits': 'Digits MCP server: Allows Claude to interact with your Digits financial reports, transactions, and accounting dashboard.',
    'claude.ai Google Drive': 'Google Drive MCP server: Allows Claude to search, read, and manage files in your Google Drive storage.',
    'claude.ai Linear': 'Linear MCP server: Allows Claude to search, create, update, and manage Linear issues, projects, and cycles.',
    'claude.ai Play Sheet Music': 'Play Sheet Music MCP server: Allows Claude to search, generate, and play music notation and sheets.',
    'claude.ai Box': 'Box MCP server: Allows Claude to read, write, and manage secure enterprise content and files in Box.',
    'claude.ai Gamma': 'Gamma MCP server: Allows Claude to create, format, and present presentations, web pages, and documents.',
    'claude.ai Ticket Tailor': 'Ticket Tailor MCP server: Allows Claude to manage event ticketing, check order statuses, and issue tickets.',
    'claude.ai Google Calendar': 'Google Calendar MCP server: Allows Claude to schedule events, list meetings, and manage your Google Calendar invites.',
    'claude.ai Gmail': 'Gmail MCP server: Allows Claude to read, compose, reply, and search emails in your Gmail inbox.'
  };

  readonly MCP_TOOLS_MAP: Record<string, McpTool[]> = {
    'claude.ai Google Drive': [
      { name: 'list_files', description: 'List files and folders in Google Drive, supports query filters.' },
      { name: 'get_file_content', description: 'Retrieve text or binary content of a file in Google Drive.' },
      { name: 'create_file', description: 'Create a new file or upload a document to Google Drive.' }
    ],
    'claude.ai Gmail': [
      { name: 'list_emails', description: 'List inbox emails, supports query string search.' },
      { name: 'get_email', description: 'Retrieve detailed contents of a specific email by ID.' },
      { name: 'send_email', description: 'Compose and send a new email message.' }
    ],
    'claude.ai Google Calendar': [
      { name: 'list_events', description: 'List upcoming events, support time ranges.' },
      { name: 'create_event', description: 'Schedule a new calendar event.' }
    ],
    'claude.ai Linear': [
      { name: 'search_issues', description: 'Search and filter Linear issue tickets.' },
      { name: 'create_issue', description: 'Create a new issue ticket in Linear.' }
    ],
    'claude.ai Digits': [
      { name: 'get_balance_sheet', description: 'Retrieve real-time balance sheet reports.' },
      { name: 'query_transactions', description: 'Query and filter transactions.' }
    ],
    'claude.ai Play Sheet Music': [
      { name: 'play_song', description: 'Play sheet music by rendering ABC notation to MIDI.' },
      { name: 'generate_sheet', description: 'Generate sheet music from prompt.' }
    ],
    'claude.ai Box': [
      { name: 'search_box_files', description: 'Search files and folders in Box storage.' }
    ],
    'claude.ai Gamma': [
      { name: 'generate_presentation', description: 'Generate AI slide decks or documents.' }
    ],
    'claude.ai Ticket Tailor': [
      { name: 'get_orders', description: 'Retrieve ticket orders and guest lists.' }
    ],
    'Docker MySQL Sync (Custom)': [
      {
        name: 'sync_db_schema',
        description: 'Synchronize MySQL databases inside the Docker network container.',
        workflow: {
          type: 'code',
          dockerized: true,
          dockerImage: 'mysql-sync-agent:latest',
          content: `// Docker sync container script entrypoint\nimport mysql from 'mysql2/promise';\n\nasync function runSync() {\n  console.log('Connecting to Docker mysql container...');\n  const conn = await mysql.createConnection(process.env.MYSQL_URI);\n  const [rows] = await conn.query('SHOW TABLES');\n  // Sync logic here\n  console.log('Synchronized ' + rows.length + ' tables.');\n}`
        }
      }
    ],
    'N8N Automation (Custom)': [
      {
        name: 'trigger_n8n_flow',
        description: 'Trigger a visual N8N flow webhook to execute integration task.',
        workflow: {
          type: 'node',
          dockerized: true,
          dockerImage: 'n8nio/n8n:latest',
          content: 'Webhook Node ➔ JS Processing Node ➔ Slack Alert Node ➔ PostgreSQL Sync'
        }
      }
    ]
  };

  // Chat state
  messages    = signal<ChatMessage[]>([]);
  inputText   = '';
  isStreaming  = signal(false);
  selectedAgent = signal('');
  activeTab   = signal<'agents' | 'skills' | 'memory' | 'schedules' | 'soul' | 'mcp'>('agents');
  selectedMemoryKey = signal('');
  sessionSearch = '';

  // Schedule form
  newSchedulePrompt = '';
  newScheduleCron   = '';

  // Token usage + cost
  tokenUsage = signal<{ input: number; output: number; cost: number } | null>(null);
  readonly Math = Math;

  // Attachments
  attachedFiles = signal<{ name: string; path: string; preview?: string }[]>([]);
  isUploading   = signal(false);

  // Soul / Persona
  soulContent = '';
  soulSaved   = signal(true);
  private soulTimer: any = null;

  // Multi-soul state
  souls = signal<SoulProfile[]>([]);
  selectedSoulId = signal<string>('');
  soulDraft = '';
  soulDraftSaved = signal(true);
  newSoulName = '';

  // Resizing signals & state
  sidebarWidth   = signal(200);
  rightWidth     = signal(260);
  inputHeight    = signal(140);
  soulSplitRatio = signal(0.5);   // 0 = all upper, 1 = all lower

  private _resizing = false;
  private _startX   = 0;
  private _startW   = 0;

  private _rightResizing = false;
  private _startXRight   = 0;
  private _startWRight   = 0;

  private _inputResizing = false;
  private _startYInput   = 0;
  private _startHInput   = 0;

  private _soulResizing  = false;
  private _soulStartY    = 0;
  private _soulStartRatio = 0;
  private _soulPanelHeight = 0;

  onResizeStart(e: MouseEvent) {
    this._resizing = true; this._startX = e.clientX; this._startW = this.sidebarWidth();
    e.preventDefault();
  }

  onRightResizeStart(e: MouseEvent) {
    this._rightResizing = true; this._startXRight = e.clientX; this._startWRight = this.rightWidth();
    e.preventDefault();
  }

  onInputResizeStart(e: MouseEvent) {
    this._inputResizing = true; this._startYInput = e.clientY; this._startHInput = this.inputHeight();
    e.preventDefault();
  }

  onSoulDividerMousedown(e: MouseEvent, panelEl: HTMLElement) {
    this._soulResizing   = true;
    this._soulStartY     = e.clientY;
    this._soulStartRatio = this.soulSplitRatio();
    this._soulPanelHeight = panelEl.clientHeight;
    e.preventDefault();
  }

  @HostListener('document:mousemove', ['$event'])
  onMouseMove(e: MouseEvent) {
    if (this._resizing) {
      this.sidebarWidth.set(Math.max(140, Math.min(420, this._startW + (e.clientX - this._startX))));
    } else if (this._rightResizing) {
      this.rightWidth.set(Math.max(200, Math.min(500, this._startWRight - (e.clientX - this._startXRight))));
    } else if (this._inputResizing) {
      this.inputHeight.set(Math.max(100, Math.min(400, this._startHInput - (e.clientY - this._startYInput))));
    } else if (this._soulResizing && this._soulPanelHeight > 0) {
      const delta = e.clientY - this._soulStartY;
      const newRatio = this._soulStartRatio + delta / this._soulPanelHeight;
      this.soulSplitRatio.set(Math.max(0.15, Math.min(0.85, newRatio)));
    }
  }

  @HostListener('document:mouseup')
  onMouseUp() {
    this._resizing = false;
    this._rightResizing = false;
    this._inputResizing = false;
    this._soulResizing  = false;
  }

  // Scroll to bottom
  showScrollBtn = signal(false);

  onMessagesScroll() {
    const el = this.scrollArea?.nativeElement;
    if (!el) return;
    this.showScrollBtn.set(el.scrollHeight - el.scrollTop - el.clientHeight > 150);
  }

  scrollToBottom() {
    this.chatEnd?.nativeElement?.scrollIntoView({ behavior: 'smooth' });
    this.showScrollBtn.set(false);
  }

  // Backend health
  backendDown = signal(false);
  private _healthTimer: any;

  // Session pagination
  sessionOffset = 0;
  hasMoreSessions = signal(false);

  // Debug mode
  debugMode = signal(false);

  // ── T11 多 Tab 對話 ────────────────────────────────────
  chatTabs   = signal<ChatTab[]>([]);
  activeChatId = signal('');

  get activeChat(): ChatTab | undefined {
    return this.chatTabs().find(t => t.id === this.activeChatId());
  }

  private makeTab(label = '新對話'): ChatTab {
    return { id: `tab-${Date.now()}`, clientId: `client-${Date.now()}`, label, messages: [], tokenUsage: null, selectedAgent: '', isStreaming: false, sessionSkills: [], sessionMcps: [] };
  }

  private saveCurrentTab() {
    const id = this.activeChatId();
    if (!id) return;
    this.chatTabs.update(tabs => tabs.map(t => t.id === id
      ? { ...t, messages: this.messages(), tokenUsage: this.tokenUsage(), selectedAgent: this.selectedAgent(), isStreaming: this.isStreaming() }
      : t));
  }

  switchChatTab(tabId: string) {
    if (tabId === this.activeChatId()) return;
    this.saveCurrentTab();
    const tab = this.chatTabs().find(t => t.id === tabId);
    if (!tab) return;
    this.messages.set([...tab.messages]);
    this.tokenUsage.set(tab.tokenUsage);
    this.selectedAgent.set(tab.selectedAgent);
    this.isStreaming.set(tab.isStreaming);
    this.claude.clientId = tab.clientId;
    this.activeChatId.set(tabId);
    this.checkQuotaInMessages(tab.messages);
  }

  checkQuotaInMessages(msgs: ChatMessage[]) {
    const hasLimit = msgs.some(m => m.text && (
      m.text.toLowerCase().includes('session limit') || 
      m.text.toLowerCase().includes('rate limit') || 
      m.text.toLowerCase().includes('limit · resets') ||
      m.text.toLowerCase().includes('quota')
    ));
    this.outOfQuota.set(hasLimit);
  }

  addChatTab() {
    if (this.chatTabs().length >= 4) return; // 畫布最多 4 個面板
    this.saveCurrentTab();
    const tab = this.makeTab();
    this.chatTabs.update(t => [...t, tab]);
    this.messages.set([]);
    this.tokenUsage.set(null);
    this.selectedAgent.set(this.settings.get().defaultAgent || '');
    this.isStreaming.set(false);
    this.claude.clientId = tab.clientId;
    this.activeChatId.set(tab.id);
  }

  // Tab 關閉確認 modal state
  tabCloseConfirmId    = signal<string | null>(null);
  tabCloseConfirmAgent = signal<string>('');

  closeChatTab(tabId: string, e: Event) {
    e.stopPropagation();
    if (this.chatTabs().length <= 1) return;
    const tab = this.chatTabs().find(t => t.id === tabId);
    if (tab?.selectedAgent && (tab.sessionSkills.length > 0 || tab.sessionMcps.length > 0)) {
      this.tabCloseConfirmId.set(tabId);
      this.tabCloseConfirmAgent.set(tab.selectedAgent);
      return;
    }
    this.doCloseTab(tabId);
  }

  confirmCloseTab(save: boolean) {
    const tabId = this.tabCloseConfirmId();
    if (!tabId) return;
    if (save) {
      const tab = this.chatTabs().find(t => t.id === tabId);
      if (tab?.selectedAgent) this.commitSessionToAgent(tab.selectedAgent, tab.sessionSkills, tab.sessionMcps);
    }
    this.tabCloseConfirmId.set(null);
    this.tabCloseConfirmAgent.set('');
    this.doCloseTab(tabId);
  }

  private commitSessionToAgent(agentId: string, skills: string[], mcps: string[]) {
    const id = agentId.replace(/^@/, '');
    if (skills.length) {
      this.agentSkillsMap.update(m => ({ ...m, [id]: [...new Set([...(m[id] ?? []), ...skills])] }));
      this.saveAgentSkillsMap();
    }
    if (mcps.length) {
      this.agentMcpsMap.update(m => ({ ...m, [id]: [...new Set([...(m[id] ?? []), ...mcps])] }));
      this.saveAgentMcpsMap();
    }
  }

  private doCloseTab(tabId: string) {
    const idx = this.chatTabs().findIndex(t => t.id === tabId);
    const isActive = tabId === this.activeChatId();
    this.chatTabs.update(t => t.filter(x => x.id !== tabId));
    if (isActive) {
      const next = this.chatTabs()[Math.max(0, idx - 1)];
      if (next) this.switchChatTab(next.id);
    }
  }

  // ── 畫布：網格比例與拖放 ──────────────────────────────────
  canvasColRatio = signal(0.5);
  canvasRowRatio = signal(0.5);
  canvasDropping = signal(false);
  canvasCol3Divider1 = signal(0.333);
  canvasCol3Divider2 = signal(0.667);

  canvasGridStyle = computed(() => {
    const n = this.chatTabs().length;
    const c = this.canvasColRatio();
    const r = this.canvasRowRatio();
    if (n <= 1) return {};
    if (n === 2) {
      const left = `${(c * 100).toFixed(1)}%`;
      const right = `${((1 - c) * 100).toFixed(1)}%`;
      return { 'grid-template-columns': `${left} ${right}` };
    }
    if (n === 3) {
      const w1 = `${(this.canvasCol3Divider1() * 100).toFixed(1)}%`;
      const w2 = `${((this.canvasCol3Divider2() - this.canvasCol3Divider1()) * 100).toFixed(1)}%`;
      const w3 = `${((1 - this.canvasCol3Divider2()) * 100).toFixed(1)}%`;
      return { 'grid-template-columns': `${w1} ${w2} ${w3}` };
    }
    // n === 4: 2×2
    const left = `${(c * 100).toFixed(1)}%`;
    const right = `${((1 - c) * 100).toFixed(1)}%`;
    const top = `${(r * 100).toFixed(1)}%`;
    const bot = `${((1 - r) * 100).toFixed(1)}%`;
    return { 'grid-template-columns': `${left} ${right}`, 'grid-template-rows': `${top} ${bot}` };
  });

  // 取得面板的訊息：active 面板用 live signal，其他用已存快照
  getPanelMessages(tabId: string): ChatMessage[] {
    if (tabId === this.activeChatId()) return this.messages();
    return this.chatTabs().find(t => t.id === tabId)?.messages ?? [];
  }

  // 從側欄拖曳 session 到畫布
  onSessionDragStart(s: Session, e: DragEvent) {
    e.dataTransfer!.setData('sessionId', s.id);
    e.dataTransfer!.effectAllowed = 'copy';
  }

  onCanvasDragOver(e: DragEvent) {
    if (this.chatTabs().length >= 4) return;
    e.preventDefault();
    this.canvasDropping.set(true);
  }

  onCanvasDragLeave(e: DragEvent) {
    // 只有離開畫布本體時才清除（避免子元素 leave 觸發）
    if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node))
      this.canvasDropping.set(false);
  }

  onCanvasDrop(e: DragEvent) {
    e.preventDefault();
    this.canvasDropping.set(false);
    const sessionId = e.dataTransfer?.getData('sessionId');
    if (!sessionId || this.chatTabs().length >= 4) return;
    const session = this.sessions().find(s => s.id === sessionId);
    if (!session) return;
    this.addChatTab();
    this.loadSession(session);
  }

  // 調整欄位比例
  onResizeCol(e: MouseEvent, canvasEl: HTMLElement) {
    e.preventDefault();
    const startX = e.clientX;
    const startRatio = this.canvasColRatio();
    const width = canvasEl.clientWidth;
    const onMove = (ev: MouseEvent) => {
      const d = (ev.clientX - startX) / width;
      this.canvasColRatio.set(Math.max(0.2, Math.min(0.8, startRatio + d)));
    };
    const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  onResizeCol3(e: MouseEvent, handleIndex: 1 | 2, canvasEl: HTMLElement) {
    e.preventDefault();
    const startX = e.clientX;
    const width = canvasEl.clientWidth;
    if (handleIndex === 1) {
      const startVal = this.canvasCol3Divider1();
      const limitMax = this.canvasCol3Divider2() - 0.1;
      const onMove = (ev: MouseEvent) => {
        const d = (ev.clientX - startX) / width;
        this.canvasCol3Divider1.set(Math.max(0.1, Math.min(limitMax, startVal + d)));
      };
      const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    } else {
      const startVal = this.canvasCol3Divider2();
      const limitMin = this.canvasCol3Divider1() + 0.1;
      const onMove = (ev: MouseEvent) => {
        const d = (ev.clientX - startX) / width;
        this.canvasCol3Divider2.set(Math.max(limitMin, Math.min(0.9, startVal + d)));
      };
      const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    }
  }

  onResizeRow(e: MouseEvent, canvasEl: HTMLElement) {
    e.preventDefault();
    const startY = e.clientY;
    const startRatio = this.canvasRowRatio();
    const height = canvasEl.clientHeight;
    const onMove = (ev: MouseEvent) => {
      const d = (ev.clientY - startY) / height;
      this.canvasRowRatio.set(Math.max(0.2, Math.min(0.8, startRatio + d)));
    };
    const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  // ── T13 File tree ────────────────────────────────────────
  fileTreePath = signal('');
  fileTree     = signal<{ path: string; parent: string; items: FileItem[] } | null>(null);
  fileTreeOpen = signal(false);

  loadFileTree(path?: string) {
    const p = path ?? (this.fileTreePath() || this.settings.get().workDir || undefined);
    this.claude.getFiles(p).subscribe(r => {
      this.fileTree.set(r);
      this.fileTreePath.set(r.path);
    });
  }

  toggleFileTree() {
    this.fileTreeOpen.update(v => !v);
    if (this.fileTreeOpen() && !this.fileTree()) this.loadFileTree();
  }

  fileTreeClick(item: FileItem) {
    if (item.isDir) { this.loadFileTree(item.path); return; }
    this.attachedFiles.update(a => a.some(f => f.path === item.path) ? a : [...a, { name: item.name, path: item.path }]);
  }

  // ── T14 ⌘K 全局搜尋 ─────────────────────────────────────
  cmdOpen  = signal(false);
  cmdQ     = signal('');
  cmdIdx   = signal(0);
  cmdInputText = '';

  cmdItems = computed(() => {
    const q = this.cmdQ().toLowerCase().trim();
    type Item = { type: string; id: string; label: string; desc: string };
    const results: Item[] = [];
    for (const c of this.BUILTIN_CMDS)
      if (!q || c.name.includes(q) || c.description.includes(q))
        results.push({ type: 'cmd', id: c.id, label: '/' + c.name, desc: c.description });
    for (const s of this.sessions())
      if (!q || s.title.toLowerCase().includes(q))
        results.push({ type: 'session', id: s.id, label: s.title, desc: '對話歷史' });
    for (const a of this.agents())
      if (!q || a.name.toLowerCase().includes(q))
        results.push({ type: 'agent', id: a.id, label: '@' + a.name, desc: a.description });
    for (const s of this.skills())
      if (!q || s.name.toLowerCase().includes(q))
        results.push({ type: 'skill', id: s.id, label: '/' + s.name, desc: s.description });
    return results.slice(0, 12);
  });

  openCmd() { this.cmdOpen.set(true); this.cmdQ.set(''); this.cmdInputText = ''; this.cmdIdx.set(0); }
  closeCmd() { this.cmdOpen.set(false); }

  onCmdKey(e: KeyboardEvent) {
    const items = this.cmdItems();
    if (e.key === 'ArrowDown') { e.preventDefault(); this.cmdIdx.update(i => Math.min(i + 1, items.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); this.cmdIdx.update(i => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); this.selectCmdItem(items[this.cmdIdx()]); }
    else if (e.key === 'Escape') { this.closeCmd(); }
  }

  onCmdInput() { this.cmdQ.set(this.cmdInputText); this.cmdIdx.set(0); }

  selectCmdItem(item: { type: string; id: string; label: string } | undefined) {
    if (!item) return;
    this.closeCmd();
    if (item.type === 'cmd')     { this.executeBuiltinCmd(item.id); }
    else if (item.type === 'session') { const s = this.sessions().find(x => x.id === item.id); if (s) this.loadSession(s); }
    else if (item.type === 'agent')   { this.selectAgent(item.id); }
    else if (item.type === 'skill')   { this.inputText = item.label + ' '; this.inputRef?.nativeElement?.focus(); }
  }

  // T01 — model / effort / permissionMode（對應 Claude CLI 參數）
  readonly MODEL_OPTIONS   = ['sonnet','opus','haiku','fable'] as const;
  readonly EFFORT_OPTIONS  = ['low','medium','high','xhigh','max'] as const;
  readonly PERM_OPTIONS    = ['acceptEdits','default','plan','bypassPermissions','auto'] as const;
  readonly PERM_LABELS: Record<string,string> = {
    acceptEdits: 'Accept edits', default: 'Default',
    plan: 'Plan', bypassPermissions: 'Bypass', auto: 'Auto',
  };
  readonly MODEL_LABELS: Record<string,string> = {
    sonnet: 'Sonnet 4.6', opus: 'Opus 4.8', haiku: 'Haiku 4.5', fable: 'Fable 5',
  };
  model          = signal('sonnet');
  effort         = signal<'low'|'medium'|'high'|'xhigh'|'max'>('medium');
  permissionMode = signal<'default'|'acceptEdits'|'bypassPermissions'|'plan'|'auto'>('acceptEdits');
  showSettingsHelp = signal(false);
  bannerDismissed = signal(false);
  outOfQuota = signal(false);
  bannerMessage = computed(() => {
    if (this.model() === 'fable' && !this.bannerDismissed()) {
      return 'Claude Fable 5 is currently unavailable.';
    }
    return null;
  });

  cycleModel() {
    const idx = (this.MODEL_OPTIONS.indexOf(this.model() as any) + 1) % this.MODEL_OPTIONS.length;
    const v = this.MODEL_OPTIONS[idx]; this.model.set(v); this.settings.save({ model: v });
    this.bannerDismissed.set(false);
    this.outOfQuota.set(false); // 切換模型時重設用量限制狀態
  }

  toggleMic() {
    this.showToast('語音輸入即將推出，敬請期待！', 'info');
  }
  cycleEffort() {
    const idx = (this.EFFORT_OPTIONS.indexOf(this.effort() as any) + 1) % this.EFFORT_OPTIONS.length;
    const v = this.EFFORT_OPTIONS[idx]; this.effort.set(v); this.settings.save({ effort: v });
  }
  cyclePermission() {
    const idx = (this.PERM_OPTIONS.indexOf(this.permissionMode() as any) + 1) % this.PERM_OPTIONS.length;
    const v = this.PERM_OPTIONS[idx]; this.permissionMode.set(v); this.settings.save({ permissionMode: v });
  }

  // T04 — drag & drop
  isDragOver = signal(false);

  onDragOver(e: DragEvent) { e.preventDefault(); this.isDragOver.set(true); }
  onDragLeave()             { this.isDragOver.set(false); }
  async onDrop(e: DragEvent) {
    e.preventDefault(); this.isDragOver.set(false);
    const files = Array.from(e.dataTransfer?.files ?? []);
    if (!files.length) return;
    this.isUploading.set(true);
    for (const file of files) {
      try {
        const preview = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined;
        const result  = await this.claude.uploadFile(file);
        this.attachedFiles.update(a => [...a, { ...result, preview }]);
      } catch {}
    }
    this.isUploading.set(false);
  }

  // T02 — Select folder
  workDir      = computed(() => this.settings.get().workDir);
  workDirLabel = computed(() => {
    const d = this.settings.get().workDir;
    return d ? (d.split(/[/\\]/).pop() || d) : '本機';
  });

  async pickFolder() {
    const dir = await this.claude.pickDirectory();
    if (dir) { this.settings.save({ workDir: dir }); this.settingsForm.workDir = dir; }
  }

  async pickProjectDir() {
    const dir = await this.claude.pickDirectory();
    if (dir) this.settingsForm.projectDir = dir;
  }

  async pickClaudeHome() {
    const dir = await this.claude.pickDirectory();
    if (dir) this.settingsForm.claudeHome = dir;
  }

  // T07 — Dashboard stats
  stats = signal<{
    sessions: number; messages: number; total_tokens: number;
    active_days: number; streak_current: number; streak_longest: number;
    heatmap: Record<string, number>;
  } | null>(null);

  heatmapDays = computed(() => {
    const h = this.stats()?.heatmap;
    if (!h) return [];
    return Object.entries(h).sort(([a], [b]) => a < b ? -1 : 1)
      .map(([date, count]) => ({ date, count }));
  });

  heatmapMax = computed(() => {
    const days = this.heatmapDays();
    return Math.max(1, ...days.map(d => d.count));
  });

  heatmapOpacity(count: number): number {
    if (count === 0) return 0;
    return Math.max(0.15, count / this.heatmapMax());
  }

  // T07 — token fun fact
  readonly BOOKS = [
    { name: '哈利波特（全集）', tokens: 1_100_000 },
    { name: '戰爭與和平',       tokens: 580_000 },
    { name: '傲慢與偏見',       tokens: 130_000 },
    { name: '星際大戰劇本',     tokens: 30_000  },
  ];
  funFact = computed(() => {
    const t = this.stats()?.total_tokens;
    if (!t) return '';
    for (const b of this.BOOKS) {
      const x = (t / b.tokens).toFixed(1);
      if (t >= b.tokens * 0.3) return `你用掉的 token 相當於讀了 ${x} 本${b.name}`;
    }
    return '';
  });

  // T06 — tool timer
  private toolTick = signal(0);
  private _toolTickTimer: any;

  getToolElapsed(msg: ChatMessage): number {
    this.toolTick(); // reactive dependency
    if (!msg.startTime || !msg.isRunning) return 0;
    return Math.floor((Date.now() - msg.startTime) / 1000);
  }

  // Quick prompts
  quickPrompts = computed(() => this.settings.get().quickPrompts);
  showQuickPromptsEdit = false;
  quickPromptsForm: QuickPrompt[] = [];

  openQuickPromptsEdit() {
    this.quickPromptsForm = [...this.settings.get().quickPrompts];
    this.showQuickPromptsEdit = true;
  }

  saveQuickPrompts() {
    this.settings.save({ quickPrompts: this.quickPromptsForm });
    this.showQuickPromptsEdit = false;
  }

  addQuickPrompt() {
    this.quickPromptsForm.push({ label: '✨ 新提示', text: '' });
  }

  removeQuickPrompt(i: number) {
    this.quickPromptsForm.splice(i, 1);
  }

  // Remaining tokens
  remainingTokens = computed(() => {
    const u = this.tokenUsage();
    if (!u) return null;
    return Math.max(0, 200000 - u.input - u.output);
  });

  // Built-in slash commands
  readonly BUILTIN_CMDS = [
    { id: '__new',       name: 'new',       description: '開始新對話' },
    { id: '__clear',     name: 'clear',     description: '清除目前訊息' },
    { id: '__undo',      name: 'undo',      description: '撤銷最後一次對話（移除最後一組問答）' },
    { id: '__retry',     name: 'retry',     description: '重試上一則訊息' },
    { id: '__compact',   name: 'compact',   description: '壓縮對話以節省 token' },
    { id: '__model',     name: 'model',     description: '切換 AI 模型' },
    { id: '__usage',     name: 'usage',     description: '顯示 token 用量' },
    { id: '__debug',     name: 'debug',     description: '切換 debug 模式' },
    { id: '__status',    name: 'status',    description: '顯示 Claude 狀態' },
    { id: '__review',    name: 'review',    description: '程式碼審查（Code Review）' },
    { id: '__plan',      name: 'plan',      description: '規劃實作步驟' },
    { id: '__tdd',       name: 'tdd',       description: '測試驅動開發流程' },
    { id: '__explain',   name: 'explain',   description: '解釋目前的程式碼或問題' },
    { id: '__git',       name: 'git',       description: '顯示 Git 狀態與最近提交' },
    { id: '__search',    name: 'search',    description: '搜尋對話歷史' },
    { id: '__shortcuts', name: 'shortcuts', description: '顯示所有鍵盤快捷鍵' },
  ];

  // Model picker
  readonly MODEL_PICKER_OPTIONS = [
    { id: 'opus',   label: 'Opus 4.8',   desc: '最強能力，適合複雜任務' },
    { id: 'sonnet', label: 'Sonnet 4.6', desc: '速度與能力的最佳平衡（預設）' },
    { id: 'haiku',  label: 'Haiku 4.5',  desc: '最快速，適合簡單任務' },
    { id: 'fable',  label: 'Fable 5',    desc: '特殊能力模型' },
  ];
  modelPickerOpen = signal(false);

  // Cron presets
  readonly CRON_PRESETS = [
    { label: '每 5 分鐘',  value: '*/5 * * * *' },
    { label: '每小時',     value: '0 * * * *'   },
    { label: '每天 9:00',  value: '0 9 * * *'   },
    { label: '每週一早上', value: '0 9 * * 1'   },
  ];

  // Session pin/star
  pinnedIds = signal<string[]>([]);

  pinnedSessions = computed(() =>
    this.sessions().filter(s => this.pinnedIds().includes(s.id))
  );

  groupedSessionsWithPins = computed(() => {
    const pinned = this.pinnedIds();
    const now = Date.now() / 1000;
    const day = 86400;
    const groups: { label: string; items: any[]; pinned?: boolean }[] = [];
    const pinItems = this.sessions().filter(s => pinned.includes(s.id));
    if (pinItems.length) groups.push({ label: '📌 置頂', items: pinItems, pinned: true });
    const unpinned = this.sessions().filter(s => !pinned.includes(s.id));
    const today: any[] = [], yesterday: any[] = [], week: any[] = [], older: any[] = [];
    for (const s of unpinned) {
      const age = now - s.mtime;
      if      (age < day)       today.push(s);
      else if (age < 2 * day)   yesterday.push(s);
      else if (age < 7 * day)   week.push(s);
      else                      older.push(s);
    }
    if (today.length)     groups.push({ label: '今天',  items: today });
    if (yesterday.length) groups.push({ label: '昨天',  items: yesterday });
    if (week.length)      groups.push({ label: '本週',  items: week });
    if (older.length)     groups.push({ label: '更早',  items: older });
    return groups;
  });

  togglePin(id: string, e: Event) {
    e.stopPropagation();
    this.pinnedIds.update(ids =>
      ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id]
    );
    localStorage.setItem('claude_pinned_sessions', JSON.stringify(this.pinnedIds()));
  }

  isPinned(id: string) { return this.pinnedIds().includes(id); }

  // Per-message cost tracking
  private _prevCostUsd = 0;

  // Keyboard shortcuts
  @HostListener('window:keydown', ['$event'])
  onGlobalKey(e: KeyboardEvent) {
    const tag = (e.target as HTMLElement).tagName;
    const inInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
    if (e.ctrlKey && e.key === 'n' && !inInput) { e.preventDefault(); this.addChatTab(); }
    if (e.ctrlKey && e.key === 'b' && !inInput) { e.preventDefault(); this.sidebarOpen.update(v => !v); }
    if (e.ctrlKey && e.key === 'k') { e.preventDefault(); if (this.cmdOpen()) this.closeCmd(); else this.openCmd(); }
    if (e.key === 'Escape') {
      if (this.contextMenu())         this.closeContextMenu();
      else if (this.cmdOpen())        this.closeCmd();
      else if (this.settingsOpen())   this.settingsOpen.set(false);
      else if (this.expandedAgentId() || this.expandedSkillId() || this.expandedMcpId()) {
        this.expandedAgentId.set('');
        this.expandedSkillId.set('');
        this.expandedMcpId.set('');
        this.expandedTranslation.set(null);
      }
      else if (this.showSettingsHelp()) this.showSettingsHelp.set(false);
      else if (this.renamingId())     this.renamingId.set(null);
    }
  }

  // Stop streaming
  private stopFn: (() => void) | null = null;

  stopStreaming() {
    if (this.stopFn) { this.stopFn(); this.stopFn = null; }
    this.claude.stopChat().subscribe();
    this.isStreaming.set(false);
    this.messages.update(msgs => {
      const copy = [...msgs];
      const last = copy[copy.length - 1];
      if (last?.role === 'assistant') copy[copy.length - 1] = { ...last, isStreaming: false };
      return copy;
    });
  }

  // Session rename
  renamingId  = signal<string | null>(null);
  renameTitle = '';

  startRename(s: Session, event: Event) {
    event.stopPropagation();
    this.renamingId.set(s.id);
    this.renameTitle = s.title;
  }

  confirmRename(s: Session) {
    const title = this.renameTitle.trim();
    if (title && title !== s.title) {
      this.claude.renameSession(s.id, title).subscribe(() =>
        this.claude.getSessions(this.sessionSearch, 0).subscribe(r => this.sessions.set(r.items))
      );
    }
    this.renamingId.set(null);
  }

  // Memory editing
  memoryDraft     = '';
  memoryDraftSaved = signal(true);
  contextMemoryKeys = signal<string[]>([]);

  selectMemoryKey(key: string) {
    this.selectedMemoryKey.set(key);
    this.memoryDraft = this.memory()[key] ?? '';
    this.memoryDraftSaved.set(true);
  }

  onMemoryEdit() {
    this.memoryDraftSaved.set(false);
  }

  saveMemoryEdit() {
    const key = this.selectedMemoryKey();
    if (!key) return;
    this.claude.saveMemory(key, this.memoryDraft).subscribe(() => {
      this.memoryDraftSaved.set(true);
      this.memory.update(m => ({ ...m, [key]: this.memoryDraft }));
    });
  }

  discardMemoryEdit() {
    const key = this.selectedMemoryKey();
    if (!key) return;
    this.memoryDraft = this.memory()[key] ?? '';
    this.memoryDraftSaved.set(true);
  }

  isMemoryInContext(key: string): boolean {
    return this.contextMemoryKeys().includes(key);
  }

  toggleMemoryContext(key: string) {
    this.contextMemoryKeys.update(keys =>
      keys.includes(key) ? keys.filter(k => k !== key) : [...keys, key]
    );
  }

  // ── Toast notification system ────────────────────────────────────────────
  toasts = signal<{ id: string; text: string; type: 'success' | 'error' | 'info' | 'warn' }[]>([]);

  showToast(text: string, type: 'success' | 'error' | 'info' | 'warn' = 'info', duration = 3000) {
    const id = `t-${Date.now()}-${Math.random()}`;
    this.toasts.update(t => [...t, { id, text, type }]);
    setTimeout(() => this.toasts.update(t => t.filter(x => x.id !== id)), duration);
  }

  dismissToast(id: string) {
    this.toasts.update(t => t.filter(x => x.id !== id));
  }

  // ── Session right-click context menu ─────────────────────────────────────
  contextMenu = signal<{ x: number; y: number; session: Session } | null>(null);

  onSessionContextMenu(s: Session, e: MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    // Keep menu inside viewport
    const x = Math.min(e.clientX, window.innerWidth  - 180);
    const y = Math.min(e.clientY, window.innerHeight - 180);
    this.contextMenu.set({ x, y, session: s });
  }

  closeContextMenu() { this.contextMenu.set(null); }

  ctxRename(s: Session) {
    this.renamingId.set(s.id);
    this.renameTitle = s.title;
    this.closeContextMenu();
  }

  ctxDelete(s: Session) {
    this.closeContextMenu();
    this.claude.deleteSession(s.id).subscribe(() =>
      this.claude.getSessions(this.sessionSearch, 0).subscribe(r => this.sessions.set(r.items))
    );
  }

  ctxCopyId(s: Session) {
    navigator.clipboard.writeText(s.id).then(() => this.showToast('Session ID 已複製', 'success', 1500));
    this.closeContextMenu();
  }

  // Copy message
  copyMessage(text: string) {
    navigator.clipboard.writeText(text).then(() => this.showToast('已複製到剪貼簿', 'success', 1500));
  }

  // Code block copy (event delegation from chat container)
  onChatClick(e: MouseEvent) {
    const btn = (e.target as HTMLElement).closest('[data-copy-code]') as HTMLElement | null;
    if (!btn) return;
    const code = btn.closest('.code-block-wrap')?.querySelector('code') as HTMLElement | null;
    if (!code) return;
    navigator.clipboard.writeText(code.innerText).then(() => {
      const orig = btn.textContent;
      btn.textContent = '✓ 已複製';
      btn.classList.add('copied');
      setTimeout(() => { btn.textContent = orig; btn.classList.remove('copied'); }, 2000);
    });
  }

  // Message edit + regenerate (#11)
  editingMsgIdx  = signal<number | null>(null);
  editingMsgText = signal('');

  startEditMsg(idx: number, text: string) {
    this.editingMsgIdx.set(idx);
    this.editingMsgText.set(text);
  }
  cancelEditMsg() {
    this.editingMsgIdx.set(null);
    this.editingMsgText.set('');
  }
  confirmEditMsg(idx: number) {
    const newText = this.editingMsgText().trim();
    if (!newText) { this.cancelEditMsg(); return; }
    // slice off from this user message onward
    this.messages.set(this.messages().slice(0, idx));
    this.editingMsgIdx.set(null);
    this.editingMsgText.set('');
    this.inputText = newText;
    // slight delay so DOM settles before send
    setTimeout(() => this.send(), 50);
  }

  // ── #17 Profile switching ─────────────────────────────────────────────────
  profiles         = signal<Profile[]>([]);
  profileSwitching = signal(false);

  loadProfiles() {
    this.claude.getProfiles().subscribe(r => this.profiles.set(r.profiles));
  }

  switchProfile(slug: string) {
    this.profileSwitching.set(true);
    // Decode slug back to directory path (e.g. C--Users-666-Desktop-myproject → C:\Users\666\Desktop\myproject)
    const dir = slug
      .replace(/^([A-Za-z])--/, '$1:\\')
      .replace(/--/g, '\\');
    this.claude.setConfig({ projectDir: dir }).subscribe({
      next: () => {
        this.settingsForm.projectDir = dir;
        this.settings.save(this.settingsForm);
        this.profileSwitching.set(false);
        this.reload();
      },
      error: () => this.profileSwitching.set(false),
    });
  }

  // ── #16 Provider mode ─────────────────────────────────────────────────────
  useProvider = computed(() => this.settings.get().provider !== 'claude');

  // ── #19 i18n ──────────────────────────────────────────────────────────────
  readonly EN_STRINGS: Record<string, string> = {
    '新對話':       'New Chat',
    '設定':         'Settings',
    '搜尋對話':     'Search chats',
    '發送訊息':     'Send message',
    '停止':         'Stop',
    '今天':         'Today',
    '昨天':         'Yesterday',
    '本週':         'This week',
    '更早':         'Earlier',
    '置頂':         'Pinned',
    '模型':         'Model',
    '記憶':         'Memory',
    '排程':         'Schedule',
    'Agents':       'Agents',
    'Skills':       'Skills',
    'MCP':          'MCP',
    '匯出':         'Export',
    '備份':         'Backup',
    '說明':         'Help',
    '工作目錄':     'Work dir',
    '目前無對話':   'No conversations yet',
    '無記憶項目':   'No memory items',
  };

  t(key: string): string {
    const lang = this.settings.get().lang ?? 'zh';
    if (lang === 'en') return this.EN_STRINGS[key] ?? key;
    return key;
  }

  setLang(lang: 'zh' | 'en') {
    this.settings.save({ lang });
  }

  // ── #21 Multi-format export ───────────────────────────────────────────────
  exportFormat = signal<'md' | 'json' | 'txt'>('md');

  exportChatAs(format: 'md' | 'json' | 'txt') {
    const msgs = this.messages().filter(m => m.role !== 'error' && m.role !== 'system');
    const date  = new Date().toLocaleString('zh-TW');
    let content = '';
    let mime    = 'text/plain';
    let ext     = format;

    if (format === 'md') {
      mime = 'text/markdown';
      const lines = msgs.map(m => {
        const ts = m.time ? ` *(${m.time})*` : '';
        if (m.role === 'user')      return `## 使用者${ts}\n\n${m.text}`;
        if (m.role === 'assistant') return `## Claude${ts}\n\n${m.text}`;
        if (m.role === 'tool') {
          const res = m.result ? `\n\n**結果：**\n\`\`\`\n${m.result}\n\`\`\`` : '';
          return `## 工具：${m.toolName}\n\n\`\`\`json\n${m.text}\n\`\`\`${res}`;
        }
        return '';
      }).filter(Boolean);
      content = `# 對話匯出\n\n> ${date}\n\n${lines.join('\n\n---\n\n')}`;
    } else if (format === 'json') {
      mime = 'application/json';
      content = JSON.stringify({ exported: date, messages: msgs }, null, 2);
    } else {
      content = msgs.map(m => {
        const who = m.role === 'user' ? '使用者' : m.role === 'assistant' ? 'Claude' : m.toolName ?? m.role;
        return `[${who}]\n${m.text}\n`;
      }).join('\n---\n\n');
    }

    const blob = new Blob([content], { type: mime });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url; a.download = `chat-${Date.now()}.${ext}`; a.click();
    URL.revokeObjectURL(url);
  }

  // ── #20 Debug dump ────────────────────────────────────────────────────────
  downloadDebugDump() {
    window.open(this.claude.debugDumpUrl(), '_blank');
  }

  // ── #18 Telegram settings ─────────────────────────────────────────────────
  telegramToken   = '';
  telegramEnabled = signal(false);
  telegramRunning = signal(false);
  telegramSaving  = signal(false);

  loadTelegramSettings() {
    this.claude.getTelegram().subscribe(r => {
      this.telegramToken   = r.token;
      this.telegramEnabled.set(r.enabled);
      this.telegramRunning.set(r.running);
    });
  }

  saveTelegramSettings() {
    this.telegramSaving.set(true);
    this.claude.setTelegram({
      token:   this.telegramToken,
      enabled: this.telegramEnabled(),
    }).subscribe({
      next: r => {
        this.telegramRunning.set(r.running);
        this.telegramSaving.set(false);
      },
      error: () => this.telegramSaving.set(false),
    });
  }

  // ── #22 Auto-update progress ──────────────────────────────────────────────
  updateProgress  = signal<number | null>(null);
  updateAvailable = signal(false);
  updateReady     = signal(false);

  // MCP log viewer (#15)
  mcpLogOpen   = signal<string | null>(null);
  mcpLogLines  = signal<string[]>([]);

  toggleMcpLog(name: string) {
    if (this.mcpLogOpen() === name) {
      this.mcpLogOpen.set(null);
      return;
    }
    this.mcpLogOpen.set(name);
    this.refreshMcpLog(name);
  }
  refreshMcpLog(name: string) {
    this.claude.getMcpLogs(name).subscribe(r => this.mcpLogLines.set(r.lines));
  }

  // Auto session title (#10)
  private _autoTitleBusy = false;
  triggerAutoTitle() {
    if (this._autoTitleBusy) return;
    this._autoTitleBusy = true;
    // Find latest session (first after reload) and auto-title if title looks auto-generated
    setTimeout(() => {
      const sess = this.sessions();
      if (!sess.length) { this._autoTitleBusy = false; return; }
      const latest = sess[0];
      // Only auto-title if title looks like truncated user text (< 80 chars, no special structure)
      if (latest.title && latest.title.length < 80) {
        this.claude.autoTitleSession(latest.id).subscribe({
          next: r => {
            this.sessions.update(list =>
              list.map(s => s.id === latest.id ? { ...s, title: r.title } : s)
            );
            this._autoTitleBusy = false;
          },
          error: () => { this._autoTitleBusy = false; },
        });
      } else {
        this._autoTitleBusy = false;
      }
    }, 1500);
  }

  // Auto-resize textarea
  autoResize(event: Event) {
    const el = event.target as HTMLTextAreaElement;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }

  // Export chat
  resetSoul() {
    if (!confirm('確定要清空靈魂提示詞嗎？')) return;
    this.claude.resetSoul().subscribe(() => { this.soulContent = ''; this.soulSaved.set(true); });
  }

  async restoreBackup(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    const res = await this.claude.restoreBackup(file);
    if (res.ok) {
      this.showToast('還原成功！重新整理中…', 'success');
      this.reload();
      this.claude.getSoul().subscribe(s => { this.soulContent = s; });
    } else {
      this.showToast('還原失敗：' + res.error, 'error', 5000);
    }
    input.value = '';
  }

  downloadBackup() {
    const port = this.settings.get().backendPort;
    fetch(`http://localhost:${port}/api/backup`)
      .then(r => r.blob())
      .then(blob => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = `claude-backup-${Date.now()}.zip`; a.click();
        URL.revokeObjectURL(url);
      });
  }

  exportChat() {
    this.exportChatAs(this.exportFormat());
  }

  // Retry last message
  private lastUserText   = '';
  private lastAttachments: string[] = [];

  retryLast() {
    if (!this.lastUserText || this.isStreaming()) return;
    this.inputText = this.lastUserText;
    this.attachedFiles.set(this.lastAttachments.map(p => ({ name: p.split(/[\\/]/).pop()!, path: p })));
    this.send();
  }

  // Workdir quick switch
  recentWorkDirs = computed(() => this.settings.get().recentWorkDirs);

  setWorkDir(dir: string) {
    this.settingsForm.workDir = dir;
    this.settings.save(this.settingsForm);
  }

  // Grouped sessions
  groupedSessions = computed(() => {
    const now = Date.now() / 1000;
    const day = 86400;
    const groups: { label: string; items: Session[] }[] = [
      { label: '今天',  items: [] },
      { label: '昨天',  items: [] },
      { label: '本週',  items: [] },
      { label: '更早',  items: [] },
    ];
    for (const s of this.sessions()) {
      const age = now - s.mtime;
      if      (age < day)       groups[0].items.push(s);
      else if (age < 2 * day)   groups[1].items.push(s);
      else if (age < 7 * day)   groups[2].items.push(s);
      else                      groups[3].items.push(s);
    }
    return groups.filter(g => g.items.length > 0);
  });

  // Detail modal
  detailItem = signal<{ id: string; label: string; name: string; description: string; type: 'agent' | 'skill' | 'mcp' } | null>(null);
  detailTranslation = signal<string | null>(null);   // null = not requested, '' = loading

  openDetail(item: { id: string; label: string; name: string; description: string; type: 'agent' | 'skill' | 'mcp' }) {
    this.detailItem.set(item);
    this.detailTranslation.set(null);
  }

  closeDetail() {
    this.detailItem.set(null);
    this.detailTranslation.set(null);
  }

  translateDetail() {
    const item = this.detailItem();
    if (!item || this.detailTranslation() !== null) return;
    this.detailTranslation.set('');
    this.claude.translate(item.description).subscribe({
      next:  r => this.detailTranslation.set(r),
      error: () => this.detailTranslation.set('[翻譯失敗，請重試]'),
    });
  }

  clearDetailTranslation() {
    this.detailTranslation.set(null);
  }

  // Slash command menu
  slashMenuOpen  = signal(false);
  slashMenuIndex = signal(0);
  slashMenuItems = computed(() => {
    const q = this.slashQuery().toLowerCase();
    const builtins = this.BUILTIN_CMDS.filter(c => !q || c.name.includes(q));
    const skills   = this.skills().filter(s => !q || s.name.toLowerCase().includes(q));
    return [...builtins, ...skills].slice(0, 10);
  });
  private slashQuery = signal('');

  // UI state
  sidebarOpen  = signal(true);
  rightOpen    = signal(true);
  settingsOpen = signal(false);
  shouldScroll = false;

  settingsForm!: AppSettings;
  backendLogs      = signal<string[]>([]);
  statusInfo       = signal('確認中…');
  projectSlug      = signal('');
  resolvedClaudeHome = signal('');
  skillGenBusy     = signal(false);
  skillGenResult   = signal<string | null>(null);

  // ── Onboarding wizard ────────────────────────────────
  showOnboarding   = signal(false);
  onboardingStep   = signal(1);   // 1=歡迎 2=確認連線 3=專案目錄 4=完成
  onboardingDir    = signal('');
  onboardingStatus = signal<any>(null);
  onboardingSlug   = computed(() => {
    const d = this.onboardingDir();
    return d ? d.replace(/:/g, '-').replace(/\\/g, '-').replace(/\//g, '-') : '';
  });

  // ── Help modal ───────────────────────────────────────
  helpOpen      = signal(false);
  helpSection   = signal<'start'|'features'|'faq'>('start');

  memoryKeys       = computed(() => Object.keys(this.memory()));
  memoryTotalChars = computed(() =>
    Object.values(this.memory()).reduce((sum, v) => sum + v.length, 0)
  );
  memoryUsagePct   = computed(() => Math.min((this.memoryTotalChars() / 50000) * 100, 100));

  constructor(private claude: ClaudeService, private settings: SettingsService) {
    this.settingsForm = this.settings.get();
    const s = this.settings.get();
    this.model.set((s.model || 'sonnet') as any);
    this.effort.set((s.effort || 'medium') as any);
    this.permissionMode.set((s.permissionMode || 'acceptEdits') as any);
    // T11 — 初始化第一個 tab
    const firstTab = this.makeTab('新對話');
    this.chatTabs.set([firstTab]);
    this.activeChatId.set(firstTab.id);
    // T13 — 初始化 file tree 路徑
    this.fileTreePath.set(s.workDir || '');

    // Initialize authorizations
    const savedSkills = localStorage.getItem('claude_desktop_auth_skills');
    if (savedSkills) {
      try { this.authorizedSkills.set(JSON.parse(savedSkills)); } catch {}
    }
    const savedMcps = localStorage.getItem('claude_desktop_auth_mcps');
    if (savedMcps) {
      try { this.authorizedMcps.set(JSON.parse(savedMcps)); } catch {}
    }

    // 載入永久 agent 綁定
    try {
      const as = localStorage.getItem('claude_desktop_agent_skills');
      if (as) this.agentSkillsMap.set(JSON.parse(as));
    } catch {}
    try {
      const am = localStorage.getItem('claude_desktop_agent_mcps_direct');
      if (am) this.agentMcpsMap.set(JSON.parse(am));
    } catch {}
    try {
      const mm = localStorage.getItem('claude_desktop_managed_mcps');
      if (mm) this.managedMcpNames.set(JSON.parse(mm));
    } catch {}

    this.loadMcp();

    // 草稿恢復
    const draft = localStorage.getItem('claude_input_draft');
    if (draft) this.inputText = draft;

    // 置頂 session ID 恢復
    try {
      const pinned = localStorage.getItem('claude_pinned_sessions');
      if (pinned) this.pinnedIds.set(JSON.parse(pinned));
    } catch {}

    // 首次啟動精靈
    if (!localStorage.getItem('claude_onboarding_done')) {
      setTimeout(() => {
        this.showOnboarding.set(true);
        this.claude.getStatus().subscribe(s => this.onboardingStatus.set(s));
      }, 600);
    }
  }

  // ── Onboarding methods ──────────────────────────────
  nextOnboardingStep() {
    const s = this.onboardingStep();
    if (s < 4) { this.onboardingStep.set(s + 1); }
    else        { this.completeOnboarding(); }
  }
  prevOnboardingStep() {
    const s = this.onboardingStep();
    if (s > 1) this.onboardingStep.set(s - 1);
  }
  completeOnboarding() {
    const dir = this.onboardingDir().trim();
    if (dir) {
      this.claude.setConfig({ projectDir: dir }).subscribe();
      this.settingsForm.projectDir = dir;
      this.settings.save(this.settingsForm);
    }
    localStorage.setItem('claude_onboarding_done', '1');
    this.showOnboarding.set(false);
    this.reload();
  }
  skipOnboarding() {
    localStorage.setItem('claude_onboarding_done', '1');
    this.showOnboarding.set(false);
  }
  resetOnboarding() {   // 可從設定手動重開精靈
    localStorage.removeItem('claude_onboarding_done');
    this.onboardingStep.set(1);
    this.onboardingDir.set('');
    this.showOnboarding.set(true);
    this.claude.getStatus().subscribe(s => this.onboardingStatus.set(s));
  }
  async pickOnboardingDir() {
    const dir = await this.claude.pickDirectory();
    if (dir) this.onboardingDir.set(dir);
  }

  openSettings() {
    this.settingsForm = this.settings.get();
    this.settingsOpen.set(true);
    this.loadLogs();
    this.loadTelegramSettings();
    this.claude.getStatus().subscribe(s => {
      this.statusInfo.set(s.claude_bin ?? '未知');
    });
    this.claude.getConfig().subscribe((c: any) => {
      this.projectSlug.set(c.slug ?? '');
      this.resolvedClaudeHome.set(c._resolvedClaudeHome ?? '');
      if (!this.settingsForm.projectDir && c.projectDir) {
        this.settingsForm.projectDir = c.projectDir;
      }
      if (!this.settingsForm.apiKeyCmd && c.apiKeyCmd) {
        this.settingsForm.apiKeyCmd = c.apiKeyCmd;
      }
      if (!this.settingsForm.claudeHome && c.claudeHome) {
        this.settingsForm.claudeHome = c.claudeHome;
      }
    });
  }

  loadLogs() {
    this.claude.getLogs().subscribe(l => this.backendLogs.set(l));
  }

  saveSettings() {
    this.settings.save(this.settingsForm);
    this.claude.setConfig({
      projectDir: this.settingsForm.projectDir,
      apiKeyCmd:  this.settingsForm.apiKeyCmd,
      claudeHome: this.settingsForm.claudeHome,
    }).subscribe();
    this.settingsOpen.set(false);
  }

  ngOnInit() {
    this.reload();
    this.claude.getSoul().subscribe(s => { this.soulContent = s; });
    this.loadProfiles();
    this._healthTimer = setInterval(() => {
      this.claude.getStatus().subscribe({
        next: () => {
          if (this.backendDown()) { this.backendDown.set(false); this.reload(); }
        },
        error: () => this.backendDown.set(true),
      });
    }, 10000);
    // T06 — 每秒更新 tool timer
    this._toolTickTimer = setInterval(() => this.toolTick.update(v => v + 1), 1000);

    // #22 — Wire Electron auto-updater IPC events
    const eAPI = (window as any).electronAPI;
    if (eAPI?.onUpdateProgress)  eAPI.onUpdateProgress((pct: number)  => this.updateProgress.set(pct));
    if (eAPI?.onUpdateAvailable) eAPI.onUpdateAvailable(() => this.updateAvailable.set(true));
    if (eAPI?.onUpdateReady)     eAPI.onUpdateReady(() => { this.updateReady.set(true); this.updateProgress.set(100); });
  }

  ngOnDestroy() { clearInterval(this._healthTimer); clearInterval(this._toolTickTimer); }

  // T03 — Ctrl+V 截圖貼上
  @HostListener('paste', ['$event'])
  async onPaste(e: ClipboardEvent) {
    const items = Array.from(e.clipboardData?.items ?? []);
    const imgItem = items.find(i => i.type.startsWith('image/'));
    if (!imgItem) return;
    e.preventDefault();
    const file = imgItem.getAsFile();
    if (!file) return;
    this.isUploading.set(true);
    try {
      const preview = URL.createObjectURL(file);
      const result  = await this.claude.uploadFile(file);
      this.attachedFiles.update(a => [...a, { ...result, preview }]);
    } catch {}
    this.isUploading.set(false);
  }

  selectSoulProfile(id: string) {
    this.selectedSoulId.set(id);
    const s = this.souls().find(x => x.id === id);
    this.soulDraft = s?.content ?? '';
    this.soulDraftSaved.set(true);
  }

  onSoulEdit() {
    this.soulDraftSaved.set(false);
  }

  onSoulListWheel(event: WheelEvent) {
    event.preventDefault();
    const list = this.souls();
    if (!list.length) return;
    const idx = list.findIndex(s => s.id === this.selectedSoulId());
    const next = Math.max(0, Math.min(list.length - 1, idx + (event.deltaY > 0 ? 1 : -1)));
    if (next !== idx) this.selectSoulProfile(list[next].id);
  }

  saveSoulProfileEdits() {
    const id = this.selectedSoulId();
    if (!id) return;
    this.claude.saveSoulProfile(id, this.soulDraft).subscribe(() => {
      this.soulDraftSaved.set(true);
      this.souls.update(list => list.map(x => x.id === id ? { ...x, content: this.soulDraft } : x));
    });
  }

  discardSoulProfileEdits() {
    const id = this.selectedSoulId();
    if (!id) return;
    const s = this.souls().find(x => x.id === id);
    this.soulDraft = s?.content ?? '';
    this.soulDraftSaved.set(true);
  }

  addSoulProfile() {
    let name = this.newSoulName.trim();
    if (!name) return;
    if (name.toLowerCase().endsWith('.md')) {
      name = name.slice(0, -3).trim();
    }
    if (!name) return;
    const id = name.replace(/\s+/g, '-');
    const defaultContent = `# ${name}

## Role
<!-- Describe the identity or persona Claude should take on. -->


## Tone & Style
<!-- e.g. concise, warm, professional, technical -->


## Rules
<!-- Hard rules Claude must always follow in this persona. -->
- 

## Context
<!-- Any background knowledge or constraints relevant to this persona. -->

`;
    this.claude.saveSoulProfile(id, defaultContent).subscribe({
      next: () => {
        this.newSoulName = '';
        this.claude.getSouls().subscribe(list => {
          this.souls.set(list);
          this.selectSoulProfile(id);
        });
      },
      error: (err) => {
        console.error(err);
        this.showToast('名稱只能包含字母、數字、連字號（-）或底線（_）', 'error', 4000);
      }
    });
  }

  deleteSoulProfile(id: string) {
    if (!confirm(`確定要刪除「${id}.md」嗎？`)) return;
    this.claude.deleteSoulProfile(id).subscribe(() => {
      if (this.selectedSoulId() === id) {
        this.selectedSoulId.set('');
        this.soulDraft = '';
      }
      this.claude.getSouls().subscribe(list => {
        this.souls.set(list);
        if (list.length && !this.selectedSoulId()) {
          this.selectSoulProfile(list[0].id);
        }
      });
    });
  }

  addSchedule() {
    if (!this.newSchedulePrompt.trim() || !this.newScheduleCron.trim()) return;
    this.claude.addSchedule(this.newSchedulePrompt, this.newScheduleCron).subscribe(() => {
      this.newSchedulePrompt = '';
      this.newScheduleCron = '';
      this.claude.getSchedules().subscribe(s => this.schedules.set(s));
    });
  }

  deleteSchedule(id: string) {
    this.claude.deleteSchedule(id).subscribe(() => {
      this.claude.getSchedules().subscribe(s => this.schedules.set(s));
    });
  }

  toggleSchedule(id: string, enabled: boolean) {
    this.claude.toggleSchedule(id, !enabled).subscribe(() => {
      this.claude.getSchedules().subscribe(s => this.schedules.set(s));
    });
  }

  runScheduleNow(id: string) {
    this.claude.runSchedule(id).subscribe(() => {
      this.claude.getSchedules().subscribe(s => this.schedules.set(s));
    });
  }

  searchSessions() {
    this.sessionOffset = 0;
    this.claude.getSessions(this.sessionSearch, 0).subscribe(r => {
      this.sessions.set(r.items);
      this.hasMoreSessions.set(r.has_more);
    });
  }

  loadMoreSessions() {
    this.sessionOffset += 30;
    this.claude.getSessions(this.sessionSearch, this.sessionOffset).subscribe(r => {
      this.sessions.update(s => [...s, ...r.items]);
      this.hasMoreSessions.set(r.has_more);
    });
  }

  deleteMemory(key: string) {
    this.claude.deleteMemory(key).subscribe(() => {
      this.selectedMemoryKey.set('');
      this.claude.getMemory().subscribe(m => this.memory.set(m));
    });
  }

  reload() {
    this.sessionOffset = 0;
    this.claude.getStats().subscribe(s => this.stats.set(s));
    this.claude.getAgents().subscribe(a => this.agents.set(a));
    this.claude.getSkills().subscribe(s => this.skills.set(s));
    this.claude.getSessions(this.sessionSearch, 0).subscribe(r => {
      this.sessions.set(r.items);
      this.hasMoreSessions.set(r.has_more);
    });
    this.claude.getSchedules().subscribe(s => this.schedules.set(s));
    this.claude.getMemory().subscribe(m => {
      this.memory.set(m);
      const keys = Object.keys(m);
      if (keys.length && !this.selectedMemoryKey()) {
        this.selectedMemoryKey.set(keys[0]);
        this.memoryDraft = m[keys[0]] ?? '';
      }
    });
    this.claude.getSouls().subscribe(list => {
      this.souls.set(list);
      if (list.length && !this.selectedSoulId()) {
        this.selectSoulProfile(list[0].id);
      }
    });
  }

  ngAfterViewChecked() {
    if (this.shouldScroll) {
      this.chatEnd?.nativeElement?.scrollIntoView({ behavior: 'smooth' });
      this.shouldScroll = false;
    }
  }

  async pickFile(event: Event) {
    const input = event.target as HTMLInputElement;
    if (!input.files?.length) return;
    this.isUploading.set(true);
    for (const file of Array.from(input.files)) {
      try {
        const preview = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined;
        const result  = await this.claude.uploadFile(file);
        this.attachedFiles.update(a => [...a, { ...result, preview }]);
      } catch { /* ignore upload error */ }
    }
    this.isUploading.set(false);
    input.value = '';
  }

  removeAttachment(path: string) {
    this.attachedFiles.update(a => a.filter(f => f.path !== path));
  }

  send() {
    const text = this.inputText.trim();
    if (!text || this.isStreaming()) return;
    this.lastUserText    = text;
    this.lastAttachments = this.attachedFiles().map(f => f.path);
    this.inputText = '';
    localStorage.removeItem('claude_input_draft');
    this.isStreaming.set(true);
    const attachments = this.attachedFiles().map(f => f.path);
    this.attachedFiles.set([]);

    // T11 — 若 tab 還是預設名稱，用第一條訊息更新
    const curTab = this.activeChat;
    if (curTab && curTab.label === '新對話') {
      const id = this.activeChatId();
      this.chatTabs.update(tabs => tabs.map(t => t.id === id ? { ...t, label: text.slice(0, 20) } : t));
    }
    const displayText = text + (attachments.length ? ` 📎×${attachments.length}` : '');
    const now = new Date().toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' });
    this.messages.update(m => [...m, { role: 'user', text: displayText, time: now }]);
    const assistantMsg: ChatMessage = { role: 'assistant', text: '', isStreaming: true, time: now };
    this.messages.update(m => [...m, assistantMsg]);
    this.shouldScroll = true;

    // Build event handler (shared between Claude and provider mode)
    const onEvent = (ev: any) => {
        if (ev.type === 'assistant' && ev.message?.content) {
          // tool is done once assistant starts replying
          this.messages.update(msgs => msgs.map(m => m.isRunning ? { ...m, isRunning: false } : m));
          for (const block of ev.message.content) {
            if (block.type === 'text') {
              this.messages.update(msgs => {
                const copy = [...msgs];
                copy[copy.length - 1] = { ...copy[copy.length - 1], text: copy[copy.length - 1].text + block.text };
                return copy;
              });
              this.shouldScroll = true;
              if (block.text && (block.text.toLowerCase().includes('session limit') || block.text.toLowerCase().includes('rate limit') || block.text.toLowerCase().includes('limit · resets') || block.text.toLowerCase().includes('quota'))) {
                this.outOfQuota.set(true);
              }
            }
          }
        } else if (ev.type === 'text') {
          this.messages.update(msgs => msgs.map(m => m.isRunning ? { ...m, isRunning: false } : m));
          this.messages.update(msgs => {
            const copy = [...msgs];
            copy[copy.length - 1] = { ...copy[copy.length - 1], text: copy[copy.length - 1].text + ev.text };
            return copy;
          });
          this.shouldScroll = true;
          if (ev.text && (ev.text.toLowerCase().includes('session limit') || ev.text.toLowerCase().includes('rate limit') || ev.text.toLowerCase().includes('limit · resets') || ev.text.toLowerCase().includes('quota'))) {
            this.outOfQuota.set(true);
          }
        } else if (ev.type === 'tool_use') {
          this.messages.update(m => [...m, {
            role: 'tool', text: JSON.stringify(ev.input ?? {}, null, 2),
            toolName: ev.name, toolUseId: ev.id, isRunning: true, startTime: Date.now()
          }]);
          this.shouldScroll = true;
        } else if (ev.type === 'user' && ev.message?.content) {
          for (const block of ev.message.content) {
            if (block.type === 'tool_result') {
              const res = typeof block.content === 'string'
                ? block.content
                : JSON.stringify(block.content);
              this.messages.update(msgs => msgs.map(m =>
                m.toolUseId === block.tool_use_id
                  ? { ...m, isRunning: false, result: res.slice(0, 3000) }
                  : m
              ));
            }
          }
        } else if (ev.type === 'result') {
          const totalCost = ev.total_cost_usd ?? 0;
          const msgCost   = Math.max(0, totalCost - this._prevCostUsd);
          this._prevCostUsd = totalCost;
          this.tokenUsage.set({
            input:  ev.usage?.input_tokens  ?? 0,
            output: ev.usage?.output_tokens ?? 0,
            cost:   totalCost,
          });
          // 標記本次訊息費用
          if (msgCost > 0) {
            this.messages.update(msgs => {
              const copy = [...msgs];
              for (let i = copy.length - 1; i >= 0; i--) {
                if (copy[i].role === 'assistant') {
                  copy[i] = { ...copy[i], cost: msgCost };
                  break;
                }
              }
              return copy;
            });
          }
        }
    };

    const onDone = () => {
      this.stopFn = null;
      this.messages.update(msgs =>
        msgs.map(m => m.isRunning ? { ...m, isRunning: false } : m)
      );
      this.messages.update(msgs => {
        const copy = [...msgs];
        copy[copy.length - 1] = { ...copy[copy.length - 1], isStreaming: false };
        return copy;
      });
      this.isStreaming.set(false);
      this.reload();
      this.triggerAutoTitle();
      this.shouldScroll = true;
      this.inputRef?.nativeElement?.focus();
      (window as any).electronAPI?.notify('Claude 完成', text.slice(0, 60));
    };

    const onError = (err: any) => {
      const errStr = String(err);
      this.messages.update(m => [...m, { role: 'error', text: errStr }]);
      this.isStreaming.set(false);
      if (errStr.toLowerCase().includes('session limit') || errStr.toLowerCase().includes('rate limit') || errStr.toLowerCase().includes('limit · resets') || errStr.toLowerCase().includes('quota')) {
        this.outOfQuota.set(true);
      }
    };

    if (this.useProvider()) {
      // #16 — Route to OpenAI-compatible provider
      const history = this.messages()
        .filter(m => m.role === 'user' || m.role === 'assistant')
        .slice(0, -1) // exclude the empty placeholder we just pushed
        .map(m => ({ role: m.role as 'user' | 'assistant', content: m.text }));
      history.push({ role: 'user', content: text });
      this.stopFn = this.claude.streamProviderChat(history, onEvent, onDone, onError);
    } else {
      this.stopFn = this.claude.streamChat(
        text, this.selectedAgent(), onEvent, onDone, onError, attachments
      );
    }
  }

  onInput() {
    const val = this.inputText;
    // 草稿持久化
    if (val) localStorage.setItem('claude_input_draft', val);
    else     localStorage.removeItem('claude_input_draft');
    const slashMatch = val.match(/(?:^|\s)\/(\S*)$/);
    if (slashMatch) {
      this.slashQuery.set(slashMatch[1]);
      this.slashMenuOpen.set(true);
      this.slashMenuIndex.set(0);
    } else {
      this.slashMenuOpen.set(false);
    }
  }

  insertSlashCommand(item: { id: string; name: string }) {
    this.slashMenuOpen.set(false);
    this.inputText = '';
    if (item.id.startsWith('__')) {
      this.executeBuiltinCmd(item.id);
      return;
    }
    this.inputText = `/${item.name} `;
    this.inputRef?.nativeElement?.focus();
  }

  executeBuiltinCmd(id: string) {
    switch (id) {
      case '__new':
        this.newChat(); break;
      case '__clear':
        this.messages.set([]); break;
      case '__undo': {
        const msgs = this.messages();
        let cut = msgs.length;
        // remove last assistant block
        while (cut > 0 && msgs[cut - 1].role !== 'assistant') cut--;
        if (cut > 0) cut--;
        // remove trailing user message
        while (cut > 0 && msgs[cut - 1].role === 'user') cut--;
        this.messages.set(msgs.slice(0, cut));
        break;
      }
      case '__retry':
        this.retryLast(); break;
      case '__model':
        this.modelPickerOpen.set(true); break;
      case '__compact':
        this.inputText = '請簡潔摘要我們到目前為止的對話重點，之後以此摘要為基礎繼續對話。';
        this.inputRef?.nativeElement?.focus(); break;
      case '__usage':
        const u = this.tokenUsage();
        const info = u
          ? `📊 輸入 ${u.input.toLocaleString()} / 輸出 ${u.output.toLocaleString()} token，費用 $${u.cost.toFixed(4)}`
          : '📊 尚無 token 紀錄';
        this.messages.update(m => [...m, { role: 'system', text: info }]); break;
      case '__debug':
        this.debugMode.update(v => !v);
        this.messages.update(m => [...m, { role: 'system', text: `🐛 Debug 模式：${this.debugMode() ? '開啟' : '關閉'}` }]); break;
      case '__status':
        this.claude.getStatus().subscribe(s =>
          this.messages.update(m => [...m, { role: 'system', text: `⚡ Claude：${s.claude_bin}` }])
        ); break;
      case '__review':
        this.inputText = '幫我 Code Review 目前的程式碼，關注：可讀性、安全性、效能問題，並提供具體改善建議。';
        this.inputRef?.nativeElement?.focus(); break;
      case '__plan':
        this.inputText = '請幫我規劃以下功能的實作步驟，並考量架構影響、風險點與測試策略：\n';
        this.inputRef?.nativeElement?.focus(); break;
      case '__tdd':
        this.inputText = '請以 TDD 方式協助我實作以下功能。先寫測試，再實作，確保測試覆蓋率 ≥80%：\n';
        this.inputRef?.nativeElement?.focus(); break;
      case '__explain':
        this.inputText = '請詳細解釋以下程式碼的功能、設計思路與可能的問題：\n';
        this.inputRef?.nativeElement?.focus(); break;
      case '__git':
        this.inputText = '請執行 git status 和 git log --oneline -10，摘要目前的分支狀態與最近的提交。';
        this.inputRef?.nativeElement?.focus(); break;
      case '__search':
        document.querySelector<HTMLInputElement>('.session-search-input')?.focus(); break;
      case '__shortcuts':
        this.messages.update(m => [...m, { role: 'system', text:
          '⌨️ 快捷鍵：\n' +
          'Ctrl+N — 新對話分頁\n' +
          'Ctrl+B — 切換側欄\n' +
          'Ctrl+K — 指令面板\n' +
          'Ctrl+Enter — 傳送訊息（enterToSend=false 時）\n' +
          'Esc — 關閉彈窗 / 取消\n' +
          '/ — 輸入框中觸發技能選單\n' +
          'Alt+← / → — 切換對話分頁'
        }]); break;
    }
  }

  selectModel(modelId: string) {
    this.model.set(modelId as any);
    this.settings.save({ model: modelId });
    this.modelPickerOpen.set(false);
    const label = this.MODEL_LABELS[modelId] ?? modelId;
    this.messages.update(m => [...m, { role: 'system', text: `🤖 已切換模型：${label}` }]);
  }

  generateSkillFromSession(sessionId: string) {
    if (this.skillGenBusy()) return;
    this.skillGenBusy.set(true);
    this.skillGenResult.set(null);
    this.claude.generateSkill(sessionId).subscribe({
      next: r => {
        this.skillGenBusy.set(false);
        this.skillGenResult.set(`✅ Skill 已儲存：${r.path}`);
        this.claude.getSkills().subscribe(s => this.skills.set(s));
      },
      error: e => {
        this.skillGenBusy.set(false);
        this.skillGenResult.set(`❌ ${e?.error?.error || String(e)}`);
      }
    });
  }

  onKeyDown(e: KeyboardEvent) {
    if (this.slashMenuOpen()) {
      const items = this.slashMenuItems();
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        this.slashMenuIndex.update(i => Math.min(i + 1, items.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        this.slashMenuIndex.update(i => Math.max(i - 1, 0));
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        const item = items[this.slashMenuIndex()];
        if (item) this.insertSlashCommand(item);
      } else if (e.key === 'Escape') {
        this.slashMenuOpen.set(false);
      }
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey && this.settings.get().enterToSend) {
      e.preventDefault(); this.send();
    }
  }

  newChat() {
    this.addChatTab();
    this.reload();
  }

  // T08 — 繼續上次對話（--continue 等效：取最新 session 並 resume）
  continueLastSession() {
    const sessions = this.sessions();
    if (!sessions.length) return;
    this.loadSession(sessions[0]);
  }

  // T09 — claude doctor
  doctorOutput = signal<string | null>(null);
  doctorRunning = signal(false);

  runDoctor() {
    this.doctorRunning.set(true);
    this.doctorOutput.set('執行中…');
    this.claude.runCliCommand(['doctor']).subscribe({
      next:  out => { this.doctorOutput.set(out); this.doctorRunning.set(false); },
      error: err => { this.doctorOutput.set(String(err)); this.doctorRunning.set(false); },
    });
  }

  runClaudeUpdate() {
    this.messages.update(m => [...m, { role: 'system', text: '正在檢查 Claude Code 更新…' }]);
    this.claude.runCliCommand(['update']).subscribe({
      next:  out => this.messages.update(m => [...m, { role: 'system', text: out || '已是最新版本' }]),
      error: err => this.messages.update(m => [...m, { role: 'system', text: String(err) }]),
    });
  }

  // T10 — MCP 管理
  mcpList    = signal<string>('');
  mcpLoading = signal(false);
  mcpNewName = '';
  mcpNewCmd  = '';

  loadMcp() {
    this.mcpLoading.set(true);
    this.claude.runCliCommand(['mcp', 'list']).subscribe({
      next:  out => {
        this.mcpList.set(out || '（無已安裝的 MCP）');
        this.parseMcpList(out || '');
        this.mcpLoading.set(false);
      },
      error: ()  => {
        this.mcpList.set('[無法取得清單]');
        this.mcpLoading.set(false);
      },
    });
  }

  isSkillAuthorized(id: string): boolean {
    return this.authorizedSkills().includes(id);
  }

  toggleSkillAuth(id: string, event?: Event) {
    if (event) event.stopPropagation();
    const current = this.authorizedSkills();
    let updated: string[];
    if (current.includes(id)) {
      updated = current.filter(x => x !== id);
    } else {
      updated = [...current, id];
    }
    this.authorizedSkills.set(updated);
    localStorage.setItem('claude_desktop_auth_skills', JSON.stringify(updated));
  }

  isMcpAuthorized(name: string): boolean {
    return this.authorizedMcps().includes(name);
  }

  toggleMcpAuth(name: string, event?: Event) {
    if (event) event.stopPropagation();
    const current = this.authorizedMcps();
    let updated: string[];
    if (current.includes(name)) {
      updated = current.filter(x => x !== name);
    } else {
      updated = [...current, name];
    }
    this.authorizedMcps.set(updated);
    localStorage.setItem('claude_desktop_auth_mcps', JSON.stringify(updated));
    this.parseMcpList(this.mcpList());
  }

  // 永久綁定：agent → skills
  getPermSkills(agentId: string): string[] {
    return this.agentSkillsMap()[agentId.replace(/^@/, '')] ?? [];
  }
  isSkillPermForAgent(agentId: string, skillId: string): boolean {
    return this.getPermSkills(agentId).includes(skillId);
  }
  toggleSkillPermForAgent(agentId: string, skillId: string) {
    const id = agentId.replace(/^@/, '');
    this.agentSkillsMap.update(m => {
      const cur = m[id] ?? [];
      return { ...m, [id]: cur.includes(skillId) ? cur.filter(s => s !== skillId) : [...cur, skillId] };
    });
    this.saveAgentSkillsMap();
  }

  // 永久綁定：agent → MCPs（直連）
  getPermMcps(agentId: string): string[] {
    return this.agentMcpsMap()[agentId.replace(/^@/, '')] ?? [];
  }
  isMcpPermForAgent(agentId: string, mcpName: string): boolean {
    return this.getPermMcps(agentId).includes(mcpName);
  }
  toggleMcpPermForAgent(agentId: string, mcpName: string) {
    const id = agentId.replace(/^@/, '');
    this.agentMcpsMap.update(m => {
      const cur = m[id] ?? [];
      return { ...m, [id]: cur.includes(mcpName) ? cur.filter(s => s !== mcpName) : [...cur, mcpName] };
    });
    this.saveAgentMcpsMap();
  }

  // 一次性：綁定到目前 tab
  private activeTabField<K extends 'sessionSkills' | 'sessionMcps'>(key: K): string[] {
    return this.chatTabs().find(t => t.id === this.activeChatId())?.[key] ?? [];
  }
  isSkillInTab(skillId: string): boolean { return this.activeTabField('sessionSkills').includes(skillId); }
  isMcpInTab(mcpName: string): boolean   { return this.activeTabField('sessionMcps').includes(mcpName); }

  toggleSkillInTab(skillId: string) {
    const tabId = this.activeChatId();
    this.chatTabs.update(tabs => tabs.map(t => {
      if (t.id !== tabId) return t;
      const cur = t.sessionSkills;
      return { ...t, sessionSkills: cur.includes(skillId) ? cur.filter(s => s !== skillId) : [...cur, skillId] };
    }));
  }
  toggleMcpInTab(mcpName: string) {
    const tabId = this.activeChatId();
    this.chatTabs.update(tabs => tabs.map(t => {
      if (t.id !== tabId) return t;
      const cur = t.sessionMcps;
      return { ...t, sessionMcps: cur.includes(mcpName) ? cur.filter(m => m !== mcpName) : [...cur, mcpName] };
    }));
  }

  // 取得所有有效技能（永久 + 當前 tab 一次性）
  getLinkedSkills(agentId: string): string[] {
    const perm    = this.getPermSkills(agentId);
    const session = this.activeTabField('sessionSkills');
    return [...new Set([...perm, ...session])];
  }

  getUsedMcps(skillId: string): string[] {
    const cleanId = skillId.replace(/^\//, '');
    return this.SKILL_MCPS_MAP[cleanId] || [];
  }

  jumpToSkillDetail(skillId: string) {
    this.activeTab.set('skills');
    this.expandedSkillId.set(skillId);
    this.expandedTranslation.set(null);
  }

  jumpToMcpDetail(mcpName: string) {
    const m = this.mcpServers().find(x => x.name === mcpName);
    const mcpId = m ? m.id : mcpName.toLowerCase().replace(/\s+/g, '-');
    this.activeTab.set('mcp');
    this.expandedMcpId.set(mcpId);
    this.expandedTranslation.set(null);
  }

  parseMcpList(out: string) {
    if (!out) {
      this.mcpServers.set([]);
      return;
    }
    const lines = out.split('\n');
    const servers: McpServer[] = [];
    
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('Checking MCP server')) continue;
      
      const colonIdx = trimmed.indexOf(':');
      if (colonIdx === -1) continue;
      
      const name = trimmed.substring(0, colonIdx).trim();
      const rest = trimmed.substring(colonIdx + 1).trim();
      
      const dashIdx = rest.lastIndexOf(' - ');
      let url = rest;
      let status = '';
      if (dashIdx !== -1) {
        url = rest.substring(0, dashIdx).trim();
        status = rest.substring(dashIdx + 3).trim();
      }
      
      const id = name.toLowerCase().replace(/\s+/g, '-');
      const authorized = this.isMcpAuthorized(name);
      const description = this.MCP_DESCRIPTIONS[name] || `Model Context Protocol server for ${name} located at ${url}.`;
      const tools = this.MCP_TOOLS_MAP[name] || [];
      
      servers.push({
        id,
        name,
        url,
        status,
        authorized,
        description,
        dockerized: false,
        tools
      });
    }

    // Append custom self-built Docker MCP servers for demonstration
    if (!servers.some(s => s.name.includes('Docker MySQL Sync'))) {
      servers.push({
        id: 'docker-mysql-sync',
        name: 'Docker MySQL Sync (Custom)',
        url: 'docker://mysql-sync-agent:latest/mcp',
        status: 'Connected',
        authorized: true,
        description: 'Custom dockerized database sync MCP server, runs inside an isolated container.',
        dockerized: true,
        dockerImage: 'mysql-sync-agent:latest',
        tools: this.MCP_TOOLS_MAP['Docker MySQL Sync (Custom)']
      });
    }
    if (!servers.some(s => s.name.includes('N8N Automation'))) {
      servers.push({
        id: 'n8n-automation',
        name: 'N8N Automation (Custom)',
        url: 'docker://n8nio/n8n:latest/webhook',
        status: 'Connected',
        authorized: true,
        description: 'Custom N8N nodes workflow execution trigger. Communicates with visual automated flow charts.',
        dockerized: true,
        dockerImage: 'n8nio/n8n:latest',
        tools: this.MCP_TOOLS_MAP['N8N Automation (Custom)']
      });
    }

    this.mcpServers.set(servers);
  }

  removeMcp(name: string) {
    if (!confirm(`確定移除 MCP "${name}"？`)) return;
    this.claude.runCliCommand(['mcp', 'remove', name]).subscribe(() => this.loadMcp());
  }

  loadSession(s: Session) {
    this.saveCurrentTab();
    this.messages.set([{ role: 'assistant', text: `Session resumed: "${s.title}"` }]);
    this.claude.resumeSession(s.id).subscribe();
    // 更新 tab 標籤
    const id = this.activeChatId();
    this.chatTabs.update(tabs => tabs.map(t => t.id === id ? { ...t, label: s.title.slice(0, 20) } : t));
  }

  deleteSession(s: Session, event: Event) {
    event.stopPropagation();
    this.claude.deleteSession(s.id).subscribe(() =>
      this.claude.getSessions(this.sessionSearch, 0).subscribe(r => this.sessions.set(r.items))
    );
  }

  selectAgent(id: string) {
    const newAgent = this.selectedAgent() === id ? '' : id;
    this.selectedAgent.set(newAgent);
    const activeId = this.activeChatId();
    if (activeId) {
      this.chatTabs.update(tabs => tabs.map(t => t.id === activeId ? { ...t, selectedAgent: newAgent } : t));
    }
    if (newAgent) {
      this.expandedAgentId.set(id);
    } else {
      this.expandedAgentId.set('');
    }
    this.expandedTranslation.set(null);
  }

  toggleAgentExpand(id: string, event?: Event) {
    if (event) event.stopPropagation();
    this.expandedAgentId.update(current => current === id ? '' : id);
    this.expandedTranslation.set(null);
  }

  toggleSkillExpand(id: string, event?: Event) {
    if (event) event.stopPropagation();
    this.expandedSkillId.update(current => current === id ? '' : id);
    this.expandedTranslation.set(null);
  }

  toggleMcpExpand(id: string, event?: Event) {
    if (event) event.stopPropagation();
    this.expandedMcpId.update(current => current === id ? '' : id);
    this.expandedTranslation.set(null);
  }

  translateExpanded(text: string) {
    if (this.expandedTranslation() !== null) {
      this.expandedTranslation.set(null);
      return;
    }
    this.expandedTranslation.set('');
    this.claude.translate(text).subscribe({
      next: r => this.expandedTranslation.set(r),
      error: () => this.expandedTranslation.set('[翻譯失敗，請重試]'),
    });
  }

  changeTabAgent(tabId: string, agentId: string) {
    this.chatTabs.update(tabs => tabs.map(t => t.id === tabId ? { ...t, selectedAgent: agentId } : t));
    if (tabId === this.activeChatId()) {
      this.selectedAgent.set(agentId);
    }
  }
  isSkillLinkedToActiveAgent(skillId: string): boolean {
    const agentId = this.selectedAgent();
    if (!agentId) return false;
    return this.getLinkedSkills(agentId).includes(skillId);
  }

  isMcpLinkedToActiveAgent(mcpName: string): boolean {
    const agentId = this.selectedAgent();
    if (!agentId) return false;
    // 透過 skill 連結
    for (const skillId of this.getLinkedSkills(agentId)) {
      if (this.getUsedMcps(skillId).includes(mcpName)) return true;
    }
    // 直連：永久 or 一次性
    return this.isMcpPermForAgent(agentId, mcpName) || this.isMcpInTab(mcpName);
  }


  get selectedMemoryContent(): string {
    return this.memory()[this.selectedMemoryKey()] ?? '';
  }
}
