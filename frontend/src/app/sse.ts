export type SseEvent = Record<string, unknown>;

export interface SseCallbacks<T extends SseEvent = SseEvent> {
  onEvent: (event: T) => void;
  onDone: () => void;
  onError: (error: unknown) => void;
}

/**
 * Starts a fetch-based SSE request and returns a cancellation function.
 * The parser follows the SSE data-line rules and tolerates chunk boundaries.
 */
export function startSseStream<T extends SseEvent>(
  input: RequestInfo | URL,
  init: RequestInit,
  callbacks: SseCallbacks<T>,
): () => void {
  const controller = new AbortController();
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;

  const run = async (): Promise<void> => {
    try {
      const response = await fetch(input, { ...init, signal: controller.signal });
      if (!response.ok) throw new Error(`SSE request failed (${response.status})`);
      if (!response.body) throw new Error('SSE response has no body');

      reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() ?? '';
        for (const frame of frames) emitFrame(frame, callbacks.onEvent);
        if (done) break;
      }
      if (buffer.trim()) emitFrame(buffer, callbacks.onEvent);
      if (!controller.signal.aborted) callbacks.onDone();
    } catch (error) {
      if (!controller.signal.aborted && (error as { name?: string })?.name !== 'AbortError') {
        callbacks.onError(error);
      }
    }
  };

  void run();
  return () => {
    controller.abort();
    void reader?.cancel().catch(() => undefined);
  };
}

function emitFrame<T extends SseEvent>(frame: string, onEvent: (event: T) => void): void {
  const data = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).replace(/^ /, ''))
    .join('\n')
    .trim();
  if (!data || data === '[DONE]') return;

  try {
    onEvent(JSON.parse(data) as T);
  } catch {
    // Ignore malformed/incomplete application events; the stream can continue.
  }
}
