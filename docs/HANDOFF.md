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

新增 `tests/test_team_run_execution_mode.py`（3 個測試）：直接呼叫 `_execute_team_run_core()` 用 monkeypatch 過的 `_agent_run_capture` 驗證 inline payload 的 `execution_mode: "sequential"` 真的會讓 step 2 的 prompt 收到 step 1 的輸出；順手更新 `tests/test_upgrade.py::TestAdversarialDebate`（原本手動建構 run state 時沒帶 `execution_mode`/`leader`，靠舊的 team_id 回頭查檔行為才能通過，改成比照 `handle_team_run_post` 實際會產生的資料直接帶上）。`pytest tests/` 165 個測試全過。

### 🟠 發現 2（未修復，需要產品/安全判斷，見下方待決策）｜`/api/team/run` 完全沒有工具權限核准機制

用真實 `claude -p` CLI 直接重現 `_agent_run_capture()`（`routes/teams.py`）目前的 subprocess 呼叫方式驗證過（非猜測）：`cmd = [claude_bin, "-p", full_prompt, "--output-format", "stream-json", "--verbose"]`，**沒有 `--permission-mode` flag，`stdin=asyncio.subprocess.DEVNULL`（無法回應任何核准提示）**。實測結果：CLI 不會卡住等待，而是**直接把需要核准的工具呼叫自動判定為拒絕**（`permission_denials` 裡記一筆，回傳文字說明「請求被系統阻擋」），process 正常以 `exit 0` 結束。

問題是：這代表**「發任務給 Team」進度面板（`POST /api/team/run`，P2-F4/F5，前端 `runTeam()`/`streamTeamRun()`）這個最主要的 team 協作入口，實質上做不了任何需要核准的真實操作**（寫檔、跑指令等）——每個 team member 遇到這類操作都會被 CLI 靜默拒絕，只能回報「無法執行」。

對照組：同一個檔案系統裡，`main.py` 的 `/api/team/execute`（`handle_team_execute`，另一條「團隊對話」相關路徑）已經做了完整的權限核准轉發——無論是舊的 stdin y/n 偵測模式（`_legacy_exec`）還是新的 pooled SDK `can_use_tool` callback 模式（`_pooled_exec`），都會把 `permission_request` 事件透過 SSE 推給前端，前端也確實有處理這個事件型別（`app.ts:3619`）。**但 `_handleTeamRunEvent()`（Team Run 進度面板專用的 SSE handler，`app.ts:2178`）完全沒有 `permission_request` 這個 case** —— 就算現在補上後端轉發，前端目前也接不住。

**這是產品/安全層級的決策，不是我可以單方面決定的修法**，列出三個選項供選擇：
1. **維持現狀（最安全，維持現況）**：Team Run 只適合「分析、規劃、產出文字報告」這類任務，不適合「直接改代碼」；在 UI 上加提示告知使用者這個限制。零開發成本。
2. **補齊權限轉發（最一致，工程量中等）**：仿照 `/api/team/execute` 的模式，`_agent_run_capture` 改用 `stdin=PIPE` + 偵測/轉發 `permission_request` 事件，前端 `_handleTeamRunEvent()` 也要補上核准/拒絕 UI（Team Run 進度面板目前沒有這塊）。維持現有安全模型（使用者仍要逐一核准），但要多做一輪前後端功能。
3. **針對 Team Run 開放 `--permission-mode acceptEdits`（或更寬鬆）**：讓 team member 能自動完成檔案編輯類操作而不逐一詢問，最貼近「team 協作直接把活幹完」的產品直覺，但等於放寬了這條路徑的安全模型（跟 T1/T2/T16-18 這幾輪特別在意的「使用者親自核准敏感操作」原則有出入），需要使用者明確拍板才能做。

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

### 🟡 發現 5（未修復，低優先級，UI 目前不可觸達）｜consensus 執行模式在成員數 >2 時會錯亂

`_execute_team_run_core()` 的 consensus 分支寫死只處理前 2 位成員（`agent_a = steps[0]`, `agent_b = steps[1]`），第 3、4 步驟用 `if len(steps) < 3/4: steps.append(...)` 才新增 —— 但如果 team 本來就有 ≥3 位成員（`handle_team_run_post` 已經照 member 數量建好對應筆數的 `steps`），第 3 位成員原本的 `steps[2]` 會被直接**覆寫**成 `agent_a` 的 revision 結果（`step_start` 事件回報的 agent 名字跟原本存在 `steps[2]["agent"]` 的名字對不上，UI 會顯示錯的成員名稱），第 4 位以後的成員則永遠停在 `"pending"`，即使整個 run 的 `status` 已經變成 `"done"`，看起來會像「卡住了」。目前前端 Team 編輯器（`app.html:2365-2366`）只提供 `parallel`／`sequential` 兩個選項，`consensus` 沒有從 UI 暴露、HR Agent 產生的 plan 也不會用到這個模式，所以目前只能透過直接編輯 team YAML 檔手動觸發，優先級較低，暫不處理，先記錄。

### 已排除的假設（複查後確認不是問題）

`build_team_memory_context()` 讀取 `_team_memory_dir(team_id)` 時對 inline/HR 派發（`team_id=""`）沒有特別擋，一開始懷疑會跟其他 HR 任務共用同一份 `CLAUDE_HOME/memory/teams/shared.md`／`projects/<slug>.md` 造成記憶體互相污染；但複查 `_execute_team_run_core()` 的寫入端（consensus 分支結尾、sequential/parallel 分支結尾）後確認兩處都已經有 `if team_id and cwd:` 擋著，inline/HR 派發的 run **從來不會寫入**這兩個共用檔案，所以讀到的永遠是空字串，不構成實際的污染路徑。

### 前端複查範圍與限制

`tsc --noEmit` / `ng build` 全過。程式碼層面複查了 Team Run／Team Chat／HR Agent 三條前端路徑的事件處理完整性（發現 2 提到的 `permission_request` 缺口）；**受限於這是無 GUI 的背景執行環境（無法起 Electron 視窗），沒有做真實瀏覽器互動的端對端測試**，這部分若要做，建議在有畫面的環境用 `claude-in-chrome` 或 Playwright 補一輪。

### 本輪結論

Team 協作系統的**資料模型與 CRUD**（P1/P2 後端）沒發現新問題；**執行引擎**這次抓到兩個已修復的邏輯 bug——一個確定會影響 ROADMAP 旗艦功能（HR 自動組隊）的 `execution_mode` 被忽略問題，一個是會讓 run 永久卡死+洩漏記憶體的例外吞噬問題；另外抓到一個「最主要的 team 協作入口做不了真實工具操作」的架構性限制，這個需要使用者對安全模型拍板才能往下推進。建議在推進「封裝成多環境軟體／支援 Codex 版本」之前，先決定發現 2 的方向——因為不管選哪個選項，都會影響到之後 Codex backend adapter 要不要／怎麼支援同一種 permission-relay 機制。
