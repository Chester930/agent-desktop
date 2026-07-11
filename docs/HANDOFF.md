# Claude 桌面版 — 計畫任務書（進版交接用）

> **快照時間**：2026-07-03（Part A 全數驗證完成，Part B 確認無法照原設計實作）
> **當前分支**：`master` @ 最新（main.py 模組化拆分 + Part A pooled SDK 遷移三個呼叫點全部完成並驗證）
> **用途**：換機續做與交接用的完整進度記錄

---

## 一、專案概述

**Claude 桌面版** 是 Claude Code CLI 的 Electron 圖形介面，支援多面板對話、Agent/Skill/MCP 管理、Team 多代理人協作、排程、LINE Bot 等功能。

| 層級 | 技術 |
|------|------|
| Frontend | Angular 22 + SCSS |
| Backend | Python 3.10+ / aiohttp |
| Desktop | Electron 42 |
| 容器化 | Docker Compose |

---

## 二、後端 API 完成狀態

### P1 — Agent Mapping（後端）

| 項目 | 狀態 | Handler | 備註 |
|------|------|---------|------|
| P1-B1：解析 agent frontmatter (skills/memory/mcp/soul/output_memory) | ✅ **已完成** | `_agent_dict()` | `soul` 欄位已修正：讀 frontmatter，fallback 到 agent id |
| P1-B2：`GET /api/agents/:id` | ✅ **已完成** | `handle_agent_get` | 回傳 `_agent_dict()` 的完整結構 |
| P1-B3：`PUT /api/agents/:id` | ✅ **已完成** | `handle_agent_put` | 更新 name/description/soul/skills/memory/mcp/output_memory/tools |
| P1-M1：`POST /api/agents` | ✅ **已完成** | `handle_agent_post` | 建立空白 `.md` 模板，含完整 frontmatter |
| P1-M2：`DELETE /api/agents/:id` | ✅ **已完成** | `handle_agent_delete` | 刪除 agent `.md` 檔 |
| P1-S1：解析 skill frontmatter (mcp/memory/output_memory) | ✅ **已完成** | `_skill_dict_from_file/dir()` | 支援 `.md` 檔案和 dir 兩種格式 |
| P1-S2：`GET /api/skills/:id` | ✅ **已完成** | `handle_skill_get` | 支援 file/dir 兩種 skill 格式 |
| P1-S3：`PUT /api/skills/:id` | ✅ **已完成** | `handle_skill_put` | 更新 description/mcp/memory/output_memory |

### P2 — Teams 定義（後端）

| 項目 | 狀態 | Handler | 備註 |
|------|------|---------|------|
| P2-B1：`GET /api/teams` | ✅ **已完成** | `handle_teams` | 讀取 `~/.claude/teams/*.yaml` |
| P2-B1：`POST /api/teams` | ✅ **已完成** | `handle_team_post` | 建立新 team YAML |
| P2-B1：`GET /api/teams/:id` | ✅ **已完成** | `handle_team_get` | 回傳 team 完整資訊 |
| P2-B1：`PUT /api/teams/:id` | ✅ **已完成** | `handle_team_put` | 更新 team YAML |
| P2-B1：`DELETE /api/teams/:id` | ✅ **已完成** | `handle_team_delete` | 刪除 team YAML |
| P2-B2：member input_memory/output_memory 解析與寫入 | ✅ **已完成**（本次修補）| `_team_dict()` + `_execute_team_run()` | per-member memory routing 完整實作 |

### Phase 3（序列執行 + SSE 串流）

| 項目 | 狀態 | 備註 |
|------|------|------|
| `POST /api/team/run` | ✅ **已完成** | `handle_team_run_post` |
| `GET /api/team/run/:id` | ✅ **已完成** | `handle_team_run_get` |
| `GET /api/team/run/:id/stream` | ✅ **已完成** | SSE 串流，含 ping keepalive |
| `DELETE /api/team/run/:id` | ✅ **已完成** | Cancel + evict |
| `POST /api/hr/dispatch` | ✅ **已完成** | HR Agent JSON plan |

---

## 三、本次修補內容（2026-07-02 15:39）

### Fix 1：`_agent_dict` — soul 欄位讀 frontmatter

```diff
- "soul": aid,   # 以前硬編碼為 agent id，前端啟動 Agent 時切換 Soul 永遠找不到正確的 soul
+ soul_val = fm.get("soul", "") or aid
+ "soul": soul_val,
```

### Fix 2：`_team_dict` — members 解析補上 input_memory/output_memory

```diff
- members.append({"agent": m.get("agent"), "role": m.get("role")})
+ members.append({
+     "agent":         m.get("agent", ""),
+     "role":          m.get("role", ""),
+     "input_memory":  m.get("input_memory", []),  # P2-B2
+     "output_memory": m.get("output_memory", []),  # P2-B2
+ })
```

### Fix 3：`_execute_team_run` — memory 中繼使用 per-member 宣告

```diff
- read_keys = agent_info.get("memory", [])   # 只讀 agent 自身的 memory
+ step_input_keys  = step.get("input_memory",  []) or agent_info.get("memory", [])
+ step_output_keys = step.get("output_memory", []) or agent_info.get("output_memory", [])
```
> 優先使用 Team YAML 裡 per-member 宣告，fallback 到 Agent frontmatter 的 memory 設定。

### Fix 4：`_write_team_yaml` fallback — 序列化 nested list

修正了 PyYAML 不可用時，member 的 `input_memory`/`output_memory` list 欄位會被直接 `str()` 成 Python list 格式的問題。

### Fix 5：`handle_team_run_post` — steps 帶入 input/output_memory

Team run 的 steps 現在會複製 member 的 `input_memory`/`output_memory`，讓 `_execute_team_run` 能正確路由。

---

## 四、Part A 現況（2026-07-03 更新）

> [!NOTE]
> Part A（`ClaudeSDKClient` 持久連線池取代每輪開新 subprocess）三個呼叫點已全部完成並驗證：

- `handle_chat`（單一對話）：`_run_pooled`，已用真實 `/api/chat` 呼叫驗證：pool 重用（連續兩輪 `pool_size` 維持 1、`input_tokens` 未隨對話累積）、`handle_chat_stop` 觸發 pool evict（`pool_size` 1→0）皆通過。
- `handle_team_chat`（team 對話）：`_exec_pooled`，Part A 最初落地的路徑，已驗證。
- `handle_team_execute`（team 任務執行）：`_pooled_exec` + `can_use_tool` callback 取代舊的 stdin y/n 權限流程，已用 throwaway team 驗證 SSE 事件序列正常、無 Python exception；`can_use_tool` 的核准/拒絕分支邏輯已讀過但尚未在真實工具呼叫情境下逐一走過（見下方技術債 #3）。

`main.py` 已模組化拆分（`routes/agents.py`、`routes/teams.py`），Part A 的三個遷移點目前仍留在 `main.py` 主檔。

---

## 五、接續開發計畫

### 🟡 P1 — Agent Mapping 前端（2026-07-03 複查：絕大部分已完成，本文件先前紀錄過時）

- [x] **P1-F1**：Agent 卡片顯示連結摘要（Soul｜Skills N｜MCP N｜Mem N）— `agent-mapping-summary` chips
- [x] **P1-F2**：「啟動 Agent」按鈕：切換 Soul + 啟動 MCPs（`activateAgent()`）
- [x] **P1-F3**：Agent 詳細面板（展開查看，含 Soul persona 預覽、Skills/MCP 連結列表）
- [x] **P1-F4**：Skills 頁籤：已連結的 skill 顯示 `● agent` 標記（`isSkillInActiveAgentFrontmatter`）
- [x] **P1-F6**：MCP 頁籤：agent 需要的 MCP 顯示「此 Agent 需要」提示（`mcp-agent-hint`）
- [x] **P1-M3~M9**：Agent 編輯器 Modal（完整 CRUD UI，含 Memory 讀取多選清單）
- [x] **P1-S4~S9**：Skill 編輯器 Modal（MCP/Memory 多選 UI）
- [x] ~~**P1-F5**：Memory 頁籤：agent 關聯的 key 自動勾入上下文~~ — 原本有做（獨立 Memory 頁籤 + `isMemoryRequiredByActiveAgent`），但被另一次 UI 改版關掉（`@if (false)`）且從未接回，2026-07-03 確認為死路後直接移除，現在的設計是 Agent/Skill 卡片摘要 chip + Settings 裡的簡化版 user/system 記憶編輯器

### 🟢 P2 — Teams 前端 UI（2026-07-03 複查：已完成）

- [x] **P2-F1**：右側面板新增 Teams 頁籤（`TEAM` 頁籤）
- [x] **P2-F2**：Teams 列表（卡片：名稱、成員 chips、leader 標示）
- [x] **P2-F3**：Team 建立 / 編輯 UI（成員排序、execution_mode 下拉）
- [x] **P2-F4**：「發任務給 Team」入口（💬 團隊對話 / 🤖 自動組隊 HR Agent）
- [x] **P2-F5**：任務執行進度面板（SSE 串流，含逐步驟狀態、`permission_request` 核准／拒絕按鈕，已對著今天修好的 backend 實測過）

---

## 六、已知問題與技術債

| # | 類別 | 問題 | 狀態 |
|---|------|------|------|
| 1 | 架構 | `main.py` 模組化 | ✅ **已完成**：已將 Agent/Skill/Team 路由及純函式移至獨立模組 |
| 2 | 限制 | 原生 Agent Teams 無法 headless 驅動（Part B 結論，見下） | ⚠️ 確定無法用現有架構解決 |
| 3 | 測試 | `handle_team_execute` 的 `can_use_tool` 權限核准/拒絕分支 | ✅ **已驗證**：approve 會實際建立檔案、reject 會正確擋下並回報，皆用真實 Write 工具呼叫測試過 |
| 4 | 限制 | dev 容器熱重載（`watcher.py` 偵測 .py 變更即整包重啟 process）會砍掉所有存活的 SDK pool 連線；`active_sessions.json` 落地的 session_id 仍在，下一輪靠 `resume` 接續，但當下正在進行中的請求會直接斷線 | 已知限制，未處理 |
| 5 | 效能 | `SessionPool.get_or_create()` 原本用單一全域 lock 包住 `client.connect()`，導致 `execution_mode: parallel` 的多個團隊成員在建立連線時彼此卡住，「並行」退化成序列 | ✅ **已修正**：改為 per-key lock，已用 2 成員 parallel team execute 驗證 exec_start 不再序列化 |
| 6 | 穩定性 | pooled SDK 路徑的例外處理原本把「客戶端斷線」和「SDK 真的失敗」混為一談，兩者都會觸發整包用 subprocess 重跑一次，造成重複副作用（例如重複寫入檔案）且必定再次寫入失敗的連線 | ✅ **已修正**：`ConnectionError` 不再觸發 legacy fallback |

