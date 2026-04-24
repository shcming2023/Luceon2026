import { test, expect } from '@playwright/test';

test.describe('MinerU Queue Semantics (Mocked UI Test)', () => {

  test('should correctly display mineru-queued and mineru-processing semantics on Task Detail Page', async ({ page }) => {
    // 拦截相关的 API 请求并返回 mock 数据
    // mockTask 的基础结构
    let currentMockTask = {
      id: 'mock-task-123',
      materialId: 'mat-999',
      engine: 'pipeline',
      stage: 'mineru-queued',
      state: 'running',
      progress: 20,
      message: 'MinerU 排队中 (前方 3 个任务)',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      metadata: {
        mineruTaskId: 'mineru-internal-abc',
        mineruQueuedAhead: 3,
        mineruStartedAt: null,
        mineruLastStatusAt: new Date().toISOString()
      }
    };

    await page.route('**/__proxy/db/tasks/mock-task-123', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(currentMockTask)
      });
    });

    await page.route('**/__proxy/db/task-events?taskId=mock-task-123', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([])
      });
    });

    await page.route('**/__proxy/db/ai-metadata-jobs?parseTaskId=mock-task-123', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([])
      });
    });

    // 假设 material 请求也拦截掉
    await page.route('**/__proxy/db/materials/mat-999', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'mat-999',
          status: 'running',
          mineruStatus: 'queued',
          metadata: { objectName: 'test.pdf' }
        })
      });
    });

    // 直接导航到该任务的详情页
    await page.goto('/cms/tasks/mock-task-123');
    
    // 验证页面加载完成
    await expect(page.locator('h1').filter({ hasText: '任务详情' })).toBeVisible();

    // 验证排队语义 (阶段/消息/MinerU Task ID)
    const stageEl = page.locator('div').filter({ hasText: /^阶段$/ }).locator('+ p, .. p').nth(1);
    await expect(stageEl).toHaveText('mineru-queued');

    const messageEl = page.locator('div').filter({ hasText: /^消息$/ }).locator('+ p, .. p').nth(1);
    await expect(messageEl).toContainText('MinerU 排队中 (前方 3 个任务)');

    // 验证基础信息下方的 MinerU 状态详情卡片
    await expect(page.locator('h2').filter({ hasText: 'MinerU 状态详情' })).toBeVisible();
    await expect(page.locator('dd').filter({ hasText: 'mineru-internal-abc' })).toBeVisible();
    await expect(page.locator('dd').filter({ hasText: '3 (前方)' })).toBeVisible();

    // 接下来模拟后端任务状态推进到了处理中
    currentMockTask = {
      ...currentMockTask,
      stage: 'mineru-processing',
      progress: 50,
      message: 'MinerU 正在解析',
      metadata: {
        ...currentMockTask.metadata,
        mineruQueuedAhead: 0,
        mineruStartedAt: new Date().toISOString()
      }
    };

    // 点击刷新按钮
    await page.getByRole('button', { name: '刷新', exact: true }).click();

    // 验证处理中语义
    await expect(stageEl).toHaveText('mineru-processing');
    await expect(messageEl).toContainText('MinerU 正在解析');
    await expect(page.locator('dd').filter({ hasText: '0 (前方)' })).toBeVisible();

  });
});
