import { Injectable, signal } from '@angular/core';
import { ClaudeService, Session } from './claude.service';

type SessionEngine = 'claude' | 'codex';

/** Owns session-history query state so App only coordinates UI behavior. */
@Injectable({ providedIn: 'root' })
export class SessionFacade {
  readonly sessions = signal<Session[]>([]);
  readonly hasMore = signal(false);
  readonly engineFilter = signal<SessionEngine>('claude');

  private offset = 0;

  constructor(private readonly claude: ClaudeService) {}

  search(query: string): void {
    this.offset = 0;
    this.claude.getSessions(query, 0, this.engineFilter()).subscribe({
      next: (result) => {
        this.sessions.set(result.items);
        this.hasMore.set(result.has_more);
      },
      error: () => {
        this.sessions.set([]);
        this.hasMore.set(false);
      },
    });
  }

  loadMore(query: string): void {
    if (!this.hasMore()) return;
    const nextOffset = this.offset + 30;
    this.claude.getSessions(query, nextOffset, this.engineFilter()).subscribe({
      next: (result) => {
        this.offset = nextOffset;
        this.sessions.update((current) => [...current, ...result.items]);
        this.hasMore.set(result.has_more);
      },
    });
  }

  setEngine(engine: SessionEngine, query: string): void {
    if (this.engineFilter() === engine) return;
    this.engineFilter.set(engine);
    this.search(query);
  }

  refresh(query: string): void {
    this.search(query);
  }
}
