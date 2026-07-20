import { test, expect } from '@playwright/test';

// 2026-07-10 team 協作優化健檢：發現 6 — 組隊「開始執行」後畫面上完全沒有
// 任何進度顯示（teamRunOpen/teamRunState 這兩個 signal 從未被任何 template
// 讀取過）。修復後改成把 team run 掛在一則 chat message 上
// （ChatMessage.teamRun），比照 executeTeamCodePhase() 已經在畫面上正確
// render 的模式。之前只驗證到 tsc/ng build 編譯通過，這裡用真實瀏覽器
// （Playwright + Chromium）驗證修復後的 DOM 真的會顯示進度。
//
// 入口原本是一次性的 HR 自動組隊，該功能已移除（改用單一 Agent + Plan
// 模式取代），這裡改用 Project（深度組隊）當入口——執行階段兩者共用同一套
// _dispatchTeamRun()／/api/team/run，這個測試真正關心的「SSE 事件有沒有
// 正確 render 進 chat 訊息」跟走哪個入口無關。
//
// 用 page.route() mock 掉 /api/hr/plan-team、/api/team/run、
// /api/team/run/:id/stream 幾個端點，不呼叫真實的 claude CLI——這個測試
// 只關心「前端收到 SSE 事件後有沒有正確 render」，跟後端/CLI 的真實行為
// 無關（後端邏輯已經在 pytest 用真實 CLI 驗證過，見 docs/HANDOFF.md 十一節）。

const PLAN_RUN_ID = 'mock-plan-run-e2e-1';
const RUN_ID = 'mock-run-e2e-1';

const PLAN_SSE_BODY = [
  `data: ${JSON.stringify({ type: 'plan_step', phase: 'plan', status: 'running' })}`,
  '',
  `data: ${JSON.stringify({ type: 'plan_step', phase: 'negotiate', status: 'done' })}`,
  '',
  `data: ${JSON.stringify({ type: 'done', summary: '已規劃完成' })}`,
  '',
  '',
].join('\n');

const PLAN_RESULT = {
  status: 'done',
  leader: 'mock-agent-1',
  reused_team_id: '',
  team_id: 'mock-auto-team',
  plan_doc: '# Plan\n\n目標是做點什麼。',
  project_path: '',
  members: [
    { agent: 'mock-agent-1', role: 'Coder', task_doc: '請幫我寫一句話', consensus: true, rounds: 1 },
  ],
};

const SSE_BODY = [
  `data: ${JSON.stringify({ type: 'step_start', step: 0, agent: 'mock-agent-1', role: 'Coder' })}`,
  '',
  `data: ${JSON.stringify({ type: 'step_text', step: 0, text: 'Hello ' })}`,
  '',
  `data: ${JSON.stringify({ type: 'step_text', step: 0, text: 'from mocked agent!' })}`,
  '',
  `data: ${JSON.stringify({ type: 'step_done', step: 0 })}`,
  '',
  `data: ${JSON.stringify({ type: 'done', summary: 'Mock task complete!' })}`,
  '',
  '',
].join('\n');

test.describe('Team Run 進度顯示（發現 6 修復驗證）', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('claude_onboarding_done', '1');
    });

    await page.route('**/api/hr/plan-team', async (route) => {
      if (route.request().method() !== 'POST') { await route.fallback(); return; }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, run_id: PLAN_RUN_ID }),
      });
    });

    await page.route(`**/api/hr/plan-team/${PLAN_RUN_ID}/stream`, async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream', 'access-control-allow-origin': '*' },
        body: PLAN_SSE_BODY,
      });
    });

    await page.route(`**/api/hr/plan-team/${PLAN_RUN_ID}`, async (route) => {
      if (route.request().method() !== 'GET') { await route.fallback(); return; }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(PLAN_RESULT),
      });
    });

    await page.route('**/api/team/run', async (route) => {
      if (route.request().method() !== 'POST') { await route.fallback(); return; }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, run_id: RUN_ID }),
      });
    });

    await page.route(`**/api/team/run/${RUN_ID}/stream`, async (route) => {
      await route.fulfill({
        status: 200,
        headers: {
          'content-type': 'text/event-stream',
          'access-control-allow-origin': '*',
        },
        body: SSE_BODY,
      });
    });

    await page.route('**/api/team/run/*/artifacts', async (route) => {
      await route.fulfill({
        status: 200,
        headers: {
          'content-type': 'application/json',
          'access-control-allow-origin': '*',
        },
        body: JSON.stringify({ run_id: RUN_ID, artifacts: [] }),
      });
    });
  });

  test('Project「開始執行」後，chat 訊息裡真的會顯示團隊進度', async ({ page }) => {
    await page.goto('/');

    const input = page.locator('.chat-input');
    await input.click();
    await input.fill('請幫我寫一句話');

    await page.locator('.project-btn').click();

    // Project 規劃結果審閱 modal 出現（mocked 回應）
    const modal = page.locator('.editor-modal', { hasText: 'Project 規劃結果' });
    await expect(modal).toBeVisible({ timeout: 10000 });
    await expect(modal).toContainText('mock-agent-1');

    await modal.locator('button', { hasText: '開始執行' }).click();

    // modal 應該關閉
    await expect(modal).not.toBeVisible();

    // 核心斷言：chat 訊息裡必須出現 embedded team run 進度區塊
    // （修復前：teamRunState/teamRunOpen 沒有任何 template 讀取，畫面上
    // 什麼都不會出現）
    // .role-tag 本身有 `display: none !important`（app.scss，跟這次修復無關的
    // 既有樣式），所以只驗證內容存在，不驗證 CSS 可見性。
    const teamMsg = page.locator('.msg-assistant-group .role-tag', { hasText: 'mock-auto-team' });
    await expect(teamMsg).toHaveCount(1, { timeout: 10000 });

    const stepBlock = page.locator('.embedded-tr-steps');
    await expect(stepBlock).toBeVisible();
    await expect(stepBlock).toContainText('mock-agent-1');

    // SSE 事件確實被套用到畫面上（step_text 累積的輸出、done 事件的摘要）
    await expect(stepBlock).toContainText('Hello from mocked agent!');
    await expect(page.locator('.msg-assistant-group').last()).toContainText('執行完成');
  });
});
