# SDD：Runtime、Layout 與串流可靠性優化

## 目標

讓 local dev、Docker dev、production 與 Playwright 使用可追蹤且一致的
runtime URL；確保三個主要 layout 分界的滑鼠命中區貼在真實邊界；降低前端
串流解析重複與取消時的資源殘留；所有改動都必須能以自動化測試驗證。

## 規格與驗收條件

### Runtime URL

- local dev 預設前端 `http://127.0.0.1:4200`、後端 `http://127.0.0.1:8765`。
- Docker dev 預設前端 `4201`、後端 `8761`；production 預設前端 `4200`、後端 `8760`。
- Electron 不得在 Docker dev 路徑硬編碼 URL，必須使用 runtime config。
- Playwright 使用 `PLAYWRIGHT_BASE_URL`，並以相同 port 啟動或連接前端。
- Docker dev 啟動等待後端超過 120 秒必須輸出診斷並結束，不能無限輪詢。

### Layout resize

- sidebar、right panel 的 hit area 寬度為 10px，visual track 為窄線並貼在面板邊界。
- input resize 的 hit area 高度為 10px，visual track 貼在輸入區與內容區邊界。
- sidebar 範圍 `200..560px`、right panel 範圍 `280..700px`、input 範圍 `100..400px`。
- 拖曳期間顯示對應 `col-resize` / `row-resize` 游標，放開後清除。
- hit area 不得依賴父層 `overflow: hidden` 外溢才能被命中。

### SSE

- 所有 fetch-based SSE endpoint 使用同一個 parser。
- parser 必須容忍事件跨 ReadableStream chunk、CRLF 換行及多個 `data:` 行。
- HTTP 非 2xx、無 response body、非取消例外要呼叫 `onError`。
- 呼叫取消函式後不得呼叫 `onDone`，並且應取消 reader。

## 設計決策

- Electron runtime config 只接受 `http` / `https` URL，無效值回退到安全的 loopback 預設值。
- Docker dev 與 production 使用不同預設 host port，避免兩個 profile 同時執行時誤連環境。
- layout resize state 移到 `LayoutResizeService`；`App` 保留 template-facing method，讓切片不破壞現有 template contract。
- SSE parser 放在獨立 utility，服務層只負責 endpoint、request body 與 callback wiring。

## 驗證命令

```bash
python -m pytest
cd frontend && npm test -- --watch=false --no-progress
cd frontend && npm run e2e -- resize.spec.ts --project=chromium
```

## 後續切片

1. 將 `app.ts` 的 chat/session/settings/MCP state 依 feature 邊界移到 facade/service。
2. 將 backend `main.py` 的 route registration、process lifecycle 與 domain service 完全分離。
3. 為 background task 建立集中註冊與 graceful shutdown，清除 Windows asyncio transport warnings。
4. 在 CI 加入格式檢查、覆蓋率門檻與 Docker smoke test。
## 實作紀錄（2026-08-27）

本輪 SDD 第一階段已完成，範圍涵蓋執行環境、拖曳分界、SSE 串流與背景任務生命週期。

### 已完成

- 統一 Electron、Docker、Playwright 與啟動批次檔的前後端 URL/port 設定，並保留舊有環境變數的相容 fallback。
- 修正內容區、輸入區與左右側欄的拖曳 hit area，使可拖曳區貼齊實際邊界；視覺軌道與紫色 grip 維持窄版樣式。
- 將版面尺寸與滑鼠拖曳狀態集中至 `LayoutResizeService`。
- 將五個 SSE endpoint 共用 chunk-safe parser，處理 CRLF、多行 `data:`、`[DONE]` 與 HTTP error。
- 集中追蹤後端 background tasks，於 shutdown 時取消、等待並清理。
- 補上單元測試、Playwright resize E2E 測試，以及根目錄測試/型別檢查指令。

### 驗證結果

| 驗證項目 | 結果 |
| --- | --- |
| `npm run test:frontend` | 通過，14 tests passed |
| Playwright `resize.spec.ts`（Docker dev） | 通過，2 tests passed |
| `npm run typecheck` | 通過 |
| `npm run frontend:build` | 通過 |
| 後端 targeted tests | 通過，2 tests passed |
| `docker compose --profile dev config` | 通過，解析為 backend `8761` / frontend `4201` |
| Electron syntax check | 通過 |
| 後端 baseline full suite | 398 passed、4 個 Windows asyncio transport warnings |

