# SDD：Canonical Agent Event Schema 對齊 ACP

優先順序：**P0**（最高槓桿，直接影響後續新增 CLI agent 引擎的成本）

## 背景與參考

`frontend/src/app/agent-events.ts` 已在 SDD 第六、七階段建立 canonical event
型別（`run_started`、`text_delta`、`tool_call_start`、`tool_call_end`、
`permission_requested`、`member_started`、`member_finished`、`usage_updated`、
`run_error`、`run_finished`），並把 Claude/Codex 既有的 legacy SSE 事件
（`assistant`、`user`、`tool_use`、`tool_result`、`exec_*`、`agent_*`、
`result`）正規化到這一層。這個設計目標「未來接入 ACP 或其他 CLI Agent 時不必
把 legacy 格式擴散到 UI」（見 `docs/SDD-2026-08-runtime-and-layout-optimization.md`
第六階段）已經與 [Agent Client Protocol（ACP）](https://github.com/zed-industries/agent-client-protocol)
的 `session/update` 語意高度重疊：

| 現有 canonical type | ACP `session/update` 對應 |
| --- | --- |
| `text_delta` | `agent_message_chunk` |
| `tool_call_start` | `tool_call`（`status: pending`/`in_progress`） |
| `tool_call_end` | `tool_call_update`（`status: completed`/`failed`） |
| `permission_requested` | `permission_request` |
| （尚無） | `plan` |

ACP 已被 Google（Gemini CLI）、GitHub、JetBrains 採用，Codex 有官方 adapter
（`zed-industries/codex-acp`），Claude Code 也有社群/官方 adapter。這代表：
若 canonical schema 直接對齊 ACP 而非自創語意，未來每接一個新 CLI agent
（例如 SDD 文件既定的下一步「接入 Gemini CLI 等外部 ACP adapter」）只需要寫一個
ACP client，不必再為每個引擎手刻一份 legacy→canonical 的 normalizer。

## 目標

在不破壞現有 UI 行為的前提下，讓 canonical event schema 的欄位與狀態機和 ACP
`session/update` 對齊，並補上目前缺少的 `plan` 語意與 tool_call 的完整狀態機。

## 規格與驗收條件

- `AgentEvent`（`frontend/src/app/agent-events.ts`）新增：
  - `tool_call_start`/`tool_call_end` 之間補上可選的中繼狀態，使其可表達
    ACP 的 `pending → in_progress → completed|failed` 四態，而非目前隱含的
    二態（start/end）。既有呼叫端不得因新增中繼狀態而收到未預期事件並中斷。
  - 新增 `plan` 事件型別（對應 ACP `plan`），欄位至少包含
    `steps: {content: string, status: 'pending'|'in_progress'|'completed'}[]`。
    後端可以先不產生這個事件，但型別與 normalizer 分支必須存在並有測試。
- `permission_requested` 的欄位需能承載 ACP `RequestPermissionRequest` 的
  `options`（可授權選項列表），而不只是單一 yes/no，為未來 ACP client 鋪路。
- `evaluate_event_contract()`（`backend/agent_harness.py`）的
  `required_types`/`forbidden_types` 詞彙表需要同步更新註解，說明其命名
  對應 ACP 的哪個概念，避免未來貢獻者各自發明新名詞。
- 新增/更新單元測試，覆蓋：
  - legacy Claude/Codex 事件仍能正確正規化（回歸測試，防止本次改動破壞
    既有行為）。
  - 一組模擬 ACP `session/update` payload 能被同一個 normalizer 正確轉換為
    canonical event（即使目前還沒有真正的 ACP client，也要先用 fixture
    證明 schema 相容）。
- 不在本次任務範圍內：實作真正的 ACP client/adapter、接入 Gemini CLI。這些
  留給後續切片，本任務只處理 schema 對齊。

## 設計決策

- Canonical schema 保留現有型別名稱（`text_delta` 等）不強改成 ACP 原名，
  避免大範圍改動呼叫端；改為在 normalizer 層做語意對齊，型別上新增欄位
  即可，維持 `docs/SDD-2026-08-...` 既定的「保留 template-facing 介面」精神。
- `plan` 事件先只做型別與 normalizer 骨架，不接後端資料來源，避免本任務
  範圍膨脹成「加入 plan 功能」。

## 驗證命令

```bash
python -m pytest
cd frontend && npm test -- --watch=false --no-progress
cd frontend && npm run typecheck
```

## 參考資料

- [Agent Client Protocol](https://github.com/zed-industries/agent-client-protocol)
- [zed-industries/codex-acp](https://github.com/zed-industries/codex-acp)
- [Zed — Agent Client Protocol 說明](https://zed.dev/acp)

## 執行紀錄（2026-09-05）

- `frontend/src/app/agent-events.ts`：`tool_call_start`/`tool_call_end` 新增
  `status` 欄位（`pending`/`in_progress` 與 `completed`/`failed`），
  `permission_requested` 新增 `options`，並新增 `plan` 事件型別。
- 新增 ACP 風格 raw 事件名稱 `tool_call`、`tool_call_update`、`plan` 的
  normalizer 分支，與既有 `tool_call_start`/`tool_call_end`/`permission_*`
  分支完全分離，不影響任何既有 legacy 事件的正規化結果。
- `backend/agent_harness.py` 的 `evaluate_event_contract()` docstring
  補上 canonical 事件與 ACP `session/update` 概念的對照表。
- `frontend/src/app/agent-events.spec.ts` 新增 3 個測試，涵蓋 ACP 風格
  tool_call 生命週期、plan 事件與 permission options；既有 3 個回歸測試
  全數維持通過。

### 驗證結果

| 驗證項目 | 結果 |
| --- | --- |
| `frontend/src/app/agent-events.spec.ts`（vitest） | 通過，7 tests passed |
| 後端 full suite (`python -m pytest`) | 通過，見下方累計驗證 |
