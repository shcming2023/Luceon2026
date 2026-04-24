import { test, expect, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const BASE_URL = process.env.BASE_URL || 'http://127.0.0.1:8081';
const TEST_PDF_DIR = process.env.TEST_PDF_DIR || path.resolve(process.cwd(), '..', 'testpdf');

test.describe.configure({ retries: 0 });

function listTestPdfs() {
  if (!fs.existsSync(TEST_PDF_DIR)) {
    throw new Error(
      [
        `TEST_PDF_DIR 不存在：${TEST_PDF_DIR}`,
        '请创建本地样本目录，例如：',
        `mkdir -p ${TEST_PDF_DIR}`,
        '并放入至少 10 个 PDF 文件用于回归测试。',
      ].join('\n'),
    );
  }

  const names = fs.readdirSync(TEST_PDF_DIR);
  return names
    .filter((n) => n.toLowerCase().endsWith('.pdf'))
    .filter((n) => n !== '.DS_Store' && !n.startsWith('._'))
    .map((n) => path.join(TEST_PDF_DIR, n))
    .sort((a, b) => a.localeCompare(b, 'zh-CN'));
}

async function resetBatchProcessingPersistence(request: APIRequestContext) {
  const empty = { items: [], running: false, paused: false, uiOpen: false };
  const resp = await request.put(`${BASE_URL}/__proxy/db/settings/batchProcessing`, { data: empty });
  expect(resp.ok()).toBe(true);
  const tsResp = await request.put(`${BASE_URL}/__proxy/db/settings/batchProcessingUpdatedAt`, { data: Date.now() });
  expect(tsResp.ok()).toBe(true);
}

async function getDbSnapshot(request: APIRequestContext) {
  const [matsResp, tasksResp] = await Promise.all([
    request.get(`${BASE_URL}/__proxy/db/materials`),
    request.get(`${BASE_URL}/__proxy/db/tasks`),
  ]);
  expect(matsResp.ok()).toBe(true);
  expect(tasksResp.ok()).toBe(true);
  const mats = await matsResp.json();
  const tasks = await tasksResp.json();
  const materials = Array.isArray(mats) ? mats : [];
  const parseTasks = Array.isArray(tasks) ? tasks : [];
  return {
    materials,
    parseTasks,
  };
}

test.describe('【P0】上传队列可靠性与 aborted 可观测', () => {
  test('多轮提交 + abort + 重试：前端成功数与后端新增任务数一致', async ({ page, request }, testInfo) => {
    const runId = `upload-queue-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    testInfo.setTimeout(10 * 60 * 1000);

    await resetBatchProcessingPersistence(request);

    await page.addInitScript(() => {
      localStorage.removeItem('app_batch_processing');
      localStorage.removeItem('app_materials');
      localStorage.removeItem('app_tasks');
      localStorage.removeItem('app_process_tasks');
      localStorage.removeItem('app_asset_details');
    });

    const pdfs = listTestPdfs();
    if (pdfs.length < 10) {
      throw new Error(`TEST_PDF_DIR 至少需要 10 个 PDF，当前仅发现 ${pdfs.length} 个：${TEST_PDF_DIR}`);
    }
    const selected = pdfs.slice(0, 10);

    const before = await getDbSnapshot(request);
    const beforeTaskIds = new Set(before.parseTasks.map((t: any) => String(t?.id || '')));
    const beforeMaterialIds = new Set(before.materials.map((m: any) => String(m?.id || '')));

    let aborted = 0;
    await page.route('**/__proxy/upload/tasks', async (route) => {
      const r = route.request();
      if (r.method() === 'POST' && aborted < 2) {
        aborted += 1;
        await route.abort();
        return;
      }
      await route.continue();
    });

    await page.goto(`${BASE_URL}/cms/workspace`);

    const input = page.locator('input[type="file"]').first();
    await input.setInputFiles(selected.slice(0, 4));
    await page.waitForTimeout(300);
    await input.setInputFiles(selected.slice(4, 7));
    await page.waitForTimeout(300);
    await input.setInputFiles(selected.slice(7, 10));

    const modalTitle = page.getByText('批量上传与处理');
    const fab = page.locator('button[title="打开批处理进度"]');
    const isModalOpen = await modalTitle.isVisible().catch(() => false);
    if (!isModalOpen) {
      await expect(fab).toBeVisible({ timeout: 10000 });
      await fab.click();
    }
    await expect(modalTitle).toBeVisible({ timeout: 10000 });

    const errorBadge = page.locator('text=失败').first();
    await expect(errorBadge).toBeVisible({ timeout: 10000 });

    const waitForQueueIdle = async () => {
      await page.waitForFunction(() => {
        const raw = localStorage.getItem('app_batch_processing');
        if (!raw) return false;
        const data = JSON.parse(raw);
        const items = Array.isArray(data?.items) ? data.items : [];
        const active = items.filter((it: any) => !['completed', 'error', 'skipped'].includes(String(it?.status)));
        return active.length === 0;
      }, { timeout: 180000 });
    };

    await waitForQueueIdle();

    const retryButtons = page.locator('button[title="重试"]');
    const retryCount = await retryButtons.count();
    expect(retryCount).toBeGreaterThanOrEqual(1);
    for (let i = 0; i < retryCount; i += 1) {
      await retryButtons.nth(0).click();
    }

    await waitForQueueIdle();

    const queue = await page.evaluate(() => {
      const raw = localStorage.getItem('app_batch_processing');
      return raw ? JSON.parse(raw) : null;
    });
    const items = Array.isArray(queue?.items) ? queue.items : [];
    const completed = items.filter((it: any) => String(it?.status) === 'completed').length;
    const errors = items.filter((it: any) => String(it?.status) === 'error').length;
    const after = await getDbSnapshot(request);
    const newTasks = after.parseTasks.filter((t: any) => !beforeTaskIds.has(String(t?.id || '')));
    const newMaterials = after.materials.filter((m: any) => !beforeMaterialIds.has(String(m?.id || '')));

    const newTasksSummary = newTasks.map((t: any) => ({
      id: t?.id,
      materialId: t?.materialId,
      state: t?.state,
      stage: t?.stage,
      progress: t?.progress,
      message: t?.message,
      createdAt: t?.createdAt,
    }));
    const newMaterialsSummary = newMaterials.map((m: any) => ({
      id: m?.id,
      title: m?.title,
      fileName: m?.fileName,
      status: m?.status,
      mineruStatus: m?.mineruStatus,
      aiStatus: m?.aiStatus,
      objectName: m?.metadata?.objectName,
    }));

    const printAudit = () => {
      console.log(`[${runId}] newTasks=${newTasks.length} newMaterials=${newMaterials.length}`);
      console.log(`[${runId}] newTasks: ${JSON.stringify(newTasksSummary, null, 2)}`);
      console.log(`[${runId}] newMaterials: ${JSON.stringify(newMaterialsSummary, null, 2)}`);
    };

    try {
      expect(errors).toBe(0);
      expect(completed).toBe(10);
      expect(newTasks.length).toBe(completed);
      expect(newMaterials.length).toBe(completed);
    } catch (err) {
      printAudit();
      throw err;
    }

    printAudit();
  });
});
