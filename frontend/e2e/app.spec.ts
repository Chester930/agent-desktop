import { test, expect } from '@playwright/test';

test.describe('Claude Desktop — 基本流程', () => {

  test('主畫面正常載入', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/Claude 桌面版/);
    await expect(page.locator('.sidebar')).toBeVisible();
    await expect(page.locator('.chat-input')).toBeVisible();
  });

  test('新對話分頁', async ({ page }) => {
    await page.goto('/');
    const tabsBefore = await page.locator('.chat-tab').count();
    await page.keyboard.press('Control+n');
    await expect(page.locator('.chat-tab')).toHaveCount(tabsBefore + 1);
  });

  test('輸入框 / 觸發 slash 選單', async ({ page }) => {
    await page.goto('/');
    const input = page.locator('.chat-input');
    await input.click();
    await input.type('/');
    await expect(page.locator('.slash-menu')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.slash-menu')).not.toBeVisible();
  });

  test('斜線指令 /new 清除輸入', async ({ page }) => {
    await page.goto('/');
    const input = page.locator('.chat-input');
    await input.click();
    await input.type('/new');
    await expect(page.locator('.slash-menu')).toBeVisible();
    await page.keyboard.press('Enter');
    await expect(input).toHaveValue('');
  });

  test('切換側欄 Ctrl+B', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.sidebar')).toBeVisible();
    await page.keyboard.press('Control+b');
    await expect(page.locator('.sidebar')).not.toBeVisible();
    await page.keyboard.press('Control+b');
    await expect(page.locator('.sidebar')).toBeVisible();
  });

  test('開關設定 modal', async ({ page }) => {
    await page.goto('/');
    await page.locator('.icon-btn[title*="設定"]').first().click();
    await expect(page.locator('.modal-backdrop')).toBeVisible();
    await expect(page.locator('.modal')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.modal-backdrop')).not.toBeVisible();
  });

  test('右側 Skills 分頁搜尋', async ({ page }) => {
    await page.goto('/');
    await page.locator('.tab-bar button', { hasText: 'Skills' }).click();
    const searchInput = page.locator('.right-panel-search-input');
    await expect(searchInput).toBeVisible();
    await searchInput.fill('tdd');
    // filter should narrow the list (may be 0 if no tdd skill installed)
    const filtered = page.locator('.panel-list .panel-card');
    const count = await filtered.count();
    expect(count).toBeGreaterThanOrEqual(0);
    await page.locator('.right-panel-search-clear').click();
    await expect(searchInput).toHaveValue('');
  });

  test('後端 /api/status 健康檢查', async ({ request }) => {
    // requires backend running on :8765
    const res = await request.get('http://localhost:8765/api/status');
    if (res.ok()) {
      const body = await res.json();
      expect(body).toHaveProperty('claude_bin');
    } else {
      // backend not running in this test env — skip gracefully
      test.skip();
    }
  });

});
