import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('claude_onboarding_done', '1');

    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => ({
          getTracks: () => [{ stop: () => undefined }],
        }),
      },
    });

    class MockMediaRecorder extends EventTarget {
      static isTypeSupported() { return true; }
      state = 'inactive';
      mimeType = 'audio/webm';
      ondataavailable: ((event: Event) => void) | null = null;
      onstop: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      start() {
        this.state = 'recording';
      }

      stop() {
        if (this.state === 'inactive') return;
        this.state = 'inactive';
        const dataEvent = new Event('dataavailable');
        Object.defineProperty(dataEvent, 'data', {
          value: new Blob(['mock-audio'], { type: this.mimeType }),
        });
        this.ondataavailable?.(dataEvent);
        this.dispatchEvent(dataEvent);
        const stopEvent = new Event('stop');
        this.onstop?.(stopEvent);
        this.dispatchEvent(stopEvent);
      }
    }

    Object.defineProperty(window, 'MediaRecorder', {
      configurable: true,
      value: MockMediaRecorder,
    });

    Object.defineProperty(window, 'speechSynthesis', {
      configurable: true,
      value: {
        speak: (utterance: SpeechSynthesisUtterance) => {
          (window as any).__speechSpeakCount = ((window as any).__speechSpeakCount || 0) + 1;
          setTimeout(() => utterance.onend?.(new SpeechSynthesisEvent('end')), 0);
        },
        cancel: () => {
          (window as any).__speechCancelCount = ((window as any).__speechCancelCount || 0) + 1;
        },
      },
    });
  });

  await page.route('**/api/audio/transcriptions', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ text: '這是語音輸入測試' }),
    });
  });

  await page.route('**/api/chat', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback();
      return;
    }
    const body = [
      `data: ${JSON.stringify({
        type: 'assistant',
        message: { content: [{ type: 'text', text: '這是語音輸出測試' }] },
      })}`,
      '',
      `data: ${JSON.stringify({ type: 'result', usage: {}, total_cost_usd: 0 })}`,
      '',
      '',
    ].join('\n');
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body,
    });
  });
});

test.describe('Agent Desktop — 語音功能', () => {
  test('語音輸入會錄音、轉錄並追加到輸入框', async ({ page }) => {
    await page.goto('/');

    const micButton = page.locator('.mic-btn');
    await micButton.click();
    await expect(micButton).toHaveClass(/recording/);

    await micButton.click();
    const input = page.locator('.chat-input');
    await expect(input).toHaveValue('這是語音輸入測試');
  });

  test('assistant 回覆可以觸發語音輸出', async ({ page }) => {
    await page.goto('/');

    const input = page.locator('.chat-input');
    await input.fill('請回覆一句話');
    await page.locator('.send-btn').click();

    await expect(page.locator('.assistant-bubble')).toContainText('這是語音輸出測試');
    await page.locator('.msg-action-btn', { hasText: '朗讀' }).click();

    await expect.poll(() => page.evaluate(() => (window as any).__speechSpeakCount || 0)).toBeGreaterThan(0);
    await expect.poll(() => page.evaluate(() => (window as any).__speechCancelCount || 0)).toBeGreaterThan(0);
  });
});
