import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env['PLAYWRIGHT_BASE_URL'] ?? 'http://127.0.0.1:4200';
const baseUrlPort = new URL(baseURL).port || '4200';

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  expect: { timeout: 5000 },
  fullyParallel: false,
  retries: process.env['CI'] ? 2 : 0,
  reporter: 'html',

  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],

  // Start ng serve before running tests
  webServer: {
    command: `npm run start -- --host 127.0.0.1 --port ${baseUrlPort}`,
    url: baseURL,
    reuseExistingServer: !process.env['CI'],
    timeout: 60000,
  },
});