### Part B（原生 Agent Teams 監控）結論

實測 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` 搭配 `-p`（headless / SDK 呼叫）**無法**觸發原生 Agent Teams：Claude 會退回使用一般的 Task tool（background subagent，`"task_type":"local_agent"`），且從未建立 `~/.claude/teams/session-{id}/` 目錄。原生 Agent Teams 目前僅在互動式 TTY session 才會啟用，與本專案的 headless Docker backend 架構衝突。

Part B 原計畫（讀取 `~/.claude/teams/`、`~/.claude/tasks/` 做前端監控）**無法照原設計實作**。可行的替代方案：
- 放棄原生 Agent Teams 整合，維持現有自建 team 協調機制（`execution_mode: parallel/sequential`）
- 或者：把意外發現的「Task tool background subagent」（可透過 headless/SDK 觸發）包裝成現有自建 team 系統的「監控」功能，而不是追求原生 Agent Teams

---

## 七、換機操作步驟

### 在新電腦上

```bash
git clone https://github.com/Chester930/claude-desktop.git
cd claude-desktop

# 安裝依賴
pip install -r backend/requirements.txt
pip install claude-agent-sdk   # Part A pooled SDK 需要

cd frontend && npm install && npm run build && cd ..
npm install

# 啟動（開發模式）
.\start.bat --dev
```

### 開發環境需求

| 工具 | 版本 |
|------|------|
| Node.js | ≥ 22.22.3 |
| Python | ≥ 3.10 |
| Claude CLI | 最新版 |
| claude-agent-sdk | 最新版（選用） |

---

## 八、後端 API 速查

| 方法 | 路徑 | Handler | 狀態 |
|------|------|---------|------|
| GET | `/api/agents` | `handle_agents` | ✅ |
| POST | `/api/agents` | `handle_agent_post` | ✅ |
| GET | `/api/agents/:id` | `handle_agent_get` | ✅ |
| PUT | `/api/agents/:id` | `handle_agent_put` | ✅ |
| DELETE | `/api/agents/:id` | `handle_agent_delete` | ✅ |
| GET | `/api/agents/registry` | `handle_agents_registry` | ✅ |
| GET | `/api/skills` | `handle_skills` | ✅ |
| GET | `/api/skills/:id` | `handle_skill_get` | ✅ |
| PUT | `/api/skills/:id` | `handle_skill_put` | ✅ |
| GET | `/api/teams` | `handle_teams` | ✅ |
| POST | `/api/teams` | `handle_team_post` | ✅ |
| GET | `/api/teams/:id` | `handle_team_get` | ✅ |
| PUT | `/api/teams/:id` | `handle_team_put` | ✅ |
| DELETE | `/api/teams/:id` | `handle_team_delete` | ✅ |
| POST | `/api/team/run` | `handle_team_run_post` | ✅ |
| GET | `/api/team/run/:id` | `handle_team_run_get` | ✅ |
| GET | `/api/team/run/:id/stream` | `handle_team_run_stream` | ✅ SSE |
| DELETE | `/api/team/run/:id` | `handle_team_run_cancel` | ✅ |
| POST | `/api/hr/dispatch` | `handle_hr_dispatch` | ✅ |

> [!TIP]
> 後端已就緒，接下來的工作幾乎全在前端 Angular。換機後直接開 `frontend/src/app/` 就可以開始。

---

## 九、2026-07-06 全面健檢 — 待修復任務清單

> **背景**：對 `8c986bf..HEAD`（`90d505f` onboarding/usage/command palette + `8015063` MCP debugging/agent 管理/system messaging，共 2103 行變更）做的全面健檢。所有項目皆已直接讀原始碼／跑測試驗證。依優先順序修，勾掉再往下。

### 🔴 P0 — 資安 / 阻斷性

- [x] **T1｜MCP 敏感操作授權閘門形同虛設** — `backend/routes/mcp_debugger.py` 原本只信任呼叫方自報的 `authorized: true`，疊加既有 CORS（`allow_credentials=True` + origin `"*"`）可被任意網頁繞過確認流程觸發破壞性 MCP 呼叫。已改為伺服器核發、單次使用、TTL 120 秒的 `pending_id`，`frontend/app.ts` 同步改讀後端回傳的 `pending_id`（原本寫死成固定字串）。CORS 白名單化見 T2。
- [x] **T2｜`docker-compose.yml` 新增 docker.sock 掛載，配合 T1 構成提權鏈** — 複查後發現這是一條**目前就能觸發、不需任何確認**的完整提權路徑：`POST /api/mcp/{name}/{action}`（start/stop/restart）與 `PUT /api/mcp-local-config/{name}` 完全不在 T1 的 `pending_id` 閘門保護範圍內，且原本對 `containerName`/`composeFile`/`composeService` 沒有任何驗證；只要能打中這兩個端點，就能餵入任意 `composeFile` 路徑並觸發 `docker compose up`，透過掛載的 docker.sock 取得 host root。已與使用者確認採「應用層加固」：①`main.py` 的全域 CORS 設定從單一 `"*"` key（疊加 `allow_credentials=True` 等於對任意來源核發帶憑證許可）改成明確白名單（`http://localhost:4200`、`http://127.0.0.1:4200`、封裝 Electron 的 `null` origin，可用 `CLAUDE_DESKTOP_EXTRA_ORIGINS` 環境變數擴充），非白名單來源的 preflight 直接 403、瀏覽器不會送出實際請求；②新增 `_is_safe_docker_ident()` 驗證 `containerName`/`composeService`（擋路徑分隔符、`..`、開頭 `-`），`composeFile` 需為已存在的檔案，`PUT /api/mcp-local-config/{name}` 與 `POST /api/mcp/{name}/{action}` 兩處都驗證（後者作為防禦深度，涵蓋手動編輯 config 檔殘留舊資料的情況）。已用 `tests/test_mcp_docker_hardening.py`（含實際發 preflight 驗證 CORS 中介層真的擋掉 disallowed origin）驗證。**docker.sock 本身仍是完整、未受限的掛載**（未改用 docker-socket-proxy、也未移除掛載），這部分維持原狀，使用者選擇先做低風險的應用層修補。
- [x] **T3｜Electron dev 模式後端啟動靜默失敗** — `electron/main.js` 用未 import 的 `execSync`，改用已 import 的 `execFileSync`。

### 🟠 P1 — 既有測試 / CI

- [x] **T4｜main.py 重構造成既有測試回歸** — 補回 `_agent_dict`、`_parse_yaml_simple` 的 import。
- [x] **T5｜新功能測試從未真正進 CI** — `backend/test_upgrade.py` 搬進 `tests/`，並把其中驗證「假授權會放行」的過時測試改成驗證新安全流程（含重放防護）。`pytest tests/` 現在 98 個測試全過。

### 🟡 P2 — 中等

- [x] **T6｜`run_artifacts.py` 路徑檢查可被同層目錄繞過** — 改用 `resolved_full.is_relative_to(base_dir)`。
- [x] **T7｜`message_bus.py` async 訂閱者例外被靜默吃掉** — `publish()` 對 async callback 的 `create_task` 補上 done-callback：記錄例外並保留任務參照（存進 `_pending_tasks` set）避免中途被 GC。新增 `tests/test_message_bus.py`。
- [x] **T8｜`memory_agent.py` 同步阻塞呼叫卡住 event loop** — `build_memory_context`/`build_team_memory_context` 在 `main.py`（`handle_chat`/`handle_team_chat`/`handle_mem_preview`）與 `routes/teams.py`（team run 執行）的呼叫全部改成 `await asyncio.to_thread(...)`；對應的 `_build_full_message`/`_build_full_prompt` 改成 `async def`。
- [x] **T9｜`database.py` 自我修復邏輯過猛** — `_init_db()` 不再對任何 `sqlite3.Error` 都刪除重建索引 DB，只有偵測到真正的檔案損毀（訊息含 `not a database`/`malformed`/`corrupt`）才觸發重建；transient 錯誤（如 `database is locked`）改為直接拋出，不動使用者的索引檔。同時修正 `_db()` 在 PRAGMA 設定失敗時洩漏連線 handle 的問題（會在 Windows 上擋住後續的 unlink+rebuild）。新增 `tests/test_database.py`。
- [x] **T10｜前端：設定說明彈窗變孤兒功能** — 觸發按鈕已移除、`showSettingsHelp` signal 永遠不會被設為 `true`，直接移除該 signal、`app.html` 對應的 `@if` 區塊與 ESC 鍵處理分支。
- [x] **T11｜前端：訊息截斷失敗時畫面與後端歷史會分岔** — `confirmEditMsg()` 呼叫 `truncateSession` 補上 `next`/`error` callback：只在後端截斷成功後才裁切畫面訊息並重新送出，失敗則顯示 toast 並保留原狀讓使用者重試。
- [x] **T12｜前端：語音輸入結果覆蓋手動編輯內容** — `toggleMic()` 的 `onresult` 改為偵測目前輸入框內容是否仍等於「上次語音辨識寫入的結果」，若使用者已手動修改則以目前內容為新基準點繼續追加，不再無條件用啟動當下的 `startText` 覆寫。另外 `ngOnDestroy` 補上 `recognition.stop()`（並先卸載 handler），避免元件銷毀後 `SpeechRecognition` 仍在背景執行。

### 🟢 P3 — 低

