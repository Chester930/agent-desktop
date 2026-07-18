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
  - ✅ `recentWorkDirs`（抽成 `RecentWorkDirsComponent`；只抽「最近
    目錄 chips」這個純讀取 `SettingsService` 的子區塊，`select`
    事件往上通知 `app.ts` 設定 `settingsForm.workDir`——`<label>工作
    目錄` 本身那個 `[(ngModel)]="settingsForm.workDir"` 的主輸入框
    仍留在 `app.ts`，因為它屬於還沒拆的 `settingsForm`）。
  - ✅ `telegramSaving`（連同 `telegramToken`/`telegramEnabled`/
    `telegramRunning`/`loadTelegramSettings`/`saveTelegramSettings`
    一併抽成 `TelegramSettingsComponent`；整塊本來就是獨立的
    `.modal-section`，沒有跨頁狀態。`loadTelegramSettings()` 原本由
    `App.openSettings()` 呼叫，改成元件自己在 `ngOnInit` 載入——因為
    `@if (settingsOpen())` 包住整個 modal，每次開 modal 都會重新
    建立這個元件，效果等價）。
  - ✅ `memEditContent`（連同 `memoryOverview`/`memViewExpanded`/
    `memEditMode`/`loadMemoryOverview`/`toggleMemViewSection`/
    `memViewIsOpen`/`memViewFilePath`/`startMemEdit`/`cancelMemEdit`/
    `saveMemEdit` 一併抽成 `MemoryEditorComponent`。盤點後發現這些
    identifier 裡只有 `memEditContent`/`saveMemEdit`（以及餵資料給
    它們的 `loadMemoryOverview`）實際在 template 有用到——
    `memoryOverview`/`memViewExpanded`/`memEditMode`/
    `toggleMemViewSection`/`memViewIsOpen`/`memViewFilePath`/
    `startMemEdit`/`cancelMemEdit` 在 `app.html` 完全沒有引用，是既有
    的死碼；因為它們互相耦合（例如 `startMemEdit`/`cancelMemEdit`
    讀寫 `memEditContent`/`memEditMode`），整塊原樣搬過去，沒有藉機
    清理——清死碼不是這次任務範圍。唯一的跨元件依賴是
    `memViewFilePath()` 用到 `resolvedClaudeHome()`（同一個
    `engineStatus` 類型的 app-wide signal），改成 `@Input
    resolvedClaudeHome`，`app.html` 傳
    `[resolvedClaudeHome]="resolvedClaudeHome()"`。`loadMemoryOverview()`
    原本由 `App.openSettings()` 呼叫，同 telegram 的做法改成元件自己
    `ngOnInit` 載入。`.memview-textarea` 搬到 `src/styles.scss`
    （global）；`.memview-empty`/`.memview-path-row`/
    `.memview-proj-row`/`.memview-label`/`.memview-edit-actions`
    同樣是死 CSS，沒有搬動也沒有清理，維持原狀）。
  - ✅ `settingsForm`（63 refs）分段拆完成，不整塊搬。
    `settingsForm` 是 `AppSettings` 一般物件（非 signal），拆分策略：
    子元件用 `@Input() settingsForm!: AppSettings` 接住同一個物件
    參考，`[(ngModel)]` 直接 mutate 它——因為是同一個物件參考，不需要
    額外 `@Output` 事件把值傳回去，`App` 自己讀 `this.settingsForm`
    時就已經是最新值（跟 `App` 現有 template 直接綁 `settingsForm.x`
    的行為完全一致，純粹是把 UI 搬到別的檔案，狀態owner 沒變）。
    子區塊完成度：
    - ✅ AI Provider（#16，`ProviderSettingsComponent`：`provider`/
      `providerApiKey`/`providerModel`/`providerApiUrl` 4 個欄位，
      本來就是獨立 `.modal-section`，沒有跨頁依賴，是最單純的一塊）。
    - ✅ `.modal-body` 最上面那組沒包在 `.modal-section` 裡的標籤
      （`GeneralSettingsComponent`：`projectDir`/`claudeHome`/
      `workDir`/`backendPort`/`backendUrl`/`defaultAgent`/`theme`/
      `lang`/`enterToSend`/`openAtLogin`，連同巢狀的
      `<app-recent-work-dirs>` 一起搬）。混在裡面的幾個依賴分別這樣
      處理：
      - `pickProjectDir()`/`pickClaudeHome()`：純粹讀寫
        `settingsForm` 的方法，整個搬進新元件（只需要注入
        `ClaudeService`）。
      - `resolvedClaudeHome()`：跨頁 signal，同 memory-editor 的作法用
        `@Input resolvedClaudeHome`。
      - `dropdownAgents()`：`App` 自己在別處（agent 相關程式碼）還在
        用這個 computed，所以留在 `App`，用
        `@Input dropdownAgents: Agent[]` 傳唯讀快照。
      - `isElectron`：純環境判斷式（`!!(window as any).electronAPI`），
        不是 app state，新元件自己算一份就好，不用 `@Input`——這也讓
        `App` 裡原本的 `isElectron` 欄位變成死碼，順手一併移除（這是
        這次搬移直接造成的死碼，不是搬移前就存在的技術債，所以清掉
        跟前幾次「發現既有死碼但不清」的原則不衝突）。
      - CSS 踩坑：`.modal-body` 這個包裹 `<div>` 本身還留在 `App`
        自己的 template 裡（沒有整個搬走，只搬了裡面的 `<label>`
        內容），但巢狀選擇器 `.modal-body { label { ... } }`
        （scoped 在 `app.scss`）在新元件渲染出的 `<label>` 上會失效
        ——因為 Angular 的 emulated encapsulation 是靠比對 content
        attribute，不是單純比對 DOM 祖先關係，子元件渲染的元素不帶
        `App` 的 attribute。解法：把巢狀的 `label {...}` 規則獨立成
        一個純粹的全域 `.modal-body label {...}` 選擇器搬進
        `src/styles.scss`（純 CSS 選擇器只看 DOM 結構，不管 Angular
        attribute，所以子元件渲染的 `<label>` 一樣吃得到）；`.modal-
        body` 外層的 padding/flex 排版本身不用動，因為那個 `<div>`
        還是 `App` 自己渲染的。注意這個規則跟已經是全域的
        `.modal-section { label {...} }` **不是**同一份、故意沒有合併
        ——兩者巢狀範圍不同（`.modal-section` 版本的
        `input/select/textarea` 直接掛在 section 底下、還多一個
        `width:100%`，`.modal-body` 版本是掛在 `label` 底下、沒有
        `width:100%`），合併會悄悄改變其中一邊的樣式，所以維持兩份
        獨立規則。用 `page.evaluate` 讀 computed style 驗證過顏色/
        字重/背景色都跟搬移前的 scss 定義完全一致，不是肉眼猜的。
    - ✅ 「執行引擎範圍」區塊（`EngineSettingsComponent`：
      `settingsForm.engineMode`/`settingsForm.agentEngine`/
      `claudeBin`/`codexBin`/`apiKeyCmd`/`codexApiKeyCmd`）。決定不做
      `AppStateService`，用 `@Input engineStatus` 傳
      `engineStatus()` 的唯讀快照（純物件，不是 signal）——因為
      `engineOptionDisabled()`/`engineOptionLabel()` 這兩個方法
      **在別處也用到**（agent 編輯區的個別引擎覆寫選單，原 2235-2236
      行），所以 `App` 自己那份不能刪，新元件裡複製一份小的純函式版本
      （改讀 `this.engineStatus[name]` 而非 `this.engineStatus()[name]`，
      因為現在是 plain object 不是 signal call），連同兩個靜態 label
      對照表 `ENGINE_LABEL`/`ENGINE_REASON_LABEL` 一起複製——這兩個是
      常數 map，複製比硬做 `@Input` function 傳遞更乾淨。至於
      `engineMode()`（後端權威鎖定狀態，agent 編輯區在用，跟
      `settingsForm.engineMode` 是同名但不同的兩個東西）：這個元件根本
      沒用到它（原本以為會用到，盤點後發現這個區塊實際只讀
      `engineStatus()`，`engineMode()` 是被其他區塊——agent 編輯區——
      用，跟這次搬移無關），所以完全不用決定 `AppStateService` 這個
      問題，之前的評估過度謹慎了。這個區塊沒有用到任何新的 CSS class
      （全部沿用已經 global 的 `.modal-section`），不用動
      `styles.scss`。
    - ✅ 語音輸入（`SttSettingsComponent`：只有 `settingsForm.sttMode`
      單一欄位，本來就是獨立 `.modal-section`，沒有跨頁依賴，比
      AI Provider 還單純）。
    - ✅ 快速提示編輯區塊（`QuickPromptsEditComponent`：
      `quickPromptsForm`/`showQuickPromptsEdit`/`openQuickPromptsEdit`/
      `saveQuickPrompts`/`addQuickPrompt`/`removeQuickPrompt` 整塊搬
      走，包含 header 裡切換編輯狀態的「✏ 編輯」按鈕——因為那顆按鈕
      控制的 `showQuickPromptsEdit` 也搬走了，留在 `App` 裡沒意義。
      這組其實不是 `settingsForm` 欄位，是獨立於 `SettingsService` 的
      表單暫存，元件自己注入 `SettingsService` 讀/寫，完全不需要
      `@Input`/`@Output`。注意：`App` 自己還留著唯讀的
      `quickPrompts = computed(() => this.settings.get().quickPrompts)`
      給 chat 輸入區的快速提示按鈕用（app.html 原 1065 行），這個
      **沒有**搬——它本來就跟編輯 UI 是分開的兩塊狀態，只是共用同一份
      底層資料。搬移過程中發現這個 computed 讀的是 `settings.get()`
      （plain method，不是 signal），所以理論上 `saveQuickPrompts()`
      存檔後這個 computed 不會自動重新求值——這是搬移前就存在的既有
      行為（不是這次改壞的），原樣保留，沒有動手修。
      `.quick-prompts-edit`/`.qp-row`/`.qp-label-input`/
      `.qp-text-input`/`.qp-actions`（只有這裡用，整塊搬到
      `src/styles.scss`）；`.btn-xs`/`.btn-danger`（`App` 自己其他地方
      還在用，所以 `app.scss` 原本的定義沒動，只在
      `src/styles.scss` 加一份全域拷貝——過程中發現 `app.scss` 裡
      `.btn-danger` 其實被定義了兩次，後面那份（第 4334 行附近）蓋掉
      前面那份的每個重疊屬性，全域拷貝抄的是「實際生效」的後面那份，
      不是文件裡先出現的那份，這是既有的技術債，這次沒有清理）。

    **`settingsForm`（63 refs）子區塊拆分至此全部完成** ✅——
    `app.html` 裡已經找不到任何 `settingsForm.` 的直接 template
    綁定，全部搬進上面 6 個獨立元件（Provider/STT/QuickPrompts/
    General/Engine + 巢狀的 RecentWorkDirs）。`app.ts` 裡剩下的
    `this.settingsForm.x = ...` 都是合理留下的業務邏輯（拖放資料夾、
    `toggleLang()`/`toggleTheme()`、`openSettings()`/`saveSettings()`
    初始化與存檔、`_autoCorrectGlobalEngine()` 自動切換），不是
    UI 綁定，本來就不屬於這次「settings modal UI 拆分」的範圍。
  - 共通踩坑：`.modal-section` / `.modal-section-header` / `.btn-sm` /
    `.toggle-label` / `.tg-status-chip` / `.memview-textarea` 等
    settings modal 共用樣式已搬到 `src/styles.scss`（global）——
    Angular 的 emulated
    encapsulation 讓子元件收不到 `app.scss` 裡的 component-scoped
    樣式，每抽一個新元件如果用到這些 class 不用再重複搬；如果用到
    新的共用 class，記得順手搬過去。
