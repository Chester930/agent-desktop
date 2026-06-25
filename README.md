# Claude 桌面版

> Claude Code 的圖形介面 —— 在 Electron 視窗裡使用 Claude，保留完整 CLI 能力。

![Platform](https://img.shields.io/badge/platform-Windows-blue)
![Electron](https://img.shields.io/badge/Electron-42-47848F)
![Angular](https://img.shields.io/badge/Angular-19-DD0031)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)

---

## 目錄

1. [這是什麼](#這是什麼)
2. [必要條件](#必要條件)
3. [安裝方式 A：下載安裝包](#安裝方式-a下載安裝包推薦)
4. [安裝方式 B：從原始碼執行](#安裝方式-b從原始碼執行開發者)
5. [首次啟動設定](#首次啟動設定)
6. [功能一覽](#功能一覽)
7. [進階設定](#進階設定)
8. [打包發布](#打包發布)
9. [疑難排解](#疑難排解)
10. [LLM 輔助安裝腳本](#llm-輔助安裝腳本)

---

## 這是什麼

Claude 桌面版是一個桌面 GUI，讓你能以視窗化介面使用 Claude Code CLI。  
底層仍呼叫你本機已安裝的 `claude` 指令，所有對話、記憶、agents、skills、MCP 設定都從你的 `~/.claude/` 目錄讀取。

**主要特性：**

| 功能 | 說明 |
|------|------|
| 多面板對話 | 最多 4 個平行對話視窗 |
| Session 歷史 | SQLite 索引 + FTS5 全文搜尋，毫秒級回應 |
| Agents / Skills / MCP | 直接從右側面板選取，可搜尋過濾 |
| Slash 指令 (`/`) | 15 個內建指令 + 自動列出所有 skills |
| Dashboard | Token 用量、熱力圖、連續使用天數 |
| 排程 | Cron 運算式定時觸發提示 |
| 靈魂（Soul） | 跨 session 人格設定 |
| 自動 Skill 生成 | 分析對話並草擬 skill 檔案 |
| Vault API Key | 支援 1Password CLI / pass 等密碼管理工具 |
| 系統匣 | 最小化到系統匣，自動更新 |

---

## 必要條件

### 所有使用者（安裝包 + 原始碼）

**1. Claude Code CLI**

```bash
# 安裝
npm install -g @anthropic-ai/claude-code

# 登入（產生 ~/.claude/ 目錄）
claude login

# 驗證
claude --version
# 預期輸出：claude 1.x.x  或更新版本
```

> ⚠️ 若未登入，應用程式仍可啟動，但送訊息時會出現認證錯誤。

---

### 僅限原始碼安裝（方式 B）

**2. Node.js ≥ 22.22.3**

```bash
# 安裝 nvm（Windows）
# 下載 nvm-windows：https://github.com/coreybutler/nvm-windows/releases

nvm install 22.22.3
nvm use 22.22.3

# 驗證
node --version   # 應顯示 v22.22.3 或更新
npm --version    # 應顯示 10.x 或更新
```

**3. Python ≥ 3.10**

```bash
# 下載：https://www.python.org/downloads/
# 安裝時勾選「Add Python to PATH」

# 驗證
python --version   # 應顯示 Python 3.10.x 或更新
pip --version
```

---

## 安裝方式 A：下載安裝包（推薦）

> 不需要 Python 或 Node.js，只需要 Claude Code CLI。

1. 前往 [Releases](../../releases/latest) 下載最新的 `Claude-桌面版-Setup-x.x.x.exe`
2. 執行安裝程式（Windows 可能顯示 SmartScreen 警告 → 點「更多資訊」→「仍要執行」）
3. 安裝完成後啟動「Claude 桌面版」
4. 繼續 [首次啟動設定](#首次啟動設定)

---

## 安裝方式 B：從原始碼執行（開發者）

### 步驟 1 — 取得原始碼

```bash
git clone https://github.com/Chester930/claude-desktop.git
cd claude-desktop
```

> 若使用 LLM 輔助安裝，請告訴 LLM 你把專案放在哪個路徑，例如：  
> `C:\Users\你的名字\Desktop\claude-desktop`

### 步驟 2 — 安裝後端相依套件

```bash
pip install -r backend/requirements.txt
```

驗證：

```bash
python -c "import aiohttp, aiohttp_cors, sqlite3; print('OK')"
# 預期輸出：OK
```

### 步驟 3 — 安裝前端相依套件

```bash
cd frontend
npm install
cd ..
```

### 步驟 4 — 建置前端

```bash
cd frontend
npm run build
cd ..
```

> 第一次建置約需 30–60 秒。後續開發可使用 `npm run start` 搭配 HMR（熱更新）。

### 步驟 5 — 啟動應用程式

**Windows（推薦）：**

```bat
start.bat
```

**手動啟動（開發模式 / HMR）：**

```bat
start.bat --dev
```

這會同時啟動：
- Python 後端（port 8765）
- Angular dev server（port 4200，HMR）
- Electron 視窗

---

## 首次啟動設定

### 設定 Claude Code 專案目錄

這是最重要的設定。Claude Code 把所有資料（記憶、排程、靈魂等）存在：

```
~/.claude/projects/<專案路徑編碼>/
```

「專案路徑編碼」的規則是把路徑中的 `:` 和 `\` 都換成 `-`，例如：

| 你的專案路徑 | 對應的目錄名稱 |
|---|---|
| `C:\Users\你的名字\Desktop\claude-desktop` | `C--Users-你的名字-Desktop-claude-desktop` |
| `C:\Users\你的名字\projects\my-project` | `C--Users-你的名字-projects-my-project` |

**設定方式：**

1. 點右上角齒輪圖示（⚙）開啟設定
2. 找到「Claude Code 專案目錄」欄位
3. 貼上你平常在 Claude Code CLI 使用的專案根目錄絕對路徑
4. 點「儲存」

> 若不確定是哪個目錄，在你的專案根目錄執行：
> ```bash
> cd /你的專案目錄 && pwd
> # Windows: cd \你的專案目錄 && cd
> ```

設定完成後，記憶、排程、靈魂等資料就會從正確的 `~/.claude/projects/` 子目錄讀取。

---

## 功能一覽

### 對話介面

- 輸入訊息，Enter 送出（可在設定改為 Ctrl+Enter）
- 輸入 `/` 觸發指令選單（含 15 個內建指令 + 所有 skills）
- 拖曳檔案或 Ctrl+V 貼上截圖作為附件
- 點「■」中止串流

**內建 `/` 指令：**

| 指令 | 功能 |
|------|------|
| `/new` | 開始新對話 |
| `/clear` | 清除目前訊息 |
| `/compact` | 壓縮對話節省 token |
| `/review` | Code Review 提示 |
| `/plan` | 規劃實作步驟 |
| `/tdd` | TDD 流程 |
| `/explain` | 解釋程式碼 |
| `/git` | Git 狀態摘要 |
| `/shortcuts` | 顯示快捷鍵列表 |
| `/search` | 搜尋對話歷史 |

### 鍵盤快捷鍵

| 快捷鍵 | 功能 |
|--------|------|
| `Ctrl+N` | 新增對話分頁 |
| `Ctrl+B` | 切換左側欄 |
| `Ctrl+K` | 開啟指令面板 |
| `Esc` | 關閉彈窗 |
| `/` | 觸發 skill 選單 |

### Session 管理

- 左側欄按日期分組（今天 / 昨天 / 本週 / 更早）
- 全文搜尋（FTS5，支援中文）
- 雙擊 session 重新命名
- 拖曳 session 到畫布建立新面板
- 點 ✦ 按鈕從對話自動生成 Skill 草稿

### 右側面板

各頁籤均有搜尋欄（Agents / Skills / MCP 均可即時過濾）：

- **Agent** — 選擇使用中的代理人
- **Skills** — 可直接點擊使用（會插入 `/skill-name` 前綴）
- **MCP** — 啟動 / 停止 MCP 伺服器
- **Memory** — 編輯工作記憶 key-value
- **Schedule** — 設定 Cron 排程提示
- **Soul** — 跨 session 人格（靈魂）設定

---

## 進階設定

### API Key 命令（Vault）

若使用密碼管理工具，可在設定的「API Key 命令」欄位填入取得 key 的指令：

```
# 1Password CLI
op read "op://Private/Anthropic/credential"

# pass
pass show anthropic/api-key

# 任意 shell 指令
cat ~/.anthropic_key
```

後端會執行此指令並把輸出設為 `ANTHROPIC_API_KEY` 環境變數。

### 切換 Claude 模型

設定介面可選擇模型（sonnet / opus / haiku）和 effort 等級。

### 自動更新

透過系統匣圖示右鍵選單的「檢查更新」觸發。需先在 `package.json` 設定 GitHub 倉庫：

```json
"publish": {
  "provider": "github",
  "owner": "你的_GitHub_帳號",
  "repo": "claude-desktop"
}
```

---

## 打包發布

### 前置作業

```bash
# 安裝 PyInstaller
pip install pyinstaller

# 安裝 Electron 工具
npm install   # 在專案根目錄
```

### 一鍵打包（Windows .exe 安裝程式）

```bash
npm run dist
```

這會依序執行：
1. Angular 生產環境建置
2. PyInstaller 編譯後端為 `backend/claude-backend.exe`
3. electron-builder 打包成 NSIS 安裝程式（`dist-electron/` 目錄）

### 僅建置（不打包安裝程式）

```bash
npm run build
```

生成 `dist-electron/win-unpacked/` 可攜式資料夾。

### 修改 GitHub Release 設定

編輯 `package.json`，將 `YOUR_GITHUB_USERNAME` 替換成你的帳號：

```json
"publish": {
  "provider": "github",
  "owner": "Chester930",
  "repo": "claude-desktop"
}
```

---

## 疑難排解

### 後端無法啟動

```
後端啟動逾時，請重新啟動應用程式。
```

**檢查：**

```bash
# 確認 port 8765 是否被佔用
netstat -ano | findstr :8765

# 手動啟動後端確認錯誤訊息
cd backend
python main.py
```

### Claude Code 未偵測到

Electron 視窗顯示「需要先安裝 Claude Code」。

```bash
# 確認 claude 在 PATH 中
where claude

# 若輸出空白，重新安裝
npm install -g @anthropic-ai/claude-code
claude login
```

### Sessions / 記憶讀取錯誤

症狀：對話歷史空白，或 Memory 頁籤無資料。

**原因：** 專案目錄未設定或設定錯誤。

**修正：**
1. 在設定中確認「Claude Code 專案目錄」的路徑正確
2. 確認 `~/.claude/projects/<編碼>/` 目錄存在：

```bash
# Windows
dir %USERPROFILE%\.claude\projects\

# 應看到以你的專案路徑編碼命名的子目錄
# 例如：C--Users-你的名字-Desktop-claude-desktop
```

### Python 套件缺失（原始碼安裝）

```bash
pip install -r backend/requirements.txt
```

若仍出錯：

```bash
pip install aiohttp aiohttp-cors croniter
```

### Node.js 版本過舊

Angular 19 需要 Node.js ≥ 22.22.3。

```bash
nvm install 22.22.3
nvm use 22.22.3
node --version  # 確認 v22.22.3
cd frontend && npm install && npm run build
```

### E2E 測試

```bash
cd frontend

# 首次需下載瀏覽器
npx playwright install chromium

# 執行測試（需後端在 :8765 執行）
npm run e2e

# 互動模式
npm run e2e:ui
```

---

## LLM 輔助安裝腳本

> 這一節提供給 AI 助理（如 Claude Code、GitHub Copilot、ChatGPT）  
> 輔助使用者完成安裝與設定時參考。

### 給 LLM 的指令模板

當使用者請你幫忙安裝此專案時，請依以下流程引導：

```
【步驟 0】確認環境資訊
請使用者提供：
- 作業系統版本
- 已安裝的 Claude Code 版本（執行 `claude --version`）
- 專案路徑（他們把 claude-desktop 克隆到哪裡）
- 是否已安裝 Python（執行 `python --version`）
- 是否已安裝 Node.js（執行 `node --version`）

【步驟 1】確認 Claude Code 已安裝並登入
執行：claude --version
執行：claude -p "say hi" --output-format json
若未安裝：npm install -g @anthropic-ai/claude-code && claude login

【步驟 2】（僅原始碼安裝）安裝後端相依
cd <專案路徑>/backend
pip install -r requirements.txt
python -c "import aiohttp, aiohttp_cors, sqlite3; print('OK')"

【步驟 3】（僅原始碼安裝）安裝前端並建置
cd <專案路徑>/frontend
npm install
npm run build

【步驟 4】啟動應用程式
Windows: <專案路徑>/start.bat
或: cd <專案路徑> && npm start（若全域安裝了 electron）

【步驟 5】設定專案目錄（關鍵）
詢問使用者：「你平常在 Claude Code CLI 裡，主要使用哪個專案目錄？
請提供絕對路徑，例如 C:\Users\你的名字\Desktop\my-project」

然後告訴他：
1. 開啟 Claude 桌面版右上角設定（⚙）
2. 在「Claude Code 專案目錄」欄位輸入上述路徑
3. 點「儲存」

【步驟 6】驗證
打開瀏覽器（或讓 Electron 自己開）到 http://localhost:8765/api/status
應看到 {"claude_bin": "...claude路徑...", ...}
```

### 關鍵路徑說明（給 LLM）

```
~/.claude/                          ← Claude Code 全域設定目錄
~/.claude/claude.json               ← MCP 設定（由 claude 指令管理）
~/.claude/agents/                   ← 自訂 agents（.md 檔）
~/.claude/skills/                   ← 自訂 skills（.md 檔 或子目錄）
~/.claude/sessions/                 ← 對話記錄（.jsonl 格式）
~/.claude/claude-desktop-config.json ← 本應用程式設定（自動建立）
~/.claude/claude-desktop-index.db   ← SQLite session 索引（自動建立）

~/.claude/projects/<slug>/          ← 專案資料目錄
    memory/                         ← 工作記憶
    schedules.json                  ← 排程
    soul.md / souls/                ← 靈魂設定

slug 計算規則：路徑中的 ':' 和 '\' 都換成 '-'
例：C:\Users\foo\proj → C--Users-foo-proj
```

### 常見問題快速診斷（給 LLM）

```
問題：應用程式顯示「需要先安裝 Claude Code」
→ 執行 where claude，確認輸出非空
→ 若空：npm install -g @anthropic-ai/claude-code

問題：Sessions 列表空白
→ 確認設定中的「Claude Code 專案目錄」是否填寫正確
→ 或確認 ~/.claude/sessions/ 目錄是否有 .jsonl 檔案

問題：後端啟動失敗（原始碼安裝）
→ cd backend && python main.py 查看錯誤訊息
→ 最常見：pip install -r requirements.txt 未執行

問題：前端建置失敗
→ node --version 確認 ≥ v22.22.3
→ nvm use 22.22.3 切換版本後重試
```

---

## 開發架構

```
claude-desktop/
├── electron/
│   ├── main.js          # Electron 主程序：後端生命週期、視窗管理、自動更新
│   └── preload.js       # IPC bridge（contextIsolation=true）
├── frontend/            # Angular 19 SPA
│   ├── src/app/
│   │   ├── app.ts       # 主元件：所有 signals、對話邏輯
│   │   ├── app.html     # 版型：側欄、多面板、設定 modal
│   │   ├── app.scss     # 深色主題樣式
│   │   ├── claude.service.ts   # HTTP client → backend API
│   │   └── settings.service.ts # localStorage 設定
│   └── e2e/             # Playwright 測試
├── backend/
│   └── main.py          # Python aiohttp：SSE 串流、SQLite 索引、所有 API
├── start.bat            # Windows 啟動腳本（--dev 旗標支援 HMR）
└── package.json         # electron-builder 打包設定
```

**後端 API 端點（port 8765）：**

| 方法 | 路徑 | 說明 |
|------|------|------|
| `POST` | `/api/chat` | SSE 串流對話 |
| `GET` | `/api/sessions` | 列出 / 搜尋 sessions（SQLite FTS5） |
| `GET` | `/api/stats` | Dashboard 統計（SQL aggregate） |
| `GET` | `/api/agents` | 列出 agents（~/.claude/agents/*.md） |
| `GET` | `/api/skills` | 列出 skills（.md 檔 + 子目錄） |
| `POST` | `/api/skills/generate` | AI 自動生成 skill 草稿 |
| `GET/PUT` | `/api/config` | 讀寫 claude-desktop-config.json |
| `POST` | `/api/mcp/{name}/{action}` | start / stop / restart MCP |
| `GET/PUT` | `/api/memory` | 工作記憶 |
| `GET/PUT` | `/api/soul` | 靈魂設定 |
| `GET/POST` | `/api/schedules` | Cron 排程 |

---

## License

MIT