- [x] **T13｜前端兩套不一致的複製到剪貼簿實作** — 統一抽成 `copyToClipboard()`（Clipboard API 優先、失敗時用暫時 textarea + `execCommand('copy')` 後援），`ctxCopyId`/`copyMessageWithFeedback`/程式碼區塊複製都改用它；順手刪除從未被呼叫的重複方法 `copyMessage()`、`copyText()`。
- [x] **T14｜MCP RPC pending authorization 送出新請求時被靜默清空** — `sendMcpRpcDebug()` 原本每次都無條件清空 `mcpPendingAuth`；改為偵測到尚有未處理的 pending 授權時直接擋下新請求並提示使用者，不再悄悄丟棄。
- [x] **T15｜（既有問題，非本次新增）Electron `shell.openExternal` IPC 沒有 protocol allowlist** — 新增 `isAllowedExternalUrl()`（只允許 `https:`/`http:`/`mailto:`），套用在 IPC handler `shell:openExternal` 與兩處 `setWindowOpenHandler`。

### 已驗證沒問題

前端 `ng build`/`tsc --noEmit` 全過；Electron `contextIsolation: true`、`nodeIntegration: false` 設定正確；所有變更的 backend 檔案 `py_compile` 全過；前端沒有 RxJS 訂閱洩漏或 XSS 風險。

### 剩餘待處理（第一輪）

- **T2 後續（可選）｜docker.sock 完整掛載本身** — 應用層加固已關閉「任意網頁觸發提權」這條已知路徑，但 `docker-compose.yml` 的 `backend-dev`/`backend` 服務仍掛載完整、未受限的 `/var/run/docker.sock`。若之後想進一步縮小攻擊面，可評估：(a) 移除掛載並砍掉 docker-compose 部署下的「本地 Docker MCP 管理」功能（Electron 桌面版本身跑在 host 上不受影響）；(b) 改用 docker-socket-proxy 之類的限權中介（需驗證 `docker compose` 子指令相容性）。屬於範疇/基礎設施決策，非本輪必要項目。

---

## 十、2026-07-06 第二輪健檢 — 全部修復完成

> **背景**：延續第一輪健檢，針對尚未覆蓋的範圍再做一輪全面掃描，涵蓋
> backend/main.py 核心 handler、routes/agents.py、routes/teams.py、
> session_pool.py、helpers.py、memory_agent.py、message_bus.py、
> agency_agents_importer.py、database.py、frontend app.ts 全文、
> claude.service.ts、markdown.pipe.ts、settings.service.ts、
> electron/main.js、preload.js、docker-compose.yml、Dockerfile 系列、
> nginx.conf。5 個並行 agent 分別負責 backend 核心、backend routes/libs、
> frontend 安全性、frontend 正確性、Electron/infra，共產出 35 筆原始
> 發現（1 筆確認為已修復的舊發現，予以排除）。所有項目皆已修復並驗證。

### 🔴 關鍵發現：後端零認證 + 綁定 0.0.0.0

`backend/main.py` 完全沒有任何身分驗證層，`web.run_app(host="0.0.0.0")`
讓同一個 LAN/VPN 上的任何主機都能直接發送請求（CORS 只擋瀏覽器，擋不住
curl/腳本），docker-compose.yml 的 `prod` profile 又把 ngrok 對外網路通道
跟 backend/frontend 綁在一起，等於預設就把這個無認證 API 曝露到公開網際
網路。這個根源問題放大了好幾筆本來看似 P2 的發現（`apiKeyCmd` RCE、
排程建立、`lineChannelSecret` 外洩等）。與使用者確認後採「最小限度部署
面收斂」（不新增完整 API 認證層）：詳見下方 T16-18。

### Backend — team 執行引擎 / 路徑穿越 / 併發

- [x] **T19｜`wrap_cmd` 從未被 import，team 執行引擎 100% 壞掉** — `routes/teams.py` 呼叫 `wrap_cmd()` 但從未 import，每個 team run step 都直接 NameError（被 broad except 吃掉變成錯誤字串）。補上 import。
- [x] **T20-22｜team run 的 memory key／agent id 路徑穿越** — `POST /api/team/run` 的 inline team payload 完全繞過已儲存的 team YAML，`agent`/`input_memory`/`output_memory` 沒有驗證就被拼進檔案路徑。新增 `_is_safe_id()`，API 邊界擋下不合法請求，並在實際讀寫檔案處加防禦深度檢查。
- [x] **T23｜parallel team run 的 process 追蹤 race** — `_team_run_processes` 從 `dict[run_id]=proc` 改成 `dict[run_id]=set(processes)`，cancel/timeout 時正確殺掉該 run 底下追蹤到的所有 process，不再留下孤兒 process。
- [x] **T24｜SessionPool.evict() race** — `evict()` 改成先拿 per-key lock 才動作、鎖物件本身永遠不刪除；新增 busy counter，避免長 turn 進行中被 `run_idle_pruner` 斷線；`evict()` 新增 `force` 參數供已知壞連線/app 關閉時無條件使用。
- [x] **T25-26｜`handle_sessions`/`handle_stats`/`handle_config_put` 等阻塞 event loop** — `_sync_index()`/`_init_presets()` 改用 `await asyncio.to_thread(...)`。

### Backend — 網路曝露收斂 / 資訊外洩 / 穩健性

- [x] **T16-18｜後端零認證 + 綁定 0.0.0.0** — `BACKEND_BIND_HOST` 環境變數（預設 `127.0.0.1`），docker-compose.yml 三個服務的 host 埠全部改綁 `127.0.0.1:`，ngrok 拆成獨立 `tunnel` profile 不再隨 `prod` 自動啟動。
- [x] **T27｜`handle_files` 任意目錄列舉** — 複查後確認是刻意設計（支援多磁碟機瀏覽），且已被 T16-18 的網路收斂降低到同機風險，維持現狀不變更程式碼。
- [x] **T28｜`lineChannelSecret` 經 debug-dump/config 外洩** — `handle_debug_dump` 過濾器加上 `secret` 子字串；`handle_config_get` 針對性移除 `lineChannelSecret`（保留 `apiKeyCmd` 供設定表單讀回填入）。
- [x] **T29｜`_db()` 連線在約 10 個呼叫點從未關閉** — 新增 `_db_ctx()` context manager 取代所有 `with _db() as c:`，語意相同但保證 close()。
- [x] **T30｜`handle_restore` 沒有解壓大小上限（zip bomb）** — 用 `ZipInfo.file_size` 檢查單一項目（20MB）與總計（50MB）上限。
- [x] **T31｜LINE webhook 簽章驗證 fail-open** — `lineChannelSecret` 未設定時原本直接放行，改成 fail-closed。
- [x] **T32-33｜MemoryAgent TOCTOU、agency_agents_importer 路徑穿越防禦深度** — `_safe_mtime()` 容錯；`_is_safe_id()` 過濾 divisions.json 的 key。

### Electron / Infra

- [x] **T34｜容器用 root 執行 + docker.sock/憑證掛載** — `backend/Dockerfile` 新增非 root 使用者，透過 `DOCKER_GID` build arg 對齊 host docker 群組；順手發現並修復這個 image 從未真正開機成功過的兩個問題（COPY 漏掉好幾個必要模組、`CLAUDE_HOME` 目錄建立順序錯誤）。已用 `docker build`+`docker run` 端對端驗證。
- [x] **T35｜Electron `did-fail-load` 無條件 fallback 到 localhost:4200** — 只在 `isDev` 才 fallback，正式版失敗顯示錯誤畫面。
- [x] **T36｜nginx 沒有安全性 header** — 新增 `X-Content-Type-Options`/`X-Frame-Options`/`Referrer-Policy`/`Permissions-Policy`/CSP，已用真實 nginx container 驗證 header 正確出現且資源仍正常載入。
- [x] **T37｜容器沒有資源限制 + 不可重現建置** — `deploy.resources.limits`（已用 `docker inspect` 驗證真的套用到 HostConfig）；`frontend/Dockerfile.dev` 的 `npm install` 改 `npm ci`。

### Frontend

- [x] **T38｜跨分頁串流污染** — `send()`/`submitTeamMessage()`/`executeTeamCodePhase()` 的事件 callback 原本直接寫共用的 `this.messages`/`this.isStreaming`/`this.stopFn`，沒檢查事件所屬分頁是否還是作用中分頁。新增 `tabMessages()`/`tabStreaming()`/`tabTokenUsage()` helper 與 per-tab 的 `tabStopFns` Map。
- [x] **T39｜editor 儲存家族吞掉錯誤** — `saveAgentEditor`/`saveSkillEditor`/`saveTeamEditor`/`deleteTeam`/`saveDockerConfig`/`saveSoulProfileEdits`/`saveSettings` 全部補上 `error` callback + toast。
- [x] **T40｜MCP log poller 在 Settings 關閉後洩漏** — 新增 `closeSettings()` 統一停止輪詢，取代 5 處直接寫 `settingsOpen.set(false)`。
- [x] **T41-42｜備份下載無錯誤處理、`loadSession` 分頁滿載時分歧** — `downloadBackup()` 補 `r.ok`+`.catch()`；`loadSession()` 改成新對話真正載入完成後才同步 `chatTabs`，不再有畫面與儲存狀態分歧。
- [x] **T43｜`providerApiKey` 明碼存在 localStorage** — 改用 Electron `safeStorage`（DPAPI/Keychain/libsecret）另外加密存放，已用 headless Electron 腳本驗證加解密流程正確、檔案不含明碼。
- [x] **T44｜session snippet 原始 innerHTML 注入、markdown 語言標籤未跳脫** — FTS5/LIKE/無查詢三條 snippet 生成路徑都補上 `html.escape()`；`markdown.pipe.ts` 的 `langLabel` 補上跳脫（原本已被下游 DOMPurify 擋住，屬於縱深防禦補強）。

### 已驗證沒問題（第二輪）

`routes/run_artifacts.py` 的路徑穿越修復重新驗證仍然有效；`message_bus.py`／`watcher.py` 沒有超出第一輪已修復範圍的問題；`electron/preload.js` 暴露的介面精簡安全；`database.py` 的 `_analyze_mcp_entry` 邏輯繁瑣但功能正確。

pytest tests/ 162 個測試全過；`tsc --noEmit`／`ng build` 全過；`docker build`/`docker run`/`docker compose config` 皆已實際驗證通過（非僅語法檢查）。

