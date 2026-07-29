# SDD: Codex Feature Parity

## Goal

Reduce Claude-only behavior in the desktop app so Codex-only users can use the same primary workflows where the Codex CLI supports them, and get clear UI behavior where it does not.

## Scope

1. Session resume state must remember both session id and engine.
2. Loading a Codex history item must resume with Codex, not whichever engine is currently selected.
3. Codex sessions must support message reading, auto title, and skill generation where possible.
4. Schedule execution, Telegram, and LINE must run through the configured engine instead of always invoking Claude.
5. User-facing help and empty states should avoid implying `~/.claude` is the only source of truth when registry paths are available.

## Non-Goals

- Do not rewrite Codex private SQLite databases.
- Do not implement Claude-style interactive tool permission approval for Codex; Codex continues to use sandbox modes.
- Do not remove the legacy `claudeHome` config key in this change. It remains the app data root for backward compatibility.

## Acceptance Criteria

- `POST /api/sessions/resume` accepts and persists `engine`.
- Chat continuation never resumes a session using a different engine than the selected runtime engine.
- Codex history load switches the runtime engine to Codex when `engineMode` is `both`.
- Codex auto-title reads Codex JSONL messages and uses Codex when the runtime policy selects Codex.
- Skill generation can consume either Claude or Codex session messages.
- Schedules, Telegram, and LINE use the configured engine mode/default engine and fall back through the existing availability gate.
- UI text names registry/data paths instead of hard-coding only `~/.claude` in Codex-relevant panels.

## Implementation Status

- Done: active session state now stores `{id, engine}` and resume/continuation paths only reuse matching-engine sessions.
- Done: Codex history loading switches the active runtime engine and posts `engine` to `/api/sessions/resume`.
- Done: session messages, auto-title, and skill generation share an engine-aware session parser.
- Done: natural-language cron parsing, schedule execution, Telegram, and LINE bot replies use the configured automation engine path.
- Done: Codex-relevant UI help and empty states now describe the shared data root/registry instead of Claude-only paths.

## Verification

- `python -m py_compile backend\main.py`
- `python -m pytest`
- `npm test` from `frontend/`
- `npm run build` from `frontend/`
- `git diff --check`
