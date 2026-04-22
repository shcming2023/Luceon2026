import { defineConfig, devices } from '@playwright/test';

/**
 * EduAsset CMS — Playwright UAT 测试配置
 *
 * 运行方式：
 *   cd uat && npx playwright test
 *   或使用项目根目录脚本：
 *   npx playwright test --config=uat/playwright.config.ts
 */

const BASE_URL = process.env.BASE_URL || 'http://localhost:8081';
/** 公网主机名，用于 presigned URL 断言。未设置时跳过主机名匹配。 */
const PUBLIC_HOST = process.env.PUBLIC_HOST || '';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 1,
  workers: 1,
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
    ['json', { outputFile: 'playwright-results.json' }],
  ],

  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    // 局域网环境适当放宽超时
    navigationTimeout: 30_000,
    actionTimeout: 15_000,
  },

  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    // 如需多浏览器测试，取消注释以下配置
    // {
    //   name: 'firefox',
    //   use: { ...devices['Desktop Firefox'] },
    // },
    // {
    //   name: 'webkit',
    //   use: { ...devices['Desktop Safari'] },
    // },
  ],
});
