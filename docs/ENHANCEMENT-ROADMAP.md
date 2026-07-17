# 全面強化路線圖（Enhancement Roadmap）

> 建立日期：2026-07-17。
> 目的：以 2026 年 agent 生態系的兩個既有標準（ACP、AG-UI）為**借鏡**，
> 對照本專案現況，列出分階段的強化任務。每個階段獨立可交付、可驗證，
> 依序執行；跨多個 session 的長期任務以此文件為單一事實來源。
>
> **定位（重要）**：Claude Code 與 Codex CLI 是且仍然是本專案的核心
> 引擎。以下所有階段都是**優化既有 Claude/Codex 整合的體驗與架構**
> ——參照這些標準的設計模式，不是引入它們來取代現有引擎。唯一涉及
> 「新增引擎」的 ACP 項目被明確列為最後的選配（Phase 4），且它是
> 純增量：完全不動 claude_engine / codex_engine 的既有路徑。
>
> **產品定位（2026-07-17 與專案擁有者確認）**：本專案目標是
> **發佈給其他使用者、包裝成可安裝軟體**（NSIS installer、GitHub
> Releases、Docker），不只是個人工作站。所有優化取捨以「零設定
> 開箱即用」優先於「單機效能極限」——例如語音的本機模型自動下載、
> GPU 自動偵測失敗退回 CPU、Docker 的 opt-in GPU overlay，都是
> 這個原則的既有先例，新工作應沿用同樣思路。

## 一、生態系研究結論（2026-07 查證）

### ACP — Agent Client Protocol（agent CLI 端的標準）

- 官方：<https://zed.dev/acp>，由 Zed 發起的開放標準。
- 定位：**「編輯器/宿主 ↔ agent CLI」的 LSP**。JSON-RPC 2.0 over
  stdio，宿主以 subprocess 方式啟動 agent，`initialize` →
  `session/new` → `session/prompt`，agent 以 `session/update`
  notification 串流回傳文字塊、工具呼叫、權限請求。
- 採用現況：JetBrains、Google（Gemini CLI 是第一個原生整合）、
  Kiro、Blackbox 等 25+ agents；已有官方 registry
  （<https://zed.dev/blog/acp-registry>）作為 agent 發現/分發層。
- **對本專案的意義**：`backend/engines/` 目前是「每接一個 CLI 就手寫
  一個 subprocess adapter」——claude_engine 解析 Claude 的 stream-json、
  codex_engine 解析 Codex 的 JSONL，兩邊各自踩過各自的坑（見各檔案
  開頭的實測註記）。做一個 `acp_engine.py` 之後，任何 ACP 相容 agent
  （Gemini CLI 起步）都能直接當第三引擎掛進來，**一個 adapter 對應
  25+ agents**，之後不用再為單一 CLI 手寫解析器。

### AG-UI — Agent-User Interaction Protocol（前端適配端的標準）

- 官方：<https://docs.ag-ui.com/introduction>、
  <https://github.com/ag-ui-protocol/ag-ui>（CopilotKit 生態）。
- 定位：**「agent 後端 ↔ 使用者介面」的事件協定**。HTTP POST 發起
  一輪執行、SSE 串流回傳 **17 種型別化事件**：`RUN_STARTED`、
  `TEXT_MESSAGE_CONTENT`、`TOOL_CALL_START/ARGS/END`、`STATE_DELTA`
  （JSON Patch 增量狀態）、`RUN_FINISHED`、`RUN_ERROR` 等。
  Microsoft Agent Framework 等已有官方整合。
- **對本專案的意義**：目前 `/api/chat` 與 `/api/team/run/{id}/stream`
  各自定義了不同形狀的 SSE payload，前端 `claude.service.ts` 為每個
  端點手寫一套解析。收斂成 AG-UI 風格的型別化事件層之後：
  (1) 前端解析邏輯統一成一套 reducer；(2) 未來要換前端框架或接入
  現成 AG-UI 客戶端（CopilotKit 等）有現成生態；(3) 工具呼叫、
  子 agent 進度這類「非純文字」的串流內容有正式的表達方式，
  不用再塞在文字流裡用字串前綴區分。

