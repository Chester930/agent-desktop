import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('claude_onboarding_done', '1'));
});

test.describe('版面拖曳分界', () => {
  test('三個主要拖曳把手都使用窄 hit area 與獨立 grip', async ({ page }) => {
    await page.goto('/');

    for (const selector of ['.sidebar-resize', '.right-resize', '.input-resize']) {
      const handle = page.locator(selector);
      await expect(handle).toBeVisible();
      await expect(handle.locator(`${selector}-track`)).toBeVisible();
      await expect(handle.locator(`${selector}-grip`)).toBeVisible();
    }

    await expect(page.locator('.sidebar-resize')).toHaveCSS('width', '10px');
    await expect(page.locator('.right-resize')).toHaveCSS('width', '10px');
    await expect(page.locator('.input-resize')).toHaveCSS('height', '10px');
  });

  test('拖曳輸入區與左右側欄會改變對應區塊尺寸', async ({ page }) => {
    await page.goto('/');

    const inputArea = page.locator('.input-area');
    const inputBefore = await inputArea.evaluate(element => element.getBoundingClientRect().height);
    const inputHandle = await page.locator('.input-resize').boundingBox();
    expect(inputHandle).not.toBeNull();
    await page.mouse.move(inputHandle!.x + inputHandle!.width / 2, inputHandle!.y + inputHandle!.height / 2);
    await page.mouse.down();
    await page.mouse.move(inputHandle!.x + inputHandle!.width / 2, inputHandle!.y - 60);
    await page.mouse.up();
    await expect.poll(
      () => inputArea.evaluate(element => element.getBoundingClientRect().height),
    ).toBeGreaterThan(inputBefore);

    const sidebar = page.locator('.sidebar');
    const sidebarBefore = (await sidebar.boundingBox())!.width;
    const sidebarHandle = await page.locator('.sidebar-resize').boundingBox();
    expect(sidebarHandle).not.toBeNull();
    const sidebarPoint = {
      x: sidebarHandle!.x + sidebarHandle!.width / 2,
      y: sidebarHandle!.y + 120,
    };
    await expect.poll(
      () => page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest('.sidebar-resize') !== null, sidebarPoint),
    ).toBe(true);
    await page.mouse.move(sidebarPoint.x, sidebarPoint.y);
    await page.mouse.down();
    await page.mouse.move(sidebarPoint.x + 60, sidebarPoint.y);
    await page.mouse.up();
    await expect.poll(
      () => sidebar.evaluate(element => element.getBoundingClientRect().width),
    ).toBeGreaterThan(sidebarBefore);

    const rightPanel = page.locator('.right-panel');
    const rightBefore = (await rightPanel.boundingBox())!.width;
    const rightHandle = await page.locator('.right-resize').boundingBox();
    expect(rightHandle).not.toBeNull();
    await page.mouse.move(rightHandle!.x + rightHandle!.width / 2, rightHandle!.y + 120);
    await page.mouse.down();
    await page.mouse.move(rightHandle!.x + rightHandle!.width / 2 - 60, rightHandle!.y + 120);
    await page.mouse.up();
    await expect.poll(
      () => rightPanel.evaluate(element => element.getBoundingClientRect().width),
    ).toBeGreaterThan(rightBefore);
  });
});