---

## 十一、2026-07-10 team 協作優化健檢

> **背景**：前兩輪健檢以安全性為主軸。這一輪針對「team 協作」本身的**功能正確性**做專項複查——起因是 T19 顯示 team 執行引擎直到最近才發現 `wrap_cmd` 從未 import、100% 壞掉，代表兩輪安全健檢都沒有真正驗證過 team 協作的行為是否正確。用真人的 `claude` CLI（已登入、有實際額度）做過一次真實 subprocess 行為驗證（非僅讀原始碼猜測），其餘用 pytest 直接呼叫核心函式驗證（不重跑真的 subprocess，避免每個案例都燒 API 額度）。

### 🔴 發現 1（已修復並補上回歸測試）｜HR Agent 自動組隊 100% 忽略 execution_mode，永遠平行跑

`_execute_team_run_core()`（`routes/teams.py`）原本靠 `run["team_id"]` 回頭去 `TEAMS_DIR` 讀對應 yaml 檔案取得 `execution_mode`／`leader`。但 `POST /api/team/run` 的 inline team payload（**HR Agent 自動組隊 `submitHRTeamRun()` 是唯一使用者**，`_run_hr_agent()` 產出的 plan JSON 沒有 `"id"` 欄位）永遠沒有對應的 `team_id`，導致 `if team_id:` 判斷直接跳過，`team_info` 維持空字典，`mode` 靜默 fallback 成 `"parallel"`。

矛盾點：`_run_hr_agent()` 的 prompt（`routes/agents.py`）明確要求「挑選 Agent 組成一個**循序執行**的團隊」、「前一個 Agent 的輸出將作為下一個 Agent 的輸入」——但實際執行時，**HR 自動組隊產生的每一個 team run，不分內容，一律用 `asyncio.gather` 平行跑**：
1. 完全沒有「前一位輸出傳給下一位」這件事（parallel 分支沒有 `prev_output` 串接邏輯，只有 sequential 分支才有）。
2. HR plan 裡設計好的 `input_memory`/`output_memory` 跨步驟串接會有 race condition——下游 member 有可能在上游 member 把 `output_memory` 寫進 `.md` 檔之前就已經開始讀了（`asyncio.gather` 讓所有 step 同時起跑）。

換句話說：ROADMAP Phase 4 的旗艦功能「HR Agent 自動組隊」，從 P4 完成以來實際上從未依照設計跑過。

**修法**：`execution_mode`／`leader` 改成在 `handle_team_run_post` 當下（此時 `team` dict 已經正確解析好，不管是 inline payload 還是存檔 team，都能拿到請求裡實際的值）就直接存進 `run` state；`_execute_team_run_core` 改讀 `run["execution_mode"]`／`run["leader"]`，不再靠 `team_id` 回頭查檔（也移除了執行時多一次不必要的磁碟 I/O）。已存檔 team（有 `id`）的既有行為不變，因為 `_team_dict()` 回傳的 dict 本來就含 `execution_mode`，`handle_team_run_post` 一樣拿得到。

新增 `tests/test_team_run_execution_mode.py`（3 個測試）：直接呼叫 `_execute_team_run_core()` 用 monkeypatch 過的 `_agent_run_capture` 驗證 inline payload 的 `execution_mode: "sequential"` 真的會讓 step 2 的 prompt 收到 step 1 的輸出；順手更新 `tests/test_upgrade.py::TestAdversarialDebate`（原本手動建構 run state 時沒帶 `execution_mode`/`leader`，靠舊的 team_id 回頭查檔行為才能通過，改成比照 `handle_team_run_post` 實際會產生的資料直接帶上）。

**追加修復（同一發現的另一半，複查上面的修法時發現還不夠）**：上面的修法只解決了「execution_mode 有值時會被正確套用」，但 `_run_hr_agent()`（`routes/agents.py`）的 prompt/JSON schema 從頭到尾**沒有要求模型輸出 `execution_mode` 欄位**——代表就算修好了套用機制，HR Agent 實際產生的 plan 還是永遠沒有這個欄位，套用端一樣只能 fallback 成預設的 `"parallel"`，等於白修。補上：① prompt 明確要求固定填 `"sequential"`、JSON Schema 範例加上這個欄位；② 解析完 JSON 後再補一層防呆（`_with_sequential_default`），模型偶爾漏填時後端直接補上 `"sequential"`，不 100% 依賴模型照 schema 輸出。新增 `tests/test_hr_dispatch_execution_mode.py`（3 個測試，含模擬模型漏填欄位、模型有填、輸出包 markdown fence 三種情境）。`pytest tests/` 170 個測試全過。

### 🟢 發現 2（已修復並補上回歸測試，採用選項 3：開放 acceptEdits）｜`/api/team/run` 完全沒有工具權限核准機制

用真實 `claude -p` CLI 直接重現 `_agent_run_capture()`（`routes/teams.py`）修復前的 subprocess 呼叫方式驗證過（非猜測）：`cmd = [claude_bin, "-p", full_prompt, "--output-format", "stream-json", "--verbose"]`，**沒有 `--permission-mode` flag，`stdin=asyncio.subprocess.DEVNULL`（無法回應任何核准提示）**。實測結果：CLI 不會卡住等待，而是**直接把需要核准的工具呼叫自動判定為拒絕**（`permission_denials` 裡記一筆，回傳文字說明「請求被系統阻擋」），process 正常以 `exit 0` 結束——「發任務給 Team」進度面板這個最主要的 team 協作入口，實質上做不了任何需要核准的真實操作（寫檔、跑指令等）。

**選項 2（補齊權限轉發，仿照 `_legacy_exec` 的 stdin y/n 模式）動手前先做了一次實測驗證，結果推翻了原本的假設**：把 `_agent_run_capture` 的 `stdin=DEVNULL` 改成 `stdin=PIPE` 後用真實 CLI 重測，headless `-p` 模式底下即使 stdin 是可寫入的 pipe，**依然不會產生任何可偵測、可回應的互動式權限提示**——CLI 只會印一行「no stdin data received in 3s, proceeding without it」然後照樣自主判斷（對敏感路徑自動拒絕）。這代表 `handle_team_execute` 的 `_legacy_exec` 那套「偵測 raw text prompt 寫 y/n 到 stdin」的假設，對目前這個 CLI 版本（2.1.206）的 headless `-p` 模式並不成立；真正能做到互動核准的只有 pooled SDK 的 `can_use_tool` callback（`_pooled_exec`），代表選項 2 實際上需要把整個 Team Run 執行引擎（parallel/sequential/consensus）從「逐步驟重開 raw subprocess」遷移成「透過 `SessionPool`/`ClaudeSDKClient` 執行」，工程量遠比原估的「中等」大。

使用者確認方向後採**選項 3**：直接開放 `--permission-mode acceptEdits`。動手前先用真實 CLI 驗證 `acceptEdits` 底下的實際行為（同一個任務、同一台機器，分別測未設定 permission-mode 與設定 `acceptEdits` 兩種情況）：在非敏感路徑（一般專案目錄）下，`acceptEdits` 讓 `Write` 與 `Bash` 兩種工具呼叫都直接成功、`permission_denials` 為空；Claude Code 自身對 `.claude/` 等敏感路徑的硬性保護則完全不受這個 flag 影響、依然生效（這也解釋了為什麼一開始在 `.claude/jobs/...` 底下測試時 `acceptEdits` 看起來「沒用」——那是另一層跟 permission-mode 無關的路徑保護）。

**修法**：`_agent_run_capture()` 新增 `permission_mode` 參數，預設 `"acceptEdits"`，組 CLI 指令時帶上 `--permission-mode <value>`；`handle_team_run_post()` 從請求 body 讀取可選的 `permission_mode`（預設 `acceptEdits`），比照既有 `_is_safe_id` 一類輸入驗證慣例，用白名單（`acceptEdits`/`auto`/`bypassPermissions`/`manual`/`dontAsk`/`plan`，來自 `claude --help` 列出的合法值）擋掉不合法的值，存進 `run["permission_mode"]`；`_execute_team_run_core()` 讀取 `run.get("permission_mode", "acceptEdits")` 並傳給所有 `_agent_run_capture` 呼叫點（consensus 4 處、parallel/sequential 各 1 處）。新增 `tests/test_team_run_permission_mode.py`（4 個測試）：驗證 `_agent_run_capture` 預設會帶 `--permission-mode acceptEdits`、可被明確覆寫、`handle_team_run_post` 正確存進 run state、不合法值會被 400 擋下。

**尚未處理（跟這個修法無直接關係，先記錄）**：`/api/team/execute` 那條路徑已有的 `permission_request`/核准 UI 沒有變動；如果之後想讓使用者能針對 Team Run 個別任務選擇更嚴格的模式（例如敏感專案想維持逐一核准），`permission_mode` 已經是可從請求 body 傳入的參數，前端只要在 Team Run 面板加一個下拉選單即可，這次沒有一併加。

### 🔴 發現 4（已修復並補上回歸測試）｜`_execute_team_run()` 用 `except Exception: pass` 整個吞掉核心邏輯的例外，run 永久卡在「執行中」+ 記憶體洩漏

`_execute_team_run()`（`_execute_team_run_core()` 的外層 timeout wrapper）原本：

```python
try:
    await asyncio.wait_for(_execute_team_run_core(...), timeout=timeout_val)
except asyncio.TimeoutError:
    ...（有完整處理：設 status="cancelled"、_finished_at、emit "done" 事件、kill 殘留 process）
except Exception:
    pass   # ← 這裡
```

只有「真的跑超過 300 秒」才會被 `TimeoutError` 分支正確收尾；`_execute_team_run_core()` 內部任何其他未預期例外（例如 `mem_dir.mkdir()` 因權限/磁碟問題失敗——這條路徑不在既有的 try/except 保護範圍內、或未來新增程式碼時不小心漏包 try/except）都會被 `except Exception: pass` 整個吃掉，後果比 timeout 更糟：