## 二、現況差距分析（對照本 codebase）

| 區塊 | 現況 | 差距 |
|------|------|------|
| 引擎層 | `engines/claude_engine.py`、`codex_engine.py` 各自手寫 subprocess + 輸出解析 | 每新增一個 CLI 都要重新踩一遍輸出格式的坑；無法接入 ACP 生態的 25+ agents |
| 串流層 | `/api/chat` 與 team run stream 各自的 SSE 形狀 | 無型別化事件契約；前端每個端點一套解析；工具呼叫/狀態變化靠字串約定 |
| 前端 | `app.ts` 約 4,400 行單一元件；初始 bundle 4.3MB 一次載入 | 無 route-level 拆分；所有分頁的程式碼與樣板都在首屏載入與變更偵測範圍內 |
| 後端 | `main.py` 約 3,700 行；routes/ 只抽出部分 handler | soul/memory/schedule/session 等 handler 仍在 main.py，模組邊界不完整 |

## 三、分階段任務

### Phase 1 — 型別化串流事件層（優化 Claude/Codex 串流體驗）

**目標**：後端輸出、前端消費統一為 AG-UI 風格的型別化事件。這直接
優化既有 Claude Code / Codex 的使用體驗——兩個 CLI 的輸出裡本來就有
工具呼叫、用量、子任務進度等結構化資訊，目前不是被丟掉就是用字串
前綴塞在文字流裡；型別化之後前端能忠實呈現。

- 定義事件模型（參照 AG-UI 的 17 種事件挑本專案用得到的子集）：
  `run_started` / `text_delta` / `tool_call_start` / `tool_call_end` /
  `member_started` / `member_finished`（team run 特有）/ `run_finished`
  / `run_error`。
- 後端：新增事件序列化 helper，`/api/chat` 與 team run stream 改發
  型別化事件；**保留舊格式一個過渡版本**（雙寫或由 query 參數選擇），
  避免一次性破壞 Electron 舊版前端。
- 前端：`claude.service.ts` 收斂成單一事件 reducer；工具呼叫顯示
  （目前的 tool timer 等）改吃 `tool_call_*` 事件而非字串解析。

**驗收**：既有 e2e 全綠；chat 與 team run 的前端解析共用同一套
reducer；工具呼叫進度不再依賴文字前綴約定。

### Phase 2 — 前端分解與延遲載入

**目標**：把 `app.ts`（約 4,800 行）按分頁拆成 feature 元件，
非首屏分頁改為 lazy route，降低初始 bundle 與變更偵測成本。

**2026-07-17 實測後的範圍修正（重要，讀完再動手）**：

原始規劃假設 settings modal 是「低耦合、可以第一個乾淨拆出去」的區塊。
實際盤點後發現這個假設不成立，記錄如下避免後續實作者重踩：

1. **狀態不是 settings-local，是 app-wide shared state。** 以
   `engineStatus` 為例：在 `app.html` 233-555 行（settings modal 範圍）
   內使用，但同時也在 onboarding 流程（55-66 行）與 agent 編輯區
   （724 行）被讀取。settings modal 用到的 signal 有不少是這種
   「順便展示在 settings 裡，但狀態本體屬於整個 app」的情況，不能
   直接把它們的宣告搬進新元件——要嘛留在 `app.ts` 用 `@Input`/service
   往下傳，要嘛抽成獨立 service（見下）。
2. **實際引用規模比預期大。** grep 統計（僅列部分）：
   `settingsForm: 63 refs`、`memEditContent: 8 refs`、
   `telegramSaving: 6 refs`、`doctorRunning: 6 refs`、
   `backendLogs: 3 refs`、`importAgencyAgents: 3 refs`、
   `recentWorkDirs: 3 refs`。`settingsForm` 的 63 個引用分散在
   template 綁定、`saveSettings()`、`resetSettingsForm()`、多個
   `effect()` 之間，不是能安全一次性搬移的規模。
