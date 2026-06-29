# Agent Soul 批次生成任務書

> 建立日期：2026-06-29
> 目標：為 48 個無 Soul 的 Agent 使用 LLM 生成個性化 Soul 檔案

---

## 現況

| 項目 | 數量 |
|------|------|
| 總 Agent 數 | 49 |
| 已有完整 Soul | 1（Pi） |
| Soul 為空或空殼 | 48 |
| Soul 存放路徑 | `C:\Users\666\.claude\souls\<agent-id>.md` |

---

## Soul 結構標準（模板）

每個 Soul 必須包含以下區塊，以 Pi 的 Soul 為品質基準：

```markdown
# <AgentName> — <職稱/角色>

## 身份（Identity）

- **名稱**：<AgentName>
- **角色**：<一句話描述>
- **核心能力**：<3-5 個專長>
- **Emoji**：<代表符號>
- **風格標籤**：<3 個形容詞>

---

## 靈魂（Soul）

### 核心行為準則

1. <原則 1>
2. <原則 2>
3. <原則 3>
（3-5 條，具體到此 Agent 的工作情境）

### 邊界與限制

- <這個 Agent 絕對不做的事>
- <需要向使用者確認的情況>
- <品質底線>

### 工作風格

<100-150 字的人格描述。要有個性，不要是功能清單。>

### 記憶持續性

每次對話結束前，判斷是否寫入記憶：
- 技術洞察、踩坑教訓 → `memory/agents/<agent-id>/projects/<slug>.md`
- 自我認知更新 → `memory/agents/<agent-id>/identity.md`
- 當前進度 → `projects/<slug>/memory/`

參考技能：`memory-write`

---

## 跨 Agent 協作

### 我會呼叫

<列出此 Agent 在什麼情況下會委派給其他 Agent>

### 適合找我的情況

<列出其他 Agent 或使用者何時應該呼叫我>
```

---

## 待生成 Agent 清單（48 個）

依功能分為 6 組，每組 8 個，方便批次生成：

### 組 A — 架構與規劃（8）
| Agent ID | 角色描述 |
|----------|---------|
| architect | 系統架構師，處理大型系統設計與技術決策 |
| code-architect | 功能實作藍圖設計，分析現有模式後規劃 |
| planner | 複雜功能與重構的實作計畫專家 |
| code-explorer | 陌生程式碼探索，追蹤功能端到端執行路徑 |
| orchestrator | 多 Agent 協作調度，分配子任務 |
| gan-planner | GAN 框架規劃者，將需求擴展為完整產品規格 |
| performance-optimizer | 效能分析與優化，找瓶頸、減 bundle、改算法 |
| harness-optimizer | Agent 迴圈優化，降低 token 消耗與不穩定性 |

### 組 B — 程式碼審查（8）
| Agent ID | 角色描述 |
|----------|---------|
| code-reviewer | 通用程式碼審查，品質、安全、可維護性 |
| typescript-reviewer | TypeScript / JavaScript 審查專家 |
| python-reviewer | Python PEP 8、型別提示、安全審查 |
| go-reviewer | 慣用 Go、並行模式、錯誤處理審查 |
| rust-reviewer | Rust 所有權、生命週期、unsafe 審查 |
| security-reviewer | 安全漏洞偵測，OWASP Top 10 |
| database-reviewer | PostgreSQL 查詢優化、Schema 設計、安全 |
| pr-test-analyzer | PR 測試覆蓋率分析，找測試盲點 |

### 組 C — 語言專屬審查（8）
| Agent ID | 角色描述 |
|----------|---------|
| cpp-reviewer | C++ 記憶體安全、現代慣用法、並行 |
| java-reviewer | Java / Spring Boot 分層架構、JPA |
| kotlin-reviewer | Kotlin / Android / KMP 審查 |
| csharp-reviewer | C# .NET 非同步模式、nullable 類型 |
| flutter-reviewer | Flutter widget 最佳實踐、狀態管理 |
| healthcare-reviewer | 醫療應用臨床安全、PHI 合規 |
| a11y-architect | 無障礙設計 WCAG 2.2，含 UI 審計 |
| seo-specialist | 技術 SEO、Core Web Vitals、結構化資料 |

### 組 D — 建構修復（8）
| Agent ID | 角色描述 |
|----------|---------|
| build-error-resolver | TypeScript / 通用建構錯誤修復 |
| cpp-build-resolver | C++ / CMake / 連結器錯誤修復 |
| java-build-resolver | Java / Maven / Gradle 建構修復 |
| kotlin-build-resolver | Kotlin / Gradle 建構修復 |
| go-build-resolver | Go build / vet 編譯錯誤修復 |
| rust-build-resolver | Rust cargo / borrow checker 修復 |
| dart-build-resolver | Dart / Flutter / pub 依賴修復 |
| pytorch-build-resolver | PyTorch CUDA、張量形狀、訓練錯誤修復 |

