import { test, expect } from '@playwright/test';

// 2026-07-20 健檢：輸入欄狀態列的權限模式／模型／思考深度三個控制項過去
// 只認 Claude 的詞彙——engines/codex_engine.py::_normalize_sandbox_mode()
// 收到 Claude 的權限模式字串（例如 "bypassPermissions"）會直接靜默忽略、
// 退回 Codex 自己的預設值，模型別名（opus/haiku/fable）原封不動傳給
// `codex --model` 會直接被判定成不存在的模型而降級，思考深度對 Codex
// 來說整個是裝飾品。修復後改成新增一顆「執行引擎」pill，依目前引擎切換
// 底下三個控制項的可見選項/行為。這裡驗證切換 pill 後，UI 真的會跟著換。

const MOCK_CODEX_MODELS = [
  { slug: 'gpt-5.6-sol', display_name: 'GPT-5.6-Sol', description: 'Flagship.' },
  { slug: 'gpt-5.4-mini', display_name: 'GPT-5.4-Mini', description: 'Fast.' },
];

test.describe('引擎感知的輸入欄控制項', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('claude_onboarding_done', '1');
    });
    // GET /api/codex/models 在真實環境會 shell 出去問已安裝的 Codex CLI
    // （`codex debug models --bundled`）——CI runner 沒有裝 codex CLI，
    // 呼叫會失敗，清單永遠是空的，模型按鈕永遠卡在「使用預設」出不來。
    // 這裡跟其他 e2e 測試一樣直接 mock 掉這個端點，不依賴環境裝了什麼
    // CLI、也不用等真的 spawn 一次 subprocess，兩個測試檔通用。
    await page.route('**/api/codex/models', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_CODEX_MODELS),
      });
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
  // 模型按鈕真的可以點擊切換到清單裡的其他模型，不會一直卡在「使用預設」
  // （用上面 mock 的固定清單，不依賴真實 CLI）。
  test('Codex 對話下，模型按鈕可以切換到清單裡的模型', async ({ page }) => {
    await page.goto('/');

    const engineBtn = page.locator('.engine-btn');
    await expect(engineBtn).toBeVisible({ timeout: 10000 });

    // 確保切到 Codex（不管一開始是哪個）
    if (!(await engineBtn.textContent())?.includes('Codex')) {
      await engineBtn.click();
    }
    await expect(engineBtn).toContainText('Codex');

    const modelBtn = page.locator('.input-statusbar button', { hasText: /使用預設|GPT/ });
    await expect(modelBtn).toHaveText('使用預設');

    await modelBtn.click();
    await expect(modelBtn).toHaveText('GPT-5.6-Sol');

    await modelBtn.click();
    await expect(modelBtn).toHaveText('GPT-5.4-Mini');

    // 循環到清單尾端後應該繞回「使用預設」
    await modelBtn.click();
    await expect(modelBtn).toHaveText('使用預設');
  });
});