1. `run["status"]` 永遠停在 `"running"`（`_execute_team_run_core` 裡負責設定 `"done"`/`_finished_at` 的那段程式碼根本沒執行到）。
2. `handle_team_run_stream()` 的 SSE 迴圈只認 `done`/`error`/`cancelled` 三種事件型別為終止訊號，這三種事件永遠不會被送出——串流不會主動關閉，只會每 30 秒送一個 `ping`，前端進度面板會**無限期**卡在「執行中」，完全沒有任何錯誤提示（比 timeout 情境的「至少 5 分鐘後看到熔斷訊息」還糟）。
3. 因為 `_finished_at` 永遠不會被設定，`_cleanup_old_runs()` 的 2 小時回收機制抓不到這個 run——`_team_runs`/`_team_events`/`_team_queues` 會一路留在記憶體裡直到 process 重啟。

**修法**：比照既有 `TimeoutError` 分支的收尾邏輯，`except Exception as e:` 補上 `_log()` 記錄例外內容、設定 `status="error"`、`_finished_at`、`summary` 帶錯誤文字、送出 `"done"` SSE 事件（讓 stream 正常關閉、前端看得到失敗訊息、GC 機制能正常回收）。新增 `tests/test_team_run_error_handling.py`（2 個測試）：monkeypatch `_execute_team_run_core` 主動拋例外，驗證 run 會正確變成 `"error"` 而非卡死、且事後可被 GC 回收。`pytest tests/` 167 個測試全過。

### 🟢 發現 5（已修復並補上回歸測試）｜consensus 執行模式在成員數 >2 時會錯亂

`_execute_team_run_core()` 的 consensus 分支原本寫死只處理前 2 位成員（`agent_a = steps[0]`, `agent_b = steps[1]`），第 3、4 步驟用 `if len(steps) < 3/4: steps.append(...)` 才新增 —— 但如果 team 本來就有 ≥3 位成員（`handle_team_run_post` 已經照 member 數量建好對應筆數的 `steps`），第 3 位成員原本的 `steps[2]` 會被直接**覆寫**成 `agent_a` 的 revision 結果（`step_start` 事件回報的 agent 名字跟原本存在 `steps[2]["agent"]` 的名字對不上，UI 會顯示錯的成員名稱），第 4 位以後的成員則永遠停在 `"pending"`，即使整個 run 的 `status` 已經變成 `"done"`，看起來會像「卡住了」。雖然目前前端 Team 編輯器只提供 `parallel`／`sequential` 兩個選項、UI 不可觸達，但既然是明確的邏輯 bug，直接修掉。

**修法**：consensus 分支一開始就把 `run["steps"]` 換成 consensus 專用的固定 4 步驟結構（Coder 草稿／Auditor 審查／Coder 修正／Leader 總結），不再挪用其他成員原本的 step slot；移除原本 `if len(steps) < 3/4: steps.append(...)` 的條件式補丁。新增 `tests/test_team_run_consensus_members.py`：模擬 4 位成員的 team 跑 consensus 模式，驗證最終只產生 4 個正確歸屬的 step、且第 3、4 位成員（`ThirdAgent`/`FourthAgent`）從未被實際呼叫。

### 🔴 發現 6（已修復並驗證編譯，本輪最嚴重的發現）｜HR 自動組隊點下「▶ 開始執行」後，畫面上完全沒有任何進度顯示——功能本身看起來像壞的

複查前端 `teamRunOpen`/`teamRunState`/`openTeamRun()`/`submitTeamRun()` 這一整組 `/api/team/run` 的前端狀態時，逐一 grep 全部 `.ts`/`.html` 檔案，發現：

1. **`teamRunOpen`、`teamRunState` 這兩個 signal 從未被任何 template 讀取過**——`app.html` 裡完全沒有 `teamRunOpen()`／`teamRunState()` 的蹤影，這組進度面板狀態純粹是「寫了但沒人看」。
2. **`openTeamRun()`／`submitTeamRun()` 從未被任何按鈕呼叫過**——唯一的呼叫鏈是 `activateTeam() → openTeamRun()`，但 `activateTeam()` 本身也從未被任何按鈕呼叫（Team 卡片上根本沒有綁這個方法）。這整條「直接對存檔 team 發任務」的路徑是 100% 死碼，不只是沒畫面，而是使用者連入口都按不到。
3. **唯一真正能從 UI 觸發進入 `/api/team/run` 的入口，只有「🤖 自動組隊」（HR Agent）流程**：`dispatchHR()` → plan 預覽 modal（`hrPlanOpen`）→ 使用者按「▶ 開始執行」→ `submitHRTeamRun()`。但 `submitHRTeamRun()` 一樣只是把資料寫進沒人讀的 `teamRunState`／`teamRunOpen`。

實際後果：使用者填任務描述、按「🤖 自動組隊」、審核 HR 產生的計畫、按「▶ 開始執行」——modal 直接關閉，畫面上**什麼都沒發生**。但後端其實真的建立了 team run、真的花錢呼叫 `claude` CLI 執行每個成員的任務（尤其現在發現 1 的修復讓它真的照順序跑、真的有輸出串接了）。使用者完全看不到任何進度、任何輸出、任何結果，也看不到任何錯誤——這是本輪測試中對「team 協作」實際可用性影響最大的一個問題：ROADMAP Phase 4 的旗艦功能，從使用者的角度看起來就是壞的、按了沒反應。

對照組：同一個檔案裡 `executeTeamCodePhase()`（`/api/team/execute`，「核准並執行」流程）走的是完全不同的機制——把 team run 掛在一則 chat message 上（`ChatMessage.teamRun`），用既有、已經在畫面上正確 render 的 `embedded-tr-steps` 區塊顯示每個成員的即時進度、輸出、權限核准卡片、完成後的成果畫廊。這條路徑是真的能用、也是 T38 修過跨分頁污染的那條。

**修法**：把 `/api/team/run` 的前端狀態管理整個換成跟 `executeTeamCodePhase()` 一樣的「掛在 chat message 上」模式：
- 新增 `_dispatchTeamRun()`（建立 `ChatMessage` 並塞進 `tabMessages(tabId, ...)`、呼叫 `runTeam()` 取得 `run_id`、用 `streamTeamRun()` 接 SSE）與 `_applyTeamRunEvent(tabId, ev)`（把 SSE 事件套用到該分頁最後一則訊息的 `teamRun` 欄位），兩者都用 `tabMessages`/`tabStreaming`/`tabStopFns` 的 per-tab 模式（避免切分頁時進度事件寫錯分頁，跟 T38 是同一類問題）。
- `submitHRTeamRun()` 改呼叫 `_dispatchTeamRun()`。
- 移除確認完全無人呼叫、無人讀取的死碼：`teamRunOpen`／`teamRunTarget`／`teamRunTask`／`teamRunState`／`teamRunLoading`／`_teamRunStopFn`／`openTeamRun()`／`submitTeamRun()`／`_handleTeamRunEvent()`／`cancelTeamRun()`／`closeTeamRun()`／`activateTeam()`。取消功能不用另外做——`ngOnDestroy()`/一般聊天的「停止」按鈕本來就會呼叫 `tabStopFns` 裡的函式，team run 註冊進同一個 map 後自動就有了。

`tsc --noEmit` 與 `ng build` 皆通過（乾淨編譯，無新增錯誤）。

**追加：已用真實瀏覽器自動化驗證過，不再只是「編譯通過」的推測**。一開始判斷這個修復無法在背景執行環境驗證，因為 `claude-in-chrome`（依賴使用者本機已連線的 Chrome 分頁）在這個 session 裡沒有連線。但 `frontend/e2e/`、`playwright.config.ts` 早就有一套完整的 Playwright 端對端測試基礎設施（`npm run e2e`），Playwright 自己的 headless Chromium 不需要連線到使用者的瀏覽器分頁，可以在背景環境獨立跑。

驗證方式：另外起一個隔離的 `ng serve --port 4201`（不動使用者機器上原本就在跑的 4200/8765 那個真實 app，那兩個 port 一直有東西在監聽，判斷是使用者自己開著的 Claude 桌面版），用 `page.route()` mock 掉 `/api/hr/dispatch`／`/api/team/run`／`/api/team/run/:id/stream` 三個端點（回傳固定的假資料與假 SSE 事件序列，不呼叫真實 `claude` CLI——真實環境有 289 個 agent，一次 HR dispatch prompt 會很大、真的跑很慢很貴，且後端邏輯已經在 pytest 用真實 CLI 驗證過，這裡只關心前端渲染邏輯），跑完整的使用者操作序列：輸入任務 → 點「🤖 自動組隊」→ 確認 plan 預覽 modal 正確顯示 mock 資料 → 點「▶ 開始執行」→ **斷言 chat 訊息裡真的出現 `embedded-tr-steps` 進度區塊，含 mock 成員名稱、SSE 逐步累積的輸出文字、完成後的「執行完成」摘要**。

新增 `frontend/e2e/team-run-progress.spec.ts`，測試通過（Chromium，headless）——截圖與 accessibility snapshot 都確認畫面上正確顯示「✓ @mock-agent-1 · (Coder) done / Hello from mocked agent!」、「📁 檢視執行成果 (Artifacts)」、「✓ mock-auto-team 執行完成」。這個修復現在有真實瀏覽器層級的自動化回歸測試覆蓋，不再需要合併前的人工驗證步驟。

另外附帶記錄：`openTeamRun()`/`activateTeam()` 被刪除後，「直接對某個存檔 team 發任務」這個 ROADMAP 提到的入口目前完全沒有 UI 按鈕可以觸發（刪除前也是如此，只是刪除前連死碼都還在）。如果之後想要在 Team 卡片上加一個「▶ 執行任務」按鈕重新開放這條路徑，`_dispatchTeamRun()` 已經是通用的，直接接一個新按鈕呼叫它即可；這屬於要不要加新 UI 入口的產品決定，這次沒有一併加。

### 🟢 發現 7（已修復並補上回歸測試，採用選項 1）｜LLM 自己輸出的文字可以不經使用者同意，直接核准任意 pending 權限請求

