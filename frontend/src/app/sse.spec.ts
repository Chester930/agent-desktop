import { startSseStream } from './sse';

function responseFromChunks(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return { ok: status >= 200 && status < 300, status, body } as Response;
}

describe('startSseStream', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('reassembles events split across network chunks and data lines', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          responseFromChunks(['data: {"text":"hel', 'lo"}\n\ndata: {"text":"world"}\n\n']),
        ),
    );
    const events: Record<string, unknown>[] = [];
    let done = false;

    startSseStream(
      '/stream',
      {},
      {
        onEvent: (event) => events.push(event),
        onDone: () => {
          done = true;
        },
        onError: (error) => {
          throw error;
        },
      },
    );

    await vi.waitFor(() => expect(done).toBe(true));
    expect(events).toEqual([{ text: 'hello' }, { text: 'world' }]);
  });

  it('reports HTTP errors without invoking the completion callback', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(responseFromChunks([], 503)));
    const errors: unknown[] = [];
    let done = false;

    startSseStream(
      '/stream',
      {},
      {
        onEvent: () => undefined,
        onDone: () => {
          done = true;
        },
        onError: (error) => errors.push(error),
      },
    );

    await vi.waitFor(() => expect(errors).toHaveLength(1));
    expect((errors[0] as Error).message).toContain('503');
    expect(done).toBe(false);
  });
});
