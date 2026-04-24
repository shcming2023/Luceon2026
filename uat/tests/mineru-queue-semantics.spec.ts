/**
 * mineru-queue-semantics.spec.ts
 *
 * 验收防线：MinerU 排队语义 UI 严格断言（Mock 驱动）
 *
 * 策略：
 *   - 通过 page.route() Mock 任务详情页的后端 API 响应，构造确定性的
 *     mineru-queued / mineru-processing 中间态，不依赖真实 MinerU 环境。
 *   - 强制断言以下三种可控语义均必须在 UI 中正确展示：
 *       1. "MinerU 排队中" 文本（stage = mineru-queued）
 *       2. "MinerU 正在解析" 文本（stage = mineru-processing）
 *       3. MinerU Task ID 展示（如 mineru-internal-abc）
 *   - 如果上述断言任意一个失败，则测试报告为失败，说明页面排队语义展示有问题。
 *
 * ⚠️ 这是验收防线修复（P0 Patch 1），不涉及任何业务逻辑变更。
 */

import { test, expect } from '@playwright/test';

const MOCK_TASK_ID = 'mock-task-123';
const MOCK_MATERIAL_ID = 'mat-999';
const MOCK_MINERU_TASK_ID = 'mineru-internal-abc';
const MOCK_QUEUED_AHEAD = 3;

