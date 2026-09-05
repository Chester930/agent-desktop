# SDD：Team Execution Mode 語意釐清（Handoff vs Agent-as-Tool）

優先順序：**P3**

## 背景與參考

`routes/teams.py` 目前的 `execution_mode` 只有兩個值：

- `parallel`：`asyncio.gather` 平行執行所有 member，彼此輸出不互相餵入。
- `sequential`：依 `steps` 陣列順序執行，前一個 member 的輸出字串會被塞進
  下一個 member 的 prompt（`tests/test_team_run_execution_mode.py` 的
  `test_inline_payload_sequential_mode_chains_output` 驗證了這個行為）。

這是 2026-07-10 健檢修復的既有行為，目前運作正確，但語意上只有「要不要把
上一步輸出塞進下一步 prompt」這一個維度，沒有區分「誰在對話中握有控制權」。
[OpenAI Agents SDK 的 handoff 文件](https://openai.github.io/openai-agents-python/handoffs/)
把多 agent 協作明確分成兩種模式：

- **Handoff**：控制權整個轉移給下一個 agent，原本的 orchestrator 不再介入
  這一輪剩下的部分。
- **Agent-as-tool**：orchestrator 全程保留控制權，把其他 agent 當工具呼叫，
  拿到結果後自己決定下一步。

我們現在的 `sequential` 其實是「像 handoff 但沒有回頭路」——member A 做完
就徹底交棒給 member B，B 做完交給 C，沒有人能「呼叫完某個 member 再自己做
判斷」。這個限制目前沒有測試明確表達出來，未來如果要支援「leader 先呼叫
member 當工具、拿到結果後自己綜合決策」這種模式（比較接近 CrewAI 的
hierarchical process、或 Claude Agent SDK 的 orchestrator-worker 模式），
需要先把現有語意講清楚，才知道要新增什麼而不是繼續往 `execution_mode`
塞更多字串值。

## 目標

不改變現有兩種模式的行為，但把它們的語意用文件與型別明確定義為
「handoff-chain」，並評估／設計是否要新增第三種「leader 保留控制權」的
模式介面（本任務只做設計與最小骨架，不要求完整實作排程邏輯）。

## 規格與驗收條件

- 在 `routes/teams.py` 或 `agent_harness.py` 中補上文件字串／註解，明確說明：
  - `parallel` = 無交接，各自獨立執行。
  - `sequential` = handoff chain：每個 member 對話控制權交出去後不會拿回來，
    輸出以純文字串接的方式往下傳。
- 新增測試 `test_team_run_execution_mode.py` 案例，明確斷言 sequential 模式
  下「member A 執行完後，其 handoff（`AgentTask`）status 變為 done，且不會
  再被要求執行第二次」——用測試把「一去不回頭」的 handoff 語意釘死，避免
  未來有人誤以為 sequential 支援迴圈或回退。
- 產出一份設計筆記（可以是本文件的「設計決策」小節，不需要另開檔案），
  評估是否新增 `execution_mode: "leader"`（agent-as-tool 模式：由
  `team.leader` 指定的 member 保留控制權，其餘 member 以工具呼叫形式被
  呼叫、回傳結果後由 leader 決定下一步）。若評估結果是「值得做」，本任務
  只需要新增對應的型別/欄位骨架（例如 `execution_mode` 允許的合法值集合
  更新、`AgentTask` 是否需要新增欄位標示「呼叫者」），不要求實作真正的
  leader 決策迴圈；若評估結果是「現階段不值得」，則在文件中記錄理由，
  作為未來重新評估的依據。
- 不得修改 `parallel`/`sequential` 既有的實際執行行為或預設值，
  `tests/test_team_run_execution_mode.py` 既有測試必須維持全部通過。

## 設計決策

- 優先做「語意釘死 + 評估筆記」而非直接實作第三種模式：`execution_mode`
  目前唯一的正式使用者是 HR Agent 自動組隊（見
  `tests/test_team_run_execution_mode.py` 檔頭註解），在沒有第二個真實
  使用情境驗證「leader 保留控制權」模式的必要性之前，直接實作容易變成
  沒有使用者的臆測功能，違反「不要為假設性需求設計」的原則。
- 用 OpenAI Agents SDK 的 handoff / agent-as-tool 詞彙作為溝通語言，而非
  發明新詞，降低未來貢獻者查閱外部資料時的認知落差。

## 驗證命令

```bash
python -m pytest tests/test_team_run_execution_mode.py -v
python -m pytest
```

## 參考資料

- [OpenAI Agents SDK — Handoffs](https://github.com/openai/openai-agents-python)
- [OpenAI Agents SDK — Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/)