複查第三條 team 協作路徑「💬 團隊對話」（`selectTeamLeader()` → `/api/team/chat` → `handle_team_chat()`）時發現：組長 agent 的回覆文字如果符合 `\[APPROVE:\s*([a-zA-Z0-9_-]+)\]` 這個正規表示式（`main.py:946`，修復前），系統會直接把對應的 `pending_permissions[req_id]["decision"] = "approve"`，**完全不經過使用者點擊確認**——跟 `handle_team_authorize()`（`main.py:1389`，使用者在前端點「✓ 允許」按鈕時走的正規核准端點）做的是一模一樣的事，差別只在於這條路徑的觸發者是 **LLM 自己生成的文字**，不是使用者的滑鼠點擊。

`pending_permissions` 是模組層級的全域 dict，key 只是一個 8 字元的 hex request_id，**沒有依 `client_id`／`team_id`／session 做任何 ownership 隔離**。這代表：如果使用者同時開著兩個分頁跑不同的 team（例如分頁 A 的某個 member 透過 `/api/team/execute` 正在等待一個危險操作的核准，分頁 B 的團隊組長在討論一個會讀取外部內容的任務——網頁、檔案、使用者貼上的文字），只要分頁 B 組長輸出的文字裡剛好出現（不管是不小心、被使用者刻意誘導、還是被組長讀到的外部內容 prompt injection 出來）`[APPROVE: <分頁A那個 req_id>]` 這個字串，分頁 A 的危險操作就會被靜默核准——使用者從頭到尾沒有點過任何確認按鈕。這跟 T1（MCP 敏感操作授權閘門形同虛設）在精神上是同一類問題。

複查所有 persona prompt 樣板（leader/member 兩種角色的系統提示）後確認：**模型從來沒有被告知 `[APPROVE: xxx]` 這個語法**（只有 `[CREATE_PROJECT: ...]` 有在 prompt 裡教過模型）——代表這是一段模型正常情況下絕不會主動輸出的死語法，唯一會觸發的情境就是被 prompt injection 誘導，沒有任何正常使用情境會用到它。

**修法（選項 1：直接移除）**：直接刪掉 `handle_team_chat()` 裡解析 `[APPROVE: xxx]` 並設定 `decision = "approve"` 的整段程式碼。核准一律只能透過使用者親自點擊「✓ 允許」呼叫 `handle_team_authorize()`。新增 `tests/test_team_chat_no_llm_auto_approve.py`：模擬組長輸出剛好包含 `[APPROVE: fake-req-1]` 字串，驗證對應的 pending_permissions 紀錄不會被靜默核准（`decision` 仍是 `None`）。

**修復過程中意外抓到的獨立重大 bug（發現 8）**：見下方。

### 🔴 發現 8（已修復並補上回歸測試，本輪與發現 6 並列最嚴重）｜「💬 團隊對話」第一輪對話 100% 觸發 NameError，整條功能等於是壞的

寫發現 7 的回歸測試時（用 mock 過的 subprocess 呼叫 `/api/team/chat`），斷言失敗，回應內容是：

```
data: {"type": "error", "text": "name 'all_members_list' is not defined"}
```

複查 `handle_team_chat()` 的 `_build_full_prompt()`（`main.py:716`）：呼叫 `build_team_memory_context(team_id, all_members_list, agent_id, cwd, ...)`，但 `all_members_list` **在整個 `handle_team_chat()` 裡從未被定義過**——唯一存在的是 `member_agent_ids`（`main.py:693`，`[m["agent"] for m in members]`），顯然是一次變數改名重構時漏改了一處。`_build_full_prompt()` 會在**每一次「第一輪對話」或「還沒有 persisted session」時**被呼叫——也就是說，「💬 團隊對話」這個功能幾乎每一次真實使用都會先撞上 `NameError`，跟 T19（`wrap_cmd` 從未 import，team 執行引擎 100% 壞掉）是同一類「看起來已經上線、實際上一叫就炸」的問題，而且這條路徑是三條 team 協作路徑裡唯一有真正 UI 按鈕（`selectTeamLeader()`）可以觸發的。

**先前的整合測試為什麼沒抓到**：`tests/test_backend.py::test_team_chat_endpoint` 對 `/api/team/chat` 發送真實請求，但只斷言 `resp.status == 200` 和 `"data:" in body`。`handle_team_chat()` 把所有例外都用 `except Exception as e: ... {"type": "error", ...}` 包成一個「正常的」SSE `data:` 事件回傳——HTTP 層看起來永遠是 200、body 永遠含有 `"data:"`，就算內部整個 `NameError` 炸掉，這個測試也會判定通過。是本輪寫發現 7 的回歸測試時用了更嚴格的斷言（明確排除 `"type": "error"`），才意外揪出這個已經存在的重大 bug。

**修法**：`all_members_list` 改成 `member_agent_ids`（一行修正）。新增 `tests/test_team_chat_first_turn_nameerror.py`：mock subprocess 模擬第一輪對話，明確斷言回應不含任何錯誤事件、且組長的真實回覆有正常送達。同時補強 `tests/test_backend.py::test_team_chat_endpoint`（既有測試，走真實 `claude` CLI 呼叫）的斷言，排除 `"type": "error"`／`NameError`，關掉「測試綠燈但功能其實是壞的」這個漏洞——已用真實 CLI 呼叫重跑過一次驗證通過（59 秒，含真實 API 呼叫）。

### 已排除的假設（複查後確認不是問題）

`build_team_memory_context()` 讀取 `_team_memory_dir(team_id)` 時對 inline/HR 派發（`team_id=""`）沒有特別擋，一開始懷疑會跟其他 HR 任務共用同一份 `CLAUDE_HOME/memory/teams/shared.md`／`projects/<slug>.md` 造成記憶體互相污染；但複查 `_execute_team_run_core()` 的寫入端（consensus 分支結尾、sequential/parallel 分支結尾）後確認兩處都已經有 `if team_id and cwd:` 擋著，inline/HR 派發的 run **從來不會寫入**這兩個共用檔案，所以讀到的永遠是空字串，不構成實際的污染路徑。

### 前端複查範圍與限制

`tsc --noEmit` / `ng build` 全過。程式碼層面複查了 Team Run／Team Chat／HR Agent 三條前端路徑的事件處理完整性（發現 2 提到的 `permission_request` 缺口，目前維持現狀，Team Run 用 acceptEdits 繞過而非補權限 UI，故此缺口不再是阻塞項）。

**修正**：一開始判斷「無 GUI 背景環境做不了真實瀏覽器測試」，後來發現這個判斷不完全正確——`claude-in-chrome`（依賴使用者本機已連線的 Chrome 分頁）確實在這個 session 沒有連線，但專案裡本來就有一套獨立的 Playwright headless 測試基礎設施（`frontend/e2e/`），不依賴使用者的瀏覽器分頁，可以在背景環境自主起一個隔離的 dev server + headless Chromium 完整跑過一次真實 DOM 渲染驗證（見發現 6 的追加驗證段落）。之後遇到類似「需要驗證前端渲染」的情境，應該先檢查專案裡有沒有現成的 Playwright/E2E 基礎設施，而不是預設只能交給人工。

### 本輪結論

本輪一共發現 8 個問題，**全數修復並完成自動化驗證**：

- **execution_mode 相關（發現 1）**：HR 自動組隊從完成以來實際上從未依設計循序執行過，且 HR prompt 從未輸出這個欄位——兩層都修了。
- **權限模型（發現 2）**：`/api/team/run` 完全無法執行需要核准的操作。原本評估的「補齊權限轉發」修法（比照 `handle_team_execute` 的 `_legacy_exec` stdin y/n 模式）動手前先實測驗證，發現**行不通**——即使 `stdin=PIPE`，headless `-p` 模式也不會產生可偵測、可回應的互動式權限提示，真正能做互動核准的只有 pooled SDK 的 `can_use_tool` callback，代表要做完整權限轉發需要把整個 Team Run 執行引擎遷移到 SessionPool，工程量遠比原估大。使用者確認方向後採**開放 `--permission-mode acceptEdits`**：已用真實 CLI 驗證 acceptEdits 底下 Write/Bash 都能正常執行、`.claude/` 等敏感路徑的硬性保護不受影響——已修復。
- **穩健性（發現 4）**：team run 核心邏輯任何未預期例外都會讓 run 永久卡死並洩漏記憶體——已修復。
- **正確性（發現 5）**：consensus 模式成員數 >2 時 UI 會顯示錯誤成員、部分成員永遠卡在 pending——已修復。
- **前端可用性（發現 6，本輪影響面最大）**：HR 自動組隊「開始執行」後畫面完全沒有進度顯示，功能看起來像壞的——已修復，並用 Playwright headless Chromium 端對端測試實際驗證過畫面渲染正確（見發現 6 段落，不再是只驗證到編譯通過）。
- **安全性（發現 7）**：LLM 自己的文字輸出可以繞過使用者核准，直接核准任意 pending 權限請求——已移除該機制。
- **可用性（發現 8，本輪與發現 6 並列最嚴重）**：「💬 團隊對話」——三條 team 協作路徑裡唯一有真正 UI 入口的一條——第一輪對話 100% 觸發 `NameError`，是修發現 7 的回歸測試時意外抓到的，舊的整合測試斷言太弱從未發現過。

**測試覆蓋**：177 個 pytest 測試全過（含 2 個真實呼叫 `claude` CLI 的整合測試）；`tsc --noEmit`/`ng build` 全過；新增 `frontend/e2e/team-run-progress.spec.ts`（Playwright headless Chromium，mock 網路層、不需真實 CLI，驗證發現 6 的修復在真實 DOM 渲染正確）。Team 協作系統這輪從資料模型、執行引擎、權限模型到前端渲染都有實測或自動化測試覆蓋，不再有「只驗證到編譯通過」的修復。

建議下一步：封裝成多環境軟體／支援 Codex 版本現在可以繼續推進——Team 協作的核心執行引擎、權限模型、前端渲染這輪都已經過實測驗證與修復；若要更完整的前端覆蓋，可以考慮把 `frontend/e2e/` 這套 Playwright 基礎設施接進 CI（目前看起來是手動執行）。

## 十二、2026-07-10／07-11 Pluggable Agent Engine（Claude / Codex 可切換架構）— 用真實 CLI 完整驗證

### 背景

