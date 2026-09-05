# SDD：ACP Client Engine（第三個可插拔引擎，MVP 目標 Gemini CLI）

優先順序：**P0**（本輪唯一項目——直接兌現先前 P0 對齊 ACP schema 的投資，
是目前唯一「使用者能感受到差異」的候選：多一個可用的 CLI agent 引擎）

## 背景與參考

`backend/engines/registry.py` 目前只認 `claude`/`codex` 兩個寫死的引擎。
[Agent Client Protocol（ACP）](https://github.com/agentclientprotocol/agent-client-protocol)
已經是被 Google、GitHub、JetBrains 採用的標準協定，官方提供
[Python SDK](https://github.com/agentclientprotocol/python-sdk)
（`pip install agent-client-protocol`，內含 Pydantic models、async base
class、JSON-RPC stdio 傳輸層，`examples/` 目錄有現成的 **Gemini CLI
bridge** 範例可以參考）。
[Gemini CLI 本身內建 ACP server 模式](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/acp-mode.md)
（`gemini --acp`），是最現成的第一個目標 agent；理論上同一份 client 實作
未來也能接
[zed-industries/codex-acp](https://github.com/zed-industries/codex-acp)
或 Claude Code 自己的 ACP adapter，但本輪只鎖定 Gemini CLI 一個目標，避免
一次驗證多個尚未實測過的 adapter。

**版本風險**：搜尋結果顯示 ACP v2 目前仍是 draft，wire protocol 可能還會
不相容變動。本輪**明確釘死在 v1**，不追 v2。

## 目標

新增 `backend/engines/acp_engine.py`，實作跟 `claude_engine.py`／
`codex_engine.py` 完全相同的 `run_turn()` 介面（見 `engines/base.py`），
把 Gemini CLI（`gemini --acp`）接成第三個可插拔引擎，讓 `/api/chat` 的
單一對話能選用它；Team Run／既有 SSE 轉發／`_format_tool_event_as_text`
一行都不用改。

## 規格與驗收條件

### 核心整合

- `acp_engine.py` 提供 `name = "acp"`、`DEFAULT_PERMISSION_MODE`、
  `async def run_turn(...)`，簽名與既有兩個 engine 一致（見
  `engines/base.py` 開頭的介面約定）。
- 用官方 Python SDK 的 client-side 連線（`ClientSideConnection` 或 SDK
  文件當下對應的 client 基底類別）spawn `gemini --acp` 子行程（binary
  路徑透過 `bin_override` 參數覆寫，預設 `"gemini"`，比照
  `codex_engine._codex_bin()` 的模式），走 stdio 建立 JSON-RPC 連線。
- 一次 `run_turn()` 呼叫對應：`session/new`（若無 `resume_session_id`）或
  用既有 session id 恢復，接著 `session/prompt` 送出 `prompt` 內容，直到
  收到終止的 `session/update`（或協定定義的完成訊號）為止。
- 回傳 `RunResult(output, session_id, error)`，`session_id` 供未來
  `resume_session_id` 復用（沿用既有引擎的 session 復用慣例）。

### 事件轉換（刻意不用 P0 的 ACP-native 型別）

- `session/update` 的 `agent_message_chunk` → 呼叫既有 `on_text(chunk)`。
- `tool_call`（初次宣告）→ 轉換成 `engines/base.py` 既有文件記載的
  **legacy envelope**：`{"type": "tool_use", "id": <toolCallId>, "name":
  <title 或 kind>, "input": <rawInput 或 {}>}`，透過 `on_tool_event` 送出。
- `tool_call_update`（`status` 變成 `completed`/`failed`）→ 轉換成既有的
  `{"type": "user", "message": {"content": [{"type": "tool_result",
  "tool_use_id": <toolCallId>, "content": <內容字串>, "is_error": status
  == "failed"}]}}`，一樣透過 `on_tool_event` 送出。
- **設計決策**：不直接沿用 P0（`docs/SDD-2026-09-acp-aligned-event-schema.md`）
  新增的 ACP-native canonical 型別（`tool_call`/`tool_call_update`）當作
  `on_tool_event` 的 payload。原因：`engines/base.py` 目前文件化的
  `on_tool_event` contract 就是 legacy `tool_use`/`user` 這兩種形狀，
  `handle_chat` 的 SSE 轉發與 `_format_tool_event_as_text()`（Team Run 用）
  都是照這個 contract寫的；如果 `acp_engine.py` 改送 ACP-native 形狀，
  `_format_tool_event_as_text()` 會安靜地把它當成未知型別回傳空字串
  （見 `tests/test_tool_event_streaming.py::TestFormatToolEventAsText::test_unknown_event_type_returns_empty_not_error`），
  Team Run 的工具呼叫可視度會無聲消失。轉換成既有 legacy 形狀能保證
  `handle_chat`、`_agent_run_capture`、前端既有 legacy 正規化路徑
  **零改動**就正確運作；P0 的 ACP-native 型別留給未來真正需要「原生
  ACP 事件直通」時再評估用。
- `plan` 事件：本輪不接任何 callback（沒有既有管線可用），直接忽略。

### Permission（明確縮小範圍，不做即時互動決策）

- ACP 的 `session/request_permission` 本質上是「agent 暫停、等待同步
  決定」的互動流程，跟這個 codebase 現有兩個引擎「headless 一次性
  run_turn()、permission_mode 在派發當下就固定」的模型不相容（見
  `routes/teams.py::_agent_run_capture` 對 `permission_mode` 的既有處理）。
- 本輪**不**實作依工具種類判斷允許/拒絕的即時決策邏輯。改成：
  - `permission_mode` 屬於「允許編輯」的一組值時（`acceptEdits`、
    `workspace-write`、`bypassPermissions`、`danger-full-access`——沿用
    `engines/claude_engine.py`／`engines/codex_engine.py` 既有的
    `VALID_PERMISSION_MODES` 語彙），對所有 `session/request_permission`
    自動選擇「允許」對應的 option（ACP 回應裡通常會有一個
    `kind: "allow_always"` 或等價選項，選不到才 fallback 選第一個
    non-cancel 選項）。
  - 其餘 `permission_mode`（例如唯讀/plan 類語彙）：`run_turn()`
    直接以 `RunResult(error=...)` 回傳明確錯誤，說明「ACP 引擎目前只支援
    允許編輯類的 permission_mode」，不嘗試猜測要不要允許——寧可明確拒絕
    整個 run，不要用錯誤的預設值悄悄允許或悄悄拒絕單一工具呼叫。
  - 這個限制要寫進模組 docstring 與使用者可見的錯誤訊息，不是只藏在
    程式碼註解裡。

### 可用性偵測

- `engines/availability.py` 新增 `acp`／Gemini CLI 的偵測邏輯：至少要能
  回答「`gemini` 執行檔存不存在（`shutil.which`）」；登入狀態偵測若
  Gemini CLI 沒有對應的輕量 status 指令，允許 MVP 階段回報
  `loggedIn: "unknown"`（不得誤報成 `True`），比照現有兩個引擎的
  `installed`/`loggedIn`/`available` 三態設計。
- `engines/registry.py` 的 `ENGINES` dict 新增 `"acp": acp_engine`。
  `DEFAULT_ENGINE_NAME` 維持 `"codex"` 不變——新引擎不改變任何既有預設
  行為，只是多一個選項。
- **明確排除**：`database._VALID_ENGINE_MODES`（`get_engine_mode()`
  鎖定引擎範圍的三選一）本輪不擴充成四選一。ACP 引擎只能在
  `engineMode == "both"` 時透過個別 agent 的 `engine: acp` frontmatter 或
  請求層級 `agent_engine` 欄位選用，不能被設成全域鎖定的唯一引擎——等
  有真實使用情境驗證穩定性後再評估。

### 測試

- 比照 `tests/test_tool_event_streaming.py` 的既有模式：用真實 `gemini
  --acp` 執行一次驗證抓到的 JSON-RPC 訊息序列（不是憑空編的），驗證
  `run_turn()` 正確送出 `session/new`/`session/prompt`、正確把
  `agent_message_chunk` 轉成 `on_text`、正確把 `tool_call`/
  `tool_call_update` 轉成 legacy `tool_use`/`user` envelope。
- 驗證 `permission_mode` 不在允許清單時，`run_turn()` 回傳明確
  `RunResult(error=...)`，不會嘗試連線或送出任何 prompt。
- 驗證 `is_cancelled()`／`on_process` 的既有慣例（子行程可被追蹤、可被
  cancel 路徑安全 kill/reap，比照 `process_lifecycle.terminate_and_reap()`
  的既有使用方式）。
- 新增至少一個 `/api/chat` 層級的整合測試（比照
  `tests/test_tool_event_streaming.py::TestHandleChatForwardsToolEvents`），
  用 fake ACP 連線驗證 SSE 端到端輸出。

### 明確排除（本輪不做）

1. Team Run（`/api/team/run`）目前不需要額外修改就能運作（因為事件轉換
   成 legacy 形狀），但**不特別驗證** Team Run 對 ACP 引擎的完整體驗
   （例如多個 ACP member 平行跑的資源/並發表現）——留給有真實需求時。
2. 不支援 `session/load` 的完整歷史回放（`resume_session_id` 只做「重用
   session id 送下一個 prompt」，不驗證跨行程重啟後的完整上下文還原）。
3. 不接 `zed-industries/codex-acp`、Claude Code 自己的 ACP adapter——只
   驗證 Gemini CLI 這一個目標。
4. 不追 ACP v2 draft 的任何新增能力。
5. 不做依工具種類判斷允許/拒絕的細緻 permission 決策（見上方「明確縮小
   範圍」）。

## 設計決策

- 事件轉換成 legacy `tool_use`/`user` 形狀而非 P0 的 ACP-native 型別：
  理由見上方「事件轉換」小節，核心是「不要求同時改動好幾個既有消費端」。
- Permission 只支援「整個 run 允許編輯」或「明確拒絕整個 run」兩種結果，
  不做細粒度決策：ACP 的即時互動 permission 模型跟這個 codebase 現有的
  headless 執行模型本質衝突，在沒有真實使用情境要求細粒度控制之前，
  寧可讓使用者選錯 permission_mode 時得到清楚的錯誤，也不要用猜測性的
  預設值造成「使用者以為被擋下了但其實被允許」或反過來的落差。
- 不擴充 `_VALID_ENGINE_MODES`：新引擎的穩定性尚未經過真實使用驗證，
  不應該讓使用者能把自己鎖死在一個還在驗證階段的引擎上。

## 驗證命令

```bash
python -m pytest tests/test_acp_engine.py -v
python -m pytest
```

## 參考資料

- [Agent Client Protocol](https://github.com/agentclientprotocol/agent-client-protocol)
- [ACP Python SDK](https://github.com/agentclientprotocol/python-sdk)
- [Gemini CLI — ACP mode 文件](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/acp-mode.md)
- [zed-industries/codex-acp](https://github.com/zed-industries/codex-acp)（未來可能的第二個目標，本輪不做）
