import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { App } from './app';

describe('App', () => {
  let http: HttpTestingController;

  beforeEach(async () => {
    localStorage.setItem('claude_onboarding_done', '1');
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    localStorage.removeItem('claude_onboarding_done');
  });

  function flushInitialRequests(): void {
    for (const req of http.match(() => true)) {
      const path = new URL(req.request.urlWithParams, 'http://localhost').pathname;
      const body: any = path.endsWith('/sessions') ? { items: [], has_more: false }
        : path.endsWith('/agents') || path.endsWith('/skills') || path.endsWith('/schedules')
          || path.endsWith('/souls') || path.endsWith('/profiles') || path.endsWith('/teams') ? []
        : path.endsWith('/soul') ? { content: '' }
        : path.endsWith('/memory') || path.endsWith('/mcp-local-config')
          || path.endsWith('/mcp-servers') || path.endsWith('/engines/status') ? {}
        : path.endsWith('/resource-sync') ? {
            agents: { missing_in_codex: [], outdated: [], conflicts: [] },
            skills: { missing_in_codex: [], outdated: [], conflicts: [] },
          }
        : path.endsWith('/config') ? { engineMode: 'both' }
        : path.endsWith('/usage/codex') ? null
        : path.endsWith('/usage') ? { five_hour: {}, seven_day: {} }
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
  });

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

  it('should prepare assistant markdown for speech output', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    flushInitialRequests();
    const app = fixture.componentInstance as any;

    const spoken = app.textForSpeech('## Result\nUse `npm test`.\n```ts\nconsole.log("skip");\n```\n[Docs](https://example.com)');

    expect(spoken).toBe('Result Use npm test. Docs');
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
});
