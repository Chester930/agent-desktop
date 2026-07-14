# ADR-003：中立 Agent／Skill Registry（雙引擎皆為渲染目標）

## 狀態

已採用（2026-07-14），延伸並取代 [ADR-002](./ADR-002-claude-codex-resource-sync.md) 的部分決策。

## 背景

ADR-002 把 `claudeHome`（預設 `~/.claude`）當成 Agent／Skill 的單一來源，
Codex 端則是由 resource sync 產生的 TOML／目錄副本。這個設計隱含一個假設：
「使用者一定有裝 Claude Code」。實際上 Agent Desktop 的使用者可能：

- 只用 Claude Code；
- 只用 Codex（`engineMode: "codex"`）；
- 兩者都用。

對只用 Codex 的使用者來說，把資料放在一個叫 `.claude` 的資料夾底下，且新建
/編輯 Agent、Skill 之後要記得手動按「同步到 Codex」才會生效，這兩件事都在
暗示 Claude 才是「正牌」引擎、Codex 是次要的——但這與「多引擎並存」的產品
定位不一致。

## 決策

1. 新增 `registryHome` 設定鍵（`backend/database.py::_resolve_registry_home`），
   獨立於 `claudeHome`。**預設值等於 `claudeHome`**，所以既有安裝零成本、
   零遷移——`REGISTRY_HOME == CLAUDE_HOME` 時，下面第 2 點的鏡像渲染會直接
   判定為 no-op（來源與目標路徑相同，Claude Code 本來就直接讀同一份檔案）。
2. `ResourceSyncService` 新增可選的 `claude_native_home` 參數。當它與
   `registry_home` 不同路徑時，Claude 也變成一個渲染目標，跟 Codex 對稱：
   registry 的 Agent／Skill 會被鏡像（Markdown 逐字複製 + 管理標記）到真正
   的 `~/.claude/agents`／`skills`，回傳於 `status()`/`sync()` 新增的
   `claude_mirror` 區塊（純新增欄位，不影響既有的 `agents`/`skills` 兩個
   欄位，向後相容）。
3. 標記必須放在 YAML frontmatter **裡面**（`---` 之後），不能像 Codex 的
   TOML 副本一樣直接放在檔案最前面——Claude Code 自己的 frontmatter 解析器
   要求檔案以 `---` 開頭，標記放在前面會讓鏡像出來的 Agent 對 Claude Code
   來說形同不存在（檔案存在，但解析不出 frontmatter）。這是實作過程中發現
   並修正的一個真實 bug，不是假設性風險。
4. 新增 `import_native()`：把 codex_only／claude_only（引擎原生已有、
   registry 裡還沒有）的資源轉換後寫回 registry，解決「Codex-only 使用者
   的原生資源」與「既有使用者手動在 Codex 端建立的 Agent」兩種情境——原本
   這些只能永遠卡在 conflict，現在可以一次性「匯入」成單一來源的一部分。
   帶有 Agent Desktop 自己管理標記的孤兒副本（來源已從 registry 刪除）不會
   被匯入，避免復活使用者已經主動刪除的內容。
5. Agent／Skill 的 CRUD（`routes/agents.py`）改讀寫 `REGISTRY_AGENTS_DIR`／
   `REGISTRY_SKILLS_DIR`，並在每次建立／更新後自動觸發一次 `sync()`——不再
   要求使用者手動按「同步到 Codex」按鈕才會讓 Codex（或已解耦的 Claude 鏡像）
   看到變更。失敗只記錄、不影響存檔本身；側邊欄的手動「檢查／同步」按鈕仍
   保留，作為重試與衝突排除的入口。
6. 沿用 ADR-002 的核心不變量：任何目標檔案/目錄只要存在但沒有 Agent Desktop
   的管理標記，一律視為使用者自有內容，同步／匯入都不會覆蓋，只會回報為
   conflict 或略過。
7. `registryHome` 額外支援 `REGISTRY_HOME` 環境變數覆寫，優先於設定檔——
   容器部署（`docker-compose.yml`）沒有桌面版設定頁可以填，這跟
   `routes/resource_sync.py::_service()` 讀取 `CODEX_RESOURCE_HOME`／
   `CODEX_SKILLS_HOME` 環境變數是同一套模式。`docker-compose.yml` 的
   `backend`／`backend-dev` 兩個 service 都新增了對應的 bind mount
   （`${REGISTRY_HOME:-${CLAUDE_HOME}}` → `/mnt/host-registry`），預設沿用
   `CLAUDE_HOME`，只有 `.env` 裡明確填了 `REGISTRY_HOME` 才會真的解耦。

## 結果

### 優點

- 「單一來源」不再隱含「使用者必須裝 Claude Code」；Codex-only 使用者的
  心智模型不再被 Claude 中心化的措辭誤導。
- 既有安裝完全零成本：`registryHome` 預設等於 `claudeHome`，`claude_mirror`
  只有在使用者主動解耦時才會出現。
- Codex-only／既有使用者的原生資源可以透過 `import_native()` 一次性納入
  單一來源，不再永遠卡在 conflict。
- CRUD 後自動渲染，消除「忘記手動同步」造成的引擎間資料落差。

### 代價

- 一旦解耦（`registryHome != claudeHome`），Claude Code 就從「直接讀同一份
  檔案（零複製）」變成「讀一份自動產生的鏡像副本」——多了一層間接，也多了
  一個可能出現 conflict 的地方（例如使用者直接編輯了 `~/.claude/agents`
  底下的鏡像檔案）。
- `import_native()` 是單次性的「採納」，不是持續雙向同步；匯入後的內容
  仍然由 registry 單向渲染回引擎，不會反過來持續追蹤引擎端後續的手動修改。
- CRUD 自動觸發 sync 略微增加每次存檔的延遲（一次檔案系統掃描 + 寫入），
  在資源數量非常多時可能需要之後改成只同步「這次變動的單一項目」而非全量
  掃描，目前先用全量 `sync()` 換取實作簡單、行為與手動按鈕完全一致。

## 未採用方案

- **重新命名 `claudeHome` 本身**：會是破壞性設定變更，且大部分使用者其實
  就是想要 `registryHome == claudeHome` 的預設行為；改用新增獨立設定鍵、
  預設繼承舊值，向後相容成本最低。
- **CRUD 直接寫入所有已啟用引擎的原生格式**（不經過中立 registry）：等於
  每個引擎都要各自成為「有時是來源、有時是目標」，衝突偵測邏輯會變成
  N 對 N，比「單一來源 + 各引擎皆為渲染目標」複雜很多，且失去單一事實
  來源的可稽核性。
- **`import_native()` 順便自動刪除來源端的原生檔案**：匯入後原檔案還留在
  原地（下次 `sync()` 會因為內容相同而判定已同步、不會重複處理），刻意不
  做刪除——跟 ADR-002 一樣的理由：破壞性操作應該由使用者主動觸發，不該是
  匯入的隱藏副作用。
