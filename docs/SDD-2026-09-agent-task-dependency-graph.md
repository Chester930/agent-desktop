# SDD：AgentTask 依賴圖擴充

優先順序：**P2**

## 背景與參考

`backend/agent_harness.py` 的 `AgentTask` 目前只有扁平的
`parent_run_id`、`input_refs`、`output_refs` 三種關聯欄位，足以表達「這個
task 屬於哪個 run、讀哪些輸入、寫哪些輸出」，但無法表達 task 之間的
先後/阻塞關係（例如「task B 要等 task A 完成才能開始」或「task C 是從
執行 task A 時臨時發現要做的」）。

[claude-code-orchestrator-kit](https://github.com/maslennikov-ig/claude-code-orchestrator-kit)
（社群專案，目標同樣是協調多個 Claude Code session/sub-agent）用一個叫
**Beads** 的 git-backed 任務追蹤機制處理這個問題：任務之間用
`blocks` / `blocked-by` / `discovered-from` 三種關係建依賴圖，並且這個依賴
圖可以跨 session 存活。這剛好對應我們目前 `routes/teams.py` 的
`execution_mode` 只有 `parallel`/`sequential` 兩種粗粒度模式的限制——
sequential 模式其實是把「A blocks B blocks C…」這種鏈狀依賴用陣列順序
隱含表達，一旦 team 的協作關係不是單純鏈狀（例如兩個 member 平行跑完才能
餵給第三個 member），現有結構就表達不出來。

## 目標

在不改變現有 `parallel`/`sequential` 執行行為的前提下，讓 `AgentTask` 能
表達任務間的依賴關係，作為未來支援「非鏈狀」team 協作拓樸的資料基礎。

## 規格與驗收條件

- `AgentTask`（`backend/agent_harness.py`）新增欄位：
  - `blocks: list[str]`：本 task 完成前，會阻塞的其他 task_id 列表。
  - `blocked_by: list[str]`：本 task 需要等待完成的 task_id 列表。
  - `discovered_from: str = ""`：若此 task 是執行另一個 task 時動態產生的，
    記錄來源 task_id；否則為空字串。
  - 三個欄位皆為選填、預設空值，現有呼叫端（`_make_handoff` 等）不需要修改
    即可繼續運作（`__post_init__` 驗證邏輯需比照 `input_refs`/`output_refs`
    的既有驗證方式處理，非空字串、長度上限一致）。
- `AgentTask.to_dict()`/`from_dict()` 需完整支援新欄位的序列化/還原，並補
  單元測試（比照現有 `input_refs`/`output_refs` 的測試風格）。
- 新增一個純函式（例如 `resolve_ready_tasks(tasks: list[AgentTask]) -> list[str]`），
  給定一組 task，回傳目前所有 `blocked_by` 已全數完成（`status == "done"`）
  的 task_id 列表。這是給未來 team runtime 用來決定「下一批可以派發的 task」
  的純邏輯，本任務只要求這個函式本身正確、有測試，不要求接進
  `routes/teams.py` 的實際排程（避免任務範圍膨脹成改執行引擎）。
- 需要有測試涵蓋：循環依賴（A blocked_by B、B blocked_by A）必須被
  `resolve_ready_tasks` 安全處理（回傳空集合或明確拋錯，二選一並在
  docstring 說明選擇的行為），不可以無限迴圈或 stack overflow。

## 設計決策

- 只做資料結構與純函式，不改 `routes/teams.py` 的實際排程邏輯：現有
  `execution_mode` 的 parallel/sequential 已有對應測試
  （`tests/test_team_run_execution_mode.py`）且是目前唯一使用者（HR Agent
  自動組隊）依賴的行為，貿然在同一個任務裡改排程邏輯風險過高。依賴圖先
  作為「資料層能力」落地，排程層要不要用、怎麼用留給後續切片評估。
- 欄位命名沿用 Beads 的 `blocks`/`blocked-by`/`discovered-from` 詞彙（改成
  Python 慣用的底線命名），而非自創詞彙，降低未來對照社群做法的理解成本。

## 驗證命令

```bash
python -m pytest tests/test_agent_harness.py -v
python -m pytest
```

## 參考資料

- [claude-code-orchestrator-kit](https://github.com/maslennikov-ig/claude-code-orchestrator-kit)

## 執行紀錄（2026-09-05）

- `AgentTask`（`backend/agent_harness.py`）新增 `blocks`/`blocked_by`
  （用 `_clean_refs` 驗證，比照 `input_refs`/`output_refs`）與
  `discovered_from`（用 `_clean_id`，required=False，比照 `parent_run_id`）
  三個欄位，預設皆為空；`to_dict()`/`from_dict()` 完整支援。
- 新增 `resolve_ready_tasks(tasks)` 純函式：只用每個 task 目前的 `status`
  做過濾（不做遞迴圖走訪），因此循環依賴（A blocked_by B、B blocked_by A）
  天生不會造成無限迴圈——兩者的狀態永遠不會變成 `done`，`resolve_ready_tasks`
  回傳空集合，不需要額外的 cycle-detection 邏輯。未知的 `blocked_by` id
  一律視為未解除封鎖。
- 未修改 `routes/teams.py` 的排程邏輯；`_make_handoff` 建立的 `AgentTask`
  沿用預設空值，行為不變。
- `tests/test_agent_harness.py` 新增 7 個測試，覆蓋欄位 round-trip、預設值、
  不安全 `discovered_from` 拒絕、`resolve_ready_tasks` 的就緒/阻塞/未知
  blocker/循環依賴/terminal 狀態排除等情境。

### 驗證結果

| 驗證項目 | 結果 |
| --- | --- |
| `tests/test_agent_harness.py` | 通過，13 tests passed（含本項新增 7 個） |
| 後端 full suite (`python -m pytest`) | 通過，422 tests passed |