3. **`@defer` 不能替代真正的元件抽取。** Angular 17+ 的 `@defer`
   區塊只有在包住「有自己獨立 import 的元件」時才會真正做到
   bundle-size 拆分；如果只是把 template 包一層 `@defer` 但邏輯還是
   綁在 host component（`app.ts`）自己的 class members 上，效果只有
   「延後渲染/變更偵測時機」，**不會**減少初始 bundle 大小——因為
   `app.ts` 本身（含 settings 邏輯）還是被整包編進主 bundle。roadmap
   驗收項「初始 bundle < 2MB raw」如果只做 `@defer` 包裝是達不到的，
   必須是真元件抽取（獨立 `.ts`/`.html`/DI 邊界）才算數。

**修正後的拆分策略**：

- 抽取前先建一個 `AppStateService`（或按領域拆多個 service）承接
  真正跨頁共用的 signal（`engineStatus` 是目前唯一已確認案例，抽取
  途中若發現同類 signal 一併移入）；`app.ts` 與新元件都注入這個
  service，而不是互相直接引用對方的 class members。
- 抽取順序改為「先挑引用數最低、且已知無跨頁共用狀態的區塊做驗證」，
  而不是原規劃的「settings modal 整塊」：`recentWorkDirs`
  （3 refs）→ `importAgencyAgents`（3 refs）→ `backendLogs`（3 refs）
  → `telegramSaving`（6 refs）→ `doctorRunning`（6 refs）→
  `memEditContent`（8 refs）→ 最後才是 `settingsForm`（63 refs，
  拆成子區塊逐步搬，不整塊搬）。
- 每抽一個區塊：建元件 → 搬 template/邏輯 → 需要跨頁狀態的改注入
  service → 跑完整 e2e → commit。不做大爆炸式重寫。
- **進度（持續更新，接手前先看這裡）**：
  - ✅ `backendLogs` + `doctorRunning`（合併抽成
    `DiagnosticsPanelComponent`，兩者本來就是同一塊 UI 且互不跨頁）。
  - ✅ `importAgencyAgents`（抽成 `AgencyImportPanelComponent`，
    `imported` 事件往上通知 `app.ts` 呼叫既有的 `reload()` /
    `loadTeams()`）。
  - ⬜ 下一個：`recentWorkDirs`（3 refs）→ `telegramSaving`
    （6 refs）→ `memEditContent`（8 refs）→ `settingsForm`
    （63 refs，分段拆）。
  - 共通踩坑：`.modal-section` / `.modal-section-header` / `.btn-sm`
    等 settings modal 共用樣式已搬到 `src/styles.scss`（global）——
    Angular 的 emulated encapsulation 讓子元件收不到 `app.scss`
    裡的 component-scoped 樣式，每抽一個新元件如果用到這些 class
    不用再重複搬。
- memview/schedules 分頁、teams/skills 側欄、chat 主畫面的 lazy
  route 化維持原規劃，在 settings 相關的小區塊都驗證完拆分模式可行
  之後再進行（這幾塊本身就是路由層級，天然比 settings modal 內部
  的子元件更適合 `loadComponent` lazy route，風險模式不同）。

**驗收**：初始 bundle 顯著下降（目標 < 2MB raw，且驗證是靠真元件
抽取達成而非 `@defer` 包裝）；所有 e2e 綠；每個抽取增量各自可獨立
回溯（每個 commit 都是綠的可運作狀態，不依賴後續 commit 才能運作）。

### Phase 3 — 後端模組化收尾

**目標**：把 main.py 剩餘的 handler（souls、memory、schedules、
sessions、upload、translate、audio）依既有 routes/ 模式抽出，
main.py 只留 app 組裝與生命週期。

**驗收**：main.py 行數減半以上；`pytest tests/` 全綠；路由行為零變化。

### Phase 4（選配）— ACP 引擎（純增量的第三引擎）

**定位**：**不取代 Claude Code / Codex**。這是一個純增量選項——
新增 `backend/engines/acp_engine.py`，以 ACP（JSON-RPC 2.0 over
stdio）對接 ACP 相容 agent（Gemini CLI 為驗證對象），讓 team 成員
多一種引擎可選；claude_engine / codex_engine 的既有路徑一行不動，
engineMode 鎖定邏輯（`resolve_engine_name_gated`）自然涵蓋。
是否執行由使用者屆時決定。