test.describe('MinerU Queue Semantics (Strict Mock-Driven UAT)', () => {

  test('mineru-queued 语义：页面必须展示排队文本、Task ID 和排队数量', async ({ page }) => {
    // ── 构造 mineru-queued 状态的 Mock 任务 ──────────────────
    const queuedTask = {
      id: MOCK_TASK_ID,
      materialId: MOCK_MATERIAL_ID,
      engine: 'local-mineru',
      stage: 'mineru-queued',
      state: 'running',
      progress: 20,
      message: `MinerU 排队中 (前方 ${MOCK_QUEUED_AHEAD} 个任务)`,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      metadata: {
        mineruTaskId: MOCK_MINERU_TASK_ID,
        mineruQueuedAhead: MOCK_QUEUED_AHEAD,
        mineruStartedAt: null,
        mineruLastStatusAt: new Date().toISOString(),
      },
    };

    // ── 拦截所有后端请求，返回 Mock 数据 ────────────────────
    await page.route(`**/__proxy/db/tasks/${MOCK_TASK_ID}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(queuedTask),
      });
    });

    await page.route(`**/__proxy/db/task-events?taskId=${MOCK_TASK_ID}`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });

    await page.route(`**/__proxy/db/ai-metadata-jobs?parseTaskId=${MOCK_TASK_ID}`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });

    await page.route(`**/__proxy/db/materials/${MOCK_MATERIAL_ID}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: MOCK_MATERIAL_ID,
          status: 'running',
          mineruStatus: 'queued',
          metadata: { objectName: 'originals/mat-999/test.pdf' },
        }),
      });
    });

    // 拦截 SSE 流，返回空响应防止连接错误干扰
    await page.route('**/__proxy/upload/tasks/stream**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
    });

    // ── 导航到任务详情页 ──────────────────────────────────────
    await page.goto(`/cms/tasks/${MOCK_TASK_ID}`);

    // ── 断言 1：页面标题可见 ──────────────────────────────────
    await expect(page.locator('h1').filter({ hasText: '任务详情' })).toBeVisible({
      timeout: 10000,
    });

    // ── 断言 2：MinerU 排队阶段值必须出现在页面上 ────────────
    // 阶段字段展示在概览 Tab 的状态卡片中
    await expect(page.getByText('mineru-queued', { exact: true })).toBeVisible({
      timeout: 5000,
    });

    // ── 断言 3：排队消息文本必须完整展示 ─────────────────────
    await expect(
      page.getByText(`MinerU 排队中 (前方 ${MOCK_QUEUED_AHEAD} 个任务)`, { exact: true })
    ).toBeVisible({ timeout: 5000 });

    // ── 断言 4：MinerU 状态详情卡片必须展示 ─────────────────
    // 当 metadata.mineruTaskId 存在时，页面渲染"MinerU 状态详情"卡片
    await expect(
      page.locator('h2').filter({ hasText: 'MinerU 状态详情' })
    ).toBeVisible({ timeout: 5000 });

    // ── 断言 5：MinerU Task ID 必须在页面中展示 ──────────────
    await expect(
      page.getByText(MOCK_MINERU_TASK_ID, { exact: true })
    ).toBeVisible({ timeout: 5000 });

    // ── 断言 6：排队数量展示必须正确 ─────────────────────────
    await expect(
      page.getByText(`${MOCK_QUEUED_AHEAD} (前方)`, { exact: true })
    ).toBeVisible({ timeout: 5000 });
  });

  test('mineru-processing 语义：页面必须展示"MinerU 正在解析"文本和 Task ID', async ({ page }) => {
    // ── 构造 mineru-processing 状态的 Mock 任务 ──────────────
    const processingTask = {
      id: MOCK_TASK_ID,
      materialId: MOCK_MATERIAL_ID,
      engine: 'local-mineru',
      stage: 'mineru-processing',
      state: 'running',
      progress: 50,
      message: 'MinerU 正在解析',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      metadata: {
        mineruTaskId: MOCK_MINERU_TASK_ID,
        mineruQueuedAhead: 0,
        mineruStartedAt: new Date().toISOString(),
        mineruLastStatusAt: new Date().toISOString(),
      },
    };

    // ── 拦截所有后端请求，返回 Mock 数据 ────────────────────
    await page.route(`**/__proxy/db/tasks/${MOCK_TASK_ID}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(processingTask),
      });
    });

    await page.route(`**/__proxy/db/task-events?taskId=${MOCK_TASK_ID}`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });

    await page.route(`**/__proxy/db/ai-metadata-jobs?parseTaskId=${MOCK_TASK_ID}`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });

    await page.route(`**/__proxy/db/materials/${MOCK_MATERIAL_ID}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: MOCK_MATERIAL_ID,
          status: 'running',
          mineruStatus: 'processing',
          metadata: { objectName: 'originals/mat-999/test.pdf' },
        }),
      });
    });

    await page.route('**/__proxy/upload/tasks/stream**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
    });

    // ── 导航到任务详情页 ──────────────────────────────────────
    await page.goto(`/cms/tasks/${MOCK_TASK_ID}`);

    // ── 断言 1：页面标题可见 ──────────────────────────────────
    await expect(page.locator('h1').filter({ hasText: '任务详情' })).toBeVisible({
      timeout: 10000,
    });

    // ── 断言 2：mineru-processing 阶段值必须出现 ─────────────
    await expect(page.getByText('mineru-processing', { exact: true })).toBeVisible({
      timeout: 5000,
    });

    // ── 断言 3：处理中消息文本必须展示 ───────────────────────
    await expect(
      page.getByText('MinerU 正在解析', { exact: true })
    ).toBeVisible({ timeout: 5000 });

    // ── 断言 4：MinerU 状态详情卡片必须可见 ─────────────────
    await expect(
      page.locator('h2').filter({ hasText: 'MinerU 状态详情' })
    ).toBeVisible({ timeout: 5000 });

    // ── 断言 5：MinerU Task ID 必须展示 ──────────────────────
    await expect(
      page.getByText(MOCK_MINERU_TASK_ID, { exact: true })
    ).toBeVisible({ timeout: 5000 });

    // ── 断言 6：处理中时排队数量应为 0 ───────────────────────
    await expect(
      page.getByText('0 (前方)', { exact: true })
    ).toBeVisible({ timeout: 5000 });
  });

  test('状态推进语义：点击刷新后，页面必须从 queued 切换到 processing', async ({ page }) => {
    // ── 构造可变 Mock 任务（初始 queued，刷新后 processing）──
    let currentTask = {
      id: MOCK_TASK_ID,
      materialId: MOCK_MATERIAL_ID,
      engine: 'local-mineru',
      stage: 'mineru-queued',
      state: 'running',
      progress: 20,
      message: `MinerU 排队中 (前方 ${MOCK_QUEUED_AHEAD} 个任务)`,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      metadata: {
        mineruTaskId: MOCK_MINERU_TASK_ID,
        mineruQueuedAhead: MOCK_QUEUED_AHEAD,
        mineruStartedAt: null,
        mineruLastStatusAt: new Date().toISOString(),
      },
    };

    // ── 拦截：每次访问该路由都返回当前 currentTask ───────────
    await page.route(`**/__proxy/db/tasks/${MOCK_TASK_ID}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(currentTask),
      });
    });

    await page.route(`**/__proxy/db/task-events?taskId=${MOCK_TASK_ID}`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });

    await page.route(`**/__proxy/db/ai-metadata-jobs?parseTaskId=${MOCK_TASK_ID}`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });

    await page.route(`**/__proxy/db/materials/${MOCK_MATERIAL_ID}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: MOCK_MATERIAL_ID,
          status: 'running',
          mineruStatus: 'queued',
          metadata: { objectName: 'originals/mat-999/test.pdf' },
        }),
      });
    });

    await page.route('**/__proxy/upload/tasks/stream**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
    });

    // ── 导航到任务详情页 ──────────────────────────────────────
    await page.goto(`/cms/tasks/${MOCK_TASK_ID}`);
    await expect(page.locator('h1').filter({ hasText: '任务详情' })).toBeVisible({ timeout: 10000 });

    // ── Phase 1：验证排队中状态 ───────────────────────────────
    await expect(page.getByText('mineru-queued', { exact: true })).toBeVisible({ timeout: 5000 });
    await expect(
      page.getByText(`MinerU 排队中 (前方 ${MOCK_QUEUED_AHEAD} 个任务)`, { exact: true })
    ).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(MOCK_MINERU_TASK_ID, { exact: true })).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(`${MOCK_QUEUED_AHEAD} (前方)`, { exact: true })).toBeVisible({ timeout: 5000 });

    // ── 更新 Mock 到 processing 状态 ─────────────────────────
    currentTask = {
      ...currentTask,
      stage: 'mineru-processing',
      progress: 50,
      message: 'MinerU 正在解析',
      metadata: {
        ...currentTask.metadata,
        mineruQueuedAhead: 0,
        mineruStartedAt: new Date().toISOString(),
        mineruLastStatusAt: new Date().toISOString(),
      },
    };

    // ── 点击刷新按钮触发重新获取 ─────────────────────────────
    await page.getByRole('button', { name: '刷新', exact: true }).click();

    // ── Phase 2：验证处理中状态 ───────────────────────────────
    await expect(page.getByText('mineru-processing', { exact: true })).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('MinerU 正在解析', { exact: true })).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(MOCK_MINERU_TASK_ID, { exact: true })).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('0 (前方)', { exact: true })).toBeVisible({ timeout: 5000 });
  });
});
