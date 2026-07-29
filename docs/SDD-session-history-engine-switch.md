# SDD: Session History Engine Switch

## Goal

讓左側「對話紀錄」可以依使用者實際使用方式顯示 Claude Code 或 Codex 的歷史紀錄：

- 只用 Claude Code：固定顯示 Claude Code 歷史，不顯示引擎切換鍵。
- 只用 Codex：固定顯示 Codex 歷史，不顯示引擎切換鍵。
- 兩者都可用且 Settings 允許兩者：顯示 Claude / Codex 歷史來源切換鍵。

現有「按日期 / 按專案」只負責分組，不改變資料來源語意。

## Data Sources

Claude Code:

- `CLAUDE_HOME/projects/*/*.jsonl`
- 既有 SQLite `sessions` index 與 FTS。

Codex:

- `CODEX_HOME` 環境變數，未設定時為 `~/.codex`
- `sessions/**/*.jsonl` 作為完整對話與 cwd 來源。
- `session_index.jsonl` 作為 thread title 與 updated_at 的輔助來源。

不讀寫 Codex 私有 SQLite 檔，避免綁定未公開 schema。

## Backend Tasks

- `sessions` table 新增 `engine` 欄位，既有資料預設 `claude`。
- `_sync_index()` 同步 Claude 與 Codex session，並移除各自來源已不存在的 orphan rows。
- `GET /api/sessions?engine=claude|codex|all` 依引擎過濾，預設 `claude` 以維持相容。
- 回傳 `engine` 欄位給前端。
- `GET /api/sessions/{id}/messages` 依該 session 的 engine 使用對應 parser。

## Frontend Tasks

- `Session` interface 新增 `engine?: 'claude' | 'codex'`。
- `ClaudeService.getSessions()` 新增 engine 參數。
- `App` 新增 `sessionEngineFilter` 與 `showSessionEngineSwitch`。
- 根據 `engineMode` 與 `engineStatus` 自動決定預設歷史來源與是否顯示切換鍵。
- 切換歷史來源時重新載入 session，保留日期/專案分組邏輯。

## Acceptance Criteria

- Claude-only 或 Codex-only 模式不顯示歷史來源切換鍵。
- both 模式且兩邊都 available 時顯示 Claude / Codex 切換鍵。
- 按日期與按專案都作用於目前選取引擎的 session 清單。
- `/api/sessions?engine=codex` 可列出 Codex session，並可讀取訊息。
- 既有 `/api/sessions` 未帶參數仍回 Claude session。