- **settings modal 內部拆分至此全部完成**（9 個獨立元件：
  DiagnosticsPanel、AgencyImportPanel、RecentWorkDirs、
  TelegramSettings、MemoryEditor、ProviderSettings、SttSettings、
  QuickPromptsEdit、GeneralSettings、EngineSettings——數字對不上是
  因為 RecentWorkDirs 巢狀在 GeneralSettings 裡，兩邊都算）。拆分
  模式已經驗證足夠多次（純讀取、`@Input`/`@Output` 事件、shared
  object reference mutation、cross-page signal 唯讀快照、純函式/
  常數複製，五種模式都出現過），可以進到下一階段。
- **2026-07-18 修正：這個 app 完全沒有裝 Angular Router**（
  `RouterModule`/`provideRouter`/`Routes` 在 `frontend/src` 裡零匹配）。
  原規劃「memview/schedules 分頁…天然適合 `loadComponent` lazy
  route」的前提不成立——右側面板的分頁（TEAM/AGENT/SKILL/MCP/
  Scheduling/soul）全部是 `activeTab()` signal 驅動的 `@if` 區塊切換，
  不是路由。引入 Router 是遠比目前做過的任何一次抽取都大的架構變更
  （routing config、navigation guard、把分頁切換改成 router
  navigation、處理 deep-link 邊界情況），跟這個 Phase 目前「最小風險
  漸進抽取」的精神不符，這次沒有做，維持既有的 `@if` + tab signal
  架構。改用跟 settings modal 完全相同、已經驗證 9 次的模式：
  `@if (activeTab() === 'x')` 裡面包 `@defer (on immediate)` + 獨立
  元件，效果一樣是「非首屏分頁不進主 bundle」，只是觸發條件是 tab
  切換而非 modal 開關，機制上沒有差異。
  - 另外「memview」這個名字本身也是死的——`activeTab` signal 的型別
    union 裡雖然列了 `'memory'`/`'memview'` 兩個值，但 `app.html`
    完全沒有任何 `@if (activeTab() === 'memview')` 或
    `'memory'` 的區塊，也沒有任何按鈕會 `.set('memview')`——這是
    寫好但從沒接上 UI 的死型別，不是這次搬移弄壞的，原樣保留。
  - ✅ **Scheduling 分頁**（`SchedulePanelComponent`）：整塊排程
    UI——新增排程表單（prompt/cron 輸入、AI 自然語言轉 cron、
    cron 快速選項）+ 排程列表（執行/啟停/刪除）——全部搬進新元件，
    採跟 Telegram/QuickPromptsEdit 相同的「完全自包含」模式：
    `schedules`/`newSchedulePrompt`/`newScheduleCron`/`aiParsing` 這些
    state，以及 `translateCron`/`isNaturalLanguage`/`parseCronFromAI`/
    `addSchedule`/`deleteSchedule`/`toggleSchedule`/`runScheduleNow`
    這些方法，盤點後確認全部只有這個分頁在用（`grep` 全域找不到第二
    處引用），整塊搬走、注入 `ClaudeService` 自己打 API。
    `loadSchedules()` 原本由 `App.reload()`（app 啟動時）呼叫，改成
    元件自己 `ngOnInit` 載入——這不只是模式一致，還是真正的行為改善：
    原本排程資料不管使用者有沒有點過 Scheduling 分頁都會在啟動時抓，
    現在只有第一次真的點進這個分頁才會抓，跟「非首屏不做非必要工作」
    的 Phase 2 目標本身對齊。
    唯一新出現的模式：`addSchedule`/`deleteSchedule`/`toggleSchedule`/
    `runScheduleNow` 的錯誤處理原本呼叫 `App.showToast(...)`（app 全域
    的 toast 通知系統，這個分頁以外還有很多地方在用，不能直接搬），
    改成 `@Output() toast = new EventEmitter<{text,type}>()`，
    `app.html` 接 `(toast)="showToast($event.text, $event.type)"`——
    跟 diagnostics-panel 當初的 `(logMessage)` 是同一個「cross-cutting
    UI 效果透過 @Output 事件往上通知」模式，只是這次是 toast 而不是
    chat log。
    CSS 部分規模比之前任何一次都大：`.schedule-view`/
    `.schedule-form`/`.schedule-input`/`.schedule-add-btn`/
    `.schedule-header`/`.schedule-status`/`.schedule-actions`/
    `.cron-row`/`.cron-presets`/`.cron-preset-btn` 這組只有這個分頁在
    用，整塊搬到 `src/styles.scss`；但 `.panel-list`/`.panel-card`/
    `.card-name`/`.card-desc`/`.icon-btn-sm`/`.del-btn`/`.empty-hint`
    是 App 自己好幾個其他分頁（agents/teams/skills/soul/mcp）共用的
    卡片式列表樣式，`app.scss` 原本的定義不能動，只在
    `src/styles.scss` 加一份全域拷貝——`.panel-card` 的全域拷貝特意
    只抄基底規則（background/border/padding/hover），沒有抄
    `.selected`/`.active-in-chat`/`.expanded` 這些修飾用的變體，因為
    排程卡片從來不會套用那些 class，抄了也用不到。
  - ✅ **Teams 分頁**（`TeamPanelComponent`）：只搬「團隊卡片列表」這個
    UI，不是整個 Teams 功能——盤點後發現 Teams 比 Scheduling 複雜
    得多，不是全部自包含：
    - `sortedTeams()`（原本的 computed）依賴 `rightPanelFilter()`，
      這是整個右側面板（agents/skills/mcp/teams 共用）的搜尋框
      signal，不能搬——所以排序/篩選邏輯留在 `App`，只把算好的結果
      用 `@Input() teams: Team[]` 傳下去（跟 `dropdownAgents` 當初
      的處理方式一樣）。
    - `expandedTeams`/`toggleTeamExpanded`（卡片展開/收合狀態）純粹
      是這個列表自己的 UI 狀態，別處沒人用，整個搬進元件、改名
      `toggleExpanded`。
    - `selectTeamLeader`（點「💬 團隊對話」）、`toggleTeamFavorite`
      （收藏切換）、`openTeamEditor`（開編輯 Team 的另一個獨立
      modal，`app.html` 2318 行左右，跟這個列表完全是分開的區塊）
      這三個**不能搬**——`selectTeamLeader` 深度耦合 chat/session
      狀態（呼叫 `saveCurrentTab()`、建立新對話分頁），
      `openTeamEditor` 開的是列表以外的另一塊 UI，`toggleTeamFavorite`
      寫回 `App` 自己持有、別處也會讀的 `teams` signal。三個都改成
      `@Output`（`chat`/`favorite`/`edit`）往上通知，`App` 收到事件後
      呼叫原本就有的方法——元件本身完全不碰 `ClaudeService`，是純
      presentational 元件，跟之前「自包含」的元件（Telegram/
      QuickPromptsEdit/Schedule）是不同的模式。
    CSS：`.card-header-row`（補進既有全域 `.panel-card` 巢狀規則
    裡）、`.agent-action-btns`/`.agent-activate-btn`/`.agent-edit-btn`/
    `.agent-fav-btn`/`.empty-guide*`/`.sticky-create-btn-wrap`/
    `.team-members-row`/`.team-member-chip`（含巢狀 `.member-dot`/
    `.member-role`）都是 App 自己其他分頁（agents/skills/chat 空狀態）
    共用的樣式，原定義不動，`src/styles.scss` 加全域拷貝；
    `.team-fav-btn`/`.team-leader-preview` 盤點後發現根本沒有對應的
    scss 規則（純裝飾用的 class，一直是空的），不用搬。
    既有 e2e 測試「Team 卡片可以切換最愛」直接驗證了 `(favorite)`
    這條線路整條可用；手動另外驗證了空狀態、展開/收合、`(edit)`
    事件（編輯既有 Team 與建立新 Team 兩條路徑都會正確開啟
    Team Editor modal）——`(chat)` 沒有另外寫測試，因為它跟已驗證
    過兩次的 `(favorite)`/`(edit)` 是完全相同的事件轉發寫法，風險
    判斷上不需要重複驗證。
  - ✅ **Skills 分頁**（`SkillPanelComponent`）：目前為止耦合最深的一個
    ——不是完全自包含，也不是單純「presentational + @Output」，是
    兩者的混合：
    - `sortedSkills()` 依賴 `rightPanelFilter()` **和**
      `selectedAgent()`（目前選中的 agent），排序邏輯留在 `App`，結果
      用 `@Input() skills: Skill[]` 傳下去（跟 teams 一樣）。
    - `isSkillLinkedToActiveAgent`/`isSkillInActiveAgentFrontmatter`/
      `isSkillInTab` 這三個方法在原本的 template 裡是「每個 skill 卡
      呼叫一次」，若直接當 `@Output`/function `@Input` 傳會很醜。改用
      新模式：`App` 新增三個 `computed()`（`linkedSkillIds`/
      `frontmatterSkillIds`/`tabSkillIds`，都是 `Set<string>`），
      元件收 `@Input() linkedSkillIds: Set<string>` 等三個唯讀快照，
      模板裡改成 `.has(s.id)` 查表，不再呼叫方法。這是首次為了拆分
      新增 computed（不是單純移動既有的），純衍生自既有 signal，行為
      應該不變。
    - `expandedTranslation`/`translateExpanded` 發現**橫跨 agents 和
      skills 兩個分頁共用**（agents 分頁原 1292-1302 行也在用同一份
      翻譯狀態）——不是 teams 那種「純本地」也不是「純跨頁唯讀」，是
      「跨分頁但可寫」，所以留在 `App`，`@Input() expandedTranslation`
      +`@Output() translate` 雙向處理。
    - `expandedSkillId` 也一樣不能像 teams 的 `expandedTeams` 那樣搬成
      本地 signal——agents 分頁有個「跳到某個 Skill 詳情」的連結
      （`jumpToSkillDetail`，原 1312 行）會直接寫入這個 signal，是
      跨分頁寫入，所以留在 `App`，用 `@Input`/`@Output` 處理。
    - `getUsedMcps`/`SKILL_MCPS_MAP`（靜態查表）複製一份到元件裡
      （`App` 自己在 `sortedMcpServers` 內部還要用，不能拿掉），跟
      `ENGINE_LABEL` 是同一個處理方式。
    - `openSkillEditor`/`jumpToMcpDetail`（跳到別的分頁）不能搬，改
      `@Output`（`edit`/`jumpToMcp`）。
    CSS：`.card-detail-content`/`.detail-desc`/`.detail-actions`/
    `.linked-section`/`.linked-title`/`.link-item`（巢狀在
    `.panel-card` 底下）、`.bind-btn`/`.agent-mapping-summary`/
    `.mapping-chip`/`.translate-btn`/`.translating` 都跟 agents 分頁
    共用，原定義不動、`src/styles.scss` 加全域拷貝；
    `.binding-btns`/`.agent-skill-badge`/`.auth-dot` 盤點後確認只剩
    skill-panel 在用，整塊搬。`@keyframes blink` 動畫改名成
    `skill-panel-blink`——Angular 的 emulated encapsulation **不**
    幫 `@keyframes` 加隔離屬性，如果全域 `styles.scss` 跟
    `app.scss` 各自定義同名 keyframe，理論上有互相蓋掉的風險，改名
    避免這個問題，不是這次才發現的既有 bug，是預防性處理。
    手動 e2e 涵蓋卡片渲染（含 mapping chip）、展開/收合、翻譯（含
    還原原文）、跳轉到 MCP 分頁（`jumpToMcp` 觸發跨分頁切換）、
    加入/移出目前對話（`bind-btn` 切換）、開啟 Skill 編輯器
    （過程中發現 `openSkillEditor` 會先打一次 `GET /api/skills/:id`
    才開 modal，不是純本地操作，一開始漏 mock 這個 API 導致 modal
    開不起來，補上後才過）。
  - ✅ **Agents 分頁**（`AgentPanelComponent`）：結構跟 skills 幾乎一樣
    （果然如預期），但實際盤點後發現比 skills 更單純一些：
    - `sortedAgents()` 依賴 `rightPanelFilter()`，留在 `App`，結果用
      `@Input() agents: Agent[]` 傳下去。
    - `expandedAgentId`/`expandedTranslation` 一樣不能變成本地
      signal——盤點時發現一個新的跨分頁寫入來源：`window:keydown`
      全域 Escape 鍵監聽器（`onGlobalKey`）會同時重置
      `expandedAgentId`/`expandedSkillId`/`expandedMcpId` 三個，跟
      分頁切換無關，是全域快捷鍵，一定要留在 `App`。用
      `@Input`/`@Output` 處理，跟 skills 同一招。
    - **比 skills 簡化的地方**：原本猜測 `getPermSkills(agentId)`/
      `getPermMcps(agentId)` 需要跟 skills 的
      `linkedSkillIds`/`frontmatterSkillIds` 一樣包成 `Set` 傳下去，
      但盤點後發現這兩個方法其實就是
      `this.agents().find(x => x.id === id)?.skills ?? []`——單純讀
      agent 物件自己的欄位。既然元件已經透過 `@Input() agents` 拿到
      完整的 agent 物件了，模板裡直接用 `a.skills ?? []` /
      `a.mcp ?? []` 取代方法呼叫即可，不需要額外的 computed 或
      Set——比 skills 少了三個新 computed。
    - `activateAgent`（啟動 Agent，套用 Soul/MCP/Memory、建立新對話
      分頁）、`toggleFavorite`、`openAgentEditor`、
      `toggleSkillPermForAgent`/`toggleMcpPermForAgent`、
      `jumpToSkillDetail`/`jumpToMcpDetail`（跳到別分頁）都不能搬，
      改 `@Output`（`activate`/`favorite`/`edit`/`removeSkillPerm`/
      `removeMcpPerm`/`jumpToSkill`/`jumpToMcp`）。
    - `getAgentSoulContent(soulId)`：純讀 `souls()` signal 找對應
      content，只在展開的詳情卡才會用到（低頻率查詢），直接把
      `souls: SoulProfile[]` 整包當 `@Input` 傳，元件自己
      `.find()`，不用像 skill 的三個布林檢查那樣包 Set——這個是
      「單次查找」不是「逐項在迴圈裡查」，包 Set 反而多此一舉。這也
      讓 `App` 裡原本的 `getAgentSoulContent` 方法變成死碼，順手移除
      （跟 `isElectron` 一樣，是這次搬移直接造成的死碼）。
    CSS：**這次沒有新增任何全域樣式**——agent-panel 用到的每一個
    class（`.card-header-row`/`.agent-action-btns`/
    `.agent-activate-btn`/`.agent-fav-btn`/`.agent-edit-btn`/
    `.agent-mapping-summary`/`.mapping-chip`/`.card-detail-content` 一
    整組/`.translate-btn`/`.translating`/`.del-btn`/`.empty-guide*`/
    `.sticky-create-btn-wrap`/`.btn-secondary`）都已經在 teams/skills
    那兩輪搬過了，盤點後確認一個字都不用改 `styles.scss`/`app.scss`。
    手動 e2e 涵蓋卡片渲染（含 mapping chip）、展開+翻譯、跳轉到
    Skills 分頁（跨分頁）、最愛切換、開啟 Agent 編輯器；`activate`
    （啟動 Agent）沒有另外寫測試，因為跟已驗證過的
    `favorite`/`edit`/`jumpToSkill` 是同一種簡單事件轉發寫法。
  - ✅ **Soul 分頁**（`SoulPanelComponent`）：卡片列表 + 下方編輯器的
    雙區塊版面，`souls`/`selectedSoulId` 兩個 signal 同時被 Agent 分頁
    的 `getAgentSoulContent` 讀取，是跨分頁共用狀態，維持在 `App`
    用 `@Input` 往下傳，不搬進元件。`soulSplitRatio`（上下區塊高度
    比例，由全域 `mousemove`/`mouseup` 監聽器拖曳調整）也留在 `App`——
    拖曳邏輯本身跟切分頁無關，是全域行為。
    - 過程中抓到兩個真的產品 bug（不是測試問題），都是靠 Playwright
      搭配 Angular dev-mode `ng.getComponent()` 直接讀 live component
      state 才定位到：
      1. **高頻文字狀態不能整包用 `@Input`/`@Output` 來回傳遞**。原本
         把編輯器草稿內容（`soulDraft`）設計成 `@Input()
         soulDraft`/`@Output() draftChange`，在快速輸入下會出現
         stale-echo race——子元件的 `[ngModel]="soulDraft"` 被父層
         回傳的舊值蓋掉，Save/Discard 按鈕在使用者明明已經編輯過的
         情況下仍顯示 disabled。修法：草稿文字（`localDraft`）完全
         留在元件本地狀態，只靠 `ngOnChanges` 監聽
         `selectedSoulId`（不是 `souls` 陣列）變化時重置——只在切換
         選取的靈魂檔案時重置，避免打字打到一半被 `souls` 陣列的
         其他變動打斷。`soulDraftSaved`（純布林、每次編輯 session
         只會 true→false 一次）維持 `@Input`，因為它是 idempotent，
         不會有 race。
      2. **`@Output()` 不能取跟原生 DOM 事件同名的名字**。原本
         `@Output() select`/`@Output() wheel` 分別對應「選取某個靈魂
         卡片」跟「滾輪捲動清單」，但 `select`/`wheel` 本身是真的、
         會冒泡的原生 DOM 事件——在下方 textarea 裡按 Ctrl+A
         全選文字時觸發的原生 `select` 事件冒泡到
         `<app-soul-panel>` host element，被 `(select)="..."`
         這個 Angular Output binding 誤接住，導致
         `App.selectedSoulId` 被寫入一個原生 `Event` 物件而不是
         靈魂檔案的 id 字串（畫面上會看到 `[object Event].md`）。
         修法：兩個 Output 分別改名為 `pickSoul`/`soulWheel`，避開
         跟原生事件同名。
    - 抓到這個 bug 之後回頭對所有已抽出的元件做了一輪
      `grep -rn "@Output() "` 掃描，檢查是否有其他同名衝突（比對
      click/change/input/submit/focus/blur/keydown/keyup/drag/
      drop/touch*/scroll/resize/load/error/toggle/close 等常見原生
      事件名稱）——只有 soul-panel 這兩個，另外在更早抽出、已合併的
      `RecentWorkDirsComponent` 發現同款 `@Output() select`（該元件
      目前模板裡沒有可選取文字的子元素，理論上不會被觸發，但屬於同一
      個已證實為真的 bug pattern，順手一併改名為 `pickDir` 並同步更新
      `general-settings.html` 的呼叫端，防患於未然）。
    CSS：`.soul-panel`/`.soul-upper`（含 `.soul-hint`）/
    `.soul-add-btn`/`.soul-rename-btn`/`.soul-rename-input`/
    `.soul-filename`/`.soul-divider`/`.soul-lower`/`.lower-empty-hint`/
    `.edit-lower-header`/`.unsaved-dot`/`.edit-lower-actions` 從
    `app.scss` 整段搬到 `styles.scss`；`.memory-editor` 因為還被
    `App` 剩餘模板（agent 編輯器裡的 soul 內容欄）直接使用，原始定義
    留在 `app.scss`，`styles.scss` 加一份字面值複製（SCSS 變數轉字面
    hex 色碼）。另外發現一個全域 `@keyframes blink` 沒有被 Angular
    的 emulated view encapsulation 限定作用域，為避免跟其他元件未來
    可能新增的動畫撞名，改名為 `skill-panel-blink`。
    手動 e2e 涵蓋選取卡片、編輯草稿、儲存（清除 unsaved 標記）、
    捨棄（還原上次儲存內容）、原地重新命名、刪除、新增全部走過。
  - ✅ **MCP 分頁**（`McpPanelComponent`，22.93 kB，目前最大的 lazy
    chunk）：如預期是四個分頁裡最複雜的一個，涵蓋 App 管理定義列表
    （雙引擎同步）、外部／本地 API 雙卡片列表、可拖曳分隔線、Docker
    設定子表單、日誌檢視器（含 2.5 秒自動輪詢）、MCP JSON-RPC 調試
    主控台。
    - 前置重構：`McpServer`/`McpServerDef`/`McpTool`/`McpType` 四個
      型別原本一半在 `app.ts`（`McpServer`/`McpTool`/`McpType`）一半在
      `claude.service.ts`（`McpServerDef`），為了讓新元件能直接從
      `claude.service` import（跟 `Agent`/`SoulProfile` 等既有慣例
      一致，避免元件回頭 import `app.ts` 造成循環依賴），把前三個也
      搬進 `claude.service.ts`，`app.ts` 改成統一從那裡 import。
    - 狀態分工採三種模式混合：
      1. **維持 App**（跨分頁或有外部呼叫源）：`mcpServerDefs`/
         `externalMcpServers`/`localMcpServers`/`expandedMcpId`（跟
         agents/skills 共用同一個全域 Escape 鍵重置邏輯）/
         `mcpLoading`/`mcpSplitPct`。`startMcp`/`stopMcp`/`loadMcp`
         也留在 App，因為分別被 agent 啟用流程（`activateAgent`）和
         `reload()`/初始化流程呼叫，不只是這個分頁自己用。
      2. **精算 Set 作為 @Input**（沿用 skill-panel 的手法）：
         `isMcpRequiredByActiveAgent`/`isMcpLinkedToActiveAgent`/
         `isMcpInTab` 三個原本逐卡呼叫的方法，改成 App 端各自
         precompute 一個 `Set<string>`（`requiredMcpNames`/
         `linkedMcpNames`/`sessionMcpNames`），元件內用
         `.has(name)` 做 O(1) 查找。
      3. **完全下放給元件**（自己注入 `ClaudeService`）：Docker
         設定（`localMcpConfigs`/`localDockerConfig`/
         `editingDockerMcp`/`openDockerConfig`/`saveDockerConfig`，
         盤點確認 `localMcpConfigs` 只有這個分頁自己讀寫，於是元件
         改成自己在 `ngOnInit` 呼叫 `getLocalMcpConfig()`，不再靠
         App 的 `loadMcp()` 帶進來）、日誌檢視器（`mcpLogOpen`/
         `mcpLogLines`/輪詢 interval）、MCP Live Debugger（RPC
         method／params／result／pending-auth 全部狀態，`sendMcpRpc`
         呼叫本身無跨分頁依賴）。Docker 設定儲存的成功/失敗提示改用
         `@Output() toast`（沿用 `SchedulePanelComponent` 已有的
         `{text, type}` 慣例），由 `app.html` 接手
         `(toast)="showToast($event.text, $event.type)"`。
    - **順手修正一個真的 bug**：日誌檢視器原本有個 T40 健檢修復註解
      ——`closeSettings()`（關閉 ⚙ 設定 modal）會強制清掉
      `mcpLogOpen`/輪詢 interval，理由是「開著 MCP 記錄檢視器再關閉
      Settings，計時器會永遠留著」。但 MCP 分頁本身跟 Settings modal
      是兩個完全不相關的畫面區域（現在的版面底下 MCP 清單早就搬到
      右側面板分頁，不在 Settings modal 裡了），這個清理掛在
      `closeSettings()` 只是巧合地堵住了其中一種情境——真正該觸發清理
      的時機是「離開 MCP 分頁」，而舊寫法完全沒處理這個情境（切到
      Skills/Agents 分頁時，開著的日誌輪詢照樣繼續打後端，直到使用者
      剛好開關一次 Settings）。改成元件的 `ngOnDestroy` 清掉 interval
      後，正確觸發時機變成「Angular 銷毀這個元件」——也就是
      `@if (activeTab() === 'mcp')` 外層條件變 false（切到別的分頁）
      或整個 app 銷毀，兩者都比原本的 `closeSettings()` 更貼近問題
      本身，也讓 `closeSettings()` 恢復成單純的
      `settingsOpen.set(false)`。
    CSS：`.mcp-view`（原本分散在三處、各自新增不同屬性的三段定義，
    照原順序整段搬移，不合併，確保 cascade 疊加結果不變）/
    `.mcp-header`/`.mcp-section`(+`-external`/`-local` variant)/
    `.mcp-section-title`/`-icon`/`-count`/`-hint`/`.mcp-empty`/
    `.mcp-footer-hint`/`.ctrl-btn`(+四個狀態 modifier)/
    `.docker-cfg-section`/`-form`/`-label`/`-input`/`-display`/`-row`/
    `.mcp-status-lamp`（目前生效的第二個定義）/`.lamp-green`/`-yellow`/
    `-red`/`-off`/`@keyframes lamp-pulse`/`.mcp-pane`/`-bottom`/
    `.mcp-divider`(+`-track`/`-grip`)/`.mcp-card-compact`/`.mcc-row`/
    `-name`/`-actions`/`-detail`/`-url`/`-tools-label`/`-tool-row`/
    `-tool-desc`/`.mcp-log-panel`/`-header`/`-body`/`-line`/`-empty`/
    `.mcp-agent-hint` 從 `app.scss` 整段搬到 `styles.scss`。盤點過程
    中發現 `app.scss` 裡這個分頁的 CSS 歷經至少三輪重新設計
    （「T10 MCP Panel」→「MCP Panel Redesign」→「MCP Split Panes」/
    「MCP Compact Card」），中間留下大量從未被目前模板引用的死碼
    （`.mcp-list-output`/`.mcp-status-dot`/`.mcp-group-label`/
    `.mcp-ctrl-btns`/`.mcp-type-chip`/`.local-mcp-card`/
    `.local-mcp-name-col`/`.mcp-ctrl-bar`/`.local-mcp-meta`/
    `.docker-image-tag`/`.mcp-port-tag`/`.mcp-log-toggle`，以及一個
    帶 `&.lamp-on` modifier、已被取代的舊版 `.mcp-status-lamp`
    定義）——逐一用 grep 對照目前模板確認後，這些死碼原封不動留在
    `app.scss`（不屬於這次抽取範圍，且部分死碼跟仍在使用的同名
    selector 交錯，貿然刪除風險大於效益），只搬移經 grep 驗證仍在
    使用的規則。
    手動 e2e 涵蓋 App 管理定義列表渲染/新增/刪除、外部/本地卡片渲染
    與展開、綁定到目前對話、本地伺服器啟動、Docker 設定顯示與編輯
    儲存（含 toast 驗證）、日誌檢視器開啟與輪詢、RPC 調試主控台送出
    請求、整理按鈕。

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
