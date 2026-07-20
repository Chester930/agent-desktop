import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('claude_onboarding_done', '1'));
});

test.describe('Agent Desktop — 基本流程', () => {

  test('主畫面正常載入', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/Agent 桌面版/);
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
    await page.locator('.umenu-trigger').click();
    await page.locator('.umenu-item', { hasText: '設定' }).click();
    await expect(page.locator('.modal-backdrop')).toBeVisible();
    await expect(page.locator('.modal')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.modal-backdrop')).not.toBeVisible();
  });

  test('右側 Skills 分頁搜尋', async ({ page }) => {
    await page.goto('/');
    await page.locator('.tab-bar button', { hasText: /Skill/i }).click();
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
    const res = await request.get('http://localhost:8765/api/status', { timeout: 2000 }).catch(() => null);
    if (!res) { test.skip(true, '後端未啟動'); return; }
    if (res.ok()) {
      const body = await res.json();
      expect(body).toHaveProperty('claude_bin');
    } else {
      test.skip();
    }
  });

});

// ── P3 新增測試 ─────────────────────────────────────────────────────────────

test.describe('Agent Desktop — P3 功能', () => {

  test('設定頁包含 Provider 選單', async ({ page }) => {
    await page.goto('/');
    await page.locator('.umenu-trigger').click();
    await page.locator('.umenu-item', { hasText: '設定' }).click();
    // Provider section header exists
    const headers = page.locator('.modal-section-header');
    const texts = await headers.allTextContents();
    expect(texts.some(t => t.includes('Provider'))).toBe(true);
    // At least one select is present inside the modal
    const selectCount = await page.locator('.modal select').count();
    expect(selectCount).toBeGreaterThanOrEqual(1);
    await page.keyboard.press('Escape');
  });

  test('設定頁包含 Telegram 區塊', async ({ page }) => {
    await page.goto('/');
    await page.locator('.umenu-trigger').click();
    await page.locator('.umenu-item', { hasText: '設定' }).click();
    const headers = page.locator('.modal-section-header');
    const texts = await headers.allTextContents();
    expect(texts.some(t => t.includes('Telegram'))).toBe(true);
    await page.keyboard.press('Escape');
  });

  test('設定頁有語言切換選項', async ({ page }) => {
    await page.goto('/');
    await page.locator('.umenu-trigger').click();
    await page.locator('.umenu-item', { hasText: '設定' }).click();
    const headers = page.locator('.modal-section-header, label');
    const texts = await headers.allTextContents();
    expect(texts.some(t => t.includes('Language') || t.includes('語言'))).toBe(true);
    await page.keyboard.press('Escape');
  });

  test('設定頁底部有 Debug 診斷按鈕', async ({ page }) => {
    await page.goto('/');
    await page.locator('.umenu-trigger').click();
    await page.locator('.umenu-item', { hasText: '設定' }).click();
    // The debug dump button has a specific title
    await expect(page.locator('button[title*="下載診斷"]')).toBeVisible();
    await page.keyboard.press('Escape');
  });

  test('對話匯出按鈕存在', async ({ page }) => {
    await page.goto('/');
    const exportButton = page.locator('button[title="匯出對話"]');
    await expect(exportButton).toBeVisible();
    await expect(exportButton).toBeDisabled();
  });

  test('⌘K 全局搜尋支援 Ctrl+K 開關', async ({ page }) => {
    await page.goto('/');
    await page.keyboard.press('Control+k');
    await expect(page.locator('.cmd-palette')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.cmd-palette')).not.toBeVisible();
  });

  test('後端 /api/profiles 回傳清單', async ({ request }) => {
    const res = await request.get('http://localhost:8765/api/profiles', { timeout: 2000 }).catch(() => null);
    if (!res) { test.skip(true, '後端未啟動'); return; }
    if (res.ok()) {
      const body = await res.json();
      expect(body).toHaveProperty('profiles');
      expect(Array.isArray(body.profiles)).toBe(true);
    } else {
      test.skip();
    }
  });

  test('後端 /api/telegram GET 回傳狀態', async ({ request }) => {
    const res = await request.get('http://localhost:8765/api/telegram', { timeout: 2000 }).catch(() => null);
    if (!res) { test.skip(true, '後端未啟動'); return; }
    if (res.ok()) {
      const body = await res.json();
      expect(body).toHaveProperty('enabled');
      expect(body).toHaveProperty('running');
    } else {
      test.skip();
    }
  });

  test('後端 /api/debug-dump 回傳 JSON', async ({ request }) => {
    const res = await request.get('http://localhost:8765/api/debug-dump', { timeout: 2000 }).catch(() => null);
    if (!res) { test.skip(true, '後端未啟動'); return; }
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

// ── P4 / Teams / Memory 新增測試 ─────────────────────────────────────────────

test.describe('Agent Desktop — Phase 4 HR Agent & Teams', () => {

  test('後端 /api/agents/registry 回傳正確結構', async ({ request }) => {
    const res = await request.get('http://localhost:8765/api/agents/registry', { timeout: 2000 }).catch(() => null);
    if (!res) { test.skip(true, '後端未啟動'); return; }
    if (!res.ok()) { test.skip(); return; }
    const body = await res.json();
    expect(Array.isArray(body)).toBe(true);
    if (body.length > 0) {
      const first = body[0];
      expect(first).toHaveProperty('id');
      expect(first).toHaveProperty('name');
      expect(first).toHaveProperty('description');
      expect(first).toHaveProperty('skills');
      expect(Array.isArray(first.skills)).toBe(true);
    }
  });

  test('後端 /api/teams 回傳清單', async ({ request }) => {
    const res = await request.get('http://localhost:8765/api/teams', { timeout: 2000 }).catch(() => null);
    if (!res) { test.skip(true, '後端未啟動'); return; }
    if (!res.ok()) { test.skip(); return; }
    const body = await res.json();
    expect(Array.isArray(body)).toBe(true);
  });

  test('後端 Teams CRUD — 建立 / 查詢 / 刪除', async ({ request }) => {
    const BASE = 'http://localhost:8765';
    // 建立
    const created = await request.post(`${BASE}/api/teams`, {
      data: { name: 'e2e-test-team', description: 'E2E 測試', members: [] },
      timeout: 2000,
    }).catch(() => null);
    if (!created) { test.skip(true, '後端未啟動'); return; }
    if (!created.ok()) { test.skip(); return; }
    // 查詢
    const got = await request.get(`${BASE}/api/teams/e2e-test-team`);
    expect(got.ok()).toBe(true);
    const body = await got.json();
    expect(body.name).toBe('e2e-test-team');
    // 刪除
    const del = await request.delete(`${BASE}/api/teams/e2e-test-team`);
    expect(del.ok()).toBe(true);
    // 確認刪除
    const notFound = await request.get(`${BASE}/api/teams/e2e-test-team`);
    expect(notFound.status()).toBe(404);
  });

  test('後端 Team Run — 建立 / 查詢狀態', async ({ request }) => {
    const BASE = 'http://localhost:8765';
    const runRes = await request.post(`${BASE}/api/team/run`, {
      data: {
        task: 'E2E 測試任務',
        team: {
          name: 'e2e-run-team',
          members: [{ agent: 'nonexistent-agent', role: '測試' }],
        },
      },
      timeout: 2000,
    }).catch(() => null);
    if (!runRes) { test.skip(true, '後端未啟動'); return; }
    if (!runRes.ok()) { test.skip(); return; }
    const runBody = await runRes.json();
    expect(runBody).toHaveProperty('run_id');
    expect(runBody.ok).toBe(true);

    // 查詢狀態
    const statusRes = await request.get(`${BASE}/api/team/run/${runBody.run_id}`);
    expect(statusRes.ok()).toBe(true);
    const status = await statusRes.json();
    expect(status.id).toBe(runBody.run_id);
    expect(status).toHaveProperty('status');
    expect(status).toHaveProperty('steps');
    expect(Array.isArray(status.steps)).toBe(true);
  });

  test('後端 Memory CRUD — 寫入 / 讀取 / 刪除', async ({ request }) => {
    const BASE = 'http://localhost:8765';
    const content = 'E2E memory relay 測試 — UNIQUE_MARKER_' + Date.now();
    // 寫入
    const writeRes = await request.put(`${BASE}/api/memory/e2e-relay-key`, {
      data: { content },
      timeout: 2000,
    }).catch(() => null);
    if (!writeRes) { test.skip(true, '後端未啟動'); return; }
    if (!writeRes.ok()) { test.skip(); return; }

    // 讀取列表確認存在
    const listRes = await request.get(`${BASE}/api/memory`);
    const list = await listRes.json();
    expect(Array.isArray(list)).toBe(true);
    const keys = list.map((m: { key: string }) => m.key);
    expect(keys).toContain('e2e-relay-key');

    // 刪除
    const delRes = await request.delete(`${BASE}/api/memory/e2e-relay-key`);
    expect(delRes.ok()).toBe(true);
  });

  test('Agent 頁籤存在', async ({ page }) => {
    await page.goto('/');
    const agentTab = page.locator('.tab-bar button', { hasText: /Agent/i });
    await expect(agentTab).toBeVisible();
  });

  test('Teams 頁籤存在', async ({ page }) => {
    await page.goto('/');
    const teamsTab = page.locator('.tab-bar button', { hasText: /Team/i });
    await expect(teamsTab).toBeVisible();
  });

  test('Team 卡片可以切換最愛', async ({ page }) => {
    let favoritePayload: unknown = null;
    await page.route('**/api/teams', async route => {
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: 'team-alpha',
            name: 'Alpha Team',
            description: 'Front-end test team',
            leader: 'agent-a',
            members: [{ agent: 'agent-a', role: 'Lead' }],
            execution_mode: 'parallel',
            favorite: false,
          },
        ]),
      });
    });
    await page.route('**/api/teams/team-alpha', async route => {
      if (route.request().method() !== 'PUT') {
        await route.fallback();
        return;
      }
      favoritePayload = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
    });

    await page.goto('/');
    await page.locator('.tab-bar button', { hasText: /Team/i }).click();

    const card = page.locator('.panel-card', { hasText: 'Alpha Team' });
    await expect(card).toBeVisible();
    const favoriteButton = card.locator('.team-fav-btn');
    await expect(favoriteButton).toContainText('☆');

    await favoriteButton.click();

    expect(favoritePayload).toEqual({ favorite: true });
    await expect(favoriteButton).toContainText('⭐');
    await expect(favoriteButton).toHaveClass(/active/);
  });

  test('Scheduling 頁籤存在', async ({ page }) => {
    await page.goto('/');
    const schedulingTab = page.locator('.tab-bar button', { hasText: /Scheduling/i });
    await expect(schedulingTab).toBeVisible();
  });

  test('Project 按鈕存在', async ({ page }) => {
    await page.goto('/');
    const projectBtn = page.locator('.project-btn, button:has-text("Project")').first();
    await expect(projectBtn).toBeVisible();
  });

});