### 已知事項與後續

- 目前工作區仍有使用者原先的修改與資料庫暫存檔變更，未予覆寫或回復；本輪也未建立 commit。
+ 根目錄 `npm test` 與 team-chat 單測目前都會長時間無進度而中止，需另開一個測試隔離/效能調查 slice。
- `.env` 仍可使用舊的 `BACKEND_HOST_PORT=8761` fallback；若要明確區分 profile，建議加入 `BACKEND_DEV_HOST_PORT=8761`、`FRONTEND_DEV_HOST_PORT=4201`，production 使用 `BACKEND_HOST_PORT=8760`。
- `backend/main.py` 中少數 `shell=True` 呼叫是既有平台相容邏輯，尚未在本輪改動；後續應進行安全審查。
- 下一輪依序建議：拆分大型 frontend/backend 模組、補 lint/format/coverage gate，再處理完整後端測試的 hang。

## 第二階段實作紀錄（2026-08-27）

本輪接續處理第一階段留下的資源生命週期、安全性與前端狀態邊界問題。

### 已完成

- 新增 `backend/task_registry.py`，由中央 registry 管理主程式與 modular routes 建立的 background tasks；shutdown 時統一取消、等待與清理。
- `routes/agents.py`、`routes/teams.py`、`routes/team_planning.py`、`routes/resource_sync.py`、`message_bus.py` 與 MCP drain 改用中央 task registry。
- API key command 改用參數化 `shell=False` 執行，拒絕 shell 控制字元，避免 `apiKeyCmd`／`codexApiKeyCmd` 形成任意命令鏈結。
- 新增 `SessionFacade`，集中 session history 的查詢、分頁、引擎篩選與 refresh；`App` 保留 template-facing 介面。
- 新增 `routes/registration.py`，將核心 route grouping、aiohttp resource 建立與 CORS 綁定從 `main.py` 抽出。
- Windows Terminal monitor 改用 argv 陣列與 PowerShell 單引數命令，移除最後一個 `shell=True`。
- 補上 task registry、安全命令測試，並將維護中的前端切片納入 CI Prettier gate。
- root backend test 改用工作區 `.pytest-tmp/backend-tests`，避免 Windows/npm 子程序無法寫入系統暫存目錄。

### 第二階段驗證

| 驗證項目 | 結果 |
| --- | --- |
| task registry + session history targeted tests | 通過，9 tests passed |
| security + Codex API key + task registry targeted tests | 通過，14 tests passed |
| frontend unit tests | 通過，14 tests passed |
| frontend typecheck/build | 通過 |
| maintained frontend Prettier gate | 通過 |
| Python compile + shell/task scan | 通過；無 `shell=True` 或散落的 `asyncio.create_task` |
| `npm test` | 通過；backend 403 passed、frontend 14 passed，Windows asyncio transport warnings 已清除 |

### 尚未完成

1. `frontend/src/app/app.ts` 尚需繼續拆分 chat、settings、MCP 等 facade；本輪先完成 session slice。
2. 核心 route registration 已抽成 `backend/routes/registration.py`；`main.py` 的 process lifecycle 與 domain service 仍可再拆分。
3. CI 尚缺正式的 backend lint/format 規則與有意義的 coverage threshold；目前已先建立 coverage 報告入口，門檻暫為 0 以避免改變既有基線。
4. 完整 suite 已恢復通過；Windows subprocess/pipe cleanup 已完成第一輪修正，仍可在後續切片補充更細的 subprocess lifecycle 測試。

### 第二階段收尾備註

- 路由註冊已完成第一層抽離；後續若要完全拆分，應再把 process lifecycle 與 domain service 移出 main.py。
- 安全掃描與編譯檢查已確認目前後端沒有 shell=True，也沒有散落的 asyncio.create_task。

## 第三階段驗證紀錄（2026-08-28）

- 修正 team-chat 基準測試的外部 CLI 隔離，避免測試因本機 Codex 登入狀態而無限等待。
- 修正 session index 增量同步條件；來源 mtime 未變時不再因缺少 project_path 而重複解析大型歷史紀錄。
- 實際驗證：backend 403 passed、frontend 14 passed；完整 npm test 通過。
- 修正 Claude/Codex engine 在 task cancellation 後未必回收 subprocess 的路徑；`CancelledError` 不會被一般 `Exception` 捕捉，因此改以 `finally` 統一 kill/wait child process。
- targeted cleanup tests 4 passed；完整 `npm test` 再驗證為 403 + 14 passed，Windows Proactor transport warnings 已清除。