使用者最終目標：軟體要能同時支援 Claude Code CLI 與 Codex CLI，而且是**可切換／可混用**的架構（同一個 team 裡允許有些成員用 Claude、有些用 Codex），不是各自獨立的 fork。詳細設計見 `C:\Users\666\.claude\plans\cozy-dancing-dragon.md`（plan mode 產出，已核准）。這輪的目標是：拿到真實已登入的 Codex CLI 帳號後，把 Codex 這一側從「根據官方文件寫的第一版、標註未驗證」升級成跟 Claude 側同等級的「已用真實 CLI 反覆驗證」。

### 已完成的架構（`backend/engines/` package）

- `base.py`：`RunResult(output, session_id, error)`，duck-typing 慣例（不用 ABC），每個 engine module 對外暴露 `name`／`DEFAULT_PERMISSION_MODE`／`VALID_PERMISSION_MODES`／`run_turn()`。
- `claude_engine.py`：`_agent_run_capture()` 原本邏輯的忠實搬遷，行為不變。
- `codex_engine.py`：組 `codex exec`／`codex exec resume`，解析 `--json` JSONL 事件。
- `registry.py`：`resolve_engine_name(frontmatter_engine, request_engine)`，優先序 frontmatter > request > 預設（`"claude"`）。
- 設定面：agent frontmatter 的 `engine:` 欄位、app 設定裡的「Agent Engine」下拉選單（`frontend/src/app/app.html`、`settings.service.ts`）、`/api/team/run` 的 `agent_engine` 請求欄位。

只動了 Team Run 執行引擎（`_agent_run_capture`）這一條路徑；`_run_hr_agent()`（`routes/agents.py`）跟 `main.py` 的 pooled SDK 路徑刻意留到下一輪（見核准的 plan 文件，範圍排除說明）。

### 用真實 Codex CLI（0.144.1，已登入真實帳號）驗證出的結果

1. **`codex exec` 預設要求在 git repo 裡執行**，不然整個 turn 安靜結束、`output`/`session_id` 都是空字串、不丟例外——非常容易誤判成「成功但沒反應」。修法：無條件加 `--skip-git-repo-check`（Team Run 的 `cwd` 不保證是 git repo）。
2. **`codex exec resume` 是子指令、且不接受 `--sandbox`/`--cd`**（`codex exec resume --help` 證實），塞了會直接整個失敗。修法：resume 分支只帶 `--json`/`--skip-git-repo-check`（+ 可選 `--model`），跟一般呼叫的 flag 集合分開組。
3. **關鍵 bug（本輪最嚴重的發現）：Windows 上多行 prompt 當 CLI 引數傳會被 `cmd.exe` 損壞。** `codex` 在 Windows 是 npm `.cmd` shim，既有的 `wrap_cmd()` 會包一層 `cmd /c` 才能執行；但 `cmd.exe` 對「一個引數裡包含換行字元」的處理是壞的。真實 Team Run 的 prompt 一定是多行（agent frontmatter body、memory context、任務描述用 `\n\n` 接起來），實測會被截斷成只剩 `"[Memory Context]"` 這一行，且 Codex 會整個退回互動式人類可讀輸出、完全不是 `--json` 要求的 JSONL。**這個 bug 會讓所有真實 Team Run + Codex 場景回傳空白輸出**，用簡短單行測試 prompt 完全測不出來，只有用真實情境的多行 prompt 端對端測試才踩得到。修法：改用 Codex 官方文件記載的方式——CLI 引數位置填 `"-"`，實際 prompt 透過 `stdin` 傳（`stdin=PIPE` 取代 `stdin=DEVNULL`）。修完後用真實 HTTP 端對端測試（`POST /api/team/run` → SSE stream）確認輸出正確。
4. **文件沒提到的 item type：`item.type == "error"`**（例如 skills context budget 超過的警告）——不是致命錯誤，turn 後面照樣正常結束，改成跟一般文字一樣用 `[codex: ...]` 包起來透過 `on_text` 送出，不吞掉。
5. **CLI 層級失敗（例如 resume 收到不支援的 flag）不會有 JSON 事件，只印純文字到 stdout 然後非零結束碼**——原本的解析器對這種情況完全沒反應，回傳看似成功的空白 `RunResult`。修法：process 非零結束碼、且完全沒解析到任何 JSON 事件時，視為失敗。
6. **錯誤路徑（invalid model name）用真實帳號實測**：`turn.failed` 事件正確被捕捉，`RunResult.error` 帶著 OpenAI API 回傳的完整 400 錯誤內容（`invalid_request_error`）；失敗前出現的非致命 `item.type=="error"` 警告（model metadata fallback、skills budget）也都正確經 `on_text` 送出，沒有被吞掉；`session_id` 即使最後失敗仍保留。**這條路徑不需要任何程式碼修正**，已有的 `turn.failed` 處理邏輯本身就是對的——用一個鎖定這個真實觀察行為的永久回歸測試補上（`test_codex_engine_real_invalid_model_scenario`）。
7. **Sandbox 等級**：`workspace-write` 已驗證可用。`danger-full-access` 一開始沒測——Claude Code 自己的安全分類器判斷使用者先前的「開放所有權限」授權沒有明確點名這個危險 flag，主動擋下並向使用者說明，沒有嘗試繞過；使用者後續明確授權（「同意測試 danger-full-access」）後才實測。實測結果：在隔離的暫存目錄裡，請 Codex 用 shell 指令寫檔案、再讀回內容，**成功**——這證實了 `danger-full-access` 是目前 Windows 上唯一能讓 Codex 執行 Bash/shell 指令的 sandbox 等級（`workspace-write` 底下 shell 指令會因 `CreateProcessAsUserW failed: 5` 被拒絕，見下方第 8 點），已加上永久回歸測試（`test_codex_engine_passes_through_danger_full_access`）鎖定 `--sandbox` 參數正確原樣傳遞、不會被 `_normalize_sandbox_mode()` 誤判成不合法值退回預設。
8. **已知限制（Codex CLI 本身的 bug，不是這個 app 的問題）**：Windows 上 `workspace-write` sandbox 允許檔案寫入，但 Shell/Bash 指令執行會失敗（`CreateProcessAsUserW failed: 5 (存取被拒)`）。已經在 Settings 的 Agent Engine 說明文字裡註記提醒使用者。
9. **混用引擎（發現 4 對應的原始需求）**：同一個 team、`parallel` 執行模式、一個成員 frontmatter 宣告 `engine: codex`、一個宣告 `engine: claude`，兩邊都用真實帳號各自正確路由到對應 CLI（codex 側回覆帶著只有 `codex_engine.py` 會產生的 `[codex: ...]` 提示字樣，claude 側乾淨沒有）。已把這個場景固定成 mock 測試（`tests/test_team_run_mixed_engine.py`）永久保護，不用每次都燒真實額度驗證。

### 前端 parity 檢查（2026-07-11）

- `tsc --noEmit` / `ng build` 全過，`agentEngine` 設定欄位（`AppSettings` 介面、Settings modal 下拉選單、`runTeam()` 帶入 `agent_engine`）編譯正常。
- 確認 SSE 事件層（`routes/teams.py::_tr_emit()` 送的 `step_start`/`step_text`/`step_done`/`done`）完全跟引擎無關——事件內容只由 `agent_id`／累積的文字／`chunk` 這些一般變數組成，不含任何 Claude 或 Codex 專屬的東西。既有的 `frontend/e2e/team-run-progress.spec.ts`（mock 掉網路層）不需要為了 Codex 額外調整，因為前端從來不知道、也不需要知道是哪個引擎產生的 SSE 事件。
- 用一支獨立的 Playwright 腳本（非委交進 committed 測試檔）在隔離的 `ng serve --port 4201` + 隔離的 `python scripts/probe...` 後端（8766）上，真的點開 Settings modal、找到新的「Agent Engine」區塊，確認下拉選單有 `claude`/`codex` 兩個選項、預設值是 `claude`、選了 `codex` 之後 `<select>` 的值真的會變成 `codex`（能切回 `claude`）——DOM 層面的真實驗證，不只是程式碼審查。
- **意外發現一個跟這次功能無關的既有問題**：`frontend/e2e/app.spec.ts` 有 8 個測試全部用 `.icon-btn[title*="設定"]` 這個 locator 找設定按鈕，但目前 `app.html` 裡已經沒有這個元素了——設定按鈕在某次重構後被移到使用者選單（`.umenu-item`，`(click)="openSettings()"`）裡，`app.spec.ts` 沒有跟著更新，導致這 8 個測試現在全部會 timeout 失敗（跟 Codex/pluggable engine 這次的改動完全無關，我沒有動過設定按鈕的 DOM 結構）。這輪沒有修（不在這次任務範圍內），記錄在這裡供下次處理。

### 測試覆蓋（本輪結束時）

- `pytest tests/` 全套 202 個測試全過，含 `tests/test_engine_registry.py`（`resolve_engine_name` 優先序、`ClaudeEngine`/`CodexEngine` 對真實觀察過的 JSONL 事件格式的解析、真實 invalid-model 錯誤情境的永久回歸測試）跟 `tests/test_team_run_mixed_engine.py`（混用引擎路由）。
- `tsc --noEmit` / `ng build` 全過。
- Playwright 端對端：既有 `team-run-progress.spec.ts` 這條 SSE 渲染測試跟引擎選擇無關、不需調整；Agent Engine 下拉選單本身用獨立腳本在真實瀏覽器（Chromium）驗證過。

### 建議下一步

- `_run_hr_agent()`（HR 自動組隊）跟 `main.py` 的 pooled SDK 路徑（`handle_chat`/`handle_team_chat`/`handle_team_execute`）目前仍然只支援 Claude，套用同一個 `engines/` 抽象是下一輪的候選項目。
- Team/Agent 編輯器 UI 目前只能用 frontmatter 手動打 `engine: codex`，還沒有下拉選單——下一輪可以加。
- `danger-full-access` sandbox 等級已於 2026-07-11 用真實帳號驗證完成（見十二節第 7 點）。`CODEX_API_KEY` env var 認證路徑（這次用的是已登入憑證 `~/.codex/auth.json`，沒有走 env var）仍未實測。