- 遵循 `engines/base.py` 的既有約定：提供 `name`、
  `DEFAULT_PERMISSION_MODE`、`async run_turn(...) -> RunResult`，
  呼叫端（`routes/teams.py::_agent_run_capture`）零改動。
- **測試策略**：單元/整合測試用「假 ACP agent」（一個 Python 小腳本，
  照協定回應 JSON-RPC）驗證握手、串流、權限、錯誤、取消五條路徑——
  不依賴真實 CLI；端到端驗證用升級後的 Gemini CLI（本機現裝 0.1.9，
  需升級到含 `--experimental-acp` 的版本）。
- 風險：ACP 權限模型與本專案 permission mode 字彙對映需要實測校準；
  Windows 上 npm shim（`gemini.cmd`）需沿用 `helpers.wrap_cmd()` 的
  既有處理。

**驗收**：假 ACP agent 的整合測試全綠；真實 Gemini CLI 能作為 team
成員完成一輪 team run；`/api/engines/status` 正確回報第三引擎。

## 四、執行順序與依賴

```
Phase 1（事件層）─────────► 優先執行（直接優化 Claude/Codex 體驗）
Phase 2（前端分解）───────► 在 Phase 1 之後（reducer 統一後拆分更乾淨）
Phase 3（後端模組化）─────► 隨時可插隊，與其他 Phase 無衝突
Phase 4（ACP 引擎，選配）──► 最後；純增量，是否執行由使用者決定
```

## 五、實作共通注意事項（給接手的實作者，2026-07 實測踩過的坑）

1. **測試跑法**：後端 `python -m pytest tests/`（完整套件是文件記載的
   標準跑法；單獨跑個別測試類別已可行——conftest 的
   CONFIG_FILE/CLAUDE_HOME 順序 bug 已在 PR #28 修掉）。前端
   `npx tsc --noEmit` + `npx ng build` + `npx ng test --watch=false` +
   `npx playwright test`。
2. **Playwright 的 port 4200 陷阱**：`playwright.config.ts` 的
   `reuseExistingServer` 會直接沿用已佔用 4200 的服務——本機 Docker
   前端（nginx）也綁 4200，e2e 會默默測到它服務的**舊靜態檔案**而
   不是最新原始碼。改前端程式碼後要嘛停掉 container、要嘛
   `npx ng build --base-href ./` 後把產物複製進主 checkout 的
   `frontend/dist/frontend/browser/`（nginx bind mount 讀那裡）。
3. **backend 新模組的兩個雷**：(a) `.gitignore` 有 `backend/local_*.py`
   規則，新檔案不要取 `local_` 開頭的名字（stt.py 當初就中招）；
   (b) `backend/Dockerfile` 的 COPY 是白名單，新增 .py 模組必須加進
   那行 COPY，不然 Docker 版直接 ModuleNotFoundError。
4. **工作流程**：在 worktree 開發 → 全部測試綠 → conventional commit
   → push → `gh pr create` → 合併前確認 `mergeable`。CRLF 警告在
   Windows 上是正常噪音。
5. **Docker 重建**：改後端後 `docker compose --profile prod build
   backend && docker compose -f docker-compose.yml -f
   docker-compose.gpu.yml --profile prod up -d backend`（GPU overlay
   是 opt-in；模型快取在 named volume `stt_model_cache`，重建不丟）。

## 六、參考資料

- ACP 官方：<https://zed.dev/acp>
- ACP registry：<https://zed.dev/blog/acp-registry>
- ACP 解說（vs MCP、editor 支援）：<https://www.morphllm.com/agent-client-protocol>
- AG-UI 官方文件：<https://docs.ag-ui.com/introduction>
- AG-UI GitHub：<https://github.com/ag-ui-protocol/ag-ui>
- AG-UI 17 種事件解說：<https://www.copilotkit.ai/blog/master-the-17-ag-ui-event-types-for-building-agents-the-right-way>
- Microsoft Agent Framework 的 AG-UI 整合：<https://learn.microsoft.com/en-us/agent-framework/integrations/ag-ui/>
