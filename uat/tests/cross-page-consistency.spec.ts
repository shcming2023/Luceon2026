import { test, expect } from '@playwright/test';
import { PDFDocument, rgb } from 'pdf-lib';

/**
 * 跨页面一致性验收测试 (PRD v0.4 §8)
 * 验证：同一素材在工作台、资产详情页、任务列表显示的状态必须严格一致（基于 ParseTask 事实源）
 */

const BASE_URL = process.env.BASE_URL || 'http://localhost:8081';

test.describe('Cross-Page Consistency (ParseTask Truth)', () => {
  let materialId: string;
  let taskId: string;
  let materialTitle: string;

  test.beforeAll(async ({ request }) => {
    // 1. 使用 pdf-lib 生成一个有效的单页 PDF
    const pdfDoc = await PDFDocument.create();
    const page = pdfDoc.addPage([600, 400]);
    page.drawText('Cross-Page Consistency Test PDF Content', { x: 50, y: 350, size: 20, color: rgb(0, 0, 0) });
    const pdfBytes = await pdfDoc.save();
    const pdfBuffer = Buffer.from(pdfBytes);

    materialId = `cross-page-${Date.now()}`;
    materialTitle = `cross-page-consistency-${materialId}`;

    // 2. 直接调用主链路入口 POST /__proxy/upload/tasks
    const resp = await request.post(`${BASE_URL}/__proxy/upload/tasks`, {
      multipart: {
        file: {
          name: `${materialTitle}.pdf`,
          mimeType: 'application/pdf',
          buffer: pdfBuffer,
        },
        materialId,
      },
    });

    // 3. 断言响应成功并获取数据
    const bodyText = await resp.text();
    expect(resp.ok(), `Upload failed: HTTP ${resp.status()} ${bodyText}`).toBeTruthy();

    const data = JSON.parse(bodyText);
    expect(data.taskId, 'Response should contain taskId').toBeTruthy();
    expect(data.materialId, 'Response should contain materialId').toBeTruthy();
    expect(data.objectName, 'Response should contain objectName').toBeTruthy();

    taskId = data.taskId;
    materialId = data.materialId;

    console.log(`Test Context: Material ${materialId}, Task ${taskId}`);
  });

  test('Status Consistency: Pending Stage', async ({ page }) => {
    // A. 工作台检查 - 使用唯一素材标题定位行，避免历史 taskId 前缀碰撞
    await page.goto(`${BASE_URL}/cms/workspace`);
    const wsRow = page.locator(`tr:has-text("${materialTitle}")`);
    // 预期显示 "等待中" (queued bucket)
    await expect(wsRow.getByText(/等待中|解析中/).first()).toBeVisible({ timeout: 15000 });

    // B. 资产详情页检查
    await page.goto(`${BASE_URL}/cms/asset/${materialId}`);
    // 等待页面稳定
    await page.waitForTimeout(2000);
    // 检查状态徽章（多个可能的选择器）
    const detailStatus = page.locator('.rounded-full').first();
    await expect(detailStatus).toBeVisible({ timeout: 15000 });
    // 资产详情页应该显示任务卡片（Task ID 单独一行，不带前缀）
    await expect(page.getByText(taskId).first()).toBeVisible();

    // C. 任务列表检查
    await page.goto(`${BASE_URL}/cms/tasks`);
    const taskRow = page.locator(`tr:has-text("${taskId}")`);
    await expect(taskRow).toBeVisible();
    await expect(taskRow.getByText(/pending|waiting|等待中/i)).toBeVisible();
  });

  test('Status Consistency: Failure Stage', async ({ request, page }) => {
    // 1. 手动将任务设为失败。db-server 的 /tasks/:id 仅支持 GET，写入需通过 POST /tasks upsert 完整对象。
    const taskResp = await request.get(`/__proxy/db/tasks/${encodeURIComponent(taskId)}`);
    const taskText = await taskResp.text();
    expect(taskResp.ok(), `Fetch task failed: HTTP ${taskResp.status()} ${taskText}`).toBeTruthy();
    const existingTask = JSON.parse(taskText);
    const updateResp = await request.post('/__proxy/db/tasks', {
      data: {
        ...existingTask,
        state: 'failed',
        errorMessage: 'Consistency Test Failure',
        updatedAt: new Date().toISOString(),
      },
    });
    const updateText = await updateResp.text();
    expect(updateResp.ok(), `Update task failed: HTTP ${updateResp.status()} ${updateText}`).toBeTruthy();

    // A. 工作台检查 - 使用唯一素材标题定位行
    await page.goto(`${BASE_URL}/cms/workspace`);
    const wsRow = page.locator(`tr:has-text("${materialTitle}")`);
    await expect(wsRow.getByText('失败')).toBeVisible({ timeout: 15000 });

    // B. 资产详情页检查
    await page.goto(`${BASE_URL}/cms/asset/${materialId}`);
    await expect(page.getByText('失败')).toBeVisible();
    await expect(page.getByText('Consistency Test Failure')).toBeVisible();

    // C. 任务列表检查 - 使用完整 taskId 定位，状态列显示中文展示桶
    await page.goto(`${BASE_URL}/cms/tasks`);
    const taskRow = page.locator(`tr:has-text("${taskId}")`);
    await expect(taskRow.getByText('失败')).toBeVisible();
  });
});
