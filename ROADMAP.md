# Claude 桌面版 — 產品路線圖

> 整理自 2026-06-26 設計討論。未完成的設計決策以 ❓ 標記。

---

## 一、現有功能（已完成）

### 核心對話
- 多面板對話（最多 4 個平行視窗）
- Session 歷史 + FTS5 全文搜尋
- Slash 指令（`/`）觸發 skill 選單
- 串流輸出、中止、重新生成

### 右側面板（各自獨立，尚無連結）
- **Agent** — 選取使用中的代理人
- **Skills** — 點擊插入 `/skill-name`
- **MCP** — 啟動 / 停止 MCP 伺服器
- **Memory** — 讀寫 `~/.claude/projects/<slug>/memory/`（今日修正）
- **Schedule** — Cron 排程提示
- **Soul** — 靈魂人格設定

### 其他
- Dashboard：Token 用量、熱力圖、連續天數
- 用量監控彈窗（本次對話 token + context 進度條）
- 自動更新、系統匣
- 備份 / 還原

---

## 二、核心設計方向：資源映射系統（Resource Mapping）

### 2.1 資源種類與關係

```
Soul        （人格，1:1 對 Agent）
 └── Agent  （角色定義）
       ├── Skills[]   （能力集合）
       │     ├── MCPs[]    （外部工具）
       │     └── Memory[]  （知識鍵值）
       ├── MCPs[]     （Agent 層級的工具）
       └── Memory[]   （Agent 層級的知識）
```

**關係規則：**
| 來源 | 目標 | 數量 |
|------|------|------|
| Agent | Soul | 1 |
| Agent | Skills | 多個 |
| Agent | MCPs | 多個 |
| Agent | Memory keys | 多個 |
| Skill | MCPs | 多個 |
| Skill | Memory keys | 多個 |

### 2.2 儲存格式（延伸 Agent frontmatter）

不新增獨立 mapping 檔案；直接在 agent `.md` 的 YAML frontmatter 裡宣告：

```yaml
---
name: code-reviewer
description: 程式碼審查專家
tools: Read, Grep, Glob
soul: 自我介紹
skills:
  - typescript-reviewer
  - security-review
memory:
  - project-conventions
  - known-bugs
mcp:
  - github
  - linear
---
```

**優點：**
- 與 Claude Code CLI 格式一致
- Agent 檔案自帶完整設定，可攜可分享
- 不需額外資料庫

❓ **待決定：** Skill 本身也需要 frontmatter 擴充嗎？還是 Skill 的 MCP/Memory 依賴只在 Agent 層聲明？

### 2.3「啟動 Agent」行為

點擊 Agent 卡片的「啟動」後，自動：
1. 切換 Soul → 對應靈魂
2. 在 Skills 頁籤高亮顯示已連結的 skills
3. 啟動對應 MCPs（若未執行）
4. 把對應 Memory keys 加入「上下文」
5. ❓ 是否自動在訊息前插入 `--agent <name>`？

---

## 三、Teams 系統

### 3.1 概念

Team = 多個 Agent 的靜態預設組合，專為特定任務類型設計。

```yaml
# ~/.claude/teams/fullstack-dev.yaml
name: fullstack-dev
description: 全端開發團隊
members:
  - agent: frontend-dev
    role: 前端實作
  - agent: backend-dev
    role: API 設計
  - agent: code-reviewer
    role: 審查
  - agent: tdd-guide
    role: 測試
```

### 3.2 任務分派選項

```
使用者輸入任務
 ├── [單獨] → 直接發給指定 Agent
 ├── [團隊] → 發給指定 Team（依序或平行執行）
 └── [自動] → 發給 HR Agent（自動分析 → 組隊 → 派任）
```

❓ **待決定：**
- Team 內的 agent 是平行執行還是有依賴順序？
- 每個 agent 的輸出如何傳遞給下一個？（共享 context？獨立 session？）
- 結果如何匯集呈現？

### 3.3 HR Agent（總指揮）

HR Agent 是一個特殊的「元 agent」，職責：
1. 接收任務描述
2. 查詢 Agent Registry（知道每個 agent 的能力）
3. 決定需要哪些 agent、以什麼順序
4. 組成臨時 Team
5. 派任子任務 → 收集結果 → 整合回報

HR Agent 本身也是一個 `.md` 檔案，但有特殊的 `type: orchestrator` 標記：

```yaml
---
name: hr-agent
description: 自動組隊與任務協調
type: orchestrator
tools: Read, Grep, Glob, Bash
---
```

