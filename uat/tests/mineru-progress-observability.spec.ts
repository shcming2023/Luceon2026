import { test, expect } from '@playwright/test';

const BASE_URL = process.env.BASE_URL || 'http://127.0.0.1:8081';

test.describe('MinerU 本地日志进度观测与停滞判定', () => {
  test('任务详情页和管理页的观测进度展示', async ({ page, request }) => {
    // 模拟创建任务并设置进度
    const materialId = `uat-prog-${Date.now()}`;
    const uploadResp = await request.post(`${BASE_URL}/__proxy/upload/tasks`, {
      multipart: {
        file: {
          name: 'uat-progress.pdf',
          mimeType: 'application/pdf',
          buffer: Buffer.from('%PDF-1.4 mock pdf'),
        },
        materialId
      },
    });

    expect(uploadResp.status()).toBe(200);
    const { taskId } = await uploadResp.json();

    // 手动更新 task metadata
    const patchResp = await request.patch(`${BASE_URL}/__proxy/db/tasks/${taskId}`, {
      data: {
        state: 'running',
        stage: 'mineru-processing',
        metadata: {
          mineruStatus: 'processing',
          mineruStartedAt: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
          mineruObservedProgress: {
            source: 'mineru-log',
            phase: 'Processing pages',
            percent: 78,
            current: 21,
            total: 27,
            observedAt: new Date(Date.now() - 6 * 60 * 1000).toISOString() // 6分钟前 -> stale-warning
          },
          mineruProgressHealth: 'stale-warning'
        }
      }
    });
    expect(patchResp.ok()).toBeTruthy();

    const tResp = await request.get(`${BASE_URL}/__proxy/db/tasks/${taskId}`);
    const task = await tResp.json();
    
    expect(task.metadata.mineruObservedProgress.phase).toBe('Processing pages');
    expect(task.metadata.mineruProgressHealth).toBe('stale-warning');

    // 验证前端展示
    await page.goto(`${BASE_URL}/cms/tasks/${taskId}`);
    
    // 断言存在真实进度观测卡片
    await expect(page.locator('text=MinerU 真实进度观测')).toBeVisible();
    await expect(page.locator('text=Processing pages')).toBeVisible();
    await expect(page.locator('text=21/27')).toBeVisible();
    await expect(page.locator('text=可能停滞')).toBeVisible();

    // 断言卡片内不存在“整体进度”字样（防止语义混淆）
    const cardText = await page.locator('text=MinerU 真实进度观测').locator('xpath=..').textContent();
    expect(cardText).not.toContain('整体进度');
    
    // 验证 Ops 页面
    await page.goto(`${BASE_URL}/cms/ops`);
    // OpsHealth 可能是从 api 拉取 diagnostics
    // 只需要验证能够打开不报错即可
    await expect(page.locator('text=系统运维概览')).toBeVisible();
  });

  test('OpsHealthPage 显示已知失败但仍在处理的 MinerU 任务', async ({ page }) => {
    await page.route('**/ops/mineru/diagnostics', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          mineru: { processingTasks: 1, queuedTasks: 0 },
          luceon: { mineruProcessingTasks: [], mineruQueuedTasks: [] },
          diagnosis: {
            status: 'blocked',
            kind: 'known-failed-but-mineru-processing',
            message: 'Luceon 任务已进入失败/取消终态，但 MinerU 仍在处理该内部任务，当前解析槽位被历史任务占用。',
            blockingLuceonTaskId: 'task-mock-failed-123',
            blockingMineruTaskId: 'mineru-mock-456',
            safeToAutoRecover: false
          }
        })
      });
    });

    await page.route('**/ops/health', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          frontend: { status: 'ok', version: 'test' },
          uploadServer: { status: 'ok', version: 'test' },
          dbServer: { status: 'ok', version: 'test' },
          minio: { status: 'ok', version: 'test' },
          mineru: { status: 'ok', version: 'test' },
          ollama: { status: 'ok', version: 'test' },
          timestamp: new Date().toISOString()
        })
      });
    });

    await page.goto(`${BASE_URL}/cms/ops`);
    
    await expect(page.locator('text=发现阻塞风险')).toBeVisible();
    await expect(page.locator('text=已失败任务仍占用 MinerU')).toBeVisible();
    await expect(page.locator('text=task-mock-failed-123')).toBeVisible();
    await expect(page.locator('text=mineru-mock-456')).toBeVisible();
    await expect(page.locator('text=建议：等待完成或人工清障')).toBeVisible();
  });
});
