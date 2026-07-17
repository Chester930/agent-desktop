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

    // 可控制的假 AudioContext/AnalyserNode，讓測試能「喊話」（高音量）跟
    // 「安靜」（低音量）之間切換，驗證靜音自動停止錄音的邏輯。
    (window as any).__setMockVolume = (v: number) => { (window as any).__mockVolume = v; };
    (window as any).__mockVolume = 0;

    class MockAnalyserNode {
      fftSize = 2048;
      get frequencyBinCount() { return this.fftSize; }
      getByteTimeDomainData(arr: Uint8Array) {
        const v = Math.max(0, Math.min(127, (window as any).__mockVolume || 0));
        for (let i = 0; i < arr.length; i++) {
          arr[i] = 128 + (i % 2 === 0 ? v : -v);
        }
      }
    }
    class MockAudioContext {
      createMediaStreamSource() { return { connect: () => undefined }; }
      createAnalyser() { return new MockAnalyserNode() as unknown as AnalyserNode; }
      close() { return Promise.resolve(); }
    }
    Object.defineProperty(window, 'AudioContext', {
      configurable: true,
      value: MockAudioContext,
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

  test('音量低於峰值 30% 持續兩秒會自動停止錄音並轉文字', async ({ page }) => {
    await page.goto('/');

    const micButton = page.locator('.mic-btn');
    // 先模擬「講話」：音量夠高，才會偵測到有講話、啟動靜音倒數的判斷
    await page.evaluate(() => (window as any).__setMockVolume(100));
    await micButton.click();
    await expect(micButton).toHaveClass(/recording/);
    await page.waitForTimeout(300);

    // 切成「安靜」：音量遠低於剛剛峰值的 30%
    await page.evaluate(() => (window as any).__setMockVolume(2));

    // 靜音超過 2 秒後應該會自動停止（不用手動再點一次）並開始轉文字
    await expect(micButton).not.toHaveClass(/recording/, { timeout: 4000 });
    const input = page.locator('.chat-input');
    await expect(input).toHaveValue('這是語音輸入測試');
  });

  test('沒偵測到講話（音量一直很低）不會誤觸發自動停止', async ({ page }) => {
    await page.goto('/');

    const micButton = page.locator('.mic-btn');
    // 音量全程都很低（低於 SPEECH_FLOOR），代表從沒偵測到講話——
    // 靜音倒數邏輯不該啟動，錄音要一直保持中，直到手動停止
    await page.evaluate(() => (window as any).__setMockVolume(1));
    await micButton.click();
    await expect(micButton).toHaveClass(/recording/);

    await page.waitForTimeout(2500);
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
