import { test, expect } from '@playwright/test';

// 2026-07-20 健檢：輸入欄狀態列的權限模式／模型／思考深度三個控制項過去
// 只認 Claude 的詞彙——engines/codex_engine.py::_normalize_sandbox_mode()
// 收到 Claude 的權限模式字串（例如 "bypassPermissions"）會直接靜默忽略、
// 退回 Codex 自己的預設值，模型別名（opus/haiku/fable）原封不動傳給
// `codex --model` 會直接被判定成不存在的模型而降級，思考深度對 Codex
// 來說整個是裝飾品。修復後改成新增一顆「執行引擎」pill，依目前引擎切換
// 底下三個控制項的可見選項/行為。這裡驗證切換 pill 後，UI 真的會跟著換。

test.describe('引擎感知的輸入欄控制項', () => {
  // GET /api/codex/models 第一次呼叫要真的 spawn 一次 `codex debug models`
  // subprocess，後端快取 1 小時——這整個套件平行跑、多個測試檔同時打
  // 同一個 dev 後端時，這個 subprocess 呼叫本身可能被排擠變慢。這裡在
  // 任何 UI 互動之前，先用 API context 直接打一次把快取焐熱，讓實際測試
  // 只需要等瀏覽器那次請求命中快取，不用跟 subprocess 的真實延遲賭時間。
  test.beforeAll(async ({ request }) => {
    await request.get('/api/codex/models', { timeout: 60000 }).catch(() => {});
  });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('claude_onboarding_done', '1');
    });
  });

  test('切換執行引擎 pill 後，權限模式選項與思考深度按鈕會跟著換', async ({ page }) => {
    await page.goto('/');

    const engineBtn = page.locator('.engine-btn');
    await expect(engineBtn).toBeVisible({ timeout: 10000 });

    // 開發環境預設沒有鎖定執行引擎範圍（database.get_engine_mode() 預設
    // 'both'），pill 應該可以點擊切換，不會是唯讀的 .locked 狀態。
    await expect(engineBtn).not.toHaveClass(/locked/);

    const permBtn = page.locator('.input-statusbar .sb-btn').nth(1); // pill 之後的下一顆是權限模式
    const effortBtnVisible = () => page.locator('.input-statusbar button[title*="思考深度"]');

    const initialEngineText = await engineBtn.textContent();
    const initialPermText = await permBtn.textContent();

    await engineBtn.click();

    await expect(engineBtn).not.toHaveText(initialEngineText?.trim() ?? '');
    // 權限模式的顯示文字應該跟著換了一套詞彙（不會還是切換前那個值）
    await expect(permBtn).not.toHaveText(initialPermText?.trim() ?? '');

    const nowOnCodex = (await engineBtn.textContent())?.includes('Codex');
    if (nowOnCodex) {
      // Codex 沒有對應的思考深度參數，控制項應該被隱藏
      await expect(effortBtnVisible()).toHaveCount(0);
      await expect(permBtn).toHaveText(/Workspace Write|Read Only|Full Access/);
    } else {
      await expect(effortBtnVisible()).toHaveCount(1);
      await expect(permBtn).toHaveText(/Default|Accept edits|Plan|Bypass|Auto/);
    }

    // 切回去應該要回到原本那套
    await engineBtn.click();
    await expect(engineBtn).toHaveText(initialEngineText?.trim() ?? '');
  });

  // 2026-07-20 續篇：模型控制過去在 Codex 時完全鎖死只能「使用預設」——
  // 改成即時問已安裝的 Codex CLI（GET /api/codex/models → `codex debug
  // models --bundled`），不是寫死在前端的清單。這裡驗證在 Codex 對話下，
  // 模型按鈕真的可以點擊切換到清單裡的其他模型，不會一直卡在「使用預設」。
  test('Codex 對話下，模型按鈕可以切換到即時查詢到的模型清單', async ({ page }) => {
    // 這個測試依賴真的問過一次已安裝的 Codex CLI（`codex debug models`，
    // 見 GET /api/codex/models），不是純記憶體操作——跟整個 e2e 套件平行
    // 跑、共用同一個 dev 後端時，這個 subprocess 呼叫可能因為資源競爭
    // 變慢，預設的 30s 測試逾時不夠寬裕，這裡單獨拉長。
    test.setTimeout(60000);
    await page.goto('/');

    const engineBtn = page.locator('.engine-btn');
    await expect(engineBtn).toBeVisible({ timeout: 10000 });

    // 確保切到 Codex（不管一開始是哪個）
    if (!(await engineBtn.textContent())?.includes('Codex')) {
      await engineBtn.click();
    }
    await expect(engineBtn).toContainText('Codex');

    const modelBtn = page.locator('.input-statusbar button', { hasText: /使用預設|GPT/ });
    await expect(modelBtn).toBeVisible();

    // GET /api/codex/models 是 ngOnInit 觸發的非阻塞背景請求，不保證這時
    // 已經回來——用 poll 反覆點擊直到文字真的變成「使用預設」以外的值
    // （清單還沒載完時 cycleModel() 是無害的 no-op，載完後下一次點擊就會
    // 真的换到清單裡的模型），比賭一次網路請求的時序穩定。
    await expect
      .poll(async () => {
        await modelBtn.click();
        return (await modelBtn.textContent())?.trim();
      }, { timeout: 45000, intervals: [300] })
      .not.toBe('使用預設');

    await expect(modelBtn).toHaveText(/GPT/);
  });
});