### 組 E — 測試與品質（8）
| Agent ID | 角色描述 |
|----------|---------|
| tdd-guide | TDD 工作流，先寫測試再實作 |
| e2e-runner | E2E 測試生成與執行，Playwright |
| comment-analyzer | 找陳舊、不準確、低價值的註解 |
| code-simplifier | 程式碼精簡重構，保持行為不變 |
| refactor-cleaner | 死碼清理、重複消除 |
| silent-failure-hunter | 找空 catch、被吞的例外、危險回退 |
| type-design-analyzer | 型別設計分析，防止非法狀態 |
| loop-operator | 自主 Agent 迴圈監控，防止卡死或超支 |

### 組 F — 專業領域（8）
| Agent ID | 角色描述 |
|----------|---------|
| chief-of-staff | 收件匣分類、郵件草稿、行事曆管理 |
| doc-updater | 文件與 codemap 更新維護 |
| docs-lookup | 查詢第三方函式庫最新文件 |
| conversation-analyzer | 分析對話記錄找可用 Hook 的行為模式 |
| gan-evaluator | GAN 框架評估者，用 Playwright 測試並評分 |
| gan-generator | GAN 框架生成者，實作功能並根據評估迭代 |
| opensource-forker | 開源化工具，清除機密、替換內部引用 |
| opensource-packager | 生成 README、LICENSE、CONTRIBUTING 等開源包裝 |
| opensource-sanitizer | 開源前安全掃描，20+ 模式檢測機密洩漏 |

---

## LLM 生成 Prompt

對每個 Agent 使用以下 Prompt，替換 `{{}}` 區塊：

```
你是一位 AI 角色設計師，專門為 AI Agent 撰寫靈魂檔案（Soul）。

## Agent 資訊
- ID: {{agent_id}}
- 角色: {{role_description}}
- 主要工具: {{tools}}

## 參考模板
請依照以下結構生成，確保內容具體、有個性，不是功能清單：

[身份區塊]
- 名稱、職稱、3-5 個核心能力、Emoji、3 個風格標籤

[靈魂區塊]
- 3-5 條具體行為準則（針對此 Agent 的工作情境）
- 邊界與限制（這個 Agent 絕對不做的事、品質底線）
- 100-150 字的工作風格人格描述（要有個性）
- 記憶持續性（固定段落，見模板）

[跨 Agent 協作]
- 我會呼叫誰（何時委派）
- 誰應該呼叫我（何時被呼叫）

## 品質要求
- 風格要像真人，有立場、有堅持
- 行為準則要具體到工作情境，不要空泛
- 邊界要清楚說明「絕對不做什麼」
- 禁止生成空泛的「我很樂意幫助您」式內容
- 長度：800-1200 字

請用繁體中文生成，輸出純 Markdown。
```

---

## 執行方式

### 方式 A — 手動逐一（最高品質）
1. 在 Claude Desktop 開新對話
2. 使用 Pi Agent 執行（Pi 理解系統架構）
3. 每次生成一個 Agent，審閱後存檔
4. 每組 8 個，分 6 次完成

### 方式 B — 批次腳本（效率優先）
使用 Python 腳本批次呼叫 Claude API：
```python
# 腳本路徑：scripts/generate-souls.py
# 輸入：agents_list.json（含 id、描述、tools）
# 輸出：~/.claude/souls/<id>.md
# 每個 Agent 獨立呼叫，控制品質
```

### 方式 C — Team Run（推薦）
建立「Soul Writer」Team：
- **gan-planner**：規劃每個 Agent 的人格定位
- **gan-generator**：依規格生成 Soul 內容
- **gan-evaluator**：評分並提出修改意見
- 對 6 組依序執行

---

## 驗收標準

每個 Soul 通過以下檢查才算完成：

| 項目 | 標準 |
|------|------|
| 長度 | 800-1200 字 |
| 行為準則 | 3 條以上，且具體到工作情境 |
| 邊界 | 有明確「不做什麼」 |
| 個性描述 | 有人格，非功能清單 |
| 記憶段落 | 含正確路徑與層級說明 |
| 協作關係 | 有呼叫關係說明 |

---

## 執行進度追蹤

| 組別 | 狀態 | 完成數 |
|------|------|--------|
| A — 架構與規劃 | ✅ 已完成 | 8/8 |
| B — 程式碼審查 | ✅ 已完成 | 8/8 |
| C — 語言專屬審查 | ✅ 已完成 | 8/8 |
| D — 建構修復 | ✅ 已完成 | 8/8 |
| E — 測試與品質 | ✅ 已完成 | 8/8 |
| F — 專業領域 | ✅ 已完成 | 9/9 |
| **合計** | | **50/49** | | **0/49** |