❓ **待決定：**
- HR Agent 是否需要知道所有 agent 的能力摘要？（自動生成 agent registry？）
- 是否支援巢狀委派（HR → Sub-Team Leader → Agent）？

---

## 四、後端需求（Multi-Agent 執行）

目前後端一次只能跑一個 `claude` 子程序（對應一個對話面板）。

### 需要新增：
1. **並行子程序管理** — 同時跑多個 `claude --agent <name>` 程序
2. **任務佇列** — 管理任務分配與狀態
3. **結果聚合** — 把多個 agent 的輸出彙整成一份報告
4. **Agent-to-Agent 通訊** — 一個 agent 的輸出可以作為下一個的輸入

### API 端點（新增）：
```
POST /api/team/run         — 對 Team 發任務
POST /api/hr/dispatch      — 發給 HR Agent
GET  /api/team/status/{id} — 查詢任務進度
GET  /api/agents/registry  — 取得所有 agent 能力摘要
```

---

## 五、UI 調整規劃

### 5.1 Agent 頁籤（擴充）

```
┌─────────────────────────────────┐
│ 🔍 搜尋 agents...               │
├─────────────────────────────────┤
│ ◉ code-reviewer      [啟動]     │
│   Soul: 自我介紹                 │
│   Skills: 2 │ MCP: 1 │ Mem: 1  │
├─────────────────────────────────┤
│ ○ planner            [啟動]     │
│   Skills: 3 │ MCP: 0 │ Mem: 2  │
└─────────────────────────────────┘
[+ 建立 Agent]  [管理 Teams]
```

### 5.2 Teams 頁籤（新增）

```
┌─────────────────────────────────┐
│ Teams                           │
├─────────────────────────────────┤
│ fullstack-dev     [啟動] [編輯] │
│ frontend / backend / reviewer   │
├─────────────────────────────────┤
│ research-team     [啟動] [編輯] │
│ researcher / analyst / writer   │
└─────────────────────────────────┘
[+ 建立 Team]
```

### 5.3 各頁籤加「來源標示」

- Skills：已連結的 skill 旁顯示 `● agent` 標記
- Memory：被當前 agent 使用的 key 自動勾選
- MCP：agent 需要的 MCP 顯示「此 Agent 需要」提示

---

## 六、實作優先順序

### Phase 1 — Agent Mapping（單一 Agent，無需多程序）

**後端**
- [ ] P1-B1：`handle_agents` 解析 frontmatter 的 `skills / memory / mcp / soul / output_memory`
- [ ] P1-B2：`GET /api/agents/:id` 回傳單一 agent 完整連結資訊
- [ ] P1-B3：`PUT /api/agents/:id` 更新 agent frontmatter（寫入 `.md` 檔）

**前端**
- [ ] P1-F1：Agent 卡片顯示連結摘要（Soul｜Skills N｜MCP N｜Mem N）
- [ ] P1-F2：「啟動 Agent」按鈕：注入 `--agent <name>` + 切換 Soul + 高亮 Skills + 啟動 MCPs + 勾選 Memory
- [ ] P1-F3：Agent 詳細面板（展開查看 / 編輯連結）
- [ ] P1-F4：Skills 頁籤：已連結的 skill 顯示 `● agent` 標記
- [ ] P1-F5：Memory 頁籤：agent 關聯的 key 自動勾入上下文
- [ ] P1-F6：MCP 頁籤：agent 需要的 MCP 顯示「此 Agent 需要」提示

**Skill frontmatter 擴充**
- [ ] P1-S1：後端解析 skill 的 `mcp / memory / output_memory`
- [ ] P1-S2：Skill 詳細面板顯示依賴（MCP、讀取 Memory、輸出 Memory）

---

### Phase 2 — Teams 定義（靜態組合，UI 管理）

**後端**
- [ ] P2-B1：`GET/POST/PUT/DELETE /api/teams`（讀寫 `~/.claude/teams/*.yaml`）
- [ ] P2-B2：Team YAML 格式定義（name / members[] / each: agent + role + input_memory + output_memory）

**前端**
- [ ] P2-F1：右側面板新增 **Teams 頁籤**
- [ ] P2-F2：Teams 列表（卡片：名稱、成員數、描述）
- [ ] P2-F3：Team 建立 / 編輯 UI（拖曳 agents 排序、設定每步驟的 memory 流向）
- [ ] P2-F4：「發任務給 Team」入口（輸入框 + 選擇 Team）
- [ ] P2-F5：任務執行進度面板（顯示每個 agent 的執行狀態）

---

### Phase 3 — Multi-Agent 序列執行（後端流水線）

