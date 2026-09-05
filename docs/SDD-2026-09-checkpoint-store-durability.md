# SDD：Checkpoint Store 升級為可查詢的事件日誌

優先順序：**P1**（已列在 `docs/SDD-2026-08-runtime-and-layout-optimization.md`
第八階段「下一步 1」，本文件將其展開為可執行的任務書）

## 背景與參考

`backend/agent_harness.py` 目前的 `AgentCheckpointStore` 是「整個 run 覆寫一份
JSON」：`save()` 每次把完整 `run` dict 與整份 `events` list 序列化、以
atomic replace 寫入 `~/.claude/agent-runs/<run_id>.json`。這保證了單檔案不會
寫壞，但有兩個限制：

1. 無法查詢單一 run 內某個時間點的狀態（沒有 checkpoint 顆粒度）。
2. 沒有 retention policy，`agent-runs/` 目錄會無限成長。

[LangGraph 的 persistence 模型](https://docs.langchain.com/oss/python/langgraph/persistence)
提供了一個已被大量生產環境驗證的參考架構：

- 以 `thread_id` 分組同一個對話/run 的所有 checkpoint。
- 每個 super-step（大致對應我們的「一個 event 邊界」）存一個 checkpoint，
  而不是整份覆寫，因此可以做 time-travel／replay。
- 序列化用統一的 serializer（`JsonPlusSerializer`）處理特殊型別。
- 本地開發可用 SQLite，正式環境用 Postgres；建議「不要停在 SQLite 當生產
  方案」（高併發寫入是已知瓶頸）。

本任務不是要求引入 LangGraph 本身，而是借用它驗證過的 checkpoint 顆粒度與
thread 概念，把現有 JSON-per-run 升級成 append-only 的事件日誌，為 SDD 文件
已經寫下的「SQLite/event-log + retention policy」鋪路。

## 目標

把 `AgentCheckpointStore` 從「整份覆寫」改為「append-only 事件日誌 + 可獨立
查詢的最新 run 狀態快照」，並加入可設定的 retention policy，同時保持現有
呼叫端（`routes/teams.py` 等）介面相容。

## 規格與驗收條件

- 新增儲存後端（建議 SQLite，理由見「設計決策」），schema 至少包含：
  - `runs` 表：`run_id`（唯一）、`updated_at`、`run_json`（最新 run 狀態快照）。
  - `events` 表：`run_id`、`seq`（單調遞增）、`event_json`、`created_at`，
    以 `(run_id, seq)` 為主鍵，允許同一 run 累積多筆而不覆寫舊事件。
- `AgentCheckpointStore.save()` 的對外行為保持相容：呼叫端仍然傳入完整
  `run` dict 與目前累積的 `events`；內部改為「更新 `runs` 表 + append 尚未
  寫入的新 events」，不得要求呼叫端改變呼叫方式（維持 `docs/SDD-2026-08`
  一貫的「保留 template-facing 介面」原則）。
- `load()` 的回傳結構（`{"run_id", "updated_at", "run", "events"}`）必須維持
  向後相容，讓既有的 restore/interrupted 判斷邏輯不需要修改。
- 新增 `list_run_ids(older_than: datetime | None = None)` 或等價 API，支援
  依時間篩選，供 retention policy 使用。
- 新增 retention 清理函式（例如 `purge_older_than(days: int)`），預設不自動
  執行（避免背景任務悄悄刪資料），由呼叫端或排程主動觸發。
- 舊有的 `~/.claude/agent-runs/*.json` 檔案需要一次性遷移腳本／相容讀取路徑，
  確保現有使用者的歷史 run 不會在升級後憑空消失（可以是「讀不到 SQLite
  紀錄時 fallback 讀舊 JSON」的簡單相容層，不需要做成通用遷移框架）。
- 新增測試覆蓋：
  - 同一 run 多次 `save()` 後，`events` 表筆數等於累積事件數（不是覆寫）。
  - `load()` 對舊版 JSON 檔案與新版 SQLite 紀錄都能正確還原。
  - retention 清理不影響未過期的 run。

## 設計決策

- 選 SQLite 而非直接跳 Postgres：這個 store 目前是單機 desktop app 的本地
  持久層，不是多執行個體共享的服務，LangGraph 文件裡「正式環境建議用
  Postgres」的前提（高併發、多程序）在這裡不成立；SQLite 足夠且不增加
  外部依賴。
- 不引入 LangGraph 套件本身：我們只需要它驗證過的資料模型（thread/checkpoint
  顆粒度），沒有必要把整個 graph 執行引擎綁進來，維持 `agent_harness.py`
  現有的「provider-neutral、不綁定第三方 framework」定位。
- Retention 預設不自動執行：避免產品在使用者沒有明確設定的情況下悄悄刪除
  歷史紀錄，這是 desktop app 對「使用者資料」該有的保守預設。

## 驗證命令

```bash
python -m pytest tests/test_agent_harness.py -v
python -m pytest
```

## 參考資料

- [LangGraph Persistence 文件](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph checkpoints API 參考](https://reference.langchain.com/python/langgraph/checkpoints)