## 十三、2026-07-11 續篇 — `frontend/e2e/app.spec.ts` 修復進度 + 新發現一個真實 pre-existing bug

延續十二節記錄的「`.icon-btn[title*="設定"]` selector 脫節」問題，這次實際動手修復並用真實瀏覽器（Playwright）反覆驗證，過程中意外連帶發現一個 Docker 環境問題跟一個真實、跟這次 Codex 工作完全無關的既有 UI bug。

### Docker 環境曾經整個沒回應（已排除，跟這次改動無關）

跑 e2e 測試時發現使用者本機 `localhost:4200`/`8765`（Docker 跑的 `claude-desktop-frontend-dev`/`claude-desktop-backend-dev` 容器）整個沒回應，連 `docker ps` 都會 hang——判斷是 Docker Desktop 背景服務本身卡住（不是容器掛了，容器程序照樣顯示 running，只是完全不回應）。經使用者同意後重啟 Docker Desktop（`Stop-Process` 全部 docker 相關程序後重新 `Start-Process "Docker Desktop.exe"`），約 5 秒後所有容器依重啟策略自動回復健康狀態，前後端都恢復正常回應。這是環境問題，不是這次任何程式碼改動造成的。

### 已修復並驗證：兩個真的只是選擇器過時的測試

1. **設定按鈕**：`app.html` 已經沒有 `.icon-btn[title*="設定"]` 這個元素了——某次改版把設定入口移進使用者選單（`.umenu-trigger` 按鈕「你 Claude Code 使用者 ⌄」→ 選單裡的 `.umenu-item`「⚙ 設定」）。影響 5 個測試（開關設定 modal、Provider 選單、Telegram 區塊、語言切換選項、Debug 診斷按鈕），全部改成先點使用者選單再點設定項目，已用真實瀏覽器反覆驗證通過。
2. **Skills 分頁按鈕文字**：`.tab-bar button` 的文字從某個版本的 "Skills" 改成全大寫 "SKILL"（沒有字尾 s），導致 `hasText: 'Skills'` 的子字串比對完全比不中。改成 `hasText: /Skill/i` 後驗證通過。

### 確認為既有環境雜訊、非真實 bug：backend 高負載造成的隨機 flaky

`後端 /api/status 健康檢查`、`後端 /api/profiles 回傳清單`、`設定頁包含 Provider 選單` 這幾個測試在跑「全部一起」時偶爾會隨機挑一兩個 timeout，但單獨或小批次重跑時穩定通過，且每次失敗的是不同測試——判斷是 Playwright 多個 worker 同時打真實的、有大量真實使用資料（`active_sessions: 39`）的 live 後端造成的資源競爭，不是程式碼問題，不需要修。

### 更正（2026-07-11 續）：上面那個「HR 自動組隊流程壞了」的結論是誤判，根本原因是環境太舊，不是程式碼 bug

用同一支診斷腳本（帶 console/network log）指向 `localhost:4200`（Docker 容器）重現時，確實觀察到網路層正常、但畫面完全沒有訊息渲染、輸入框也沒清空。原本以為是真實 regression，但深入追查後發現：

1. **Docker 前端容器（`claude-desktop-frontend-dev`）掛載的是主要 checkout（`D:\Users\666\Desktop\claude-desktop\frontend`），不是這個 worktree**（`docker inspect` 的 `Mounts` 證實）。也就是說整段除錯期間，`localhost:4200` 服務的其實是完全不同的一份原始碼。
2. 主要 checkout 的本地 `master` 分支落後 `origin/master` **9 個 commit**，剛好完全缺少 PR #5 的全部 8 項修復——包括 `5f23489`，這個 commit 正是十一節「發現 6」用來修「HR 自動組隊沒有進度顯示」問題的那次修復本身！換句話說，`localhost:4200` 這段期間跑的是**發現 6 修復之前**的舊版程式碼，`submitHRTeamRun()`/`_dispatchTeamRun()` 那時候還是壞的（甚至 `activateTeam()`/`openTeamRun()`/`teamRunOpen` 那些已經在最新版被清掉的死碼都還在）。
3. 額外發現：Docker 容器的檔案監看器（Watchpack）啟動時會噴 `ENOMEM: not enough memory, scandir '/app/src'`，重啟容器後這個錯誤依然會重現——**代表這個容器的 hot-reload 完全失效**，任何原始碼修改都不會自動反映到執行中的頁面，唯一能看到最新效果的方法是手動 `docker restart claude-desktop-frontend-dev` 強迫它整個重新編譯。

改用這個 worktree 自己啟動的隔離 `ng serve --port 4201`（真正跑最新程式碼，不經過 Docker）重跑同一支診斷腳本後，**HR 自動組隊流程完全正常**：輸入框正確清空、`.msg-assistant-group` 正確渲染出 1 則訊息、team run 進度與「執行完成」摘要都正確顯示。證實現在的程式碼（`origin/master` 加上這個分支的乾淨改動）本身完全沒問題，十一節發現 6 的修復也依然有效。

**下一步（環境維護，不是程式碼修復）**：
- 主要 checkout（`D:\Users\666\Desktop\claude-desktop`）需要 `git pull` 到最新 `origin/master`，才會真的拿到 PR #5 的修復，Docker 服務的 UI 才會是正確的版本。
- Docker 前端容器的 Watchpack ENOMEM 問題建議另外調查（可能是容器記憶體上限設太低，或是 `/app/src` 底下有異常大量的檔案/inotify watch 超過限制），目前的暫時解法是每次改完前端原始碼要 `docker restart claude-desktop-frontend-dev` 才看得到效果。

### 待使用者決定，未動手（可能是刻意簡化、也可能是意外遺失的功能）

- **Memory 頁籤**：`app.html` 的 `.tab-bar` 現在只有 TEAM/AGENT/SKILL/MCP/Scheduling，沒有 Memory。但 Settings modal 的說明文字（`app.html` 行 2039 附近）仍寫著「以 key-value 形式存在右側 Memory 頁籤」——文件跟實際 UI 不一致。是功能被拿掉了、還是搬到別的地方了，需要使用者確認。
- **匯出格式下拉選單**：`.export-format-select`（`.md`/`.json`/`.txt` 三選一）在 `app.html` 已經完全找不到，只剩 `app.scss` 裡的死 CSS class。目前 topbar 上只有一個單一的「匯出對話」（⬇）按鈕（`exportChat()`），看起來像是簡化成固定格式匯出，但不確定是否為刻意設計。

## 十四、2026-07-11 續篇二 — 依十二/十三節建議繼續優化與修復

延續十二、十三節列出的待辦，這輪把「建議下一步」清單裡可以做的項目都做完了。

### 已完成

1. **`_run_hr_agent()`（HR 自動組隊派發）遷移到 `engines/` 抽象**：原本寫死呼叫 `claude -p ... --output-format text`，改用 `engines/registry.py`，`POST /api/hr/dispatch` 新增可選 `engine` 欄位，前端 Settings 的 Agent Engine 選擇現在也會套用到 HR 派發本身（不只是派發出來的 team run）。
2. **修復一個真實 bug：Anthropic API key 誤植進 Codex 環境變數**——`resolve_key()`（`main._resolve_api_key()`）只解析 Anthropic key，但 `routes/teams.py::_agent_run_capture()` 跟 `routes/agents.py::_run_hr_agent()` 之前都不分引擎一律把這把 key 傳給 `engine.run_turn()`。如果使用者設定了 Anthropic key、又選 Codex 引擎，會把 Anthropic key 誤植進 `codex_engine.py` 的 `CODEX_API_KEY` 環境變數，蓋掉正常運作的 `codex login` 憑證。這個 bug 存在於這次 pluggable engine 架構自己的程式碼裡（不是外部相依問題），已修好並用 3 個永久回歸測試鎖定。
3. **Agent 編輯器 UI 加上「執行引擎」下拉選單**：`engine:` frontmatter 欄位終於可以直接從 UI 設定（跟隨全域設定／Claude／Codex 三選一），不用再手動編輯 frontmatter。已用隔離的 `ng serve`（避開 Docker 的舊程式碼問題）實際驗證下拉選單渲染、選擇、儲存送出的 payload 都正確。
4. **Team 編輯器刻意沒加類似欄位**：查證後發現目前完全沒有任何 UI 入口能直接「執行」一個已存檔的 Team（`activateTeam()`/`openTeamRun()` 這條路徑在十一節發現 6 已經確認是死碼並移除，唯一能觸發 `/api/team/run` 的只有 HR 自動組隊）。在這個前提下，Team 層級的引擎預設欄位現在加了也不會被任何東西讀取，是無效果的 UI，這次刻意不做，避免重蹈「加了功能但沒有真正的使用路徑」的覆轍。
5. **`CODEX_API_KEY` env var 認證路徑**：使用者確認目前用 `codex login` 的方式登入，不需要另外設定 API key，這條路徑對目前的實際使用情境不適用，決定不繼續投入時間驗證。Code-level 行為（`codex_engine.py` 正確把 `api_key` 參數設進 `CODEX_API_KEY` 環境變數）已有既有的 mock 測試鎖定，這部分維持現狀。

### 測試覆蓋（這輪結束時）

`pytest tests/` **210 個測試全過**（新增：HR 派發路由到 Codex、HR 派發／Team Run 的 Anthropic key 不外洩到 Codex、Agent PUT/POST 帶合法與不合法 `engine` 值）。`tsc --noEmit`／`ng build` 全過。

### 仍待處理

- `main.py` 的 pooled SDK 路徑（`handle_chat`/`handle_team_chat`/`handle_team_execute`）仍然只支援 Claude——這條路徑用 Anthropic 自家 SDK 做長駐連線，Codex 沒有對應物，維持這輪核准的 plan 排除範圍，暫不處理。
- PR #6 的 base branch 仍未從 `worktree-cozy-dancing-dragon` 改成 `master`——本機已經 rebase 乾淨（見對話紀錄），但 `git push --force-with-lease` 被使用者的安全 hook 擋下，需要使用者自己執行 push（或調整 hook）才能完成最後這一步。
- 主要 checkout（`D:\Users\666\Desktop\claude-desktop`）仍然落後 `origin/master`，建議找時間 `git pull`（見十三節）。
