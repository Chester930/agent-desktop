# ADR-004：刪除清理、單項同步與衝突解決

## 狀態

已採用（2026-07-14），延伸 [ADR-002](./ADR-002-claude-codex-resource-sync.md)／
[ADR-003](./ADR-003-neutral-agent-skill-registry.md)，不取代其決策。

## 背景

實測 ADR-003 的實作後，發現三個 ADR-003 沒有涵蓋、或明確列為已知代價待改
的落地問題：

1. **刪除不會被同步**：`handle_agent_delete` 只刪 registry 裡的檔案，從不
   呼叫 sync；`sync()` 本身也只處理 create/update，從不清除「來源已消失」
   的目標。結果是刪除一個 Agent 之後，`~/.codex/agents/xxx.toml`（以及解耦
   時的 Claude 鏡像）會永久留在硬碟上，變成孤兒檔案。Skill 更嚴重：後端
   完全沒有註冊 DELETE route，前端也沒有對應 UI，跟 Agent 的 CRUD 不對稱。
2. **全量掃描**：ADR-003「代價」一節已經記錄「CRUD 自動觸發 sync 略微增加
   每次存檔的延遲……可能需要之後改成只同步這次變動的單一項目」——這裡就是
   那個「之後」。
3. **衝突處理體驗**：`status()` 回報衝突名稱，但使用者只能看到名字，看不到
   registry 內容跟引擎端實際內容差在哪，也沒有任何「解決」的操作入口，只能
   自己去外部檔案系統比對、手動編輯。

## 決策

1. **刪除清理**：`ResourceSyncService.sync()` 現在會在處理完所有來源之後，
   額外掃一輪目標——任何「registry 裡已經沒有對應來源、但仍帶著 Agent
   Desktop 管理標記」的目標會被刪除，計入新的 `pruned` 欄位。不帶標記的
   同名目標（使用者自己的原生內容）完全不動，行為跟既有的 conflict 判斷
   一致：從不刪除非受管內容。`handle_agent_delete`／新增的
   `handle_skill_delete` 在刪檔後都會呼叫 `sync_agent(name)`／
   `sync_skill(name)`，讓刪除跟建立/更新走同一條自動同步路徑。
   `status()` 對應新增 `orphaned` 欄位，跟 `codex_only`／`claude_only`
   （引擎原生、可匯入的真實使用者內容）分開回報，避免使用者把「我們自己
   產生、等著被清掉的孤兒副本」誤認成「可以匯入的原生資源」。
2. **單項同步**：新增 `sync_agent(name)`／`sync_skill(name)`，直接用名字
   建構來源/目標路徑，不對整個 registry／引擎目錄做 `iterdir()`／`glob()`
   全量掃描。CRUD 路由（新增、更新、刪除）都改呼叫這兩個方法而非全量
   `sync()`；渲染/衝突判斷邏輯抽成 `_render_agent_to_codex()`／
   `_render_agent_to_claude_mirror()`／`_render_skill()`／
   `_prune_agent_target()`／`_prune_skill_target()` 等共用 helper，讓全量
   `sync()`跟單項方法呼叫同一套邏輯，避免兩份實作分岔導致行為不一致（見
   `tests/test_resource_sync.py::test_sync_agent_single_item_matches_full_sync_across_many_items`）。
   側邊欄手動「同步」按鈕仍然呼叫全量 `sync()`——單項同步只用在 CRUD
   auto-sync 這一條路徑，手動整體檢查/同步不需要也不應該省略掉其他項目。
3. **衝突預覽 + 顯式單一目標覆蓋**：新增 `conflict_preview(kind, name)`
   回傳 registry／Codex／Claude 鏡像三邊的實際內容（Skill 只讀入口檔
   SKILL.md／README.md，不做整棵樹的 diff），以及
   `resolve_conflict(kind, name, target_engine)`——只針對「呼叫者明確指定
   的這一個名字、這一個引擎目標」略過管理標記檢查、強制覆蓋。這是整個
   服務裡唯一會覆蓋非受管內容的路徑，而且只能由使用者在前端點擊「覆蓋」
   後觸發，自動同步（`sync()`／`sync_agent()`／`sync_skill()`）永遠不會
   呼叫它。前端在衝突清單新增「查看」入口，開啟 Modal 顯示三邊內容並提供
   對應的覆蓋按鈕。

## 結果

### 優點

- 刪除 Agent／Skill 不再留下永久孤兒檔案；Codex／Claude 端看到的資源
  列表跟 registry 保持一致，不會出現「明明已經刪除但 Codex 還看得到」的
  落差。
- Skill 補齊了跟 Agent 對稱的刪除能力（後端 route + 前端 UI）。
- CRUD 存檔的效能不再跟整個 registry／引擎目錄的資源總數成正比，只跟
  「這一個名字」有關。
- 衝突不再是死路——使用者可以先看內容再決定覆蓋哪一邊，或者什麼都不做
  繼續保留現狀。

### 代價

- `sync()` 現在多了一輪目標端掃描（用於偵測孤兒），全量同步的成本略微
  增加；但這輪掃描本來就要做（原本用於計算 `codex_only`／`claude_only`），
  只是額外做了一次刪除判斷，量級沒有變化。
- `resolve_conflict()` 是這個服務裡唯一允許覆蓋非受管內容的路徑，等於在
  「絕不覆蓋使用者內容」這條核心不變量上開了一個唯一、顯式、使用者觸發
  的例外——只接受這個代價是因為衝突原本就是「使用者需要出手」的情況，
  沒有這個入口只是把選擇權留在檔案系統層面，不會更安全。
- 新增的 `orphaned` 狀態欄位是 API 形狀的擴充（原有 `codex_only`／
  `claude_only` 語意變窄，不再包含孤兒副本）；前端型別跟著更新，但如果有
  外部程式直接呼叫 `/api/resource-sync` 並依賴舊語意，需要注意這個變化。

## 未採用方案

- **CRUD 刪除時順便清空所有其他孤兒**：只清「這一個名字」的孤兒，不做
  全域孤兒清理——跟 ADR-002／ADR-003 一致的理由，刪除是明確、局部的操作，
  不該有「順便處理其他不相關項目」的隱藏副作用；全域孤兒清理仍然可以透過
  側邊欄的手動「同步」（全量 `sync()`）達成。
- **`resolve_conflict()` 支援雙邊同時覆蓋**：故意限制成一次只能指定一個
  `target_engine`——衝突通常只發生在其中一邊，讓使用者一次動一個目標，
  避免「以為只改了 Codex，結果 Claude 鏡像也被覆蓋」的意外行為。
