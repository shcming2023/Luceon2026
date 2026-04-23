import { test, expect } from '@playwright/test';

/**
 * 跨页面一致性验收测试 (PRD v0.4 §8)
 * 验证：同一素材在工作台、资产详情页、任务列表显示的状态必须严格一致（基于 ParseTask 事实源）
 */

const BASE_URL = process.env.BASE_URL || 'http://localhost:8081';

test.describe('Cross-Page Consistency (ParseTask Truth)', () => {
  let materialId: number;
  let taskId: string;

  test.beforeAll(async ({ request }) => {
    // 1. 创建素材
    const uploadResp = await request.post(`${BASE_URL}/__proxy/upload/upload`, {
      multipart: {
        file: {
          name: 'consistency-test.pdf',
          mimeType: 'application/pdf',
          buffer: Buffer.from('%PDF-1.4 test'),
        },
        materialId: 'test-' + Date.now()
      }
    });
    const uploadData = await uploadResp.json();
    materialId = uploadData.materialId;

    // 2. 创建一个 Pending 任务
    const taskResp = await request.post(`${BASE_URL}/__proxy/upload/tasks`, {
      multipart: {
        materialId: String(materialId),
        objectName: uploadData.objectName
      }
    });
    const taskData = await taskResp.json();
    taskId = taskData.taskId;
    
    console.log(`Test Context: Material ${materialId}, Task ${taskId}`);
  });

  test('Status Consistency: Pending Stage', async ({ page }) => {
    // A. 工作台检查
    await page.goto(`${BASE_URL}/workspace`);
    const wsRow = page.locator(`tr:has-text("${materialId}")`);
    // 预期显示 "等待中" (queued bucket)
    await expect(wsRow.getByText(/等待中|解析中/)).toBeVisible();
    await expect(wsRow.getByText(taskId.slice(0, 8))).toBeVisible();

    // B. 资产详情页检查
    await page.goto(`${BASE_URL}/asset/${materialId}`);
    const detailStatus = page.locator('.flex.items-center.gap-2 span.rounded-full');
    await expect(detailStatus).toBeVisible();
    // 资产详情页应该显示任务卡片
    await expect(page.getByText(`Task ID: ${taskId}`)).toBeVisible();

    // C. 任务列表检查
    await page.goto(`${BASE_URL}/tasks`);
    const taskRow = page.locator(`tr:has-text("${taskId}")`);
    await expect(taskRow).toBeVisible();
    await expect(taskRow.getByText(/pending|waiting|等待中/i)).toBeVisible();
  });

  test('Status Consistency: Failure Stage', async ({ request, page }) => {
    // 1. 手动将任务设为失败
    await request.post(`${BASE_URL}/__proxy/db/tasks/${taskId}`, {
      data: { state: 'failed', errorMessage: 'Consistency Test Failure' }
    });

    // A. 工作台检查
    await page.goto(`${BASE_URL}/workspace`);
    const wsRow = page.locator(`tr:has-text("${materialId}")`);
    await expect(wsRow.getByText('失败')).toBeVisible();

    // B. 资产详情页检查
    await page.goto(`${BASE_URL}/asset/${materialId}`);
    await expect(page.getByText('失败')).toBeVisible();
    await expect(page.getByText('Consistency Test Failure')).toBeVisible();

    // C. 任务列表检查
    await page.goto(`${BASE_URL}/tasks`);
    const taskRow = page.locator(`tr:has-text("${taskId}")`);
    await expect(taskRow.getByText('failed', { exact: false })).toBeVisible();
  });
});
