import {
  Component, OnInit, OnDestroy, signal, computed,
  ViewChild, ElementRef, AfterViewChecked, HostListener
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { MarkdownPipe } from './markdown.pipe';
import { DiagnosticsPanelComponent } from './components/diagnostics-panel/diagnostics-panel';
import { AgencyImportPanelComponent } from './components/agency-import-panel/agency-import-panel';
import { TelegramSettingsComponent } from './components/telegram-settings/telegram-settings';
import { MemoryEditorComponent } from './components/memory-editor/memory-editor';
import { ProviderSettingsComponent } from './components/provider-settings/provider-settings';
import { SttSettingsComponent } from './components/stt-settings/stt-settings';
import { QuickPromptsEditComponent } from './components/quick-prompts-edit/quick-prompts-edit';
import { GeneralSettingsComponent } from './components/general-settings/general-settings';
import { EngineSettingsComponent } from './components/engine-settings/engine-settings';
import { SchedulePanelComponent } from './components/schedule-panel/schedule-panel';
import { TeamPanelComponent } from './components/team-panel/team-panel';
import { SkillPanelComponent } from './components/skill-panel/skill-panel';
import { AgentPanelComponent } from './components/agent-panel/agent-panel';
import { SoulPanelComponent } from './components/soul-panel/soul-panel';
import { McpPanelComponent } from './components/mcp-panel/mcp-panel';
import { SettingsService, AppSettings } from './settings.service';
import {
  ClaudeService, Agent, Skill, Team, TeamMember, TeamRun, TeamRunStep, Session, ChatMessage, ChatTab, FileItem, SoulProfile, Profile, McpServerDef, McpServer, McpTool, McpType, EngineAvailability, ResourceSyncStatus, CodexUsage
} from './claude.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule, DatePipe, DecimalPipe, MarkdownPipe, DiagnosticsPanelComponent, AgencyImportPanelComponent, TelegramSettingsComponent, MemoryEditorComponent, ProviderSettingsComponent, SttSettingsComponent, QuickPromptsEditComponent, GeneralSettingsComponent, EngineSettingsComponent, SchedulePanelComponent, TeamPanelComponent, SkillPanelComponent, AgentPanelComponent, SoulPanelComponent, McpPanelComponent],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App implements OnInit, OnDestroy, AfterViewChecked {
  @ViewChild('chatEnd') chatEnd!: ElementRef;
  @ViewChild('inputRef') inputRef!: ElementRef;
  @ViewChild('scrollArea') scrollArea!: ElementRef;

  // isElectron: extracted into components/general-settings (Phase 2) —
  // that component computes its own copy (pure environment check).

  agents = signal<Agent[]>([]);
  dropdownAgents = computed(() => {
    const list = this.agents();
    const orchestrator = list.find(x => x.id === 'orchestrator');
    const others = list.filter(x => x.id !== 'orchestrator');
    if (orchestrator) {
      const mainAgent = { ...orchestrator, name: '總代理人' };
      return [mainAgent, ...others];
    }
    return list;
  });
  // MCP Live Debugger state (mcpRpcName/mcpRpcMethod/mcpRpcParamsText/
  // mcpRpcResult/isMcpRpcSending/mcpPendingAuth/sendMcpRpcDebug/
  // authorizeMcpRpc): extracted into components/mcp-panel (Phase 2) —
  // self-contained, no cross-tab reads/writes found.
  activeRunId = '';

  // Team Run Artifacts Tracer
  runArtifacts = signal<any[]>([]);

  skills = signal<Skill[]>([]);
  resourceSyncStatus = signal<ResourceSyncStatus | null>(null);
  resourceSyncPending = computed(() => {
    const status = this.resourceSyncStatus();
    if (!status) return 0;
    const codex = status.agents.missing_in_codex.length + status.agents.outdated.length
      + status.skills.missing_in_codex.length + status.skills.outdated.length;
    const mirror = status.claude_mirror;
    const claude = mirror
      ? mirror.agents.missing_in_claude.length + mirror.agents.outdated.length
        + mirror.skills.missing_in_claude.length + mirror.skills.outdated.length
      : 0;
    return codex + claude;
  });
  resourceSyncConflicts = computed(() => {
    const status = this.resourceSyncStatus();
    if (!status) return 0;
    const codex = status.agents.conflicts.length + status.skills.conflicts.length;
    const mirror = status.claude_mirror;
    return codex + (mirror ? mirror.agents.conflicts.length + mirror.skills.conflicts.length : 0);
  });
  // 衝突/待同步的實際名稱（不只是數字），讓使用者知道具體是哪個 Agent/Skill 卡住
  resourceSyncConflictNames = computed(() => {
    const status = this.resourceSyncStatus();
    if (!status) return [];
    return [
      ...status.agents.conflicts.map(name => ({ name, kind: 'agent' as const, target: 'Codex' as const })),
      ...status.skills.conflicts.map(name => ({ name, kind: 'skill' as const, target: 'Codex' as const })),
      ...(status.claude_mirror?.agents.conflicts ?? []).map(name => ({ name, kind: 'agent' as const, target: 'Claude' as const })),
      ...(status.claude_mirror?.skills.conflicts ?? []).map(name => ({ name, kind: 'skill' as const, target: 'Claude' as const })),
    ];
  });
  resourceSyncPendingNames = computed(() => {
    const status = this.resourceSyncStatus();
    if (!status) return [];
    return [
      ...status.agents.missing_in_codex.map(name => ({ name, kind: 'agent' as const, target: 'Codex' as const })),
      ...status.agents.outdated.map(name => ({ name, kind: 'agent' as const, target: 'Codex' as const })),
      ...status.skills.missing_in_codex.map(name => ({ name, kind: 'skill' as const, target: 'Codex' as const })),
      ...status.skills.outdated.map(name => ({ name, kind: 'skill' as const, target: 'Codex' as const })),
      ...(status.claude_mirror?.agents.missing_in_claude ?? []).map(name => ({ name, kind: 'agent' as const, target: 'Claude' as const })),
      ...(status.claude_mirror?.agents.outdated ?? []).map(name => ({ name, kind: 'agent' as const, target: 'Claude' as const })),
      ...(status.claude_mirror?.skills.missing_in_claude ?? []).map(name => ({ name, kind: 'skill' as const, target: 'Claude' as const })),
      ...(status.claude_mirror?.skills.outdated ?? []).map(name => ({ name, kind: 'skill' as const, target: 'Claude' as const })),
    ];
  });
  // 引擎原生已有、但 registry 尚未採納；背景 reconcile 會處理無歧義項目。
  resourceImportableNames = computed(() => {
    const status = this.resourceSyncStatus();
    if (!status) return [];
    const mirror = status.claude_mirror;
    return [
      ...status.agents.codex_only.map(name => ({ name, kind: 'agent' as const, from: 'codex' as const })),
      ...status.skills.codex_only.map(name => ({ name, kind: 'skill' as const, from: 'codex' as const })),
      ...(mirror?.agents.claude_only ?? []).map(name => ({ name, kind: 'agent' as const, from: 'claude' as const })),
      ...(mirror?.skills.claude_only ?? []).map(name => ({ name, kind: 'skill' as const, from: 'claude' as const })),
    ];
  });
  resourceSyncDetailsExpanded = signal(false);
  sessions = signal<Session[]>([]);
  memory = signal<Record<string, string>>({});
  // schedules signal: extracted into components/schedule-panel (Phase 2)
  // memoryOverview / memViewExpanded / memEditMode / memEditContent:
  // extracted into components/memory-editor (Phase 2)
  // expandedTeams: extracted into components/team-panel (Phase 2)

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

  // Precomputed for components/skill-panel (Phase 2): avoids passing
  // isSkillLinkedToActiveAgent/isSkillInActiveAgentFrontmatter/isSkillInTab
  // as function @Inputs — the component checks membership instead.
  linkedSkillIds = computed(() => new Set(this.selectedAgent() ? this.getLinkedSkills(this.selectedAgent()) : []));
  frontmatterSkillIds = computed(() => {
    const agentId = this.selectedAgent();
    if (!agentId) return new Set<string>();
    const agent = this.agents().find(a => a.id === agentId.replace(/^@/, ''));
    return new Set(agent?.skills ?? []);
  });
  tabSkillIds = computed(() => new Set(this.activeTabField('sessionSkills')));

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
  agentMcpsMap = signal<Record<string, string[]>>({}); // agent 直連 MCP，不透過 skill

  // MCP panel split (top section height %, clamped 15–80)
  mcpSplitPct = signal<number>(
    Number(localStorage.getItem('claude_mcp_split_pct') || '45')
  );
  private _mcpDragActive = false;
  private _mcpDragStartY = 0;
  private _mcpDragStartPct = 45;

  onMcpDividerDown(e: MouseEvent) {
    e.preventDefault();
    this._mcpDragActive = true;
    this._mcpDragStartY = e.clientY;
    this._mcpDragStartPct = this.mcpSplitPct();

    const onMove = (mv: MouseEvent) => {
      if (!this._mcpDragActive) return;
      const container = document.querySelector('.mcp-view') as HTMLElement;
      if (!container) return;
      const totalH = container.clientHeight;
      const delta = mv.clientY - this._mcpDragStartY;
      const newPct = Math.max(15, Math.min(80, this._mcpDragStartPct + (delta / totalH) * 100));
      this.mcpSplitPct.set(Math.round(newPct));
    };

    const onUp = () => {
      this._mcpDragActive = false;
      localStorage.setItem('claude_mcp_split_pct', String(this.mcpSplitPct()));
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
    };

    document.body.style.cursor = 'ns-resize';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  // localMcpConfigs / localDockerConfig / editingDockerMcp / openDockerConfig /
  // saveDockerConfig / loadLocalMcpConfigs: extracted into
  // components/mcp-panel (Phase 2) — only read/written within the MCP tab,
  // so the component now owns this slice entirely (loads its own copy via
  // ClaudeService on init instead of receiving it as an @Input).

  // Manual override: names force-promoted to local section
  managedMcpNames = signal<string[]>([]);

  isMcpLocal(m: McpServer): boolean {
    if (this.managedMcpNames().includes(m.name)) return true;
    const t = m.mcpType;
    if (t === 'docker' || t === 'stdio' || t === 'local-http') return true;
    // Fallback: detect from URL if mcpType wasn't set
    const url = (m.url || '').toLowerCase();
    return url.startsWith('docker://')
      || url.includes('localhost')
      || url.includes('127.0.0.1')
      || url.startsWith('stdio://')
      || m.dockerized === true;
  }

  externalMcpServers = computed(() => this.sortedMcpServers().filter(m => !this.isMcpLocal(m)));
  localMcpServers = computed(() => this.sortedMcpServers().filter(m => this.isMcpLocal(m)));

  // components/mcp-panel (Phase 2) needs per-card O(1) membership checks in
  // its @for loops — same reasoning as skill-panel's precomputed Sets:
  // passing isMcpRequiredByActiveAgent/isMcpLinkedToActiveAgent/isMcpInTab
  // as function @Inputs would re-run their full lookup on every card, every
  // change-detection pass.
  requiredMcpNames = computed(() => {
    const agentId = this.selectedAgent();
    if (!agentId) return new Set<string>();
    const agent = this.agents().find(a => a.id === agentId.replace(/^@/, ''));
    return new Set(agent?.mcp ?? []);
  });
  linkedMcpNames = computed(() => {
    const all = [...this.externalMcpServers(), ...this.localMcpServers()];
    return new Set(all.filter(m => this.isMcpLinkedToActiveAgent(m.name)).map(m => m.name));
  });
  sessionMcpNames = computed(() => new Set(this.activeTabField('sessionMcps')));
  // Manual "force this into 本地 API" override — isMcpLocal() checks
  // membership before falling back to mcpType/URL auto-detection, for
  // servers that auto-detection misses (e.g. an actually-local server
  // whose URL doesn't match the docker://\/localhost\/127.0.0.1 patterns).
  // Precomputed Set for the same per-card O(1)-lookup reason as the three
  // above; toggleManagedMcp() below stays a plain method since it mutates
  // rather than reads.
  managedMcpNameSet = computed(() => new Set(this.managedMcpNames()));

  toggleManagedMcp(name: string) {
    this.managedMcpNames.update(arr =>
      arr.includes(name) ? arr.filter(n => n !== name) : [...arr, name]
    );
    localStorage.setItem('claude_desktop_managed_mcps', JSON.stringify(this.managedMcpNames()));
  }

  isMcpRunning(status: string) { return status?.toLowerCase().includes('connected'); }

  // getMcpLampClass / getMcpLampTitle: extracted into components/mcp-panel
  // (Phase 2) as local pure methods (same 4-state logic, "inUse" now comes
  // from the precomputed linkedMcpNames @Input instead of calling back into
  // App per card).

  startMcp(name: string) { this.claude.startMcp(name).subscribe({ error: (e) => this.showToast(`MCP 啟動失敗: ${e.message ?? e}`, 'error') }); }
  stopMcp(name: string) { this.claude.stopMcp(name).subscribe({ error: (e) => this.showToast(`MCP 停止失敗: ${e.message ?? e}`, 'error') }); }
  restartMcp(name: string) { this.claude.restartMcp(name).subscribe({ error: (e) => this.showToast(`MCP 重啟失敗: ${e.message ?? e}`, 'error') }); }

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
  messages = signal<ChatMessage[]>([]);
  inputText = '';
  isStreaming = signal(false);
  isRecording = signal(false);
  isTranscribing = signal(false);
  private mediaRecorder: MediaRecorder | null = null;
  private mediaStream: MediaStream | null = null;
  private audioChunks: Blob[] = [];
  // 靜音自動停止錄音：用「這次錄音目前為止的音量峰值」當基準（不是固定
  // 閾值——每個人講話音量、麥克風靈敏度都不一樣，固定閾值容易對某些人
  // 太敏感、對某些人完全沒反應）。要先偵測到「有講話」才會開始算靜音
  // 時間，不然開口前的安靜狀態會被誤判成靜音直接停止。
  private audioContext: AudioContext | null = null;
  private audioAnalyser: AnalyserNode | null = null;
  private volumeMonitorTimer: any = null;
  private recordingPeakVolume = 0;
  private speechDetectedInRecording = false;
  private silenceStartedAt: number | null = null;
  private static readonly SILENCE_RATIO = 0.3;
  private static readonly SILENCE_DURATION_MS = 2000;
  private static readonly SPEECH_FLOOR = 0.02;
  readonly speechOutputSupported =
    typeof window !== 'undefined'
    && 'speechSynthesis' in window
    && 'SpeechSynthesisUtterance' in window;
  speakingMessageIndex = signal<number | null>(null);
  private currentUtterance: SpeechSynthesisUtterance | null = null;
  selectedAgent = signal('');
  activeTab = signal<'agents' | 'teams' | 'skills' | 'memory' | 'schedules' | 'soul' | 'mcp' | 'memview'>('teams');
  sessionSearch = '';

  // Schedule form + schedules signal: extracted into
  // components/schedule-panel (Phase 2)

  // Token usage + cost
  tokenUsage = signal<{ input: number; output: number; cost: number } | null>(null);
  readonly Math = Math;

  // Claude Code 用量
  usage = signal<{ fiveHour: number; fiveHourReset: string | number | null; sevenDay: number; sevenDayReset: string | number | null } | null>(null);
  codexUsage = signal<CodexUsage | null>(null);
  private usageTimer: any = null;

  codexWindowLabel(minutes: number | null | undefined): string {
    if (!minutes) return '限制';
    if (minutes % 10080 === 0) return `${minutes / 10080}w`;
    if (minutes % 1440 === 0) return `${minutes / 1440}d`;
    if (minutes % 60 === 0) return `${minutes / 60}h`;
    return `${minutes}m`;
  }

  codexResetMillis(seconds: number | null | undefined): number | null {
    return seconds ? seconds * 1000 : null;
  }

  claudeResetMillis(value: string | number | null | undefined): number | null {
    if (value === null || value === undefined || value === '') return null;
    if (typeof value === 'number') {
      return value > 10_000_000_000 ? value : value * 1000;
    }
    const trimmed = value.trim();
    if (!trimmed) return null;
    const numeric = Number(trimmed);
    if (Number.isFinite(numeric)) {
      return numeric > 10_000_000_000 ? numeric : numeric * 1000;
    }
    const parsed = Date.parse(trimmed);
    return Number.isNaN(parsed) ? null : parsed;
  }

  // Attachments
  attachedFiles = signal<{ name: string; path: string; preview?: string }[]>([]);
  isUploading = signal(false);

  // Soul / Persona
  soulContent = '';
  soulSaved = signal(true);
  private soulTimer: any = null;

  // Multi-soul state
  souls = signal<SoulProfile[]>([]);
  selectedSoulId = signal<string>('');
  soulDraftSaved = signal(true);
  newSoulName = '';
  // renamingSoulId / renameSoulInput / soulDraft (the draft text itself,
  // as opposed to soulDraftSaved which stays here): extracted into
  // components/soul-panel (Phase 2) — purely local UI state, nothing
  // outside the soul tab reads/writes it. soulDraft specifically had to
  // move after an e2e test caught a stale-echo race when round-tripped
  // through @Input/@Output on every keystroke.
  agentEditorSoulContent = '';

  // Resizing signals & state
  sidebarWidth = signal(300);
  rightWidth = signal(300);
  inputHeight = signal(140);
  soulSplitRatio = signal(0.5);   // 0 = all upper, 1 = all lower

  private _resizing = false;
  private _startX = 0;
  private _startW = 0;

  private _rightResizing = false;
  private _startXRight = 0;
  private _startWRight = 0;

  private _inputResizing = false;
  private _startYInput = 0;
  private _startHInput = 0;

  private _soulResizing = false;
  private _soulStartY = 0;
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

  onSoulDividerMousedown(e: MouseEvent, panelHeight: number) {
    this._soulResizing = true;
    this._soulStartY = e.clientY;
    this._soulStartRatio = this.soulSplitRatio();
    this._soulPanelHeight = panelHeight;
    e.preventDefault();
  }

  @HostListener('document:mousemove', ['$event'])
  onMouseMove(e: MouseEvent) {
    if (this._resizing) {
      this.sidebarWidth.set(Math.max(200, Math.min(560, this._startW + (e.clientX - this._startX))));
    } else if (this._rightResizing) {
      this.rightWidth.set(Math.max(280, Math.min(700, this._startWRight - (e.clientX - this._startXRight))));
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
    this._soulResizing = false;
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
  private _resourceSyncTimer: any;

  // Session pagination
  sessionOffset = 0;
  hasMoreSessions = signal(false);

  // Debug mode
  debugMode = signal(false);

  // ── T11 多 Tab 對話 ────────────────────────────────────
  chatTabs = signal<ChatTab[]>([]);
  activeChatId = signal('');

  get activeChat(): ChatTab | undefined {
    return this.chatTabs().find(t => t.id === this.activeChatId());
  }

  private makeTab(label = '新對話', projectDir?: string, teamId?: string): ChatTab {
    return {
      id: `tab-${Date.now()}`,
      clientId: `client-${Date.now()}`,
      label,
      messages: [],
      tokenUsage: null,
      selectedAgent: '',
      isStreaming: false,
      sessionSkills: [],
      sessionMcps: [],
      projectDir: projectDir ?? this.settings.get().workDir,
      teamId,
      draft: '',
    };
  }

  private saveCurrentTab() {
    const id = this.activeChatId();
    if (!id) return;
    this.chatTabs.update(tabs => tabs.map(t => t.id === id
      ? { ...t, messages: this.messages(), tokenUsage: this.tokenUsage(), selectedAgent: this.selectedAgent(), isStreaming: this.isStreaming(), draft: this.inputText }
      : t));
  }

  switchChatTab(tabId: string) {
    if (tabId === this.activeChatId()) return;
    if (this.isRecording()) {
      this.toggleMic();
    }
    this.saveCurrentTab();
    const tab = this.chatTabs().find(t => t.id === tabId);
    if (!tab) return;
    this.messages.set([...tab.messages]);
    this.tokenUsage.set(tab.tokenUsage);
    this.selectedAgent.set(tab.selectedAgent);
    this.isStreaming.set(tab.isStreaming);
    this.claude.clientId = tab.clientId;
    this.inputText = tab.draft || '';

    setTimeout(() => {
      if (this.inputRef?.nativeElement) {
        const el = this.inputRef.nativeElement;
        el.style.height = 'auto';
        el.style.height = el.scrollHeight + 'px';
      }
    }, 50);

    this.activeChatId.set(tabId);
    this.checkQuotaInMessages(tab.messages);
    // 切換分頁後預設捲到最新訊息，不然新分頁的畫面會停在瀏覽器預設的捲動
    // 位置（通常是最上面），使用者切過去看到的是最舊的訊息而不是最新進度。
    // 不用 shouldScroll + ngAfterViewChecked（這個 app 沒裝 zone.js，這個
    // 機制只有在由 (click)/(keydown) 這類 DOM 事件同步觸發時才可靠——這個
    // function 目前雖然都是從 click handler 呼叫，但直接呼叫
    // scrollIntoView 更穩，不會因為未來新增一個非同步呼叫路徑而悄悄失效）。
    setTimeout(() => this.chatEnd?.nativeElement?.scrollIntoView({ behavior: 'smooth' }), 0);
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
    if (this.isRecording()) {
      this.toggleMic();
    }
    this.saveCurrentTab();
    const tab = this.makeTab();
    this.chatTabs.update(t => [...t, tab]);
    this.messages.set([]);
    this.tokenUsage.set(null);
    this.selectedAgent.set(this.settings.get().defaultAgent || '');
    this.isStreaming.set(false);
    this.claude.clientId = tab.clientId;
    this.inputText = '';
    this.activeChatId.set(tab.id);
  }

  // Tab 關閉確認 modal state
  tabCloseConfirmId = signal<string | null>(null);
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
    const tabs = this.chatTabs();
    const idx = tabs.findIndex(t => t.id === tabId);
    if (idx === -1) return;
    const tabToClose = tabs[idx];
    if (tabToClose && tabToClose.clientId) {
      this.claude.stopChat(tabToClose.clientId).subscribe();
    }
    this.tabStopFns.delete(tabId);
    const isActive = tabId === this.activeChatId();
    const next = tabs[idx > 0 ? idx - 1 : 1];
    if (isActive && this.isRecording()) {
      this.toggleMic();
    }
    this.chatTabs.update(t => t.filter(x => x.id !== tabId));
    if (isActive && next) this.switchChatTab(next.id);
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
  fileTree = signal<{ path: string; parent: string; items: FileItem[] } | null>(null);
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
  cmdOpen = signal(false);
  cmdQ = signal('');
  cmdIdx = signal(0);
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
    if (item.type === 'cmd') { this.executeBuiltinCmd(item.id); }
    else if (item.type === 'session') { const s = this.sessions().find(x => x.id === item.id); if (s) this.loadSession(s); }
    else if (item.type === 'agent') { this.selectAgent(item.id); }
    else if (item.type === 'skill') { this.inputText = item.label + ' '; this.inputRef?.nativeElement?.focus(); }
  }

  // T01 — model / effort / permissionMode（對應 Claude CLI 參數）
  readonly MODEL_OPTIONS = ['sonnet', 'opus', 'haiku', 'fable'] as const;
  readonly EFFORT_OPTIONS = ['low', 'medium', 'high', 'xhigh', 'max'] as const;
  readonly PERM_OPTIONS = ['acceptEdits', 'default', 'plan', 'bypassPermissions', 'auto'] as const;
  readonly PERM_LABELS: Record<string, string> = {
    acceptEdits: 'Accept edits', default: 'Default',
    plan: 'Plan', bypassPermissions: 'Bypass', auto: 'Auto',
  };
  readonly MODEL_LABELS: Record<string, string> = {
    sonnet: 'Sonnet 4.6', opus: 'Opus 4.8', haiku: 'Haiku 4.5', fable: 'Fable 5',
  };
  model = signal('sonnet');
  effort = signal<'low' | 'medium' | 'high' | 'xhigh' | 'max'>('medium');
  permissionMode = signal<'default' | 'acceptEdits' | 'bypassPermissions' | 'plan' | 'auto'>('acceptEdits');
  bannerDismissed = signal(false);
  outOfQuota = signal(false);
  usageOpen  = signal(false);
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
    if (this.isRecording()) {
      this.stopAudioRecording();
      return;
    }

    this.startAudioRecording();
  }

  async startAudioRecording() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      this.showToast('此環境不支援麥克風錄音；無法使用語音輸入', 'error');
      return;
    }

    try {
      this.audioChunks = [];
      // 這裡不是通話應用，沒有喇叭播放需要回音消除的「遠端聲音」可以參照；
      // 瀏覽器預設開啟的 echoCancellation/noiseSuppression 在沒有遠端參考訊號
      // 時常會誤判、把麥克風收到的人聲一併壓低，錄出來的音量明顯變小。
      // 關掉這兩項、只留 autoGainControl 補償收音較小聲的麥克風。
      this.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: true,
        },
      });
      const mimeType = this.pickAudioMimeType();
      this.mediaRecorder = new MediaRecorder(
        this.mediaStream,
        mimeType ? { mimeType } : undefined,
      );
      this.mediaRecorder.ondataavailable = event => {
        if (event.data.size > 0) this.audioChunks.push(event.data);
      };
      this.mediaRecorder.onerror = event => {
        console.error('Audio recording error:', event);
        this.showToast('語音錄製失敗', 'error');
        this.cleanupAudioRecording();
      };
      this.mediaRecorder.onstop = () => {
        const type = this.mediaRecorder?.mimeType || mimeType || 'audio/webm';
        const audio = new Blob(this.audioChunks, { type });
        this.cleanupAudioRecording();
        this.transcribeRecordedAudio(audio, type);
      };
      this.mediaRecorder.start();
      this.isRecording.set(true);
      this.showToast('語音錄製中，停止後會自動轉成文字', 'success');
      this.startVolumeMonitor(this.mediaStream);
    } catch (e: any) {
      console.error('Failed to start audio recording:', e);
      const denied = e?.name === 'NotAllowedError' || e?.name === 'SecurityError';
      this.showToast(denied ? '麥克風權限被拒絕' : '啟動語音錄製失敗', 'error');
      this.cleanupAudioRecording();
    }
  }

  private startVolumeMonitor(stream: MediaStream) {
    this.recordingPeakVolume = 0;
    this.speechDetectedInRecording = false;
    this.silenceStartedAt = null;
    try {
      const AudioContextCtor = window.AudioContext || (window as any).webkitAudioContext;
      this.audioContext = new AudioContextCtor();
      const source = this.audioContext.createMediaStreamSource(stream);
      this.audioAnalyser = this.audioContext.createAnalyser();
      this.audioAnalyser.fftSize = 2048;
      source.connect(this.audioAnalyser);
      const data = new Uint8Array(this.audioAnalyser.frequencyBinCount);

      this.volumeMonitorTimer = setInterval(() => {
        if (!this.audioAnalyser) return;
        this.audioAnalyser.getByteTimeDomainData(data);
        let sumSquares = 0;
        for (let i = 0; i < data.length; i++) {
          const normalized = (data[i] - 128) / 128;
          sumSquares += normalized * normalized;
        }
        const rms = Math.sqrt(sumSquares / data.length);

        if (rms > this.recordingPeakVolume) this.recordingPeakVolume = rms;
        if (!this.speechDetectedInRecording && this.recordingPeakVolume > App.SPEECH_FLOOR) {
          this.speechDetectedInRecording = true;
        }
        if (!this.speechDetectedInRecording) return;

        if (rms < this.recordingPeakVolume * App.SILENCE_RATIO) {
          if (this.silenceStartedAt === null) this.silenceStartedAt = Date.now();
          else if (Date.now() - this.silenceStartedAt >= App.SILENCE_DURATION_MS) {
            this.showToast('偵測到安靜，自動停止錄音', 'info');
            this.stopAudioRecording();
          }
        } else {
          this.silenceStartedAt = null;
        }
      }, 100);
    } catch (e) {
      // 音量監控是加分功能，建立失敗（例如瀏覽器不支援 AudioContext）
      // 不該讓錄音本身也跟著失敗，安靜略過、退回純手動停止。
      console.error('Failed to start volume monitor:', e);
    }
  }

  private stopVolumeMonitor() {
    if (this.volumeMonitorTimer) {
      clearInterval(this.volumeMonitorTimer);
      this.volumeMonitorTimer = null;
    }
    this.audioAnalyser = null;
    if (this.audioContext) {
      this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }
  }

  stopAudioRecording() {
    if (!this.mediaRecorder) {
      this.cleanupAudioRecording();
      return;
    }
    try {
      if (this.mediaRecorder.state !== 'inactive') {
        this.mediaRecorder.stop();
      }
    } catch (e) {
      console.error('Failed to stop audio recording:', e);
      this.cleanupAudioRecording();
    }
  }

  private pickAudioMimeType(): string {
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/mp4',
    ];
    return candidates.find(type => MediaRecorder.isTypeSupported(type)) || '';
  }

  private cleanupAudioRecording() {
    this.isRecording.set(false);
    this.stopVolumeMonitor();
    this.mediaStream?.getTracks().forEach(track => track.stop());
    this.mediaStream = null;
    this.mediaRecorder = null;
  }

  private transcribeRecordedAudio(audio: Blob, mimeType: string) {
    if (!audio.size) {
      this.audioChunks = [];
      this.showToast('沒有錄到音訊', 'info');
      return;
    }
    const ext = mimeType.includes('mp4') ? 'm4a' : 'webm';
    const isLocal = (this.settings.get().sttMode ?? 'local') === 'local';
    this.isTranscribing.set(true);
    this.showToast(isLocal ? '語音轉文字中...（本機模型第一次使用需要先下載，可能要等一下）' : '語音轉文字中...', 'info');
    this.claude.transcribeAudio(audio, `recording.${ext}`)
      .then(result => {
        const text = result.text?.trim();
        if (!text) {
          this.showToast('沒有辨識到文字', 'info');
          return;
        }
        this.appendInputText(text);
        this.showToast('語音已轉成文字', 'success');
      })
      .catch(err => {
        console.error('Transcription failed:', err);
        const msg = String(err?.message ?? err);
        if (msg.includes('missing provider API key')) {
          this.showToast('語音輸入尚未設定 API Key，請至「設定 → AI Provider」填入（用於 Whisper 語音轉文字）', 'error', 5000);
          this.openSettings();
        } else if (msg.includes('faster-whisper') || msg.includes('本機語音模型')) {
          this.showToast(`本機語音模型無法使用：${msg}（可到「設定 → 語音輸入」切換成雲端 API）`, 'error', 6000);
          this.openSettings();
        } else {
          this.showToast(`語音轉文字失敗: ${msg}`, 'error');
        }
      })
      .finally(() => {
        this.audioChunks = [];
        this.isTranscribing.set(false);
      });
  }

  private appendInputText(text: string) {
    const current = this.inputText.trimEnd();
    this.inputText = current ? `${current} ${text}` : text;
    this.saveCurrentTab();
    setTimeout(() => {
      const el = this.inputRef?.nativeElement;
      if (!el) return;
      el.focus();
      el.style.height = 'auto';
      el.style.height = el.scrollHeight + 'px';
    }, 50);
  }

  isSpeakingMessage(index: number): boolean {
    return this.speakingMessageIndex() === index;
  }

  speakMessage(text: string, index: number) {
    if (!this.speechOutputSupported) {
      this.showToast('此環境不支援語音輸出', 'error');
      return;
    }
    if (this.isSpeakingMessage(index)) {
      this.stopSpeaking();
      return;
    }

    const content = this.textForSpeech(text);
    if (!content) {
      this.showToast('沒有可朗讀的文字', 'info');
      return;
    }

    this.stopSpeaking(false);
    const utterance = new SpeechSynthesisUtterance(content);
    utterance.lang = this.settings.get().lang === 'en' ? 'en-US' : 'zh-TW';
    utterance.rate = 1;
    utterance.onend = () => this.clearSpeechState(utterance);
    utterance.onerror = (event: SpeechSynthesisErrorEvent) => {
      this.clearSpeechState(utterance);
      if (event.error !== 'canceled' && event.error !== 'interrupted') {
        this.showToast(`語音輸出失敗: ${event.error}`, 'error');
      }
    };

    this.currentUtterance = utterance;
    this.speakingMessageIndex.set(index);
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
  }

  stopSpeaking(resetIndex = true) {
    if (this.speechOutputSupported) {
      window.speechSynthesis.cancel();
    }
    this.currentUtterance = null;
    if (resetIndex) this.speakingMessageIndex.set(null);
  }

  private clearSpeechState(utterance: SpeechSynthesisUtterance) {
    if (this.currentUtterance !== utterance) return;
    this.currentUtterance = null;
    this.speakingMessageIndex.set(null);
  }

  private textForSpeech(text: string): string {
    return text
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/[#>*_~|]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
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
  onDragLeave() { this.isDragOver.set(false); }
  async onDrop(e: DragEvent) {
    e.preventDefault(); this.isDragOver.set(false);
    const files = Array.from(e.dataTransfer?.files ?? []);
    if (!files.length) return;
    this.isUploading.set(true);
    for (const file of files) {
      try {
        const preview = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined;
        const result = await this.claude.uploadFile(file);
        this.attachedFiles.update(a => [...a, { ...result, preview }]);
      } catch (err: any) {
        this.showToast(`上傳檔案失敗: ${err.message ?? err}`, 'error');
      }
    }
    this.isUploading.set(false);
  }

  // T02 — Select folder
  // 顯示 active tab 的 projectDir（已有訊息 = 鎖定）；fallback 至 settings.workDir
  workDir = computed(() => this.activeChat?.projectDir || this.settings.get().workDir);
  workDirLabel = computed(() => {
    const d = this.workDir();
    return d ? (d.split(/[/\\]/).pop() || d) : '本機';
  });
  // active tab 是否已鎖定目錄（有訊息就算鎖定）
  isDirLocked = computed(() => (this.activeChat?.messages.length ?? 0) > 0);

  async pickFolder() {
    if (this.isDirLocked()) return; // 有訊息時禁止更換目錄
    const dir = await this.claude.pickDirectory();
    if (dir) {
      // 同步更新 active tab 的 projectDir
      const id = this.activeChatId();
      this.chatTabs.update(tabs => tabs.map(t =>
        t.id === id ? { ...t, projectDir: dir } : t
      ));
      this.settings.save({ workDir: dir });
      this.settingsForm.workDir = dir;
    }
  }

  // pickProjectDir / pickClaudeHome: extracted into
  // components/general-settings (Phase 2)

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
    { name: '戰爭與和平', tokens: 580_000 },
    { name: '傲慢與偏見', tokens: 130_000 },
    { name: '星際大戰劇本', tokens: 30_000 },
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

  // Quick prompts (read-only, for the chat-input buttons): edit UI
  // extracted into components/quick-prompts-edit (Phase 2)
  quickPrompts = computed(() => this.settings.get().quickPrompts);

  // Remaining tokens
  remainingTokens = computed(() => {
    const u = this.tokenUsage();
    if (!u) return null;
    return Math.max(0, 200000 - u.input - u.output);
  });

  // Built-in slash commands
  readonly BUILTIN_CMDS = [
    { id: '__new', name: 'new', description: '開始新對話' },
    { id: '__clear', name: 'clear', description: '清除目前訊息' },
    { id: '__undo', name: 'undo', description: '撤銷最後一次對話（移除最後一組問答）' },
    { id: '__retry', name: 'retry', description: '重試上一則訊息' },
    { id: '__compact', name: 'compact', description: '壓縮對話以節省 token' },
    { id: '__model', name: 'model', description: '切換 AI 模型' },
    { id: '__usage', name: 'usage', description: '顯示 token 用量' },
    { id: '__debug', name: 'debug', description: '切換 debug 模式' },
    { id: '__status', name: 'status', description: '顯示 Claude 狀態' },
    { id: '__review', name: 'review', description: '程式碼審查（Code Review）' },
    { id: '__plan', name: 'plan', description: '規劃實作步驟' },
    { id: '__tdd', name: 'tdd', description: '測試驅動開發流程' },
    { id: '__explain', name: 'explain', description: '解釋目前的程式碼或問題' },
    { id: '__git', name: 'git', description: '顯示 Git 狀態與最近提交' },
    { id: '__search', name: 'search', description: '搜尋對話歷史' },
    { id: '__shortcuts', name: 'shortcuts', description: '顯示所有鍵盤快捷鍵' },
  ];

  // Model picker
  readonly MODEL_PICKER_OPTIONS = [
    { id: 'opus', label: 'Opus 4.8', desc: '最強能力，適合複雜任務' },
    { id: 'sonnet', label: 'Sonnet 4.6', desc: '速度與能力的最佳平衡（預設）' },
    { id: 'haiku', label: 'Haiku 4.5', desc: '最快速，適合簡單任務' },
    { id: 'fable', label: 'Fable 5', desc: '特殊能力模型' },
  ];
  modelPickerOpen = signal(false);

  // Cron presets / translateCron: extracted into
  // components/schedule-panel (Phase 2)

  // Session pin/star
  pinnedIds = signal<string[]>([]);

  pinnedSessions = computed(() =>
    this.sessions().filter(s => this.pinnedIds().includes(s.id))
  );

  groupedSessionsWithPins = computed(() => {
    const pinned = this.pinnedIds();
    const now = Date.now() / 1000;
    const day = 86400;
    const groups: { label: string; subLabel?: string; items: any[]; pinned?: boolean }[] = [];
    const pinItems = this.sessions().filter(s => pinned.includes(s.id));
    if (pinItems.length) groups.push({ label: '📌 置頂', items: pinItems, pinned: true });
    const unpinned = this.sessions().filter(s => !pinned.includes(s.id));
    const today: any[] = [], yesterday: any[] = [], week: any[] = [], older: any[] = [];
    for (const s of unpinned) {
      const age = now - s.mtime;
      if (age < day) today.push(s);
      else if (age < 2 * day) yesterday.push(s);
      else if (age < 7 * day) week.push(s);
      else older.push(s);
    }
    if (today.length) groups.push({ label: '今天', items: today });
    if (yesterday.length) groups.push({ label: '昨天', items: yesterday });
    if (week.length) groups.push({ label: '本週', items: week });
    if (older.length) groups.push({ label: '更早', items: older });
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

  // ── Session metadata: colors + tags ─────────────────────────────────────
  sessionMeta = signal<Record<string, { tags: string[]; color: string }>>({});
  sessionGroupMode = signal<'date' | 'project'>('date');
  tagInputId = signal<string | null>(null);
  tagInputVal = '';

  getSessionMeta(id: string) { return this.sessionMeta()[id] || { tags: [], color: '' }; }

  groupedByProject = computed(() => {
    const pinned = this.pinnedIds();
    const all = this.sessions();
    // key = projectPath (full) OR projectDir (short) OR '未知專案'
    const map = new Map<string, { sessions: Session[]; latestMtime: number; folderName: string; fullPath: string }>();
    for (const s of all) {
      const fullPath = s.projectPath || '';
      // Use actual path's last segment as folder name; fall back to slug-derived projectDir
      const folderName = fullPath
        ? (fullPath.split(/[/\\]/).filter(Boolean).pop() ?? s.projectDir ?? '未知專案')
        : (s.projectDir || '未知專案');
      const key = fullPath || s.projectDir || '未知專案';
      if (!map.has(key)) map.set(key, { sessions: [], latestMtime: 0, folderName: folderName || '未知專案', fullPath });
      const entry = map.get(key)!;
      entry.sessions.push(s);
      if (s.mtime > entry.latestMtime) entry.latestMtime = s.mtime;
    }
    const groups: { label: string; subLabel?: string; items: Session[]; pinned?: boolean }[] = [];
    const pinItems = all.filter(s => pinned.includes(s.id));
    if (pinItems.length) groups.push({ label: '📌 置頂', items: pinItems, pinned: true });
    // Sort project groups by most-recent session mtime (newest first)
    const sorted = Array.from(map.entries()).sort((a, b) => b[1].latestMtime - a[1].latestMtime);
    for (const [, entry] of sorted) {
      const unpinned = entry.sessions.filter(s => !pinned.includes(s.id));
      if (unpinned.length) {
        groups.push({
          label: entry.folderName,
          subLabel: entry.fullPath || undefined,
          items: unpinned,
        });
      }
    }
    return groups;
  });

  activeGroupedSessions = computed(() =>
    this.sessionGroupMode() === 'project' ? this.groupedByProject() : this.groupedSessionsWithPins()
  );

  private loadSessionMeta() {
    try {
      const raw = localStorage.getItem('claude_session_meta');
      if (raw) this.sessionMeta.set(JSON.parse(raw));
    } catch { }
  }

  private _saveSessionMeta() {
    localStorage.setItem('claude_session_meta', JSON.stringify(this.sessionMeta()));
  }

  cycleSessionColor(id: string, e: Event) {
    e.stopPropagation();
    const colors = ['', 'red', 'orange', 'yellow', 'green', 'blue', 'purple'];
    this.sessionMeta.update(m => {
      const cur = m[id]?.color || '';
      const next = colors[(colors.indexOf(cur) + 1) % colors.length];
      return { ...m, [id]: { tags: m[id]?.tags || [], color: next } };
    });
    this._saveSessionMeta();
  }

  showTagInput(id: string, e: Event) {
    e.stopPropagation();
    this.tagInputId.set(id);
    this.tagInputVal = '';
  }

  addSessionTag(id: string, tag: string) {
    tag = tag.trim().replace(/^#/, '');
    if (!tag) { this.tagInputId.set(null); return; }
    this.sessionMeta.update(m => {
      const ex = m[id] || { tags: [], color: '' };
      if (ex.tags.includes(tag)) return m;
      return { ...m, [id]: { ...ex, tags: [...ex.tags, tag] } };
    });
    this._saveSessionMeta();
    this.tagInputVal = '';
    this.tagInputId.set(null);
  }

  removeSessionTag(id: string, tag: string, e: Event) {
    e.stopPropagation();
    this.sessionMeta.update(m => {
      const ex = m[id] || { tags: [], color: '' };
      return { ...m, [id]: { ...ex, tags: ex.tags.filter(t => t !== tag) } };
    });
    this._saveSessionMeta();
  }

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
      if (this.contextMenu()) this.closeContextMenu();
      else if (this.cmdOpen()) this.closeCmd();
      else if (this.settingsOpen()) this.closeSettings();
      else if (this.expandedAgentId() || this.expandedSkillId() || this.expandedMcpId()) {
        this.expandedAgentId.set('');
        this.expandedSkillId.set('');
        this.expandedMcpId.set('');
        this.expandedTranslation.set(null);
      }
      else if (this.renamingId()) this.renamingId.set(null);
    }
  }

  // Stop streaming
  // T38 健檢修復：原本用單一 this.stopFn 記錄「目前這一個」串流的中止函式，
  // 但 send()/submitTeamMessage()/executeTeamCodePhase() 的事件 callback 都
  // 直接寫入共用的 this.messages/this.isStreaming/this.tokenUsage，完全沒
  // 檢查「觸發這次事件的串流，是不是還對應著目前作用中的分頁」。切分頁不會
  // 中止背景中的串流，於是背景分頁後續收到的 token 會被寫進「現在正在看」
  // 的另一個分頁裡，且切換走的分頁狀態直接被凍結在切換當下那一刻，收不到
  // 後續進度。改成每個分頁各自的 stop 函式，且事件 callback 一律透過
  // tabMessages()/tabStreaming()/tabTokenUsage() 依「事件所屬的 tabId」
  // 決定要寫進 live signal（該分頁仍是作用中）還是 chatTabs 裡儲存的狀態
  // （該分頁已經不是作用中，之後切回去時才看得到完整進度）。
  private tabStopFns = new Map<string, () => void>();

  private tabMessages(tabId: string, updater: (msgs: ChatMessage[]) => ChatMessage[]) {
    if (tabId === this.activeChatId()) {
      this.messages.update(updater);
    } else {
      this.chatTabs.update(tabs => tabs.map(t => t.id === tabId ? { ...t, messages: updater(t.messages) } : t));
    }
  }

  private tabStreaming(tabId: string, streaming: boolean) {
    if (tabId === this.activeChatId()) {
      this.isStreaming.set(streaming);
    } else {
      this.chatTabs.update(tabs => tabs.map(t => t.id === tabId ? { ...t, isStreaming: streaming } : t));
    }
  }

  private tabTokenUsage(tabId: string, usage: { input: number; output: number; cost: number } | null) {
    if (tabId === this.activeChatId()) {
      this.tokenUsage.set(usage);
    } else {
      this.chatTabs.update(tabs => tabs.map(t => t.id === tabId ? { ...t, tokenUsage: usage } : t));
    }
  }

  stopStreaming() {
    const tabId = this.activeChatId();
    const fn = this.tabStopFns.get(tabId);
    if (fn) { fn(); this.tabStopFns.delete(tabId); }
    this.claude.stopChat(this.activeChat?.clientId).subscribe();
    this.isStreaming.set(false);
    this.messages.update(msgs => {
      const copy = [...msgs];
      const last = copy[copy.length - 1];
      if (last?.role === 'assistant') copy[copy.length - 1] = { ...last, isStreaming: false };
      return copy;
    });
  }

  // Session rename
  renamingId = signal<string | null>(null);
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

  loadRunArtifacts(runId: string) {
    if (!runId) return;
    this.claude.getTeamRunArtifacts(runId).subscribe({
      next: (data) => {
        this.runArtifacts.set(data?.artifacts || []);
      },
      error: (err) => {
        console.error('加載成果失敗:', err);
      }
    });
  }

  loadResourceSyncStatus() {
    this.claude.getResourceSyncStatus().subscribe({
      next: status => this.resourceSyncStatus.set(status),
      error: () => this.resourceSyncStatus.set(null),
    });
  }

  toggleResourceSyncDetails() {
    this.resourceSyncDetailsExpanded.update(v => !v);
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
    const x = Math.min(e.clientX, window.innerWidth - 180);
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

  // Single clipboard-copy implementation shared by every copy button: tries the
  // async Clipboard API first, falls back to execCommand('copy') via a temp
  // textarea when it's unavailable (e.g. insecure context), and always surfaces
  // failure instead of silently doing nothing.
  private copyToClipboard(text: string): Promise<void> {
    if (navigator.clipboard?.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise<void>((resolve, reject) => {
      try {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
        resolve();
      } catch (err) {
        reject(err);
      }
    });
  }

  ctxCopyId(s: Session) {
    this.copyToClipboard(s.id).then(
      () => this.showToast('Session ID 已複製', 'success', 1500),
      (err) => { console.error('Copy failed', err); this.showToast('複製失敗', 'error'); }
    );
    this.closeContextMenu();
  }

  copyMessageWithFeedback(event: MouseEvent, text: string) {
    const btn = event.currentTarget as HTMLButtonElement;
    this.copyToClipboard(text).then(
      () => {
        const orig = btn.textContent ?? '';
        btn.textContent = '✓ 已複製';
        btn.classList.add('copied');
        setTimeout(() => {
          btn.textContent = orig;
          btn.classList.remove('copied');
        }, 2000);
      },
      (err) => { console.error('Copy failed', err); this.showToast('複製失敗', 'error'); }
    );
  }

  // Code block copy (event delegation from chat container)
  onChatClick(e: MouseEvent) {
    const btn = (e.target as HTMLElement).closest('[data-copy-code]') as HTMLElement | null;
    if (!btn) return;
    const code = btn.closest('.code-block-wrap')?.querySelector('code') as HTMLElement | null;
    if (!code) return;
    const textToCopy = code.innerText;
    this.copyToClipboard(textToCopy).then(
      () => {
        const orig = btn.textContent;
        btn.textContent = '✓ 已複製';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = orig; btn.classList.remove('copied'); }, 2000);
      },
      (err) => console.error('Fallback copy failed', err)
    );
  }

  // Message edit + regenerate (#11)
  editingMsgIdx = signal<number | null>(null);
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
    const sid = this.activeChatId();

    const applyEditAndResend = () => {
      // slice off from this user message onward
      this.messages.set(this.messages().slice(0, idx));
      this.editingMsgIdx.set(null);
      this.editingMsgText.set('');
      this.inputText = newText;
      // slight delay so DOM settles before send
      setTimeout(() => this.send(), 50);
    };

    if (sid) {
      // Only mutate the displayed history / resend once the backend session
      // history is actually truncated — otherwise the UI would show a shorter
      // conversation than what's persisted, and the next resume would replay
      // the "deleted" messages.
      this.claude.truncateSession(sid, idx).subscribe({
        next: () => applyEditAndResend(),
        error: (e) => this.showToast(`編輯訊息失敗，後端歷史未截斷: ${e.message ?? e}`, 'error'),
      });
    } else {
      applyEditAndResend();
    }
  }

  // ── #17 Profile switching ─────────────────────────────────────────────────
  profiles = signal<Profile[]>([]);
  profileSwitching = signal(false);
  profileDropdownOpen = signal(false);

  loadProfiles() {
    this.claude.getProfiles().subscribe(r => this.profiles.set(r.profiles));
  }

  switchProfile(slug: string) {
    this.profileSwitching.set(true);
    const dir = slug.replace(/^([A-Za-z])--/, '$1:\\').replace(/--/g, '\\');
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

  switchProfileNewTab(slug: string) {
    this.profileDropdownOpen.set(false);
    if (slug === this.projectSlug()) return;
    this.profileSwitching.set(true);
    const dir = slug.replace(/^([A-Za-z])--/, '$1:\\').replace(/--/g, '\\');
    this.claude.setConfig({ projectDir: dir }).subscribe({
      next: (res) => {
        this.settingsForm.projectDir = dir;
        this.settings.save(this.settingsForm);
        this.projectSlug.set(res.slug ?? slug);
        this.profileSwitching.set(false);
        this.addChatTab();   // 建立新對話欄
        this.reload();       // 重載 sessions / agents（即該目錄的歷史對話）
      },
      error: () => this.profileSwitching.set(false),
    });
  }

  // ── Agent 編輯器 ────────────────────────────────────────────────────────────
  agentEditorOpen  = signal(false);
  agentEditorIsNew = signal(false);
  agentEditorData  = signal<Partial<Agent>>({});
  activeAgentId    = computed(() => this.selectedAgent());

  openAgentEditor(agent?: Agent) {
    if (agent) {
      this.agentEditorData.set({ ...agent });
      this.agentEditorIsNew.set(false);
      const soulId = agent.soul || agent.id;
      const s = this.souls().find(x => x.id === soulId);
      this.agentEditorSoulContent = s ? s.content : '';
    } else {
      this.agentEditorData.set({ name: '', description: '', soul: '', skills: [], memory: [], mcp: [], output_memory: [], tools: 'Read, Grep, Glob', engine: '' });
      this.agentEditorIsNew.set(true);
      this.agentEditorSoulContent = '';
    }
    this.agentEditorOpen.set(true);

    this.claude.getEngineStatus().subscribe({
      next: status => {
        this.engineStatus.set(status);
        const eng = this.agentEditorData().engine;
        if (eng && status[eng]?.available === false) {
          const other = eng === 'claude' ? 'codex' : 'claude';
          if (status[other]?.available) {
            this.agentEditorData.set({ ...this.agentEditorData(), engine: other });
            this.showToast(
              `此 Agent 指定的引擎「${this.ENGINE_LABEL[eng]}」目前無法使用，編輯器已預選「${this.ENGINE_LABEL[other]}」（尚未儲存）。`,
              'info', 4000,
            );
          }
        }
      },
      error: () => {},
    });
  }

  saveAgentEditor() {
    const d = this.agentEditorData();
    if (!d.name?.trim()) return;

    const agentId = this.agentEditorIsNew()
      ? d.name.toLowerCase().replace(/[\\/:*?"<>|\s]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '')
      : d.id!;

    d.soul = agentId;

    const obs = this.agentEditorIsNew()
      ? this.claude.createAgent(d)
      : this.claude.updateAgent(d.id!, d);

    obs.subscribe({
      next: () => {
        this.claude.saveSoulProfile(agentId, this.agentEditorSoulContent).subscribe({
          next: () => {
            this.agentEditorOpen.set(false);
            this.claude.getAgents().subscribe(a => this.agents.set(a));
            this.claude.getSouls().subscribe(s => this.souls.set(s));
            this.loadResourceSyncStatus();
          },
          error: (e) => this.showToast(`Agent 已儲存，但 Soul 內容儲存失敗: ${e.message ?? e}`, 'error'),
        });
      },
      error: (e) => this.showToast(`儲存 Agent 失敗: ${e.message ?? e}`, 'error'),
    });
  }

  // getAgentSoulContent: extracted into components/agent-panel (Phase 2)

  deleteAgent(id: string) {
    this.claude.deleteAgent(id).subscribe({
      next: () => {
        this.claude.getAgents().subscribe(a => this.agents.set(a));
        this.loadResourceSyncStatus();
      },
      error: (e) => this.showToast(`刪除 Agent 失敗: ${e.message ?? e}`, 'error'),
    });
  }

  toggleFavorite(agent: Agent) {
    const next = !agent.favorite;
    this.claude.updateAgent(agent.id, { favorite: next }).subscribe({
      next: () => {
        this.agents.update(list => list.map(a => a.id === agent.id ? { ...a, favorite: next } : a));
        this.showToast(next ? `⭐ ${agent.name} 已同步到 Claude Code CLI` : `☆ ${agent.name} 已從 Claude Code CLI 移除`, 'success');
      },
      error: (e) => this.showToast(`更新最愛失敗: ${e.message ?? e}`, 'error'),
    });
  }

  activateAgent(agent: Agent) {
    // 設定 soul
    if (agent.soul) {
      const s = this.souls().find(s => s.id === agent.soul || s.name === agent.soul);
      if (s) this.selectSoulProfile(s.id);
    }
    // 啟動對應 MCPs
    agent.mcp?.forEach(name => {
      const srv = this.mcpServers().find(s => s.name === name || s.id === name);
      if (srv && srv.status !== 'running') this.startMcp(srv.name);
    });

    // 儲存當前 active tab 的狀態，避免狀態流失
    this.saveCurrentTab();

    const agentName = agent.name || agent.id;
    const tabLabel = `與 ${agentName} 對話`;

    // 檢查是否已經有現成的 chat tab 的 selectedAgent 是這個 Agent，且為個人對話 (無 teamId)
    const existingTab = this.chatTabs().find(tab => tab.selectedAgent === agent.id && !tab.teamId);

    if (existingTab) {
      // 如果有，切換到該對話分頁
      this.switchChatTab(existingTab.id);
    } else {
      const activeId = this.activeChatId();
      const activeTabObj = this.chatTabs().find(x => x.id === activeId);
      const activeTabIsEmpty = activeTabObj && (!activeTabObj.messages || activeTabObj.messages.length === 0);

      // 如果沒有，看目前 tab 數量是否小於 4
      if (this.chatTabs().length < 4) {
        // 建立新對話分頁
        const tab = this.makeTab(tabLabel);
        tab.selectedAgent = agent.id;
        
        if (activeTabIsEmpty) {
          // 如果原本對話沒有內容，在添加 Agent 對話的同時，移除(關閉)原本的空對話 Tab
          this.chatTabs.update(tabs => [...tabs.filter(x => x.id !== activeId), tab]);
        } else {
          this.chatTabs.update(tabs => [...tabs, tab]);
        }
        
        // 延遲切換，確保 chatTabs 陣列已更新，並完整同步 Agent 與狀態
        setTimeout(() => {
          this.switchChatTab(tab.id);
        }, 0);
      } else {
        // 如果已經 4 個分頁了，就將當前 active tab 的 agent 切換成該 Agent（清除 teamId 以免衝突）
        if (activeId) {
          this.chatTabs.update(tabs => tabs.map(tab =>
            tab.id === activeId ? { ...tab, selectedAgent: agent.id, label: tabLabel, teamId: undefined } : tab
          ));
          this.selectedAgent.set(agent.id);
        } else {
          // 沒有 activeId 的話就使用第一個 tab
          const firstTab = this.chatTabs()[0];
          this.chatTabs.update(tabs => tabs.map(tab =>
            tab.id === firstTab.id ? { ...tab, selectedAgent: agent.id, label: tabLabel, teamId: undefined } : tab
          ));
          this.switchChatTab(firstTab.id);
        }
      }
    }

    // 自動讓輸入框獲取焦點，方便對話
    setTimeout(() => {
      this.inputRef?.nativeElement?.focus();
    }, 100);
  }

  agentEditorToggleList(field: 'skills' | 'memory' | 'mcp' | 'output_memory', value: string) {
    const d = this.agentEditorData();
    const list = (d[field] as string[]) ?? [];
    const next = list.includes(value) ? list.filter(x => x !== value) : [...list, value];
    this.agentEditorData.set({ ...d, [field]: next });
  }

  agentEditorAddOutputMemory(key: string) {
    if (!key.trim()) return;
    const d = this.agentEditorData();
    const list = d.output_memory ?? [];
    if (!list.includes(key)) this.agentEditorData.set({ ...d, output_memory: [...list, key] });
  }

  agentEditorRemoveOutputMemory(key: string) {
    const d = this.agentEditorData();
    this.agentEditorData.set({ ...d, output_memory: (d.output_memory ?? []).filter(x => x !== key) });
  }

  // ── Skill 編輯器 ────────────────────────────────────────────────────────────
  skillEditorOpen = signal(false);
  skillEditorData = signal<Partial<Skill>>({});

  openSkillEditor(skill: Skill) {
    this.claude.getSkill(skill.id).subscribe(s => {
      this.skillEditorData.set({ ...s });
      this.skillEditorOpen.set(true);
    });
  }

  saveSkillEditor() {
    const d = this.skillEditorData();
    if (!d.id) return;
    this.claude.updateSkill(d.id, { description: d.description, mcp: d.mcp, memory: d.memory, output_memory: d.output_memory })
      .subscribe({
        next: () => {
          this.skillEditorOpen.set(false);
          this.claude.getSkills().subscribe(s => this.skills.set(s));
          this.loadResourceSyncStatus();
        },
        error: (e) => this.showToast(`儲存 Skill 失敗: ${e.message ?? e}`, 'error'),
      });
  }

  skillEditorToggleList(field: 'memory' | 'mcp', value: string) {
    const d = this.skillEditorData();
    const list = (d[field] as string[]) ?? [];
    const next = list.includes(value) ? list.filter(x => x !== value) : [...list, value];
    this.skillEditorData.set({ ...d, [field]: next });
  }

  skillEditorAddOutputMemory(key: string) {
    if (!key.trim()) return;
    const d = this.skillEditorData();
    const list = d.output_memory ?? [];
    if (!list.includes(key)) this.skillEditorData.set({ ...d, output_memory: [...list, key] });
  }

  skillEditorRemoveOutputMemory(key: string) {
    const d = this.skillEditorData();
    this.skillEditorData.set({ ...d, output_memory: (d.output_memory ?? []).filter(x => x !== key) });
  }

  // ── Teams ────────────────────────────────────────────────────────────────────
  teams = signal<Team[]>([]);
  teamEditorOpen  = signal(false);
  teamEditorIsNew = signal(false);
  teamEditorData  = signal<Partial<Team>>({});

  sortedTeams = computed(() => {
    const q = this.rightPanelFilter().toLowerCase();
    let list = [...this.teams()];
    if (q) {
      list = list.filter(t => t.name.toLowerCase().includes(q) || t.description?.toLowerCase().includes(q));
    }
    return list.sort((a, b) => {
      if (!!a.favorite !== !!b.favorite) return a.favorite ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  });

  loadTeams() {
    this.claude.getTeams().subscribe(t => this.teams.set(t));
  }

  // toggleTeamExpanded: extracted into components/team-panel (Phase 2)

  openTeamEditor(team?: Team) {
    if (team) {
      this.teamEditorData.set({ ...team, members: team.members.map(m => ({ ...m })) });
      this.teamEditorIsNew.set(false);
    } else {
      this.teamEditorData.set({ name: '', description: '', members: [], execution_mode: 'parallel' });
      this.teamEditorIsNew.set(true);
    }
    this.teamEditorOpen.set(true);
  }

  getAgentName(id: string): string {
    const a = this.dropdownAgents().find(x => x.id === id);
    return a ? a.name : id;
  }

  onTeamLeaderChange(val: string) {
    const d = this.teamEditorData();
    const members = d.members ?? [];
    if (members.length === 0 && val) {
      this.teamEditorData.set({
        ...d,
        leader: val,
        members: [{ agent: val, role: '組長' }]
      });
    } else {
      this.teamEditorData.set({
        ...d,
        leader: val
      });
    }
  }

  saveTeamEditor() {
    const d = { ...this.teamEditorData() };
    if (!d.name?.trim()) return;

    // 確保組長只能是成員之一。如果沒有設定組長，或該組長不在成員名單中，預設為第一個成員
    const members = d.members ?? [];
    const memberIds = members.map(m => m.agent).filter(Boolean);

    if (!d.leader || !memberIds.includes(d.leader)) {
      d.leader = memberIds.length > 0 ? memberIds[0] : '';
    }

    const obs = this.teamEditorIsNew()
      ? this.claude.createTeam(d)
      : this.claude.updateTeam(d.id!, d);
    obs.subscribe({
      next: () => { this.teamEditorOpen.set(false); this.loadTeams(); },
      error: (e) => this.showToast(`儲存 Team 失敗: ${e.message ?? e}`, 'error'),
    });
  }

  deleteTeam(id: string) {
    this.claude.deleteTeam(id).subscribe({
      next: () => this.loadTeams(),
      error: (e) => this.showToast(`刪除 Team 失敗: ${e.message ?? e}`, 'error'),
    });
  }

  toggleTeamFavorite(team: Team) {
    const next = !team.favorite;
    this.claude.updateTeam(team.id, { favorite: next }).subscribe({
      next: () => {
        this.teams.update(list => list.map(t => t.id === team.id ? { ...t, favorite: next } : t));
        this.showToast(next ? `⭐ ${team.name} 已加入最愛` : `☆ ${team.name} 已取消最愛`, 'success');
      },
      error: (e) => this.showToast(`更新 Team 最愛失敗: ${e.message ?? e}`, 'error'),
    });
  }

  teamEditorAddMember() {
    const d = this.teamEditorData();
    this.teamEditorData.set({ ...d, members: [...(d.members ?? []), { agent: '', role: '' }] });
  }

  teamEditorRemoveMember(idx: number) {
    const d = this.teamEditorData();
    this.teamEditorData.set({ ...d, members: (d.members ?? []).filter((_, i) => i !== idx) });
  }

  teamEditorUpdateMember(idx: number, field: 'agent' | 'role', val: string) {
    const d = this.teamEditorData();
    const members = (d.members ?? []).map((m, i) => i === idx ? { ...m, [field]: val } : m);
    this.teamEditorData.set({ ...d, members });
  }

  selectTeamLeader(t: Team) {
    // 組長優先用 t.leader，空時 fallback 到第一個成員
    const leaderId = t.leader || (t.members[0]?.agent ?? '');
    if (!leaderId) {
      alert(`此團隊 "${t.name}" 尚未設定組長且無成員！`);
      return;
    }

    // 注意：不在前端驗證 agent 是否存在，讓後端決定（避免 agents 清單未即時更新的問題）

    // 1. 儲存當前 active tab 的狀態，避免狀態流失
    this.saveCurrentTab();

    // 從 dropdownAgents 取名稱，找不到就直接用 id
    const leaderAgent = this.dropdownAgents().find(a => a.id === leaderId);
    const leaderName = leaderAgent?.name || leaderId;
    const tabLabel = `👥 團隊對話 (${t.name})`;

    // 2. 檢查是否已經有現成的 chat tab 綁定了該團隊的組長對話
    const existingTab = this.chatTabs().find(tab => tab.selectedAgent === leaderId && tab.teamId === t.id);

    if (existingTab) {
      // 如果有，切換到該對話分頁
      this.switchChatTab(existingTab.id);
    } else {
      const activeId = this.activeChatId();
      const activeTabObj = this.chatTabs().find(x => x.id === activeId);
      const activeTabIsEmpty = activeTabObj && (!activeTabObj.messages || activeTabObj.messages.length === 0);

      // 如果沒有，看目前 tab 數量是否小於 4
      if (this.chatTabs().length < 4) {
        // 建立新對話分頁，傳入團隊 ID 進行綁定
        const tab = this.makeTab(tabLabel, undefined, t.id);
        tab.selectedAgent = leaderId;

        if (activeTabIsEmpty) {
          // 如果原本對話沒有內容，在添加組長對話的同時，移除(關閉)原本的空對話 Tab
          this.chatTabs.update(tabs => [...tabs.filter(x => x.id !== activeId), tab]);
        } else {
          this.chatTabs.update(tabs => [...tabs, tab]);
        }

        // 延遲切換，確保 chatTabs 陣列已更新，並完整同步 Agent 與狀態
        setTimeout(() => {
          this.switchChatTab(tab.id);
        }, 0);
      } else {
        // 已經 4 個分頁：覆蓋當前 active tab，同時正確設置 teamId
        if (activeId) {
          this.chatTabs.update(tabs => tabs.map(tab =>
            tab.id === activeId ? { ...tab, selectedAgent: leaderId, label: tabLabel, teamId: t.id } : tab
          ));
          this.selectedAgent.set(leaderId);
        } else {
          const firstTab = this.chatTabs()[0];
          this.chatTabs.update(tabs => tabs.map(tab =>
            tab.id === firstTab.id ? { ...tab, selectedAgent: leaderId, label: tabLabel, teamId: t.id } : tab
          ));
          this.switchChatTab(firstTab.id);
        }
      }
    }

    // 3. 自動讓輸入框獲取焦點，方便對話
    setTimeout(() => {
      this.inputRef?.nativeElement?.focus();
    }, 100);
  }

  // ── Team Run (Phase 3) ────────────────────────────────────────────────────
  // 2026-07-10 修復：teamRunOpen/teamRunState 這兩個 signal 從未被任何
  // template 讀取過，openTeamRun()/submitTeamRun() 也從未被任何按鈕呼叫過
  // ——結果是唯一真正可從 UI 觸發的入口 submitHRTeamRun()（🤖 自動組隊）點下
  // 「▶ 開始執行」後，後端會真的啟動一個會消耗 API 額度的 team run，但畫面上
  // 完全沒有任何進度顯示、沒有結果、沒有錯誤訊息——使用者只會看到彈窗關閉，
  // 像什麼都沒發生一樣。改成比照 executeTeamCodePhase()（/api/team/execute
  // 那條路徑，已經在畫面上正確 render）的模式：把 team run 掛在一則 chat
  // message 上（ChatMessage.teamRun），用既有的 embedded-tr-steps 區塊顯示
  // 進度；並改用 tabMessages/tabStreaming/tabStopFns 的 per-tab 模式，避免
  // 切分頁時進度事件寫錯分頁（跟 T38 是同一類問題）。真正無人呼叫的
  // openTeamRun()/teamRunTarget/teamRunTask/teamRunOpen/teamRunState/
  // cancelTeamRun()/closeTeamRun() 直接移除，不留死碼。

  private _applyTeamRunEvent(tabId: string, ev: any) {
    if (ev.type === 'ping') return;
    this.tabMessages(tabId, msgs => {
      const lastIdx = msgs.length - 1;
      const lastMsg = msgs[lastIdx];
      if (!lastMsg || !lastMsg.teamRun) return msgs;
      const tr = lastMsg.teamRun;
      const steps = [...tr.steps];
      const copy = [...msgs];
      if (ev.type === 'step_start' && steps[ev.step]) {
        steps[ev.step] = { ...steps[ev.step], status: 'running' };
      } else if (ev.type === 'step_text' && steps[ev.step]) {
        steps[ev.step] = { ...steps[ev.step], output: steps[ev.step].output + ev.text };
      } else if (ev.type === 'step_done' && steps[ev.step]) {
        steps[ev.step] = { ...steps[ev.step], status: 'done' };
      } else if (ev.type === 'done') {
        copy[lastIdx] = {
          ...lastMsg, isStreaming: false, text: `✓ ${tr.name} 執行完成`,
          teamRun: { ...tr, status: 'done', steps, summary: ev.summary ?? '' },
        };
        return copy;
      } else if (ev.type === 'cancelled') {
        copy[lastIdx] = { ...lastMsg, isStreaming: false, teamRun: { ...tr, status: 'cancelled', steps } };
        return copy;
      } else if (ev.type === 'error') {
        copy[lastIdx] = { ...lastMsg, isStreaming: false, teamRun: { ...tr, status: 'error', steps } };
        return copy;
      }
      copy[lastIdx] = { ...lastMsg, teamRun: { ...tr, steps } };
      return copy;
    });
    if (tabId === this.activeChatId()) this.shouldScroll = true;
  }

  private _dispatchTeamRun(
    tabId: string, teamId: string, task: string, cwd: string, model: string,
    teamName: string, members: { agent: string; role: string }[], inlineTeam?: any,
  ) {
    const now = new Date().toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' });
    const teamRun: TeamRun = {
      id: '', team_id: teamId, name: teamName, task,
      status: 'running',
      steps: members.map(m => ({ agent: m.agent, role: m.role, status: 'pending' as const, output: '' })),
      summary: '',
    };
    const runMsg: ChatMessage = { role: 'assistant', text: '', isStreaming: true, time: now, teamRun };
    this.tabMessages(tabId, m => [...m, runMsg]);
    if (tabId === this.activeChatId()) this.shouldScroll = true;
    this.tabStreaming(tabId, true);

    const agentEngine = this.settings.get().agentEngine;
    this.claude.runTeam(teamId, task, model, cwd, inlineTeam, agentEngine).subscribe({
      next: (r) => {
        const runId = r.run_id;
        this.tabMessages(tabId, msgs => {
          const lastIdx = msgs.length - 1;
          const lastMsg = msgs[lastIdx];
          if (!lastMsg?.teamRun) return msgs;
          const copy = [...msgs];
          copy[lastIdx] = { ...lastMsg, teamRun: { ...lastMsg.teamRun, id: runId } };
          return copy;
        });

        const stopFn = this.claude.streamTeamRun(
          runId,
          (ev) => this._applyTeamRunEvent(tabId, ev),
          () => {
            this.tabStreaming(tabId, false);
            this.tabStopFns.delete(tabId);
            this.loadRunArtifacts(runId);
          },
          (e) => {
            console.error('team run error', e);
            this.tabStreaming(tabId, false);
            this.tabStopFns.delete(tabId);
          },
        );
        this.tabStopFns.set(tabId, () => {
          stopFn();
          this.claude.cancelTeamRun(runId).subscribe();
          this.tabStreaming(tabId, false);
          this.tabStopFns.delete(tabId);
          this.tabMessages(tabId, msgs => {
            const lastIdx = msgs.length - 1;
            const lastMsg = msgs[lastIdx];
            if (!lastMsg?.teamRun) return msgs;
            const copy = [...msgs];
            copy[lastIdx] = { ...lastMsg, isStreaming: false, teamRun: { ...lastMsg.teamRun, status: 'cancelled' } };
            return copy;
          });
        });
      },
      error: (err) => {
        this.tabStreaming(tabId, false);
        const errMsg = err.error?.error || err.message || '執行失敗';
        this.showToast(errMsg, 'error');
        this.tabMessages(tabId, msgs => {
          const lastIdx = msgs.length - 1;
          const lastMsg = msgs[lastIdx];
          if (!lastMsg?.teamRun) return msgs;
          const copy = [...msgs];
          copy[lastIdx] = { ...lastMsg, isStreaming: false, text: `⚠ ${errMsg}`, teamRun: { ...lastMsg.teamRun, status: 'error' } };
          return copy;
        });
      },
    });
  }

  // ── Team Run — step output expand/collapse ────────────────────────────────
  expandedOutputs = signal<number[]>([]);

  toggleStepOutput(idx: number) {
    this.expandedOutputs.update(list =>
      list.includes(idx) ? list.filter(i => i !== idx) : [...list, idx]
    );
  }

  // ── HR Agent (Phase 4) ────────────────────────────────────────────────────
  hrLoading  = signal(false);
  hrPlanOpen = signal(false);
  hrTeamPlan = signal<any>(null);
  hrError    = signal<string | null>(null);

  dispatchHR() {
    const task = this.inputText.trim();
    if (!task) return;
    this.hrLoading.set(true);
    this.hrError.set(null);

    const agentEngine = this.settings.get().agentEngine;
    this.claude.dispatchHR(task, agentEngine).subscribe({
      next: (plan) => {
        this.hrLoading.set(false);
        if (plan.error) {
          this.hrError.set(plan.error);
          this.showToast(plan.error, 'error');
        } else {
          if (plan.engine_notice) this.showToast(plan.engine_notice, 'info', 4000);
          // Normalise plan fields to avoid undefined errors
          if (!plan.members) plan.members = [];
          plan.members.forEach((m: any) => {
            if (!m.input_memory) m.input_memory = [];
            if (!m.output_memory) m.output_memory = [];
          });
          this.hrTeamPlan.set(plan);
          this.hrPlanOpen.set(true);
        }
      },
      error: (err) => {
        this.hrLoading.set(false);
        const errMsg = err.error?.error || err.message || '自動組隊失敗';
        this.hrError.set(errMsg);
        this.showToast(errMsg, 'error');
      }
    });
  }

  hrAddStep() {
    const plan = this.hrTeamPlan();
    if (!plan) return;
    const members = [...(plan.members || []), { agent: '', role: '', input_memory: [], output_memory: [] }];
    this.hrTeamPlan.set({ ...plan, members });
  }

  hrRemoveStep(idx: number) {
    const plan = this.hrTeamPlan();
    if (!plan) return;
    const members = (plan.members || []).filter((_: any, i: number) => i !== idx);
    this.hrTeamPlan.set({ ...plan, members });
  }

  hrUpdateStep(idx: number, field: string, val: any) {
    const plan = this.hrTeamPlan();
    if (!plan) return;
    if (idx === -1) {
      this.hrTeamPlan.set({ ...plan, [field]: val });
      return;
    }
    const members = (plan.members || []).map((m: any, i: number) => {
      if (i === idx) {
        if (field === 'input_memory' || field === 'output_memory') {
          return { ...m, [field]: typeof val === 'string' ? val.split(',').map((x: string) => x.trim()).filter((x: string) => x) : val };
        }
        return { ...m, [field]: val };
      }
      return m;
    });
    this.hrTeamPlan.set({ ...plan, members });
  }

  submitHRTeamRun() {
    const plan = this.hrTeamPlan();
    const task = this.inputText.trim();
    if (!plan || !task) return;

    this.hrPlanOpen.set(false);
    this.expandedOutputs.set([]);
    this.inputText = '';

    const tabId = this.activeChatId();
    const s = this.settings.get();
    this._dispatchTeamRun(
      tabId, '', task, s.workDir, s.model,
      plan.name || '自動組隊任務', plan.members, plan,
    );
  }

  // 清空某個對話欄的訊息
  clearTab(tabId: string, e: Event) {
    e.stopPropagation();
    const tab = this.chatTabs().find(t => t.id === tabId);
    const msgCount = tab?.messages?.length ?? 0;
    if (msgCount > 0 && !confirm(`確定要清空此對話嗎？（${msgCount} 則訊息將被刪除，此操作無法復原）`)) {
      return;
    }
    // 呼叫後端清除該 Tab 的 Session 快取，防止重啟對話時又 resume 舊歷史
    this.claude.clearChat(tab?.clientId).subscribe();

    this.chatTabs.update(tabs => tabs.map(t =>
      t.id === tabId ? { ...t, messages: [], label: '新對話', selectedAgent: '', teamId: undefined } : t
    ));
    if (tabId === this.activeChatId()) {
      this.messages.set([]);
      this.tokenUsage.set(null);
      this.selectedAgent.set('');
    }
  }

  // ── #16 Provider mode ─────────────────────────────────────────────────────
  useProvider = computed(() => this.settings.get().provider !== 'claude');

  // ── #19 i18n ──────────────────────────────────────────────────────────────
  readonly EN_STRINGS: Record<string, string> = {
    '新對話': 'New Chat',
    '設定': 'Settings',
    '搜尋對話': 'Search chats',
    '發送訊息': 'Send message',
    '停止': 'Stop',
    '今天': 'Today',
    '昨天': 'Yesterday',
    '本週': 'This week',
    '更早': 'Earlier',
    '置頂': 'Pinned',
    '模型': 'Model',
    '記憶': 'Memory',
    '排程': 'Schedule',
    'Agents': 'Agents',
    'Skills': 'Skills',
    'MCP': 'MCP',
    '匯出': 'Export',
    '備份': 'Backup',
    '說明': 'Help',
    '工作目錄': 'Work dir',
    '目前無對話': 'No conversations yet',
    '無記憶項目': 'No memory items',
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
    const date = new Date().toLocaleString('zh-TW');
    let content = '';
    let mime = 'text/plain';
    let ext = format;

    if (format === 'md') {
      mime = 'text/markdown';
      const lines = msgs.map(m => {
        const ts = m.time ? ` *(${m.time})*` : '';
        if (m.role === 'user') return `## 使用者${ts}\n\n${m.text}`;
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
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `chat-${Date.now()}.${ext}`; a.click();
    URL.revokeObjectURL(url);
  }

  // ── #20 Debug dump ────────────────────────────────────────────────────────
  downloadDebugDump() {
    window.open(this.claude.debugDumpUrl(), '_blank');
  }

  // ── #18 Telegram settings: extracted into components/telegram-settings (Phase 2) ──

  // ── #22 Auto-update progress ──────────────────────────────────────────────
  updateProgress = signal<number | null>(null);
  updateAvailable = signal(false);
  updateReady = signal(false);

  // MCP log viewer (#15): extracted into components/mcp-panel (Phase 2),
  // including the 2.5s auto-refresh interval and its cleanup — the
  // component now clears it in ngOnDestroy, which fires whenever Angular
  // destroys it (leaving the MCP tab via the @if wrapping the @defer
  // block, or app teardown). This actually fixes the leak T40 patched
  // around (see removed stopMcpLogPolling()) more correctly than before:
  // that fix only stopped polling when Settings closed, an unrelated
  // modal — switching tabs away from 'mcp' with the log open never
  // stopped it. ngOnDestroy stops it on the transition that actually
  // matters.
  closeSettings() {
    this.settingsOpen.set(false);
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
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.blob();
      })
      .then(blob => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = `claude-backup-${Date.now()}.zip`; a.click();
        URL.revokeObjectURL(url);
      })
      .catch((e) => this.showToast(`備份下載失敗: ${e.message ?? e}`, 'error'));
  }

  exportChat() {
    this.exportChatAs(this.exportFormat());
  }

  // Retry last message
  private lastUserText = '';
  private lastAttachments: string[] = [];

  retryLast() {
    if (!this.lastUserText || this.isStreaming()) return;
    this.inputText = this.lastUserText;
    this.attachedFiles.set(this.lastAttachments.map(p => ({ name: p.split(/[\\/]/).pop()!, path: p })));
    this.send();
  }

  // Workdir quick switch — recentWorkDirs signal extracted into
  // components/recent-work-dirs (Phase 2)
  setWorkDir(dir: string) {
    this.settingsForm.workDir = dir;
    this.settings.save(this.settingsForm);
  }

  // Grouped sessions
  groupedSessions = computed(() => {
    const now = Date.now() / 1000;
    const day = 86400;
    const groups: { label: string; items: Session[] }[] = [
      { label: '今天', items: [] },
      { label: '昨天', items: [] },
      { label: '本週', items: [] },
      { label: '更早', items: [] },
    ];
    for (const s of this.sessions()) {
      const age = now - s.mtime;
      if (age < day) groups[0].items.push(s);
      else if (age < 2 * day) groups[1].items.push(s);
      else if (age < 7 * day) groups[2].items.push(s);
      else groups[3].items.push(s);
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
      next: r => this.detailTranslation.set(r),
      error: () => this.detailTranslation.set('[翻譯失敗，請重試]'),
    });
  }

  clearDetailTranslation() {
    this.detailTranslation.set(null);
  }

  // Slash command menu
  slashMenuOpen = signal(false);
  slashMenuIndex = signal(0);
  slashMenuItems = computed(() => {
    const q = this.slashQuery().toLowerCase();
    const builtins = this.BUILTIN_CMDS.filter(c => !q || c.name.includes(q));
    const skills = this.skills().filter(s => !q || s.name.toLowerCase().includes(q));
    return [...builtins, ...skills].slice(0, 10);
  });
  private slashQuery = signal('');

  // UI state
  sidebarOpen = signal(true);
  rightOpen = signal(true);
  settingsOpen = signal(false);
  shouldScroll = false;

  settingsForm!: AppSettings;
  statusInfo = signal('確認中…');
  projectSlug = signal('');
  resolvedClaudeHome = signal('');
  skillGenBusy = signal(false);
  skillGenResult = signal<string | null>(null);

  // ── Onboarding wizard ────────────────────────────────
  showOnboarding = signal(false);
  onboardingStep = signal(1);   // 1=歡迎 2=確認連線 3=專案目錄 4=完成
  onboardingDir = signal('');
  onboardingSlug = computed(() => {
    const d = this.onboardingDir();
    return d ? d.replace(/:/g, '-').replace(/\\/g, '-').replace(/\//g, '-') : '';
  });

  // ── Help modal ───────────────────────────────────────
  helpOpen = signal(false);
  helpSection = signal<'start' | 'features' | 'faq'>('start');

  memoryKeys = computed(() => Object.keys(this.memory()));
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
      try { this.authorizedSkills.set(JSON.parse(savedSkills)); } catch { }
    }
    const savedMcps = localStorage.getItem('claude_desktop_auth_mcps');
    if (savedMcps) {
      try { this.authorizedMcps.set(JSON.parse(savedMcps)); } catch { }
    }

    // 載入永久 agent 綁定
    try {
      const as = localStorage.getItem('claude_desktop_agent_skills');
      if (as) this.agentSkillsMap.set(JSON.parse(as));
    } catch { }
    try {
      const am = localStorage.getItem('claude_desktop_agent_mcps_direct');
      if (am) this.agentMcpsMap.set(JSON.parse(am));
    } catch { }
    try {
      const mm = localStorage.getItem('claude_desktop_managed_mcps');
      if (mm) this.managedMcpNames.set(JSON.parse(mm));
    } catch { }

    this.loadMcp();

    // 草稿恢復
    const draft = localStorage.getItem('claude_input_draft');
    if (draft) this.inputText = draft;

    // 置頂 session ID 恢復
    try {
      const pinned = localStorage.getItem('claude_pinned_sessions');
      if (pinned) this.pinnedIds.set(JSON.parse(pinned));
    } catch { }

    // Session metadata 恢復（顏色 + 標籤）
    this.loadSessionMeta();

    // 首次啟動精靈
    if (!localStorage.getItem('claude_onboarding_done')) {
      setTimeout(() => {
        this.showOnboarding.set(true);
        this.loadEngineStatus();
      }, 600);
    }
  }

  // ── Onboarding methods ──────────────────────────────
  nextOnboardingStep() {
    const s = this.onboardingStep();
    if (s < 4) { this.onboardingStep.set(s + 1); }
    else { this.completeOnboarding(); }
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
    this.loadEngineStatus();
  }
  async pickOnboardingDir() {
    const dir = await this.claude.pickDirectory();
    if (dir) this.onboardingDir.set(dir);
  }

  // ── 左下角使用者選單 ──────────────────────────────────────────────────────
  userMenuOpen = signal(false);

  openExternalUrl(url: string) {
    const api = (window as any).electronAPI;
    if (api?.openExternal) api.openExternal(url);
    else window.open(url, '_blank');
  }

  openHelp() {
    this.userMenuOpen.set(false);
    this.helpOpen.set(true);
  }

  openFeedback() {
    this.openExternalUrl('https://github.com/anthropics/claude-code/issues');
    this.userMenuOpen.set(false);
  }

  toggleLang() {
    const next = this.settings.get().lang === 'zh' ? 'en' : 'zh';
    this.settings.save({ lang: next });
    this.settingsForm.lang = next;
  }

  get currentTheme(): 'dark' | 'light' {
    return this.settings.get().theme || 'dark';
  }

  toggleTheme() {
    const current = this.settings.get().theme || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    this.settings.save({ theme: next });
    this.settingsForm.theme = next;
  }

  applyQuickPrompt(text: string) {
    this.inputText = text;
    this.saveCurrentTab();
    setTimeout(() => {
      if (this.inputRef?.nativeElement) {
        const el = this.inputRef.nativeElement;
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 200) + 'px';
        el.focus();
      }
    }, 10);
  }

  checkForUpdates() {
    this.userMenuOpen.set(false);
    const api = (window as any).electronAPI;
    if (api?.checkForUpdates) { api.checkForUpdates(); return; }
    this.showToast('請使用發行版（非 dev 模式）才能自動更新');
  }

  logoutClaude() {
    this.userMenuOpen.set(false);
    this.claude.runCliCommand(['logout']).subscribe({
      next: out => this.showToast(out || '已登出 Claude Code'),
      error: () => this.showToast('登出指令執行失敗'),
    });
  }

  async openSettings() {
    this.settingsForm = this.settings.get();
    this.settingsOpen.set(true);
    this.loadEngineStatus();
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
      if (!this.settingsForm.codexApiKeyCmd && c.codexApiKeyCmd) {
        this.settingsForm.codexApiKeyCmd = c.codexApiKeyCmd;
      }
      if (!this.settingsForm.claudeHome && c.claudeHome) {
        this.settingsForm.claudeHome = c.claudeHome;
      }
      const mode = c.engineMode ?? 'both';
      this.settingsForm.engineMode = mode;
      this.engineMode.set(mode);
    });
    // 從 Electron 讀取真實的 login item 狀態
    const eAPI = (window as any).electronAPI;
    if (eAPI?.getLoginItem) {
      this.settingsForm.openAtLogin = await eAPI.getLoginItem();
    }
  }

  saveSettings() {
    this.settings.save(this.settingsForm);
    this.claude.setConfig({
      projectDir: this.settingsForm.projectDir,
      apiKeyCmd: this.settingsForm.apiKeyCmd,
      codexApiKeyCmd: this.settingsForm.codexApiKeyCmd,
      claudeHome: this.settingsForm.claudeHome,
      engineMode: this.settingsForm.engineMode,
    }).subscribe({
      next: () => this.engineMode.set(this.settingsForm.engineMode),
      error: (e) => this.showToast(`後端設定儲存失敗: ${e.message ?? e}`, 'error'),
    });
    // 同步 Electron login item
    const eAPI = (window as any).electronAPI;
    if (eAPI?.setLoginItem) {
      eAPI.setLoginItem(this.settingsForm.openAtLogin);
    }
    this.closeSettings();
  }

  ngOnInit() {
    this.reload();
    // 執行引擎範圍是後端權威值（database.get_engine_mode()），不是純本地
    // localStorage 值——啟動時就先讀一次，這樣 Agent 編輯器不用自己另外
    // 打一次 /api/config 才能判斷目前是否鎖定。
    this.claude.getConfig().subscribe({
      next: c => this.engineMode.set(c.engineMode ?? 'both'),
      error: () => {},
    });
    this._healthTimer = setInterval(() => {
      this.claude.getStatus().subscribe({
        next: () => {
          if (this.backendDown()) { this.backendDown.set(false); this.reload(); }
        },
        error: () => this.backendDown.set(true),
      });
    }, 10000);
    this._resourceSyncTimer = setInterval(() => this.loadResourceSyncStatus(), 60 * 1000);
    // T06 — 每秒更新 tool timer
    this._toolTickTimer = setInterval(() => this.toolTick.update(v => v + 1), 1000);

    // 用量：啟動時取一次，之後每 5 分鐘輪詢
    const fetchUsage = () => this.claude.getUsage().subscribe({
      next: (d: any) => this.usage.set({
        fiveHour:      d.five_hour?.utilization ?? 0,
        fiveHourReset: d.five_hour?.resets_at   ?? '',
        sevenDay:      d.seven_day?.utilization  ?? 0,
        sevenDayReset: d.seven_day?.resets_at    ?? '',
      }),
      error: (err) => {
        console.error('Failed to fetch usage:', err);
        if (!this.usage()) {
          setTimeout(fetchUsage, 10000);
        }
      },
    });
    const fetchCodexUsage = () => this.claude.getCodexUsage().subscribe({
      next: data => this.codexUsage.set(data),
      error: err => {
        console.error('Failed to fetch Codex usage:', err);
        if (!this.codexUsage()) setTimeout(fetchCodexUsage, 10000);
      },
    });
    fetchUsage();
    fetchCodexUsage();
    this.usageTimer = setInterval(() => { fetchUsage(); fetchCodexUsage(); }, 5 * 60 * 1000);

    // #22 — Wire Electron auto-updater IPC events
    const eAPI = (window as any).electronAPI;
    if (eAPI?.onUpdateProgress) eAPI.onUpdateProgress((pct: number) => this.updateProgress.set(pct));
    if (eAPI?.onUpdateAvailable) eAPI.onUpdateAvailable(() => this.updateAvailable.set(true));
    if (eAPI?.onUpdateReady) eAPI.onUpdateReady(() => { this.updateReady.set(true); this.updateProgress.set(100); });
  }

  ngOnDestroy() {
    clearInterval(this._healthTimer); clearInterval(this._resourceSyncTimer);
    clearInterval(this._toolTickTimer); clearInterval(this.usageTimer);
    for (const fn of this.tabStopFns.values()) fn();
    this.tabStopFns.clear();
    if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
      this.mediaRecorder.ondataavailable = null;
      this.mediaRecorder.onerror = null;
      this.mediaRecorder.onstop = null;
      try { this.mediaRecorder.stop(); } catch { /* already stopped */ }
    }
    this.cleanupAudioRecording();
    this.stopSpeaking();
  }

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
      const result = await this.claude.uploadFile(file);
      this.attachedFiles.update(a => [...a, { ...result, preview }]);
    } catch (err: any) {
      this.showToast(`上傳截圖失敗: ${err.message ?? err}`, 'error');
    }
    this.isUploading.set(false);
  }

  selectSoulProfile(id: string) {
    this.selectedSoulId.set(id);
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

  saveSoulProfileEdits(content: string) {
    const id = this.selectedSoulId();
    if (!id) return;
    this.claude.saveSoulProfile(id, content).subscribe({
      next: () => {
        this.soulDraftSaved.set(true);
        this.souls.update(list => list.map(x => x.id === id ? { ...x, content } : x));
      },
      error: (e) => this.showToast(`Soul 儲存失敗: ${e.message ?? e}`, 'error'),
    });
  }

  discardSoulProfileEdits() {
    const id = this.selectedSoulId();
    if (!id) return;
    this.soulDraftSaved.set(true);
  }

  addSoulProfile() {
    // Auto-generate default name if none given
    let name = this.newSoulName.trim().replace(/\.md$/i, '').trim();
    if (!name) {
      const existing = this.souls().map(s => s.id);
      let n = 1;
      while (existing.includes(`靈魂-${n}`)) n++;
      name = `靈魂-${n}`;
    }
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
        this.showToast('新增靈魂失敗，請確認名稱無包含特殊字元。', 'error', 4000);
      }
    });
  }

  // startRenameSoul: extracted into components/soul-panel (Phase 2)

  confirmRenameSoul(oldId: string, rawInput: string) {
    const newName = rawInput.trim().replace(/\.md$/i, '').trim();
    if (!newName || newName === oldId) return;
    this.claude.renameSoulProfile(oldId, newName).subscribe({
      next: (res) => {
        const newId = res.id || newName;
        this.claude.getSouls().subscribe(list => {
          this.souls.set(list);
          if (this.selectedSoulId() === oldId) this.selectedSoulId.set(newId);
        });
      },
      error: () => this.showToast('改名失敗，名稱可能已存在', 'error', 3000),
    });
  }

  deleteSoulProfile(id: string) {
    if (!confirm(`確定要刪除「${id}.md」嗎？`)) return;
    this.claude.deleteSoulProfile(id).subscribe(() => {
      if (this.selectedSoulId() === id) {
        this.selectedSoulId.set('');
      }
      this.claude.getSouls().subscribe(list => {
        this.souls.set(list);
        if (list.length && !this.selectedSoulId()) {
          this.selectSoulProfile(list[0].id);
        }
      });
    });
  }

  // aiParsing / isNaturalLanguage / parseCronFromAI / addSchedule /
  // deleteSchedule / toggleSchedule / runScheduleNow: extracted into
  // components/schedule-panel (Phase 2)

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

  reload() {
    this.sessionOffset = 0;
    this.claude.getStats().subscribe(s => this.stats.set(s));
    this.claude.getAgents().subscribe(a => this.agents.set(a));
    this.claude.getSkills().subscribe(s => this.skills.set(s));
    this.claude.getSessions(this.sessionSearch, 0).subscribe(r => {
      this.sessions.set(r.items);
      this.hasMoreSessions.set(r.has_more);
    });
    this.claude.getMemory().subscribe(m => this.memory.set(m));
    this.claude.getSouls().subscribe(list => {
      this.souls.set(list);
      if (list.length && !this.selectedSoulId()) {
        this.selectSoulProfile(list[0].id);
      }
    });
    this.loadProfiles();
    this.loadTeams();
    this.claude.getSoul().subscribe(s => { this.soulContent = s; });
    this.loadMcp();
    this.loadResourceSyncStatus();
    this.loadEngineStatus();
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
        const result = await this.claude.uploadFile(file);
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
    this.lastUserText = text;
    this.lastAttachments = this.attachedFiles().map(f => f.path);
    this.inputText = '';
    if (this.inputRef?.nativeElement) {
      this.inputRef.nativeElement.style.height = 'auto';
    }
    localStorage.removeItem('claude_input_draft');
    this.isStreaming.set(true);
    const attachments = this.attachedFiles().map(f => f.path);
    this.attachedFiles.set([]);

    const curTab = this.activeChat;
    const tabId = this.activeChatId();
    if (curTab && curTab.teamId) {
      this.submitTeamMessage(text, attachments);
      return;
    }

    // T11 — 若 tab 還是預設名稱，用第一條訊息更新
    if (curTab && curTab.label === '新對話') {
      this.chatTabs.update(tabs => tabs.map(t => t.id === tabId ? { ...t, label: text.slice(0, 20) } : t));
    }
    const displayText = text + (attachments.length ? ` 📎×${attachments.length}` : '');
    const now = new Date().toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' });
    this.tabMessages(tabId, m => [...m, { role: 'user', text: displayText, time: now }]);
    const assistantMsg: ChatMessage = { role: 'assistant', text: '', isStreaming: true, time: now };
    this.tabMessages(tabId, m => [...m, assistantMsg]);
    this.shouldScroll = true;

    // Build event handler (shared between Claude and provider mode)
    const onEvent = (ev: any) => {
      if (ev.type === 'assistant' && ev.message?.content) {
        // tool is done once assistant starts replying
        this.tabMessages(tabId, msgs => msgs.map(m => m.isRunning ? { ...m, isRunning: false } : m));
        for (const block of ev.message.content) {
          if (block.type === 'text') {
            this.tabMessages(tabId, msgs => {
              const copy = [...msgs];
              copy[copy.length - 1] = { ...copy[copy.length - 1], text: copy[copy.length - 1].text + block.text };
              return copy;
            });
            if (tabId === this.activeChatId()) this.shouldScroll = true;
            if (block.text && (block.text.toLowerCase().includes('session limit') || block.text.toLowerCase().includes('rate limit') || block.text.toLowerCase().includes('limit · resets') || block.text.toLowerCase().includes('quota'))) {
              this.outOfQuota.set(true);
            }
          }
        }
      } else if (ev.type === 'text') {
        this.tabMessages(tabId, msgs => msgs.map(m => m.isRunning ? { ...m, isRunning: false } : m));
        this.tabMessages(tabId, msgs => {
          const copy = [...msgs];
          copy[copy.length - 1] = { ...copy[copy.length - 1], text: copy[copy.length - 1].text + ev.text };
          return copy;
        });
        if (tabId === this.activeChatId()) this.shouldScroll = true;
        if (ev.text && (ev.text.toLowerCase().includes('session limit') || ev.text.toLowerCase().includes('rate limit') || ev.text.toLowerCase().includes('limit · resets') || ev.text.toLowerCase().includes('quota'))) {
          this.outOfQuota.set(true);
        }
      } else if (ev.type === 'tool_use') {
        this.tabMessages(tabId, m => [...m, {
          role: 'tool', text: JSON.stringify(ev.input ?? {}, null, 2),
          toolName: ev.name, toolUseId: ev.id, isRunning: true, startTime: Date.now()
        }]);
        if (tabId === this.activeChatId()) this.shouldScroll = true;
      } else if (ev.type === 'user' && ev.message?.content) {
        for (const block of ev.message.content) {
          if (block.type === 'tool_result') {
            const res = typeof block.content === 'string'
              ? block.content
              : JSON.stringify(block.content);
            this.tabMessages(tabId, msgs => msgs.map(m =>
              m.toolUseId === block.tool_use_id
                ? { ...m, isRunning: false, result: res.slice(0, 3000) }
                : m
            ));
          }
        }
      } else if (ev.type === 'result') {
        const totalCost = ev.total_cost_usd ?? 0;
        const msgCost = Math.max(0, totalCost - this._prevCostUsd);
        this._prevCostUsd = totalCost;
        this.tabTokenUsage(tabId, {
          input: ev.usage?.input_tokens ?? 0,
          output: ev.usage?.output_tokens ?? 0,
          cost: totalCost,
        });
        // 標記本次訊息費用
        if (msgCost > 0) {
          this.tabMessages(tabId, msgs => {
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
      this.tabStopFns.delete(tabId);
      this.tabMessages(tabId, msgs =>
        msgs.map(m => m.isRunning ? { ...m, isRunning: false } : m)
      );
      this.tabMessages(tabId, msgs => {
        const copy = [...msgs];
        copy[copy.length - 1] = { ...copy[copy.length - 1], isStreaming: false };
        return copy;
      });
      this.tabStreaming(tabId, false);
      this.reload();
      this.triggerAutoTitle();
      if (tabId === this.activeChatId()) {
        this.shouldScroll = true;
        this.inputRef?.nativeElement?.focus();
      }
      (window as any).electronAPI?.notify('Claude 完成', text.slice(0, 60));
    };

    const onError = (err: any) => {
      const errStr = String(err);
      this.tabMessages(tabId, m => [...m, { role: 'error', text: errStr }]);
      this.tabStreaming(tabId, false);
      this.tabStopFns.delete(tabId);
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
      this.tabStopFns.set(tabId, this.claude.streamProviderChat(history, onEvent, onDone, onError));
    } else {
      this.tabStopFns.set(tabId, this.claude.streamChat(
        text, this.selectedAgent(), onEvent, onDone, onError, attachments,
        this.activeChat?.projectDir,  // 對話欄鎖定的目錄
        this.activeChat?.teamId,      // 綁定的團隊 ID
        this.activeChat?.clientId     // 傳遞 Tab 的 clientId，解決多 Tab 衝突
      ));
    }
  }

  submitTeamMessage(text: string, attachments: string[]) {
    const curTab = this.activeChat;
    if (!curTab || !curTab.teamId) return;
    const tabId = curTab.id;

    const team = this.teams().find(t => t.id === curTab.teamId);
    const teamName = team ? team.name : 'Auto Team';
    const now = new Date().toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' });

    // 1. 新增 User 訊息
    const displayText = text + (attachments.length ? ` 📎×${attachments.length}` : '');
    this.tabMessages(tabId, m => [...m, { role: 'user', text: displayText, time: now }]);
    this.shouldScroll = true;

    // 2. 啟動團隊討論
    let createdProjectMeta: any = null;

    const abortFn = this.claude.streamTeamChat(
      text,
      curTab.teamId,
      (ev) => {
        if (ev.type === 'agent_start') {
          const agentName = ev.agent;
          const msg: ChatMessage = {
            role: 'assistant',
            agentId: agentName,
            text: '',
            isStreaming: true,
            time: new Date().toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' })
          };
          this.tabMessages(tabId, m => [...m, msg]);
          if (tabId === this.activeChatId()) this.shouldScroll = true;
        } else if (ev.type === 'text') {
          this.tabMessages(tabId, msgs => {
            const copy = [...msgs];
            for (let i = copy.length - 1; i >= 0; i--) {
              if (copy[i].role === 'assistant' && copy[i].agentId === ev.agent) {
                copy[i] = { ...copy[i], text: copy[i].text + ev.text };
                break;
              }
            }
            return copy;
          });
          if (tabId === this.activeChatId()) this.shouldScroll = true;
        } else if (ev.type === 'agent_done') {
          this.tabMessages(tabId, msgs => {
            const copy = [...msgs];
            for (let i = copy.length - 1; i >= 0; i--) {
              if (copy[i].role === 'assistant' && copy[i].agentId === ev.agent) {
                copy[i] = { ...copy[i], isStreaming: false };
                break;
              }
            }
            return copy;
          });
          if (tabId === this.activeChatId()) this.shouldScroll = true;
        } else if (ev.type === 'project_created') {
          createdProjectMeta = {
            teamId: curTab.teamId!,
            projectName: ev.project_name,
            projectPath: ev.project_path,
            task: text
          };
          this.tabMessages(tabId, m => [...m, {
            role: 'system',
            text: `📁 專案資料夾 "${ev.project_name}" 建立成功。路徑: ${ev.project_path}`,
            time: new Date().toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' })
          }]);
          this.chatTabs.update(tabs => tabs.map(t => t.id === tabId ? { ...t, projectDir: ev.project_path } : t));
          if (tabId === this.activeChatId()) this.shouldScroll = true;
        } else if (ev.type === 'error') {
          this.tabMessages(tabId, m => [...m, { role: 'error', text: ev.text }]);
          this.tabStreaming(tabId, false);
          if (tabId === this.activeChatId()) this.shouldScroll = true;
        }
      },
      () => {
        this.tabStopFns.delete(tabId);
        this.tabStreaming(tabId, false);
        this.reload();

        if (createdProjectMeta) {
          this.tabMessages(tabId, m => [...m, {
            role: 'system',
            text: `📋 專案計畫已就緒，資料夾："${createdProjectMeta.projectName}"，是否同意並啟動團隊執行？`,
            time: new Date().toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' }),
            pendingExec: createdProjectMeta
          }]);
        }
        if (tabId === this.activeChatId()) {
          this.inputRef?.nativeElement?.focus();
          this.shouldScroll = true;
        }
      },
      (err) => {
        console.error('team chat error', err);
        this.tabStreaming(tabId, false);
        this.tabStopFns.delete(tabId);
        this.tabMessages(tabId, m => [...m, { role: 'error', text: `團隊討論異常斷開: ${err}` }]);
      },
      attachments,
      curTab.projectDir,
      curTab.clientId
    );

    this.tabStopFns.set(tabId, () => {
      abortFn();
      this.tabStreaming(tabId, false);
      this.tabStopFns.delete(tabId);
      this.tabMessages(tabId, msgs => msgs.map(m => m.isStreaming ? { ...m, isStreaming: false } : m));
    });
  }

  approveAndExecuteTeam(msg: ChatMessage, index: number) {
    if (msg.pendingExec) {
      msg.hasExecuted = true;
      this.executeTeamCodePhase(msg.pendingExec.teamId, msg.pendingExec.projectPath, msg.pendingExec.task);
    }
  }

  executeTeamCodePhase(teamId: string, projectPath: string, task: string) {
    const tabId = this.activeChatId();
    const team = this.teams().find(t => t.id === teamId);
    const teamName = team ? team.name : 'Auto Team';
    const now = new Date().toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' });

    const teamRun: TeamRun = {
      id: 'executing',
      team_id: teamId,
      name: teamName,
      task: task,
      status: 'running',
      steps: team ? team.members.map(m => ({ agent: m.agent, role: m.role, status: 'pending', output: '' })) : [],
      summary: ''
    };

    const execMsg: ChatMessage = {
      role: 'assistant',
      text: '🤖 各 Agent 啟動 Claude Code 進行實作中...',
      isStreaming: true,
      time: now,
      teamRun
    };
    this.tabMessages(tabId, m => [...m, execMsg]);
    this.shouldScroll = true;
    this.tabStreaming(tabId, true);

    const abortExec = this.claude.executeTeamTask(
      teamId,
      projectPath,
      task,
      (ev) => {
        if (ev.type === 'exec_start') {
          this.tabMessages(tabId, msgs => {
            const copy = [...msgs];
            const lastIdx = copy.length - 1;
            const lastMsg = copy[lastIdx];
            if (lastMsg && lastMsg.teamRun) {
              const tr = { ...lastMsg.teamRun };
              const steps = tr.steps.map(s => s.agent === ev.agent ? { ...s, status: 'running' as const } : s);
              copy[lastIdx] = { ...lastMsg, teamRun: { ...tr, steps } };
            }
            return copy;
          });
        } else if (ev.type === 'exec_text') {
          this.tabMessages(tabId, msgs => {
            const copy = [...msgs];
            const lastIdx = copy.length - 1;
            const lastMsg = copy[lastIdx];
            if (lastMsg && lastMsg.teamRun) {
              const tr = { ...lastMsg.teamRun };
              const steps = tr.steps.map(s => s.agent === ev.agent ? { ...s, output: s.output + ev.text } : s);
              copy[lastIdx] = { ...lastMsg, teamRun: { ...tr, steps } };
            }
            return copy;
          });
          if (tabId === this.activeChatId()) this.shouldScroll = true;
        } else if (ev.type === 'exec_done') {
          this.tabMessages(tabId, msgs => {
            const copy = [...msgs];
            const lastIdx = copy.length - 1;
            const lastMsg = copy[lastIdx];
            if (lastMsg && lastMsg.teamRun) {
              const tr = { ...lastMsg.teamRun };
              const steps = tr.steps.map(s => s.agent === ev.agent ? { ...s, status: 'done' as const } : s);
              copy[lastIdx] = { ...lastMsg, teamRun: { ...tr, steps } };
            }
            return copy;
          });
        } else if (ev.type === 'permission_request') {
          this.tabMessages(tabId, msgs => {
            const copy = [...msgs];
            const lastIdx = copy.length - 1;
            const lastMsg = copy[lastIdx];
            if (lastMsg && lastMsg.teamRun) {
              const tr = { ...lastMsg.teamRun };
              const steps = tr.steps.map(s => s.agent === ev.agent ? {
                ...s,
                status: 'pending_permission' as const,
                requestId: ev.request_id,
                command: ev.command
              } : s);
              copy[lastIdx] = { ...lastMsg, teamRun: { ...tr, steps } };
            }
            return copy;
          });
          if (tabId === this.activeChatId()) this.shouldScroll = true;
        } else if (ev.type === 'done') {
          this.tabMessages(tabId, msgs => {
            const copy = [...msgs];
            const lastIdx = copy.length - 1;
            const lastMsg = copy[lastIdx];
            if (lastMsg && lastMsg.teamRun) {
              copy[lastIdx] = {
                ...lastMsg,
                text: '✓ 協同實作全部完成！所有產出已存入專案目錄。',
                isStreaming: false,
                teamRun: { ...lastMsg.teamRun!, status: 'done' }
              };
            }
            return copy;
          });
          this.tabStreaming(tabId, false);
          this.tabStopFns.delete(tabId);
          this.reload();
          if (tabId === this.activeChatId()) setTimeout(() => this.scrollToBottom(), 100);
        } else if (ev.type === 'error') {
          this.tabMessages(tabId, msgs => {
            const copy = [...msgs];
            const lastIdx = copy.length - 1;
            const lastMsg = copy[lastIdx];
            if (lastMsg && lastMsg.teamRun) {
              copy[lastIdx] = {
                ...lastMsg,
                text: `⚠ 執行出錯: ${ev.text}`,
                isStreaming: false,
                teamRun: { ...lastMsg.teamRun!, status: 'error' }
              };
            }
            return copy;
          });
          this.tabStreaming(tabId, false);
          this.tabStopFns.delete(tabId);
        }
      },
      () => {
        this.tabStreaming(tabId, false);
        this.tabStopFns.delete(tabId);
      },
      (err) => {
        console.error('exec error', err);
        this.tabStreaming(tabId, false);
        this.tabStopFns.delete(tabId);
        this.tabMessages(tabId, msgs => {
          const copy = [...msgs];
          const lastIdx = copy.length - 1;
          const lastMsg = copy[lastIdx];
          if (lastMsg && lastMsg.teamRun) {
            copy[lastIdx] = {
              ...lastMsg,
              text: `⚠ 執行異常中斷: ${err}`,
              isStreaming: false,
              teamRun: { ...lastMsg.teamRun!, status: 'error' }
            };
          }
          return copy;
        });
      },
      this.activeChat?.clientId
    );

    this.tabStopFns.set(tabId, () => {
      abortExec();
      this.tabStreaming(tabId, false);
      this.tabStopFns.delete(tabId);
      this.tabMessages(tabId, msgs => {
        const copy = [...msgs];
        const lastIdx = copy.length - 1;
        const lastMsg = copy[lastIdx];
        if (lastMsg && lastMsg.teamRun) {
          copy[lastIdx] = {
            ...lastMsg,
            text: `⏹ 實作已被使用者停止。`,
            isStreaming: false,
            teamRun: { ...lastMsg.teamRun!, status: 'cancelled' }
          };
        }
        return copy;
      });
    });
  }

  handleUserAuthorize(requestId: string, agent: string, decision: 'approve' | 'reject') {
    this.claude.authorizeTeamTask(requestId, decision).subscribe({
      next: () => {
        this.messages.update(msgs => {
          const copy = [...msgs];
          for (let i = copy.length - 1; i >= 0; i--) {
            const msg = copy[i];
            if (msg.teamRun) {
              const steps = msg.teamRun.steps.map(s =>
                s.requestId === requestId ? {
                  ...s,
                  status: (decision === 'approve' ? 'running' as const : 'error' as const)
                } : s
              );
              copy[i] = { ...msg, teamRun: { ...msg.teamRun, steps } };
              break;
            }
          }
          return copy;
        });
      },
      error: (e) => {
        this.showToast(`授權請求發送失敗: ${e.message ?? e}`, 'error');
      }
    });
  }

  onInput() {
    const val = this.inputText;
    // 草稿持久化
    if (val) localStorage.setItem('claude_input_draft', val);
    else localStorage.removeItem('claude_input_draft');
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
        this.messages.update(m => [...m, {
          role: 'system', text:
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

  // T09 — claude doctor: extracted into components/diagnostics-panel (Phase 2)
  // Agency Agents importer: extracted into components/agency-import-panel (Phase 2)

  // T10 — MCP 管理
  mcpList = signal<string>('');
  mcpLoading = signal(false);

  loadMcp() {
    this.mcpLoading.set(true);
    this.claude.runCliCommand(['mcp', 'list']).subscribe({
      next: out => {
        this.mcpList.set(out || '（無已安裝的 MCP）');
        this.parseMcpList(out || '');
        this.mcpLoading.set(false);
      },
      error: () => {
        this.mcpList.set('[無法取得清單]');
        this.mcpLoading.set(false);
      },
    });
    this.loadMcpServerDefs();
  }

  // ── MCP server 定義單一來源（同步到 Claude／Codex 兩邊 CLI）─────────────
  // 跟上面 mcpList/parseMcpList（parse `claude mcp list` 輸出，只反映
  // Claude 那邊看得到什麼）是不同的資料來源：這裡是 app 自己記錄、新增/
  // 刪除時會同步推到兩邊 CLI 的那份（backend/mcp_sync.py）。
  mcpServerDefs = signal<Record<string, McpServerDef>>({});
  mcpServerEditorOpen = signal(false);
  mcpServerEditorData = signal<McpServerDef>({ type: 'stdio' });
  mcpServerEditorName = '';
  mcpServerEditorArgsText = '';
  mcpServerEditorEnvText = '';
  mcpServerEditorHeadersText = '';
  mcpServerSaving = signal(false);

  loadMcpServerDefs() {
    this.claude.listMcpServers().subscribe({
      next: defs => this.mcpServerDefs.set(defs),
      error: () => {},
    });
  }

  // objectKeys: extracted into components/mcp-panel (Phase 2) as a local
  // helper — its only caller was the mcp-view template.

  openMcpServerEditor() {
    this.mcpServerEditorName = '';
    this.mcpServerEditorArgsText = '';
    this.mcpServerEditorEnvText = '';
    this.mcpServerEditorHeadersText = '';
    this.mcpServerEditorData.set({ type: 'stdio' });
    this.mcpServerEditorOpen.set(true);
  }

  private _parseKvLines(text: string): Record<string, string> {
    const out: Record<string, string> = {};
    for (const line of text.split('\n')) {
      const idx = line.indexOf('=');
      if (idx > 0) out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
    }
    return out;
  }

  saveMcpServerEditor() {
    const name = this.mcpServerEditorName.trim();
    if (!name) { this.showToast('請填寫名稱', 'error'); return; }
    const d = this.mcpServerEditorData();
    const payload: McpServerDef = d.type === 'http'
      ? { type: 'http', url: (d.url || '').trim(), headers: this._parseKvLines(this.mcpServerEditorHeadersText) }
      : {
          type: 'stdio',
          command: (d.command || '').trim(),
          args: this.mcpServerEditorArgsText.split('\n').map(s => s.trim()).filter(Boolean),
          env: this._parseKvLines(this.mcpServerEditorEnvText),
        };
    if (d.type === 'stdio' && !payload.command) { this.showToast('請填寫執行指令', 'error'); return; }
    if (d.type === 'http' && !payload.url) { this.showToast('請填寫 URL', 'error'); return; }

    this.mcpServerSaving.set(true);
    this.claude.createMcpServer(name, payload).subscribe({
      next: () => {
        this.mcpServerSaving.set(false);
        this.mcpServerEditorOpen.set(false);
        this.loadMcpServerDefs();
      },
      error: (e) => {
        this.mcpServerSaving.set(false);
        this.showToast(e.error?.error || '新增 MCP Server 失敗', 'error');
      },
    });
  }

  deleteMcpServerDef(name: string) {
    if (!confirm(`確定要刪除 MCP Server「${name}」？會同時從 Claude／Codex 兩邊移除。`)) return;
    this.claude.deleteMcpServer(name).subscribe({
      next: () => this.loadMcpServerDefs(),
      error: (e) => this.showToast(e.error?.error || '刪除失敗', 'error'),
    });
  }

  // ── 引擎可用性偵測（已安裝／已登入，不含用量數字——兩邊 CLI 都沒有可
  // 腳本化的用量查詢管道）────────────────────────────────────────────────
  engineStatus = signal<Record<string, EngineAvailability>>({});
  engineUserKind = computed<'loading' | 'both' | 'claude' | 'codex' | 'none'>(() => {
    const status = this.engineStatus();
    if (!status['claude'] && !status['codex']) return 'loading';
    const claude = status['claude']?.available === true;
    const codex = status['codex']?.available === true;
    if (claude && codex) return 'both';
    if (claude) return 'claude';
    if (codex) return 'codex';
    return 'none';
  });
  engineUserLabel = computed(() => ({
    loading: '正在檢查引擎…',
    both: 'Claude Code + Codex',
    claude: 'Claude Code 使用者',
    codex: 'Codex 使用者',
    none: '尚無可用引擎',
  })[this.engineUserKind()]);
  engineUserAvatar = computed(() => ({
    loading: '…', both: 'AI', claude: 'C', codex: 'CX', none: '!',
  })[this.engineUserKind()]);

  // 執行引擎範圍（後端權威，database.get_engine_mode()）——'claude'/'codex'
  // 時鎖定單一引擎，agent 自己的 engine: 覆寫在執行期完全不生效；'both'
  // 時維持既有行為。啟動時在 ngOnInit() 填一次，openSettings() 開啟時會
  // 再刷新一次。
  engineMode = signal<'claude' | 'codex' | 'both'>('both');

  private readonly ENGINE_LABEL: Record<string, string> = { claude: 'Claude Code CLI', codex: 'OpenAI Codex CLI' };
  protected readonly ENGINE_REASON_LABEL: Record<string, string> = {
    not_installed: '未安裝', not_logged_in: '未登入',
    check_timeout: '狀態檢查逾時', unexpected_output: '狀態檢查失敗',
  };

  loadEngineStatus(force = false) {
    this.claude.getEngineStatus(force).subscribe({
      next: status => {
        this.engineStatus.set(status);
        this._autoCorrectGlobalEngine(status);
        this._warnIfLockedEngineUnavailable(status);
      },
      error: () => {},   // 拿不到狀態就保持現狀（既有 select 行為不變），不擋住 UI
    });
  }

  engineOptionDisabled(name: 'claude' | 'codex'): boolean {
    const s = this.engineStatus()[name];
    return !!s && !s.available;   // 還沒拿到狀態時（{} 空物件）不擋，避免載入瞬間全部變成 disabled
  }

  engineOptionLabel(name: 'claude' | 'codex'): string {
    const s = this.engineStatus()[name];
    const base = this.ENGINE_LABEL[name];
    if (!s || s.available) return base;
    const reason = this.ENGINE_REASON_LABEL[s.reason] || '不可用';
    return `${base}（${reason}）`;
  }

  private _autoCorrectGlobalEngine(status: Record<string, EngineAvailability>) {
    const current = this.settings.get().agentEngine;
    if (status[current]?.available !== false) return;   // 可用或狀態未知都不動
    const other = current === 'claude' ? 'codex' : 'claude';
    if (!status[other]?.available) return;               // 兩邊都不可用，UI 端不硬猜，交給執行期防護網處理
    this.settings.save({ agentEngine: other as 'claude' | 'codex' });
    if (this.settingsOpen()) this.settingsForm.agentEngine = other as 'claude' | 'codex';
    this.showToast(
      `全域執行引擎「${this.ENGINE_LABEL[current]}」目前無法使用，已自動切換為「${this.ENGINE_LABEL[other]}」。`,
      'info', 4000,
    );
  }

  private _warnIfLockedEngineUnavailable(status: Record<string, EngineAvailability>) {
    const mode = this.engineMode();
    if (mode !== 'claude' && mode !== 'codex') return;   // 'both' 沒有鎖定，不用管
    if (status[mode]?.available !== false) return;
    // 刻意不自動切換範圍——使用者鎖定範圍是刻意的硬限制，默默幫他改回
    // 「兩者都開放」等於把限制取消掉，只提示、讓使用者自己去 Settings 處理。
    this.showToast(
      `已鎖定僅使用「${this.ENGINE_LABEL[mode]}」，但目前無法使用，請至 Settings 安裝／登入，或切換為「兩者都開放」。`,
      'error', 5000,
    );
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

  // 永久綁定：agent → skills（源自 frontmatter，透過後端 API 讀寫）
  getPermSkills(agentId: string): string[] {
    const id = agentId.replace(/^@/, '');
    return this.agents().find(a => a.id === id)?.skills ?? [];
  }
  isSkillPermForAgent(agentId: string, skillId: string): boolean {
    return this.getPermSkills(agentId).includes(skillId);
  }
  toggleSkillPermForAgent(agentId: string, skillId: string) {
    const id = agentId.replace(/^@/, '');
    const agent = this.agents().find(a => a.id === id);
    if (!agent) return;
    const cur = agent.skills ?? [];
    const next = cur.includes(skillId) ? cur.filter(s => s !== skillId) : [...cur, skillId];
    this.claude.updateAgent(id, { skills: next }).subscribe(() => {
      this.claude.getAgents().subscribe(a => this.agents.set(a));
      this.loadResourceSyncStatus();
    });
  }

  // 永久綁定：agent → MCPs（源自 frontmatter）
  getPermMcps(agentId: string): string[] {
    const id = agentId.replace(/^@/, '');
    return this.agents().find(a => a.id === id)?.mcp ?? [];
  }
  isMcpPermForAgent(agentId: string, mcpName: string): boolean {
    return this.getPermMcps(agentId).includes(mcpName);
  }
  toggleMcpPermForAgent(agentId: string, mcpName: string) {
    const id = agentId.replace(/^@/, '');
    const agent = this.agents().find(a => a.id === id);
    if (!agent) return;
    const cur = agent.mcp ?? [];
    const next = cur.includes(mcpName) ? cur.filter(m => m !== mcpName) : [...cur, mcpName];
    this.claude.updateAgent(id, { mcp: next }).subscribe(() => {
      this.claude.getAgents().subscribe(a => this.agents.set(a));
      this.loadResourceSyncStatus();
    });
  }

  // 一次性：綁定到目前 tab
  private activeTabField<K extends 'sessionSkills' | 'sessionMcps'>(key: K): string[] {
    return this.chatTabs().find(t => t.id === this.activeChatId())?.[key] ?? [];
  }
  isSkillInTab(skillId: string): boolean { return this.activeTabField('sessionSkills').includes(skillId); }
  isMcpInTab(mcpName: string): boolean { return this.activeTabField('sessionMcps').includes(mcpName); }

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
    const perm = this.getPermSkills(agentId);
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

      // Auto-detect type from URL
      const urlL = url.toLowerCase();
      let mcpType: McpType = 'external';
      if (urlL.startsWith('docker://') || urlL.includes('docker')) mcpType = 'docker';
      else if (urlL.includes('localhost') || urlL.includes('127.0.0.1')) mcpType = 'local-http';

      // Extract port
      const portMatch = url.match(/:(\d+)/);
      const port = portMatch ? portMatch[1] : undefined;

      servers.push({
        id, name, url, status, authorized, description,
        mcpType, port,
        dockerized: mcpType === 'docker',
        tools,
      });
    }

    this.mcpServers.set(servers);
  }

  removeMcp(name: string) {
    if (!confirm(`確定移除 MCP "${name}"？`)) return;
    this.claude.runCliCommand(['mcp', 'remove', name]).subscribe(() => this.loadMcp());
  }

  loadSession(s: Session) {
    const activeIsEmpty = (this.activeChat?.messages?.length ?? 0) === 0;

    if (activeIsEmpty) {
      // 如果當前 activeChat 是空白的，直接在當前 activeChat 中載入
      // 同時，將其他空白 Tab 都關閉
      const currentActiveId = this.activeChatId();
      this.chatTabs.update(tabs => tabs.filter(t => t.id === currentActiveId || (t.messages?.length ?? 0) > 0));
      this.saveCurrentTab();
    } else {
      // 如果當前 activeChat 有內容，我們必須開一個新 Tab 來載入歷史對話
      // 同時在開新 Tab 之前，將所有空白的 Tab 都關閉
      this.chatTabs.update(tabs => tabs.filter(t => (t.messages?.length ?? 0) > 0));
      if (this.chatTabs().length < 4) {
        this.addChatTab();
      }
      // 已達 4 個分頁上限時，就地取代目前分頁的對話。這裡刻意不呼叫
      // saveCurrentTab() —— 舊內容本來就要被取代掉，先存進去只會造成
      // 「畫面顯示新對話、chatTabs 裡卻還是舊對話」的分歧，切走再切回來
      // 舊對話又跑出來蓋掉剛載入的內容。改成下面訊息載入完成後才同步。
    }

    const id = this.activeChatId();
    this.chatTabs.update(tabs => tabs.map(t =>
      t.id === id ? { ...t, label: s.title.slice(0, 20) } : t
    ));
    // 先顯示載入中，再取得完整對話
    this.messages.set([{ role: 'system', text: '載入歷史對話中…' }]);
    this.claude.resumeSession(s.id).subscribe();
    this.claude.getSessionMessages(s.id).subscribe({
      next: res => {
        this.messages.set(res.messages);
        this.saveCurrentTab();
        // shouldScroll + ngAfterViewChecked (用在 sendMessage 等由 DOM 事件
        // 觸發的同步流程) 在這裡不可靠：這個 callback 是 HTTP 訂閱的非同步
        // 回呼，不是由 (click)/(keydown) 這類會自動觸發完整 change
        // detection tick 的 DOM 事件觸發——這個 app 沒有裝 zone.js，訊號寫入
        // （messages.set）只會就地更新用到它的 DOM 節點，不會連帶跑一次完整
        // 的 lifecycle hook 檢查，所以 ngAfterViewChecked 不會被叫到，
        // shouldScroll 這個旗標永遠不會被消費掉。改成訊息渲染完（下一個
        // macrotask）後直接呼叫 scrollIntoView，不依賴這個機制。
        setTimeout(() => this.chatEnd?.nativeElement?.scrollIntoView({ behavior: 'smooth' }), 0);
      },
      error: () => { this.messages.set([{ role: 'system', text: '無法載入歷史對話' }]); this.saveCurrentTab(); },
    });
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
    this.chatTabs.update(tabs => tabs.map(t => {
      if (t.id === tabId) {
        let label = t.label;
        const currentAgentName = t.selectedAgent ? (this.agents().find(a => a.id === t.selectedAgent)?.name ?? t.selectedAgent) : '';
        const isDefaultOrAgentLabel = !t.label || t.label === '新對話' || (currentAgentName && t.label === currentAgentName);

        if (isDefaultOrAgentLabel) {
          if (agentId) {
            const newAgentObj = this.agents().find(a => a.id === agentId);
            label = newAgentObj ? newAgentObj.name : agentId;
          } else {
            label = '新對話';
          }
        }
        return { ...t, selectedAgent: agentId, label };
      }
      return t;
    }));
    if (tabId === this.activeChatId()) {
      this.selectedAgent.set(agentId);
    }
  }
  isSkillLinkedToActiveAgent(skillId: string): boolean {
    const agentId = this.selectedAgent();
    if (!agentId) return false;
    return this.getLinkedSkills(agentId).includes(skillId);
  }

  // 此 Skill 是否在 activeAgent 的 frontmatter skills[] 中（P1-F4）
  isSkillInActiveAgentFrontmatter(skillId: string): boolean {
    const agentId = this.selectedAgent();
    if (!agentId) return false;
    return this.agents().find(a => a.id === agentId.replace(/^@/, ''))?.skills?.includes(skillId) ?? false;
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

  // isMcpRequiredByActiveAgent: extracted into requiredMcpNames computed()
  // above (Phase 2) — same P1-F6 frontmatter-mcp[] check, precomputed as a
  // Set for the mcp-panel component's per-card lookup.

}
