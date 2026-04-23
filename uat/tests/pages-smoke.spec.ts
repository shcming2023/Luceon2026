import { test, expect, type Page } from '@playwright/test';

/**
 * Pages Smoke Test — 页面级运行时可用性检测
 * 
 * 目标：防止出现“HTTP 200 但 React 崩溃（ReferenceError 等）”的回归事故。
 * 覆盖：Tasks, Audit, Ops Health, Materials, Library 等核心页面。
 */

const BASE_URL = process.env.BASE_URL || 'http://localhost:8081';

test.describe('Dashboard Pages Smoke (Runtime Stability)', () => {
  
  const consoleErrors: string[] = [];

  // 在每个测试前清空错误集合并设置监听
  test.beforeEach(async ({ page }) => {
    consoleErrors.length = 0;

    page.on('pageerror', (exception) => {
      consoleErrors.push(`[PageError] ${exception.stack || exception.message}`);
    });

    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        const text = msg.text();
        // 捕获关键运行时错误
        if (text.includes('ReferenceError') || text.includes('is not defined') || text.includes('ErrorBoundary')) {
          consoleErrors.push(`[ConsoleError] ${text}`);
        }
      }
    });
  });

  // 每个测试后断言是否存在严重错误
  test.afterEach(async () => {
    if (consoleErrors.length > 0) {
      const errorMsg = consoleErrors.join('\n');
      expect(consoleErrors, `Detected runtime errors in browser console:\n${errorMsg}`).toHaveLength(0);
    }
  });

  const targetPages = [
    { name: '任务管理', path: '/cms/tasks', heading: '任务管理' },
    { name: '一致性审计', path: '/cms/audit', heading: '一致性审计' },
    { name: '系统健康', path: '/cms/ops/health', heading: '系统健康' },
    { name: '工作台', path: '/cms/workspace', heading: '工作台' },
    { name: '成果库', path: '/cms/library', heading: '成果库' },
    { name: '系统设置', path: '/cms/settings', heading: '系统设置' },
  ];

  for (const pageInfo of targetPages) {
    test(`Page: ${pageInfo.name} (${pageInfo.path}) should render without crash`, async ({ page }) => {
      console.log(`Testing visibility of ${pageInfo.name}...`);
      
      const response = await page.goto(`${BASE_URL}${pageInfo.path}`);
      
      // 1. 基础响应断言
      expect(response?.status()).toBe(200);

      // 2. 核心元素等待
      await page.waitForSelector('main', { timeout: 10000 });

      // 3. 负面断言：不出现 ErrorBoundary 的关键词
      const bodyText = await page.innerText('body');
      expect(bodyText).not.toContain('应用程序遇到了一个意外错误');
      expect(bodyText).not.toContain('ErrorBoundary');
      
      // 4. 正面断言：关键标题存在 (不分大小写或模糊匹配)
      const h1 = page.locator('h1');
      await expect(h1.first()).toBeVisible();
      
      // 特殊处理：有些页面标题可能是 div 或 span 模拟的，根据实际 DOM 结构适配
      const pageText = await page.textContent('body');
      expect(pageText).toContain(pageInfo.heading);
    });
  }

  test('Task Detail page should render without crash', async ({ page }) => {
    // 访问一个不存在的 ID 也会触发详情页组件，应检查其诊断矩阵是否因空数据崩溃
    await page.goto(`${BASE_URL}/cms/tasks/non-existent-id`);
    await page.waitForSelector('main', { timeout: 10000 });
    const bodyText = await page.innerText('body');
    expect(bodyText).not.toContain('ReferenceError');
    expect(bodyText).not.toContain('应用程序遇到了一个意外错误');
  });

});