**後端**
- [ ] P3-B1：`POST /api/team/run`（接收任務 + team name，啟動流水線）
- [ ] P3-B2：序列子程序管理器（依序啟動各 agent，每步等待完成再傳 memory 給下一步）
- [ ] P3-B3：任務狀態 DB（task_id / step / agent / status / memory_key）
- [ ] P3-B4：`GET /api/team/status/:task_id`（輪詢進度）
- [ ] P3-B5：`GET /api/team/result/:task_id`（取得最終彙整結果）

**前端**
- [ ] P3-F1：即時進度條（每個 agent step 的執行狀態）
- [ ] P3-F2：每步驟的輸出 memory 可展開查看
- [ ] P3-F3：失敗步驟可重試或跳過

---

### Phase 4 — HR Agent（自動組隊）

**後端**
- [ ] P4-B1：`GET /api/agents/registry`（回傳所有 agent 的 name + description + skills）
- [ ] P4-B2：`POST /api/hr/dispatch`（傳入任務描述，HR Agent 分析並建立臨時 Team）
- [ ] P4-B3：HR Agent 執行流程（呼叫 claude 扮演 orchestrator，輸出 JSON team plan）

**前端**
- [ ] P4-F1：對話欄加入「🤖 自動組隊」按鈕
- [ ] P4-F2：HR Agent 回傳的 Team plan 可預覽、修改後再執行
- [ ] P4-F3：最終結果彙整呈現

---

## 七、設計決策（已確認）

| # | 問題 | 決定 | 理由 |
|---|------|------|------|
| 1 | Skill 需要自己的 MCP/Memory 依賴嗎？ | **是** | Skill 本身有固定工具需求（如 `typescript-reviewer` 需要 github MCP），不應每個 Agent 重複聲明 |
| 2 | Team 執行順序 | **序列流水線** | 配合 3.C 的 memory 中繼：Agent A 寫 memory → Agent B 讀 → 依此類推 |
| 3 | Agent 間 context 傳遞 | **Memory 檔案中繼（3.C）** | 按需讀取，避免巨大 context；每個 Agent 只讀自己需要的 memory key |
| 4 | HR Agent 判斷依據 | **description + skills 清單** | HR Agent 讀取所有 agent 的 description 與 skills 欄位，判斷最適合的組合 |
| 5 | 啟動 Agent 是否注入 `--agent` | **是** | 啟動後在 claude 指令加上 `--agent <name>`，讓 CLI 真正切換 agent 行為 |

---

## 八、核心流程（設計定稿）

### 8.1 單一 Agent 啟動流程

```
使用者點「啟動 Agent」
 ↓
1. UI 切換 Soul 顯示（對應 soul 名稱）
2. 對話欄注入 --agent <name> 旗標
3. Skills 頁籤高亮已連結的 skills
4. 自動啟動對應 MCPs（若未執行）
5. 對應 Memory keys 加入「上下文」候選清單
   （使用者可手動勾選要帶入哪幾個）
```

### 8.2 Team 執行流程（序列流水線）

```
使用者發任務給 Team
 ↓
Step 1: Agent A 執行
  - 讀取指定 Memory keys（知識輸入）
  - 執行，產出結果
  - 把結果寫入 Memory（memory-task-{id}-step1.md）
 ↓
Step 2: Agent B 執行
  - 讀取 Agent A 的輸出 memory
  - 讀取自己需要的 Memory keys
  - 執行，產出結果，寫入 memory
 ↓
...
 ↓
最後一個 Agent 彙整 → 回傳最終結果給使用者
```

### 8.3 HR Agent 自動組隊流程

```
使用者描述任務（自然語言）
 ↓
HR Agent 讀取 Agent Registry：
  - 每個 agent 的 name / description / skills[]
 ↓
HR Agent 分析任務，決定：
  - 需要哪些 agents（角色）
  - 執行順序（誰先誰後）
  - 每一步要讀/寫哪些 memory keys
 ↓
動態建立臨時 Team
 ↓
按序列流水線執行（同 8.2）
 ↓
彙整結果回報使用者
```

### 8.4 Skill 與 Memory 的關係

```yaml
# skill: typescript-reviewer.md frontmatter
---
name: typescript-reviewer
description: TypeScript 程式碼審查
mcp:
  - github
memory:
  - project-conventions   # 讀取：專案規範
  - review-checklist      # 讀取：審查清單
output_memory:
  - review-result         # 寫入：審查結果（供下游 Agent 讀取）
---
```

每個 skill 明確聲明：
- `memory`：需要**讀取**的知識
- `output_memory`：執行後**寫入**的結果（其他 Agent 的輸入來源）
