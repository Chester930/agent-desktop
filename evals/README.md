# Agent contract evals

這個目錄是 Agent Desktop 的本地、供應商中立 contract harness。它驗證
Team handoff、checkpoint 與事件生命週期，不需要 API key，也不會啟動 Claude
或 Codex CLI。

目前的可執行 contract 在 `tests/test_agent_harness.py`：

- handoff 必須有安全的 `task_id`、`assigned_agent` 與 acceptance criteria。
- checkpoint 必須能以 atomic replace 寫入，並由新的 store instance 還原。
- event stream 可檢查必要事件、禁止事件、事件數與 elapsed time。

未來接 Harbor 或 Inspect AI 時，外部 benchmark 只需將實際事件轉成相同的
`{type, timestamp_ms, ...}` envelope；這個 harness 不把任何第三方 framework
放進產品 runtime。
