import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { McpFacade } from './mcp-facade.service';

describe('McpFacade', () => {
  let facade: McpFacade;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [McpFacade, provideHttpClient(), provideHttpClientTesting()],
    });
    facade = TestBed.inject(McpFacade);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('loads the CLI list and server definitions into facade state', () => {
    facade.loadMcp();

    const cli = http.expectOne(request => request.url.endsWith('/api/cli'));
    expect(cli.request.body).toEqual({ args: ['mcp', 'list'] });
    cli.flush({ output: 'server-a' });

    const defs = http.expectOne(request => request.url.endsWith('/api/mcp-servers'));
    defs.flush({ 'server-a': { type: 'stdio', command: 'npx' } });

    expect(facade.mcpList()).toBe('server-a');
    expect(facade.serverDefs()).toEqual({ 'server-a': { type: 'stdio', command: 'npx' } });
    expect(facade.mcpLoading()).toBe(false);
  });

  it('resets the editor and builds normalized stdio and HTTP payloads', () => {
    facade.openEditor();
    expect(facade.editorOpen()).toBe(true);
    expect(facade.editorData()).toEqual({ type: 'stdio' });

    expect(facade.buildPayload({ type: 'stdio', command: ' npx ' }, ' pkg\n --yes ', ' TOKEN = abc ')).toEqual({
      type: 'stdio',
      command: 'npx',
      args: ['pkg', '--yes'],
      env: { TOKEN: 'abc' },
    });
    expect(facade.buildPayload({ type: 'http', url: ' https://example.test ' }, '', '', 'Authorization = Bearer token')).toEqual({
      type: 'http',
      url: 'https://example.test',
      headers: { Authorization: 'Bearer token' },
    });
  });

  it('tracks saving state while creating a server', () => {
    let completed = false;
    facade.createServer('demo', { type: 'stdio', command: 'npx' }).subscribe(() => completed = true);
    expect(facade.saving()).toBe(true);

    const request = http.expectOne(request => request.url.endsWith('/api/mcp-servers'));
    expect(request.request.body).toEqual({ name: 'demo', type: 'stdio', command: 'npx' });
    request.flush({ ok: true, name: 'demo', type: 'stdio', command: 'npx' });

    expect(completed).toBe(true);
    expect(facade.saving()).toBe(false);
  });
});
