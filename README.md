# Claude 桌面版

> Claude Code 的圖形介面 —— 在 Electron 視窗裡使用 Claude，保留完整 CLI 能力。

![Platform](https://img.shields.io/badge/platform-Windows-blue)
![Electron](https://img.shields.io/badge/Electron-42-47848F)
![Angular](https://img.shields.io/badge/Angular-22-DD0031)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED)

---

## 快速選擇

| 我是... | 推薦方式 |
|---------|---------|
| 想直接使用這個應用程式 | [一般使用者模式（Docker）](#一般使用者模式docker) |
| 想修改 / 開發這個專案 | [開發者模式（原始碼）](#開發者模式原始碼) |
| 想下載安裝包直接安裝 | [安裝包方式](#安裝包方式) |

---

## 目錄

1. [這是什麼](#這是什麼)
2. [一般使用者模式（Docker）](#一般使用者模式docker)
3. [開發者模式（原始碼）](#開發者模式原始碼)
4. [安裝包方式](#安裝包方式)
5. [首次啟動設定](#首次啟動設定)
6. [功能一覽](#功能一覽)
7. [進階設定](#進階設定)
8. [疑難排解](#疑難排解)

---

## 這是什麼

Claude 桌面版是一個桌面 GUI，讓你能以視窗化介面使用 Claude Code CLI。  
底層仍呼叫你本機已安裝的 `claude` 指令，所有對話、記憶、agents、skills、MCP 設定都從你的 `~/.claude/` 目錄讀取。

**主要特性：**

| 功能 | 說明 |
|------|------|
| 多面板對話 | 最多 4 個平行對話視窗 |
| Session 歷史 | SQLite 索引 + FTS5 全文搜尋 |
| Agents / Skills / MCP | 直接從右側面板選取 |
| Dashboard | Token 用量、熱力圖 |
| 排程 | Cron 定時觸發，支援 LINE 推送 |
| LINE Bot | 透過 LINE 官方帳號與 AI 對話 |
| 靈魂（Soul） | 跨 session 人格設定 |
| 系統匣 | 最小化到系統匣，自動更新 |

---

## 一般使用者模式（Docker）

> **不需要** Python 或 Node.js 開發環境，只需要 Docker Desktop 和 Claude CLI。

### 必要條件

**1. Docker Desktop**

下載並安裝：https://www.docker.com/products/docker-desktop/

安裝完成後確認 Docker 正在執行（系統匣有 Docker 圖示）。

**2. Claude Code CLI**

```bash
npm install -g @anthropic-ai/claude-code
claude login
```

### 步驟

**1. 取得專案**

```bash
git clone https://github.com/Chester930/claude-desktop.git
cd claude-desktop
```

**2. 建立設定檔**

複製範本並填入你的 ngrok Token：

```bash
copy .env.example .env
```

用記事本開啟 `.env`，填入：

```env
NGROK_AUTHTOKEN=你的ngrok_token    # https://dashboard.ngrok.com 免費申請
CLAUDE_HOME=C:/Users/你的名字/.claude
CODEX_HOME=C:/Users/你的名字/.codex    # 選用，要在容器裡用 Codex CLI 引擎才需要
```

**3. 啟動**

```bat
.\start.bat --docker
```

第一次執行會自動 build Docker image（約 3–5 分鐘），之後啟動只需數秒。

**日常指令：**

```bat
# 啟動（Docker + Electron）
.\start.bat --docker

# 停止所有容器
docker compose down

# 有程式碼更新時重新 build
.\start.bat --docker --build
```

### Docker 架構

```
Electron 視窗 ──→ localhost:4200 (nginx / 前端)
              ──→ localhost:8765 (Python 後端)
LINE 用戶    ──→ ngrok 公開網址 ──→ 後端 (LINE Bot)
```

| 容器 | 功能 | Port |
|------|------|------|
| `claude-desktop-backend` | Python API + Claude CLI | 8765 |
| `claude-desktop-frontend` | nginx 靜態前端 | 4200 |
| `claude-desktop-ngrok` | LINE Webhook 公開網址 | 4040 |

### LINE Bot 設定（選用）

若要啟用 LINE 官方帳號對話功能：

1. 到 [LINE Developers Console](https://developers.line.biz) 建立 Messaging API Channel
2. 取得 **Channel Access Token** 和 **Channel Secret**
3. 填入 `~/.claude/claude-desktop-config.json`：

```json
{
  "lineChannelAccessToken": "你的Token",
  "lineChannelSecret": "你的Secret",
  "lineAllowedUsers": ["你的LINE用戶ID"]
}
```

4. 啟動後，終端機會顯示 Webhook URL（例如 `https://xxxx.ngrok-free.app/api/line/webhook`）
5. 將此 URL 填入 LINE Developers Console → Messaging API → Webhook URL

---

## 開發者模式（原始碼）

> 適合想修改程式碼、新增功能的開發者。支援前端熱重載（HMR）。

### 必要條件

| 工具 | 版本 | 說明 |
|------|------|------|
| Node.js | ≥ 22.22.3 | 前端建置 + Electron |
| Python | ≥ 3.10 | 後端 |
| Claude CLI | 最新版 | 核心 AI 能力 |

```bash
# 安裝 Claude CLI
npm install -g @anthropic-ai/claude-code
claude login

# 驗證
node --version    # v22+
python --version  # 3.10+
claude --version
```

### 步驟

**1. 取得並安裝相依**

```bash
git clone https://github.com/Chester930/claude-desktop.git
cd claude-desktop

# 後端
pip install -r backend/requirements.txt

# 前端
cd frontend
npm install
npm run build    # 初次建置
cd ..

# Electron
npm install
```

**2. 啟動（三種模式）**

```bat
# 一般模式（本機後端 + dist 前端）
.\start.bat

# 開發模式（HMR 熱重載，改 Angular 程式碼即時更新）
.\start.bat --dev

# Docker 模式（後端跑在容器，適合測試 LINE Bot）
.\start.bat --docker
```

**3. 開發模式說明**

`start.bat --dev` 同時啟動：
- Python 後端（port 8765）
- Angular dev server（port 4200，HMR 熱重載）
- Electron 視窗

修改 `frontend/src/` 裡的程式碼後，Electron 視窗會自動重整。

### 專案結構

```
claude-desktop/
├── electron/
│   ├── main.js          # Electron 主程序（視窗、後端生命週期）
│   └── preload.js       # IPC bridge
├── frontend/            # Angular 22
│   ├── src/app/
│   │   ├── app.ts       # 主元件、對話邏輯
│   │   ├── app.html     # 版型
│   │   ├── app.scss     # 深色主題
│   │   └── claude.service.ts   # API client
│   └── e2e/             # Playwright 測試
├── backend/
│   ├── main.py          # Python aiohttp（所有 API + LINE Bot + 排程）
│   └── Dockerfile       # Docker image
├── docker-compose.yml   # Docker 服務編排
├── nginx.conf           # 前端靜態服務設定
├── start.bat            # 一鍵啟動腳本
└── .env.example         # 環境變數範本
```

### 後端 API 端點（port 8765）

| 方法 | 路徑 | 說明 |
|------|------|------|
| `POST` | `/api/chat` | SSE 串流對話 |
| `GET` | `/api/sessions` | 列出 / 搜尋 sessions |
| `GET` | `/api/stats` | Dashboard 統計 |
| `GET` | `/api/agents` | 列出 agents |
| `GET` | `/api/skills` | 列出 skills |
| `GET/PUT` | `/api/config` | 讀寫設定 |
| `GET/POST` | `/api/schedules` | Cron 排程 |
| `POST` | `/api/line/webhook` | LINE Bot Webhook |
| `GET/PUT` | `/api/telegram` | Telegram Bot 設定 |

### 前端建置指令

```bash
cd frontend

npm run build    # 生產建置（輸出 dist/）
npm run start    # 開發 server（HMR，port 4200）
npm run e2e      # Playwright E2E 測試
```

### 前端更新到 Docker 容器

```bash
cd frontend && npm run build
docker compose restart frontend
```

---

## 安裝包方式

> 最簡單，不需要任何開發工具，但功能受限於發布版本。

1. 前往 [Releases](../../releases/latest) 下載最新的 `Claude-桌面版-Setup-x.x.x.exe`
2. 執行安裝程式
3. 安裝 Claude CLI：`npm install -g @anthropic-ai/claude-code && claude login`
4. 啟動「Claude 桌面版」

---

## 首次啟動設定

### 設定 Claude Code 專案目錄

1. 點右上角齒輪圖示（⚙）開啟設定
2. 找到「Claude Code 專案目錄」欄位
3. 貼上你平常使用的專案根目錄絕對路徑
4. 點「儲存」

> 這個目錄決定記憶、排程、靈魂等資料的儲存位置。

---

## 功能一覽

### 鍵盤快捷鍵

| 快捷鍵 | 功能 |
|--------|------|
| `Ctrl+N` | 新增對話分頁 |
| `Ctrl+B` | 切換左側欄 |
| `Ctrl+K` | 開啟指令面板 |
| `/` | 觸發 skill 選單 |

### 內建 `/` 指令

| 指令 | 功能 |
|------|------|
| `/new` | 開始新對話 |
| `/clear` | 清除目前訊息 |
| `/review` | Code Review |
| `/plan` | 規劃實作步驟 |
| `/tdd` | TDD 流程 |

---

## 進階設定

### API Key 命令（Vault）

```json
// ~/.claude/claude-desktop-config.json
{
  "apiKeyCmd": "op read \"op://Private/Anthropic/credential\""
}
```

支援 1Password CLI、pass 等任意 shell 指令。

---

## 疑難排解

### Docker 後端未回應

```bash
# 確認容器狀態
docker compose ps

# 查看後端 log
docker compose logs backend

# 重新啟動
docker compose restart backend
```

### 後端無法啟動（本機模式）

```bash
# 確認 port 未被佔用
netstat -ano | findstr :8765

# 手動啟動查看錯誤
cd backend && python main.py
```

### Claude Code 未偵測到

```bash
where claude
# 若無輸出：
npm install -g @anthropic-ai/claude-code && claude login
```

### Sessions 列表空白

→ 設定中確認「Claude Code 專案目錄」路徑正確

### Node.js 版本過舊（開發模式）

```bash
nvm install 22.22.3
nvm use 22.22.3
cd frontend && npm install && npm run build
```

---

## License

MIT
