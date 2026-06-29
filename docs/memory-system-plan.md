# 分層記憶系統 — 完整計畫任務書

> 最後更新：2026-06-29（修訂：Agent 工作日誌定義、Team 注入邏輯）

## 背景與目標

Claude Desktop 目前的記憶機制僅有專案內部 KV 記憶，且 Agent 對話不自動注入任何記憶內容。
本計畫建立一套**分層、分類、跨 Agent 可共享**的記憶架構，讓 Agent 像真人一樣擁有個人記憶，Team 像團隊一樣共享知識。

---

## 記憶路徑對應確認

| 環境 | 根路徑 |
|------|--------|
| Windows 主機（Claude Code CLI） | `C:\Users\666\.claude\` |
| Docker 容器（Backend） | `/root/.claude/` |

**結論：同一份 volume，雙向即時可見，無需額外同步。**

---

## 記憶分類架構

### 目錄結構

```
C:\Users\666\.claude\
│
├── memory\                          ← 全域公共記憶
│   ├── user\
│   │   └── profile.md              # 使用者身份、偏好、習慣
│   ├── system\
│   │   └── state.md                # Claude Code 全域狀態、安裝設定
│   ├── agents\
│   │   └── <agent-id>\
│   │       ├── identity.md         # Agent 自我認知、專長、風格
│   │       └── projects\
│   │           └── <slug>.md       # 此 Agent 在該專案累積的【經驗與成長】
│   └── teams\
│       └── <team-id>\
│           ├── shared.md           # 成員互知的共享知識
│           └── projects\
│               └── <slug>.md      # 此 Team 做過該專案的集體記憶（自動生成摘要）
│
└── projects\
    └── <slug>\
        └── memory\                 ← 專案內部記憶（詳細進度，現有機制）
            ├── progress.md         # 詳細進度、TODO（Agent 自己維護，需要時匯報）
            ├── decisions.md        # 架構決策
            └── issues.md           # 已知問題
```

### 記憶類型說明

| 類型 | 內容本質 | 更新頻率 | 預設注入範圍 |
|------|---------|---------|------------|
| User | 使用者身份、偏好 | 低 | 所有 Agent |
| System | Claude Code 全域設定 | 低 | 所有 Agent |
| Agent Identity | 自我認知、專長、風格 | 低 | 該 Agent + 同 Team 成員互知 |
| **Agent Project** | **在此專案累積的經驗與成長**（非進度） | 中 | 該 Agent + 同 Team 成員互知 |
| Team Shared | 團隊共識、協作模式 | 中 | Team 所有成員 |
| Team Project | 過去執行此專案的集體摘要 | 中（自動） | Team 所有成員 |
| Project Internal | 詳細進度、決策、問題（最細） | 高 | 單 Agent 對話；Team 不預載 |

---

## Agent 工作日誌的正確定義

> Agent 的專案記憶（`agents/<id>/projects/<slug>.md`）記錄的是**經驗與成長**，不是進度追蹤。

### 正確內容（經驗）

```markdown
# Pi 在 claude-desktop 的經驗

- Docker 環境下 aiohttp 必須用 host="0.0.0.0"，用 127.0.0.1 會讓外部無法連線
- 使用者偏好先看結論，解釋要簡短、有表格
- 這個專案的前端是 Angular standalone component，不用 NgModule
```

### 不應出現（進度）

```markdown
# Pi 在 claude-desktop 的進度  ← 錯誤定義

- 2026-06-29 完成 Phase 1
- 下一步：Phase 2
```

進度由 **專案內部記憶**（`projects/<slug>/memory/`）負責，Agent 自己維護，需要時再匯報，不預先共享。

---

## 記憶注入邏輯

### 單 Agent 對話時

```
注入順序：
1. [User]    memory/user/profile.md
2. [System]  memory/system/state.md
3. [Agent]   memory/agents/<id>/identity.md
4. [Agent]   memory/agents/<id>/projects/<slug>.md（若存在）
5. [Project] projects/<slug>/memory/*.md（詳細進度，單 Agent 可用）
```

### Team Run 時（修訂版）

```
注入順序（每位成員各自收到）：
1. [User]    memory/user/profile.md
2. [Team]    memory/teams/<team-id>/shared.md
3. [Team]    memory/teams/<team-id>/projects/<slug>.md（若存在）
4. [Cross]   memory/agents/<所有成員-id>/identity.md（成員互知，含本人）
5. [Agent]   memory/agents/<本人-id>/projects/<slug>.md（本人經驗）

❌ 不注入：projects/<slug>/memory/（專案內部進度，需要時才主動查詢）
```

**設計原則**：Team 共享的是**智慧與經驗**，不是行程表。需要了解專案細節時，由 Agent 主動呼叫工具讀取。

---

## 實作狀態

### Phase 1 — 目錄結構 + 後端 API ✅

- `_global_memory_dir()` / `_agent_memory_dir()` / `_team_memory_dir()` helper 已建立
- 17 個 `/api/mem/*` endpoints 已實作並部署

### Phase 2 — 對話時自動注入記憶 ✅

- `build_memory_context(agent_id, cwd)` 已實作
- `handle_chat` 已修改，對話前自動組裝並注入五層記憶

### Phase 3 — Team Run 記憶共享 ✅（已依修訂版更新）

- `build_team_memory_context()` 已實作
- Team Run 不預載專案內部記憶
- Team Run 完成後自動寫入 `teams/<id>/projects/<slug>.md`

### Phase 4 — 前端記憶檢視器（唯讀）✅

- 右側面板新增「記憶」tab
- 分層樹狀展示，含 Markdown 渲染與檔案路徑提示

---

## 連接確認

| 項目 | 狀態 |
|------|------|
| Windows 主機 `C:\Users\666\.claude\memory\` | ✓ |
| Docker 容器 `/root/.claude/memory/` | ✓ 同一 volume |
| Claude Code CLI 寫入 → Backend 可讀 | ✓ 即時同步 |
| Backend 寫入 → Claude Code CLI 可讀 | ✓ 即時同步 |
| `memory\agents\Pi\identity.md` | ✓ 已建立 |

---

## 注意事項

1. 每份記憶檔案上限 2000 字元，避免 context 爆炸
2. Agent 工作日誌寫的是**經驗**，進度留在專案內部記憶
3. Team 不預載專案內部記憶，需要時由 Agent 主動查詢
4. `MEMORY.md` 為 Claude Code 自動記憶索引，不納入本系統管理
5. 專案 slug 統一使用 `_encode_slug()` 函式轉換
