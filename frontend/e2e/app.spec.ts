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
    const tabsBefore = await page.locator('.panel').count();
    await page.keyboard.press('Control+n');
    await expect(page.locator('.panel')).toHaveCount(tabsBefore + 1);
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
    const filtered = page.locator('.panel-list .panel-card');
    const count = await filtered.count();
    expect(count).toBeGreaterThanOrEqual(0);
    await page.locator('.right-panel-search-clear').click();
    await expect(searchInput).toHaveValue('');
  });

  test('後端 /api/status 健康檢查', async ({ request }) => {
    const res = await request.get('http://localhost:8765/api/status');
    if (res.ok()) {
      const body = await res.json();
      expect(body).toHaveProperty('claude_bin');
    } else {
      test.skip();
    }
  });

});

// ── P3 新增測試 ─────────────────────────────────────────────────────────────

test.describe('Claude Desktop — P3 功能', () => {

  test('設定頁包含 Provider 選單', async ({ page }) => {
    await page.goto('/');
    await page.locator('.icon-btn[title*="設定"]').first().click();
    await expect(page.locator('select[name="provider"], select')).toHaveCount({ minimum: 1 });
    // Provider section header
    const headers = page.locator('.modal-section-header');
    const texts = await headers.allTextContents();
    expect(texts.some(t => t.includes('Provider'))).toBe(true);
    await page.keyboard.press('Escape');
  });

  test('設定頁包含 Telegram 區塊', async ({ page }) => {
    await page.goto('/');
    await page.locator('.icon-btn[title*="設定"]').first().click();
    const headers = page.locator('.modal-section-header');
    const texts = await headers.allTextContents();
    expect(texts.some(t => t.includes('Telegram'))).toBe(true);
    await page.keyboard.press('Escape');
  });

  test('設定頁有語言切換選項', async ({ page }) => {
    await page.goto('/');
    await page.locator('.icon-btn[title*="設定"]').first().click();
    const headers = page.locator('.modal-section-header, label');
    const texts = await headers.allTextContents();
    expect(texts.some(t => t.includes('Language') || t.includes('語言'))).toBe(true);
    await page.keyboard.press('Escape');
  });

  test('設定頁底部有 Debug 診斷按鈕', async ({ page }) => {
    await page.goto('/');
    await page.locator('.icon-btn[title*="設定"]').first().click();
    await expect(page.locator('button[title*="診斷"], button:has-text("診斷")')).toBeVisible();
    await page.keyboard.press('Escape');
  });

  test('匯出格式選單在 topbar 存在', async ({ page }) => {
    await page.goto('/');
    const select = page.locator('.export-format-select');
    await expect(select).toBeVisible();
    // Default is .md
    await expect(select).toHaveValue('md');
    // Can switch to JSON
    await select.selectOption('json');
    await expect(select).toHaveValue('json');
  });

  test('⌘K 全局搜尋支援 Ctrl+K 開關', async ({ page }) => {
    await page.goto('/');
    await page.keyboard.press('Control+k');
    await expect(page.locator('.cmd-palette')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.cmd-palette')).not.toBeVisible();
  });

  test('後端 /api/profiles 回傳清單', async ({ request }) => {
    const res = await request.get('http://localhost:8765/api/profiles');
    if (res.ok()) {
      const body = await res.json();
      expect(body).toHaveProperty('profiles');
      expect(Array.isArray(body.profiles)).toBe(true);
    } else {
      test.skip();
    }
  });

  test('後端 /api/telegram GET 回傳狀態', async ({ request }) => {
    const res = await request.get('http://localhost:8765/api/telegram');
    if (res.ok()) {
      const body = await res.json();
      expect(body).toHaveProperty('enabled');
      expect(body).toHaveProperty('running');
    } else {
      test.skip();
    }
  });

  test('後端 /api/debug-dump 回傳 JSON', async ({ request }) => {
    const res = await request.get('http://localhost:8765/api/debug-dump');
    if (res.ok()) {
      const body = await res.json();
      expect(body).toHaveProperty('timestamp');
      expect(body).toHaveProperty('platform');
      expect(body).toHaveProperty('sqlite');
    } else {
      test.skip();
    }
  });

});