### 第三階段收尾

- `backend/database.py` 的 session index 改以 source mtime 作為增量同步 authoritative key，避免既有紀錄缺少 `project_path` 時重複解析大型歷史檔。
- `tests/test_backend.py` 的 team-chat endpoint 測試改用 fake engine 隔離外部 Codex CLI，避免測試依賴本機登入狀態或外部程序。
- `backend/engines/claude_engine.py` 與 `backend/engines/codex_engine.py` 在串流取消/例外時皆會回收子程序，降低 Windows event loop 關閉時的 pipe transport 殘留。

### 第三階段驗證結果

| 驗證項目 | 結果 |
| --- | --- |
| cleanup targeted tests | 通過，4 tests passed |
| `npm test` | 通過，backend 403 passed、frontend 14 passed、無 Windows asyncio transport warnings |
| Python compile | 通過，Claude/Codex engines |
| `git diff --check` | 通過 |

## 第四階段微優化紀錄（2026-08-28）

- `frontend/src/app/app.ts` 的 MCP 關聯排序由陣列 `includes()` 改為 `Set.has()`，避免每次比較都線性掃描已使用 MCP 清單。
- `linkedMcpNames` 改為一次彙整目前 Agent 的技能、永久 MCP 綁定與 active tab MCP，交給 `mcp-panel` 做 O(1) membership lookup；不再逐張卡片反查完整關聯鏈。
- 新增回歸測試，確認三種 MCP 關聯來源仍會合併且不重複。

### 第四階段驗證結果

| 驗證項目 | 結果 |
| --- | --- |
| frontend unit tests | 通過，15 tests passed |
| `npm run typecheck` | 通過 |
| `npm run frontend:build` | 通過 |
| backend full suite（warning-as-error） | 通過，403 tests passed |

### 後續切片

1. 將 `app.ts` 的 chat/settings/MCP API 操作再依 feature 邊界移到 facade/service；目前只完成 MCP derived-state 計算優化。
2. 將 backend `main.py` 的 process lifecycle 與 domain service 移出 route/bootstrap 模組。
3. 在 CI 加入正式 backend lint/format 規則、有意義的 coverage threshold 與 Docker smoke test。

## 第五階段：process lifecycle 與 MCP facade（2026-08-28）

### Backend subprocess cleanup

- 新增 `backend/process_lifecycle.py` 的 `terminate_and_reap()`，統一處理仍在執行的子行程：先 best-effort kill，再 await `wait()`，並以 timeout/例外隔離避免清理覆蓋原始錯誤。
- 套用到 chat/team legacy subprocess、MCP CLI/list/handshake、Codex models、engine availability，以及 Claude/Codex engine 的取消與 finally 路徑。
- 補上 running、already-finished、kill 失敗三種測試，避免取消流程只 kill 不 reap。
- 保留 `availability.safe_kill_process` 的 legacy module-level 匯出，維持既有測試與呼叫者相容性；非同步路徑改走集中 helper。

### Frontend MCP facade

- 新增 `frontend/src/app/mcp-facade.service.ts`，集中 MCP CLI 清單、managed server definitions、編輯器 signals、KV 解析、payload 正規化與 create/delete API 操作。
- `App` 保留原有 template-facing 欄位與方法名稱，HTML 不需大幅改寫；實作改由 facade 持有與轉交。
- 新增 facade 單元測試，覆蓋清單載入、編輯器 reset、stdio/http payload 正規化與 saving 狀態。

### 第五階段驗證結果

| 驗證項目 | 結果 |
| --- | --- |
| subprocess lifecycle targeted tests | 通過，43 tests passed |
| backend full suite | 406 passed；另有 1 個 Windows Proactor transport teardown warning |
| frontend unit tests | 通過，18 tests passed |
| `npm run typecheck` | 通過 |
| `npm run frontend:build` | 通過 |
| `git diff --check` | 通過（僅 Git 顯示既有 LF/CRLF 轉換提示） |

嚴格 warning-as-error 序列在 405 tests passed 後，於排程測試 setup 將同一個 teardown warning 升級為 1 error。單獨執行受影響的排程測試，以及 lifecycle 相關 targeted tests 均通過；該 Proactor 警告尚未視為已完全消除，列入下一輪測試 teardown 專項。
