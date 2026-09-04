import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { App } from './app';

describe('App', () => {
  let http: HttpTestingController;

  beforeEach(async () => {
    localStorage.setItem('claude_onboarding_done', '1');
    Element.prototype.scrollIntoView = vi.fn();
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    document.body.style.cursor = '';
    localStorage.removeItem('claude_onboarding_done');
  });

  function flushInitialRequests(): void {
    for (const req of http.match(() => true)) {
      const path = new URL(req.request.urlWithParams, 'http://localhost').pathname;
      const body: any = path.endsWith('/sessions')
        ? { items: [], has_more: false }
        : path.endsWith('/agents') ||
            path.endsWith('/skills') ||
            path.endsWith('/schedules') ||
            path.endsWith('/souls') ||
            path.endsWith('/profiles') ||
            path.endsWith('/teams')
          ? []
          : path.endsWith('/soul')
            ? { content: '' }
            : path.endsWith('/memory') ||
                path.endsWith('/mcp-local-config') ||
                path.endsWith('/mcp-servers') ||
                path.endsWith('/engines/status')
              ? {}
              : path.endsWith('/resource-sync')
                ? {
                    agents: { missing_in_codex: [], outdated: [], conflicts: [] },
                    skills: { missing_in_codex: [], outdated: [], conflicts: [] },
                  }
                : path.endsWith('/config')
                  ? { engineMode: 'both' }
                  : path.endsWith('/codex/models')
                    ? []
                    : path.endsWith('/usage/codex')
                      ? null
                      : path.endsWith('/usage')
                        ? { five_hour: {}, seven_day: {} }
                        : {};
      req.flush(body);
    }
  }

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    flushInitialRequests();
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  }, 15_000);

  it('should render the application shell', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    flushInitialRequests();
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.logo')?.textContent).toContain('Agent 桌面版');
    expect(compiled.querySelector('.sidebar')).not.toBeNull();
    expect(compiled.querySelector('.chat-input')).not.toBeNull();
  });

  it('should show the session engine switch only when both engines are available', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    flushInitialRequests();
    const app = fixture.componentInstance;

    app.engineMode.set('both');
    app.engineStatus.set({
      claude: { available: true, installed: true, loggedIn: true, reason: '' },
      codex: { available: true, installed: true, loggedIn: true, reason: '' },
    });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.session-engine-toggle')).not.toBeNull();

    app.engineMode.set('claude');
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.session-engine-toggle')).toBeNull();
  });

  it('should reload session history with the selected Codex engine', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    flushInitialRequests();
    const app = fixture.componentInstance;

    app.engineMode.set('both');
    app.engineStatus.set({
      claude: { available: true, installed: true, loggedIn: true, reason: '' },
      codex: { available: true, installed: true, loggedIn: true, reason: '' },
    });
    fixture.detectChanges();

    const buttons = Array.from(
      fixture.nativeElement.querySelectorAll('.session-engine-toggle button'),
    ) as HTMLButtonElement[];
    buttons.find((button) => button.textContent?.trim() === 'Codex')?.click();

    const req = http.expectOne((request) => {
      const url = new URL(request.urlWithParams, 'http://localhost');
      return url.pathname.endsWith('/sessions') && url.searchParams.get('engine') === 'codex';
    });
    req.flush({ items: [], has_more: false });
  });

  it('should switch runtime engine and resume with Codex when loading Codex history', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    flushInitialRequests();
    const app = fixture.componentInstance;

    app.engineMode.set('both');
    app.loadSession({
      id: '019fabcd-0000-7000-8000-000000000006',
      title: 'Codex History',
      mtime: 1,
      engine: 'codex',
    });

    expect(app.agentEngine()).toBe('codex');

    const resumeReq = http.expectOne((request) => {
      const url = new URL(request.urlWithParams, 'http://localhost');
      return url.pathname.endsWith('/sessions/resume');
    });
    expect(resumeReq.request.body.engine).toBe('codex');
    resumeReq.flush({ ok: true, engine: 'codex' });

    const messagesReq = http.expectOne((request) => {
      const url = new URL(request.urlWithParams, 'http://localhost');
      return url.pathname.endsWith('/sessions/019fabcd-0000-7000-8000-000000000006/messages');
    });
    messagesReq.flush({ messages: [{ role: 'user', text: 'hello from codex' }] });
  });

  it('should prepare assistant markdown for speech output', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    flushInitialRequests();
    const app = fixture.componentInstance as any;

    const spoken = app.textForSpeech(
      '## Result\nUse `npm test`.\n```ts\nconsole.log("skip");\n```\n[Docs](https://example.com)',
    );

    expect(spoken).toBe('Result Use npm test. Docs');
  });

  it('should precompute MCP links from skills, permanent bindings, and the active tab', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    flushInitialRequests();
    const app = fixture.componentInstance;

    app.agents.set([
      {
        id: 'writer',
        name: 'Writer',
        description: '',
        skills: ['google-agents-cli-publish'],
        mcp: ['permanent-mcp'],
      } as any,
    ]);
    app.selectedAgent.set('writer');
    app.chatTabs.update((tabs) =>
      tabs.map((tab) =>
        tab.id === app.activeChatId() ? { ...tab, sessionMcps: ['session-mcp'] } : tab,
      ),
    );

    expect(app.linkedMcpNames()).toEqual(
      new Set([
        'session-mcp',
        'permanent-mcp',
        'claude.ai Google Calendar',
        'claude.ai Google Drive',
      ]),
    );
  });

  it('should normalize Claude Code reset timestamps', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    flushInitialRequests();
    const app = fixture.componentInstance;

    expect(app.claudeResetMillis('2026-07-16T08:00:00Z')).toBe(Date.parse('2026-07-16T08:00:00Z'));
    expect(app.claudeResetMillis(1784188800)).toBe(1784188800000);
    expect(app.claudeResetMillis(1784188800000)).toBe(1784188800000);
    expect(app.claudeResetMillis('')).toBeNull();
    expect(app.claudeResetMillis('not-a-date')).toBeNull();
  });

  it('should render narrow, consistent resize handles for the main boundaries', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    flushInitialRequests();
    fixture.detectChanges();

    for (const selector of ['.sidebar-resize', '.right-resize', '.input-resize']) {
      const handle = fixture.nativeElement.querySelector(selector) as HTMLElement | null;
      expect(handle).not.toBeNull();
      expect(handle?.querySelector(`${selector}-track`)).not.toBeNull();
      expect(handle?.querySelector(`${selector}-grip`)).not.toBeNull();
    }
  });

  it('should keep sidebar, right panel, and input resizing within their bounds', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    flushInitialRequests();
    const app = fixture.componentInstance;

    app.sidebarWidth.set(300);
    app.onResizeStart(new MouseEvent('mousedown', { clientX: 100 }));
    app.onMouseMove(new MouseEvent('mousemove', { clientX: 260 }));
    expect(app.sidebarWidth()).toBe(460);
    app.onMouseMove(new MouseEvent('mousemove', { clientX: -1000 }));
    expect(app.sidebarWidth()).toBe(200);
    app.onMouseUp();

    app.rightWidth.set(400);
    app.onRightResizeStart(new MouseEvent('mousedown', { clientX: 500 }));
    app.onMouseMove(new MouseEvent('mousemove', { clientX: 1000 }));
    expect(app.rightWidth()).toBe(280);
    app.onMouseUp();

    app.inputHeight.set(240);
    app.onInputResizeStart(new MouseEvent('mousedown', { clientY: 100 }));
    app.onMouseMove(new MouseEvent('mousemove', { clientY: 500 }));
    expect(app.inputHeight()).toBe(100);
    app.onMouseUp();
    expect(document.body.style.cursor).toBe('');
  });
});
