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

- [x] **T1｜MCP 敏感操作授權閘門形同虛設** — `backend/routes/mcp_debugger.py` 原本只信任呼叫方自報的 `authorized: true`，疊加既有 CORS（`allow_credentials=True` + origin `"*"`）可被任意網頁繞過確認流程觸發破壞性 MCP 呼叫。已改為伺服器核發、單次使用、TTL 120 秒的 `pending_id`，`frontend/app.ts` 同步改讀後端回傳的 `pending_id`（原本寫死成固定字串）。CORS 白名單化仍待評估，未變更。
- [ ] **T2｜`docker-compose.yml` 新增 docker.sock 掛載，配合 T1 構成提權鏈** — 需與使用者確認是否真的需要掛整個 socket，或改用受限中介方案（如 docker-socket-proxy）。**需要產品決策，尚未處理。**
- [x] **T3｜Electron dev 模式後端啟動靜默失敗** — `electron/main.js` 用未 import 的 `execSync`，改用已 import 的 `execFileSync`。

### 🟠 P1 — 既有測試 / CI

- [x] **T4｜main.py 重構造成既有測試回歸** — 補回 `_agent_dict`、`_parse_yaml_simple` 的 import。
- [x] **T5｜新功能測試從未真正進 CI** — `backend/test_upgrade.py` 搬進 `tests/`，並把其中驗證「假授權會放行」的過時測試改成驗證新安全流程（含重放防護）。`pytest tests/` 現在 98 個測試全過。

### 🟡 P2 — 中等

- [x] **T6｜`run_artifacts.py` 路徑檢查可被同層目錄繞過** — 改用 `resolved_full.is_relative_to(base_dir)`。
- [ ] **T7｜`message_bus.py` async 訂閱者例外被靜默吃掉** — `publish()` 對 async callback 用 `create_task` 發送後不接錯誤、不留參照。待修。
- [ ] **T8｜`memory_agent.py` 同步阻塞呼叫卡住 event loop** — `build_memory_context`/`build_team_memory_context` 在 `main.py` 熱路徑同步呼叫，待改成 `await asyncio.to_thread(...)`。
- [ ] **T9｜`database.py` 自我修復邏輯過猛** — `_init_db()` 捕到任何 `sqlite3.Error` 就刪除重建整個索引 DB，重建呼叫沒有二次 try/except。待修。
- [ ] **T10｜前端：設定說明彈窗變孤兒功能** — 觸發按鈕被移除但 signal/modal/ESC 邏輯還留著。待修。
- [ ] **T11｜前端：訊息截斷失敗時畫面與後端歷史會分岔** — `confirmEditMsg()` 呼叫 `truncateSession` 沒有錯誤處理。待修。
- [ ] **T12｜前端：語音輸入結果覆蓋手動編輯內容** — `toggleMic()` 的 `onresult` 永遠用 `startText` 覆寫，且 `SpeechRecognition` 沒在 `ngOnDestroy` 強制停止。待修。

### 🟢 P3 — 低

- [ ] **T13｜前端兩套不一致的複製到剪貼簿實作**。
- [ ] **T14｜MCP RPC pending authorization 送出新請求時被靜默清空**。
- [ ] **T15｜（既有問題，非本次新增）Electron `shell.openExternal` IPC 沒有 protocol allowlist**。

### 已驗證沒問題

前端 `ng build`/`tsc --noEmit` 全過；Electron `contextIsolation: true`、`nodeIntegration: false` 設定正確；所有變更的 backend 檔案 `py_compile` 全過；前端沒有 RxJS 訂閱洩漏或 XSS 風險。
